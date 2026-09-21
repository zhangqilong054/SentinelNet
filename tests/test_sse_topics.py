# -*- coding: utf-8 -*-
"""SSE topic 契约一致性测试 —— 防「订阅了也收不到」复发。

## 为什么需要这个文件

2026-09-17 用真实 uvicorn 做 T2.9 端到端验真时发现：SSE 端点**声称**支持 4 个
topic，实际只有 2 个有生产者，其中 1 个名字还是错的：

| 生产者 | 发布的名字 | SSE 订阅的名字 | 结果 |
|---|---|---|---|
| `alert_service.py` | `"alerts"` | `"alerts"` | 通，但帧名 `alerts` ≠ 旧契约 `alert` |
| `detection_service.py` | `"traffic_update"` | `"traffic"` | **订阅者永远收不到** |
| —— | 无 | `"tasks"` | 死 topic |
| —— | 无 | `"detection"` | 死 topic |

根因：`runtime/events.py` 的 `EVENT_*` 常量**定义了却零使用**，生产端各自写字符串
字面量，SSE 端又维护了第三份 `VALID_TOPICS` 字面量。而
`tests/test_event_bus.py` 全绿 —— 它订阅和发布用的是同一个常量，**自洽**。
这是本项目「单测全绿 ≠ 功能成立」的第 6 次。

## 本文件如何防复发（全部为静态检查，不需要起服务）

1. 生产代码里 `.publish()` 的第一个实参**必须是常量引用**，禁止字符串字面量；
2. 「生产代码确有生产者的 topic 集合」必须**恰好等于** `VALID_TOPICS`；
3. `PLANNED_TOPICS` 必须**没有任何生产者**（否则就该提升进 `VALID_TOPICS`）；
4. `VALID_TOPICS` 必须与**旧应用 `_broadcast_sse` 的帧名契约**一致。

第 4 条是关键：期望值从**旧实现源码**里解析出来，而不是在新代码里再抄一遍 ——
否则又是"两份真相"。
"""
from __future__ import annotations

import ast
from pathlib import Path

from campus_ids.runtime import events as events_mod
from campus_ids.web.api import stream as stream_mod

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "campus_ids"

# SSE 帧名契约 —— 前端 EventSource 监听的关键字
# 旧应用 web/app.py 的 _broadcast_sse("alert", ...) / _broadcast_sse("traffic", ...)
# 已在 T5 阶段删除，此处硬编码契约值作为前端兼容性断言
LEGACY_SSE_FRAME_NAMES: frozenset[str] = frozenset({"alert", "traffic"})

# 常量必须能从 events 模块解析到；解析不到就是"引用了不存在的常量"
_UNRESOLVED = object()


def _module_constants() -> dict[str, object]:
    """`events` 模块里所有 `TOPIC_*` / `EVENT_*` 常量名 → 值。"""
    ns: dict[str, object] = {}
    for name in dir(events_mod):
        if name.startswith(("TOPIC_", "EVENT_")):
            ns[name] = getattr(events_mod, name)
    return ns


def _iter_publish_calls() -> list[tuple[Path, int, ast.Call]]:
    """扫描 `src/campus_ids/` 下所有 `.publish(...)` 调用点。"""
    found: list[tuple[Path, int, ast.Call]] = []
    for py in sorted(SRC_ROOT.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:  # pragma: no cover - 语法错会由别的测试抓
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "publish"
                and node.args
            ):
                found.append((py, node.lineno, node))
    return found


def _resolve_arg(node: ast.expr, consts: dict[str, object]) -> object:
    """把 `publish()` 的第一个实参解析成常量值。"""
    if isinstance(node, ast.Name):
        return consts.get(node.id, _UNRESOLVED)
    if isinstance(node, ast.Attribute):
        # `events.TOPIC_ALERT` 形式
        return consts.get(node.attr, _UNRESOLVED)
    return _UNRESOLVED


def test_publish_args_are_constants_not_literals() -> None:
    """生产代码发布事件时**禁止写字符串字面量** —— 一律引用 `TOPIC_*` 常量。"""
    consts = _module_constants()
    offenders: list[str] = []
    for path, lineno, node in _iter_publish_calls():
        arg = node.args[0]
        if isinstance(arg, ast.Constant):
            offenders.append(f"{path.relative_to(SRC_ROOT.parent.parent)}:{lineno} 用了字面量 {arg.value!r}")
        elif _resolve_arg(arg, consts) is _UNRESOLVED:
            offenders.append(f"{path.relative_to(SRC_ROOT.parent.parent)}:{lineno} 引用了未声明的常量")
    assert not offenders, (
        "`publish()` 的第一个实参必须是 campus_ids.runtime.events 里声明的常量：\n  "
        + "\n  ".join(offenders)
    )


