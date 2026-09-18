# -*- coding: utf-8 -*-
"""契约录制安全底座（T2.18 / R2）。

**为什么必须存在**：

`scripts/record_golden.py` 录制契约时需要调用真实端点，但某些端点会触发
破坏性操作（训练覆盖 model.pkl、抓包占用网卡等）。

**三道保险**：

1. `bootstrap()` —— `CAMPUS_IDS_DATA_DIR` 指向临时目录，且**必须在 import 任何
   `campus_ids` 模块之前**调用（`config.py` 在 import 期把 `DATA_DIR / MODEL_PATH /
   TRAFFIC_CSV / ...` 算成模块级常量）。
2. `neutralize()` —— 把会真训练的函数换成记录型 no-op。
   关键实现细节：**必须按对象身份在整个 `campus_ids.*` 里替换**，
   因为 `from campus_ids.model.train import train` 是
   **import 期按值绑定**，只改源模块改不到消费方。
3. `assert_products_untouched()` —— 录制前后对 6 个真实产物做 md5 比对，
   任何改写都直接抛错。这是唯一不依赖"我枚举全了危险函数"的兜底。
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

# 真实产物（都在 .gitignore 里，无回滚副本）
PRODUCTS = (
    "model.pkl",
    "evaluation_report.txt",
    "confusion_matrix.png",
    "traffic_stats.csv",
    "traffic_data.csv",
    "sentinelnet.db",
)

_NEUTRALIZED: list[str] = []


def bootstrap() -> Path:
    """隔离 data_dir 到临时目录。**必须在 import campus_ids 之前调用。**"""
    tmp = Path(tempfile.mkdtemp(prefix="sn_record_"))
    os.environ["CAMPUS_IDS_DATA_DIR"] = str(tmp)
    os.environ.setdefault("CAMPUS_IDS_DEBUG", "1")
    # 收录集/日志同样挪走
    os.environ["CAMPUS_IDS_LOG_DIR"] = str(tmp / "logs")
    return tmp


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_products() -> dict[str, str]:
    """记录真实产物的 md5（文件不存在则记空串）。"""
    return {name: (_md5(ROOT / name) if (ROOT / name).exists() else "")
            for name in PRODUCTS}


def assert_products_untouched(before: dict[str, str]) -> None:
    """比对真实产物 md5；发现改写就抛错。"""
    changed = []
    for name in PRODUCTS:
        now = _md5(ROOT / name) if (ROOT / name).exists() else ""
        if now != before.get(name, ""):
            changed.append(name)
    if changed:
        raise RuntimeError(
            "安全底座失守：以下真实产物被改写 " + ", ".join(changed) +
            "。请检查是否漏掉了某个危险入口，产物需从备份恢复。"
        )


def _replace_by_identity(original, replacement, label: str) -> int:
    """在整个 campus_ids.* 命名空间里把所有 `is original` 的绑定换成 replacement。

    返回替换点数量。**不能只改源模块** —— 见模块 docstring 第 2 条。
    """
    count = 0
    for mod_name, mod in list(sys.modules.items()):
        if not mod_name.startswith("campus_ids"):
            continue
        for attr, value in list(vars(mod).items()):
            if value is original:
                setattr(mod, attr, replacement)
                _NEUTRALIZED.append(f"{mod_name}.{attr}")
                count += 1
    if count == 0:
        _NEUTRALIZED.append(f"(未找到绑定) {label}")
    return count


def _make_recorder_func(label: str, calls: list[str], result=None):
    def _stub(*args, **kwargs):
        calls.append(f"{label}({', '.join(map(repr, args))})")
        return result
    _stub.__name__ = f"stub_{label}"
    return _stub


def neutralize(dangerous_result_map: dict[str, object] | None = None) -> list[str]:
    """把旧应用的危险入口换成无副作用 stub。

    必须在 **import 旧应用之后**调用（需要模块已加载）。
    `dangerous_result_map` 为 `函数限定名 -> stub 的返回值`，
    例如 `{"start_capture_thread": True}` 让端点走到 success 分支，
    从而录到**有意义的成功响应**而不是错误分支。
    """
    results = dangerous_result_map or {}
    calls: list[str] = neutralize.calls  # type: ignore[attr-defined]
    calls.clear()

    targets = [
        # 会真训练（分钟级 + 覆盖三个产物）→ 拦截
        ("campus_ids.model.train", "train"),
        # 注意：`save_traffic_data` / `update_traffic_data` / `cleanup_old_data`
        # **故意不拦** —— 它们只写 data_dir 内的文件与库，而 data_dir 已被
        # bootstrap() 隔离到临时目录，所以既安全又能录到**真实**行为，
        # 不必牺牲 golden 保真度。
    ]

    for mod_name, attr in targets:
        mod = sys.modules.get(mod_name)
        if mod is None:
            try:
                mod = __import__(mod_name, fromlist=[attr])
            except ImportError:
                continue
        original = getattr(mod, attr, None)
        if original is None:
            continue
        label = f"{mod_name.split('.')[-1]}.{attr}"
        default = results.get(attr, True)
        stub = _make_recorder_func(label, calls, default)
        _replace_by_identity(original, stub, label)

    return _NEUTRALIZED


neutralize.calls = []  # type: ignore[attr-defined]


def neutralized_report() -> str:
    """返回被替换的绑定清单（用于把"安全事实"写进录制产物的元信息）。"""
    return "\n".join(f"  - {item}" for item in _NEUTRALIZED)


def get_csrf_token(client) -> str:
    """从旧应用首页的 <meta name="csrf-token"> 取出 CSRF token。

    没有它，所有 POST 都会被 Flask-WTF 拦成 400 —— 这正是旧 golden 不可用的原因。
    """
    import re

    html = client.get("/").get_data(as_text=True)
    match = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    if not match:
        raise RuntimeError("首页未渲染 csrf-token meta，无法录制有效的写端点样本")
    return match.group(1)
