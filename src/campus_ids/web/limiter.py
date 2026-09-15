"""O-04: API 速率限制 — 按端点分级。

GET 查询/SSE 心跳豁免，写操作（start/stop/train/config/attack）从严。
"""
from __future__ import annotations

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[],
        storage_uri="memory://",
    )

    # 写端点限流装饰器：30 次/分钟
    write_limit = limiter.limit("30 per minute")

    # 对同时处理 GET/POST 的端点，仅限流 POST 请求
    write_limit_post = limiter.limit("30 per minute", methods=["POST"])
except ImportError:
    limiter = None  # type: ignore[assignment]

    def write_limit(f):  # type: ignore[misc]
        """无 flask-limiter 时的空装饰器。"""
        return f

    def write_limit_post(f):  # type: ignore[misc]
        """无 flask-limiter 时的空装饰器（仅限 POST）。"""
        return f