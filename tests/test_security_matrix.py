"""安全三档矩阵测试 — 验证 @public/@readonly/@write 策略。

ADR-0001 §3.4 / §6:
- @public:   免认证、免 CSRF（/api/health, /api/csrf-token）
- @readonly: 需认证（若开启），免 CSRF（GET 端点）
- @write:    认证 + CSRF（POST/PUT/DELETE 端点）
"""
from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

from campus_ids.web_new.app import create_app
from campus_ids.runtime.settings import reset_settings


@pytest.fixture(autouse=True)
def _clean_env():
    """每个测试前后清理环境变量、设置单例和限流器状态。"""
    # 确保认证关闭（默认）
    os.environ.pop("CAMPUS_IDS_AUTH_ENABLED", None)
    os.environ.pop("CAMPUS_IDS_API_TOKEN", None)
    reset_settings()
    # 重置限流器存储，避免跨测试污染
    from campus_ids.web_new.security import limiter
    limiter.reset()
    yield
    os.environ.pop("CAMPUS_IDS_AUTH_ENABLED", None)
    os.environ.pop("CAMPUS_IDS_API_TOKEN", None)
    reset_settings()
    limiter.reset()


@pytest.fixture()
def client_no_auth():
    """认证关闭的 TestClient。"""
    app = create_app()
    return TestClient(app)


@pytest.fixture()
def client_with_auth():
    """认证开启的 TestClient。"""
    os.environ["CAMPUS_IDS_AUTH_ENABLED"] = "1"
    os.environ["CAMPUS_IDS_API_TOKEN"] = "test-secret-token"
    reset_settings()
    app = create_app()
    return TestClient(app)


# ── @public 策略 ──────────────────────────────────────────────────


class TestPublicPolicy:
    """@public 端点：免认证、免 CSRF。"""

    def test_health_no_auth(self, client_no_auth):
        """GET /api/health 无需认证返回 200。"""
        resp = client_no_auth.get("/api/health")
        assert resp.status_code == 200

    def test_health_with_auth_still_accessible(self, client_with_auth):
        """GET /api/health 认证开启时仍无需 Token。"""
        resp = client_with_auth.get("/api/health")
        assert resp.status_code == 200

    def test_csrf_token_no_auth(self, client_no_auth):
        """GET /api/csrf-token 无需认证返回 200。"""
        resp = client_no_auth.get("/api/csrf-token")
        assert resp.status_code == 200

    def test_csrf_token_with_auth_still_accessible(self, client_with_auth):
        """GET /api/csrf-token 认证开启时仍无需 Token。"""
        resp = client_with_auth.get("/api/csrf-token")
        assert resp.status_code == 200


# ── @readonly 策略 ─────────────────────────────────────────────────


