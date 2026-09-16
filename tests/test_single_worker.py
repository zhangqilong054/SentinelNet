"""单 worker 不变量测试 — 验证 _assert_single_worker() 四组断言。

ADR-0001 §4.1：运行时状态全部驻留进程内，多 worker 会导致状态分裂。
create_app() 启动时调用 _assert_single_worker()，检测到违规即拒绝启动。

四组断言：
1. WEB_CONCURRENCY>1 → RuntimeError
2. --workers>1 / --workers=N>1 → RuntimeError
3. 非数字值（WEB_CONCURRENCY/--workers）→ RuntimeError
4. 单 worker 正常启动（WEB_CONCURRENCY=1/--workers=1/无约束）
"""
from __future__ import annotations

import os
import sys

import pytest

from campus_ids.web_new.app import _assert_single_worker, create_app
from campus_ids.runtime.settings import reset_settings


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