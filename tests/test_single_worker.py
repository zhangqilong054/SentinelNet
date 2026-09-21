"""启动断言测试 — 验证 _assert_single_worker() 与 _assert_secret_key()。

ADR-0001 §4.1：运行时状态全部驻留进程内，多 worker 会导致状态分裂。
ADR-0001 §6.1：secret_key 不得使用公开默认值（生产模式拒绝启动）。

单 worker 四组断言：
1. WEB_CONCURRENCY>1 → RuntimeError
2. --workers>1 / --workers=N>1 → RuntimeError
3. 非数字值（WEB_CONCURRENCY/--workers）→ RuntimeError
4. 单 worker 正常启动（WEB_CONCURRENCY=1/--workers=1/无约束）

secret_key 断言：
1. 默认密钥 + 非调试模式 → RuntimeError（拒绝启动）
2. 默认密钥 + 调试模式 → WARNING（允许启动）
3. 自定义密钥 → 正常启动
4. .env.example 中的公开密钥 → 同默认密钥处理
"""
from __future__ import annotations

import os
import sys

import pytest

from campus_ids.web.app import (
    _assert_single_worker,
    _assert_secret_key,
    _INSECURE_SECRET_KEYS,
    create_app,
)
from campus_ids.runtime.settings import Settings, reset_settings


@pytest.fixture(autouse=True)
def _clean_env():
    """每个测试前后清理环境变量和设置单例。"""
    os.environ.pop("WEB_CONCURRENCY", None)
    reset_settings()
    yield
    os.environ.pop("WEB_CONCURRENCY", None)
    reset_settings()


# ── 第1组：WEB_CONCURRENCY>1 → RuntimeError ──────────────────────


class TestWebConcurrencyExceeded:
    """WEB_CONCURRENCY 环境变量 > 1 时拒绝启动。"""

    def test_web_concurrency_2(self):
        """WEB_CONCURRENCY=2 应抛出 RuntimeError。"""
        os.environ["WEB_CONCURRENCY"] = "2"
        with pytest.raises(RuntimeError, match="WEB_CONCURRENCY=2"):
            _assert_single_worker()

    def test_web_concurrency_4(self):
        """WEB_CONCURRENCY=4 应抛出 RuntimeError。"""
        os.environ["WEB_CONCURRENCY"] = "4"
        with pytest.raises(RuntimeError, match="WEB_CONCURRENCY=4"):
            _assert_single_worker()

    def test_web_concurrency_100(self):
        """WEB_CONCURRENCY=100 应抛出 RuntimeError。"""
        os.environ["WEB_CONCURRENCY"] = "100"
        with pytest.raises(RuntimeError, match="WEB_CONCURRENCY=100"):
            _assert_single_worker()


# ── 第2组：--workers>1 → RuntimeError ─────────────────────────────


class TestWorkersCliExceeded:
    """CLI --workers 参数 > 1 时拒绝启动。"""

    def test_workers_space_2(self, monkeypatch):
        """--workers 2 应抛出 RuntimeError。"""
        monkeypatch.setattr(sys, "argv", ["uvicorn", "--workers", "2"])
        with pytest.raises(RuntimeError, match=r"--workers=2"):
            _assert_single_worker()

    def test_workers_equals_4(self, monkeypatch):
        """--workers=4 应抛出 RuntimeError。"""
        monkeypatch.setattr(sys, "argv", ["uvicorn", "--workers=4"])
        with pytest.raises(RuntimeError, match=r"--workers=4"):
            _assert_single_worker()

    def test_workers_space_8(self, monkeypatch):
        """--workers 8 应抛出 RuntimeError。"""
        monkeypatch.setattr(sys, "argv", ["uvicorn", "--workers", "8"])
        with pytest.raises(RuntimeError, match=r"--workers=8"):
            _assert_single_worker()


# ── 第3组：非数字值 → RuntimeError ────────────────────────────────


class TestNonNumericValues:
    """非数字值给出明确 RuntimeError 而非 ValueError。"""

    def test_web_concurrency_abc(self):
        """WEB_CONCURRENCY=abc 应抛出 RuntimeError（非 ValueError）。"""
        os.environ["WEB_CONCURRENCY"] = "abc"
        with pytest.raises(RuntimeError, match="不是有效整数"):
            _assert_single_worker()

    def test_web_concurrency_empty_not_blocked(self):
        """WEB_CONCURRENCY 为空字符串不触发错误（空字符串视为未设置）。"""
        # 空字符串在 _assert_single_worker 中 if web_concurrency: 为 False
        # 不会进入检查逻辑
        os.environ["WEB_CONCURRENCY"] = ""
        _assert_single_worker()  # 不应抛出异常

    def test_workers_space_abc(self, monkeypatch):
        """--workers abc 应抛出 RuntimeError（非 ValueError）。"""
        monkeypatch.setattr(sys, "argv", ["uvicorn", "--workers", "abc"])
        with pytest.raises(RuntimeError, match="不是有效整数"):
            _assert_single_worker()

    def test_workers_equals_abc(self, monkeypatch):
        """--workers=abc 应抛出 RuntimeError（非 ValueError）。"""
        monkeypatch.setattr(sys, "argv", ["uvicorn", "--workers=abc"])
        with pytest.raises(RuntimeError, match="不是有效整数"):
            _assert_single_worker()


