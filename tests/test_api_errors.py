"""T1.8 统一错误协议 — 验证 ApiError 体系 + 全局异常处理器。

核心验收：
- ApiError 基类: error_code + detail + status_code + extra → to_dict()
- 5 个快捷子类: NotFoundError/ConflictError/NotRunningError/AlreadyRunningError/ValidationError
- 全局异常处理器: ApiError → JSON, RequestValidationError → JSON, Exception → 500
- register_exception_handlers 注册到 FastAPI
"""
from __future__ import annotations

from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from campus_ids.web_new.errors import (
    AlreadyRunningError,
    ApiError,
    ConflictError,
    NotFoundError,
    NotRunningError,
    ValidationError,
    register_exception_handlers,
)


# ── ApiError 基类 ─────────────────────────────────────────────────


class TestApiErrorBase:
    """验证 ApiError 基类。"""

    def test_basic_creation(self):
        err = ApiError(error_code="TEST_ERROR", detail="测试错误")
        assert err.error_code == "TEST_ERROR"
        assert err.detail == "测试错误"
        assert err.status_code == status.HTTP_400_BAD_REQUEST
        assert err.extra == {}

    def test_custom_status_code(self):
        err = ApiError(error_code="TEST", detail="test", status_code=500)
        assert err.status_code == 500

    def test_extra_fields(self):
        err = ApiError(error_code="TEST", detail="test", extra={"key": "value"})
        assert err.extra == {"key": "value"}

    def test_to_dict(self):
        err = ApiError(
            error_code="TEST_ERROR",
            detail="测试错误",
            status_code=400,
            extra={"task": "capture"},
        )
        d = err.to_dict()
        assert d["error"] == "TEST_ERROR"
        assert d["detail"] == "测试错误"
        assert d["status"] == 400
        assert d["task"] == "capture"

    def test_to_dict_no_extra(self):
        err = ApiError(error_code="TEST", detail="test")
        d = err.to_dict()
        assert "error" in d
        assert "detail" in d
        assert "status" in d
        assert len(d) == 3  # 无额外字段

    def test_is_exception(self):
        err = ApiError(error_code="TEST", detail="test")
        assert isinstance(err, Exception)


# ── NotFoundError ──────────────────────────────────────────────────


class TestNotFoundError:
    """验证 NotFoundError。"""

    def test_basic(self):
        err = NotFoundError("用户")
        assert err.error_code == "NOT_FOUND"
        assert "用户" in err.detail
        assert err.status_code == status.HTTP_404_NOT_FOUND

    def test_with_identifier(self):
        err = NotFoundError("用户", "admin")
        assert "admin" in err.detail
        assert err.status_code == 404

    def test_to_dict(self):
        err = NotFoundError("模型", "v1")
        d = err.to_dict()
        assert d["error"] == "NOT_FOUND"
        assert d["status"] == 404


# ── ConflictError ──────────────────────────────────────────────────


class TestConflictError:
    """验证 ConflictError。"""

    def test_basic(self):
        err = ConflictError("资源冲突")
        assert err.error_code == "CONFLICT"
        assert err.detail == "资源冲突"
        assert err.status_code == status.HTTP_409_CONFLICT

    def test_to_dict(self):
        err = ConflictError("冲突")
        d = err.to_dict()
        assert d["error"] == "CONFLICT"
        assert d["status"] == 409


# ── NotRunningError ────────────────────────────────────────────────


class TestNotRunningError:
    """验证 NotRunningError。"""

    def test_basic(self):
        err = NotRunningError("capture")
        assert err.error_code == "NOT_RUNNING"
        assert "capture" in err.detail
        assert err.status_code == status.HTTP_409_CONFLICT

    def test_to_dict(self):
        err = NotRunningError("detection")
        d = err.to_dict()
        assert d["error"] == "NOT_RUNNING"
        assert d["status"] == 409


# ── AlreadyRunningError ────────────────────────────────────────────


class TestAlreadyRunningError:
    """验证 AlreadyRunningError。"""

    def test_basic(self):
        err = AlreadyRunningError("capture")
        assert err.error_code == "ALREADY_RUNNING"
        assert "capture" in err.detail
        assert err.status_code == status.HTTP_409_CONFLICT

    def test_to_dict(self):
        err = AlreadyRunningError("attack")
        d = err.to_dict()
        assert d["error"] == "ALREADY_RUNNING"
        assert d["status"] == 409


# ── ValidationError ────────────────────────────────────────────────


class TestValidationError:
    """验证 ValidationError。"""

    def test_basic(self):
        err = ValidationError("参数不合法")
        assert err.error_code == "VALIDATION_ERROR"
        assert err.detail == "参数不合法"
        assert err.status_code == status.HTTP_400_BAD_REQUEST

    def test_to_dict(self):
        err = ValidationError("校验失败")
        d = err.to_dict()
        assert d["error"] == "VALIDATION_ERROR"
        assert d["status"] == 400


# ── 全局异常处理器（集成测试）──────────────────────────────────────


class TestExceptionHandlers:
    """验证全局异常处理器在 FastAPI 中正确工作。"""

    def _create_app(self) -> FastAPI:
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/test-api-error")
        def raise_api_error():
            raise ApiError("TEST_ERROR", "测试业务异常", status_code=400)

        @app.get("/test-not-found")
        def raise_not_found():
            raise NotFoundError("资源", "res-1")

        @app.get("/test-already-running")
        def raise_already_running():
            raise AlreadyRunningError("capture")

        @app.get("/test-generic")
        def raise_generic():
            raise RuntimeError("未捕获异常")

        return app

    def test_api_error_handler(self):
        app = self._create_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-api-error")
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"] == "TEST_ERROR"
        assert body["detail"] == "测试业务异常"
        assert body["status"] == 400

    def test_not_found_handler(self):
        app = self._create_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-not-found")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"] == "NOT_FOUND"

    def test_already_running_handler(self):
        app = self._create_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-already-running")
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "ALREADY_RUNNING"

    def test_generic_error_handler(self):
        app = self._create_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-generic")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"] == "INTERNAL_ERROR"
        assert body["status"] == 500