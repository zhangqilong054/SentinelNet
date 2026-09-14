"""Flask Web 监控面板 — 路由与 API 端点。"""
from __future__ import annotations

import json
import logging
import os
import threading
import time as _time
from functools import wraps
from pathlib import Path

from flask import Flask, render_template, jsonify, request, Response

from campus_ids.config import (
    API_TOKEN, AUTH_ENABLED, MAX_ALERT_API_RETURN, REGISTRY_JSON, TRAFFIC_STATS_CSV,
)
from campus_ids.detector.detector import create_rule_detector
from campus_ids.web.helpers import (
    CONFIG, _capture_running, _state_lock,
    dual_detector, save_traffic_data, start_capture_thread,
    stop_capture_thread, traffic_data, update_traffic_data,
    start_enhanced_capture_thread, stop_enhanced_capture_thread,
    get_enhanced_capture_status, start_auto_thread, get_auto_status,
    update_config,
)
from campus_ids.web.database import (
    query_alerts, count_alerts, query_traffic,
    get_all_config as db_get_all_config, list_models as db_list_models,
    cleanup_old_data, init_db, get_alert_type_distribution,
)
import campus_ids.web.helpers as _helpers_module
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── M6: Flasgger Swagger 文档 ────────────────────────────────────────
try:
    from flasgger import Swagger
    _swagger = Swagger(app, template={
        "info": {
            "title": "SentinelNet API",
            "version": "1.0",
            "description": "校园网加密流量入侵检测系统 — REST API 文档",
        },
        "basePath": "/",
        "tags": [
            {"name": "流量监控", "description": "实时流量统计与监控"},
            {"name": "告警", "description": "入侵检测告警查询"},
            {"name": "配置", "description": "系统配置管理"},
            {"name": "数据管理", "description": "数据集与特征管理"},
            {"name": "抓包控制", "description": "网络抓包启停与状态"},
            {"name": "TLS分析", "description": "TLS 加密流量分析"},
            {"name": "双引擎检测", "description": "规则引擎 + ML 双引擎检测"},
            {"name": "载荷检测", "description": "恶意载荷深度检测"},
            {"name": "SSE实时推送", "description": "Server-Sent Events 实时推送"},
            {"name": "攻击模拟", "description": "攻击模拟与演示"},
            {"name": "模型管理", "description": "ML 模型训练与管理"},
            {"name": "演示模式", "description": "演示模式控制"},
            {"name": "增强抓包", "description": "增强版抓包（18维流特征 + TLS 分析）"},
            {"name": "一键全流程", "description": "一键全流程：抓包 → 训练 → 检测"},
            {"name": "环境自检", "description": "运行环境检查与诊断"},
            {"name": "运维", "description": "系统运维与健康检查"},
        ],
    })
    logger.info("Flasgger Swagger 文档已初始化 → /apidocs/")
except ImportError:
    _swagger = None
    logger.warning("flasgger 未安装，Swagger 文档未启用")

# ── M4: 安全中间件初始化 ────────────────────────────────────────────

# Flask-CORS: 限制跨域来源
try:
    from flask_cors import CORS
    _cors_origins = os.environ.get("CAMPUS_IDS_CORS_ORIGINS", "").split(",")
    _cors_origins = [o.strip() for o in _cors_origins if o.strip()]
    CORS(app, origins=_cors_origins or None)  # None = 同源策略
    logger.info("Flask-CORS 已初始化")
except ImportError:
    logger.warning("flask-cors 未安装，CORS 保护未启用")

# Flask-Limiter: API 速率限制
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    _limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["60 per minute"],
        storage_uri="memory://",
    )
    logger.info("Flask-Limiter 已初始化 (60 次/分钟)")
except ImportError:
    _limiter = None
    logger.warning("flask-limiter 未安装，速率限制未启用")

# Flask-WTF: CSRF 保护
try:
    from flask_wtf.csrf import CSRFProtect
    _csrf = CSRFProtect(app)
    logger.info("Flask-WTF CSRF 保护已初始化")
except ImportError:
    _csrf = None
    logger.warning("flask-wtf 未安装，CSRF 保护未启用")

