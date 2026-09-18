# -*- coding: utf-8 -*-
"""`web_new/api/auth_routes.py` 行为测试（R5 覆盖率补齐）。

改动前覆盖率 **47%**。注意 `tests/test_web_auth_routes.py` 测的是**旧 Flask 版**
（旧 Flask 版 `campus_ids.web.app`），对新栈的 `/api/login`、`/api/logout`、`/api/change-password`
一行都没覆盖 —— 覆盖率数字低正是因为"名字像、其实无关"。

## 本文件重点验证的三件事

1. **凭据不得泄露**：响应体里不能出现 `password_hash`；"用户不存在"与"密码错误"
   必须返回**字面相同**的错误，否则攻击者可枚举用户名。
2. **空哈希必须是 401 而不是 500**：`app.py` 在 `CAMPUS_IDS_API_TOKEN` 未配置时把
   `password_hash` 写成空串，而 werkzeug 对此会抛 `ValueError`。这是一条真实踩过的
   缺陷路径（见 `web_new/auth.py::verify_password` 的 docstring）。
3. **会话真的能授权写操作**：开启认证后，未登录的写请求 401；登录后同一请求 200。
   只断言"login 返回 200"是不够的 —— 会话没写进去也能返回 200。
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from werkzeug.security import generate_password_hash

from campus_ids.runtime.db import get_connection, init_db, users
from campus_ids.runtime.repositories import UserRepository
from campus_ids.runtime.settings import reset_settings
from campus_ids.web_new.app import create_app
from campus_ids.web_new.auth import hash_password, verify_password

TOKEN = "tok-3f9a2b7c"
ADMIN = "admin"
GOOD_PASSWORD = TOKEN  # app.py 用 api_token 作为首次启动的 admin 密码


# ── fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_env():
    """隔离环境 + 重置限流器（login 10/minute、change-password 5/minute）。

    限流器是**模块级单例**，跨测试累积计数，不重置会让后续测试随机 429。
    """

    def _clear() -> None:
        for key in ("CAMPUS_IDS_API_TOKEN", "CAMPUS_IDS_AUTH_ENABLED"):
            os.environ.pop(key, None)
        reset_settings()
        from campus_ids.web_new.security import limiter

        limiter.reset()

    _clear()
    yield
    _clear()


def _open_client() -> TestClient:
    """认证未启用的客户端（api_token 为空）。"""
    os.environ["CAMPUS_IDS_AUTH_ENABLED"] = "0"
    reset_settings()
    return TestClient(create_app())


def _secure_client() -> TestClient:
    """认证启用的客户端 —— admin 的 bootstrap 密码 == TOKEN。"""
    os.environ["CAMPUS_IDS_API_TOKEN"] = TOKEN
    os.environ["CAMPUS_IDS_AUTH_ENABLED"] = "1"
    reset_settings()
    return TestClient(create_app())


@pytest.fixture
def client():
    with _open_client() as c:
        yield c


@pytest.fixture
def secure_client():
    with _secure_client() as c:
        yield c


def _csrf(client: TestClient) -> dict[str, str]:
    """取 CSRF token 并同时写入 cookie 与请求头（双提交）。"""
    token = client.get("/api/csrf-token").json()["csrf_token"]
    client.cookies.set("csrf_token", token)
    return {"X-CSRFToken": token}


def _login(client: TestClient, username: str = ADMIN, password: str = GOOD_PASSWORD):
    return client.post("/api/login", json={"username": username, "password": password})


def _seed_user(username: str, password_hash: str) -> None:
    init_db()
    with get_connection() as conn:
        if UserRepository.get_by_username(conn, username) is None:
            UserRepository.create(conn, username=username, password_hash=password_hash)


def _stored_hash(username: str) -> str:
    with get_connection() as conn:
        row = UserRepository.get_by_username(conn, username)
    assert row is not None, f"用户 {username} 不存在"
    return row.password_hash


# ══ POST /api/login ═══════════════════════════════════════════════


class TestLogin:
    def test_success_returns_username_and_message(self, secure_client):
        resp = _login(secure_client)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "success"
        assert body["message"] == "登录成功"
        assert body["username"] == ADMIN

    def test_response_never_leaks_password_hash(self, secure_client):
        """响应体（含错误响应）绝不得包含 password_hash 字段。"""
        ok = _login(secure_client).text
        bad = _login(secure_client, password="wrong").text
        for payload in (ok, bad):
            assert "password" not in payload.lower() or "password_hash" not in payload
        assert "pbkdf2" not in ok and "pbkdf2" not in bad

    def test_wrong_password_is_401(self, secure_client):
        resp = _login(secure_client, password="not-the-token")
        assert resp.status_code == 401, resp.text
        assert resp.json()["error"] == "UNAUTHORIZED"

    def test_unknown_user_and_wrong_password_are_indistinguishable(self, secure_client):
        """防用户名枚举 —— 两种失败必须返回**同一个**错误文案与状态码。"""
        unknown = _login(secure_client, username="ghost", password="x")
        wrong = _login(secure_client, username=ADMIN, password="x")

        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["detail"] == wrong.json()["detail"]

    def test_empty_hash_user_is_401_not_500(self, client):
        """`CAMPUS_IDS_API_TOKEN` 未配置时 admin 的哈希是空串 ——

        werkzeug 对空串会抛 `ValueError`，若 `verify_password` 不兜底则登录端点 500。
        认证失败应当是 401，500 会把"没设密码"暴露成服务故障。
        """
        assert _stored_hash(ADMIN) == "", "前提：未配置 token 时 bootstrap 哈希为空"

        resp = _login(client, password="anything")

        assert resp.status_code == 401, f"空哈希应 401，实际 {resp.status_code}: {resp.text}"

    def test_existing_werkzeug_hash_is_still_accepted(self, secure_client):
        """兼容既有库 —— 用 werkzeug **默认** method 生成的哈希（非 pbkdf2:sha256）也要能登录。

        这是"不迁移现有用户密码"这一设计决策的可执行证据。
        """
        _seed_user("legacy", generate_password_hash("legacy-pass"))

        assert _login(secure_client, "legacy", "legacy-pass").status_code == 200
        assert _login(secure_client, "legacy", "bad").status_code == 401

    @pytest.mark.parametrize("payload", [
        {"username": "", "password": "x"},
        {"username": "admin", "password": ""},
        {},
    ])
    def test_missing_fields_are_rejected_by_schema(self, secure_client, payload):
        assert _login_payload(secure_client, payload).status_code == 422

    def test_overlong_password_is_rejected(self, secure_client):
        resp = _login_payload(secure_client, {"username": "admin", "password": "x" * 200})
        assert resp.status_code == 422

    def test_get_is_not_allowed(self, secure_client):
        """凭据必须走 POST body，不得出现在 URL 里（会进日志/Referer）。"""
        assert secure_client.get("/api/login").status_code == 405

    def test_login_is_rate_limited(self, secure_client):
        """暴力破解防护：10/minute 之外的第 11 次必须 429。"""
        for _ in range(10):
            _login(secure_client, password="wrong")

        resp = _login(secure_client, password="wrong")

        assert resp.status_code == 429, f"限流未生效，实际 {resp.status_code}"


def _login_payload(client: TestClient, payload: dict):
    return client.post("/api/login", json=payload)


# ══ POST /api/logout ══════════════════════════════════════════════


class TestLogout:
    def test_logout_without_login_is_idempotent(self, secure_client):
        """未登录时登出不得报错（Public 端点）。"""
        resp = secure_client.post("/api/logout")
        assert resp.status_code == 200, resp.text
        assert resp.json()["message"] == "已登出"

    def test_logout_revokes_session_access(self, secure_client):
        """登录 → 写操作 200；登出 → 同一写操作 401。

        这是本文件最关键的一条：它证明**会话真的参与鉴权**，而不是登录端点
        单方面返回 200 而会话从未写入。
        """
        csrf = _csrf(secure_client)
        assert _login(secure_client).status_code == 200

        before = secure_client.post("/api/admin/cleanup", json={"days": 7}, headers=csrf)
        assert before.status_code == 200, f"登录后写操作应通过，实际 {before.status_code}"

        assert secure_client.post("/api/logout").status_code == 200

        after = secure_client.post("/api/admin/cleanup", json={"days": 7}, headers=csrf)
        assert after.status_code == 401, f"登出后应 401，实际 {after.status_code}"


# ══ 会话鉴权（write/readonly 策略）════════════════════════════════


class TestSessionAuthorization:
    def test_write_without_login_is_401(self, secure_client):
        """认证启用 + 无会话 → 401（不是 403；CSRF 通过后是认证层拒绝）。"""
        csrf = _csrf(secure_client)
        resp = secure_client.post("/api/admin/cleanup", json={"days": 7}, headers=csrf)
        assert resp.status_code == 401, f"未登录写操作应 401，实际 {resp.status_code}"

    def test_readonly_without_login_is_401(self, secure_client):
        resp = secure_client.get("/api/models")
        assert resp.status_code == 401, f"未登录读操作应 401，实际 {resp.status_code}"

    def test_login_grants_readonly_access(self, secure_client):
        assert _login(secure_client).status_code == 200
        assert secure_client.get("/api/models").status_code == 200

    def test_write_still_requires_csrf_when_logged_in(self, secure_client):
        """会话认证**不能**替代 CSRF —— 双提交 cookie 与认证模式无关。

        否则被登录用户的浏览器会被跨站请求静默触发写操作。
        """
        assert _login(secure_client).status_code == 200
        resp = secure_client.post("/api/admin/cleanup", json={"days": 7})
        assert resp.status_code == 403, f"缺 CSRF 应 403，实际 {resp.status_code}"

    def test_public_endpoints_need_no_login(self, secure_client):
        assert secure_client.get("/api/csrf-token").status_code == 200
        assert secure_client.get("/api/health").status_code == 200

    def test_auth_disabled_lets_write_through_without_session(self, client):
        """认证未启用（api_token 为空）时，写操作只需 CSRF。"""
        csrf = _csrf(client)
        resp = client.post("/api/admin/cleanup", json={"days": 7}, headers=csrf)
        assert resp.status_code == 200, resp.text


# ══ POST /api/change-password ═════════════════════════════════════


class TestChangePassword:
    def test_requires_login(self, secure_client):
        csrf = _csrf(secure_client)
        resp = secure_client.post(
            "/api/change-password",
            json={"old_password": GOOD_PASSWORD, "new_password": "newpass123"},
            headers=csrf,
        )
        assert resp.status_code == 401, f"未登录应 401，实际 {resp.status_code}"

    def test_requires_csrf(self, secure_client):
        _login(secure_client)
        resp = secure_client.post(
            "/api/change-password",
            json={"old_password": GOOD_PASSWORD, "new_password": "newpass123"},
        )
        assert resp.status_code == 403, f"缺 CSRF 应 403，实际 {resp.status_code}"

    def test_wrong_old_password_is_rejected(self, secure_client):
        csrf = _csrf(secure_client)
        _login(secure_client)

        resp = secure_client.post(
            "/api/change-password",
            json={"old_password": "not-the-token", "new_password": "newpass123"},
            headers=csrf,
        )

        assert resp.status_code == 401, resp.text
        assert resp.json()["detail"] == "旧密码错误"

    def test_wrong_old_password_does_not_change_stored_hash(self, secure_client):
        """验证失败时**不得**已写入新密码（先写后校验是典型漏洞）。"""
        before = _stored_hash(ADMIN)
        csrf = _csrf(secure_client)
        _login(secure_client)

        secure_client.post(
            "/api/change-password",
            json={"old_password": "wrong", "new_password": "newpass123"},
            headers=csrf,
        )

        assert _stored_hash(ADMIN) == before

    def test_success_changes_hash_and_login_password(self, secure_client):
        """端到端：改密 → 旧密码失效 → 新密码可登录。"""
        old_hash = _stored_hash(ADMIN)
        csrf = _csrf(secure_client)
        assert _login(secure_client).status_code == 200

        resp = secure_client.post(
            "/api/change-password",
            json={"old_password": GOOD_PASSWORD, "new_password": "brand-new-pass"},
            headers=csrf,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["message"] == "密码修改成功"

        new_hash = _stored_hash(ADMIN)
        assert new_hash != old_hash, "哈希未落库"
        assert verify_password("brand-new-pass", new_hash) is True
        assert verify_password(GOOD_PASSWORD, new_hash) is False

        # 会话仍有效（改密不需重新登录）
        assert secure_client.get("/api/models").status_code == 200
        assert secure_client.post("/api/logout").status_code == 200

        assert _login(secure_client, password=GOOD_PASSWORD).status_code == 401
        assert _login(secure_client, password="brand-new-pass").status_code == 200

    def test_missing_password_record_returns_404(self, secure_client):
        """会话里有用户名但库里没有该用户 → 404（不是 500，也不是静默成功）。"""
        _seed_user("ghost", hash_password("ghost-pass"))
        csrf = _csrf(secure_client)
        assert _login(secure_client, "ghost", "ghost-pass").status_code == 200

        with get_connection() as conn:
            conn.execute(users.delete().where(users.c.username == "ghost"))
            conn.commit()

        resp = secure_client.post(
            "/api/change-password",
            json={"old_password": "ghost-pass", "new_password": "whatever123"},
            headers=csrf,
        )

        assert resp.status_code == 404, f"用户已不存在应 404，实际 {resp.status_code}: {resp.text}"
        assert resp.json()["error"] == "NOT_FOUND"

    @pytest.mark.parametrize("payload", [
        {"old_password": "", "new_password": "newpass123"},     # 旧密码不能为空
        {"old_password": "x", "new_password": "short"},         # 新密码 < 6
        {"old_password": "x"},                                  # 缺新密码
    ])
    def test_schema_rejects_invalid_payload(self, secure_client, payload):
        csrf = _csrf(secure_client)
        _login(secure_client)
        resp = secure_client.post("/api/change-password", json=payload, headers=csrf)
        assert resp.status_code == 422, f"{payload} 应被 schema 拒绝，实际 {resp.status_code}"

    def test_get_is_not_allowed(self, secure_client):
        assert secure_client.get("/api/change-password").status_code == 405

    def test_not_rate_limited_before_limit(self, secure_client):
        """5/minute 之内不得误伤（阈值写小了会把正常用户锁在外面）。"""
        csrf = _csrf(secure_client)
        _login(secure_client)

        for _ in range(4):
            resp = secure_client.post(
                "/api/change-password",
                json={"old_password": "wrong", "new_password": "newpass123"},
                headers=csrf,
            )
            assert resp.status_code == 401, f"第 {_} 次被限流误伤: {resp.status_code}"
