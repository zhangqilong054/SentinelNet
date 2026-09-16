"""web_new/api/system.py — 系统 API 路由。

端点：
- GET  /api/health      健康检查
- GET  /api/settings     获取配置
- PUT  /api/settings     更新阈值
- GET  /api/csrf-token   获取 CSRF token
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from campus_ids.web_new.security import Public, Readonly, Write, generate_csrf_token, CSRF_COOKIE_NAME, limiter
from campus_ids.web_new.schemas import (
    HealthResponse,
    MessageResponse,
    SettingsResponse,
    ThresholdUpdateRequest,
)

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", dependencies=[Public], summary="系统健康检查")
async def health_check() -> HealthResponse:
    """健康检查。"""
    return HealthResponse()


@router.get("/settings", dependencies=[Readonly], summary="获取当前配置")
async def get_settings(request: Request) -> SettingsResponse:
    """获取当前配置。"""
    # 阶段1空壳，阶段2接入 Settings
    return SettingsResponse()


@router.put("/settings", dependencies=[Write], summary="更新阈值配置")
@limiter.limit("30/minute")
async def update_settings(
    body: ThresholdUpdateRequest,
    request: Request,
) -> MessageResponse:
    """更新阈值配置。"""
    # 阶段1空壳，阶段2接入 Settings + DB 持久化
    return MessageResponse(message=f"阈值 {body.key} 已更新为 {body.value}")


@router.get("/csrf-token", dependencies=[Public], summary="获取CSRF令牌")
async def get_csrf_token(request: Request) -> JSONResponse:
    """获取 CSRF token 并设置双提交 cookie。

    双提交模式：cookie + X-CSRFToken 头比对。
    cookie 设为 HttpOnly + SameSite=Lax，JS 从响应体读取 token。
    """
    token = generate_csrf_token()
    response = JSONResponse({"csrf_token": token})
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # 开发环境；生产环境应通过配置启用
        path="/",
    )
    return response