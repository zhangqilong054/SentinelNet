"""测试全局配置 — 确保所有测试在安全默认值下运行。"""
from __future__ import annotations

import os
import pytest


@pytest.fixture(autouse=True)
def _test_debug_mode():
    """为所有测试开启 debug 模式，允许不安全默认值。

    create_app() 在非 debug 模式下会拒绝使用公开默认 secret_key 启动。
    测试环境属于开发上下文，设置 CAMPUS_IDS_DEBUG=1 使应用允许默认密钥
    （仅打印 WARNING），避免每个测试都要手动设置安全密钥。

    secret_key 断言的 RuntimeError / WARNING 行为由
    test_single_worker.py 中的 TestSecretKeyAssertion 专项测试覆盖。
    """
    prev = os.environ.get("CAMPUS_IDS_DEBUG")
    os.environ["CAMPUS_IDS_DEBUG"] = "1"
    yield
    if prev is None:
        os.environ.pop("CAMPUS_IDS_DEBUG", None)
    else:
        os.environ["CAMPUS_IDS_DEBUG"] = prev