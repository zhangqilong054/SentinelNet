"""web_new/shim.py — 旧端点 shim 路由（T4.1）。

所有被取代的旧端点保留原路径转发，返回 Deprecation + Sunset 头。
命中记录由 DeprecationTracker 追踪（T4.2）。

映射表唯一真相源：tests/contract/mapping.py。
本模块只覆盖 kind=renamed 和 kind=consolidated 的条目（kind=kept 路径不变，无需 shim）。

D2 约束：旧端点保留 1 个发布版本，带 Deprecation / Sunset 头。
"""
from __future__ import annotations

import csv
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from campus_ids.web_new.deprecation import (
    SUNSET_DATE,
    DEPRECATION_HEADER,
    get_deprecation_tracker,
)
from campus_ids.web_new.security import Public, Readonly, Write

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["deprecated"], include_in_schema=False)


# ── 辅助函数 ──────────────────────────────────────────────────

def _deprecation_headers(new_path: str) -> dict[str, str]:
    """构建 Deprecation + Sunset + Link 响应头。"""
    return {
        "Deprecation": DEPRECATION_HEADER,
        "Sunset": SUNSET_DATE,
        "Link": f'</api{new_path}>; rel="successor-version"',
    }


def _record_hit(request: Request, path: str, method: str) -> None:
    """记录旧端点命中（T4.2）。"""
    source = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    if ua:
        source = f"{source} ({ua[:50]})"
    get_deprecation_tracker().record_hit(path, method, source=source)


def _deprecation_response(
    data: Any,
    new_path: str,
    status_code: int = 200,
) -> JSONResponse:
    """构建带 Deprecation 头的 JSON 响应。"""
    return JSONResponse(
        content=data,
        status_code=status_code,
        headers=_deprecation_headers(new_path),
    )


def _get_registry(request: Request):
    """从应用状态获取任务注册表。"""
    from campus_ids.web_new.errors import ApiError
    registry = getattr(request.app.state, "task_registry", None)
    if registry is None:
        raise ApiError(
            error_code="SERVICE_UNAVAILABLE",
            detail="任务注册表未初始化",
            status_code=503,
        )
    return registry


def _get_scenario_service(request: Request):
    """从应用状态获取剧本服务。"""
    from campus_ids.web_new.errors import ApiError
    svc = getattr(request.app.state, "scenario_service", None)
    if svc is None:
        raise ApiError(
            error_code="SERVICE_UNAVAILABLE",
            detail="剧本服务未初始化",
            status_code=503,
        )
    return svc


def _get_model_service(request: Request):
    """从应用状态获取模型服务。"""
    from campus_ids.web_new.errors import ApiError
    svc = getattr(request.app.state, "model_service", None)
    if svc is None:
        raise ApiError(
            error_code="SERVICE_UNAVAILABLE",
            detail="模型服务未初始化",
            status_code=503,
        )
    return svc


# ── 编排族：7 个状态端点 → GET /api/tasks ────────────────────

@router.get("/capture/status", dependencies=[Readonly])
async def shim_capture_status(request: Request) -> JSONResponse:
    """[DEPRECATED] → GET /api/tasks"""
    _record_hit(request, "/api/capture/status", "GET")
    registry = _get_registry(request)
    tasks = registry.status_all()
    return _deprecation_response({"tasks": tasks, "deprecated_hint": "Use GET /api/tasks"}, "/tasks")


@router.get("/capture/enhanced-status", dependencies=[Readonly])
async def shim_capture_enhanced_status(request: Request) -> JSONResponse:
    """[DEPRECATED] → GET /api/tasks"""
    _record_hit(request, "/api/capture/enhanced-status", "GET")
    registry = _get_registry(request)
    tasks = registry.status_all()
    return _deprecation_response({"tasks": tasks, "deprecated_hint": "Use GET /api/tasks"}, "/tasks")


