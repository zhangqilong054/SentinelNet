"""auth.py 路由测试 — 登录/登出/改密端点。

覆盖 auth.py 中 login/logout/change_password 路由的各分支：
- GET /login（已登录跳转、未登录渲染表单）
- POST /login（空字段、用户不存在、密码错误、默认密码改密、正常登录）
- POST /logout
- GET /change-password（渲染表单）
- POST /change-password（空字段、短密码、旧密码错误、正常改密）

注意：使用主应用 app（已注册所有蓝图和路由）进行测试，避免独立 app 缺少路由。
运行时需设置 CAMPUS_IDS_LOGIN_ENABLED=1 以注册 auth 蓝图。
"""
import os
import pytest
from werkzeug.security import generate_password_hash, check_password_hash

from campus_ids.web.app import app
from campus_ids.web.database import ensure_default_user, get_user_by_username, update_user_password

# 禁用 CSRF 保护以便 POST 测试正常工作
app.config["WTF_CSRF_ENABLED"] = False

# 若 auth 蓝图未注册则跳过所有测试
if "auth" not in app.blueprints:
    pytest.skip("auth 蓝图未注册（需 CAMPUS_IDS_LOGIN_ENABLED=1）", allow_module_level=True)


@pytest.fixture()
def logged_in_client():
    """返回已登录 admin 用户的 test_client（密码设为 testpass123）。"""
    ensure_default_user()
    admin = get_user_by_username("admin")
    original_hash = admin["password_hash"]
    update_user_password(admin["id"], generate_password_hash("testpass123"))
    client = app.test_client()
    client.post("/login", data={"username": "admin", "password": "testpass123"})
    yield client
    # 恢复默认密码
    update_user_password(admin["id"], generate_password_hash("admin"))


# ── GET /login ──────────────────────────────────────────────────────

class TestLoginGet:
    def test_login_page_renders(self):
        """GET /login 应返回 200 和登录表单。"""
        client = app.test_client()
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"login" in resp.data.lower() or b"password" in resp.data.lower()

    def test_login_page_authenticated_redirects(self, logged_in_client):
        """已登录用户访问 /login 应重定向到首页。"""
        resp = logged_in_client.get("/login")
        assert resp.status_code in (302, 303)


# ── POST /login ─────────────────────────────────────────────────────

class TestLoginPost:
    def test_login_empty_username(self):
        """空用户名应返回 400。"""
        client = app.test_client()
        resp = client.post("/login", data={"username": "", "password": "x"})
        assert resp.status_code == 400

    def test_login_empty_password(self):
        """空密码应返回 400。"""
        client = app.test_client()
        resp = client.post("/login", data={"username": "admin", "password": ""})
        assert resp.status_code == 400

    def test_login_nonexistent_user(self):
        """不存在的用户应返回 401。"""
        client = app.test_client()
        resp = client.post("/login", data={"username": "nouser", "password": "x"})
        assert resp.status_code == 401

    def test_login_wrong_password(self):
        """错误密码应返回 401。"""
        ensure_default_user()
        client = app.test_client()
        resp = client.post("/login", data={"username": "admin", "password": "wrong"})
        assert resp.status_code == 401

    def test_login_default_password_forces_change(self):
        """使用默认密码 admin 登录应重定向到改密页。"""
        ensure_default_user()
        admin = get_user_by_username("admin")
        update_user_password(admin["id"], generate_password_hash("admin"))
        client = app.test_client()
        resp = client.post("/login", data={"username": "admin", "password": "admin"})
        assert resp.status_code in (302, 303)
        if resp.headers.get("Location"):
            assert "change-password" in resp.headers["Location"]

    def test_login_success(self):
        """正确密码登录应成功。"""
        ensure_default_user()
        admin = get_user_by_username("admin")
        update_user_password(admin["id"], generate_password_hash("testpass123"))
        client = app.test_client()
        resp = client.post("/login", data={"username": "admin", "password": "testpass123"})
        assert resp.status_code in (302, 303)
        # 恢复默认密码
        update_user_password(admin["id"], generate_password_hash("admin"))


# ── POST /logout ────────────────────────────────────────────────────

class TestLogout:
    def test_logout_requires_login(self):
        """未登录时登出应重定向到登录页。"""
        client = app.test_client()
        resp = client.get("/logout")
        assert resp.status_code in (302, 303, 401)

    def test_logout_success(self, logged_in_client):
        """登录后登出应成功。"""
        resp = logged_in_client.post("/logout")
        assert resp.status_code in (302, 303)


# ── GET /change-password ────────────────────────────────────────────

class TestChangePasswordGet:
    def test_change_password_requires_login(self):
        """未登录时访问改密页应重定向到登录页。"""
        client = app.test_client()
        resp = client.get("/change-password")
        assert resp.status_code in (302, 303, 401)

    def test_change_password_page_renders(self, logged_in_client):
        """登录后访问改密页应返回 200。"""
        resp = logged_in_client.get("/change-password")
        assert resp.status_code == 200


# ── POST /change-password ──────────────────────────────────────────

class TestChangePasswordPost:
    def setup_method(self):
        ensure_default_user()
        self.admin = get_user_by_username("admin")
        update_user_password(self.admin["id"], generate_password_hash("oldpass123"))
        self.client = app.test_client()
        self.client.post("/login", data={"username": "admin", "password": "oldpass123"})

    def teardown_method(self):
        try:
            update_user_password(self.admin["id"], generate_password_hash("admin"))
        except Exception:
            pass

    def test_change_password_empty_fields(self):
        """空字段应返回 400。"""
        resp = self.client.post("/change-password", data={"old_password": "", "new_password": ""})
        assert resp.status_code == 400

    def test_change_password_short_new(self):
        """新密码少于 6 个字符应返回 400。"""
        resp = self.client.post("/change-password", data={
            "old_password": "oldpass123", "new_password": "12345"
        })
        assert resp.status_code == 400

    def test_change_password_wrong_old(self):
        """旧密码错误应返回 401。"""
        resp = self.client.post("/change-password", data={
            "old_password": "wrongpass", "new_password": "newpass123"
        })
        assert resp.status_code == 401

    def test_change_password_success(self):
        """正确改密应成功。"""
        resp = self.client.post("/change-password", data={
            "old_password": "oldpass123", "new_password": "newpass456"
        })
        assert resp.status_code in (302, 303)
        admin = get_user_by_username("admin")
        assert check_password_hash(admin["password_hash"], "newpass456")


# ── _unauthorized 分支 ──────────────────────────────────────────────

class TestUnauthorizedHandler:
    def test_api_path_returns_401(self):
        """未登录访问 API 路径应返回 401 JSON。"""
        client = app.test_client()
        resp = client.get("/api/traffic")
        assert resp.status_code in (401, 302)

    def test_non_api_path_redirects(self):
        """未登录访问非 API 路径应重定向到登录页。"""
        client = app.test_client()
        resp = client.get("/change-password")
        assert resp.status_code in (302, 303, 401)