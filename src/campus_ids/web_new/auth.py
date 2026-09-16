"""web_new/auth.py — 密码哈希与会话管理。

ADR-0001 §6 #1/#6: 会话认证 + 密码哈希（沿用 werkzeug）。

密码哈希使用 werkzeug.security 的 pbkdf2 实现，确保与现有数据库中
已存储的哈希值兼容。不引入新哈希算法，避免迁移风险。
"""
from __future__ import annotations

from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash


# ── 密码哈希 ──────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """生成密码哈希（werkzeug pbkdf2:sha256）。

    与 Flask 版 auth.py 中 generate_password_hash 兼容，
    现有数据库中的哈希值无需迁移。
    """
    return generate_password_hash(password, method="pbkdf2:sha256")


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码与哈希是否匹配。

    兼容 werkzeug 生成的所有哈希格式（pbkdf2、argon2 等）。
    """
    return check_password_hash(password_hash, password)


# ── 会话管理 ──────────────────────────────────────────────────────

def login_user(request: Any, username: str) -> None:
    """将用户标记为已认证（写入会话）。

    Args:
        request: FastAPI Request 对象（需有 session 属性）
        username: 已认证的用户名
    """
    request.session["user"] = username  # type: ignore[attr-defined]
    request.session["authenticated"] = True  # type: ignore[attr-defined]


def logout_user(request: Any) -> None:
    """清除会话中的认证信息。"""
    request.session.pop("user", None)  # type: ignore[attr-defined]
    request.session.pop("authenticated", None)  # type: ignore[attr-defined]


def get_current_user(request: Any) -> str | None:
    """从会话获取当前已认证用户名。

    Returns:
        用户名或 None（未登录）
    """
    user = request.session.get("user")  # type: ignore[attr-defined]
    if isinstance(user, str) and user:
        return user
    return None


def is_authenticated(request: Any) -> bool:
    """检查当前会话是否已认证。"""
    return bool(request.session.get("authenticated"))  # type: ignore[attr-defined]