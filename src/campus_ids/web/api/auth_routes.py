"""web/api/auth_routes.py — 认证 API 路由。

端点：
- POST /api/login          会话登录
- POST /api/logout         会话登出
- POST /api/change-password  修改密码
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from campus_ids.runtime.db import get_connection
from campus_ids.runtime.repositories import UserRepository
from campus_ids.web.auth import (
    authenticate,
    AuthenticationError,
    get_current_user,
    hash_password,
    login_user,
    logout_user,
    verify_password,
)
from campus_ids.web.errors import ApiError, NotFoundError, UnauthorizedError
from campus_ids.web.security import Public, Write, limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])


class LoginRequest(BaseModel):
    """登录请求。"""
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class LoginResponse(BaseModel):
    """登录响应。"""
    status: str = "success"
    message: str = ""
    username: str = ""


class LogoutResponse(BaseModel):
    """登出响应。"""
    status: str = "success"
    message: str = "已登出"


class ChangePasswordRequest(BaseModel):
    """修改密码请求。"""
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=6, max_length=128)


class ChangePasswordResponse(BaseModel):
    """修改密码响应。"""
    status: str = "success"
    message: str = ""


# ── POST /api/login ────────────────────────────────────────────────

@router.post("/login", dependencies=[Public], summary="会话登录")
@limiter.limit("10/minute")
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    """用户名+密码登录，成功后写入会话。"""
    try:
        with get_connection() as conn:
            authenticate(conn, body.username, body.password)
    except AuthenticationError as exc:
        if exc.reason == "account_disabled":
            raise ApiError(error_code="ACCOUNT_DISABLED", detail=exc.message, status_code=403)
        raise UnauthorizedError(exc.message)

    login_user(request, body.username)
    logger.info("用户 %s 登录成功", body.username)
    return LoginResponse(message="登录成功", username=body.username)


# ── POST /api/logout ───────────────────────────────────────────────

@router.post("/logout", dependencies=[Public], summary="会话登出")
@limiter.limit("10/minute")
async def logout(request: Request) -> LogoutResponse:
    """清除会话认证信息。"""
    username = get_current_user(request)
    logout_user(request)
    if username:
        logger.info("用户 %s 已登出", username)
    return LogoutResponse()


# ── POST /api/change-password ──────────────────────────────────────

@router.post("/change-password", dependencies=[Write], summary="修改密码")
@limiter.limit("5/minute")
async def change_password(body: ChangePasswordRequest, request: Request) -> ChangePasswordResponse:
    """修改当前用户密码。需提供旧密码验证。"""
    username = get_current_user(request)
    if not username:
        raise UnauthorizedError("请先登录")

    with get_connection() as conn:
        user = UserRepository.get_by_username(conn, username)
        if user is None:
            raise NotFoundError("用户不存在")

        if not verify_password(body.old_password, user.password_hash):
            raise UnauthorizedError("旧密码错误")

        new_hash = hash_password(body.new_password)
        UserRepository.update_password(conn, username=username, password_hash=new_hash)

    logger.info("用户 %s 修改密码成功", username)
    return ChangePasswordResponse(message="密码修改成功")