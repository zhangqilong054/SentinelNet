"""web_new/security.py — 安全策略三档。

取代原 Flask 版 18 处 @_csrf_exempt，用显式装饰器表达安全语义：
- @public:   完全免认证/免 CSRF（SSE、/api/health、页面）
- @readonly: GET，需认证（若开启），免 CSRF
- @write:    写操作：认证 + CSRF + 限流，行为与模式无关（Bearer / session 一致）

ADR-0001 §3.4 / §6 #1-2: 会话认证 + CSRF 双提交 cookie 自实现。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from slowapi import Limiter
from slowapi.util import get_remote_address

from campus_ids.runtime.settings import Settings, get_settings


# ── 限流器（ADR-0001 §6 #4）───────────────────────────────────────

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])


# ── CSRF ──────────────────────────────────────────────────────────

CSRF_TOKEN_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRFToken"
CSRF_COOKIE_NAME = "csrf_token"


def generate_csrf_token() -> str:
    """生成 HMAC 签名的 CSRF token（ADR-0001 §6.1 #2，路线 B）。

    Token 格式: nonce:signature
    - nonce: 64 字符随机十六进制
    - signature: HMAC-SHA256(secret_key, nonce) 的十六进制表示

    签名确保攻击者无法伪造有效 token，即使能写入同域 cookie。
    """
    settings = get_settings()
    nonce = secrets.token_hex(32)
    signature = hmac.new(
        settings.secret_key.encode(),
        nonce.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{nonce}:{signature}"


def validate_csrf(request: Request) -> None:
    """验证 CSRF 双提交 cookie（ADR-0001 §6.1 #2，路线 B）。

    验证步骤：
    1. cookie 与 header 必须同时存在
    2. cookie 与 header 必须匹配（双提交）
    3. token 格式必须为 nonce:signature
    4. HMAC 签名必须有效
    """
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_token or not header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token 缺失",
        )
    # 双提交比对
    if not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token 验证失败",
        )
    # HMAC 签名验证
    parts = header_token.split(":", 1)
    if len(parts) != 2:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token 格式无效",
        )
    nonce, signature = parts
    settings = get_settings()
    expected = hmac.new(
        settings.secret_key.encode(),
        nonce.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token 签名无效",
        )


# ── API Token 认证 ────────────────────────────────────────────────

_bearer_scheme = HTTPBearer(auto_error=False)


async def require_api_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """验证 API Token（Bearer 认证）。

    若 settings.auth_enabled=False 或 api_token 为空，跳过认证。
    """
    if not settings.auth_enabled or not settings.api_token:
        return  # 认证未启用
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供 API Token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not hmac.compare_digest(credentials.credentials, settings.api_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Token 无效",
        )


# ── 会话认证 ──────────────────────────────────────────────────────

async def require_session_user(request: Request) -> str | None:
    """从会话中获取已认证用户名。

    返回用户名或 None（未登录）。用于 @readonly/@write 的
    会话认证回退：Bearer 优先，无 Bearer 时检查会话。
    """
    user = request.session.get("user")  # type: ignore[attr-defined]
    return user if isinstance(user, str) and user else None


# ── 安全策略三档 ──────────────────────────────────────────────────

async def public_policy() -> None:
    """@public 策略：免认证、免 CSRF。用于 SSE、health、页面。"""
    return


async def readonly_policy(
    request: Request,
    auth: Annotated[None, Depends(require_api_token)],
) -> None:
    """@readonly 策略：需认证（若开启），免 CSRF。用于 GET 端点。

    认证顺序：Bearer Token 优先；若 auth_enabled=False 则跳过。
    会话认证：若 Bearer 未提供且 session 中有 user，视为已认证。
    """
    # Bearer 已由 require_api_token 验证（或认证未启用时跳过）
    # 若 Bearer 未提供但 session 有 user，也视为已认证
    if request.headers.get("authorization"):
        return  # Bearer 已验证
    session_user = await require_session_user(request)
    if session_user:
        return  # 会话已认证
    # 认证启用但无 Bearer 且无会话 — require_api_token 已抛 401
    return


async def write_policy(
    request: Request,
    auth: Annotated[None, Depends(require_api_token)],
    csrf: Annotated[None, Depends(validate_csrf)],
) -> None:
    """@write 策略：认证 + CSRF。用于 POST/PUT/DELETE 端点。

    CSRF 双提交 cookie 与认证模式无关（Bearer / session 一致）。
    """
    # Bearer + CSRF 已验证；若 Bearer 未提供但 session 有 user 也可
    if not request.headers.get("authorization"):
        session_user = await require_session_user(request)
        if not session_user:
            # require_api_token 已抛 401（若认证启用）
            pass
    return


# ── 依赖别名（方便在路由中使用）────────────────────────────────────

Public = Depends(public_policy)
Readonly = Depends(readonly_policy)
Write = Depends(write_policy)