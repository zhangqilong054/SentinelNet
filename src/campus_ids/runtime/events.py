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

# 事件类型常量
EVENT_TRAFFIC_UPDATE = "traffic_update"
EVENT_ALERT = "alert"
EVENT_TASK_STATUS = "task_status"
EVENT_DETECTION = "detection"

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