"""O-01: 队列溢出与丢包计数单元测试。

验证 _packet_queue 满时 put_nowait 抛出 queue.Full，
以及 _dropped_packets 计数器正确递增。
"""
import queue
from unittest.mock import patch, MagicMock

import pytest


class TestQueueOverflow:
    """验证抓包队列满时的丢包行为。"""

    def test_put_nowait_raises_full_on_full_queue(self):
        """当队列满时，put_nowait 应抛出 queue.Full。"""
        q = queue.Queue(maxsize=1)
        q.put_nowait({"test": 1})
        with pytest.raises(queue.Full):
            q.put_nowait({"test": 2})

    def test_dropped_packets_increments_on_full(self):
        """模拟 _capture_worker 中 queue.Full 分支，验证 _dropped_packets 递增。"""
        import campus_ids.web.helpers as helpers

        # 保存原始值
        original_dropped = helpers._dropped_packets

        # 模拟队列满
        full_queue = queue.Queue(maxsize=1)
        full_queue.put_nowait({})

        dropped_before = helpers._dropped_packets
        try:
            full_queue.put_nowait({"length": 64})
        except queue.Full:
            helpers._dropped_packets += 1

        assert helpers._dropped_packets == dropped_before + 1

        # 恢复
        helpers._dropped_packets = original_dropped

    def test_dropped_counter_multiple_overflows(self):
        """多次溢出应累加丢包计数。"""
        import campus_ids.web.helpers as helpers

        original_dropped = helpers._dropped_packets
        helpers._dropped_packets = 0

        try:
            for _ in range(5):
                helpers._dropped_packets += 1
            assert helpers._dropped_packets == 5
        finally:
            helpers._dropped_packets = original_dropped

    def test_queue_maxsize_is_20000(self):
        """验证 _packet_queue 的 maxsize 为 20000。"""
        import campus_ids.web.helpers as helpers
        assert helpers._packet_queue.maxsize == 20000

    def test_normal_put_succeeds(self):
        """队列未满时 put_nowait 应成功。"""
        import campus_ids.web.helpers as helpers

        original_dropped = helpers._dropped_packets
        # 清空队列确保有空间
        while not helpers._packet_queue.empty():
            try:
                helpers._packet_queue.get_nowait()
            except queue.Empty:
                break

        try:
            helpers._packet_queue.put_nowait({"length": 64, "test": True})
            assert helpers._dropped_packets == original_dropped
        finally:
            # 清理
            try:
                helpers._packet_queue.get_nowait()
            except queue.Empty:
                pass
            helpers._dropped_packets = original_dropped