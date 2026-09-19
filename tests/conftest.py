"""测试全局配置 — 确保所有测试在安全默认值下运行。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest


# ══ 会话级兜底重定向 ══════════════════════════════════════════════
# 必须在**任何 campus_ids 模块被 import 之前**执行：`config.py` 在 import 期就用
# `CAMPUS_IDS_DATA_DIR` 算出 `DATA_DIR`，此后所有 `from campus_ids.config import X`
# 都绑定到这个值。
#
# 为什么不能只靠下面的 per-test fixture：
#   编排端点会启动**后台训练线程**（`/api/auto/start` → `helpers._auto_worker` →
#   `train()` → `_save_evaluation_report`）。该线程可能**活过测试 teardown**，
#   那时 per-test 的 monkeypatch 已撤销、绑定回落到项目根 → 线程改写真实产物。
#   2026-09-17 实测：正是这条路径改写了 `evaluation_report.txt`。
#
# 会话级重定向让"回落的落点"也变成临时目录，从而堵死该路径。
_SESSION_TMP = Path(tempfile.mkdtemp(prefix="sn_pytest_session_"))
os.environ["CAMPUS_IDS_DATA_DIR"] = str(_SESSION_TMP)
os.environ["CAMPUS_IDS_LOG_DIR"] = str(_SESSION_TMP / "logs")
# DEBUG 必须在 import 期就设好（2026-09-19）：config.py 的兼容委托层在模块顶层
# 执行 `_s = get_settings()`，任何 campus_ids 模块的首次 import 都会缓存 Settings
# 单例——若此时 DEBUG 未设，缓存的是 debug=False，随后 create_app() 的
# secret_key 断言（非 debug + 默认密钥 → RuntimeError）会让全部用 app 的测试
# ERROR。仅靠 per-test 的 _test_debug_mode 夹具来不及：夹具 setup 时单例可能
# 已被夹具链条中的模块导入提前创建。测试进程视为开发上下文，全局开启。
os.environ.setdefault("CAMPUS_IDS_DEBUG", "1")


# ── 产物路径重定向表 ──────────────────────────────────────────────
# key = campus_ids.config 模块属性名；value = 目标目录下的相对路径（"" 表示目录本身）
_ARTIFACT_PATHS: dict[str, str] = {
    "DATA_DIR": "",
    "MODEL_PATH": "model.pkl",
    "TRAFFIC_CSV": "traffic_data.csv",
    "CONFUSION_MATRIX_PATH": "confusion_matrix.png",
    "EVALUATION_PATH": "evaluation_report.txt",
    "MODELS_DIR": "models",
    "RUNS_DIR": "models/runs",
    "LATEST_JSON": "models/latest.json",
    "BEST_JSON": "models/best.json",
    "REGISTRY_JSON": "models/registry.json",
    "LOG_DIR": "logs",
}

# 除 config 常量外还需重定向的名字：
#   _DEFAULT_LOG_DIR —— logging_config.py 对 config.LOG_DIR 的按值绑定（重命名后的别名）
_EXTRA_NAMES: tuple[str, ...] = ("_DEFAULT_LOG_DIR",)

# 项目根目录 —— 生产产物所在位置，测试绝不应写它
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 隔离专用环境变量：个别测试的环境清理 fixture 不得连带删除
# （test_settings.py 的 _clean_env 会清空所有 CAMPUS_IDS_*，需显式豁免这些）
ISOLATION_ENV_VARS = frozenset({
    "CAMPUS_IDS_DATA_DIR",
    "CAMPUS_IDS_LOG_DIR",
    "CAMPUS_IDS_DEBUG",
})


def artifact_targets(tmp_path: Path) -> dict[str, Path]:
    """返回产物常量的目标路径（全部位于 tmp_path 下）。"""
    return {
        name: (tmp_path / rel if rel else tmp_path)
        for name, rel in _ARTIFACT_PATHS.items()
    }


def is_under(path: Path, root: Path) -> bool:
    """判断 path 是否位于 root 之下（跨盘符返回 False）。"""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return True


def iter_artifact_bindings() -> list[tuple[str, Path]]:
    """列出当前所有 campus_ids 模块持有的产物路径。

    供 `tests/test_artifact_isolation.py` 断言"没有一个绑定点指向项目根"。
    """
    bindings: list[tuple[str, Path]] = []
    for mod_name, module in list(sys.modules.items()):
        if module is None or not _is_campus_module(mod_name):
            continue
        if mod_name == "campus_ids.config":
            continue
        for attr in list(_ARTIFACT_PATHS) + list(_EXTRA_NAMES):
            value = getattr(module, attr, None)
            if isinstance(value, Path):
                bindings.append((f"{mod_name}.{attr}", value))
    return bindings


def _is_campus_module(mod_name: str) -> bool:
    return mod_name == "campus_ids" or mod_name.startswith("campus_ids.")


def _ensure_consumer_modules_loaded() -> None:
    """确保持有产物路径常量的消费模块已加载到 sys.modules。

    `from campus_ids.config import X` 在 import 期按值绑定，
    因此必须先加载这些模块，才能在 _redirect_config_bindings 中改写绑定。
    旧 web 层删除后，不再有 _isolate_legacy_db 触发的级联 import，
    需要显式加载关键消费模块。
    """
    import campus_ids.model.train
    import campus_ids.model.evaluation
    import campus_ids.model.data_loader
    import campus_ids.logging_config


def _redirect_config_bindings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> int:
    """把产物常量同步到所有**按值绑定**它们的模块。

    根因：`from campus_ids.config import MODEL_PATH` 在 import 期就把值绑进消费
    模块的命名空间，因此只 patch `config.MODEL_PATH` 对消费方无效
    （2026-09-17 实测：12 个消费路径 0 个生效，`pytest` 仍改写生产产物）。

    做法：遍历已加载的 campus_ids.* 模块，凡是持有 Path 类型产物常量的，
    一并改写到 tmp 路径。返回改写的绑定数。
    """
    import campus_ids.config as config

    # 确保持有 Path 常量的消费模块已加载
    _ensure_consumer_modules_loaded()

    targets = artifact_targets(tmp_path)
    patched = 0

    for mod_name, module in list(sys.modules.items()):
        if module is None or module is config or not _is_campus_module(mod_name):
            continue
        for attr, new_value in targets.items():
            current = getattr(module, attr, None)
            if not isinstance(current, Path) or current == new_value:
                continue  # 未绑定该常量，或已指向目标
            monkeypatch.setattr(module, attr, new_value, raising=False)
            patched += 1
        for attr in _EXTRA_NAMES:
            current = getattr(module, attr, None)
            if not isinstance(current, Path):
                continue
            new_value = targets["LOG_DIR"] if attr == "_DEFAULT_LOG_DIR" else targets["DATA_DIR"]
            if current == new_value:
                continue
            monkeypatch.setattr(module, attr, new_value, raising=False)
            patched += 1

    # config 自身也要改，使此后新 import 的模块直接拿到 tmp 路径
    for attr, new_value in targets.items():
        monkeypatch.setattr(config, attr, new_value, raising=False)
        patched += 1

    return patched


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
    """将所有产物路径重定向到 tmp_path，防止测试改写生产文件。

    保护范围（三层，缺一不可）：
    1. **config 常量的按值绑定**（`from campus_ids.config import MODEL_PATH`）
       —— 必须逐个消费模块改写，只 patch config 无效；
    2. **新应用的 Settings.data_dir**（经 `CAMPUS_IDS_DATA_DIR` 环境变量 + 重置单例）。

    被保护的产物：model.pkl / evaluation_report.txt / confusion_matrix.png /
    traffic_data.csv / traffic_stats.csv / models/ / logs/。

    回归防线：`tests/test_artifact_isolation.py` 断言隔离后没有任何绑定点
    指向项目根目录。改动本 fixture 时该测试必须仍然通过。
    """
    # 1. 环境变量 → 影响 Settings.data_dir（新应用路径）
    monkeypatch.setenv("CAMPUS_IDS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUS_IDS_LOG_DIR", str(tmp_path / "logs"))

    # 2. 重置单例，使新 data_dir 生效
    from campus_ids.runtime.settings import reset_settings
    from campus_ids.runtime.db import reset_engine

    reset_settings()
    reset_engine()

    # 3. 改写所有按值绑定的产物常量
    _redirect_config_bindings(monkeypatch, tmp_path)

    yield

    # 4. 收敛单例，避免把 tmp 路径的实例留给下一个测试
    reset_engine()
    reset_settings()


# ── 供隔离回归测试使用的 fixture ──────────────────────────────────


@pytest.fixture
def artifact_bindings() -> list[tuple[str, Path]]:
    """当前所有 campus_ids 模块持有的产物路径（名称, 路径）。

    见 `tests/test_artifact_isolation.py`。
    """
    return iter_artifact_bindings()


@pytest.fixture
def project_root() -> Path:
    """项目根目录（生产产物所在处）。"""
    return PROJECT_ROOT


def _pin_session_bindings() -> None:
    """把**已加载**的 campus_ids 模块的产物绑定永久指向会话临时目录。

    处理两种情况：
    1. config 尚未 import —— 环境变量已就位，它自然会算成会话 tmp；
    2. config 已被更早的插件/导入链加载（拿到项目根路径）—— 这里强制改写。

    与 per-test fixture 的区别：这里是**永久**改写（不撤销），因此后台线程
    在测试结束后仍只会写临时目录，不会回落到项目根。
    """
    targets = artifact_targets(_SESSION_TMP)

    for mod_name, module in list(sys.modules.items()):
        if module is None or not _is_campus_module(mod_name):
            continue
        for attr, new_value in targets.items():
            if isinstance(getattr(module, attr, None), Path):
                setattr(module, attr, new_value)
        # 派生常量 / 别名
        if isinstance(getattr(module, "_DEFAULT_LOG_DIR", None), Path):
            module._DEFAULT_LOG_DIR = targets["LOG_DIR"]


# 立即执行：确保此后任何后台线程都不会写到项目根
_pin_session_bindings()


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """会话结束断言：没有任何产物绑定回落到项目根。

    专门针对"后台线程活过 teardown"这条路径 —— per-test monkeypatch 撤销后，
    绑定若回落到项目根，滞后的训练线程（auto / demo）就会改写真实产物。
    会话级的 `_pin_session_bindings()` 应保证回落点是临时目录；这里做兜底断言，
    违反时让整个测试会话以非零状态退出。
    """
    import campus_ids.config as config

    leaked = [
        (name, path)
        for name, path in iter_artifact_bindings()
        if is_under(path, PROJECT_ROOT)
    ]
    for attr in _ARTIFACT_PATHS:
        value = getattr(config, attr, None)
        if isinstance(value, Path) and is_under(value, PROJECT_ROOT):
            leaked.append((f"campus_ids.config.{attr}", value))

    if not leaked:
        return

    session.exitstatus = 1
    print("\n" + "=" * 78)
    print("🔴 产物隔离失败：会话结束时以下绑定仍指向项目根")
    for name, path in leaked:
        print(f"     {name} = {path}")
    print("   后果：滞后的后台线程会改写真实产物（无版本控制兜底）。")
    print("   修法：见 tests/conftest.py::_pin_session_bindings")
    print("=" * 78)
