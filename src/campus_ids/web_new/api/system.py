"""web_new/api/system.py — 系统 API 路由。

端点：
- GET  /api/health      健康检查（接入 RuntimeState + DB + ML + EventBus）
- GET  /api/check       环境自检（Python/依赖/Npcap/模型/数据）
- GET  /api/settings    获取配置
- PUT  /api/settings    更新阈值
- GET  /api/csrf-token  获取 CSRF token
"""
from __future__ import annotations

import importlib
import logging
import sys
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from campus_ids.runtime.timeutil import now_str

logger = logging.getLogger(__name__)

from campus_ids.runtime.db import get_connection
from campus_ids.runtime.repositories import ConfigRepository
from campus_ids.runtime.settings import Settings, get_settings
from campus_ids.web_new.errors import ValidationError
from campus_ids.web_new.security import (
    Public, Readonly, Write, generate_csrf_token, CSRF_COOKIE_NAME, limiter,
)
from campus_ids.web_new.schemas import (
    CheckResponse,
    HealthResponse,
    MessageResponse,
    SettingsResponse,
    ThresholdUpdateRequest,
)

router = APIRouter(prefix="/api", tags=["system"])

# 应用启动时间（模块加载时记录）
_app_start_time = time.time()


# ── GET /api/health ────────────────────────────────────────────────

@router.get("/health", dependencies=[Public], summary="系统健康检查")
async def health_check(request: Request) -> HealthResponse:
    """系统健康检查 — 对齐旧 Flask /api/health 返回格式。

    组件：database / capture / ml_model / memory / sse / threads
    """
    health: dict = {
        "status": "healthy",
        "timestamp": now_str(),
        "uptime_seconds": round(time.time() - _app_start_time, 1),
        "components": {},
    }

    # 数据库连接状态
    try:
        with get_connection() as conn:
            conn.execute(text("SELECT 1"))
        health["components"]["database"] = {"status": "ok"}
    except Exception as e:
        health["components"]["database"] = {"status": "error", "message": str(e)}
        health["status"] = "degraded"

    # 抓包线程状态（含生效网卡/过滤，抓不到包时一眼定位是选卡还是过滤问题）
    state = request.app.state.runtime_state
    capture_service = getattr(request.app.state, "capture_service", None)
    capture_comp: dict = {
        "status": "running" if state.capture_running else "stopped",
        "dropped_packets": state.dropped_packets,
        "queue_size": state.packet_queue.qsize(),
    }
    if capture_service is not None and hasattr(capture_service, "capture_status"):
        try:
            snap = capture_service.capture_status()
            capture_comp["iface"] = snap.get("iface")
            capture_comp["iface_source"] = snap.get("iface_source")
            capture_comp["filter"] = snap.get("filter")
        except Exception:  # noqa: BLE001 —— 状态快照失败不影响健康检查
            pass
    health["components"]["capture"] = capture_comp

    # ML 模型状态
    try:
        dual_detector = getattr(request.app.state, "dual_detector", None)
        if dual_detector and hasattr(dual_detector, "get_stats"):
            ml_stats = dual_detector.get_stats()
            health["components"]["ml_model"] = {
                "model_loaded": ml_stats.get("model_loaded", False),
                "loop_running": state.ml_running,
                "predict_count": ml_stats.get("ml_predict_count", 0),
                "attack_count": ml_stats.get("ml_attack_count", 0),
            }
            if "detection_latency_ms" in ml_stats:
                health["components"]["detection_latency"] = ml_stats["detection_latency_ms"]
        else:
            health["components"]["ml_model"] = {
                "model_loaded": False,
                "loop_running": False,
                "predict_count": 0,
                "attack_count": 0,
            }
    except Exception:
        health["components"]["ml_model"] = {"status": "error"}

    # 内存使用量
    try:
        import psutil
        process = psutil.Process()
        mem_info = process.memory_info()
        health["components"]["memory"] = {
            "rss_mb": round(mem_info.rss / 1024 / 1024, 1),
            "vms_mb": round(mem_info.vms / 1024 / 1024, 1),
        }
    except Exception:
        health["components"]["memory"] = {"status": "unavailable"}

    # SSE 订阅者数量
    event_bus = getattr(state, "event_bus", None)
    if event_bus and hasattr(event_bus, "subscriber_count"):
        health["components"]["sse"] = {"subscribers": event_bus.subscriber_count()}
    else:
        health["components"]["sse"] = {"subscribers": 0}

    # 线程存活状态
    health["components"]["threads"] = {
        "capture_alive": state.capture_thread is not None and state.capture_thread.is_alive(),
        "enhanced_capture_alive": (
            state.enhanced_capture_thread is not None
            and state.enhanced_capture_thread.is_alive()
        ),
        "ml_loop_alive": state.ml_running,
    }

    return HealthResponse(**health)


# ── GET /api/check ─────────────────────────────────────────────────

