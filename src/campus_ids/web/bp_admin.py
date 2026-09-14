"""配置管理 & 数据管理 & 攻击模拟 & 环境自检 & 健康检查蓝图。"""
from __future__ import annotations

import importlib
import logging
import threading
import time as _time

from flask import Blueprint, jsonify, request

from campus_ids.config import (
    MODEL_PATH, EVALUATION_PATH, TRAFFIC_CSV, TRAFFIC_STATS_CSV,
)
from campus_ids.detector.detector import create_rule_detector
from campus_ids.web.helpers import (
    CONFIG, _capture_running, _state_lock, dual_detector,
    save_traffic_data, update_config,
)
from campus_ids.web.database import cleanup_old_data
from campus_ids.web.sse import _sse_lock, _sse_subscribers
from campus_ids.web.utils import _clamp_duration, _csrf_exempt, _int_param

import campus_ids.web.helpers as _helpers_module

logger = logging.getLogger(__name__)

bp_admin = Blueprint("admin", __name__)

# ── 应用启动时间（用于 uptime 计算）──────────────────────────────────
_app_start_time = _time.time()


# ── 攻击模拟全局状态 ────────────────────────────────────────────────

class AttackSimState:
    """攻击模拟状态管理（封装全局变量，消除 global 语句）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.sim = None          # AttackSimulator 实例
        self.running = False
        self.type = ""
        self.start_time = 0.0
        self.duration = 0        # 攻击持续时长（秒），用于自动停止检测


_sim_state = AttackSimState()


def _attack_watchdog(duration: int) -> None:
    """攻击模拟看门狗：在 duration 到期后自动清理状态。"""
    _time.sleep(duration + 1)  # 等待攻击自然结束 + 1秒缓冲
    with _sim_state.lock:
        if _sim_state.running and _sim_state.sim is not None:
            # 检查所有攻击线程是否已结束
            all_dead = all(not t.is_alive() for t in _sim_state.sim._threads)
            if all_dead:
                _sim_state.running = False
                _sim_state.type = ""
                _sim_state.duration = 0
                _sim_state.sim = None
                logger.info("攻击模拟已自动结束（持续 %ds）", duration)


def _start_attack_sim(attack_type: str, duration: int) -> tuple | None:
    """启动攻击模拟的公共逻辑。

    Returns:
        tuple: (error_response, status_code) 如果启动失败
        None: 如果启动成功
    """
    valid_types = ('syn_flood', 'port_scan', 'udp_flood', 'brute_force', 'lateral', 'all')
    if attack_type not in valid_types:
        return ({'status': 'failed', 'message': f'无效攻击类型，可选: {valid_types}'}), 400

    with _sim_state.lock:
        if _sim_state.running:
            return ({'status': 'failed', 'message': '攻击模拟正在运行中，请先停止'}), 409

        from campus_ids.demo.attack_sim import AttackSimulator
        _sim_state.sim = AttackSimulator()
        _sim_state.type = attack_type
        _sim_state.start_time = _time.time()
        _sim_state.duration = duration
        _sim_state.running = True

        if attack_type == 'all':
            _sim_state.sim.start_all(duration)
        else:
            method_map = {
                'syn_flood': _sim_state.sim.inject_syn_flood,
                'port_scan': _sim_state.sim.inject_port_scan,
                'udp_flood': _sim_state.sim.inject_udp_flood,
                'brute_force': _sim_state.sim.inject_brute_force,
                'lateral': _sim_state.sim.inject_lateral_movement,
            }
            t = threading.Thread(target=method_map[attack_type], args=(duration,), daemon=True)
            t.start()
            _sim_state.sim._threads.append(t)

        watchdog = threading.Thread(target=_attack_watchdog, args=(duration,), daemon=True)
        watchdog.start()

    logger.info("攻击模拟已启动: type=%s, duration=%ds", attack_type, duration)
    return None


# ── 配置管理 API ──────────────────────────────────────────────────────

@bp_admin.route("/api/config", methods=['GET', 'POST'])
@_csrf_exempt
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

    err = _start_attack_sim(attack_type, duration)
    if err:
        return jsonify(err[0]), err[1]
    return jsonify({'status': 'success', 'type': attack_type, 'duration': duration})


@bp_admin.route("/api/attack/stop", methods=['POST'])
@_csrf_exempt
def api_attack_stop():
    """停止攻击模拟
    ---
    tags: [攻击模拟]
    responses:
      200:
        description: 攻击模拟已停止
    """
    with _sim_state.lock:
        if not _sim_state.running or _sim_state.sim is None:
            return jsonify({'status': 'success', 'message': '攻击模拟未在运行'})
        _sim_state.sim.stop()
        _sim_state.running = False
        _sim_state.type = ""
        _sim_state.duration = 0
        _sim_state.sim = None

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
    with _sim_state.lock:
        elapsed = _time.time() - _sim_state.start_time if _sim_state.running else 0
        return jsonify({
            'running': _sim_state.running,
            'type': _sim_state.type if _sim_state.running else "",
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

    # 抓包线程状态
    with _state_lock:
        health["components"]["capture"] = {
            "status": "running" if _capture_running else "stopped",
        }

    # ML 模型状态
    health["components"]["ml_model"] = {
        "status": "loaded" if dual_detector._ml_running else "not_loaded",
    }

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

    status_code = 200 if health["status"] == "healthy" else 503
    return jsonify(health), status_code