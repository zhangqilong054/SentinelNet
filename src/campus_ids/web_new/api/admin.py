"""web_new/api/admin.py — 管理操作 API 路由。

端点：
- POST /api/admin/cleanup   清理过期历史数据
- POST /api/admin/export    导出流量数据到 CSV
"""
from __future__ import annotations

import csv
import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from campus_ids.runtime.db import get_connection
from campus_ids.runtime.repositories import UserRepository
from campus_ids.runtime.settings import get_settings
from campus_ids.web_new.errors import ApiError
from campus_ids.web_new.security import Write, limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class CleanupRequest(BaseModel):
    """清理请求。"""
    days: int = Field(default=7, ge=1, description="保留最近N天的数据")


class CleanupResponse(BaseModel):
    """清理响应。"""
    status: str = "success"
    alerts_deleted: int = 0
    traffic_deleted: int = 0


class ExportResponse(BaseModel):
    """导出响应。"""
    status: str = "success"
    message: str = ""
    rows: int = 0


# ── POST /api/admin/cleanup ────────────────────────────────────────

@router.post("/cleanup", dependencies=[Write], summary="清理过期历史数据")
@limiter.limit("10/minute")
async def cleanup_data(body: CleanupRequest, request: Request) -> CleanupResponse:
    """清理过期历史数据 — 删除超过指定天数的告警和流量记录。

    对齐旧 /api/cleanup 端点行为。
    """
    days = body.days
    with get_connection() as conn:
        alerts_deleted, traffic_deleted = UserRepository.cleanup_old_data(conn, days=days)

    logger.info("数据清理完成: 保留 %d 天, 告警删除 %d 条, 流量删除 %d 条",
                days, alerts_deleted, traffic_deleted)
    return CleanupResponse(
        alerts_deleted=alerts_deleted,
        traffic_deleted=traffic_deleted,
    )


# ── POST /api/admin/export ─────────────────────────────────────────

@router.post("/export", dependencies=[Write], summary="导出流量数据到CSV")
@limiter.limit("10/minute")
async def export_data(request: Request) -> ExportResponse:
    """导出流量数据到 CSV — 从 SQLite 查询写入 traffic_stats.csv。

    对齐旧 /api/save 端点行为。
    """
    settings = get_settings()
    csv_path = settings.data_dir / "traffic_stats.csv"

    try:
        from campus_ids.runtime.repositories import TrafficRepository
        with get_connection() as conn:
            rows = TrafficRepository.query(conn, limit=10000)

        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Time", "QPS", "Connections", "PacketCount",
                "PortCount", "SrcIPCount", "Alert",
            ])
            for row in rows:
                writer.writerow([
                    row.time or "",
                    row.qps or "",
                    row.connections or "",
                    row.packet_count or "",
                    row.port_count or "",
                    row.src_ip_count or "",
                    row.alert or "Normal",
                ])

        logger.info("数据已导出到 %s (%d 行)", csv_path, len(rows))
        return ExportResponse(
            message=f"数据已保存到 {csv_path.name}",
            rows=len(rows),
        )
    except Exception as exc:
        logger.error("导出数据失败: %s", exc)
        raise ApiError("EXPORT_FAILED", f"导出数据失败: {exc}", status_code=500)


# ── GET /api/admin/deprecation-stats ──────────────────────────────

@router.get("/deprecation-stats", summary="查询旧端点命中统计（T4.2）")
async def deprecation_stats(request: Request) -> dict[str, Any]:
    """查询旧端点命中统计 — 用于 T4.5 删除判定。

    返回每个旧端点的调用次数、最近来源、最近命中时间。
    命中数为 0 或超过 30 天未命中的端点可安全删除。
    """
    from campus_ids.web_new.deprecation import get_deprecation_tracker
    tracker = get_deprecation_tracker()
    return {
        "sunset_date": "2026-11-01",
        "endpoints": tracker.get_stats(),
        "deletable": tracker.get_deletable_endpoints(),
    }