@router.get("/check", dependencies=[Public], summary="环境自检")
async def environment_check() -> CheckResponse:
    """环境自检：Python 版本、依赖包、Npcap、模型文件、数据文件。

    数据目录取自 `Settings.data_dir`（而非硬算项目根），这样
    `CAMPUS_IDS_DATA_DIR` 指向别处时自检结果与实际运行目录一致。
    """
    data_dir = get_settings().data_dir
    result: dict = {"ok": True}

    # 1. Python 版本
    py_ver = sys.version_info
    py_ok = py_ver >= (3, 10)
    result["python"] = {
        "version": f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}",
        "ok": py_ok,
        "required": ">=3.10",
    }
    if not py_ok:
        result["ok"] = False

    # 2. 依赖包
    required = [
        ("scapy", "scapy"), ("fastapi", "fastapi"),
        ("sklearn", "scikit-learn"), ("joblib", "joblib"),
        ("pandas", "pandas"), ("numpy", "numpy"),
        ("sqlalchemy", "sqlalchemy"), ("uvicorn", "uvicorn"),
    ]
    optional = [
        ("xgboost", "xgboost"), ("lightgbm", "lightgbm"),
    ]
    req_results = []
    for mod, pkg in required:
        try:
            importlib.import_module(mod)
            req_results.append({"package": pkg, "installed": True})
        except ImportError:
            req_results.append({"package": pkg, "installed": False})
            result["ok"] = False
    opt_results = []
    for mod, pkg in optional:
        try:
            importlib.import_module(mod)
            opt_results.append({"package": pkg, "installed": True})
        except ImportError:
            opt_results.append({"package": pkg, "installed": False})
    result["dependencies"] = {"required": req_results, "optional": opt_results}

    # 3. Npcap / libpcap + 抓包自证诊断（2026-09-20 增补）
    #    只报「Npcap 可用」不足以解释抓不到包 —— 把生效网卡、权限、
    #    过滤表达式、候选网卡一并暴露，排障时无需登机器。
    capture_info: dict = {}
    try:
        from scapy.arch import get_if_addr  # noqa: F401
        capture_ok = True
        capture_msg = "Npcap/libpcap 可用"
    except Exception:
        capture_ok = False
        capture_msg = "Npcap/libpcap 不可用 — 抓包功能受限，仍可使用模拟数据"

    try:
        import ctypes

        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        is_admin = None  # 非 Windows

    settings = get_settings()
    capture_iface = settings.capture_iface
    if capture_iface:
        iface_source = "config"
    else:
        try:
            from campus_ids.services.capture_service import autodetect_iface

            capture_iface, iface_source = autodetect_iface()
        except Exception:  # noqa: BLE001 —— 诊断失败不影响自检结论
            capture_iface, iface_source = None, "fallback"

    # 生效的 BPF：与实际抓包共用同一构造逻辑（避免诊断口径漂移）
    try:
        from campus_ids.services.capture_service import build_capture_filter

        effective_filter = build_capture_filter() or "<无>"
    except Exception:  # noqa: BLE001
        effective_filter = settings.capture_filter or "<无>"

    candidates = []
    if capture_ok:
        try:
            from scapy.arch.windows import get_windows_if_list

            for itf in get_windows_if_list():
                ips = [i for i in (itf.get("ips") or []) if i not in ("0.0.0.0", "::")]
                if not ips:
                    continue
                candidates.append({
                    "name": itf.get("name", ""),
                    "description": itf.get("description", ""),
                    "ips": ips[:3],
                })
        except Exception:  # noqa: BLE001 —— 非 Windows 无此枚举
            try:
                from scapy.all import get_if_list

                candidates = [{"name": n, "description": "", "ips": []} for n in get_if_list()]
            except Exception:
                candidates = []

    capture_info = {
        "ok": capture_ok,
        "message": capture_msg,
        "is_admin": is_admin,
        "iface": capture_iface,
        "iface_source": iface_source,
        "filter": effective_filter,
        "exclude_web_port": settings.capture_exclude_web_port,
        "candidate_ifaces": candidates[:12],
    }
    result["capture"] = capture_info

    # 4. 模型文件
    model_results = []
    for name, rel_path in [
        ("model.pkl", "model.pkl"),
        ("evaluation_report.txt", "evaluation_report.txt"),
    ]:
        path = data_dir / rel_path
        exists = path.exists()
        size_kb = round(path.stat().st_size / 1024, 1) if exists else 0
        model_results.append({"name": name, "exists": exists, "size_kb": size_kb})
    result["model_files"] = model_results

    # 5. 数据文件
    data_results = []
    for name, rel_path in [
        ("traffic_data.csv", "traffic_data.csv"),
        ("traffic_stats.csv", "traffic_stats.csv"),
    ]:
        path = data_dir / rel_path
        exists = path.exists()
        size_kb = round(path.stat().st_size / 1024, 1) if exists else 0
        data_results.append({"name": name, "exists": exists, "size_kb": size_kb})
    result["data_files"] = data_results

    return CheckResponse(**result)


# ── GET /api/settings ──────────────────────────────────────────────