# Flask-Talisman: 安全响应头
try:
    from flask_talisman import Talisman
    _talisman = Talisman(
        app,
        force_https=False,  # 开发环境不强制 HTTPS
        content_security_policy={
            'default-src': "'self'",
            'script-src': "'self' 'unsafe-inline' 'unsafe-eval'",
            'style-src': "'self' 'unsafe-inline'",
            'img-src': "'self' data:",
            'connect-src': "'self'",
        },
        strict_transport_security=True,
        frame_options='DENY',
    )
    logger.info("Flask-Talisman 安全头已初始化")
except ImportError:
    logger.warning("flask-talisman 未安装，安全响应头未启用")

# Flask-Login: 用户认证（可选启用）
_LOGIN_ENABLED = os.environ.get("CAMPUS_IDS_LOGIN_ENABLED", "0") == "1"
if _LOGIN_ENABLED:
    try:
        from campus_ids.web.auth import init_auth, login_required
        init_auth(app)
        logger.info("Flask-Login 用户认证已启用")
    except ImportError:
        _LOGIN_ENABLED = False
        logger.warning("flask-login 未安装，用户认证未启用")


def _csrf_exempt(view_func):
    """CSRF 豁免装饰器 — 对 SSE 和 API 端点禁用 CSRF 检查。"""
    if _csrf is not None:
        return _csrf.exempt(view_func)
    return view_func

# M3: 注册 SSE 广播回调 — 当 helpers 检测到新告警/流量更新时推送给 SSE 订阅者
def _on_alert_callback(alert_entry: dict) -> None:
    _broadcast_sse("alert", alert_entry)

def _on_traffic_callback(traffic_entry: dict) -> None:
    _broadcast_sse("traffic", traffic_entry)

_helpers_module._on_alert_callbacks.append(_on_alert_callback)
_helpers_module._on_traffic_callbacks.append(_on_traffic_callback)

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

# ── 模型训练全局状态 ────────────────────────────────────────────────
_train_thread = None
_train_lock = threading.Lock()
_train_status = {"running": False, "progress": "", "result": None, "error": None}

# P2-10.5: API 基础认证 — 从配置模块读取


def _check_auth() -> bool:
    """检查 API 认证。支持 Header 和 Query 参数两种方式。"""
    if not AUTH_ENABLED:
        return True
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:] == API_TOKEN
    query_token = request.args.get("token", "")
    return query_token == API_TOKEN


@app.before_request
def _require_auth():
    """全局请求钩子：API 端点需要认证，HTML 页面免认证。"""
    # M4: Flask-Login 会话认证优先
    if _LOGIN_ENABLED:
        from flask_login import current_user
        # 登录页和静态资源免认证
        if request.path.startswith("/login") or request.path.startswith("/logout") or request.path.startswith("/static"):
            return None
        if not current_user.is_authenticated:
            from flask import redirect, url_for
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized", "message": "请先登录"}), 401
            return redirect(url_for("auth.login"))
        return None

    # P2-10.5: API Token 认证（无 Flask-Login 时的降级方案）
    if not AUTH_ENABLED:
        return None
    if request.path == "/" or request.path.startswith("/static"):
        return None
    if request.path.startswith("/api/"):
        if not _check_auth():
            return jsonify({"error": "Unauthorized", "message": "需要有效的 API token"}), 401
    return None


# ── 路由 ────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template('index.html')


@app.route("/api/traffic")
def get_traffic_data():
    """获取当前流量统计数据
    ---
    tags: [流量监控]
    responses:
      200:
        description: 当前流量统计
        schema:
          type: object
          properties:
            qps: {type: number, description: 每秒请求数}
            connections: {type: integer, description: 活跃连接数}
            alert: {type: string, description: 告警状态}
            packet_count: {type: integer, description: 数据包计数}
            port_count: {type: integer, description: 访问端口数}
            src_ip_count: {type: integer, description: 来源IP数}
            syn_packets: {type: integer, description: TCP SYN包数}
            udp_packets: {type: integer, description: UDP包数}
            dns_packets: {type: integer, description: DNS查询数}
    """
    update_traffic_data()
    with _state_lock:
        return jsonify({
            'qps': traffic_data['qps'],
            'connections': traffic_data['connections'],
            'alert': traffic_data['alert'],
            'timestamp': traffic_data['timestamp'],
            'packet_count': traffic_data['packet_count'],
            'port_count': len(set(traffic_data['unique_ports'])),
            'src_ip_count': len(set(traffic_data['src_ips'])),
            'syn_packets': traffic_data['syn_packets'],
            'udp_packets': traffic_data['udp_packets'],
            'dns_packets': traffic_data['dns_packets'],
        })


