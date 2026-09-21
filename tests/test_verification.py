"""O-10: 密码验证与安全机制单元测试。

验证 web.auth.verify_password / hash_password 的行为：
- werkzeug pbkdf2 格式正确/错误密码
- 空哈希返回 False（而非抛 ValueError）
- hash_password 生成 werkzeug 格式哈希
- 边界情况：空密码、非哈希字符串
"""

from campus_ids.web.auth import verify_password, hash_password


class TestVerifyPasswordWerkzeug:
    """验证 werkzeug 格式哈希的密码校验。"""

    def test_pbkdf2_hash_correct_password(self):
        """pbkdf2 格式哈希 + 正确密码应通过。"""
        from werkzeug.security import generate_password_hash
        pw = "secureP@ss123"
        h = generate_password_hash(pw, method="pbkdf2:sha256")
        assert verify_password(pw, h) is True

    def test_pbkdf2_hash_wrong_password(self):
        """pbkdf2 格式哈希 + 错误密码应拒绝。"""
        from werkzeug.security import generate_password_hash
        h = generate_password_hash("correct", method="pbkdf2:sha256")
        assert verify_password("wrong", h) is False

    def test_scrypt_hash_correct_password(self):
        """scrypt 格式哈希 + 正确密码应通过。"""
        from werkzeug.security import generate_password_hash
        pw = "scryptP@ss"
        h = generate_password_hash(pw, method="scrypt")
        assert verify_password(pw, h) is True


class TestVerifyPasswordEdgeCases:
    """边界情况测试。"""

    def test_empty_password_fails(self):
        """空密码应验证失败。"""
        from werkzeug.security import generate_password_hash
        h = generate_password_hash("nonempty", method="pbkdf2:sha256")
        assert verify_password("", h) is False

    def test_empty_hash_returns_false(self):
        """空哈希应返回 False（而非抛 ValueError）。

        回归：CAMPUS_IDS_API_TOKEN 未配置时 password_hash 为空串，
        werkzeug check_password_hash 会因 pwhash.split('$', 2) 失败抛 ValueError。
        verify_password 必须捕获此异常并返回 False。
        """
        assert verify_password("any_password", "") is False

    def test_non_hash_string_returns_false(self):
        """非哈希格式的字符串应返回 False。"""
        assert verify_password("password", "not_a_hash") is False

    def test_none_hash_returns_false(self):
        """None 哈希应返回 False。"""
        # verify_password 对空值统一返回 False
        assert verify_password("password", "") is False


class TestHashPassword:
    """验证 hash_password 生成兼容的哈希。"""

    def test_hash_password_generates_werkzeug_format(self):
        """hash_password 应生成 werkzeug 格式哈希。"""
        h = hash_password("test_password")
        assert h.startswith(("pbkdf2:", "sha256$", "scrypt:"))

    def test_hash_password_verifiable(self):
        """hash_password 生成的哈希应可被 verify_password 验证。"""
        pw = "verifiable_password"
        h = hash_password(pw)
        assert verify_password(pw, h) is True

    def test_hash_password_wrong_password_fails(self):
        """hash_password 生成的哈希 + 错误密码应验证失败。"""
        h = hash_password("correct")
        assert verify_password("wrong", h) is False

    def test_hash_password_different_salts(self):
        """同一密码两次哈希应产生不同结果（随机盐）。"""
        h1 = hash_password("same_password")
        h2 = hash_password("same_password")
        assert h1 != h2  # 盐不同，哈希不同