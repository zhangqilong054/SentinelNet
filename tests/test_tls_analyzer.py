"""TLS 分析器单元测试 — D4 修复验证：JA3 指纹 raw/hash 比对口径一致性。"""
import hashlib

import pytest

from campus_ids.capture.tls_analyzer import (
    KNOWN_BENIGN_JA3, KNOWN_MALICIOUS_JA3,
    _KNOWN_BENIGN_JA3_RAW, _KNOWN_MALICIOUS_JA3_RAW,
    TLSAnalyzer, TLSInfo,
)


# ── 辅助：构建 TLSInfo ────────────────────────────────────────────

def _make_tls_info(ja3_raw: str, ja3_hash: str = "",
                   tls_version: str = "TLS 1.3",
                   cipher_count: int = 10) -> TLSInfo:
    """构建测试用 TLSInfo，自动计算 ja3_hash（如未提供）。"""
    if not ja3_hash:
        ja3_hash = hashlib.md5(ja3_raw.encode()).hexdigest()
    return TLSInfo(
        src_ip="10.0.0.1", dst_ip="192.168.1.1",
        src_port=12345, dst_port=443,
        tls_version=tls_version,
        ja3_raw=ja3_raw, ja3_hash=ja3_hash,
        sni="example.com",
        cipher_count=cipher_count,
    )


# ── D4 核心验证：raw → hash 预计算与比对一致性 ──────────────────

class TestJA3HashConsistency:
    """验证 KNOWN_BENIGN_JA3 / KNOWN_MALICIOUS_JA3 是 MD5 哈希集合，
    与 _classify_tls 中 ja3_hash 比对口径一致。"""

    def test_benign_set_contains_md5_hashes(self):
        """KNOWN_BENIGN_JA3 应包含 ja3_raw 的 MD5 哈希，而非原始字符串。"""
        for raw in _KNOWN_BENIGN_JA3_RAW:
            expected_hash = hashlib.md5(raw.encode()).hexdigest()
            assert expected_hash in KNOWN_BENIGN_JA3, (
                f"良性 JA3 原始字符串 '{raw[:30]}...' 的 MD5 哈希不在集合中"
            )

    def test_malicious_set_contains_md5_hashes(self):
        """KNOWN_MALICIOUS_JA3 应包含 ja3_raw 的 MD5 哈希，而非原始字符串。"""
        for raw in _KNOWN_MALICIOUS_JA3_RAW:
            expected_hash = hashlib.md5(raw.encode()).hexdigest()
            assert expected_hash in KNOWN_MALICIOUS_JA3, (
                f"恶意 JA3 原始字符串 '{raw[:30]}...' 的 MD5 哈希不在集合中"
            )

    def test_benign_set_does_not_contain_raw_strings(self):
        """KNOWN_BENIGN_JA3 不应包含原始 ja3_raw 字符串。"""
        for raw in _KNOWN_BENIGN_JA3_RAW:
            assert raw not in KNOWN_BENIGN_JA3, (
                f"良性集合不应包含原始字符串 '{raw[:30]}...'"
            )


# ── _classify_tls 行为验证 ────────────────────────────────────────

class TestClassifyTls:
    """验证 _classify_tls 基于 ja3_hash 正确分类。"""

    @staticmethod
    def _find_exclusive_benign_raw() -> str:
        """找一个仅在良性库中、不在恶意库中的 JA3 raw。"""
        malicious_hashes = {hashlib.md5(r.encode()).hexdigest() for r in _KNOWN_MALICIOUS_JA3_RAW}
        for raw in _KNOWN_BENIGN_JA3_RAW:
            h = hashlib.md5(raw.encode()).hexdigest()
            if h not in malicious_hashes:
                return raw
        pytest.skip("无互斥良性指纹（良性库完全被恶意库覆盖）")

    def test_known_benign_not_suspicious(self):
        """已知良性指纹（不在恶意库中）不应标记为可疑。"""
        analyzer = TLSAnalyzer()
        raw = self._find_exclusive_benign_raw()
        info = _make_tls_info(raw)
        analyzer._classify_tls(info)
        assert info.is_suspicious is False, (
            f"已知良性指纹不应判可疑: {info.suspicion_reason}"
        )

    def test_known_malicious_flagged(self):
        """已知恶意指纹应标记为可疑，原因为'已知恶意'。"""
        analyzer = TLSAnalyzer()
        raw = _KNOWN_MALICIOUS_JA3_RAW[0]  # Metasploit 指纹
        info = _make_tls_info(raw)
        analyzer._classify_tls(info)
        assert info.is_suspicious is True
        assert "已知恶意" in info.suspicion_reason

    def test_unknown_fingerprint_flagged(self):
        """未知指纹（不在良性库也不在恶意库）应标记为'未知'。"""
        analyzer = TLSAnalyzer()
        info = _make_tls_info("771,99999,0,29-23-24,0")  # 不存在的套件
        analyzer._classify_tls(info)
        assert info.is_suspicious is True
        assert "未知" in info.suspicion_reason

    def test_old_tls_version_flagged(self):
        """良性指纹但 TLS 版本过旧也应标记为可疑。"""
        analyzer = TLSAnalyzer()
        raw = self._find_exclusive_benign_raw()
        info = _make_tls_info(raw, tls_version="TLS 1.0")
        analyzer._classify_tls(info)
        assert info.is_suspicious is True
        assert "过旧" in info.suspicion_reason

    def test_few_ciphers_flagged(self):
        """良性指纹但加密套件数量异常少应标记为可疑。"""
        analyzer = TLSAnalyzer()
        raw = self._find_exclusive_benign_raw()
        info = _make_tls_info(raw, cipher_count=2)
        analyzer._classify_tls(info)
        assert info.is_suspicious is True
        assert "加密套件数量异常少" in info.suspicion_reason


# ── get_stats 中 is_benign 判断验证 ──────────────────────────────

class TestGetStatsIsBenign:
    """验证 get_stats 的 ja3_top_fingerprints 中 is_benign 字段正确。"""

    def test_benign_fingerprint_is_benign_in_stats(self):
        """良性指纹在统计中 is_benign 应为 True。"""
        analyzer = TLSAnalyzer()
        raw = _KNOWN_BENIGN_JA3_RAW[0]
        ja3_hash = hashlib.md5(raw.encode()).hexdigest()
        info = _make_tls_info(raw)
        analyzer._classify_tls(info)
        analyzer._ja3_counter[ja3_hash] += 1
        analyzer._tls_records.append(info)

        stats = analyzer.get_stats()
        ja3_top = stats["ja3_top_fingerprints"]
        assert len(ja3_top) == 1
        assert ja3_top[0]["hash"] == ja3_hash
        assert ja3_top[0]["is_benign"] is True, "良性指纹 is_benign 应为 True"

    def test_unknown_fingerprint_not_benign_in_stats(self):
        """未知指纹在统计中 is_benign 应为 False。"""
        analyzer = TLSAnalyzer()
        raw = "771,99999,0,29-23-24,0"
        ja3_hash = hashlib.md5(raw.encode()).hexdigest()
        info = _make_tls_info(raw)
        analyzer._classify_tls(info)
        analyzer._ja3_counter[ja3_hash] += 1
        analyzer._tls_records.append(info)

        stats = analyzer.get_stats()
        ja3_top = stats["ja3_top_fingerprints"]
        assert len(ja3_top) == 1
        assert ja3_top[0]["is_benign"] is False, "未知指纹 is_benign 应为 False"