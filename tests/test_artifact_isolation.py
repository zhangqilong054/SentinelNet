"""产物隔离回归测试 — 断言 pytest 绝不改写项目根的生产产物。

背景（2026-09-17 定为 P0）
---------------------------
`tests/conftest.py` 的 `_isolate_artifacts` 初版只 patch 了 `campus_ids.config`
的模块属性，但 20 个消费模块全都用 `from campus_ids.config import X` —— 该写法在
**import 期就按值绑定**，此后改 `config.X` 对消费方无效。实测 12 个消费路径
**0 个生效**，全量跑 `pytest` 仍改写：

    evaluation_report.txt / traffic_stats.csv / traffic_data.csv / sentinelnet.db

而这些文件**全部在 `.gitignore` 内，没有版本控制兜底**。

本文件是修复的回归防线。如果有人把 fixture 改回"只 patch config"，
或新增了绕过 config 的硬编码路径，这里的断言会立刻失败。

`artifact_bindings` / `project_root` 两个 fixture 由 `tests/conftest.py` 提供。
"""
from __future__ import annotations

from pathlib import Path

from tests.conftest import is_under


class TestArtifactIsolation:
    """验证 `_isolate_artifacts` 真的把所有产物路径挪进了 tmp。"""

    def test_no_binding_points_into_project_root(
        self, artifact_bindings, project_root, tmp_path
    ):
        """核心断言：没有任何产物绑定点仍指向项目根。"""
        leaked = [
            (name, path)
            for name, path in artifact_bindings
            if is_under(path, project_root) and not is_under(path, tmp_path)
        ]
        assert not leaked, (
            "以下产物绑定点仍指向项目根，测试会改写生产产物：\n"
            + "\n".join(f"  {name} = {path}" for name, path in leaked)
        )

    def test_bindings_are_not_empty(self, artifact_bindings):
        """防 fixture 空转：必须确实发现了产物绑定点。"""
        assert artifact_bindings, (
            "未发现任何产物绑定点 —— 隔离 fixture 可能已失效或扫描逻辑被改坏"
        )

    def test_all_bindings_inside_tmp(self, artifact_bindings, tmp_path):
        """所有绑定点都应落在本次测试的 tmp_path 内。"""
        outside = [
            (name, path)
            for name, path in artifact_bindings
            if not is_under(path, tmp_path)
        ]
        assert not outside, (
            "以下绑定点不在 tmp_path 内：\n"
            + "\n".join(f"  {name} = {path}" for name, path in outside)
        )


class TestRepresentativeConsumers:
    """逐模块核对关键消费方 —— 这些正是当初漏掉的按值绑定点。"""

    def test_train_and_evaluation_module_paths(self, tmp_path):
        import campus_ids.model.evaluation as evaluation
        import campus_ids.model.train as train

        # 注：EVALUATION_PATH 由 model/evaluation.py 绑定（train.py 不导入它）
        for module, attr in [
            (train, "MODEL_PATH"),
            (train, "CONFUSION_MATRIX_PATH"),
            (train, "DATA_DIR"),
            (train, "TRAFFIC_CSV"),
            (evaluation, "EVALUATION_PATH"),
        ]:
            value = getattr(module, attr)
            assert is_under(value, tmp_path), f"{module.__name__}.{attr} = {value} 未重定向"

    def test_runtime_state_paths(self, tmp_path):
        """RuntimeState 的产物路径也应落在 tmp。"""
        from campus_ids.runtime.state import RuntimeState
        state = RuntimeState()
        # RuntimeState 不直接持有产物路径常量，但 data_dir 应指向 tmp
        from campus_ids.runtime.settings import get_settings
        assert get_settings().data_dir == tmp_path

    def test_data_loader_paths(self, tmp_path):
        import campus_ids.model.data_loader as data_loader

        for attr in ("TRAFFIC_CSV", "DATA_DIR"):
            value = getattr(data_loader, attr)
            assert is_under(value, tmp_path), f"data_loader.{attr} = {value} 未重定向"

    def test_logging_config_redirected(self, tmp_path):
        """logging_config 把 config.LOG_DIR 重命名为 _DEFAULT_LOG_DIR 后绑定。"""
        import campus_ids.logging_config as logging_config

        value = getattr(logging_config, "_DEFAULT_LOG_DIR", None)
        if value is not None:
            assert is_under(value, tmp_path), f"_DEFAULT_LOG_DIR = {value} 未重定向"


class TestSettingsDataDir:
    """新应用路径：Settings.data_dir 也必须落在 tmp。"""

    def test_settings_data_dir_is_tmp(self, tmp_path):
        from campus_ids.runtime.settings import get_settings

        assert get_settings().data_dir == tmp_path

    def test_settings_survives_other_fixture_env_purge(self, tmp_path):
        """`reset_settings()` 后仍应指向 tmp。

        回归点：`test_settings.py::_clean_env` 曾清空所有 `CAMPUS_IDS_*`，
        连带删掉 `CAMPUS_IDS_DATA_DIR`，使重置后的 Settings 又指回项目根。
        """
        from campus_ids.runtime.settings import get_settings, reset_settings

        reset_settings()
        assert get_settings().data_dir == tmp_path
        assert get_settings().log_dir == tmp_path / "logs"


class TestWriteDoesNotEscape:
    """端到端：通过重定向后的路径写文件，只应落在 tmp。"""

    def test_evaluation_report_write_lands_in_tmp(self, tmp_path, project_root):
        import campus_ids.model.evaluation as evaluation

        evaluation.EVALUATION_PATH.write_text("isolation-probe", encoding="utf-8")
        assert (tmp_path / "evaluation_report.txt").read_text(encoding="utf-8") == "isolation-probe"
        real = project_root / "evaluation_report.txt"
        assert real.read_text(encoding="utf-8") != "isolation-probe"

    def test_train_artifact_write_lands_in_tmp(self, tmp_path, project_root):
        """model.pkl 由 joblib 写出，这里只验证路径本身已被重定向。"""
        import campus_ids.model.train as train

        train.MODEL_PATH.write_bytes(b"isolation-probe")
        assert train.MODEL_PATH == tmp_path / "model.pkl"
        assert not (project_root / "model.pkl").read_bytes() == b"isolation-probe"
