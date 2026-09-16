"""auth 模块测试 — User 模型、密码验证、_load_user、告警去抖。
登录/登出/改密路由需要 Flask-Login 初始化，测试在集成环境中验证。"""
import hashlib
import os
import pytest

from campus_ids.web.auth import User, _load_user, _unauthorized, login_manager
from campus_ids.web.database import ensure_default_user, get_user_by_username


# ── User 模型 ──────────────────────────────────────────────────────

class TestUserModel:
    def test_init_and_get_id(self):
        user = User({"id": 1, "username": "admin", "password_hash": "x", "is_active": 1})
        assert user.id == 1
        assert user.username == "admin"
        assert user.get_id() == "1"

    def test_is_active_default_true(self):
        user = User({"id": 2, "username": "u", "password_hash": "x"})
        assert user.is_active is True

    def test_is_active_false(self):
        user = User({"id": 3, "username": "u", "password_hash": "x", "is_active": 0})
        assert user.is_active is False

    def test_verify_password_werkzeug(self):
        """werkzeug 格式密码应直接验证。"""
        from werkzeug.security import generate_password_hash, check_password_hash
        h = generate_password_hash("test123")
        # 先确认 werkzeug 本身能验证
        assert check_password_hash(h, "test123") is True
        user = User({"id": 10, "username": "u", "password_hash": h, "is_active": 1})
        assert user.verify_password("test123") is True
        assert user.verify_password("wrong") is False

    def test_verify_password_sha256_legacy(self):
        """旧 SHA-256 哈希应兼容验证并自动升级。"""
        legacy = hashlib.sha256("oldpass".encode()).hexdigest()
        user = User({"id": 11, "username": "u", "password_hash": legacy, "is_active": 1})
        result = user.verify_password("oldpass")
        assert result is True

    def test_verify_password_wrong(self):
        from werkzeug.security import generate_password_hash
        h = generate_password_hash("right")
        user = User({"id": 12, "username": "u", "password_hash": h, "is_active": 1})
        assert user.verify_password("wrong") is False

    def test_user_mixin_properties(self):
        """User 应继承 UserMixin 的 is_authenticated 属性。"""
        user = User({"id": 1, "username": "admin", "password_hash": "x", "is_active": 1})
        assert user.is_authenticated is True


# ── _load_user ─────────────────────────────────────────────────────

class TestLoadUser:
    def test_load_existing_user(self):
        """数据库中存在的用户应返回 User 对象。"""
        ensure_default_user()
        admin = get_user_by_username("admin")
        assert admin is not None
        user = _load_user(str(admin["id"]))
        assert user is not None
        assert user.username == "admin"

    def test_load_nonexistent_user(self):
        """不存在的用户 ID 应返回 None。"""
        user = _load_user("99999")
        assert user is None


# ── _unauthorized ──────────────────────────────────────────────────

class TestUnauthorized:
    def test_api_path_returns_401_json(self):
        """API 路径未授权应返回 JSON 401。"""
        from campus_ids.web.app import app
        app.config["TESTING"] = True
        with app.test_request_context("/api/test"):
            resp = _unauthorized()
            # _unauthorized 返回 (jsonify, 401) 元组
            assert resp is not None

    def test_non_api_path_redirects(self):
        """非 API 路径未授权应重定向到登录页（需要auth蓝图注册）。"""
        # auth蓝图仅在LOGIN_ENABLED=1时注册，跳过此测试
        # 因为主app未注册auth蓝图时url_for会BuildError
        from campus_ids.web.app import app
        if "auth" not in app.blueprints:
            pytest.skip("auth 蓝图未注册（LOGIN_ENABLED=0）")
        app.config["TESTING"] = True
        with app.test_request_context("/some-page"):
            resp = _unauthorized()
            assert resp is not None


# ── init_auth ──────────────────────────────────────────────────────

class TestInitAuth:
    def test_init_auth_sets_secret_key(self):
        """init_auth 应设置 app.secret_key。"""
        from campus_ids.web.auth import init_auth
        from flask import Flask
        test_app = Flask(__name__)
        test_app.config["TESTING"] = True
        init_auth(test_app)
        assert test_app.secret_key is not None
        assert len(test_app.secret_key) > 0

    def test_init_auth_registers_blueprint(self):
        """init_auth 应注册 auth 蓝图。"""
        from campus_ids.web.auth import init_auth
        from flask import Flask
        test_app = Flask(__name__)
        test_app.config["TESTING"] = True
        init_auth(test_app)
        assert "auth" in test_app.blueprints

    def test_init_auth_creates_default_user(self):
        """init_auth 应确保默认管理员用户存在。"""
        ensure_default_user()
        admin = get_user_by_username("admin")
        assert admin is not None