"""T1.5 事件总线 — 验证 EventBus 同步/异步双模式、订阅者上限、线程安全。

核心验收：
- 同步回调 subscribe/unsubscribe
- 异步队列 subscribe_async/unsubscribe_async
- publish 投递到同步回调和异步队列
- 订阅者上限 MAX_SUBSCRIBERS=20
- subscriber_count 查询
- 跨线程投递 loop.call_soon_threadsafe
"""
from __future__ import annotations

import asyncio
import threading


from campus_ids.runtime.events import (
    PLANNED_TOPIC_DETECTION,
    PLANNED_TOPIC_TASKS,
    TOPIC_ALERT,
    TOPIC_TRAFFIC,
    EventBus,
    MAX_SUBSCRIBERS,
    VALID_TOPICS,
)


# ── 事件类型常量 ──────────────────────────────────────────────────


class TestEventConstants:
    """验证事件类型常量。

    topic 名与 SSE 帧 `event:` 名统一，取值与旧应用
    `_broadcast_sse("alert"|"traffic", ...)` 的对外契约一致。
    本类只断言"常量属于合法 topic 集合"，漂移检测由 `tests/test_sse_topics.py` 负责。
    """

    def test_event_types_defined(self):
        assert TOPIC_TRAFFIC == "traffic"
        assert TOPIC_ALERT == "alert"
        # 以下两个是**预留** topic（暂无生产者），不得出现在可订阅集合里
        assert PLANNED_TOPIC_TASKS == "tasks"
        assert PLANNED_TOPIC_DETECTION == "detection"
        assert VALID_TOPICS == frozenset({TOPIC_TRAFFIC, TOPIC_ALERT})
        assert PLANNED_TOPIC_TASKS not in VALID_TOPICS
        assert PLANNED_TOPIC_DETECTION not in VALID_TOPICS

    def test_max_subscribers(self):
        assert MAX_SUBSCRIBERS == 20


# ── 同步回调 ──────────────────────────────────────────────────────


class TestSyncCallbacks:
    """验证同步回调订阅/取消/发布。"""

    def test_subscribe_and_publish(self):
        bus = EventBus()
        received = []
        bus.subscribe(TOPIC_ALERT, lambda et, data: received.append((et, data)))
        bus.publish(TOPIC_ALERT, {"level": "high"})
        assert len(received) == 1
        assert received[0] == (TOPIC_ALERT, {"level": "high"})

    def test_multiple_sync_subscribers(self):
        bus = EventBus()
        received_a = []
        received_b = []
        bus.subscribe(TOPIC_ALERT, lambda et, data: received_a.append(data))
        bus.subscribe(TOPIC_ALERT, lambda et, data: received_b.append(data))
        bus.publish(TOPIC_ALERT, "test")
        assert len(received_a) == 1
        assert len(received_b) == 1

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        cb = lambda et, data: received.append(data)  # noqa: E731
        bus.subscribe(TOPIC_ALERT, cb)
        bus.unsubscribe(TOPIC_ALERT, cb)
        bus.publish(TOPIC_ALERT, "test")
        assert len(received) == 0

    def test_unsubscribe_nonexistent_no_error(self):
        """取消不存在的回调不报错。"""
        bus = EventBus()
        bus.unsubscribe(TOPIC_ALERT, lambda et, data: None)

    def test_different_event_types_isolated(self):
        """不同事件类型互不干扰。"""
        bus = EventBus()
        alerts = []
        traffic = []
        bus.subscribe(TOPIC_ALERT, lambda et, data: alerts.append(data))
        bus.subscribe(TOPIC_TRAFFIC, lambda et, data: traffic.append(data))
        bus.publish(TOPIC_ALERT, "alert_data")
        assert len(alerts) == 1
        assert len(traffic) == 0

    def test_sync_callback_exception_does_not_break_publish(self):
        """同步回调异常不影响其他回调。"""
        bus = EventBus()
        received = []

        def bad_callback(et, data):
            raise RuntimeError("回调异常")

        bus.subscribe(TOPIC_ALERT, bad_callback)
        bus.subscribe(TOPIC_ALERT, lambda et, data: received.append(data))
        bus.publish(TOPIC_ALERT, "test")
        assert len(received) == 1  # 第二个回调仍被调用