@app.route("/api/alerts")
def get_alerts():
    """查询告警列表（支持分页和级别筛选）
    ---
    tags: [告警]
    parameters:
      - name: limit
        in: query
        type: integer
        default: 50
        description: 返回条数上限
      - name: offset
        in: query
        type: integer
        default: 0
        description: 偏移量
      - name: level
        in: query
        type: string
        default: all
        description: 级别筛选 (critical/warning/info/all)
    responses:
      200:
        description: 告警列表
        schema:
          type: object
          properties:
            alerts: {type: array, description: 告警记录列表}
            total: {type: integer, description: 符合条件的总数}
            limit: {type: integer}
            offset: {type: integer}
    """
    limit = request.args.get("limit", str(MAX_ALERT_API_RETURN))
    level = request.args.get("level", "all")
    offset = request.args.get("offset", "0")
    try:
        limit = min(int(limit), MAX_ALERT_API_RETURN)
        offset = max(int(offset), 0)
    except (ValueError, TypeError):
        limit = MAX_ALERT_API_RETURN
        offset = 0
    alerts = query_alerts(limit=limit, level=level, offset=offset)
    total = count_alerts(level=level)
    return jsonify({"alerts": alerts, "total": total, "limit": limit, "offset": offset})


@_csrf_exempt
@app.route("/api/config", methods=['GET', 'POST'])
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


@app.route("/api/save")
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


@app.route("/api/traffic/history")
def api_traffic_history():
    """查询流量历史数据（支持分页）
    ---
    tags: [流量监控]
    parameters:
      - name: limit
        in: query
        type: integer
        default: 60
        description: 返回条数上限（最大200）
      - name: offset
        in: query
        type: integer
        default: 0
        description: 偏移量
    responses:
      200:
        description: 流量历史记录
    """
    limit = request.args.get("limit", "60")
    offset = request.args.get("offset", "0")
    try:
        limit = min(int(limit), 200)
        offset = max(int(offset), 0)
    except (ValueError, TypeError):
        limit = 60
        offset = 0
    records = query_traffic(limit=limit, offset=offset)
    return jsonify({"history": records, "limit": limit, "offset": offset})


@app.route("/api/cleanup")
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
    days = request.args.get("days", "7")
    try:
        days = max(int(days), 1)
    except (ValueError, TypeError):
        days = 7
    result = cleanup_old_data(days=days)
    return jsonify({'status': 'success', **result})


@app.route("/api/capture/start", methods=['POST'])
@_csrf_exempt
def api_capture_start():
    """启动网络抓包
    ---
    tags: [抓包控制]
    responses:
      200:
        description: 抓包已启动
    """
    start_capture_thread()
    return jsonify({'status': 'success', 'running': True})


@app.route("/api/capture/stop", methods=['POST'])
@_csrf_exempt
def api_capture_stop():
    """停止网络抓包
    ---
    tags: [抓包控制]
    responses:
      200:
        description: 抓包已停止
    """
    stop_capture_thread()
    return jsonify({'status': 'success', 'running': False})


@app.route("/api/capture/status")
def api_capture_status():
    """查询抓包状态
    ---
    tags: [抓包控制]
    responses:
      200:
        description: 抓包状态
        schema:
          type: object
          properties:
            running: {type: boolean, description: 是否正在抓包}
    """
    with _state_lock:
        running = _capture_running
    return jsonify({'running': running})


@app.route("/api/capture/start-enhanced", methods=['POST'])
@_csrf_exempt
def api_capture_start_enhanced():
    """启动增强抓包（18维流特征 + TLS 分析）
    ---
    tags: [增强抓包]
    parameters:
      - name: body
        in: body
        required: false
        schema:
          type: object
          properties:
            duration: {type: integer, default: 60, description: 抓包时长(秒, 10-600)}
    responses:
      200:
        description: 增强抓包已启动
        schema:
          type: object
          properties:
            status: {type: string}
            duration: {type: integer}
      409:
        description: 增强抓包已在运行
    """
    data = request.get_json() or {}
    duration = int(data.get('duration', 60))
    if duration < 10:
        duration = 10
    if duration > 600:
        duration = 600
    ok = start_enhanced_capture_thread(duration)
    if not ok:
        return jsonify({'status': 'failed', 'message': '增强抓包已在运行中'}), 409
    return jsonify({'status': 'success', 'duration': duration})


