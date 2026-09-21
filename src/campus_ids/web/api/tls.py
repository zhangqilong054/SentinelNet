"""web/api/tls.py — TLS 分析 API 路由。

端点：
- GET /api/tls/analyze     TLS 异常分析（聚合统计）
- GET /api/tls/stats       TLS 加密流量统计
- GET /api/tls/suspicious  可疑 TLS 记录

取代旧 Flask 端点 GET /api/tls/stats + GET /api/tls/suspicious。
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from campus_ids.web.deps import get_service
from campus_ids.web.security import Readonly
from campus_ids.web.schemas import TlsAnalysisResponse

router = APIRouter(prefix="/api", tags=["tls"])


def _get_tls_analyzer(request: Request):
    """从应用状态获取 TLS 分析器。"""
    return get_service(request, "tls_analyzer", "TLS分析")


@router.get("/tls/analyze", dependencies=[Readonly], summary="TLS异常分析")
async def analyze_tls(request: Request) -> TlsAnalysisResponse:
    """TLS 异常分析（聚合统计 + 可疑记录摘要）。"""
    analyzer = _get_tls_analyzer(request)
    stats = analyzer.get_stats()
    suspicious = analyzer.get_suspicious_records(limit=10)
    return TlsAnalysisResponse(
        anomalies=suspicious,
        summary=stats,
    )


@router.get("/tls/stats", dependencies=[Readonly], summary="TLS加密流量统计")
async def get_tls_stats(request: Request) -> dict:
    """获取 TLS 加密流量统计。"""
    analyzer = _get_tls_analyzer(request)
    return analyzer.get_stats()


@router.get("/tls/suspicious", dependencies=[Readonly], summary="获取可疑TLS记录")
async def get_tls_suspicious(
    request: Request,
    limit: int = Query(default=50, ge=1, le=1000),
) -> dict:
    """获取可疑 TLS 记录列表。"""
    analyzer = _get_tls_analyzer(request)
    records = analyzer.get_suspicious_records(limit=limit)
    return {"records": records, "count": len(records)}