# ── 异步队列 ──────────────────────────────────────────────────────


class TestAsyncQueues:
    """验证异步队列订阅/取消/发布。"""

    def test_subscribe_async_and_publish(self):
        bus = EventBus()
        loop = asyncio.new_event_loop()
        queue = asyncio.Queue()
        result = bus.subscribe_async(TOPIC_ALERT, queue, loop)
        assert result is True

        bus.publish(TOPIC_ALERT, {"level": "high"})

        # loop.call_soon_threadsafe 投递的回调需运行事件循环才能执行
        loop.run_until_complete(asyncio.sleep(0.05))

        # 检查队列
        assert not queue.empty()
        msg = queue.get_nowait()
        assert msg["event"] == TOPIC_ALERT
        assert msg["data"] == {"level": "high"}
        loop.close()

    def test_unsubscribe_async(self):
        bus = EventBus()
        loop = asyncio.new_event_loop()
        queue = asyncio.Queue()
        bus.subscribe_async(TOPIC_ALERT, queue, loop)
        bus.unsubscribe_async(TOPIC_ALERT, queue)
        bus.publish(TOPIC_ALERT, "test")
        loop.run_until_complete(asyncio.sleep(0.05))
        assert queue.empty()
        loop.close()


# ── 订阅者上限 ────────────────────────────────────────────────────


class TestMaxSubscribers:
    """验证订阅者上限。"""

    def test_subscribe_async_rejects_at_limit(self):
        bus = EventBus(max_subscribers=3)
        loop = asyncio.new_event_loop()
        queues = [asyncio.Queue() for _ in range(3)]
        for q in queues:
            assert bus.subscribe_async(TOPIC_ALERT, q, loop) is True
        # 第4个应被拒绝
        q4 = asyncio.Queue()
        assert bus.subscribe_async(TOPIC_ALERT, q4, loop) is False
        loop.close()


# ── subscriber_count ──────────────────────────────────────────────


class TestSubscriberCount:
    """验证订阅者计数。"""

    def test_count_empty(self):
        bus = EventBus()
        assert bus.subscriber_count() == 0
        assert bus.subscriber_count(TOPIC_ALERT) == 0

    def test_count_sync_only(self):
        bus = EventBus()
        bus.subscribe(TOPIC_ALERT, lambda et, data: None)
        bus.subscribe(TOPIC_ALERT, lambda et, data: None)
        assert bus.subscriber_count(TOPIC_ALERT) == 2
        assert bus.subscriber_count() == 2

    def test_count_mixed(self):
        bus = EventBus()
        loop = asyncio.new_event_loop()
        bus.subscribe(TOPIC_ALERT, lambda et, data: None)
        bus.subscribe_async(TOPIC_ALERT, asyncio.Queue(), loop)
        assert bus.subscriber_count(TOPIC_ALERT) == 2
        loop.close()

    def test_count_by_event_type(self):
        bus = EventBus()
        bus.subscribe(TOPIC_ALERT, lambda et, data: None)
        bus.subscribe(TOPIC_TRAFFIC, lambda et, data: None)
        assert bus.subscriber_count(TOPIC_ALERT) == 1
        assert bus.subscriber_count(TOPIC_TRAFFIC) == 1
        assert bus.subscriber_count() == 2


# ── 线程安全 ──────────────────────────────────────────────────────


class TestThreadSafety:
    """验证多线程并发发布/订阅不崩溃。"""

    def test_concurrent_publish(self):
        bus = EventBus()
        received = []
        lock = threading.Lock()

        def cb(et, data):
            with lock:
                received.append(data)

        bus.subscribe(TOPIC_ALERT, cb)

        def publisher(n):
            for i in range(50):
                bus.publish(TOPIC_ALERT, n * 100 + i)

        threads = [threading.Thread(target=publisher, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(received) == 200  # 4 threads × 50