# ── 第4组：单 worker 正常启动 ─────────────────────────────────────


class TestSingleWorkerOk:
    """单 worker 配置下正常启动，不抛出异常。"""

    def test_no_constraint(self):
        """无 WEB_CONCURRENCY、无 --workers 时正常启动。"""
        _assert_single_worker()  # 不应抛出异常

    def test_web_concurrency_1(self):
        """WEB_CONCURRENCY=1 时正常启动。"""
        os.environ["WEB_CONCURRENCY"] = "1"
        _assert_single_worker()  # 不应抛出异常

    def test_workers_1(self, monkeypatch):
        """--workers 1 时正常启动。"""
        monkeypatch.setattr(sys, "argv", ["uvicorn", "--workers", "1"])
        _assert_single_worker()  # 不应抛出异常

    def test_workers_equals_1(self, monkeypatch):
        """--workers=1 时正常启动。"""
        monkeypatch.setattr(sys, "argv", ["uvicorn", "--workers=1"])
        _assert_single_worker()  # 不应抛出异常

    def test_create_app_succeeds(self):
        """create_app() 在单 worker 配置下成功创建应用。"""
        app = create_app()
        assert app is not None
        assert app.title == "SentinelNet"


# ── 组合场景 ──────────────────────────────────────────────────────


class TestCombinedConstraints:
    """WEB_CONCURRENCY 与 --workers 同时设置时的行为。"""

    def test_both_exceeded(self, monkeypatch):
        """WEB_CONCURRENCY=2 + --workers=2：先检查 WEB_CONCURRENCY，抛出 RuntimeError。"""
        os.environ["WEB_CONCURRENCY"] = "2"
        monkeypatch.setattr(sys, "argv", ["uvicorn", "--workers", "2"])
        with pytest.raises(RuntimeError, match="WEB_CONCURRENCY=2"):
            _assert_single_worker()

    def test_env_ok_cli_bad(self, monkeypatch):
        """WEB_CONCURRENCY=1 + --workers=4：CLI 违规，抛出 RuntimeError。"""
        os.environ["WEB_CONCURRENCY"] = "1"
        monkeypatch.setattr(sys, "argv", ["uvicorn", "--workers", "4"])
        with pytest.raises(RuntimeError, match=r"--workers=4"):
            _assert_single_worker()


# ── secret_key 安全断言 ──────────────────────────────────────────


class TestSecretKeyAssertion:
    """_assert_secret_key() 验证：公开默认密钥在非调试模式下拒绝启动。"""

    def test_default_key_production_raises(self):
        """默认密钥 + 非调试模式 → RuntimeError（拒绝启动）。"""
        settings = Settings(secret_key="change-me-in-production", debug=False)
        with pytest.raises(RuntimeError, match="公开默认值"):
            _assert_secret_key(settings)

    def test_default_key_debug_warns(self, caplog):
        """默认密钥 + 调试模式 → WARNING（允许启动）。"""
        import logging

        settings = Settings(secret_key="change-me-in-production", debug=True)
        with caplog.at_level(logging.WARNING):
            _assert_secret_key(settings)  # 不应抛出异常

        assert "secret_key" in caplog.text.lower()
        assert "公开默认值" in caplog.text or "WARNING" in caplog.text

    def test_custom_key_production_ok(self):
        """自定义密钥 + 非调试模式 → 正常启动。"""
        settings = Settings(secret_key="my-super-secret-key-12345", debug=False)
        _assert_secret_key(settings)  # 不应抛出异常

    def test_env_example_key_production_raises(self):
        """.env.example 中的公开密钥 + 非调试模式 → RuntimeError。"""
        settings = Settings(
            secret_key="sentinelnet-dev-secret-key-change-in-prod", debug=False
        )
        with pytest.raises(RuntimeError, match="公开默认值"):
            _assert_secret_key(settings)

    def test_insecure_keys_set_contains_both_defaults(self):
        """_INSECURE_SECRET_KEYS 包含两个已知的公开默认值。"""
        assert "change-me-in-production" in _INSECURE_SECRET_KEYS
        assert "sentinelnet-dev-secret-key-change-in-prod" in _INSECURE_SECRET_KEYS

    def test_create_app_with_default_key_in_debug_mode(self):
        """create_app() 在调试模式 + 默认密钥下可正常创建（conftest 已设 DEBUG=1）。"""
        # conftest.py 的 _test_debug_mode 已设 CAMPUS_IDS_DEBUG=1
        app = create_app()
        assert app is not None

    def test_create_app_with_custom_key_in_production_mode(self):
        """create_app() 在自定义密钥 + 非调试模式下可正常创建。"""
        os.environ["CAMPUS_IDS_SECRET_KEY"] = "my-production-secret-key-67890"
        os.environ.pop("CAMPUS_IDS_DEBUG", None)
        reset_settings()
        try:
            app = create_app()
            assert app is not None
        finally:
            os.environ.pop("CAMPUS_IDS_SECRET_KEY", None)
            os.environ["CAMPUS_IDS_DEBUG"] = "1"
            reset_settings()