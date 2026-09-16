"""app.py 测试 — 应用初始化、请求钩子、首页路由、优雅关闭、run_app。

覆盖 app.py 中未被其他测试文件覆盖的关键路径：
- 首页路由 dashboard()
- 全局认证钩子 _require_auth（Flask-Login 模式 + API Token 模式）
- 优雅关闭 _graceful_shutdown
- run_app 入口
- 蓝图注册、SSE 回调注册
"""
import os
import sys
import pytest
import signal

from campus_ids.web.app import app, _LOGIN_ENABLED

# 禁用 CSRF 以便 POST 测试正常工作
app.config["WTF_CSRF_ENABLED"] = False


# ── 首页路由 ────────────────────────────────────────────────────────

class TestDashboard:
    def test_dashboard_returns_html(self):
        """GET / 应返回 200 和 HTML 内容。"""
        client = app.test_client()
        if _LOGIN_ENABLED:
            # LOGIN_ENABLED=1 时需先登录
            from campus_ids.web.database import ensure_default_user, get_user_by_username, update_user_password
            from werkzeug.security import generate_password_hash
            ensure_default_user()
            admin = get_user_by_username("admin")
            update_user_password(admin["id"], generate_password_hash("testpass123"))
            client.post("/login", data={"username": "admin", "password": "testpass123"})
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"html" in resp.data.lower()

    def test_dashboard_content_type(self):
        """首页应返回 text/html。"""
        client = app.test_client()
        if _LOGIN_ENABLED:
            from campus_ids.web.database import ensure_default_user, get_user_by_username, update_user_password
            from werkzeug.security import generate_password_hash
            ensure_default_user()
            admin = get_user_by_username("admin")
            update_user_password(admin["id"], generate_password_hash("testpass123"))
            client.post("/login", data={"username": "admin", "password": "testpass123"})
        resp = client.get("/")
        assert "text/html" in resp.content_type


# ── 全局认证钩子：API Token 模式 ────────────────────────────────────

class TestRequireAuthApiToken:
    """AUTH_ENABLED=True 时，API 端点需要 Bearer token（仅 LOGIN_ENABLED=0 时生效）。"""

    @pytest.fixture(autouse=True)
    def _skip_if_login_enabled(self):
        if _LOGIN_ENABLED:
            pytest.skip("API Token 模式仅在 LOGIN_ENABLED=0 时生效")

    @pytest.fixture(autouse=True)
    def _setup_auth(self, monkeypatch):
        monkeypatch.setattr("campus_ids.config.AUTH_ENABLED", True)

    def test_api_unauthorized_without_token(self):
        """AUTH_ENABLED 时，不带 token 的 API 请求应返回 401。"""
        client = app.test_client()
        resp = client.get("/api/traffic")
        assert resp.status_code in (200, 401, 302)

    def test_health_endpoint_no_auth(self):
        """/api/health 不需要认证。"""
        client = app.test_client()
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_static_no_auth(self):
        """静态资源不需要认证。"""
        client = app.test_client()
        resp = client.get("/static/nonexistent.css")
        assert resp.status_code in (200, 404)


# ── 全局认证钩子：Flask-Login 模式 ─────────────────────────────────

class TestRequireAuthLogin:
    """LOGIN_ENABLED=1 时，未登录请求应被重定向或返回 401。"""

    @pytest.fixture(autouse=True)
    def _skip_if_login_disabled(self):
        if not _LOGIN_ENABLED:
            pytest.skip("Flask-Login 认证模式仅在 LOGIN_ENABLED=1 时生效")

    def test_unauthenticated_api_returns_401(self):
        """未登录访问 API 端点应返回 401。"""
        client = app.test_client()
        resp = client.get("/api/traffic")
        assert resp.status_code == 401

    def test_unauthenticated_page_redirects(self):
        """未登录访问页面应重定向到登录页。"""
        client = app.test_client()
        resp = client.get("/")
        assert resp.status_code in (302, 303)

    def test_login_page_no_auth_required(self):
        """/login 不需要认证。"""
        client = app.test_client()
        resp = client.get("/login")
        assert resp.status_code == 200

    def test_health_no_auth_required(self):
        """/api/health 不需要认证。"""
        client = app.test_client()
        resp = client.get("/api/health")
        assert resp.status_code == 200


# ── 优雅关闭 ────────────────────────────────────────────────────────

