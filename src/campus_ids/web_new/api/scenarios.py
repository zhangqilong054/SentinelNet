"""web_new/api/scenarios.py — 剧本 API 路由。

端点（D3 三合一）：
- GET  /api/scenarios          剧本列表
- POST /api/scenarios/start    启动剧本
- POST /api/scenarios/stop     停止剧本

取代旧 Flask 3 个编排端点：
- /api/demo/start   → POST /api/scenarios/start {"scenario": "demo"}
- /api/auto/start   → POST /api/scenarios/start {"scenario": "full"}
- /api/attack/start → POST /api/scenarios/start {"scenario": "attack"}
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from campus_ids.services.scenario_service import ScenarioService
from campus_ids.web_new.errors import AlreadyRunningError, ApiError, NotFoundError
from campus_ids.web_new.security import Readonly, Write, limiter
from campus_ids.web_new.schemas import (
    MessageResponse,
    ScenarioInfoResponse,
    ScenarioListResponse,
    ScenarioStartRequest,
    ScenarioStopRequest,
)

router = APIRouter(prefix="/api", tags=["scenarios"])


def _get_scenario_service(request: Request) -> ScenarioService:
    """从应用状态获取剧本服务。"""
    service = getattr(request.app.state, "scenario_service", None)
    if service is None:
        raise ApiError(
            error_code="SERVICE_UNAVAILABLE",
            detail="剧本服务未初始化",
            status_code=503,
        )
    return service


@router.get("/scenarios", dependencies=[Readonly], summary="获取可用剧本列表")
async def list_scenarios(request: Request) -> ScenarioListResponse:
    """获取可用剧本列表（供前端抽屉渲染，替代硬编码按钮）。"""
    service = _get_scenario_service(request)
    scenarios = service.list_scenarios()
    return ScenarioListResponse(
        scenarios=[ScenarioInfoResponse(**s) for s in scenarios]
    )


@router.post("/scenarios/start", dependencies=[Write], summary="启动指定剧本")
@limiter.limit("30/minute")
async def start_scenario(
    body: ScenarioStartRequest,
    request: Request,
) -> MessageResponse:
    """启动指定剧本（按序执行子任务，任一失败则整体失败并回滚）。"""
    service = _get_scenario_service(request)
    try:
        result = service.start_scenario(body.scenario, duration=body.duration)
    except KeyError:
        raise NotFoundError("剧本", body.scenario)

    status = result.get("status")
    if status == "already_running":
        raise AlreadyRunningError(f"剧本 '{body.scenario}'（子任务 '{result.get('task', '')}' 已在运行）")
    if status == "error":
        raise ApiError(
            error_code="SCENARIO_START_FAILED",
            detail=result.get("message", f"剧本 '{body.scenario}' 启动失败"),
            status_code=503,
        )
    steps = result.get("steps", 0)
    return MessageResponse(message=f"剧本 '{body.scenario}' 已启动，{steps} 个子任务")


@router.post("/scenarios/stop", dependencies=[Write], summary="停止指定剧本")
@limiter.limit("30/minute")
async def stop_scenario(
    body: ScenarioStopRequest,
    request: Request,
) -> MessageResponse:
    """停止指定剧本（停止所有子任务，幂等）。"""
    service = _get_scenario_service(request)
    try:
        result = service.stop_scenario(body.scenario)
    except KeyError:
        raise NotFoundError("剧本", body.scenario)

    status = result.get("status")
    if status == "not_running":
        return MessageResponse(message=f"剧本 '{body.scenario}' 无运行中的子任务")
    stopped = result.get("stopped", [])
    return MessageResponse(message=f"剧本 '{body.scenario}' 停止请求已发送，{len(stopped)} 个子任务")