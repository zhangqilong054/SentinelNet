"""enhanced_features.py 单元测试 — 覆盖 label_packets 函数。"""
import pytest
from campus_ids.capture.enhanced_features import label_packets, HIGH_FREQ_IP_THRESHOLD, PORT_SCAN_THRESHOLD


class TestLabelPackets:
    """测试 label_packets 的启发式打标逻辑。"""

    def _make_row(self, src_ip="192.168.1.1", dst_ip="10.0.0.1",
                  src_port=12345, dst_port=80, proto="TCP",
                  length=64, timestamp=0.0):
        return [src_ip, dst_ip, src_port, dst_port, proto, length, timestamp]

    def test_normal_traffic(self):
        """少量正常流量应全部标记为 Normal。"""
        rows = [self._make_row(src_ip=f"192.168.1.{i}") for i in range(5)]
        labeled = label_packets(rows)
        assert all(r[-1] == "Normal" for r in labeled)

    def test_high_freq_ip_attack(self):
        """超过高频阈值的源IP应标记为 Attack。"""
        # 生成超过 HIGH_FREQ_IP_THRESHOLD 的包（同一源IP）
        rows = [self._make_row(src_ip="192.168.1.100") for _ in range(HIGH_FREQ_IP_THRESHOLD + 1)]
        labeled = label_packets(rows)
        attack_count = sum(1 for r in labeled if r[-1] == "Attack")
        assert attack_count == len(rows)  # 全部标记为 Attack

    def test_port_scan_attack(self):
        """同一源IP访问超过端口扫描阈值的不同端口应标记为 Attack。"""
        rows = [self._make_row(src_ip="192.168.1.200", dst_port=i)
                for i in range(PORT_SCAN_THRESHOLD + 1)]
        labeled = label_packets(rows)
        attack_count = sum(1 for r in labeled if r[-1] == "Attack")
        assert attack_count == len(rows)

    def test_mixed_traffic(self):
        """混合流量：正常IP + 攻击IP，应正确区分。"""
        normal_rows = [self._make_row(src_ip="192.168.1.1") for _ in range(5)]
        attack_rows = [self._make_row(src_ip="10.0.0.99") for _ in range(HIGH_FREQ_IP_THRESHOLD + 1)]
        all_rows = normal_rows + attack_rows
        labeled = label_packets(all_rows)
        normal_count = sum(1 for r in labeled if r[-1] == "Normal")
        attack_count = sum(1 for r in labeled if r[-1] == "Attack")
        assert normal_count == len(normal_rows)
        assert attack_count == len(attack_rows)

    def test_empty_rows(self):
        """空输入应返回空列表。"""
        labeled = label_packets([])
        assert labeled == []

    def test_label_position(self):
        """标签应在最后一列。"""
        rows = [self._make_row()]
        labeled = label_packets(rows)
        assert len(labeled[0]) == 8  # 7 original + 1 label
        assert labeled[0][-1] in ("Normal", "Attack")

    def test_boundary_threshold(self):
        """恰好等于阈值（不超限）应标记为 Normal。"""
        rows = [self._make_row(src_ip="192.168.1.50") for _ in range(HIGH_FREQ_IP_THRESHOLD)]
        labeled = label_packets(rows)
        assert all(r[-1] == "Normal" for r in labeled)