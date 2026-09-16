"""web_new/api/models.py — 模型管理 API 路由。

端点：
- GET   /api/models           模型列表
- POST  /api/models/train     启动训练
- GET   /api/models/train/status  训练状态
- DELETE /api/models/{name}   删除模型
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from campus_ids.web_new.errors import NotFoundError
from campus_ids.web_new.security import Readonly, Write, limiter
from campus_ids.web_new.schemas import (
    MessageResponse,
    ModelListResponse,
    TrainRequest,
    TrainStatusResponse,
)

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models", dependencies=[Readonly], summary="获取可用模型列表")
async def list_models(request: Request) -> ModelListResponse:
    """获取可用模型列表。"""
    # 阶段1空壳，阶段2接入 ModelService
    return ModelListResponse(models=[])


@router.post("/models/train", dependencies=[Write], summary="启动模型训练")
@limiter.limit("30/minute")
async def start_training(body: TrainRequest, request: Request) -> MessageResponse:
    """启动模型训练。"""
    # 阶段1空壳，阶段2接入 ModelService.train()
    return MessageResponse(message=f"训练已启动: dataset={body.dataset}, epochs={body.epochs}")


@router.get("/models/train/status", dependencies=[Readonly], summary="获取训练状态")
async def get_train_status(request: Request) -> TrainStatusResponse:
    """获取训练状态。"""
    # 阶段1空壳，阶段2接入 ModelService.train_status()
    return TrainStatusResponse(status="idle")


@router.delete("/models/{name}", dependencies=[Write], summary="删除指定模型")
@limiter.limit("30/minute")
async def delete_model(name: str, request: Request) -> MessageResponse:
    """删除指定模型。"""
    # 阶段1空壳，阶段2接入 ModelService.delete()
    return MessageResponse(message=f"模型 '{name}' 已删除")