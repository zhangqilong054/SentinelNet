"""web_new/api/payload.py — 载荷分析 API 路由。

端点：
- POST /api/payload/analyze  载荷分析
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from campus_ids.web_new.security import Write, limiter
from campus_ids.web_new.schemas import PayloadAnalysisRequest, PayloadAnalysisResponse

router = APIRouter(prefix="/api", tags=["payload"])


@router.post("/payload/analyze", dependencies=[Write], summary="载荷分析")
@limiter.limit("30/minute")
async def analyze_payload(
    body: PayloadAnalysisRequest,
    request: Request,
) -> PayloadAnalysisResponse:
    """载荷分析。"""
    # 阶段1空壳，阶段2接入载荷分析器
    return PayloadAnalysisResponse()