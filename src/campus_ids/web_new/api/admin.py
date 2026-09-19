"""web_new/api/admin.py — 管理操作 API 路由。

端点：
- POST /api/admin/cleanup   清理过期历史数据
- POST /api/admin/export    导出流量数据到 CSV
"""
from __future__ import annotations

import csv
import logging

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
    days: int | None = Field(default=None, ge=1, description="保留最近N天的数据（默认取 Settings.cleanup_days）")


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
    days = body.days if body.days is not None else get_settings().cleanup_days
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


