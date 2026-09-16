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


@pytest.fixture(autouse=True)
def _isolate_artifacts(tmp_path, monkeypatch):
    """将产物路径重定向到 tmp_path，防止测试改写生产文件。

    P0-4 修复：测试套件通过旧 Flask 端点（/api/auto/start、/api/save 等）
    会覆盖 model.pkl、evaluation_report.txt、traffic_stats.csv、sentinelnet.db
    等生产产物。本 fixture 将所有路径重定向到临时目录。

    保护范围：
    - config.py 模块级常量（MODEL_PATH、EVALUATION_PATH 等）
    - runtime/settings.py 的 data_dir（通过 CAMPUS_IDS_DATA_DIR 环境变量）
    - runtime/db.py 的引擎单例（reset_engine 确保新路径生效）
    """
    # 1. 设置 CAMPUS_IDS_DATA_DIR 环境变量 → 影响 Settings.data_dir
    monkeypatch.setenv("CAMPUS_IDS_DATA_DIR", str(tmp_path))

    # 2. 重置 Settings 和 DB 引擎单例，使新 data_dir 生效
    from campus_ids.runtime.settings import reset_settings
    from campus_ids.runtime.db import reset_engine
    reset_settings()
    reset_engine()

    # 3. monkeypatch config.py 模块级常量（旧 Flask 代码路径）
    import campus_ids.config as config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "MODEL_PATH", tmp_path / "model.pkl")
    monkeypatch.setattr(config, "TRAFFIC_CSV", tmp_path / "traffic_data.csv")
    monkeypatch.setattr(config, "TRAFFIC_STATS_CSV", tmp_path / "traffic_stats.csv")
    monkeypatch.setattr(config, "CONFUSION_MATRIX_PATH", tmp_path / "confusion_matrix.png")
    monkeypatch.setattr(config, "EVALUATION_PATH", tmp_path / "evaluation_report.txt")
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "models" / "runs")
    monkeypatch.setattr(config, "LATEST_JSON", tmp_path / "models" / "latest.json")
    monkeypatch.setattr(config, "BEST_JSON", tmp_path / "models" / "best.json")
    monkeypatch.setattr(config, "REGISTRY_JSON", tmp_path / "models" / "registry.json")

    yield