"""O-07b: 告警去抖单元测试 — should_emit_alert / reset_alert_cooldown。

验证冷却窗口内同类型+同级告警合并，恢复正常后冷却重置。
"""
import time
from unittest.mock import patch

import pytest

from campus_ids.web.helpers import should_emit_alert, reset_alert_cooldown


class TestShouldEmitAlert:
    """验证 should_emit_alert 的去抖逻辑。"""

    def setup_method(self):
        """每个测试前重置冷却状态。"""
        reset_alert_cooldown()

    def teardown_method(self):
        """每个测试后清理。"""
        reset_alert_cooldown()

    def test_first_alert_emitted(self):
        """首次告警应立即发出。"""
        assert should_emit_alert("ddos", "high") is True

    def test_same_key_within_cooldown_suppressed(self):
        """冷却窗口内同类型+同级告警应被抑制。"""
        assert should_emit_alert("ddos", "high") is True
        # 冷却窗口内再次调用
        assert should_emit_alert("ddos", "high") is False

    def test_different_attack_type_emitted(self):
        """不同攻击类型应重置冷却并发出。"""
        assert should_emit_alert("ddos", "high") is True
        assert should_emit_alert("port_scan", "high") is True

    def test_different_level_emitted(self):
        """同攻击类型不同级别应重置冷却并发出。"""
        assert should_emit_alert("ddos", "high") is True
        assert should_emit_alert("ddos", "medium") is True

    def test_cooldown_expiry_emits_again(self):
        """冷却窗口过期后同类型+同级告警应再次发出。"""
        assert should_emit_alert("ddos", "high") is True
        # 模拟时间流逝超过冷却窗口
        with patch("campus_ids.web.helpers._time") as mock_time:
            mock_time.time.return_value = time.time() + 999
            assert should_emit_alert("ddos", "high") is True

    def test_reset_allows_immediate_realert(self):
        """reset_alert_cooldown 后应允许立即重新告警。"""
        assert should_emit_alert("ddos", "high") is True
        assert should_emit_alert("ddos", "high") is False  # 冷却中
        reset_alert_cooldown()
        assert should_emit_alert("ddos", "high") is True  # 重置后可再告警


class TestResetAlertCooldown:
    """验证 reset_alert_cooldown 的重置行为。"""

    def setup_method(self):
        reset_alert_cooldown()

    def teardown_method(self):
        reset_alert_cooldown()

    def test_reset_clears_key(self):
        """重置后 _last_alert_key 应为空。"""
        should_emit_alert("ddos", "high")
        import campus_ids.web.helpers as h
        assert h._last_alert_key == "ddos:high"
        reset_alert_cooldown()
        assert h._last_alert_key == ""

    def test_reset_clears_time(self):
        """重置后 _last_alert_time 应为 0。"""
        should_emit_alert("ddos", "high")
        import campus_ids.web.helpers as h
        assert h._last_alert_time > 0
        reset_alert_cooldown()
        assert h._last_alert_time == 0.0

    def test_recovery_scenario(self):
        """模拟完整告警→恢复→再告警生命周期。"""
        # 1. 首次告警
        assert should_emit_alert("ddos", "high") is True
        # 2. 冷却中抑制
        assert should_emit_alert("ddos", "high") is False
        # 3. 恢复正常 → reset
        reset_alert_cooldown()
        # 4. 新告警应立即发出
        assert should_emit_alert("port_scan", "medium") is True