@router.get("/detector/status", dependencies=[Readonly])
async def shim_detector_status(request: Request) -> JSONResponse:
    """[DEPRECATED] → GET /api/tasks"""
    _record_hit(request, "/api/detector/status", "GET")
    registry = _get_registry(request)
    tasks = registry.status_all()
    return _deprecation_response({"tasks": tasks, "deprecated_hint": "Use GET /api/tasks"}, "/tasks")


@router.get("/attack/status", dependencies=[Readonly])
async def shim_attack_status(request: Request) -> JSONResponse:
    """[DEPRECATED] → GET /api/tasks"""
    _record_hit(request, "/api/attack/status", "GET")
    registry = _get_registry(request)
    tasks = registry.status_all()
    return _deprecation_response({"tasks": tasks, "deprecated_hint": "Use GET /api/tasks"}, "/tasks")


@router.get("/auto/status", dependencies=[Readonly])
async def shim_auto_status(request: Request) -> JSONResponse:
    """[DEPRECATED] → GET /api/tasks"""
    _record_hit(request, "/api/auto/status", "GET")
    registry = _get_registry(request)
    tasks = registry.status_all()
    return _deprecation_response({"tasks": tasks, "deprecated_hint": "Use GET /api/tasks"}, "/tasks")


@router.get("/dual/stats", dependencies=[Readonly])
async def shim_dual_stats(request: Request) -> JSONResponse:
    """[DEPRECATED] → GET /api/tasks"""
    _record_hit(request, "/api/dual/stats", "GET")
    registry = _get_registry(request)
    tasks = registry.status_all()
    return _deprecation_response({"tasks": tasks, "deprecated_hint": "Use GET /api/tasks"}, "/tasks")


@router.get("/model/train-status", dependencies=[Readonly])
async def shim_model_train_status(request: Request) -> JSONResponse:
    """[DEPRECATED] → GET /api/models/train/status"""
    _record_hit(request, "/api/model/train-status", "GET")
    registry = _get_registry(request)
    train_status = registry.status("train")
    return _deprecation_response(train_status, "/models/train/status")


# ── 编排族：8 个启动端点 → POST /api/tasks/{name}/start ──────

@router.post("/capture/start", dependencies=[Write])
async def shim_capture_start(request: Request) -> JSONResponse:
    """[DEPRECATED] → POST /api/tasks/capture/start"""
    _record_hit(request, "/api/capture/start", "POST")
    registry = _get_registry(request)
    result = registry.start("capture")
    return _deprecation_response(result, "/tasks/capture/start", status_code=200)


@router.post("/capture/start-enhanced", dependencies=[Write])
async def shim_capture_start_enhanced(request: Request) -> JSONResponse:
    """[DEPRECATED] → POST /api/tasks/capture_full/start"""
    _record_hit(request, "/api/capture/start-enhanced", "POST")
    registry = _get_registry(request)
    result = registry.start("capture_full")
    return _deprecation_response(result, "/tasks/capture_full/start", status_code=200)


@router.post("/detector/start", dependencies=[Write])
async def shim_detector_start(request: Request) -> JSONResponse:
    """[DEPRECATED] → POST /api/tasks/detection/start"""
    _record_hit(request, "/api/detector/start", "POST")
    registry = _get_registry(request)
    result = registry.start("detection")
    return _deprecation_response(result, "/tasks/detection/start", status_code=200)


@router.post("/attack/start", dependencies=[Write])
async def shim_attack_start(request: Request) -> JSONResponse:
    """[DEPRECATED] → POST /api/tasks/attack/start"""
    _record_hit(request, "/api/attack/start", "POST")
    registry = _get_registry(request)
    result = registry.start("attack")
    return _deprecation_response(result, "/tasks/attack/start", status_code=200)


@router.post("/auto/start", dependencies=[Write])
async def shim_auto_start(request: Request) -> JSONResponse:
    """[DEPRECATED] → POST /api/scenarios/start {"scenario":"full"}"""
    _record_hit(request, "/api/auto/start", "POST")
    svc = _get_scenario_service(request)
    result = svc.start_scenario("full")
    return _deprecation_response(result, "/scenarios/start", status_code=200)