@app.route("/api/capture/stop-enhanced", methods=['POST'])
@_csrf_exempt
def api_capture_stop_enhanced():
    """停止增强抓包
    ---
    tags: [增强抓包]
    responses:
      200:
        description: 增强抓包已停止
    """
    stop_enhanced_capture_thread()
    return jsonify({'status': 'success'})


@app.route("/api/capture/enhanced-status")
def api_capture_enhanced_status():
    """查询增强抓包状态
    ---
    tags: [增强抓包]
    responses:
      200:
        description: 增强抓包状态
        schema:
          type: object
          properties:
            running: {type: boolean}
            status: {type: string}
            packets: {type: integer}
            flows: {type: integer}
            duration: {type: integer}
            error: {type: string}
    """
    return jsonify(get_enhanced_capture_status())


@app.route("/api/tls/stats")
def api_tls_stats():
    """获取 TLS 加密流量统计
    ---
    tags: [TLS分析]
    responses:
      200:
        description: TLS 流量统计数据
    """
    from campus_ids.capture.tls_analyzer import tls_analyzer
    return jsonify(tls_analyzer.get_stats())


@app.route("/api/tls/suspicious")
def api_tls_suspicious():
    """获取可疑 TLS 记录
    ---
    tags: [TLS分析]
    parameters:
      - name: limit
        in: query
        type: integer
        default: 50
        description: 返回条数上限（最大1000）
    responses:
      200:
        description: 可疑 TLS 记录列表
    """
    from campus_ids.capture.tls_analyzer import tls_analyzer
    limit = request.args.get("limit", "50")
    try:
        limit = int(limit)
        if limit < 1 or limit > 1000:
            limit = 50
    except (ValueError, TypeError):
        limit = 50
    return jsonify(tls_analyzer.get_suspicious_records(limit=limit))


@app.route("/api/dual/stats")
def api_dual_stats():
    """获取双引擎检测统计
    ---
    tags: [双引擎检测]
    responses:
      200:
        description: 双引擎统计数据（含攻击类型分布）
    """
    stats = dual_detector.get_stats()
    stats['attack_type_distribution'] = get_alert_type_distribution()
    stats['total_alerts'] = count_alerts()
    return jsonify(stats)


@app.route("/api/dual/load", methods=['POST'])
@_csrf_exempt
def api_dual_load():
    """加载 ML 模型
    ---
    tags: [双引擎检测]
    parameters:
      - name: body
        in: body
        required: false
        schema:
          type: object
          properties:
            run_id: {type: string, description: 版本化run ID}
            which: {type: string, enum: [best, latest], description: 模型指针}
            model_path: {type: string, description: 传统pkl路径}
    responses:
      200:
        description: 模型加载成功
      400:
        description: 参数无效或加载失败
    """
    data = request.get_json() or {}
    run_id = data.get('run_id')
    which = data.get('which', 'best')
    model_path = data.get('model_path')

    # 参数校验
    if run_id is not None and (not isinstance(run_id, str) or not run_id.strip()):
        return jsonify({'status': 'failed', 'message': '无效的 run_id'}), 400
    if which not in ('best', 'latest'):
        return jsonify({'status': 'failed', 'message': 'which 必须为 best 或 latest'}), 400
    if model_path is not None and (not isinstance(model_path, str) or not model_path.strip()):
        return jsonify({'status': 'failed', 'message': '无效的 model_path'}), 400

    # 按优先级调用
    if run_id:
        success = dual_detector.load_model(run_id=run_id)
    elif model_path:
        success = dual_detector.load_model(model_path=Path(model_path))
    else:
        success = dual_detector.load_model(which=which)

    if success:
        dual_detector.start_ml_loop(packet_source=dual_detector._drain_flow_buffer)
        artifact = dual_detector._artifact or {}
        return jsonify({
            'status': 'success',
            'model_loaded': True,
            'run_id': artifact.get('run_id', ''),
            'model_type': type(artifact.get('model')).__name__ if artifact.get('model') else '',
        })
    return jsonify({'status': 'failed', 'model_loaded': False}), 400


@app.route("/api/dual/stop", methods=['POST'])
@_csrf_exempt
def api_dual_stop():
    """停止 ML 预测循环
    ---
    tags: [双引擎检测]
    responses:
      200:
        description: ML 预测已停止
    """
    dual_detector.stop_ml_loop()
    return jsonify({'status': 'success', 'ml_running': False})


