"""T1.6 运行时句柄容器 — 验证 RuntimeState 无模块级裸全局。

核心验收：
- 所有可变运行时状态只存在于 RuntimeState 容器内
- event_bus 为 EventBus 实例
- packet_queue 为 Queue(maxsize=20000)
"""
from __future__ import annotations

import queue

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

    def test_event_bus_initially_none(self):
        """RuntimeState 不预建 EventBus，由 lifespan 注入。"""
        state = RuntimeState()
        assert state.event_bus is None

    def test_event_bus_can_be_set(self):
        """lifespan 可注入 EventBus 实例。"""
        state = RuntimeState()
        state.event_bus = EventBus()
        assert isinstance(state.event_bus, EventBus)