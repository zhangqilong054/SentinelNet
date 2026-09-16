"""web_new/api/tasks.py — 任务 API 路由。

端点（21→3 合并，根因 A）：
- GET  /api/tasks           所有任务状态
- POST /api/tasks/{name}/start  启动任务
- POST /api/tasks/{name}/stop   停止任务

取代旧 Flask 7 个状态端点 + 8 个启动端点 + 5 个停止端点。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from campus_ids.runtime.tasks import TaskRegistry
from campus_ids.web_new.errors import AlreadyRunningError, ApiError, NotFoundError
from campus_ids.web_new.security import Readonly, Write, limiter
from campus_ids.web_new.schemas import (
    MessageResponse,
    TaskActionRequest,
    TaskListResponse,
    TaskStatusResponse,
)

router = APIRouter(prefix="/api", tags=["tasks"])


def _get_registry(request: Request) -> TaskRegistry:
    """从应用状态获取任务注册表。"""
    registry = getattr(request.app.state, "task_registry", None)
    if registry is None:
        raise ApiError(
            error_code="SERVICE_UNAVAILABLE",
            detail="任务注册表未初始化",
            status_code=503,
        )
    return registry


@router.get("/tasks", dependencies=[Readonly], summary="获取所有任务状态")
async def list_tasks(request: Request) -> TaskListResponse:
    """获取所有任务状态。

    取代旧端点：
    - GET /api/capture/status
    - GET /api/capture/enhanced-status
    - GET /api/detector/status
    - GET /api/attack/status
    - GET /api/auto/status
    - GET /api/dual/stats
    - GET /api/model/train-status
    """
    registry = _get_registry(request)
    tasks = registry.status_all()
    return TaskListResponse(
        tasks=[TaskStatusResponse(**t) for t in tasks]
    )


@router.post("/tasks/{name}/start", dependencies=[Write], summary="启动指定任务")
@limiter.limit("30/minute")
async def start_task(
    name: str,
    body: TaskActionRequest,
    request: Request,
) -> MessageResponse:
    """启动指定任务。

    限时任务可指定 duration（秒），未指定则使用任务默认时长。
    连续任务忽略 duration 参数。

    取代旧端点：
    - POST /api/capture/start
    - POST /api/capture/start-enhanced
    - POST /api/detector/start
    - POST /api/attack/start
    - POST /api/auto/start
    - POST /api/demo/start
    - POST /api/dual/load
    - POST /api/model/train
    """
    registry = _get_registry(request)
    try:
        result = registry.start(name, duration=body.duration)
    except KeyError:
        raise NotFoundError("任务", name)

    status = result.get("status")
    if status == "already_running":
        raise AlreadyRunningError(name)
    if status == "error":
        raise ApiError(
            error_code="SERVICE_UNAVAILABLE",
            detail=result.get("message", f"任务 '{name}' 暂不可用"),
            status_code=503,
        )
    return MessageResponse(message=f"任务 '{name}' 已启动")


@router.post("/tasks/{name}/stop", dependencies=[Write], summary="停止指定任务")
@limiter.limit("30/minute")
async def stop_task(name: str, request: Request) -> MessageResponse:
    """停止指定任务（幂等）。

    即使任务未在运行也返回成功，与旧端点行为一致。

    取代旧端点：
    - POST /api/capture/stop
    - POST /api/capture/stop-enhanced
    - POST /api/detector/stop
    - POST /api/attack/stop
    - POST /api/dual/stop
    """
    registry = _get_registry(request)
    try:
        result = registry.stop(name)
    except KeyError:
        raise NotFoundError("任务", name)

    status = result.get("status")
    if status == "not_running":
        # 幂等：任务未运行也返回成功（与旧端点行为一致）
        return MessageResponse(message=f"任务 '{name}' 未在运行")
    return MessageResponse(message=f"任务 '{name}' 停止请求已发送")