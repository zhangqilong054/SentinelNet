"""web_new/api/tls.py — TLS 分析 API 路由。

端点：
- GET /api/tls/analyze  TLS 异常分析
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from campus_ids.web_new.security import Readonly
from campus_ids.web_new.schemas import TlsAnalysisResponse

router = APIRouter(prefix="/api", tags=["tls"])


@router.get("/tls/analyze", dependencies=[Readonly], summary="TLS异常分析")
async def analyze_tls(request: Request) -> TlsAnalysisResponse:
    """TLS 异常分析。"""
    # 阶段1空壳，阶段2接入 TLS 分析器
    return TlsAnalysisResponse()