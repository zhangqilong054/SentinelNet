"""T1.6 运行时句柄容器 — 验证 RuntimeState 无模块级裸全局。

核心验收：
- 所有可变运行时状态只存在于 RuntimeState 容器内
- get_state/set_state 线程安全
- is_any_task_running 正确聚合
- reset 重置所有状态
- event_bus 为 EventBus 实例
- packet_queue 为 Queue(maxsize=20000)
"""
from __future__ import annotations

import queue
import threading

import pytest

from campus_ids.runtime.events import EventBus
from campus_ids.runtime.state import RuntimeState


# ── 初始化默认值 ──────────────────────────────────────────────────


class TestDefaults:
    """验证 RuntimeState 初始默认值。"""

    def test_capture_defaults(self):
        state = RuntimeState()
        assert state.capture_running is False
        assert state.capture_thread is None

    def test_enhanced_capture_defaults(self):
        state = RuntimeState()
        assert state.enhanced_capture_running is False
        assert state.enhanced_capture_thread is None
        assert state.enhanced_capture_result == {}

    def test_detection_defaults(self):
        state = RuntimeState()
        assert state.detection_running is False
        assert state.detection_thread is None

    def test_ml_defaults(self):
        state = RuntimeState()
        assert state.ml_running is False

    def test_attack_defaults(self):
        state = RuntimeState()
        assert state.attack_running is False
        assert state.attack_thread is None

    def test_train_defaults(self):
        state = RuntimeState()
        assert state.train_running is False
        assert state.train_thread is None
        assert state.train_progress == 0.0
        assert state.train_status == "idle"

    def test_auto_defaults(self):
        state = RuntimeState()
        assert state.auto_running is False
        assert state.auto_status == "idle"
        assert state.auto_progress == 0.0

    def test_traffic_defaults(self):
        state = RuntimeState()
        assert state.traffic_data == {}
        assert state.recent_packets == []
        assert state.last_update_time == 0.0

    def test_dropped_packets_default(self):
        state = RuntimeState()
        assert state.dropped_packets == 0


# ── packet_queue ──────────────────────────────────────────────────


class TestPacketQueue:
    """验证 packet_queue 属性。"""

    def test_queue_type(self):
        state = RuntimeState()
        assert isinstance(state.packet_queue, queue.Queue)

    def test_queue_maxsize(self):
        state = RuntimeState()
        assert state.packet_queue.maxsize == 20000


# ── event_bus ──────────────────────────────────────────────────────


class TestEventBus:
    """验证 event_bus 属性。"""

    def test_event_bus_type(self):
        state = RuntimeState()
        assert isinstance(state.event_bus, EventBus)


# ── get_state / set_state ──────────────────────────────────────────


class TestStateAccess:
    """验证线程安全状态读写。"""

    def test_get_state_existing(self):
        state = RuntimeState()
        assert state.get_state("capture_running") is False

    def test_get_state_with_default(self):
        state = RuntimeState()
        assert state.get_state("nonexistent", "default") == "default"

    def test_set_state(self):
        state = RuntimeState()
        state.set_state("capture_running", True)
        assert state.capture_running is True
        assert state.get_state("capture_running") is True

    def test_set_state_new_attribute(self):
        state = RuntimeState()
        state.set_state("custom_flag", 42)
        assert state.get_state("custom_flag") == 42


# ── is_any_task_running ────────────────────────────────────────────


class TestIsAnyTaskRunning:
    """验证 is_any_task_running 聚合逻辑。"""

    def test_none_running(self):
        state = RuntimeState()
        assert state.is_any_task_running() is False

    def test_capture_running(self):
        state = RuntimeState()
        state.capture_running = True
        assert state.is_any_task_running() is True

    def test_detection_running(self):
        state = RuntimeState()
        state.detection_running = True
        assert state.is_any_task_running() is True

    def test_ml_running(self):
        state = RuntimeState()
        state.ml_running = True
        assert state.is_any_task_running() is True

    def test_attack_running(self):
        state = RuntimeState()
        state.attack_running = True
        assert state.is_any_task_running() is True

    def test_train_running(self):
        state = RuntimeState()
        state.train_running = True
        assert state.is_any_task_running() is True

    def test_auto_running(self):
        state = RuntimeState()
        state.auto_running = True
        assert state.is_any_task_running() is True

    def test_enhanced_capture_running(self):
        state = RuntimeState()
        state.enhanced_capture_running = True
        assert state.is_any_task_running() is True


# ── reset ──────────────────────────────────────────────────────────


class TestReset:
    """验证 reset 重置所有运行时状态。"""

    def test_reset_all_flags(self):
        state = RuntimeState()
        state.capture_running = True
        state.detection_running = True
        state.ml_running = True
        state.attack_running = True
        state.train_running = True
        state.auto_running = True
        state.enhanced_capture_running = True
        state.dropped_packets = 100
        state.train_progress = 0.5
        state.train_status = "running"
        state.auto_status = "running"
        state.auto_progress = 0.3

        state.reset()

        assert state.capture_running is False
        assert state.detection_running is False
        assert state.ml_running is False
        assert state.attack_running is False
        assert state.train_running is False
        assert state.auto_running is False
        assert state.enhanced_capture_running is False
        assert state.dropped_packets == 0
        assert state.train_progress == 0.0
        assert state.train_status == "idle"
        assert state.auto_status == "idle"
        assert state.auto_progress == 0.0

    def test_reset_is_any_task_running(self):
        state = RuntimeState()
        state.capture_running = True
        state.reset()
        assert state.is_any_task_running() is False


# ── 线程安全 ──────────────────────────────────────────────────────


class TestThreadSafety:
    """验证多线程并发访问不崩溃。"""

    def test_concurrent_set_state(self):
        state = RuntimeState()
        errors = []

        def writer(n):
            try:
                for i in range(100):
                    state.set_state("dropped_packets", n * 1000 + i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0