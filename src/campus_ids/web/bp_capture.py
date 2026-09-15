"""抓包控制 & TLS 分析 & 双引擎检测 & 载荷检测蓝图。"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, jsonify, request

from campus_ids.web import helpers as _helpers
from campus_ids.web.helpers import (
    _state_lock,
    dual_detector, start_capture_thread, stop_capture_thread,
    start_enhanced_capture_thread, stop_enhanced_capture_thread,
    get_enhanced_capture_status,
    start_detector_tick, stop_detector_tick,
)
from campus_ids.web.database import count_alerts, get_alert_type_distribution
from campus_ids.web.utils import _int_param, _clamp_duration, _csrf_exempt
from campus_ids.web.limiter import write_limit

logger = logging.getLogger(__name__)

bp_capture = Blueprint("capture", __name__)


# ── 抓包控制 ──────────────────────────────────────────────────────────

@bp_capture.route("/api/capture/start", methods=['POST'])
@_csrf_exempt
@write_limit
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


@bp_capture.route("/api/capture/stop", methods=['POST'])
@_csrf_exempt
@write_limit
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


@bp_capture.route("/api/capture/status")
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
        running = _helpers._capture_running
    return jsonify({'running': running})


# ── 检测节拍控制 ──────────────────────────────────────────────────────

@bp_capture.route("/api/detector/start", methods=['POST'])
@_csrf_exempt
@write_limit
def api_detector_start():
    """启动检测节拍
    ---
    tags: [检测节拍]
    responses:
      200:
        description: 检测节拍已启动
        schema:
          type: object
          properties:
            status: {type: string}
            running: {type: boolean}
    """
    ok = start_detector_tick()
    if not ok:
        return jsonify({'status': 'already_running', 'running': True})
    return jsonify({'status': 'success', 'running': True})


@bp_capture.route("/api/detector/stop", methods=['POST'])
@_csrf_exempt
@write_limit
def api_detector_stop():
    """停止检测节拍
    ---
    tags: [检测节拍]
    responses:
      200:
        description: 检测节拍已停止
        schema:
          type: object
          properties:
            status: {type: string}
            running: {type: boolean}
    """
    stop_detector_tick()
    return jsonify({'status': 'success', 'running': False})


@bp_capture.route("/api/detector/status")
def api_detector_status():
    """查询检测节拍状态
    ---
    tags: [检测节拍]
    responses:
      200:
        description: 检测节拍状态
        schema:
          type: object
          properties:
            running: {type: boolean}
    """
    with _state_lock:
        running = _helpers._detector_tick_running
    return jsonify({'running': running})


# ── 增强抓包 ──────────────────────────────────────────────────────────

@bp_capture.route("/api/capture/start-enhanced", methods=['POST'])
@_csrf_exempt
@write_limit
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
    duration = _clamp_duration(data, default=60, lo=10, hi=600)
    ok = start_enhanced_capture_thread(duration)
    if not ok:
        return jsonify({'status': 'failed', 'message': '增强抓包已在运行中'}), 409
    return jsonify({'status': 'success', 'duration': duration})


@bp_capture.route("/api/capture/stop-enhanced", methods=['POST'])
@_csrf_exempt
@write_limit
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


@bp_capture.route("/api/capture/enhanced-status")
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


# ── TLS 分析 ──────────────────────────────────────────────────────────

@bp_capture.route("/api/tls/stats")
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


@bp_capture.route("/api/tls/suspicious")
def api_tls_suspicious():
    """获取可疑 TLS 记录
    ---
    tags: [TLS分析]
    parameters:
      - name: limit
        in: query
        type: integer
        default: 20
        description: 返回条数上限（最大1000）
    responses:
      200:
        description: 可疑 TLS 记录列表
    """
    from campus_ids.capture.tls_analyzer import tls_analyzer
    limit = _int_param("limit", 50, min_val=1, max_val=1000)
    return jsonify(tls_analyzer.get_suspicious_records(limit=limit))


# ── 双引擎检测 ────────────────────────────────────────────────────────

@bp_capture.route("/api/dual/stats")
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


@bp_capture.route("/api/dual/load", methods=['POST'])
@_csrf_exempt
@write_limit
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
        dual_detector.start_ml_loop()
        artifact = dual_detector._artifact or {}
        return jsonify({
            'status': 'success',
            'model_loaded': True,
            'run_id': artifact.get('run_id', ''),
            'model_type': type(artifact.get('model')).__name__ if artifact.get('model') else '',
        })
    return jsonify({'status': 'failed', 'model_loaded': False}), 400


@bp_capture.route("/api/dual/stop", methods=['POST'])
@_csrf_exempt
@write_limit
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


# ── 载荷检测 ──────────────────────────────────────────────────────────

@bp_capture.route("/api/payload/check", methods=['POST'])
@_csrf_exempt
@write_limit
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