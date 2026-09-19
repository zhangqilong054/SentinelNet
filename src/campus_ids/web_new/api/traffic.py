"""web_new/api/traffic.py — 流量 API 路由。

端点：
- GET /api/traffic          实时流量统计
- GET /api/traffic/history  流量历史

取代旧 Flask 端点 GET /api/traffic + GET /api/traffic/history。
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from campus_ids.services.traffic_service import TrafficService
from campus_ids.web_new.deps import get_service
from campus_ids.web_new.security import Readonly
from campus_ids.web_new.schemas import (
    TrafficHistoryResponse,
    TrafficHistoryRecord,
    TrafficStatsResponse,
)

router = APIRouter(prefix="/api", tags=["traffic"])


def _get_traffic_service(request: Request) -> TrafficService:
    """从应用状态获取流量服务。"""
    return get_service(request, "traffic_service", "流量")


@router.get("/traffic", dependencies=[Readonly], summary="获取实时流量统计")
async def get_traffic(request: Request) -> TrafficStatsResponse:
    """获取实时流量统计数据（从 RuntimeState 读取内存中的当前值）。"""
    service = _get_traffic_service(request)
    data = service.get_traffic()
    return TrafficStatsResponse(**data)


@router.get("/traffic/history", dependencies=[Readonly], summary="获取流量历史数据")
async def get_traffic_history(
    request: Request,
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> TrafficHistoryResponse:
    """获取流量历史数据（从 SQLite traffic_history 表查询）。"""
    service = _get_traffic_service(request)
    result = service.get_traffic_history(limit=limit, offset=offset)
    return TrafficHistoryResponse(
        history=[TrafficHistoryRecord(**r) for r in result["history"]],
        total=result["total"],
        limit=result["limit"],
        offset=result["offset"],
    )