def test_produced_topics_exactly_match_valid_topics() -> None:
    """「确有生产者的 topic 集合」必须恰好等于 `VALID_TOPICS`。

    少 → 声明了却永远无事件（死 topic，调用方静默收不到东西）；
    多 → 发布了没人能订阅的 topic（订阅参数会被 400 拒绝）。
    """
    consts = _module_constants()
    produced: set[str] = set()
    for _path, _lineno, node in _iter_publish_calls():
        value = _resolve_arg(node.args[0], consts)
        assert isinstance(value, str), f"常量解析失败：{ast.dump(node.args[0])}"
        produced.add(value)

    assert produced == set(events_mod.VALID_TOPICS), (
        f"生产者发布的 topic = {sorted(produced)}，"
        f"VALID_TOPICS = {sorted(events_mod.VALID_TOPICS)}；"
        "多则应为 400 拒绝，少则调用方永远收不到事件"
    )


def test_planned_topics_have_no_producer() -> None:
    """预留 topic 不得有生产者。

    一旦有人开始发布预留 topic，这条会失败 —— 提示把它提升进 `VALID_TOPICS`，
    而不是让它以"发布了但订阅不了"的状态长期存在。
    """
    consts = _module_constants()
    produced = {_resolve_arg(n.args[0], consts) for _p, _l, n in _iter_publish_calls()}
    leaked = produced & set(events_mod.PLANNED_TOPICS)
    assert not leaked, (
        f"预留 topic {sorted(leaked)} 已有生产者，应移入 VALID_TOPICS 并更新本文件"
    )


def test_valid_and_planned_are_disjoint() -> None:
    assert not (set(events_mod.VALID_TOPICS) & set(events_mod.PLANNED_TOPICS))


def test_default_topics_are_valid() -> None:
    assert set(events_mod.DEFAULT_TOPICS) <= set(events_mod.VALID_TOPICS)


def test_topics_match_legacy_broadcast_contract() -> None:
    """新应用可订阅的 topic 必须与旧应用的 SSE 帧名契约一致。

    旧应用 `web/app.py` 的 `_broadcast_sse("alert"|\"traffic\", ...)` 帧名是前端
    `EventSource.addEventListener('alert', ...)` 的监听键，改名即破坏前端。
    旧应用已在 T5 阶段删除，契约值硬编码在 `LEGACY_SSE_FRAME_NAMES` 中。
    """
    assert set(events_mod.VALID_TOPICS) == LEGACY_SSE_FRAME_NAMES, (
        f"新应用 topic={sorted(events_mod.VALID_TOPICS)} 与旧应用帧名契约="
        f"{sorted(LEGACY_SSE_FRAME_NAMES)} 不一致；帧名是前端 EventSource 的监听键，改名即破坏前端"
    )


def test_stream_router_reuses_shared_topic_set() -> None:
    """`stream.py` 不得自持 topic 字面量 —— 必须是同一个对象。"""
    assert stream_mod.VALID_TOPICS is events_mod.VALID_TOPICS
    assert stream_mod._DEFAULT_TOPICS_STR == ",".join(events_mod.DEFAULT_TOPICS)


def test_stream_router_has_no_hardcoded_topic_literals() -> None:
    """`stream.py` 源码里不得再出现 topic 字符串字面量（防第三份真相回流）。"""
    src = (SRC_ROOT / "web" / "api" / "stream.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    literals = set()
    for node in ast.walk(tree):
        # 只看会被当作查询默认值/订阅键的字符串常量；docstring 不计入
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
    for topic in events_mod.VALID_TOPICS | events_mod.PLANNED_TOPICS:
        assert topic not in literals, (
            f"stream.py 里出现了 topic 字面量 {topic!r} —— "
            "应改为引用 campus_ids.runtime.events 的常量"
        )


# ⚠️ 本文件有意**不**用 TestClient 请求 /api/stream：
# TestClient 走 Starlette 的同步 transport，对无限 SSE 流会**静默挂死**
# （连 `connected` 帧都读不到，且不报错），会把整个测试会话拖死。
# SSE 的真实 HTTP 行为一律由 `tests/test_sse_live.py` 用真实 uvicorn 验证。

