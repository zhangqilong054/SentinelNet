"""web_new/api/payload.py — 载荷分析 API 路由。

端点：
- POST /api/payload/analyze  载荷分析（SQL 注入 + XSS 检测）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from campus_ids.detector.detector import create_rule_detector
from campus_ids.web_new.security import Write, limiter
from campus_ids.web_new.schemas import PayloadAnalysisRequest, PayloadAnalysisResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["payload"])

# 模块级规则检测器实例（无状态，线程安全）
_rule_detector = create_rule_detector()


@router.post("/payload/analyze", dependencies=[Write], summary="载荷分析")
@limiter.limit("30/minute")
async def analyze_payload(
    body: PayloadAnalysisRequest,
    request: Request,
) -> PayloadAnalysisResponse:
    """对 HTTP 载荷执行应用层检测（SQL 注入 + XSS）。

    对齐旧 /api/payload/analyze 端点行为。
    """
    alerts = _rule_detector.check_payload(body.payload)
    return PayloadAnalysisResponse(
        threats=alerts,
        is_malicious=len(alerts) > 0,
    )