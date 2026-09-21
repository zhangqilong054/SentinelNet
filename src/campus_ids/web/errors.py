"""web/errors.py — 统一错误协议。

取代原 Flask 版 6 种不同错误格式（根因 D），统一为：
- ApiError: 业务异常基类，携带 error_code + detail + status_code
- 全局异常处理器: 自动将 ApiError → JSON {"error": ..., "detail": ..., "status": ...}
- 422/500 处理器: 确保所有响应格式一致

ADR-0001 §3.5: 统一错误协议。
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


# ── ApiError 业务异常基类 ──────────────────────────────────────────

class ApiError(Exception):
    """业务异常基类。

    所有业务层抛出的异常应继承此类，由全局处理器统一序列化。

    Attributes:
        error_code: 业务错误码（如 "CAPTURE_NOT_RUNNING"）
        detail: 人类可读的错误描述
        status_code: HTTP 状态码
        extra: 额外字段（可选）
    """

    def __init__(
        self,
        error_code: str,
        detail: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.error_code = error_code
        self.detail = detail
        self.status_code = status_code
        self.extra = extra or {}
        super().__init__(detail)

    def to_dict(self) -> dict[str, Any]:
        """序列化为统一错误响应体。"""
        result = {
            "error": self.error_code,
            "detail": self.detail,
            "status": self.status_code,
        }
        result.update(self.extra)
        return result


# ── 常用业务异常快捷类 ─────────────────────────────────────────────

class NotFoundError(ApiError):
    """资源不存在。"""

    def __init__(self, resource: str, identifier: str = "") -> None:
        msg = f"{resource} 不存在"
        if identifier:
            msg = f"{resource} '{identifier}' 不存在"
        super().__init__(
            error_code="NOT_FOUND",
            detail=msg,
            status_code=status.HTTP_404_NOT_FOUND,
        )


class ConflictError(ApiError):
    """资源冲突（如任务已在运行）。"""

    def __init__(self, detail: str) -> None:
        super().__init__(
            error_code="CONFLICT",
            detail=detail,
            status_code=status.HTTP_409_CONFLICT,
        )


class NotRunningError(ApiError):
    """任务未运行时尝试停止。"""

    def __init__(self, task_name: str) -> None:
        super().__init__(
            error_code="NOT_RUNNING",
            detail=f"任务 '{task_name}' 未在运行",
            status_code=status.HTTP_409_CONFLICT,
        )


class AlreadyRunningError(ApiError):
    """任务已在运行时尝试启动。"""

    def __init__(self, task_name: str) -> None:
        super().__init__(
            error_code="ALREADY_RUNNING",
            detail=f"任务 '{task_name}' 已在运行",
            status_code=status.HTTP_409_CONFLICT,
        )


class ValidationError(ApiError):
    """业务层校验失败（非 HTTP 422 的 Pydantic 校验）。"""

    def __init__(self, detail: str) -> None:
        super().__init__(
            error_code="VALIDATION_ERROR",
            detail=detail,
            status_code=status.HTTP_400_BAD_REQUEST,
        )


class UnauthorizedError(ApiError):
    """认证失败（未登录或凭据无效）。"""

    def __init__(self, detail: str = "认证失败") -> None:
        super().__init__(
            error_code="UNAUTHORIZED",
            detail=detail,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


# ── 全局异常处理器 ─────────────────────────────────────────────────

def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    """ApiError → 统一 JSON 响应。"""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )


def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Pydantic/FastAPI 422 校验错误 → 统一格式。"""
    errors = exc.errors()
    detail_parts = []
    for err in errors:
        loc = " → ".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        detail_parts.append(f"{loc}: {msg}" if loc else msg)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "VALIDATION_ERROR",
            "detail": "; ".join(detail_parts),
            "status": 422,
        },
    )


def _generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常 → 500 统一格式（避免泄露堆栈）。"""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "INTERNAL_ERROR",
            "detail": "服务器内部错误",
            "status": 500,
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器到 FastAPI 应用。"""
    app.add_exception_handler(ApiError, _api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _generic_error_handler)