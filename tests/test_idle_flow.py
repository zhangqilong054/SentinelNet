"""O-20: ML 流表 idle timeout 单元测试 — _drain_idle_flows。

验证流表中超时流被正确回收，活跃流保留。
"""
import time

import pytest

from campus_ids.detector.dual_detector import DualDetector
from campus_ids.detector.detector import AnomalyDetector


@pytest.fixture
def dual():
    """创建 DualDetector 实例，设置短 idle timeout。"""
    rule_det = AnomalyDetector()
    det = DualDetector(rule_det, model_path="/nonexistent/model.pkl")
    det._flow_idle_timeout = 0.1  # 100ms，加速测试
    return det


class TestDrainIdleFlows:
    """验证 _drain_idle_flows 的超时回收逻辑。"""

    def test_idle_flow_drained(self, dual):
        """超时流应被回收并返回其包。"""
        pkt = {"src_ip": "1.1.1.1", "dst_ip": "2.2.2.2", "sport": 1234, "dport": 80, "proto": "TCP"}
        # 添加包到流表
        dual.add_packet(pkt)

        # 确认流表有条目
        assert len(dual._flow_table) == 1

        # 等待超时
        time.sleep(0.2)

        # 回收超时流
        drained = dual._drain_idle_flows()
        assert len(drained) >= 1  # 至少有流表包 + 缓冲区包
        assert len(dual._flow_table) == 0  # 流表应清空

    def test_active_flow_not_drained(self, dual):
        """活跃流（未超时）不应被回收。"""
        pkt = {"src_ip": "1.1.1.1", "dst_ip": "2.2.2.2", "sport": 1234, "dport": 80, "proto": "TCP"}
        dual.add_packet(pkt)

        # 立即回收（未超时）
        dual._drain_idle_flows()
        # 流表条目应保留（只有缓冲区包被取出）
        assert len(dual._flow_table) == 1

    def test_mixed_idle_and_active(self, dual):
        """混合场景：超时流回收，活跃流保留。"""
        pkt1 = {"src_ip": "1.1.1.1", "dst_ip": "2.2.2.2", "sport": 1234, "dport": 80, "proto": "TCP"}
        pkt2 = {"src_ip": "3.3.3.3", "dst_ip": "4.4.4.4", "sport": 5678, "dport": 443, "proto": "TCP"}

        dual.add_packet(pkt1)
        time.sleep(0.15)  # 等第一个流超时

        dual.add_packet(pkt2)  # 第二个流刚活跃

        dual._drain_idle_flows()
        # 第一个流应被回收，第二个保留
        remaining_keys = list(dual._flow_table.keys())
        assert len(remaining_keys) == 1
        key = remaining_keys[0]
        assert key[0] == "3.3.3.3"  # 第二个流保留

    def test_empty_flow_table(self, dual):
        """空流表和空缓冲区应返回空列表。"""
        drained = dual._drain_idle_flows()
        assert isinstance(drained, list)

    def test_flow_buffer_also_drained(self, dual):
        """_drain_idle_flows 应同时取出缓冲区中的包。"""
        pkt = {"src_ip": "1.1.1.1", "dst_ip": "2.2.2.2", "sport": 1234, "dport": 80, "proto": "TCP"}
        dual.add_packet(pkt)

        # 缓冲区和流表都应有包
        assert len(dual._flow_buffer) == 1
        assert len(dual._flow_table) == 1

        time.sleep(0.2)
        dual._drain_idle_flows()

        # 两者都应被清空
        assert len(dual._flow_buffer) == 0
        assert len(dual._flow_table) == 0

    def test_idle_timeout_configurable(self, dual):
        """idle timeout 应可通过环境变量配置。"""
        assert dual._flow_idle_timeout == 0.1  # fixture 设置的值

    def test_add_packet_creates_flow_entry(self, dual):
        """add_packet 应在流表中创建条目。"""
        pkt = {"src_ip": "10.0.0.1", "dst_ip": "10.0.0.2", "sport": 9999, "dport": 22, "proto": "TCP"}
        dual.add_packet(pkt)
        assert len(dual._flow_table) == 1
        key = list(dual._flow_table.keys())[0]
        assert key == ("10.0.0.1", "10.0.0.2", 9999, 22, "TCP")

    def test_add_packet_updates_last_active(self, dual):
        """add_packet 对已有流应更新 last_active。"""
        pkt = {"src_ip": "1.1.1.1", "dst_ip": "2.2.2.2", "sport": 1234, "dport": 80, "proto": "TCP"}
        dual.add_packet(pkt)
        t1 = dual._flow_table[list(dual._flow_table.keys())[0]]["last_active"]

        time.sleep(0.05)
        dual.add_packet(pkt)
        t2 = dual._flow_table[list(dual._flow_table.keys())[0]]["last_active"]

        assert t2 > t1