@router.get("/settings", dependencies=[Readonly], summary="获取当前配置")
async def get_settings_endpoint(request: Request) -> SettingsResponse:
    """获取当前配置 — 从 Settings 单例读取。"""
    settings: Settings = request.app.state.settings

    # 阈值类
    threshold_keys = settings.threshold_keys
    thresholds = {k: getattr(settings, k) for k in threshold_keys}

    # ML 配置
    ml_config = {
        "ml_interval_sec": settings.ml_interval_sec,
        "ml_flow_buffer_size": settings.ml_flow_buffer_size,
        "ml_history_size": settings.ml_history_size,
        "ml_conf_high": settings.ml_conf_high,
        "ml_conf_low": settings.ml_conf_low,
    }

    # Web 配置
    web_config = {
        "web_port": settings.web_port,
        "web_refresh_interval_ms": settings.web_refresh_interval_ms,
    }

    # 告警配置
    alert_config = {
        "max_alert_api_return": settings.max_alert_api_return,
    }

    return SettingsResponse(
        thresholds=thresholds,
        ml_config=ml_config,
        web_config=web_config,
        auth_enabled=settings.auth_enabled,
        alert_config=alert_config,
    )


# ── T2.12: 阈值热更新辅助 ──────────────────────────────────────

def _rebuild_rule_detector(app) -> None:
    """用当前 settings 重建 rule_detector，迁移有状态追踪器。

    仅在阈值类配置变更时调用，保证：
    1. 新 detector 使用最新阈值
    2. _bf_tracker / _lateral_tracker 从旧实例迁移（不丢失运行时状态）
    3. app.state.rule_detector 和 dual_detector.rule_detector 同步更新
    """
    from campus_ids.detector.detector import create_rule_detector

    settings: Settings = app.state.settings
    old_detector = getattr(app.state, "rule_detector", None)

    new_detector = create_rule_detector(
        ddos_threshold=settings.ddos_threshold,
        port_scan_threshold=settings.port_scan_threshold,
        syn_flood_threshold=settings.syn_flood_threshold,
        udp_flood_threshold=settings.udp_flood_threshold,
        brute_force_threshold=settings.brute_force_threshold,
        brute_force_window=settings.brute_force_window,
        lateral_movement_threshold=settings.lateral_movement_threshold,
    )

    # 迁移有状态追踪器（暴力破解滑窗 + 横向移动追踪）
    if old_detector is not None:
        new_detector._bf_tracker = old_detector._bf_tracker
        new_detector._lateral_tracker = old_detector._lateral_tracker

    app.state.rule_detector = new_detector

    # 同步更新 dual_detector 的 rule_detector 引用
    dual_detector = getattr(app.state, "dual_detector", None)
    if dual_detector is not None:
        dual_detector.rule_detector = new_detector

    logger.info(
        "规则检测器已热重建 (ddos=%d, port_scan=%d, syn=%d, udp=%d, bf=%d, bf_win=%d, lateral=%d)",
        settings.ddos_threshold, settings.port_scan_threshold,
        settings.syn_flood_threshold, settings.udp_flood_threshold,
        settings.brute_force_threshold, settings.brute_force_window,
        settings.lateral_movement_threshold,
    )


# ── PUT /api/settings ──────────────────────────────────────────────

@router.put("/settings", dependencies=[Write], summary="更新阈值配置")
@limiter.limit("30/minute")
async def update_settings(
    body: ThresholdUpdateRequest,
    request: Request,
) -> MessageResponse:
    """更新阈值配置 — 运行期覆盖 + DB 持久化 + 检测器热重建。"""
    settings: Settings = request.app.state.settings

    # 校验 key 是否为可配置阈值
    if body.key not in settings.threshold_keys:
        raise ValidationError(f"不可配置的阈值键: {body.key}，可配置键: {sorted(settings.threshold_keys)}")

    # 类型转换
    try:
        field_type = type(getattr(settings, body.key))
        typed_value = field_type(body.value)
    except (ValueError, TypeError) as e:
        raise ValidationError(f"类型转换失败: {e}")

    # 运行期覆盖
    settings.set_override(body.key, typed_value)

    # DB 持久化
    with get_connection() as conn:
        ConfigRepository.set(conn, key=body.key, value=str(typed_value))

    # T2.12: 阈值热更新 — 重建 rule_detector 并迁移有状态追踪器
    _rebuild_rule_detector(request.app)

    return MessageResponse(message=f"阈值 {body.key} 已更新为 {typed_value}")


# ── GET /api/csrf-token ────────────────────────────────────────────

@router.get("/csrf-token", dependencies=[Public], summary="获取CSRF令牌")
async def get_csrf_token(request: Request) -> JSONResponse:
    """获取 CSRF token 并设置双提交 cookie。

    双提交模式：cookie + X-CSRFToken 头比对。
    cookie 设为 HttpOnly + SameSite=Lax，JS 从响应体读取 token。
    """
    token = generate_csrf_token()
    response = JSONResponse({"csrf_token": token})
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # 开发环境；生产环境应通过配置启用
        path="/",
    )
    return response