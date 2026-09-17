"""T2.17 页面路由集成测试。

新应用此前 **完全不提供界面**：`GET /`、`/login`、`/logout`、`/change-password`
全部 404。本文件断言的是**对外可观测行为**（状态码 + 跳转目标 + 会话后的可访问性 +
改密后旧密码失效），而不是"函数被调用了"。

⚠️ 本项目的教训：`_render()` 每次 GET 都会**换发** CSRF cookie，
因此每个 POST 前必须**重新**从它要提交的那个页面取 token，
不能用之前抓到的 token（会因 cookie 已被覆盖而 403/400）。
"""
from __future__ import annotations

import re
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from campus_ids.runtime.settings import reset_settings
from campus_ids.web_new.app import create_app

CSRF_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_env():
    """每个测试前后清理认证相关环境变量与限流计数。"""
    import os

    from campus_ids.web_new.security import limiter

    def _clear():
        os.environ.pop("CAMPUS_IDS_API_TOKEN", None)
        os.environ.pop("CAMPUS_IDS_AUTH_ENABLED", None)
        reset_settings()
        limiter.reset()

    _clear()
    yield
    _clear()


@pytest.fixture()
def client_no_auth():
    """认证关闭的客户端（`api_token` 为空 → 页面直接放行）。"""
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def client_auth(monkeypatch):
    """认证开启的客户端（`admin` 的初始密码 = `CAMPUS_IDS_API_TOKEN` 的值）。"""
    token = "s3cret-bootstrap-token"
    monkeypatch.setenv("CAMPUS_IDS_API_TOKEN", token)
    reset_settings()
    with TestClient(create_app()) as c:
        c.bootstrap_token = token  # type: ignore[attr-defined]
        yield c


def _csrf_from(response) -> str:
    """从页面 HTML 里取出 CSRF token（与响应同时下发的 cookie 配对）。"""
    match = CSRF_RE.search(response.text)
    assert match, "页面未渲染 csrf_token 隐藏域"
    return match.group(1)


# ── 认证关闭时的页面可用性 ────────────────────────────────────────


