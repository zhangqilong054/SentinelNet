"""web_new/api/models.py — 模型管理 API 路由。

端点：
- GET   /api/models           模型列表（从 registry.json 读取）
- POST  /api/models/train     启动训练
- GET   /api/models/train/status  训练状态
- DELETE /api/models/{name}   删除模型

范围边界（D4）：只读注册表与产物列表，不重构训练链路内部。
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from campus_ids.runtime.settings import get_settings
from campus_ids.web_new.errors import ConflictError, NotFoundError
from campus_ids.web_new.security import Readonly, Write, limiter
from campus_ids.web_new.schemas import (
    MessageResponse,
    ModelInfoResponse,
    ModelListResponse,
    TrainRequest,
    TrainStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["models"])

# 训练状态（进程内单例，线程安全）
_train_lock = threading.Lock()
_train_status: dict[str, Any] = {
    "running": False,
    "progress": "",
    "result": None,
    "error": None,
}
_train_thread: threading.Thread | None = None


def _get_models_dir() -> Path:
    """获取 models 目录路径。"""
    settings = get_settings()
    return settings.data_dir / "models"


def _read_registry() -> list[dict]:
    """读取 registry.json 中的模型列表。"""
    registry_path = _get_models_dir() / "registry.json"
    if not registry_path.exists():
        return []
    try:
        return json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("读取模型注册表失败: %s", exc)
        return []


# ── GET /api/models ────────────────────────────────────────────────

@router.get("/models", dependencies=[Readonly], summary="获取可用模型列表")
async def list_models(request: Request) -> ModelListResponse:
    """获取可用模型列表 — 从 registry.json 读取。"""
    runs = _read_registry()
    models = []
    for run in runs:
        models.append(ModelInfoResponse(
            name=run.get("run_id", "unknown"),
            version=run.get("model_type", "unknown"),
            created_at=run.get("created_at", ""),
            metrics=run.get("metrics", {}),
        ))
    return ModelListResponse(models=models)


# ── POST /api/models/train ─────────────────────────────────────────

@router.post("/models/train", dependencies=[Write], summary="启动模型训练")
@limiter.limit("30/minute")
async def start_training(body: TrainRequest, request: Request) -> MessageResponse:
    """启动模型训练 — 后台线程异步执行。

    训练参数通过 TaskRegistry 的 train 任务管理，此处为便捷入口。
    """
    global _train_thread

    with _train_lock:
        if _train_status["running"]:
            raise ConflictError("模型训练正在进行中")

        _train_status["running"] = True
        _train_status["progress"] = "初始化训练..."
        _train_status["result"] = None
        _train_status["error"] = None

        # 从 TrainRequest 提取参数
        # 旧端点支持 dataset_type/balance_method/quick，
        # 新 schema 用 dataset/epochs，做兼容映射
        dataset_type = body.dataset if body.dataset else "auto"
        quick = body.epochs <= 3 if body.epochs else False

        def _train_worker():
            """后台训练线程。"""
            try:
                from campus_ids.model.train import train
                _train_status["progress"] = "加载数据集..."
                result = train(dataset_type=dataset_type, quick=quick)
                _train_status["result"] = result
                _train_status["progress"] = "训练完成"
            except Exception as exc:
                logger.error("模型训练失败: %s", exc)
                _train_status["error"] = str(exc)
                _train_status["progress"] = f"训练失败: {exc}"
            finally:
                _train_status["running"] = False

        _train_thread = threading.Thread(target=_train_worker, daemon=True)
        _train_thread.start()

    logger.info("模型训练已启动: dataset_type=%s", dataset_type)
    return MessageResponse(message=f"训练已启动: dataset={dataset_type}")


# ── GET /api/models/train/status ───────────────────────────────────

@router.get("/models/train/status", dependencies=[Readonly], summary="获取训练状态")
async def get_train_status(request: Request) -> TrainStatusResponse:
    """获取训练状态 — 对齐旧 /api/model/train-status 返回格式。"""
    with _train_lock:
        running = _train_status["running"]
        progress = _train_status["progress"]
        result = _train_status["result"]
        error = _train_status["error"]

    # 映射到 TrainStatusResponse
    if running:
        status = "training"
    elif error:
        status = "failed"
    elif result:
        status = "completed"
    else:
        status = "idle"

    return TrainStatusResponse(
        status=status,
        progress=0.0,
        epoch=0,
        total_epochs=0,
        error=error,
    )


# ── DELETE /api/models/{name} ──────────────────────────────────────

@router.delete("/models/{name}", dependencies=[Write], summary="删除指定模型")
@limiter.limit("30/minute")
async def delete_model(name: str, request: Request) -> MessageResponse:
    """删除指定模型 — 从 registry.json 移除并删除 run 目录。"""
    runs = _read_registry()
    target_run = None
    for run in runs:
        if run.get("run_id") == name:
            target_run = run
            break

    if target_run is None:
        raise NotFoundError(f"模型 '{name}' 不存在")

    # 删除 run 目录
    run_dir = target_run.get("run_dir")
    if run_dir:
        run_path = Path(run_dir)
        if run_path.exists():
            import shutil
            shutil.rmtree(run_path, ignore_errors=True)
            logger.info("已删除模型目录: %s", run_dir)

    # 从 registry.json 中移除
    runs = [r for r in runs if r.get("run_id") != name]
    registry_path = _get_models_dir() / "registry.json"
    try:
        registry_path.write_text(json.dumps(runs, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.error("更新注册表失败: %s", exc)

    return MessageResponse(message=f"模型 '{name}' 已删除")