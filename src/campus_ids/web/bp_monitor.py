"""流量监控 & 告警 & SSE 实时推送蓝图。"""
from __future__ import annotations

import json
import queue

from flask import Blueprint, jsonify, request, Response

from campus_ids.config import MAX_ALERT_API_RETURN
from campus_ids.web.helpers import traffic_data, _state_lock
from campus_ids.web.database import query_alerts, count_alerts, query_traffic
from campus_ids.web.utils import _int_param, _csrf_exempt, _csrf_always_exempt
from campus_ids.web.sse import _sse_subscribers, _sse_lock, MAX_SSE_SUBSCRIBERS

bp_monitor = Blueprint("monitor", __name__)


# ── 流量监控 ──────────────────────────────────────────────────────────

@bp_monitor.route("/api/traffic")
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
    # O-07: 纯读当前状态，不再触发检测
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


@bp_monitor.route("/api/traffic/history")
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
    limit = _int_param("limit", 60, max_val=200)
    offset = _int_param("offset", 0)
    records = query_traffic(limit=limit, offset=offset)
    return jsonify({"history": records, "limit": limit, "offset": offset})


# ── 告警查询 ──────────────────────────────────────────────────────────

@bp_monitor.route("/api/alerts")
def get_alerts():
    """查询告警列表（支持分页和级别筛选）
    ---
    tags: [告警]
    parameters:
      - name: limit
        in: query
        type: integer
        default: 20
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
        description: 级别筛选 (high/medium/low/all)
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
    limit = _int_param("limit", MAX_ALERT_API_RETURN, max_val=MAX_ALERT_API_RETURN)
    level = request.args.get("level", "all")
    offset = _int_param("offset", 0)
    alerts = query_alerts(limit=limit, level=level, offset=offset)
    total = count_alerts(level=level)
    return jsonify({"alerts": alerts, "total": total, "limit": limit, "offset": offset})


# ── SSE 实时推送 ──────────────────────────────────────────────────────

@bp_monitor.route("/api/stream/traffic")
@_csrf_always_exempt
def stream_traffic():
    """SSE 实时流量推送
    ---
    tags: [SSE实时推送]
    produces: text/event-stream
    responses:
      200:
        description: "SSE 事件流（event: traffic）"
      503:
        description: "SSE 订阅数已达上限"
    """
    # O-03: SSE 订阅上限检查
    with _sse_lock:
        if len(_sse_subscribers) >= MAX_SSE_SUBSCRIBERS:
            return jsonify({"error": "SSE 订阅数已达上限，请稍后重试"}), 503

    def generate():
        q: queue.Queue = queue.Queue(maxsize=64)
        with _sse_lock:
            _sse_subscribers.append(q)
        try:
            # 发送初始数据（O-07: 纯读当前状态，不再触发检测）
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


@bp_monitor.route("/api/stream/alerts")
@_csrf_always_exempt
def stream_alerts():
    """SSE 实时告警推送
    ---
    tags: [SSE实时推送]
    produces: text/event-stream
    responses:
      200:
        description: "SSE 事件流（event: alert）"
      503:
        description: "SSE 订阅数已达上限"
    """
    # O-03: SSE 订阅上限检查
    with _sse_lock:
        if len(_sse_subscribers) >= MAX_SSE_SUBSCRIBERS:
            return jsonify({"error": "SSE 订阅数已达上限，请稍后重试"}), 503

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