@router.post("/demo/start", dependencies=[Write])
async def shim_demo_start(request: Request) -> JSONResponse:
    """[DEPRECATED] → POST /api/scenarios/start {"scenario":"demo"}"""
    _record_hit(request, "/api/demo/start", "POST")
    svc = _get_scenario_service(request)
    result = svc.start_scenario("demo")
    return _deprecation_response(result, "/scenarios/start", status_code=200)


@router.post("/dual/load", dependencies=[Write])
async def shim_dual_load(request: Request) -> JSONResponse:
    """[DEPRECATED] → POST /api/tasks/ml/start"""
    _record_hit(request, "/api/dual/load", "POST")
    registry = _get_registry(request)
    result = registry.start("ml")
    return _deprecation_response(result, "/tasks/ml/start", status_code=200)


@router.post("/model/train", dependencies=[Write])
async def shim_model_train(request: Request) -> JSONResponse:
    """[DEPRECATED] → POST /api/models/train

    旧端点无 confirm 要求，shim 自动确认并备份产物（P1-2 修复）。
    委托 TaskRegistry.start("train") 而非 model_service.train()，
    避免复活 model_service 私有状态机（P1-3 修复）。
    """
    _record_hit(request, "/api/model/train", "POST")
    from campus_ids.web_new.api.models import (
        TRAIN_TASK,
        backup_training_products,
    )
    from campus_ids.web_new.errors import ApiError, ConflictError

    registry = _get_registry(request)

    # 检查是否已在运行（与新端点一致）
    try:
        current = registry.status(TRAIN_TASK)
    except KeyError as exc:
        raise ApiError("TASK_NOT_FOUND", "训练任务未注册", status_code=503) from exc

    if current.get("status") == "running":
        raise ConflictError("模型训练正在进行中")

    # 备份产物（与新端点一致）
    backup_dir = backup_training_products()

    # 通过 TaskRegistry 启动（避免 model_service 私有状态机）
    result = registry.start(TRAIN_TASK)
    state = result.get("status")
    if state == "error":
        raise ApiError(
            error_code="TRAIN_START_FAILED",
            detail=result.get("message", "训练任务启动失败"),
            status_code=503,
        )
    if state == "already_running":
        raise ConflictError("模型训练正在进行中")

    message = "训练已启动（旧端点自动确认）"
    if backup_dir is not None:
        message += f"；产物已备份至 {backup_dir}"
    return _deprecation_response(
        {"message": message, "deprecated_hint": "Use POST /api/models/train"},
        "/models/train",
    )


# ── 编排族：5 个停止端点 → POST /api/tasks/{name}/stop ──────

@router.post("/capture/stop", dependencies=[Write])
async def shim_capture_stop(request: Request) -> JSONResponse:
    """[DEPRECATED] → POST /api/tasks/capture/stop"""
    _record_hit(request, "/api/capture/stop", "POST")
    registry = _get_registry(request)
    result = registry.stop("capture")
    return _deprecation_response(result, "/tasks/capture/stop", status_code=200)


@router.post("/capture/stop-enhanced", dependencies=[Write])
async def shim_capture_stop_enhanced(request: Request) -> JSONResponse:
    """[DEPRECATED] → POST /api/tasks/capture_full/stop"""
    _record_hit(request, "/api/capture/stop-enhanced", "POST")
    registry = _get_registry(request)
    result = registry.stop("capture_full")
    return _deprecation_response(result, "/tasks/capture_full/stop", status_code=200)


@router.post("/detector/stop", dependencies=[Write])
async def shim_detector_stop(request: Request) -> JSONResponse:
    """[DEPRECATED] → POST /api/tasks/detection/stop"""
    _record_hit(request, "/api/detector/stop", "POST")
    registry = _get_registry(request)
    result = registry.stop("detection")
    return _deprecation_response(result, "/tasks/detection/stop", status_code=200)


@router.post("/attack/stop", dependencies=[Write])
async def shim_attack_stop(request: Request) -> JSONResponse:
    """[DEPRECATED] → POST /api/tasks/attack/stop"""
    _record_hit(request, "/api/attack/stop", "POST")
    registry = _get_registry(request)
    result = registry.stop("attack")
    return _deprecation_response(result, "/tasks/attack/stop", status_code=200)


