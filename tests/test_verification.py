"""O-10: 密码验证与安全机制单元测试。

验证 User.verify_password 的三种哈希格式兼容：
- werkzeug pbkdf2 格式
- werkzeug sha256$ 格式
- 旧 SHA-256 无盐哈希（含自动升级）
"""
import hashlib
from unittest.mock import patch, MagicMock

import pytest

from campus_ids.web.auth import User


def _make_user(password_hash: str, user_id: int = 1, username: str = "testuser") -> User:
    """构建测试用 User。"""
    return User({
        "id": user_id,
        "username": username,
        "password_hash": password_hash,
        "is_active": 1,
    })


class TestVerifyPasswordWerkzeug:
    """验证 werkzeug 格式哈希的密码校验。"""

    def test_pbkdf2_hash_correct_password(self):
        """pbkdf2 格式哈希 + 正确密码应通过。"""
        from werkzeug.security import generate_password_hash
        pw = "secureP@ss123"
        h = generate_password_hash(pw, method="pbkdf2:sha256")
        user = _make_user(h)
        assert user.verify_password(pw) is True

    def test_pbkdf2_hash_wrong_password(self):
        """pbkdf2 格式哈希 + 错误密码应拒绝。"""
        from werkzeug.security import generate_password_hash
        h = generate_password_hash("correct", method="pbkdf2:sha256")
        user = _make_user(h)
        assert user.verify_password("wrong") is False


class TestVerifyPasswordLegacySHA256:
    """验证旧 SHA-256 无盐哈希的密码校验及自动升级。"""

    def test_legacy_sha256_correct_password(self):
        """旧 SHA-256 哈希 + 正确密码应通过并触发升级。"""
        pw = "admin"
        legacy_hash = hashlib.sha256(pw.encode()).hexdigest()
        user = _make_user(legacy_hash)

        with patch("campus_ids.web.auth.update_user_password") as mock_update:
            result = user.verify_password(pw)
            assert result is True
            # 应调用密码升级
            mock_update.assert_called_once()
            # 新哈希应为 werkzeug 格式
            new_hash = mock_update.call_args[0][1]
            assert new_hash.startswith(("pbkdf2:", "sha256$", "scrypt:"))

    def test_legacy_sha256_wrong_password(self):
        """旧 SHA-256 哈希 + 错误密码应拒绝。"""
        legacy_hash = hashlib.sha256("admin".encode()).hexdigest()
        user = _make_user(legacy_hash)
        assert user.verify_password("wrong_password") is False

    def test_legacy_hash_upgrade_failure_still_returns_true(self):
        """旧 SHA-256 正确密码但升级失败时仍应返回 True。"""
        pw = "admin"
        legacy_hash = hashlib.sha256(pw.encode()).hexdigest()
        user = _make_user(legacy_hash)

        with patch("campus_ids.web.auth.update_user_password", side_effect=Exception("DB error")):
            result = user.verify_password(pw)
            assert result is True  # 验证成功，升级失败不影响登录


class TestVerifyPasswordEdgeCases:
    """边界情况测试。"""

    def test_empty_password_fails(self):
        """空密码应验证失败。"""
        from werkzeug.security import generate_password_hash
        h = generate_password_hash("nonempty", method="pbkdf2:sha256")
        user = _make_user(h)
        assert user.verify_password("") is False

    def test_user_is_active_property(self):
        """is_active 属性应正确反映。"""
        user_active = _make_user("fake_hash")
        assert user_active.is_active is True

        user_inactive = User({
            "id": 2, "username": "disabled",
            "password_hash": "fake", "is_active": 0,
        })
        assert user_inactive.is_active is False

    def test_get_id_returns_string(self):
        """get_id 应返回字符串形式的 ID。"""
        user = _make_user("fake_hash", user_id=42)
        assert user.get_id() == "42"