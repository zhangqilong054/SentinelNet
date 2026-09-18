"""web_new/api/alerts.py — 告警 API 路由。

端点：
- GET  /api/alerts        告警列表（分页 + 级别筛选）
- GET  /api/alerts/stats  告警统计

取代旧 Flask 端点 GET /api/alerts。
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from campus_ids.services.alert_service import AlertService
from campus_ids.web_new.deps import get_service
from campus_ids.web_new.errors import ApiError
from campus_ids.web_new.security import Readonly
from campus_ids.web_new.schemas import (
    AlertListResponse,
    AlertResponse,
    AlertStatsResponse,
)

router = APIRouter(prefix="/api", tags=["alerts"])


def _get_alert_service(request: Request) -> AlertService:
    """从应用状态获取告警服务。"""
    return get_service(request, "alert_service", "告警")


@router.get("/alerts", dependencies=[Readonly], summary="查询告警列表（支持分页和级别筛选）")
async def get_alerts(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    level: str = Query(default="all", description="级别筛选: high/medium/low/all"),
) -> AlertListResponse:
    """获取告警列表（从 SQLite alerts 表查询）。"""
    service = _get_alert_service(request)
    result = service.get_alerts(level=level, limit=limit, offset=offset)
    return AlertListResponse(
        alerts=[AlertResponse(**a) for a in result["alerts"]],
        total=result["total"],
        limit=result["limit"],
        offset=result["offset"],
    )


@router.get("/alerts/stats", dependencies=[Readonly], summary="获取告警统计")
async def get_alert_stats(request: Request) -> AlertStatsResponse:
    """获取告警统计（总数、类型分布、级别分布）。"""
    service = _get_alert_service(request)
    result = service.get_alert_stats()
    return AlertStatsResponse(**result)