@router.post("/dual/stop", dependencies=[Write])
async def shim_dual_stop(request: Request) -> JSONResponse:
    """[DEPRECATED] → POST /api/tasks/ml/stop"""
    _record_hit(request, "/api/dual/stop", "POST")
    registry = _get_registry(request)
    result = registry.stop("ml")
    return _deprecation_response(result, "/tasks/ml/stop", status_code=200)


# ── 配置：GET /api/config → GET /api/settings ────────────────

@router.get("/config", dependencies=[Readonly])
async def shim_config_get(request: Request) -> JSONResponse:
    """[DEPRECATED] → GET /api/settings"""
    _record_hit(request, "/api/config", "GET")
    settings = request.app.state.settings
    threshold_keys = settings.threshold_keys
    thresholds = {k: getattr(settings, k) for k in threshold_keys}
    ml_config = {
        "ml_interval_sec": settings.ml_interval_sec,
        "ml_flow_buffer_size": settings.ml_flow_buffer_size,
        "ml_history_size": settings.ml_history_size,
        "ml_conf_high": settings.ml_conf_high,
        "ml_conf_low": settings.ml_conf_low,
    }
    web_config = {
        "web_port": settings.web_port,
        "web_refresh_interval_ms": settings.web_refresh_interval_ms,
    }
    alert_config = {
        "max_alert_api_return": settings.max_alert_api_return,
    }
    return _deprecation_response({
        "thresholds": thresholds,
        "ml_config": ml_config,
        "web_config": web_config,
        "auth_enabled": settings.auth_enabled,
        "alert_config": alert_config,
        "deprecated_hint": "Use GET /api/settings",
    }, "/settings")


@router.post("/config", dependencies=[Write])
async def shim_config_post(request: Request) -> JSONResponse:
    """[DEPRECATED] → PUT /api/settings

    旧端点用 POST + JSON body，新端点用 PUT + Pydantic body。
    Shim 解析旧格式请求体，委托给 settings.set_override + ConfigRepository。
    """
    _record_hit(request, "/api/config", "POST")
    from campus_ids.runtime.db import get_connection
    from campus_ids.runtime.repositories import ConfigRepository
    from campus_ids.web_new.errors import ApiError

    settings = request.app.state.settings
    try:
        body = await request.json()
    except Exception:
        raise ApiError("INVALID_BODY", "请求体解析失败", status_code=400)

    key = body.get("key")
    value = body.get("value")
    if not key or value is None:
        raise ApiError("INVALID_BODY", "需要 key 和 value 字段", status_code=400)

    if key not in settings.threshold_keys:
        raise ApiError(
            "INVALID_KEY",
            f"不可配置的阈值键: {key}，可配置键: {sorted(settings.threshold_keys)}",
            status_code=400,
        )

    try:
        field_type = type(getattr(settings, key))
        typed_value = field_type(value)
    except (ValueError, TypeError) as e:
        raise ApiError("TYPE_ERROR", f"类型转换失败: {e}", status_code=400)

    settings.set_override(key, typed_value)
    with get_connection() as conn:
        ConfigRepository.set(conn, key=key, value=str(typed_value))

    # 热重建检测器（与 PUT /api/settings 行为一致）
    from campus_ids.web_new.api.system import _rebuild_rule_detector
    _rebuild_rule_detector(request.app)

    return _deprecation_response(
        {"message": f"阈值 {key} 已更新为 {typed_value}", "deprecated_hint": "Use PUT /api/settings"},
        "/settings",
    )


# ── 运维：/api/cleanup → /api/admin/cleanup, /api/save → /api/admin/export ──

