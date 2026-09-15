"""配置管理 & 数据管理 & 攻击模拟 & 环境自检 & 健康检查蓝图。"""
from __future__ import annotations

import importlib
import logging
import time as _time

from flask import Blueprint, jsonify, request

from campus_ids.config import (
    MODEL_PATH, EVALUATION_PATH, TRAFFIC_CSV, TRAFFIC_STATS_CSV,
)
from campus_ids.detector.detector import create_rule_detector
from campus_ids.web import helpers as _helpers
from campus_ids.web.helpers import (
    CONFIG, _state_lock, dual_detector,
    save_traffic_data, update_config,
)
from campus_ids.web.attack_sim_state import sim_state, start_attack_sim
from campus_ids.web.database import cleanup_old_data
from campus_ids.web.sse import _sse_lock, _sse_subscribers
from campus_ids.web.utils import _clamp_duration, _csrf_exempt, _int_param
from campus_ids.web.limiter import write_limit, write_limit_post

import campus_ids.web.helpers as _helpers_module

logger = logging.getLogger(__name__)

bp_admin = Blueprint("admin", __name__)

# ── 应用启动时间（用于 uptime 计算）──────────────────────────────────
_app_start_time = _time.time()


# ── 配置管理 API ──────────────────────────────────────────────────────

@bp_admin.route("/api/config", methods=['GET', 'POST'])
@_csrf_exempt
@write_limit_post
def config():
    """获取或更新检测阈值配置
    ---
    tags: [配置]
    parameters:
      - name: body
        in: body
        required: false
        schema:
          type: object
          properties:
            ddos_threshold: {type: integer, description: DDoS QPS阈值}
            port_scan_threshold: {type: integer, description: 端口扫描阈值}
            syn_flood_threshold: {type: integer, description: SYN Flood阈值}
            udp_flood_threshold: {type: integer, description: UDP Flood阈值}
            brute_force_threshold: {type: integer, description: 暴力破解阈值}
            brute_force_window: {type: integer, description: 暴力破解时间窗口(秒)}
            lateral_movement_threshold: {type: integer, description: 横向移动阈值}
    responses:
      200:
        description: 当前配置（GET）或更新结果（POST）
      400:
        description: 无效 JSON
    """
    if request.method == 'POST':
        with _state_lock:
            data = request.get_json()
            if data is None:
                return jsonify({'error': 'Invalid JSON'}), 400
            for key in ('ddos_threshold', 'port_scan_threshold', 'syn_flood_threshold',
                        'udp_flood_threshold', 'brute_force_threshold', 'brute_force_window',
                        'lateral_movement_threshold'):
                if key in data:
                    update_config(key, data[key], persist=True)

            new_detector = create_rule_detector(
                ddos_threshold=CONFIG['ddos_threshold'],
                port_scan_threshold=CONFIG['port_scan_threshold'],
                syn_flood_threshold=CONFIG['syn_flood_threshold'],
                udp_flood_threshold=CONFIG['udp_flood_threshold'],
                brute_force_threshold=CONFIG['brute_force_threshold'],
                brute_force_window=CONFIG['brute_force_window'],
                lateral_movement_threshold=CONFIG['lateral_movement_threshold'],
            )
            # O-18: 热更新时迁移追踪器状态，避免清空暴力破解/横向移动滑窗
            old_detector = _helpers_module._rule_detector
            if hasattr(old_detector, '_bf_tracker'):
                new_detector._bf_tracker = old_detector._bf_tracker
            if hasattr(old_detector, '_lateral_tracker'):
                new_detector._lateral_tracker = old_detector._lateral_tracker
            # R-13 fix: 同时更新 helpers 模块级 _rule_detector 和 dual_detector 的引用
            _helpers_module._rule_detector = new_detector
            dual_detector.rule_detector = new_detector

            return jsonify({'status': 'success', 'config': CONFIG})
    with _state_lock:
        return jsonify(dict(CONFIG))


# ── 数据管理 API ──────────────────────────────────────────────────────

@bp_admin.route("/api/save")
def save_data():
    """保存流量数据到 CSV 文件
    ---
    tags: [数据管理]
    responses:
      200:
        description: 保存成功
    """
    save_traffic_data()
    return jsonify({'status': 'success', 'message': '数据已保存到 traffic_stats.csv'})


@bp_admin.route("/api/cleanup")
def api_cleanup():
    """清理过期历史数据
    ---
    tags: [数据管理]
    parameters:
      - name: days
        in: query
        type: integer
        default: 7
        description: 保留最近N天的数据
    responses:
      200:
        description: 清理结果
    """
    days = _int_param("days", 7, min_val=1)
    result = cleanup_old_data(days=days)
    return jsonify({'status': 'success', **result})


