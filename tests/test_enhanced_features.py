"""enhanced_features.py 单元测试 — 覆盖特征提取与流聚合。"""
import pytest

from campus_ids.capture.enhanced_features import (
    FEATURE_NAMES,
    PacketInfo,
    FlowFeatures,
    _compute_entropy,
    _ja3_to_numeric,
    aggregate_flow_features,
    flow_to_feature_vector,
    flow_to_csv_row,
    CSV_HEADER,
)


# ── 辅助函数 ──────────────────────────────────────────────────

def _make_pkt(src_ip="192.168.1.1", dst_ip="10.0.0.1",
              src_port=12345, dst_port=80, proto="TCP",
              length=64, timestamp=0.0,
              is_syn=False, is_fin=False, is_rst=False, is_psh=False,
              window_size=0, ja3_hash="", tls_version="", cipher_suite_count=0):
    return PacketInfo(
        src_ip=src_ip, dst_ip=dst_ip, src_port=src_port, dst_port=dst_port,
        proto=proto, length=length, timestamp=timestamp,
        is_syn=is_syn, is_fin=is_fin, is_rst=is_rst, is_psh=is_psh,
        window_size=window_size, ja3_hash=ja3_hash,
        tls_version=tls_version, cipher_suite_count=cipher_suite_count,
    )


# ── _compute_entropy ──────────────────────────────────────────

class TestComputeEntropy:
    def test_empty_list(self):
        assert _compute_entropy([]) == 0.0

    def test_single_value(self):
        assert _compute_entropy([42]) == 0.0

    def test_uniform_distribution(self):
        import math
        values = [1, 2, 3, 4]
        entropy = _compute_entropy(values)
        assert abs(entropy - 2.0) < 0.01  # log2(4) = 2

    def test_all_same(self):
        assert _compute_entropy([5, 5, 5, 5]) == 0.0


# ── _ja3_to_numeric ──────────────────────────────────────────

class TestJA3ToNumeric:
    def test_empty_string(self):
        assert _ja3_to_numeric("") == 0.0

    def test_valid_hash(self):
        result = _ja3_to_numeric("a1b2c3d4e5f6")
        assert 0.0 <= result <= 1.0

    def test_invalid_hash(self):
        assert _ja3_to_numeric("not_hex!") == 0.0

    def test_deterministic(self):
        assert _ja3_to_numeric("abc123") == _ja3_to_numeric("abc123")


# ── aggregate_flow_features ──────────────────────────────────

class TestAggregateFlowFeatures:
    def test_single_packet(self):
        pkts = [_make_pkt(timestamp=1.0)]
        flows = aggregate_flow_features(pkts)
        assert len(flows) == 1
        assert flows[0].pkt_count == 1
        assert flows[0].label == "Normal"

    def test_multiple_flows(self):
        pkts = [
            _make_pkt(src_ip="1.1.1.1", dst_port=80, timestamp=1.0),
            _make_pkt(src_ip="1.1.1.1", dst_port=80, timestamp=2.0),
            _make_pkt(src_ip="2.2.2.2", dst_port=443, timestamp=1.5),
        ]
        flows = aggregate_flow_features(pkts)
        assert len(flows) == 2  # 两条流（不同五元组）

    def test_syn_flood_labeling(self):
        """SYN 比例 > 0.9 且包数 > 100 应标记为 Attack（收紧后阈值）。"""
        pkts = [_make_pkt(is_syn=True, timestamp=float(i)) for i in range(120)]
        flows = aggregate_flow_features(pkts)
        assert len(flows) == 1
        assert flows[0].label == "Attack"

    def test_high_freq_labeling(self):
        """包数 > 800 应标记为 Attack（收紧后阈值）。"""
        pkts = [_make_pkt(timestamp=float(i)) for i in range(900)]
        flows = aggregate_flow_features(pkts)
        assert len(flows) == 1
        assert flows[0].label == "Attack"

    def test_empty_packets(self):
        flows = aggregate_flow_features([])
        assert flows == []

    def test_tls_records_integration(self):
        """TLS 记录应正确关联到流。"""
        pkts = [_make_pkt(src_ip="1.1.1.1", src_port=54321, timestamp=1.0)]
        tls_records = [{"src_ip": "1.1.1.1", "src_port": 54321,
                        "ja3_hash": "abc123", "tls_version": "TLS 1.3",
                        "cipher_count": 10}]
        flows = aggregate_flow_features(pkts, tls_records)
        assert len(flows) == 1
        assert flows[0].ja3_hash_enc > 0.0
        assert flows[0].tls_version_enc >= 0
        assert flows[0].cipher_suite_count == 10

    def test_tcp_flag_ratios(self):
        """TCP 标志比例应正确计算。"""
        pkts = [
            _make_pkt(is_syn=True, timestamp=1.0),
            _make_pkt(is_syn=True, timestamp=2.0),
            _make_pkt(is_fin=True, timestamp=3.0),
            _make_pkt(is_psh=True, timestamp=4.0),
        ]
        flows = aggregate_flow_features(pkts)
        assert len(flows) == 1
        assert abs(flows[0].syn_flag_ratio - 0.5) < 0.01
        assert abs(flows[0].fin_flag_ratio - 0.25) < 0.01
        assert abs(flows[0].psh_flag_ratio - 0.25) < 0.01


# ── flow_to_feature_vector ──────────────────────────────────

class TestFlowToFeatureVector:
    def test_feature_vector_length(self):
        """特征向量长度应与 FEATURE_NAMES 一致。"""
        flow = FlowFeatures()
        vec = flow_to_feature_vector(flow)
        assert len(vec) == len(FEATURE_NAMES)

    def test_csv_row_includes_metadata_and_label(self):
        """CSV 行应包含元信息 + 特征 + 标签。"""
        flow = FlowFeatures(src_ip="1.1.1.1", dst_ip="2.2.2.2",
                            src_port=1234, dst_port=80, proto="TCP")
        row = flow_to_csv_row(flow)
        # 5 元信息 + FEATURE_NAMES 数量 + 1 标签
        assert len(row) == 5 + len(FEATURE_NAMES) + 1

    def test_csv_header_matches_row_length(self):
        """CSV_HEADER 长度应与 csv_row 一致。"""
        flow = FlowFeatures()
        row = flow_to_csv_row(flow)
        assert len(CSV_HEADER) == len(row)


# ── PacketInfo dataclass ────────────────────────────────────

class TestPacketInfo:
    def test_defaults(self):
        pkt = PacketInfo()
        assert pkt.src_ip == ""
        assert pkt.proto == ""
        assert pkt.is_syn is False
        assert pkt.ja3_hash == ""

    def test_custom_values(self):
        pkt = PacketInfo(src_ip="10.0.0.1", dst_port=443, proto="TCP", is_syn=True)
        assert pkt.src_ip == "10.0.0.1"
        assert pkt.dst_port == 443
        assert pkt.is_syn is True