@router.post("/cleanup", dependencies=[Write])
async def shim_cleanup(request: Request) -> JSONResponse:
    """[DEPRECATED] → POST /api/admin/cleanup"""
    _record_hit(request, "/api/cleanup", "POST")
    from campus_ids.runtime.db import get_connection
    from campus_ids.runtime.repositories import UserRepository
    from campus_ids.web_new.errors import ApiError

    try:
        body = await request.json()
    except Exception:
        body = {}
    days = body.get("days", 7)
    if not isinstance(days, int) or days < 1:
        days = 7

    with get_connection() as conn:
        alerts_deleted, traffic_deleted = UserRepository.cleanup_old_data(conn, days=days)

    return _deprecation_response({
        "status": "success",
        "alerts_deleted": alerts_deleted,
        "traffic_deleted": traffic_deleted,
        "deprecated_hint": "Use POST /api/admin/cleanup",
    }, "/admin/cleanup")


@router.post("/save", dependencies=[Write])
async def shim_save(request: Request) -> JSONResponse:
    """[DEPRECATED] → POST /api/admin/export"""
    _record_hit(request, "/api/save", "POST")
    from campus_ids.runtime.db import get_connection
    from campus_ids.runtime.repositories import TrafficRepository
    from campus_ids.runtime.settings import get_settings
    from campus_ids.web_new.errors import ApiError

    settings = get_settings()
    csv_path = settings.data_dir / "traffic_stats.csv"
    try:
        with get_connection() as conn:
            rows = TrafficRepository.query(conn, limit=10000)
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Time", "QPS", "Connections", "PacketCount", "PortCount", "SrcIPCount", "Alert"])
            for row in rows:
                writer.writerow([
                    row.time or "", row.qps or "", row.connections or "",
                    row.packet_count or "", row.port_count or "",
                    row.src_ip_count or "", row.alert or "Normal",
                ])
        return _deprecation_response({
            "status": "success",
            "message": f"数据已保存到 {csv_path.name}",
            "rows": len(rows),
            "deprecated_hint": "Use POST /api/admin/export",
        }, "/admin/export")
    except Exception as exc:
        raise ApiError("EXPORT_FAILED", f"导出数据失败: {exc}", status_code=500)


# ── 模型：/api/model/list → /api/models ──────────────────────

@router.get("/model/list", dependencies=[Readonly])
async def shim_model_list(request: Request) -> JSONResponse:
    """[DEPRECATED] → GET /api/models"""
    _record_hit(request, "/api/model/list", "GET")
    model_svc = _get_model_service(request)
    models = model_svc.list_models()
    return _deprecation_response({
        "models": models,
        "deprecated_hint": "Use GET /api/models",
    }, "/models")


# ── SSE：/api/stream/alerts + /api/stream/traffic → /api/stream ──

@router.get("/stream/alerts", dependencies=[Public])
async def shim_stream_alerts(request: Request) -> JSONResponse:
    """[DEPRECATED] → GET /api/stream?topics=alert

    旧端点返回 SSE 流，shim 返回 JSON 提示（SSE 不能简单转发）。
    客户端应迁移到 GET /api/stream?topics=alert。
    """
    _record_hit(request, "/api/stream/alerts", "GET")
    return _deprecation_response(
        {
            "error": "deprecated",
            "message": "此端点已弃用。请使用 GET /api/stream?topics=alert 获取 SSE 事件流。",
            "successor": "/api/stream?topics=alert",
        },
        "/stream?topics=alert",
        status_code=410,  # Gone — 旧 SSE 端点不再支持流式响应
    )


@router.get("/stream/traffic", dependencies=[Public])
async def shim_stream_traffic(request: Request) -> JSONResponse:
    """[DEPRECATED] → GET /api/stream?topics=traffic

    旧端点返回 SSE 流，shim 返回 JSON 提示（SSE 不能简单转发）。
    客户端应迁移到 GET /api/stream?topics=traffic。
    """
    _record_hit(request, "/api/stream/traffic", "GET")
    return _deprecation_response(
        {
            "error": "deprecated",
            "message": "此端点已弃用。请使用 GET /api/stream?topics=traffic 获取 SSE 事件流。",
            "successor": "/api/stream?topics=traffic",
        },
        "/stream?topics=traffic",
        status_code=410,  # Gone
    )