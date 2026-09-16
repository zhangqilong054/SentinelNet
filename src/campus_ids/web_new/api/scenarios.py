"""web_new/api/scenarios.py — 剧本 API 路由。

端点：
- GET  /api/scenarios          剧本列表
- POST /api/scenarios/start    启动剧本
- POST /api/scenarios/stop     停止剧本
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from campus_ids.web_new.errors import NotFoundError, AlreadyRunningError
from campus_ids.web_new.security import Readonly, Write, limiter
from campus_ids.web_new.schemas import (
    MessageResponse,
    ScenarioListResponse,
    ScenarioStartRequest,
)

router = APIRouter(prefix="/api", tags=["scenarios"])


@router.get("/scenarios", dependencies=[Readonly], summary="获取可用剧本列表")
async def list_scenarios(request: Request) -> ScenarioListResponse:
    """获取可用剧本列表。"""
    # 阶段1空壳，阶段2接入 ScenarioService
    return ScenarioListResponse(scenarios=[])


@router.post("/scenarios/start", dependencies=[Write], summary="启动指定剧本")
@limiter.limit("30/minute")
async def start_scenario(
    body: ScenarioStartRequest,
    request: Request,
) -> MessageResponse:
    """启动指定剧本。"""
    # 阶段1空壳，阶段2接入 ScenarioService
    return MessageResponse(message=f"剧本 '{body.scenario}' 已启动")


@router.post("/scenarios/stop", dependencies=[Write], summary="停止当前剧本")
@limiter.limit("30/minute")
async def stop_scenario(request: Request) -> MessageResponse:
    """停止当前剧本。"""
    # 阶段1空壳，阶段2接入 ScenarioService
    return MessageResponse(message="剧本已停止")