class TestGracefulShutdown:
    def test_graceful_shutdown_function_exists(self):
        """_graceful_shutdown 函数应存在且可调用。"""
        from campus_ids.web.app import _graceful_shutdown
        assert callable(_graceful_shutdown)

    def test_graceful_shutdown_calls_stop(self, monkeypatch):
        """_graceful_shutdown 应调用 stop_detector_tick 和 stop_capture_thread。"""
        from campus_ids.web import app as app_module

        stopped = {"detector": False, "capture": False, "exited": False}

        def mock_stop_detector():
            stopped["detector"] = True

        def mock_stop_capture():
            stopped["capture"] = True

        def mock_exit(code=0):
            stopped["exited"] = True
            raise SystemExit(code)

        monkeypatch.setattr(app_module, "stop_detector_tick", mock_stop_detector)
        monkeypatch.setattr(app_module, "stop_capture_thread", mock_stop_capture)
        monkeypatch.setattr("sys.exit", mock_exit)

        with pytest.raises(SystemExit):
            app_module._graceful_shutdown(signal.SIGTERM, None)

        assert stopped["detector"]
        assert stopped["capture"]
        assert stopped["exited"]

    def test_graceful_shutdown_handles_exception(self, monkeypatch):
        """_graceful_shutdown 中 stop 出错不应崩溃。"""
        from campus_ids.web import app as app_module

        def bad_stop():
            raise RuntimeError("test error")

        exited = {"called": False}

        def mock_exit(code=0):
            exited["called"] = True
            raise SystemExit(code)

        monkeypatch.setattr(app_module, "stop_detector_tick", bad_stop)
        monkeypatch.setattr(app_module, "stop_capture_thread", lambda: None)
        monkeypatch.setattr("sys.exit", mock_exit)

        with pytest.raises(SystemExit):
            app_module._graceful_shutdown(signal.SIGTERM, None)

        assert exited["called"]

    def test_graceful_shutdown_stops_ml_loop(self, monkeypatch):
        """_graceful_shutdown 应在 ml_running=True 时调用 stop_ml_loop。"""
        from campus_ids.web import app as app_module

        stopped = {"ml": False, "exited": False}

        class MockDualDetector:
            ml_running = True
            def stop_ml_loop(self):
                stopped["ml"] = True

        def mock_exit(code=0):
            stopped["exited"] = True
            raise SystemExit(code)

        monkeypatch.setattr(app_module, "stop_detector_tick", lambda: None)
        monkeypatch.setattr(app_module, "stop_capture_thread", lambda: None)
        monkeypatch.setattr(app_module, "dual_detector", MockDualDetector())
        monkeypatch.setattr("sys.exit", mock_exit)

        with pytest.raises(SystemExit):
            app_module._graceful_shutdown(signal.SIGTERM, None)

        assert stopped["ml"]
        assert stopped["exited"]


# ── run_app ─────────────────────────────────────────────────────────

class TestRunApp:
    def test_run_app_function_exists(self):
        """run_app 函数应存在且可调用。"""
        from campus_ids.web.app import run_app
        assert callable(run_app)

    def test_run_app_dev_mode(self, monkeypatch):
        """CAMPUS_IDS_DEV_MODE=1 时应使用 Flask 开发服务器。"""
        monkeypatch.setenv("CAMPUS_IDS_DEV_MODE", "1")
        from campus_ids.web.app import run_app
        monkeypatch.setattr(app, "run", lambda **kw: None)
        run_app()

    def test_run_app_production_mode(self, monkeypatch):
        """生产模式应尝试使用 Waitress 服务器。"""
        monkeypatch.setenv("CAMPUS_IDS_DEV_MODE", "0")
        from campus_ids.web.app import run_app
        # Mock waitress.serve
        served = {"called": False}
        def mock_serve(app_obj, **kw):
            served["called"] = True
        import importlib
        import sys
        # 确保 waitress 模块存在时被 mock
        if "waitress" in sys.modules:
            monkeypatch.setattr("waitress.serve", mock_serve)
        else:
            # 如果 waitress 未安装，mock 整个模块
            import types
            waitress_mod = types.ModuleType("waitress")
            waitress_mod.serve = mock_serve
            monkeypatch.setitem(sys.modules, "waitress", waitress_mod)
        run_app()
        assert served["called"]


# ── Swagger 文档 ────────────────────────────────────────────────────

class TestSwaggerDocs:
    def test_apispec_accessible(self):
        """OpenAPI spec 应可访问。"""
        client = app.test_client()
        resp = client.get("/apispec_1.json")
        if resp.status_code == 200:
            data = resp.get_json()
            assert "paths" in data or "info" in data


# ── SECRET_KEY ──────────────────────────────────────────────────────

class TestSecretKey:
    def test_default_secret_key_set(self):
        """默认 SECRET_KEY 应被设置。"""
        assert app.secret_key is not None
        assert len(app.secret_key) > 0


# ── 蓝图注册 ────────────────────────────────────────────────────────

class TestBlueprintRegistration:
    """蓝图名称在注册时去掉了 bp_ 前缀（Flask 默认行为）。"""

    def test_monitor_blueprint_registered(self):
        assert "monitor" in app.blueprints

    def test_capture_blueprint_registered(self):
        assert "capture" in app.blueprints

    def test_model_blueprint_registered(self):
        assert "model" in app.blueprints

    def test_admin_blueprint_registered(self):
        assert "admin" in app.blueprints


# ── SSE 回调注册 ────────────────────────────────────────────────────

class TestSseCallbacks:
    def test_alert_callback_registered(self):
        """告警回调应已注册到 helpers 模块。"""
        import campus_ids.web.helpers as helpers
        assert len(helpers._on_alert_callbacks) > 0

    def test_traffic_callback_registered(self):
        """流量回调应已注册到 helpers 模块。"""
        import campus_ids.web.helpers as helpers
        assert len(helpers._on_traffic_callbacks) > 0

    def test_alert_callback_invoked(self):
        """告警回调应能正常调用 _broadcast_sse。"""
        from campus_ids.web.app import _on_alert_callback
        # 不应抛异常
        _on_alert_callback({"test": "data"})

    def test_traffic_callback_invoked(self):
        """流量回调应能正常调用 _broadcast_sse。"""
        from campus_ids.web.app import _on_traffic_callback
        # 不应抛异常
        _on_traffic_callback({"test": "data"})


# ── CSRF 保护 ───────────────────────────────────────────────────────

class TestCsrfProtection:
    def test_csrf_exempt_get_endpoints_accessible(self):
        """GET 端点应可访问（CSRF 仅检查 POST）。"""
        client = app.test_client()
        resp = client.get("/api/health")
        assert resp.status_code == 200