# ── 攻击模拟 API ──────────────────────────────────────────────────────

@bp_admin.route("/api/attack/start", methods=['POST'])
@_csrf_exempt
@write_limit
def api_attack_start():
    """启动攻击模拟
    ---
    tags: [攻击模拟]
    parameters:
      - name: body
        in: body
        required: false
        schema:
          type: object
          properties:
            type: {type: string, enum: [syn_flood, port_scan, udp_flood, brute_force, lateral, all], default: all, description: 攻击类型}
            duration: {type: integer, default: 30, description: 持续时间(秒, 5-300)}
    responses:
      200:
        description: 攻击模拟已启动
      400:
        description: 无效参数
      409:
        description: 攻击模拟已在运行
    """
    data = request.get_json() or {}
    attack_type = data.get('type', 'all')
    duration = _clamp_duration(data, default=30, lo=5, hi=300)

    err = start_attack_sim(attack_type, duration)
    if err:
        return jsonify(err[0]), err[1]
    return jsonify({'status': 'success', 'type': attack_type, 'duration': duration})


@bp_admin.route("/api/attack/stop", methods=['POST'])
@_csrf_exempt
@write_limit
def api_attack_stop():
    """停止攻击模拟
    ---
    tags: [攻击模拟]
    responses:
      200:
        description: 攻击模拟已停止
    """
    with sim_state.lock:
        if not sim_state.running or sim_state.sim is None:
            return jsonify({'status': 'success', 'message': '攻击模拟未在运行'})
        sim_state.sim.stop()
        sim_state.running = False
        sim_state.type = ""
        sim_state.duration = 0
        sim_state.sim = None

    logger.info("攻击模拟已停止")
    return jsonify({'status': 'success', 'running': False})


@bp_admin.route("/api/attack/status")
def api_attack_status():
    """获取攻击模拟状态
    ---
    tags: [攻击模拟]
    responses:
      200:
        description: 攻击模拟状态
        schema:
          type: object
          properties:
            running: {type: boolean}
            type: {type: string}
            elapsed_seconds: {type: number}
    """
    with sim_state.lock:
        elapsed = _time.time() - sim_state.start_time if sim_state.running else 0
        return jsonify({
            'running': sim_state.running,
            'type': sim_state.type if sim_state.running else "",
            'elapsed_seconds': round(elapsed, 1),
        })


# ── 环境自检 API ──────────────────────────────────────────────────────

@bp_admin.route("/api/check")
def api_check():
    """环境自检：Python 版本、依赖包、Npcap、模型文件、数据文件
    ---
    tags: [环境自检]
    responses:
      200:
        description: 环境检查结果
        schema:
          type: object
          properties:
            ok: {type: boolean, description: 整体是否通过}
            python: {type: object}
            dependencies: {type: object}
            capture: {type: object}
            model_files: {type: object}
            data_files: {type: object}
    """
    import sys as _sys

    result = {'ok': True}

    # 1. Python 版本
    py_ver = _sys.version_info
    py_ok = py_ver >= (3, 10)
    result['python'] = {
        'version': f'{py_ver.major}.{py_ver.minor}.{py_ver.micro}',
        'ok': py_ok,
        'required': '>=3.10',
    }
    if not py_ok:
        result['ok'] = False

    # 2. 依赖包
    required = [
        ('scapy', 'scapy'), ('flask', 'flask'),
        ('sklearn', 'scikit-learn'), ('joblib', 'joblib'),
        ('pandas', 'pandas'), ('numpy', 'numpy'),
    ]
    optional = [
        ('xgboost', 'xgboost'), ('lightgbm', 'lightgbm'),
    ]
    req_results = []
    for mod, pkg in required:
        try:
            importlib.import_module(mod)
            req_results.append({'package': pkg, 'installed': True})
        except ImportError:
            req_results.append({'package': pkg, 'installed': False})
            result['ok'] = False
    opt_results = []
    for mod, pkg in optional:
        try:
            importlib.import_module(mod)
            opt_results.append({'package': pkg, 'installed': True})
        except ImportError:
            opt_results.append({'package': pkg, 'installed': False})
    result['dependencies'] = {
        'required': req_results,
        'optional': opt_results,
    }

    # 3. Npcap / libpcap
    try:
        from scapy.arch import get_if_addr  # noqa: F401
        capture_ok = True
        capture_msg = 'Npcap/libpcap 可用'
    except Exception:
        capture_ok = False
        capture_msg = 'Npcap/libpcap 不可用 — 抓包功能受限，仍可使用模拟数据'
    result['capture'] = {'ok': capture_ok, 'message': capture_msg}

    # 4. 模型文件
    model_results = []
    for name, path in [('model.pkl', MODEL_PATH), ('evaluation_report.txt', EVALUATION_PATH)]:
        exists = path.exists()
        size_kb = round(path.stat().st_size / 1024, 1) if exists else 0
        model_results.append({'name': name, 'exists': exists, 'size_kb': size_kb})
    result['model_files'] = model_results

    # 5. 数据文件
    data_results = []
    for name, path in [('traffic_data.csv', TRAFFIC_CSV), ('traffic_stats.csv', TRAFFIC_STATS_CSV)]:
        exists = path.exists()
        size_kb = round(path.stat().st_size / 1024, 1) if exists else 0
        data_results.append({'name': name, 'exists': exists, 'size_kb': size_kb})
    result['data_files'] = data_results

    return jsonify(result)


