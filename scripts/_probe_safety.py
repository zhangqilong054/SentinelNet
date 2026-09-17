# -*- coding: utf-8 -*-
"""探针安全底座 —— 保证验收探针永远不会误触真实产物。

**为什么需要它**（2026-09-17 教训）：

T1 阶段提交的 `scripts/probe_t1_acceptance.py` / `probe_t2_acceptance.py` 在
"任务未接线"时是无害的 —— 所有 `POST /api/tasks/*/start` 都返回 503，什么都不会发生。
T2 阶段 `_register_default_tasks()` 接上真实 target 之后，**同样的脚本立刻变成破坏性的**：
`POST /api/tasks/train/start` 会真的触发 `train()`，覆盖
`model.pkl` / `evaluation_report.txt` / `confusion_matrix.png`；
`POST /api/scenarios/start {"scenario":"full"}` 同样会走到训练。
而这三个产物**都在 `.gitignore` 里，没有可回滚副本**。

**本模块提供的三道保险**：

1. `bootstrap()` —— 把 `CAMPUS_IDS_DATA_DIR` 指向临时目录。
   必须在 import 任何 `campus_ids` 模块**之前**调用：`config.py` 在 import 期读环境变量
   并把 `DATA_DIR / MODEL_PATH / TRAFFIC_CSV / TRAFFIC_STATS_CSV / EVALUATION_PATH`
   算成模块级常量，之后再改环境变量无效。
   （注意：`web/database.py` 的 `DB_PATH`、`web/helpers.py` 的 `TRAFFIC_STATS_CSV` 等都是
   `from campus_ids.config import X` 的**按值绑定**，所以只有在 import 之前设好 env 才拦得住。
   见 `_probe_isolation.py` 的实测：晚设 12/12 泄漏。）

2. `install_service_stubs()` —— 把 lifespan 里构造的业务 service 换成记录型 stub。
   这样 `target is not None`（接线事实）依然成立，但不会真的抓包 / 训练 / 写产物。
   只替换**类**，不替换 target —— 避免"注入假 target 掩盖未接线"的老问题。
   想验证接线，用 `assert_targets_wired()`（只读断言 `target is not None`）。

3. **不留痕** —— `bootstrap()` 在 `atexit` 里 `rmtree` 临时目录；
   探针自己的临时文件（如对照用的 SQLite 库）**一律放进该临时目录，不放进项目根**。
   2026-09-17 教训：对照库曾建在 `ROOT / "_probe_cleanup.db"`，末尾 `unlink()` 被宿主的
   批量删除保护拦下 → 工作区残留，且污染下一轮 `git status` 检查。

用法：

    from _probe_safety import bootstrap, install_service_stubs, assert_targets_wired, verdict
    TMP = bootstrap()                     # ← 第一件事
    from campus_ids.web_new.app import create_app   # ← 之后才能 import 项目模块
    rec = install_service_stubs()
    ...
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

_RESULTS: list[tuple[str, bool, str]] = []


def bootstrap() -> Path:
    """把 data_dir 隔离到临时目录，并允许开发用默认 secret_key。

    **必须在 import 任何 campus_ids 模块之前调用。**
    返回临时目录路径。

    临时目录在进程退出时**尽力删除**（`atexit` + `ignore_errors=True`）。
    2026-09-17 补：此前只 `mkdtemp` 不清理，反复跑探针会在 `%TEMP%` 下堆积
    `sn_probe_*` 目录；且 Windows 下 SQLite 连接未释放时删除会失败，
    故删除失败不算错误 —— 探针的契约是"不污染工作区"，而不是"保证回收临时目录"。
    """
    tmp = Path(tempfile.mkdtemp(prefix="sn_probe_"))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    os.environ["CAMPUS_IDS_DATA_DIR"] = str(tmp)
    os.environ.setdefault("CAMPUS_IDS_DEBUG", "1")
    return tmp


def verdict(name: str, ok: bool, detail: str = "") -> bool:
    """登记一条判定，按实测结果打印 ✅/🔴（结论由测量算出，不写死在文案里）。"""
    _RESULTS.append((name, bool(ok), detail))
    mark = "✅" if ok else "🔴"
    print(f"  {mark} {name}" + (f"  — {detail}" if detail else ""))
    return bool(ok)


def summary() -> int:
    """打印汇总，返回失败条数（可作为 exit code）。"""
    failed = [r for r in _RESULTS if not r[1]]
    print()
    print("=" * 78)
    print(f"探针汇总：{len(_RESULTS) - len(failed)} 通过 / {len(failed)} 未通过")
    for name, _ok, detail in failed:
        print(f"  🔴 {name}" + (f"  — {detail}" if detail else ""))
    print("=" * 78)
    return len(failed)


def assert_targets_wired(app) -> list[str]:
    """只读断言：`app.state.task_registry` 里每个 task 的 `target is not None`。

    这是判断"编排域是否真接线"的唯一可靠方式 —— 不要靠 POST 返回码，
    因为服务层可能返回 200 却什么都没做；也不要靠单测，因为单测常自己注入假 target。
    返回未接线的任务名列表（空列表 = 全部已接线）。
    """
    registry = app.state.task_registry
    unwired = []
    for name in sorted(registry.registered_names):
        task = registry._tasks.get(name)
        target = getattr(task, "target", None)
        if target is None:
            unwired.append(name)
    return unwired


class Recorder:
    """记录型 stub 收到的调用，便于断言"服务是否真被调用"。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def add(self, what: str) -> None:
        self.calls.append(what)

    def clear(self) -> None:
        self.calls.clear()

    def has(self, fragment: str) -> bool:
        return any(fragment in c for c in self.calls)


