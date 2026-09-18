"""runtime/events.py — 进程内事件总线（线程安全）。

取代 helpers.py 的 _on_alert_callbacks / _on_traffic_callbacks 两个列表。
同时承载 SSE 扇出（取代 sse.py 的 _sse_subscribers）。

本模块 import 不产生任何 I/O 副作用。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── 事件 topic 常量（唯一真相源）─────────────────────────────────
#
# ⚠️ 2026-09-17 修复的真实缺陷（T2.9 端到端验真时发现）：
# 这些常量此前**定义了却零使用**，生产端（services/）各自写字符串字面量，
# SSE 订阅端（web_new/api/stream.py）又维护了第三份 `VALID_TOPICS` 字面量，
# 三方各自漂移，结果是**订阅了也收不到**：
#
#   alert_service.py     发布 "alerts"          （复数）
#   SSE VALID_TOPICS     订阅 "alerts"          → 恰好能通
#   detection_service.py 发布 "traffic_update"
#   SSE VALID_TOPICS     订阅 "traffic"         → **永远收不到** ❌
#   EVENT_ALERT          = "alert"（单数）      → 与上面两处都不一致
#
# 且旧应用的对外契约（web/app.py `_broadcast_sse`）推的帧名是
# `alert`（单数）/ `traffic` —— 新实现改成 `alerts` / `traffic_update`
# 会让前端 `addEventListener('alert', ...)` 全部失效。
#
# 结论：**topic 名（订阅键）与 SSE 帧 `event:` 名统一**，取值与旧契约一致。
# 单层命名，不引入 topic→event 映射表（映射表会是第四个漂移点）。
# 防复发：`tests/test_sse_topics.py` 用 AST 扫描生产代码的 `publish()` 实参，
# 断言「有生产者的 topic 集合」== VALID_TOPICS，且无未声明 topic。
TOPIC_TRAFFIC = "traffic"          # 旧: _broadcast_sse("traffic", ...)
TOPIC_ALERT = "alert"              # 旧: _broadcast_sse("alert", ...)

# 可订阅 topic 集合 —— 仅包含**确有生产者**的 topic。
# 严禁直接写字符串字面量，一律引用上面的常量。
VALID_TOPICS: frozenset[str] = frozenset({TOPIC_TRAFFIC, TOPIC_ALERT})

# SSE 未指定 topics 时的默认订阅集合。
DEFAULT_TOPICS: tuple[str, ...] = (TOPIC_TRAFFIC, TOPIC_ALERT)

# 预留 topic —— **尚无生产者**，因此不在 VALID_TOPICS 内。
# `?topics=` 传这些值会得到 400（而不是静默回落到默认订阅），
# 避免"以为订阅了、其实收的是别的"这类静默失败。
# 接上生产者时：把常量移入 VALID_TOPICS，并同步 `tests/test_sse_topics.py`。
PLANNED_TOPIC_TASKS = "tasks"          # 预留：TaskRegistry 尚未发布状态变更事件
PLANNED_TOPIC_DETECTION = "detection"  # 预留：检测结果尚未单独立流（现走 traffic）
PLANNED_TOPICS: frozenset[str] = frozenset({PLANNED_TOPIC_TASKS, PLANNED_TOPIC_DETECTION})

# 订阅者上限（与原 sse.py MAX_SSE_SUBSCRIBERS = 20 一致）
MAX_SUBSCRIBERS = 20


class EventBus:
    """线程安全进程内事件总线。

    支持两类订阅者：
    - 同步回调（Callable）：取代原 _on_alert_callbacks / _on_traffic_callbacks
    - 异步队列（asyncio.Queue）：SSE 端点通过此队列接收事件

    线程安全：所有写操作由内部锁保护。
    跨线程投递：生产者（抓包/检测线程）通过 loop.call_soon_threadsafe 投递到 asyncio.Queue。
    """

    def __init__(self, max_subscribers: int = MAX_SUBSCRIBERS) -> None:
        self._lock = threading.Lock()
        self._sync_subs: dict[str, list[Callable]] = defaultdict(list)
        self._async_subs: dict[str, list[tuple[asyncio.Queue, asyncio.AbstractEventLoop]]] = defaultdict(list)
        self._max_subscribers = max_subscribers

    # ── 同步回调订阅 ──────────────────────────────────────────────

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """注册同步回调。"""
        with self._lock:
            self._sync_subs[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """移除同步回调。"""
        with self._lock:
            try:
                self._sync_subs[event_type].remove(callback)
            except ValueError:
                pass

    # ── 异步队列订阅（SSE）────────────────────────────────────────

    def subscribe_async(
        self, event_type: str, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop
    ) -> bool:
        """注册异步队列订阅（SSE 端点用）。

        Returns:
            True 注册成功，False 已达订阅者上限。
        """
        with self._lock:
            total = sum(len(qs) for qs in self._async_subs.values())
            if total >= self._max_subscribers:
                logger.warning("事件总线订阅者已达上限 %d，拒绝新订阅", self._max_subscribers)
                return False
            self._async_subs[event_type].append((queue, loop))
            return True

    def unsubscribe_async(self, event_type: str, queue: asyncio.Queue) -> None:
        """移除异步队列订阅。"""
        with self._lock:
            self._async_subs[event_type] = [
                (q, l) for q, l in self._async_subs[event_type] if q is not queue
            ]

    # ── 事件发布 ──────────────────────────────────────────────────

    def publish(self, event_type: str, data: Any = None) -> None:
        """发布事件（从任意线程调用）。

        同步回调在当前线程直接调用；
        异步队列通过 loop.call_soon_threadsafe 投递。
        """
        with self._lock:
            sync_callbacks = list(self._sync_subs.get(event_type, []))
            async_queues = list(self._async_subs.get(event_type, []))

        # 同步回调
        for cb in sync_callbacks:
            try:
                cb(event_type, data)
            except Exception:
                logger.exception("事件回调异常: %s", event_type)

        # 异步队列（线程安全投递）
        msg = {"event": event_type, "data": data}
        for queue, loop in async_queues:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, msg)
            except Exception:
                logger.exception("异步事件投递异常: %s", event_type)

    # ── 查询 ──────────────────────────────────────────────────────

    def subscriber_count(self, event_type: str | None = None) -> int:
        """返回订阅者数量。"""
        with self._lock:
            if event_type:
                return len(self._sync_subs.get(event_type, [])) + len(self._async_subs.get(event_type, []))
            return sum(len(qs) for qs in self._sync_subs.values()) + sum(
                len(qs) for qs in self._async_subs.values()
            )