# ── M6: 健康检查端点 ─────────────────────────────────────────────────

@bp_admin.route("/api/health")
def api_health():
    """系统健康检查
    ---
    tags: [运维]
    responses:
      200:
        description: 系统健康
        schema:
          type: object
          properties:
            status: {type: string, enum: [healthy, degraded]}
            timestamp: {type: string}
            uptime_seconds: {type: number}
            components:
              type: object
              properties:
                database: {type: object}
                capture: {type: object}
                ml_model: {type: object}
                memory: {type: object}
                sse: {type: object}
      503:
        description: 系统降级
    """
    import psutil
    import time as _health_time

    health = {
        "status": "healthy",
        "timestamp": _health_time.strftime("%Y-%m-%d %H:%M:%S"),
        "uptime_seconds": round(_health_time.time() - _app_start_time, 1),
        "components": {},
    }

    # 数据库连接状态
    try:
        from campus_ids.web.database import _get_conn
        conn = _get_conn()
        conn.execute("SELECT 1")
        health["components"]["database"] = {"status": "ok"}
    except Exception as e:
        health["components"]["database"] = {"status": "error", "message": str(e)}
        health["status"] = "degraded"

    # 抓包线程状态 & 队列丢包
    with _state_lock:
        health["components"]["capture"] = {
            "status": "running" if _helpers._capture_running else "stopped",
            "dropped_packets": _helpers._dropped_packets,
            "queue_size": _helpers._packet_queue.qsize(),
        }

    # ML 模型状态
    ml_stats = dual_detector.get_stats()
    ml_buffer_len = len(dual_detector._flow_buffer)
    ml_buffer_oldest_age = None
    if ml_buffer_len > 0:
        try:
            oldest_ts = dual_detector._flow_buffer[0].get("timestamp", 0)
            if oldest_ts:
                ml_buffer_oldest_age = round(_health_time.time() - oldest_ts, 1)
        except Exception:
            pass
    health["components"]["ml_model"] = {
        "model_loaded": dual_detector.model_loaded,
        "loop_running": dual_detector.ml_running,
        "buffer_size": ml_buffer_len,
        "buffer_oldest_age_sec": ml_buffer_oldest_age,
        "predict_count": ml_stats.get("ml_predict_count", 0),
        "attack_count": ml_stats.get("ml_attack_count", 0),
    }
    # O-19: 检测延迟统计
    if "detection_latency_ms" in ml_stats:
        health["components"]["detection_latency"] = ml_stats["detection_latency_ms"]

    # 内存使用量
    try:
        process = psutil.Process()
        mem_info = process.memory_info()
        health["components"]["memory"] = {
            "rss_mb": round(mem_info.rss / 1024 / 1024, 1),
            "vms_mb": round(mem_info.vms / 1024 / 1024, 1),
        }
    except (ImportError, Exception):
        health["components"]["memory"] = {"status": "unavailable"}

    # SSE 订阅者数量
    with _sse_lock:
        health["components"]["sse"] = {
            "subscribers": len(_sse_subscribers),
        }

    # O-19: tick 耗时与线程存活状态
    health["components"]["detector_tick"] = {
        "running": _helpers._detector_tick_running,
        "last_duration_ms": round(_helpers._last_tick_duration_ms, 2),
        "tick_count": _helpers._tick_count,
    }
    health["components"]["threads"] = {
        "capture_alive": _helpers._capture_thread is not None and _helpers._capture_thread.is_alive(),
        "enhanced_capture_alive": _helpers._enhanced_capture_thread is not None and _helpers._enhanced_capture_thread.is_alive(),
        "detector_tick_alive": _helpers._detector_tick_thread is not None and _helpers._detector_tick_thread.is_alive(),
        "ml_loop_alive": dual_detector._ml_thread is not None and dual_detector._ml_thread.is_alive(),
    }

    status_code = 200 if health["status"] == "healthy" else 503
    return jsonify(health), status_code