def install_service_stubs(recorder: Recorder | None = None) -> Recorder:
    """把 lifespan 会用到的业务 service 换成 stub（无副作用）。

    必须在本模块 import 之后、`create_app()` 调用**之前**执行。
    故意**不**替换 `task_registry` 的 target —— 接线事实要保持真实。
    """
    rec = recorder or Recorder()

    class _Capture:
        def __init__(self, **kw): rec.add("CaptureService()")
        def start_capture(self): rec.add("capture.start")
        def stop_capture(self): rec.add("capture.stop")
        def start_enhanced(self, duration=60, **kw): rec.add(f"capture.start_enhanced({duration})")
        def stop_enhanced(self): rec.add("capture.stop_enhanced")

    class _Detection:
        def __init__(self, **kw): rec.add("DetectionService()")
        def start_detection(self): rec.add("detection.start")
        def stop_detection(self): rec.add("detection.stop")
        def load_ml(self): rec.add("detection.load_ml")
        def stop_ml(self): rec.add("detection.stop_ml")

    class _Model:
        def __init__(self, **kw): rec.add("ModelService()")
        def train(self, **kw): rec.add("model.train(STUB)")

    class _Attack:
        def __init__(self, **kw): rec.add("AttackSimulator()")
        def start_all(self, duration=30, **kw): rec.add(f"attack.start_all({duration})")
        def stop(self): rec.add("attack.stop")

    import campus_ids.demo.attack_sim as _as
    import campus_ids.services.capture_service as _cs
    import campus_ids.services.detection_service as _ds
    import campus_ids.services.model_service as _ms

    _cs.CaptureService = _Capture
    _ds.DetectionService = _Detection
    _ms.ModelService = _Model
    _as.AttackSimulator = _Attack
    return rec


def guard_no_real_training() -> None:
    """兜底：把 `model/train.py` 的 `train` 换成直接抛异常的哨兵。

    万一漏了某个入口真的走到训练，宁可让探针失败也不要覆盖用户产物。
    """
    import campus_ids.model.train as _train

    def _blocked(*a, **kw):
        raise RuntimeError("探针拦截：train() 被调用 —— 这会覆盖 model.pkl 等产物")

    _train.train = _blocked

    # helpers._auto_worker 是函数内 import，同样拦
    import campus_ids.web.helpers as _helpers
    for attr in ("train",):
        if hasattr(_helpers, attr):
            setattr(_helpers, attr, _blocked)
