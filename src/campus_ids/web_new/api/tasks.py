"""web_new/api/tasks.py — 任务 API 路由。

端点（21→3 合并，根因 A）：
- GET  /api/tasks           所有任务状态
- POST /api/tasks/{name}/start  启动任务
- POST /api/tasks/{name}/stop   停止任务
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from campus_ids.web_new.errors import AlreadyRunningError, NotRunningError
from campus_ids.web_new.security import Readonly, Write, limiter
from campus_ids.web_new.schemas import (
    MessageResponse,
    TaskActionRequest,
    TaskListResponse,
    TaskStatusResponse,
)

router = APIRouter(prefix="/api", tags=["tasks"])


@router.get("/tasks", dependencies=[Readonly], summary="获取所有任务状态")
async def list_tasks(request: Request) -> TaskListResponse:
    """获取所有任务状态。"""
    # 阶段1空壳，阶段2接入 TaskRegistry
    return TaskListResponse(tasks=[])


@router.post("/tasks/{name}/start", dependencies=[Write], summary="启动指定任务")
@limiter.limit("30/minute")
async def start_task(
    name: str,
    body: TaskActionRequest,
    request: Request,
) -> MessageResponse:
    """启动指定任务。"""
    # 阶段1空壳，阶段2接入 TaskRegistry.start()
    # if result["status"] == "already_running":
    #     raise AlreadyRunningError(name)
    return MessageResponse(message=f"任务 '{name}' 已启动")


@router.post("/tasks/{name}/stop", dependencies=[Write], summary="停止指定任务")
@limiter.limit("30/minute")
async def stop_task(name: str, request: Request) -> MessageResponse:
    """停止指定任务。"""
    # 阶段1空壳，阶段2接入 TaskRegistry.stop()
    # if result["status"] == "not_running":
    #     raise NotRunningError(name)
    return MessageResponse(message=f"任务 '{name}' 已停止")