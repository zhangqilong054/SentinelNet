"""模型管理 & 训练 & 一键全流程 & 演示模式蓝图。"""
from __future__ import annotations

import json
import logging
import threading

from flask import Blueprint, jsonify, request

from campus_ids.config import REGISTRY_JSON
from campus_ids.web.helpers import (
    _capture_running, dual_detector, start_capture_thread,
    start_auto_thread, get_auto_status,
)
from campus_ids.web.utils import _clamp_duration, _csrf_exempt
from campus_ids.web.bp_admin import _start_attack_sim

logger = logging.getLogger(__name__)

bp_model = Blueprint("model", __name__)

# ── 模型训练全局状态 ──────────────────────────────────────────────────
_train_thread = None
_train_lock = threading.Lock()
_train_status = {"running": False, "progress": "", "result": None, "error": None}


# ── 模型管理 API ──────────────────────────────────────────────────────

@bp_model.route("/api/model/list")
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
            source: {type: string, description: 数据来源(registry_json)}
    """
    try:
        if REGISTRY_JSON.exists():
            runs = json.loads(REGISTRY_JSON.read_text(encoding='utf-8'))
        else:
            runs = []
        return jsonify({'runs': runs, 'count': len(runs), 'source': 'registry_json'})
    except Exception as exc:
        logger.error("读取模型注册表失败: %s", exc)
        return jsonify({'runs': [], 'count': 0, 'error': str(exc)})


@bp_model.route("/api/model/train", methods=['POST'])
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


@bp_model.route("/api/model/train-status")
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


# ── 一键全流程 API ────────────────────────────────────────────────────

@bp_model.route("/api/auto/start", methods=['POST'])
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
    duration = _clamp_duration(data, default=30, lo=10, hi=300)
    ok = start_auto_thread(duration)
    if not ok:
        return jsonify({'status': 'failed', 'message': '全流程已在运行中'}), 409
    return jsonify({'status': 'success', 'duration': duration, 'steps': 3})


@bp_model.route("/api/auto/status")
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

@bp_model.route("/api/demo/start", methods=['POST'])
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
    duration = _clamp_duration(data, default=30, lo=5, hi=300)

    # 1. 启动抓包（如果未运行）
    if not _capture_running:
        start_capture_thread()

    # 2. 尝试加载 ML 模型（如果未加载）
    if not dual_detector._ml_running:
        try:
            dual_detector.load_model(which='best')
            dual_detector.start_ml_loop()
        except Exception as exc:
            logger.warning("演示模式: ML 模型加载失败（继续运行）: %s", exc)

    # 3. 启动攻击模拟（委托公共函数）
    err = _start_attack_sim('all', duration)
    if err:
        return jsonify(err[0]), err[1]

    logger.info("演示模式已启动: duration=%ds", duration)
    return jsonify({
        'status': 'success',
        'duration': duration,
        'capture': True,
        'ml_loaded': dual_detector._ml_running,
        'attack': True,
    })