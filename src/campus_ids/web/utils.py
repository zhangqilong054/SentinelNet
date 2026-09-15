"""Web 层共享工具函数。"""
from __future__ import annotations

from flask import request

from campus_ids.config import API_TOKEN, AUTH_ENABLED

# ── CSRF 豁免支持 ─────────────────────────────────────────────────────
# _csrf 由 app.py 初始化后设置，供蓝图中的 @_csrf_exempt 使用
_csrf = None


def _csrf_exempt(view_func):
    """CSRF 豁免装饰器 — Bearer 模式下豁免，会话模式下不豁免。

    O-09: 会话模式（_LOGIN_ENABLED）下恢复 CSRF 实效，
    Bearer 模式（AUTH_ENABLED）维持豁免（token 已提供认证）。
    SSE/健康检查等端点始终豁免（通过 _csrf_always_exempt）。
    """
    if _csrf is not None and AUTH_ENABLED:
        return _csrf.exempt(view_func)
    return view_func


def _csrf_always_exempt(view_func):
    """CSRF 始终豁免装饰器 — 用于 SSE、健康检查等端点。"""
    if _csrf is not None:
        return _csrf.exempt(view_func)
    return view_func


def _check_auth() -> bool:
    """检查 API 认证。支持 Header 和 Query 参数两种方式。"""
    if not AUTH_ENABLED:
        return True
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:] == API_TOKEN
    query_token = request.args.get("token", "")
    return query_token == API_TOKEN


def _int_param(name: str, default: int, *, min_val: int = 0, max_val: int | None = None) -> int:
    """从 request.args 解析整数参数，带范围校验和容错。"""
    raw = request.args.get(name, str(default))
    try:
        val = int(raw)
    except (ValueError, TypeError):
        return default
    val = max(val, min_val)
    if max_val is not None:
        val = min(val, max_val)
    return val


def _clamp_duration(data: dict, default: int = 30, lo: int = 5, hi: int = 300) -> int:
    """从请求体解析 duration 并钳制到 [lo, hi] 范围。"""
    try:
        val = int(data.get('duration', default))
    except (ValueError, TypeError):
        val = default
    return max(lo, min(hi, val))