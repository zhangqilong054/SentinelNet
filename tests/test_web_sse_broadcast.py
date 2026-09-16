"""SSE 广播基础设施测试 — _broadcast_sse 与订阅者管理。"""
import queue
import pytest

from campus_ids.web.sse import _broadcast_sse, _sse_subscribers, _sse_lock, MAX_SSE_SUBSCRIBERS


@pytest.fixture(autouse=True)
def clean_subscribers():
    """每个测试前清空订阅者列表。"""
    with _sse_lock:
        _sse_subscribers.clear()
    yield
    with _sse_lock:
        _sse_subscribers.clear()


class TestBroadcastSSE:
    def test_broadcast_to_single_subscriber(self):
        q = queue.Queue()
        with _sse_lock:
            _sse_subscribers.append(q)
        _broadcast_sse("test", {"key": "value"})
        msg = q.get_nowait()
        assert "event: test" in msg
        assert '"key": "value"' in msg

    def test_broadcast_to_multiple_subscribers(self):
        queues = [queue.Queue() for _ in range(3)]
        with _sse_lock:
            _sse_subscribers.extend(queues)
        _broadcast_sse("alert", {"type": "ddos"})
        for q in queues:
            msg = q.get_nowait()
            assert "event: alert" in msg

    def test_broadcast_removes_full_queue(self):
        """队列满时，_broadcast_sse 移除该订阅者。"""
        q = queue.Queue(maxsize=1)
        q.put("old")  # 填满队列
        with _sse_lock:
            _sse_subscribers.append(q)
        _broadcast_sse("test", {"key": "value"})
        # 队列应被移除
        with _sse_lock:
            assert len(_sse_subscribers) == 0

    def test_broadcast_no_subscribers(self):
        """无订阅者时不报错。"""
        _broadcast_sse("test", {"key": "value"})  # 不应抛异常

    def test_message_format(self):
        q = queue.Queue()
        with _sse_lock:
            _sse_subscribers.append(q)
        _broadcast_sse("traffic", {"qps": 100})
        msg = q.get_nowait()
        assert msg.startswith("event: traffic\n")
        assert msg.endswith("\n\n")
        assert "data:" in msg

    def test_max_subscribers_constant(self):
        assert MAX_SSE_SUBSCRIBERS == 20