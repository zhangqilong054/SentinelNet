# -*- coding: utf-8 -*-
"""测试隔离体检 —— `tests/conftest.py` 的产物重定向到底生效没有？

    C:/Users/18551/anaconda3/python.exe scripts/probe_test_isolation.py

**为什么要单独体检**：`conftest.py` 里写 `monkeypatch.setattr(config, "EVALUATION_PATH", ...)`
看起来足够，但 `setattr(config, X, ...)` 只改 **config 模块自己的属性**；
消费方写的是 `from campus_ids.config import X` —— 这是**按值绑定**，
import 期就把真实路径复制进了自己的命名空间，所以 `config.X` 改了也没用。

本脚本**直接复用 conftest 的实现**（不复制逻辑，避免两边漂移），复刻 fixture 的全部
动作，然后逐个检查消费模块实际持有的路径，给出"几个被真正重定向"。

只读：只在临时目录下动，不写项目内任何文件。退出码 0 = 全部重定向，1 = 有泄漏。

配套体检（跑完本脚本后执行，看产物 md5 是否变化）：

    md5sum model.pkl evaluation_report.txt traffic_stats.csv sentinelnet.db \
           traffic_data.csv confusion_matrix.png > _h.txt
    python -m pytest tests/ -q
    md5sum -c _h.txt          # 出现 FAILED 就是泄漏
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))          # 以便 import tests.conftest
os.environ.setdefault("CAMPUS_IDS_DEBUG", "1")

# 消费方模块：`from campus_ids.config import X` 的落点
import campus_ids.config as config  # noqa: E402
import campus_ids.logging_config as logging_config  # noqa: E402
import campus_ids.model.evaluation as evaluation  # noqa: E402
import campus_ids.model.train as train  # noqa: E402
import campus_ids.model.data_loader as data_loader  # noqa: E402

CONSUMERS = [
    ("model/train.py", train,
     ["MODEL_PATH", "CONFUSION_MATRIX_PATH", "TRAFFIC_CSV", "DATA_DIR"]),
    ("model/evaluation.py", evaluation, ["EVALUATION_PATH"]),
    ("model/data_loader.py", data_loader, ["TRAFFIC_CSV", "DATA_DIR"]),
    ("logging_config.py", logging_config, ["_DEFAULT_LOG_DIR"]),
]


class _FakeMonkeyPatch:
    """最小替身 —— conftest 的重定向函数只用到 `setattr(..., raising=False)`。"""

    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def setattr(self, target, name, value, raising: bool = False):  # noqa: A003
        if not hasattr(target, name) and not raising:
            raise AttributeError(f"{target!r} 没有属性 {name}")
        setattr(target, name, value)
        self.records.append((getattr(target, "__name__", repr(target)), name))


def main() -> int:
    from tests.conftest import (  # noqa: E402
        _redirect_config_bindings, is_under,
    )

    tmp_path = Path(tempfile.mkdtemp(prefix="sn_isolate_"))
    print("=" * 78)
    print("测试隔离体检")
    print("=" * 78)
    print(f"临时目录: {tmp_path}")

    # ── 复刻 conftest._isolate_artifacts 的全部动作 ──────────────────
    os.environ["CAMPUS_IDS_DATA_DIR"] = str(tmp_path)          # monkeypatch.setenv
    os.environ["CAMPUS_IDS_LOG_DIR"] = str(tmp_path / "logs")

    from campus_ids.runtime.db import reset_engine  # noqa: E402
    from campus_ids.runtime.settings import reset_settings  # noqa: E402

    reset_settings()
    reset_engine()

    mp = _FakeMonkeyPatch()
    patched = _redirect_config_bindings(mp, tmp_path)           # monkeypatch.setattr × N

    print("\n--- config 模块自身（被 patch 的对象）---")
    print(f"  config.DATA_DIR         {config.DATA_DIR}")
    print(f"  config.EVALUATION_PATH  {config.EVALUATION_PATH}")
    print(f"\n  本次共改写 {patched} 处绑定（含 config 自身）")

    print("\n--- 消费模块实际持有的路径（关键）---")
    rows: list[tuple[str, Path, bool]] = []
    for src, mod, attrs in CONSUMERS:
        for attr in attrs:
            if not hasattr(mod, attr):
                print(f"  ⚪ 未绑定      {src}:{attr}")
                continue
            value = Path(str(getattr(mod, attr)))
            inside = is_under(value, tmp_path)
            rows.append((f"{src}:{attr}", value, inside))
            print(f"  {'✅ 已重定向' if inside else '🔴 仍泄漏  '}  {src}:{attr:<24} {value}")

    # ── 新应用路径 ──────────────────────────────────────────────────
    from campus_ids.runtime.settings import get_settings  # noqa: E402
    settings = get_settings()
    settings_ok = settings.data_dir == tmp_path
    rows.append(("runtime/settings.data_dir", settings.data_dir, settings_ok))
    print(f"\n  {'✅ 已重定向' if settings_ok else '🔴 仍泄漏  '}  runtime/settings.data_dir  {settings.data_dir}")

    redirected = sum(1 for _n, _v, ok in rows if ok)
    leaked = [(n, v) for n, v, ok in rows if not ok]

    print("\n" + "=" * 78)
    if leaked:
        print(f"🔴 只有 {redirected}/{len(rows)} 被重定向 —— 隔离**失效**，pytest 会改写生产产物")
        print("   泄漏项：")
        for name, value in leaked:
            print(f"     {name} = {value}")
        return 1
    print(f"✅ 全部 {redirected}/{len(rows)} 被重定向 —— 隔离有效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())