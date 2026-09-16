"""utils.py 工具函数测试 — CSRF 豁免、认证检查、参数解析。"""
import os
import pytest

from campus_ids.web.app import app
from campus_ids.web import utils


@pytest.fixture
def client():
    """Flask test client（禁用认证）。"""
    os.environ.setdefault("CAMPUS_IDS_AUTH_ENABLED", "0")
    os.environ.setdefault("CAMPUS_IDS_LOGIN_ENABLED", "0")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── _csrf_exempt ──────────────────────────────────────────────────

class TestCsrfExempt:
    def test_exempt_when_no_csrf(self):
        """无 CSRF 扩展时，装饰器透传原函数。"""
        utils._csrf = None
        def my_view():
            pass
        result = utils._csrf_exempt(my_view)
        assert result is my_view

    def test_exempt_with_csrf_and_auth_enabled(self, monkeypatch):
        """AUTH_ENABLED 时调用 _csrf.exempt。"""
        monkeypatch.setattr(utils, "AUTH_ENABLED", True)
        mock_csrf = type("CSRF", (), {"exempt": staticmethod(lambda f: "exempted")})()
        utils._csrf = mock_csrf
        def my_view():
            pass
        result = utils._csrf_exempt(my_view)
        assert result == "exempted"
        utils._csrf = None  # 清理


class TestCsrfAlwaysExempt:
    def test_always_exempt_with_csrf(self):
        """有 CSRF 扩展时始终豁免。"""
        mock_csrf = type("CSRF", (), {"exempt": staticmethod(lambda f: "exempted")})()
        utils._csrf = mock_csrf
        def my_view():
            pass
        result = utils._csrf_always_exempt(my_view)
        assert result == "exempted"
        utils._csrf = None

    def test_always_exempt_no_csrf(self):
        utils._csrf = None
        def my_view():
            pass
        result = utils._csrf_always_exempt(my_view)
        assert result is my_view


# ── _check_auth ───────────────────────────────────────────────────

class TestCheckAuth:
    def test_auth_disabled(self, monkeypatch):
        """AUTH_ENABLED=False 时始终返回 True。"""
        monkeypatch.setattr(utils, "AUTH_ENABLED", False)
        with app.test_request_context("/"):
            assert utils._check_auth() is True

    def test_auth_enabled_valid_token(self, monkeypatch):
        monkeypatch.setattr(utils, "AUTH_ENABLED", True)
        monkeypatch.setattr(utils, "API_TOKEN", "test-token")
        with app.test_request_context("/", headers={"Authorization": "Bearer test-token"}):
            assert utils._check_auth() is True

    def test_auth_enabled_invalid_token(self, monkeypatch):
        monkeypatch.setattr(utils, "AUTH_ENABLED", True)
        monkeypatch.setattr(utils, "API_TOKEN", "test-token")
        with app.test_request_context("/", headers={"Authorization": "Bearer wrong"}):
            assert utils._check_auth() is False

    def test_auth_enabled_query_token(self, monkeypatch):
        monkeypatch.setattr(utils, "AUTH_ENABLED", True)
        monkeypatch.setattr(utils, "API_TOKEN", "test-token")
        with app.test_request_context("/?token=test-token"):
            assert utils._check_auth() is True


# ── _int_param ────────────────────────────────────────────────────

class TestIntParam:
    def test_valid_int(self, client):
        with app.test_request_context("/?count=42"):
            assert utils._int_param("count", 0) == 42

    def test_default_when_missing(self, client):
        with app.test_request_context("/"):
            assert utils._int_param("missing", 99) == 99

    def test_invalid_int_uses_default(self, client):
        with app.test_request_context("/?count=abc"):
            assert utils._int_param("count", 10) == 10

    def test_min_val_clamp(self, client):
        with app.test_request_context("/?count=-5"):
            assert utils._int_param("count", 0, min_val=0) == 0

    def test_max_val_clamp(self, client):
        with app.test_request_context("/?count=200"):
            assert utils._int_param("count", 0, max_val=100) == 100


# ── _clamp_duration ───────────────────────────────────────────────

class TestClampDuration:
    def test_valid_duration(self):
        assert utils._clamp_duration({"duration": 60}) == 60

    def test_default_when_missing(self):
        assert utils._clamp_duration({}) == 30

    def test_clamp_low(self):
        assert utils._clamp_duration({"duration": 1}) == 5

    def test_clamp_high(self):
        assert utils._clamp_duration({"duration": 999}) == 300

    def test_invalid_value_uses_default(self):
        assert utils._clamp_duration({"duration": "abc"}) == 30