@app.route("/api/payload/check", methods=['POST'])
@_csrf_exempt
def api_payload_check():
    """应用层载荷检测（SQL 注入 / XSS）
    ---
    tags: [载荷检测]
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required: [payload]
          properties:
            payload: {type: string, description: 待检测的载荷文本}
    responses:
      200:
        description: 检测结果
        schema:
          type: object
          properties:
            alerts: {type: array, description: 告警列表}
            is_anomaly: {type: boolean, description: 是否异常}
            payload_length: {type: integer, description: 载荷长度}
      400:
        description: 参数无效
    """
    data = request.get_json() or {}
    payload = data.get('payload', '')
    if not isinstance(payload, str):
        return jsonify({'error': 'payload 必须为字符串'}), 400
    if not payload:
        return jsonify({'alerts': [], 'is_anomaly': False})
    alerts = dual_detector.rule_detector.check_payload(payload)
    return jsonify({
        'alerts': alerts,
        'is_anomaly': len(alerts) > 0,
        'payload_length': len(payload),
    })


# ── 攻击模拟 API ────────────────────────────────────────────────────

# M3: SSE 实时推送 — 订阅者队列管理
_sse_subscribers: list[queue.Queue] = []
_sse_lock = threading.Lock()


def _broadcast_sse(event: str, data: dict) -> None:
    """向所有 SSE 订阅者广播事件。"""
    msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _sse_lock:
        dead = []
        for i, q in enumerate(_sse_subscribers):
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(i)
        for i in reversed(dead):
            _sse_subscribers.pop(i)


@app.route("/api/stream/traffic")
@_csrf_exempt
def stream_traffic():
    """SSE 实时流量推送
    ---
    tags: [SSE实时推送]
    produces: text/event-stream
    responses:
      200:
        description: "SSE 事件流（event: traffic）"
    """
    def generate():
        q: queue.Queue = queue.Queue(maxsize=64)
        with _sse_lock:
            _sse_subscribers.append(q)
        try:
            # 发送初始数据
            update_traffic_data()
            with _state_lock:
                initial = {
                    'qps': traffic_data['qps'],
                    'connections': traffic_data['connections'],
                    'alert': traffic_data['alert'],
                    'timestamp': traffic_data['timestamp'],
                    'packet_count': traffic_data['packet_count'],
                    'port_count': len(set(traffic_data['unique_ports'])),
                    'src_ip_count': len(set(traffic_data['src_ips'])),
                    'syn_packets': traffic_data['syn_packets'],
                    'udp_packets': traffic_data['udp_packets'],
                    'dns_packets': traffic_data['dns_packets'],
                }
            yield f"event: traffic\ndata: {json.dumps(initial, ensure_ascii=False)}\n\n"
            # 持续推送
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except queue.Empty:
                    # 心跳保活
                    yield ":keepalive\n\n"
        finally:
            with _sse_lock:
                if q in _sse_subscribers:
                    _sse_subscribers.remove(q)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route("/api/stream/alerts")
@_csrf_exempt
def stream_alerts():
    """SSE 实时告警推送
    ---
    tags: [SSE实时推送]
    produces: text/event-stream
    responses:
      200:
        description: "SSE 事件流（event: alert）"
    """
    def generate():
        q: queue.Queue = queue.Queue(maxsize=64)
        with _sse_lock:
            _sse_subscribers.append(q)
        try:
            # 发送最近告警
            recent = query_alerts(limit=5)
            for alert in reversed(recent):
                yield f"event: alert\ndata: {json.dumps(alert, ensure_ascii=False)}\n\n"
            # 持续推送
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except queue.Empty:
                    yield ":keepalive\n\n"
        finally:
            with _sse_lock:
                if q in _sse_subscribers:
                    _sse_subscribers.remove(q)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

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
    duration = max(5, min(300, duration))
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


@app.route("/api/attack/start", methods=['POST'])
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
    duration = int(data.get('duration', 30))

    err = _start_attack_sim(attack_type, duration)
    if err:
        return jsonify(err[0]), err[1]
    return jsonify({'status': 'success', 'type': attack_type, 'duration': max(5, min(300, duration))})


@app.route("/api/attack/stop", methods=['POST'])
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


@app.route("/api/attack/status")
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


# ── 模型管理 API ────────────────────────────────────────────────────