class TestPagesWithoutAuth:
    def test_index_served_without_login(self, client_no_auth):
        resp = client_no_auth.get("/")
        assert resp.status_code == 200
        assert "SentinelNet" in resp.text
        assert "任务编排" in resp.text
        assert 'name="csrf-token"' in resp.text, "缺少 CSRF meta，前端无法发起写请求"

    def test_login_page_renders_form(self, client_no_auth):
        resp = client_no_auth.get("/login")
        assert resp.status_code == 200
        assert _csrf_from(resp)
        assert 'action="/login"' in resp.text

    def test_change_password_page_reachable(self, client_no_auth):
        resp = client_no_auth.get("/change-password")
        assert resp.status_code == 200
        assert "修改密码" in resp.text

    def test_static_asset_served(self, client_no_auth):
        resp = client_no_auth.get("/static/css/app.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]

    def test_static_js_served(self, client_no_auth):
        resp = client_no_auth.get("/static/js/app.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]

    def test_auth_pages_are_not_api_routes(self, client_no_auth):
        """页面路由不得污染 OpenAPI 规格（阶段 3 的 TS 类型生成依赖它）。"""
        paths = client_no_auth.get("/openapi.json").json()["paths"]
        for page in ("/", "/login", "/logout", "/change-password"):
            assert page not in paths, f"页面路由 {page} 不应出现在 API 规格里"


# ── 登出方法收窄（有意的破坏性变更）────────────────────────────────


class TestLogoutMethod:
    def test_get_logout_rejected(self, client_no_auth):
        """`GET /logout` 必须 405 —— 否则可被跨站强制登出。"""
        resp = client_no_auth.get("/logout")
        assert resp.status_code == 405
        assert "POST" in resp.text

    def test_post_logout_without_csrf_rejected(self, client_no_auth):
        resp = client_no_auth.post("/logout", data={"csrf_token": ""})
        assert resp.status_code == 400


# ── 认证开启时的完整登录 / 登出流程 ───────────────────────────────


class TestLoginFlow:
    def test_index_redirects_to_login(self, client_auth):
        resp = client_auth.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login?next=/"

    def test_change_password_redirects_to_login(self, client_auth):
        resp = client_auth.get("/change-password", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login?next=/change-password"

    def test_login_without_csrf_shows_error(self, client_auth):
        client_auth.get("/login")  # 先拿到 cookie
        resp = client_auth.post(
            "/login",
            data={"username": "admin", "password": client_auth.bootstrap_token,
                  "csrf_token": "", "next": "/"},
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert "表单已过期" in resp.text

    def test_login_with_wrong_password_rejected(self, client_auth):
        token = _csrf_from(client_auth.get("/login"))
        resp = client_auth.post(
            "/login",
            data={"username": "admin", "password": "wrong", "csrf_token": token, "next": "/"},
            follow_redirects=False,
        )
        assert resp.status_code == 401
        assert "用户名或密码错误" in resp.text

    def test_login_unknown_user_rejected(self, client_auth):
        token = _csrf_from(client_auth.get("/login"))
        resp = client_auth.post(
            "/login",
            data={"username": "nobody", "password": "whatever", "csrf_token": token, "next": "/"},
            follow_redirects=False,
        )
        assert resp.status_code == 401

    def test_login_then_index_accessible(self, client_auth):
        token = _csrf_from(client_auth.get("/login"))
        resp = client_auth.post(
            "/login",
            data={"username": "admin", "password": client_auth.bootstrap_token,
                  "csrf_token": token, "next": "/"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

        index = client_auth.get("/")
        assert index.status_code == 200
        assert "<code>admin</code>" in index.text, "首页未体现已登录用户"

    def test_logout_clears_session(self, client_auth):
        token = _csrf_from(client_auth.get("/login"))
        client_auth.post(
            "/login",
            data={"username": "admin", "password": client_auth.bootstrap_token,
                  "csrf_token": token, "next": "/"},
            follow_redirects=False,
        )
        assert client_auth.get("/", follow_redirects=False).status_code == 200

        token = _csrf_from(client_auth.get("/"))
        out = client_auth.post("/logout", data={"csrf_token": token}, follow_redirects=False)
        assert out.status_code == 302
        assert out.headers["location"] == "/login"

        after = client_auth.get("/", follow_redirects=False)
        assert after.status_code == 302, "登出后首页仍可访问，会话未清除"

    def test_login_already_authenticated_goes_home(self, client_auth):
        token = _csrf_from(client_auth.get("/login"))
        client_auth.post(
            "/login",
            data={"username": "admin", "password": client_auth.bootstrap_token,
                  "csrf_token": token, "next": "/"},
            follow_redirects=False,
        )
        resp = client_auth.get("/login", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

    def test_open_redirect_blocked(self, client_auth):
        """`next` 只接受站内相对路径。"""
        token = _csrf_from(client_auth.get("/login"))
        resp = client_auth.post(
            "/login",
            data={"username": "admin", "password": client_auth.bootstrap_token,
                  "csrf_token": token, "next": "//evil.example.com/steal"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"


# ── 改密流程（端到端：改完必须用新密码才能登录）────────────────────


class TestChangePasswordFlow:
    def _login(self, client, password):
        token = _csrf_from(client.get("/login"))
        return client.post(
            "/login",
            data={"username": "admin", "password": password,
                  "csrf_token": token, "next": "/"},
            follow_redirects=False,
        )

    def test_change_password_then_old_password_fails(self, client_auth):
        old = client_auth.bootstrap_token
        new = "brand-new-password"

        assert self._login(client_auth, old).status_code == 302

        token = _csrf_from(client_auth.get("/change-password"))
        changed = client_auth.post(
            "/change-password",
            data={"old_password": old, "new_password": new,
                  "confirm_password": new, "csrf_token": token},
            follow_redirects=False,
        )
        assert changed.status_code == 302
        # Location 头会把中文 message 做百分号编码，比对前先解码
        assert unquote(changed.headers["location"]) == "/?message=密码修改成功"

        token = _csrf_from(client_auth.get("/login"))
        client_auth.post("/logout", data={"csrf_token": token}, follow_redirects=False)

        assert self._login(client_auth, old).status_code == 401, "旧密码修改后仍可登录"
        assert self._login(client_auth, new).status_code == 302, "新密码无法登录"

    def test_wrong_old_password_rejected(self, client_auth):
        assert self._login(client_auth, client_auth.bootstrap_token).status_code == 302
        token = _csrf_from(client_auth.get("/change-password"))
        resp = client_auth.post(
            "/change-password",
            data={"old_password": "not-my-password", "new_password": "abcdef",
                  "confirm_password": "abcdef", "csrf_token": token},
            follow_redirects=False,
        )
        assert resp.status_code == 401
        assert "旧密码不正确" in resp.text

    def test_mismatched_confirmation_rejected(self, client_auth):
        assert self._login(client_auth, client_auth.bootstrap_token).status_code == 302
        token = _csrf_from(client_auth.get("/change-password"))
        resp = client_auth.post(
            "/change-password",
            data={"old_password": client_auth.bootstrap_token, "new_password": "abcdef",
                  "confirm_password": "abcdeg", "csrf_token": token},
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert "不一致" in resp.text

    def test_short_password_rejected(self, client_auth):
        assert self._login(client_auth, client_auth.bootstrap_token).status_code == 302
        token = _csrf_from(client_auth.get("/change-password"))
        resp = client_auth.post(
            "/change-password",
            data={"old_password": client_auth.bootstrap_token, "new_password": "12345",
                  "confirm_password": "12345", "csrf_token": token},
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert "至少 6 个字符" in resp.text

    def test_change_password_requires_csrf(self, client_auth):
        assert self._login(client_auth, client_auth.bootstrap_token).status_code == 302
        client_auth.get("/change-password")
        resp = client_auth.post(
            "/change-password",
            data={"old_password": client_auth.bootstrap_token,
                  "new_password": "abcdef", "confirm_password": "abcdef",
                  "csrf_token": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 400