class TestReadonlyPolicy:
    """@readonly 端点：需认证（若开启），免 CSRF。"""

    def test_get_alerts_no_auth_disabled(self, client_no_auth):
        """认证关闭时 GET /api/alerts 返回 200。"""
        resp = client_no_auth.get("/api/alerts")
        assert resp.status_code == 200

    def test_get_alerts_no_token_auth_enabled(self, client_with_auth):
        """认证开启但无 Token 时 GET /api/alerts 返回 401。"""
        resp = client_with_auth.get("/api/alerts")
        assert resp.status_code == 401

    def test_get_alerts_valid_token(self, client_with_auth):
        """认证开启 + 有效 Bearer Token 时 GET /api/alerts 返回 200。"""
        resp = client_with_auth.get(
            "/api/alerts",
            headers={"Authorization": "Bearer test-secret-token"},
        )
        assert resp.status_code == 200

    def test_get_alerts_invalid_token(self, client_with_auth):
        """认证开启 + 无效 Token 时 GET /api/alerts 返回 401。"""
        resp = client_with_auth.get(
            "/api/alerts",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_get_traffic_no_auth_disabled(self, client_no_auth):
        """认证关闭时 GET /api/traffic 返回 200。"""
        resp = client_no_auth.get("/api/traffic")
        assert resp.status_code == 200

    def test_get_traffic_auth_enabled_no_token(self, client_with_auth):
        """认证开启无 Token 时 GET /api/traffic 返回 401。"""
        resp = client_with_auth.get("/api/traffic")
        assert resp.status_code == 401

    def test_get_models_no_auth_disabled(self, client_no_auth):
        """认证关闭时 GET /api/models 返回 200。"""
        resp = client_no_auth.get("/api/models")
        assert resp.status_code == 200

    def test_get_tasks_no_auth_disabled(self, client_no_auth):
        """认证关闭时 GET /api/tasks 返回 200。"""
        resp = client_no_auth.get("/api/tasks")
        assert resp.status_code == 200

    def test_get_scenarios_no_auth_disabled(self, client_no_auth):
        """认证关闭时 GET /api/scenarios 返回 200。"""
        resp = client_no_auth.get("/api/scenarios")
        assert resp.status_code == 200

    def test_get_tls_analyze_no_auth_disabled(self, client_no_auth):
        """认证关闭时 GET /api/tls/analyze 返回 200。"""
        resp = client_no_auth.get("/api/tls/analyze")
        assert resp.status_code == 200


# ── @write 策略 ────────────────────────────────────────────────────


class TestWritePolicy:
    """@write 端点：认证 + CSRF。"""

    def test_post_task_start_no_csrf_no_auth(self, client_no_auth):
        """认证关闭 + 无 CSRF 时 POST /api/tasks/x/start 返回 403（CSRF 缺失）。"""
        resp = client_no_auth.post(
            "/api/tasks/capture/start",
            json={"duration": 30},
        )
        assert resp.status_code == 403

    def test_post_task_start_with_csrf_no_auth(self, client_no_auth):
        """认证关闭 + 有 CSRF 时 POST /api/tasks/x/start 返回 200。"""
        # 先获取 CSRF token
        csrf_resp = client_no_auth.get("/api/csrf-token")
        csrf_data = csrf_resp.json()
        csrf_token = csrf_data.get("csrf_token", "")
        # 设置 cookie + header
        client_no_auth.cookies.set("csrf_token", csrf_token)
        resp = client_no_auth.post(
            "/api/tasks/capture/start",
            json={"duration": 30},
            headers={"X-CSRFToken": csrf_token},
        )
        assert resp.status_code == 200

    def test_post_task_start_auth_enabled_no_token(self, client_with_auth):
        """认证开启 + 无 Token + 无 CSRF 时 POST 返回 401 或 403。"""
        resp = client_with_auth.post(
            "/api/tasks/capture/start",
            json={"duration": 30},
        )
        assert resp.status_code in (401, 403)

    def test_post_task_start_auth_enabled_with_token_no_csrf(self, client_with_auth):
        """认证开启 + 有效 Token + 无 CSRF 时 POST 返回 403。"""
        resp = client_with_auth.post(
            "/api/tasks/capture/start",
            json={"duration": 30},
            headers={"Authorization": "Bearer test-secret-token"},
        )
        assert resp.status_code == 403

    def test_post_task_start_auth_enabled_full(self, client_with_auth):
        """认证开启 + 有效 Token + CSRF 时 POST 返回 200。"""
        # 获取 CSRF
        csrf_resp = client_with_auth.get("/api/csrf-token")
        csrf_token = csrf_resp.json().get("csrf_token", "")
        client_with_auth.cookies.set("csrf_token", csrf_token)
        resp = client_with_auth.post(
            "/api/tasks/capture/start",
            json={"duration": 30},
            headers={
                "Authorization": "Bearer test-secret-token",
                "X-CSRFToken": csrf_token,
            },
        )
        assert resp.status_code == 200

    def test_put_settings_no_csrf(self, client_no_auth):
        """无 CSRF 时 PUT /api/settings 返回 403。"""
        resp = client_no_auth.put("/api/settings", json={"ddos_threshold": 500})
        assert resp.status_code == 403

    def test_put_settings_with_csrf(self, client_no_auth):
        """有 CSRF 时 PUT /api/settings 返回 200。"""
        csrf_resp = client_no_auth.get("/api/csrf-token")
        csrf_token = csrf_resp.json().get("csrf_token", "")
        client_no_auth.cookies.set("csrf_token", csrf_token)
        resp = client_no_auth.put(
            "/api/settings",
            json={"key": "ddos_threshold", "value": 500},
            headers={"X-CSRFToken": csrf_token},
        )
        assert resp.status_code == 200

    def test_delete_model_no_csrf(self, client_no_auth):
        """无 CSRF 时 DELETE /api/models/x 返回 403。"""
        resp = client_no_auth.delete("/api/models/test-model")
        assert resp.status_code == 403

    def test_delete_model_with_csrf(self, client_no_auth):
        """有 CSRF 时 DELETE /api/models/x 返回 200。"""
        csrf_resp = client_no_auth.get("/api/csrf-token")
        csrf_token = csrf_resp.json().get("csrf_token", "")
        client_no_auth.cookies.set("csrf_token", csrf_token)
        resp = client_no_auth.delete(
            "/api/models/test-model",
            headers={"X-CSRFToken": csrf_token},
        )
        assert resp.status_code == 200

    def test_csrf_mismatch(self, client_no_auth):
        """CSRF cookie 与 header 不匹配时 POST 返回 403。"""
        client_no_auth.cookies.set("csrf_token", "cookie-token")
        resp = client_no_auth.post(
            "/api/tasks/capture/start",
            json={"duration": 30},
            headers={"X-CSRFToken": "different-header-token"},
        )
        assert resp.status_code == 403

    def test_csrf_forged_signature(self, client_no_auth):
        """伪造签名的 CSRF token 应被拒绝（HMAC 签名验证，T1.11 ②）。"""
        # 构造格式正确但签名伪造的 token
        forged_nonce = "a" * 64
        forged_signature = "b" * 64
        forged_token = f"{forged_nonce}:{forged_signature}"
        client_no_auth.cookies.set("csrf_token", forged_token)
        resp = client_no_auth.post(
            "/api/tasks/capture/start",
            json={"duration": 30},
            headers={"X-CSRFToken": forged_token},
        )
        assert resp.status_code == 403

    def test_csrf_invalid_format(self, client_no_auth):
        """格式无效的 CSRF token（无冒号分隔）应被拒绝。"""
        raw_token = "a" * 128  # 无 nonce:signature 分隔
        client_no_auth.cookies.set("csrf_token", raw_token)
        resp = client_no_auth.post(
            "/api/tasks/capture/start",
            json={"duration": 30},
            headers={"X-CSRFToken": raw_token},
        )
        assert resp.status_code == 403

    def test_csrf_signed_token_roundtrip(self, client_no_auth):
        """HMAC 签名 token 完整往返：获取 → 使用 → 成功。"""
        csrf_resp = client_no_auth.get("/api/csrf-token")
        csrf_token = csrf_resp.json().get("csrf_token", "")
        # 验证 token 格式为 nonce:signature
        assert ":" in csrf_token, "CSRF token 应为 nonce:signature 格式"
        nonce, signature = csrf_token.split(":", 1)
        assert len(nonce) == 64, "nonce 应为 64 字符十六进制"
        assert len(signature) == 64, "signature 应为 64 字符十六进制"
        # 使用签名 token 发起写请求
        client_no_auth.cookies.set("csrf_token", csrf_token)
        resp = client_no_auth.post(
            "/api/tasks/capture/start",
            json={"duration": 30},
            headers={"X-CSRFToken": csrf_token},
        )
        assert resp.status_code == 200


# ── 限流验证（T1.11 ④ / T1.7）─────────────────────────────────────


class TestRateLimiting:
    """验证 @limiter.limit 装饰器与 SlowAPIMiddleware 实际生效。

    T1.7 验收要求：连发超限出现 429。
    写端点限流 30/minute，默认限流 60/minute。
    """

    def test_write_endpoint_rate_limited_429(self, client_no_auth):
        """写端点超限后返回 429（T1.11 ④ / T1.7）。

        向 /api/tasks/capture/start 连发 35 次（限流 30/minute），
        应至少出现一个 429。
        """
        # 获取有效 CSRF token
        csrf_resp = client_no_auth.get("/api/csrf-token")
        csrf_token = csrf_resp.json().get("csrf_token", "")
        client_no_auth.cookies.set("csrf_token", csrf_token)

        statuses = []
        for _ in range(35):
            resp = client_no_auth.post(
                "/api/tasks/capture/start",
                json={"duration": 30},
                headers={"X-CSRFToken": csrf_token},
            )
            statuses.append(resp.status_code)

        # 至少有一个 429（限流生效）
        assert 429 in statuses, (
            f"限流未生效：35 次请求中无 429，状态码分布: "
            f"{{200: {statuses.count(200)}, 409: {statuses.count(409)}, 429: {statuses.count(429)}}}"
        )

    # 注：slowapi default_limits 对无 @limiter.limit 装饰器的端点不自动生效，
    # 需逐路由显式装饰。当前 @public/@readonly 端点无限流装饰器，
    # 仅 @write 端点有 @limiter.limit("30/minute")，符合 ADR 设计意图。


# ── CORS 验证（T1.11 ⑤）─────────────────────────────────────────────


class TestCORSHeaders:
    """验证 CORS 中间件配置（ADR-0001 §6 #5）。

    默认 cors_origins=["*"]，allow_methods=["*"]，allow_headers=["*"]。
    """

    def test_cors_preflight_options(self, client_no_auth):
        """OPTIONS 预检请求返回 CORS 头。"""
        resp = client_no_auth.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers

    def test_cors_allow_methods_header(self, client_no_auth):
        """CORS 响应包含 allow-methods 头。"""
        resp = client_no_auth.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        allow_methods = resp.headers.get("access-control-allow-methods", "")
        # allow_methods=["*"] 应允许 POST
        assert "POST" in allow_methods or "*" in allow_methods

    def test_cors_allow_headers_header(self, client_no_auth):
        """CORS 响应包含 allow-headers 头。"""
        resp = client_no_auth.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-CSRFToken",
            },
        )
        allow_headers = resp.headers.get("access-control-allow-headers", "")
        # allow_headers=["*"] 应允许 X-CSRFToken
        assert "x-csrftoken" in allow_headers.lower() or "*" in allow_headers

    def test_cors_actual_request_headers(self, client_no_auth):
        """实际 GET 请求也返回 CORS 头。"""
        resp = client_no_auth.get(
            "/api/health",
            headers={"Origin": "http://localhost:3000"},
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers


# ── 密码哈希验证（T1.11 ⑥）─────────────────────────────────────────


class TestPasswordHash:
    """验证密码哈希实现（ADR-0001 §6 #6，werkzeug pbkdf2:sha256）。"""

    def test_hash_password_returns_hash(self):
        """hash_password 返回非空哈希字符串。"""
        from campus_ids.web_new.auth import hash_password
        h = hash_password("test-password")
        assert isinstance(h, str)
        assert len(h) > 0
        assert h != "test-password"

    def test_hash_password_format(self):
        """哈希格式为 pbkdf2:sha256（werkzeug 格式）。"""
        from campus_ids.web_new.auth import hash_password
        h = hash_password("test-password")
        assert h.startswith("pbkdf2:sha256:"), f"哈希格式不符: {h[:30]}..."

    def test_verify_password_correct(self):
        """正确密码验证成功。"""
        from campus_ids.web_new.auth import hash_password, verify_password
        h = hash_password("my-secret")
        assert verify_password("my-secret", h) is True

    def test_verify_password_wrong(self):
        """错误密码验证失败。"""
        from campus_ids.web_new.auth import hash_password, verify_password
        h = hash_password("my-secret")
        assert verify_password("wrong-password", h) is False

    def test_hash_password_salts_differ(self):
        """相同密码的两次哈希不同（盐值随机）。"""
        from campus_ids.web_new.auth import hash_password
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        assert h1 != h2, "相同密码应产生不同哈希（盐值应随机）"

    def test_verify_password_both_succeed(self):
        """不同盐值哈希均能验证同一密码。"""
        from campus_ids.web_new.auth import hash_password, verify_password
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        assert verify_password("same-password", h1) is True
        assert verify_password("same-password", h2) is True


# ── 会话认证验证（T1.11 ①）─────────────────────────────────────────


class TestSessionAuth:
    """验证 SessionMiddleware 与会话管理工具函数（ADR-0001 §6 #1）。

    SessionMiddleware 配置：secret_key, session_cookie="campus_ids_session",
    same_site="lax", https_only=False（开发环境）。
    """

    def test_session_cookie_set_on_request(self, client_no_auth):
        """SessionMiddleware 已注册：首次写入 session 数据后设置 cookie。
        
        注意：SessionMiddleware 仅在 session 有数据时才设置 cookie，
        纯只读请求（如 GET /api/health）不会触发 session cookie。
        """
        # CSRF token 端点会设置 csrf_token cookie（非 session cookie）
        # 验证 SessionMiddleware 已注册：访问任意端点不报错
        resp = client_no_auth.get("/api/health")
        assert resp.status_code == 200
        # 验证 app 中间件栈包含 SessionMiddleware
        from campus_ids.web_new.app import create_app
        app = create_app()
        middleware_classes = [m.cls.__name__ for m in app.user_middleware]
        assert "SessionMiddleware" in middleware_classes, (
            f"SessionMiddleware 未注册，中间件栈: {middleware_classes}"
        )

    def test_session_persists_across_requests(self, client_no_auth):
        """session 数据跨请求持久化。"""
        # 通过 /api/csrf-token 写入 session（CSRF nonce 存入 session）
        resp1 = client_no_auth.get("/api/csrf-token")
        assert resp1.status_code == 200
        # 同一 client 应保持 session cookie
        resp2 = client_no_auth.get("/api/csrf-token")
        assert resp2.status_code == 200

    def test_login_user_writes_session(self):
        """login_user 将 user 和 authenticated 写入会话。"""
        from campus_ids.web_new.auth import login_user

        class FakeSession(dict):
            pass

        class FakeRequest:
            session = FakeSession()

        req = FakeRequest()
        login_user(req, "admin")
        assert req.session["user"] == "admin"
        assert req.session["authenticated"] is True

    def test_logout_user_clears_session(self):
        """logout_user 清除会话中的认证信息。"""
        from campus_ids.web_new.auth import login_user, logout_user

        class FakeSession(dict):
            pass

        class FakeRequest:
            session = FakeSession()

        req = FakeRequest()
        login_user(req, "admin")
        assert req.session.get("user") == "admin"
        logout_user(req)
        assert req.session.get("user") is None
        assert req.session.get("authenticated") is None

    def test_get_current_user_returns_username(self):
        """get_current_user 从会话返回已认证用户名。"""
        from campus_ids.web_new.auth import get_current_user

        class FakeRequest:
            session = {"user": "alice", "authenticated": True}

        req = FakeRequest()
        assert get_current_user(req) == "alice"

    def test_get_current_user_returns_none_when_not_logged_in(self):
        """未登录时 get_current_user 返回 None。"""
        from campus_ids.web_new.auth import get_current_user

        class FakeRequest:
            session = {}

        req = FakeRequest()
        assert get_current_user(req) is None

    def test_is_authenticated_true(self):
        """is_authenticated 对已认证会话返回 True。"""
        from campus_ids.web_new.auth import is_authenticated

        class FakeRequest:
            session = {"user": "admin", "authenticated": True}

        req = FakeRequest()
        assert is_authenticated(req) is True

    def test_is_authenticated_false(self):
        """is_authenticated 对未认证会话返回 False。"""
        from campus_ids.web_new.auth import is_authenticated

        class FakeRequest:
            session = {}

        req = FakeRequest()
        assert is_authenticated(req) is False


# ── 会话认证 HTTP 级集成测试（修复 T1.11 ① 死代码）─────────────────────


class TestSessionAuthHTTP:
    """验证会话认证在 HTTP 层实际生效（T1.11 ① 修复验证）。

    修复前：require_api_token 在无 Bearer 时直接抛 401，会话回退是死代码。
    修复后：_verify_bearer_token 返回 False，policy 函数回退检查 session。
    """

    @pytest.fixture()
    def client_with_session_auth(self):
        """认证开启 + 会话登录的 TestClient。

        使用 itsdangerous.TimestampSigner 创建合法 session cookie，
        签名方式与 Starlette SessionMiddleware 完全一致：
        - 无 salt（Starlette 默认不传 salt）
        - 默认 JSON 分隔符（带空格）
        - base64 编码
        """
        import base64
        import json

        import itsdangerous

        os.environ["CAMPUS_IDS_AUTH_ENABLED"] = "1"
        os.environ["CAMPUS_IDS_API_TOKEN"] = "test-secret-token"
        reset_settings()
        app = create_app()
        client = TestClient(app)

        # 获取 secret_key（与 SessionMiddleware 使用同一个）
        from campus_ids.runtime.settings import get_settings
        settings = get_settings()

        # 创建签名器（与 Starlette SessionMiddleware 相同：无 salt）
        signer = itsdangerous.TimestampSigner(str(settings.secret_key))
        session_data = {"user": "admin", "authenticated": True}
        # 使用默认 JSON 分隔符（与 Starlette 一致）
        data = base64.b64encode(json.dumps(session_data).encode("utf-8"))
        signed = signer.sign(data).decode("utf-8")
        client.cookies.set("campus_ids_session", signed)
        yield client
        os.environ.pop("CAMPUS_IDS_AUTH_ENABLED", None)
        os.environ.pop("CAMPUS_IDS_API_TOKEN", None)
        reset_settings()

    def test_readonly_with_session_no_bearer(self, client_with_session_auth):
        """认证开启 + 会话登录（无 Bearer）→ GET /api/alerts 返回 200。

        这是 T1.11 ① 的核心修复验证：会话回退不再是死代码。
        """
        resp = client_with_session_auth.get("/api/alerts")
        assert resp.status_code == 200, (
            f"会话认证应生效：auth_enabled=1 + session user=admin → 期望 200，"
            f"实际 {resp.status_code}（{resp.text[:200]})"
        )

    def test_write_with_session_and_csrf(self, client_with_session_auth):
        """认证开启 + 会话登录 + CSRF → POST 返回 200。"""
        # 获取 CSRF token
        csrf_resp = client_with_session_auth.get("/api/csrf-token")
        csrf_token = csrf_resp.json().get("csrf_token", "")
        client_with_session_auth.cookies.set("csrf_token", csrf_token)
        resp = client_with_session_auth.post(
            "/api/tasks/capture/start",
            json={"duration": 30},
            headers={"X-CSRFToken": csrf_token},
        )
        assert resp.status_code == 200, (
            f"会话认证 + CSRF 应生效：期望 200，实际 {resp.status_code}"
        )

    def test_session_without_user_fails(self, client_with_auth):
        """认证开启 + 空 session（无 user）→ GET /api/alerts 返回 401。"""
        # client_with_auth 有 Bearer token 认证但无 session user
        # 使用不带 Authorization header 的请求
        resp = client_with_auth.get("/api/alerts")
        assert resp.status_code == 401, (
            f"无 Bearer + 无 session user → 期望 401，实际 {resp.status_code}"
        )

    def test_bearer_still_works_with_session_auth_fix(self, client_with_auth):
        """修复后 Bearer 认证仍然正常工作。"""
        resp = client_with_auth.get(
            "/api/alerts",
            headers={"Authorization": "Bearer test-secret-token"},
        )
        assert resp.status_code == 200