@app.route("/api/model/list")
def api_model_list():
    """列出所有已训练的模型版本
    ---
    tags: [模型管理]
    responses:
      200:
        description: 模型版本列表
        schema:
          type: object
          properties:
            runs: {type: array, description: 模型版本列表}
            count: {type: integer}
            source: {type: string, description: 数据来源(database/registry_json)}
    """
    try:
        # 优先从数据库查询
        runs = db_list_models()
        if runs:
            return jsonify({'runs': runs, 'count': len(runs), 'source': 'database'})
        # 回退到 JSON 文件（兼容旧数据）
        if REGISTRY_JSON.exists():
            runs = json.loads(REGISTRY_JSON.read_text(encoding='utf-8'))
        else:
            runs = []
        return jsonify({'runs': runs, 'count': len(runs), 'source': 'registry_json'})
    except Exception as exc:
        logger.error("读取模型注册表失败: %s", exc)
        return jsonify({'runs': [], 'count': 0, 'error': str(exc)})


@app.route("/api/model/train", methods=['POST'])
@_csrf_exempt
def api_model_train():
    """异步启动模型训练
    ---
    tags: [模型管理]
    parameters:
      - name: body
        in: body
        required: false
        schema:
          type: object
          properties:
            dataset_type: {type: string, enum: [cicids2017, nsl_kdd, local], default: cicids2017, description: 数据集类型}
            balance_method: {type: string, enum: [smote, oversample, undersample, none], default: smote, description: 均衡方法}
            quick: {type: boolean, default: false, description: 快速模式}
    responses:
      200:
        description: 训练已启动
      409:
        description: 训练已在进行中
    """
    global _train_thread

    with _train_lock:
        if _train_status['running']:
            return jsonify({'status': 'failed', 'message': '模型训练正在进行中'}), 409

        data = request.get_json() or {}
        dataset_type = data.get('dataset_type', 'cicids2017')
        balance_method = data.get('balance_method', 'smote')
        quick = bool(data.get('quick', False))

        _train_status['running'] = True
        _train_status['progress'] = '初始化训练...'
        _train_status['result'] = None
        _train_status['error'] = None

        def _train_worker():
            """后台训练线程。"""
            try:
                from campus_ids.model.train import train
                _train_status['progress'] = '加载数据集...'
                result = train(dataset_type=dataset_type,
                               balance_method=balance_method,
                               quick=quick)
                _train_status['result'] = result
                _train_status['progress'] = '训练完成'
            except Exception as exc:
                logger.error("模型训练失败: %s", exc)
                _train_status['error'] = str(exc)
                _train_status['progress'] = f'训练失败: {exc}'
            finally:
                _train_status['running'] = False

        _train_thread = threading.Thread(target=_train_worker, daemon=True)
        _train_thread.start()

    logger.info("模型训练已启动: dataset_type=%s", dataset_type)
    return jsonify({'status': 'success', 'message': '模型训练已启动'})


@app.route("/api/model/train-status")
def api_model_train_status():
    """获取模型训练状态
    ---
    tags: [模型管理]
    responses:
      200:
        description: 训练状态
        schema:
          type: object
          properties:
            running: {type: boolean}
            progress: {type: string}
            result: {type: object}
            error: {type: string}
    """
    with _train_lock:
        return jsonify({
            'running': _train_status['running'],
            'progress': _train_status['progress'],
            'result': _train_status['result'],
            'error': _train_status['error'],
        })


# ── 一键全流程 API ──────────────────────────────────────────────────

@app.route("/api/auto/start", methods=['POST'])
@_csrf_exempt
def api_auto_start():
    """一键全流程：增强抓包 → 训练 → ML 加载
    ---
    tags: [一键全流程]
    parameters:
      - name: body
        in: body
        required: false
        schema:
          type: object
          properties:
            duration: {type: integer, default: 30, description: 抓包时长(秒, 10-300)}
    responses:
      200:
        description: 全流程已启动
        schema:
          type: object
          properties:
            status: {type: string}
            duration: {type: integer}
      409:
        description: 全流程已在运行
    """
    data = request.get_json() or {}
    duration = int(data.get('duration', 30))
    if duration < 10:
        duration = 10
    if duration > 300:
        duration = 300
    ok = start_auto_thread(duration)
    if not ok:
        return jsonify({'status': 'failed', 'message': '全流程已在运行中'}), 409
    return jsonify({'status': 'success', 'duration': duration, 'steps': 3})


