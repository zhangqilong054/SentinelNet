"""web_new/api/alerts.py — 告警 API 路由。

端点：
- GET  /api/alerts        告警列表
- GET  /api/alerts/stats  告警统计
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from campus_ids.web_new.security import Readonly
from campus_ids.web_new.schemas import AlertListResponse, AlertStatsResponse

router = APIRouter(prefix="/api", tags=["alerts"])


@router.get("/alerts", dependencies=[Readonly], summary="查询告警列表（支持分页和级别筛选）")
async def get_alerts(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AlertListResponse:
    """获取告警列表。"""
    # 阶段1空壳，阶段2接入 AlertService
    return AlertListResponse(alerts=[], total=0)


@router.get("/alerts/stats", dependencies=[Readonly], summary="获取告警统计")
async def get_alert_stats(request: Request) -> AlertStatsResponse:
    """获取告警统计。"""
    # 阶段1空壳，阶段2接入 AlertService
    return AlertStatsResponse()