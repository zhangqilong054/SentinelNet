"""Flask Web 监控面板 — 路由与 API 端点。"""
from __future__ import annotations

import logging
from functools import wraps
from pathlib import Path

from flask import Flask, render_template, jsonify, request

from campus_ids.config import API_TOKEN, AUTH_ENABLED, MAX_ALERT_API_RETURN
from campus_ids.detector.detector import AnomalyDetector
from campus_ids.web.helpers import (
    CONFIG, OUTPUT_CSV, _capture_running, _rule_detector, _state_lock,
    alert_history, dual_detector, save_traffic_data, start_capture_thread,
    stop_capture_thread, traffic_data, traffic_history, update_traffic_data,
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

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
    with _state_lock:
        return jsonify(alert_history[:MAX_ALERT_API_RETURN])


@app.route("/api/config", methods=['GET', 'POST'])
def config():
    global CONFIG, _rule_detector, dual_detector
    if request.method == 'POST':
        with _state_lock:
            data = request.get_json()
            if data is None:
                return jsonify({'error': 'Invalid JSON'}), 400
            for key in ('ddos_threshold', 'port_scan_threshold', 'syn_flood_threshold',
                        'udp_flood_threshold', 'brute_force_threshold', 'brute_force_window',
                        'lateral_movement_threshold'):
                if key in data:
                    CONFIG[key] = int(data[key])

            _rule_detector = AnomalyDetector(
                ddos_threshold=CONFIG['ddos_threshold'],
                port_scan_threshold=CONFIG['port_scan_threshold'],
                syn_flood_threshold=CONFIG['syn_flood_threshold'],
                udp_flood_threshold=CONFIG['udp_flood_threshold'],
                brute_force_threshold=CONFIG['brute_force_threshold'],
                brute_force_window=CONFIG['brute_force_window'],
                lateral_movement_threshold=CONFIG['lateral_movement_threshold'],
            )
            dual_detector.rule_detector = _rule_detector
            return jsonify({'status': 'success', 'config': CONFIG})
    with _state_lock:
        return jsonify(dict(CONFIG))


@app.route("/api/save")
def save_data():
    save_traffic_data()
    return jsonify({'status': 'success', 'message': '数据已保存到 traffic_stats.csv'})


@app.route("/api/capture/start", methods=['POST'])
def api_capture_start():
    start_capture_thread()
    return jsonify({'status': 'success', 'running': True})


@app.route("/api/capture/stop", methods=['POST'])
def api_capture_stop():
    stop_capture_thread()
    return jsonify({'status': 'success', 'running': False})


@app.route("/api/capture/status")
def api_capture_status():
    with _state_lock:
        running = _capture_running
    return jsonify({'running': running})


@app.route("/api/tls/stats")
def api_tls_stats():
    """P0-7: 加密流量统计 API。"""
    from campus_ids.capture.tls_analyzer import tls_analyzer
    return jsonify(tls_analyzer.get_stats())


@app.route("/api/tls/suspicious")
def api_tls_suspicious():
    """P0-7: 可疑 TLS 记录 API。"""
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
    """P1-#8: 双引擎检测统计 API。"""
    stats = dual_detector.get_stats()
    with _state_lock:
        attack_types = {}
        for entry in alert_history:
            at = entry.get('attack_type', 'Unknown')
            attack_types[at] = attack_types.get(at, 0) + 1
        stats['attack_type_distribution'] = attack_types
        stats['total_alerts'] = len(alert_history)
    return jsonify(stats)


@app.route("/api/dual/load", methods=['POST'])
def api_dual_load():
    """P1-#8: 加载 ML 模型 API。"""
    data = request.get_json() or {}
    model_path = data.get('model_path', 'model.pkl')
    if not isinstance(model_path, str) or not model_path.strip():
        return jsonify({'status': 'failed', 'message': '无效的 model_path'}), 400
    success = dual_detector.load_model(Path(model_path))
    if success:
        dual_detector.start_ml_loop(packet_source=dual_detector._drain_flow_buffer)
        return jsonify({'status': 'success', 'model_loaded': True, 'model_path': model_path})
    return jsonify({'status': 'failed', 'model_loaded': False, 'model_path': model_path}), 400


@app.route("/api/dual/stop", methods=['POST'])
def api_dual_stop():
    """P1-#8: 停止 ML 预测循环 API。"""
    dual_detector.stop_ml_loop()
    return jsonify({'status': 'success', 'ml_running': False})


@app.route("/api/payload/check", methods=['POST'])
def api_payload_check():
    """P1-#9: 应用层载荷检测 API（SQL 注入 / XSS）。"""
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


def run_app():
    logger.info("SentinelNet 监控系统启动成功")
    logger.info("访问地址: http://localhost:%s", CONFIG['port'])
    logger.info("刷新间隔: %sms", CONFIG['refresh_interval'])
    logger.info("数据保存文件: %s", OUTPUT_CSV)
    app.run(debug=False, port=CONFIG['port'], host='0.0.0.0')


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    run_app()