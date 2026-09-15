"""O-13: GREASE 过滤单元测试 — _filter_grease 及 GREASE_VALUES 完整性验证。

验证 RFC 8701 GREASE 保留值被正确过滤，不影响正常加密套件值。
"""
import pytest

from campus_ids.capture.tls_analyzer import _filter_grease, GREASE_VALUES


class TestFilterGrease:
    """验证 _filter_grease 函数。"""

    def test_removes_all_grease_values(self):
        """所有 GREASE 值应被过滤。"""
        input_values = list(GREASE_VALUES)
        result = _filter_grease(input_values)
        assert result == []

    def test_preserves_non_grease_values(self):
        """非 GREASE 值应保留。"""
        normal = [0x002f, 0x0035, 0x009c, 0xc02b, 0xc02f]
        result = _filter_grease(normal)
        assert result == normal

    def test_mixed_grease_and_normal(self):
        """混合列表应只保留非 GREASE 值。"""
        mixed = [0x0a0a, 0x002f, 0x1a1a, 0x0035, 0x2a2a]
        result = _filter_grease(mixed)
        assert result == [0x002f, 0x0035]

    def test_empty_list(self):
        """空列表应返回空列表。"""
        assert _filter_grease([]) == []

    def test_single_grease_value(self):
        """单个 GREASE 值应被过滤。"""
        assert _filter_grease([0x0a0a]) == []

    def test_single_normal_value(self):
        """单个正常值应保留。"""
        assert _filter_grease([0x009c]) == [0x009c]


class TestGreaseValuesCompleteness:
    """验证 GREASE_VALUES 集合的完整性。"""

    def test_grease_values_count(self):
        """RFC 8701 定义了 16 个 GREASE 值。"""
        assert len(GREASE_VALUES) == 16

    def test_grease_values_are_valid(self):
        """每个 GREASE 值应符合 0x?a?a 模式。"""
        for v in GREASE_VALUES:
            # 高 nibble: 0x0-0xf, 低 nibble 固定 0xa
            assert (v & 0x0f0f) == 0x0a0a, f"GREASE 值 0x{v:04x} 不符合 0x?a?a 模式"

    def test_all_grease_first_nibbles(self):
        """16 个 GREASE 值应覆盖 0x0a0a 到 0xfafa（步长 0x1010）。"""
        high_nibbles = {(v >> 12) & 0xf for v in GREASE_VALUES}
        assert high_nibbles == set(range(16))

    def test_no_duplicate_values(self):
        """GREASE_VALUES 不应有重复。"""
        assert len(GREASE_VALUES) == len(set(GREASE_VALUES))