@app.route("/api/auto/status")
def api_auto_status():
    """查询一键全流程进度
    ---
    tags: [一键全流程]
    responses:
      200:
        description: 全流程状态
        schema:
          type: object
          properties:
            running: {type: boolean}
            step: {type: integer}
            total_steps: {type: integer}
            step_name: {type: string}
            message: {type: string}
            error: {type: string}
            result: {type: object}
    """
    return jsonify(get_auto_status())


# ── 演示模式 API ────────────────────────────────────────────────────

@app.route("/api/demo/start", methods=['POST'])
@_csrf_exempt
def api_demo_start():
    """一键演示模式：启动抓包 + 攻击模拟 + ML模型加载
    ---
    tags: [演示模式]
    parameters:
      - name: body
        in: body
        required: false
        schema:
          type: object
          properties:
            duration: {type: integer, default: 30, description: 演示时长(秒, 5-300)}
    responses:
      200:
        description: 演示已启动
        schema:
          type: object
          properties:
            status: {type: string}
            duration: {type: integer}
            capture: {type: boolean}
            ml_loaded: {type: boolean}
            attack: {type: boolean}
      409:
        description: 攻击模拟已在运行
    """
    data = request.get_json() or {}
    duration = int(data.get('duration', 30))

    # 1. 启动抓包（如果未运行）
    if not _capture_running:
        start_capture_thread()

    # 2. 尝试加载 ML 模型（如果未加载）
    if not dual_detector._ml_running:
        try:
            dual_detector.load_model(which='best')
            dual_detector.start_ml_loop(packet_source=dual_detector._drain_flow_buffer)
        except Exception as exc:
            logger.warning("演示模式: ML 模型加载失败（继续运行）: %s", exc)

    # 3. 启动攻击模拟（委托公共函数）
    err = _start_attack_sim('all', duration)
    if err:
        return jsonify(err[0]), err[1]

    clamped = max(5, min(300, duration))
    logger.info("演示模式已启动: duration=%ds", clamped)
    return jsonify({
        'status': 'success',
        'duration': clamped,
        'capture': True,
        'ml_loaded': dual_detector._ml_running,
        'attack': True,
    })


# ── 环境自检 API ────────────────────────────────────────────────────

@app.route("/api/check")
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
    import importlib
    import sys as _sys
    from campus_ids.config import MODEL_PATH, EVALUATION_PATH, TRAFFIC_CSV, TRAFFIC_STATS_CSV

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


# ── M6: 健康检查端点 ────────────────────────────────────────────────

@app.route("/api/health")
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


# 应用启动时间（用于 uptime 计算）
_app_start_time = __import__('time').time()


# ── M6: 信号处理（优雅关闭） ────────────────────────────────────────

def _graceful_shutdown(signum, frame):
    """SIGTERM/SIGINT 优雅关闭：停止抓包和 ML 循环。"""
    import sys
    logger.info("收到信号 %d，开始优雅关闭…", signum)
    try:
        stop_capture_thread()
        if dual_detector._ml_running:
            dual_detector.stop_ml()
        logger.info("优雅关闭完成")
    except Exception as e:
        logger.error("优雅关闭出错: %s", e)
    sys.exit(0)


import signal as _signal
_signal.signal(_signal.SIGTERM, _graceful_shutdown)
_signal.signal(_signal.SIGINT, _graceful_shutdown)


def run_app():
    """启动 Web 应用（生产模式使用 Waitress，开发模式使用 Flask 开发服务器）。"""
    import os
    port = CONFIG['port']
    host = '0.0.0.0'
    logger.info("SentinelNet 监控系统启动成功")
    logger.info("访问地址: http://localhost:%s", port)
    logger.info("刷新间隔: %sms", CONFIG['refresh_interval'])
    logger.info("数据保存文件: %s", TRAFFIC_STATS_CSV)

    # 生产模式：Waitress（Windows 原生支持，无需 WSL）
    if os.environ.get('CAMPUS_IDS_DEV_MODE', '0') != '1':
        try:
            from waitress import serve
            logger.info("使用 Waitress 生产服务器")
            serve(app, host=host, port=port, _quiet=True)
        except ImportError:
            logger.warning("waitress 未安装，回退到 Flask 开发服务器（不推荐生产使用）")
            app.run(debug=False, port=port, host=host)
    else:
        # 开发模式
        logger.info("使用 Flask 开发服务器（开发模式）")
        app.run(debug=True, port=port, host=host)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    run_app()