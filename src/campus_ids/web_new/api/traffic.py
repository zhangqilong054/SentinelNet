"""web_new/api/traffic.py — 流量 API 路由。

端点：
- GET /api/traffic        实时流量统计
- GET /api/traffic/history 流量历史
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from campus_ids.web_new.security import Readonly
from campus_ids.web_new.schemas import TrafficStatsResponse, TrafficHistoryResponse

router = APIRouter(prefix="/api", tags=["traffic"])


@router.get("/traffic", dependencies=[Readonly], summary="获取实时流量统计")
async def get_traffic(request: Request) -> TrafficStatsResponse:
    """获取实时流量统计。"""
    # 阶段1空壳，阶段2接入 TrafficService
    return TrafficStatsResponse()


@router.get("/traffic/history", dependencies=[Readonly], summary="获取流量历史数据")
async def get_traffic_history(request: Request) -> TrafficHistoryResponse:
    """获取流量历史数据。"""
    # 阶段1空壳，阶段2接入 TrafficService
    return TrafficHistoryResponse()