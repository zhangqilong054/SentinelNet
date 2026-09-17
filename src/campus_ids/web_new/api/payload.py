"""web_new/api/payload.py — 载荷分析 API 路由。

端点：
- POST /api/payload/check   载荷送检（旧路径，保持兼容）
- POST /api/payload/analyze 载荷分析（新路径，同逻辑）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from campus_ids.web_new.security import Write, limiter
from campus_ids.web_new.schemas import PayloadAnalysisRequest, PayloadAnalysisResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["payload"])


def _analyze(payload: str, request: Request) -> PayloadAnalysisResponse:
    """载荷分析核心逻辑（SQL 注入 + XSS 检测）。

    从 app.state.rule_detector 获取检测器实例，
    确保阈值热更新后立即生效（模块级单例会让 `/api/settings` 的改动不生效）。

    响应字段名对齐旧 `web/bp_capture.py:368-373`：
    `alerts` / `is_anomaly` / `payload_length`。
    """
    rule_detector = request.app.state.rule_detector
    alerts = rule_detector.check_payload(payload)
    return PayloadAnalysisResponse(
        alerts=alerts,
        is_anomaly=len(alerts) > 0,
        payload_length=len(payload),
    )


@router.post("/payload/check", dependencies=[Write], summary="载荷送检（旧路径）")
@limiter.limit("30/minute")
async def check_payload(
    body: PayloadAnalysisRequest,
    request: Request,
) -> PayloadAnalysisResponse:
    """对 HTTP 载荷执行应用层检测（SQL 注入 + XSS）。

    保留旧路径 /api/payload/check 以兼容现有客户端。
    """
    return _analyze(body.payload, request)


@router.post("/payload/analyze", dependencies=[Write], summary="载荷分析")
@limiter.limit("30/minute")
async def analyze_payload(
    body: PayloadAnalysisRequest,
    request: Request,
) -> PayloadAnalysisResponse:
    """对 HTTP 载荷执行应用层检测（SQL 注入 + XSS）。"""
    return _analyze(body.payload, request)