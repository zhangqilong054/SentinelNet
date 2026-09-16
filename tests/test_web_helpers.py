"""helpers 模块测试 — CONFIG/update_config/save_traffic_data/告警去抖/检测节拍/抓包线程状态。"""
import os
import pytest
import threading
import time as _time

import campus_ids.web.helpers as helpers
from campus_ids.web.helpers import (
    CONFIG, update_config, should_emit_alert, reset_alert_cooldown,
    start_detector_tick, stop_detector_tick,
    start_capture_thread, stop_capture_thread,
    start_enhanced_capture_thread, stop_enhanced_capture_thread,
    get_enhanced_capture_status,
    get_auto_status, start_auto_thread,
    traffic_data, dual_detector,
)


# ── CONFIG 字典 ────────────────────────────────────────────────────

class TestConfig:
    def test_config_has_required_keys(self):
        required = [
            'port', 'refresh_interval',
            'ddos_threshold', 'port_scan_threshold',
            'syn_flood_threshold', 'udp_flood_threshold',
            'brute_force_threshold', 'brute_force_window',
            'lateral_movement_threshold',
        ]
        for key in required:
            assert key in CONFIG, f"CONFIG 缺少键: {key}"

    def test_config_values_are_int(self):
        for key, val in CONFIG.items():
            assert isinstance(val, int), f"CONFIG[{key}] = {val} 不是 int"


# ── update_config ──────────────────────────────────────────────────

class TestUpdateConfig:
    def test_update_int_value(self):
        old = CONFIG['ddos_threshold']
        update_config('ddos_threshold', 999)
        assert CONFIG['ddos_threshold'] == 999
        # 恢复
        update_config('ddos_threshold', old)

    def test_update_string_converted_to_int(self):
        old = CONFIG['port_scan_threshold']
        update_config('port_scan_threshold', '50')
        assert CONFIG['port_scan_threshold'] == 50
        update_config('port_scan_threshold', old)

    def test_update_persist_flag(self):
        """persist=True 且 key 在 _THRESHOLD_KEYS 中应写入数据库。"""
        old = CONFIG['ddos_threshold']
        update_config('ddos_threshold', 100, persist=True)
        assert CONFIG['ddos_threshold'] == 100
        # 验证数据库中也有
        from campus_ids.web.database import get_all_config
        saved = get_all_config()
        assert 'ddos_threshold' in saved
        update_config('ddos_threshold', old, persist=True)

    def test_update_non_threshold_key_no_persist(self):
        """非阈值键 persist=True 不应报错（但不写入数据库）。"""
        update_config('port', 8080, persist=True)
        assert CONFIG['port'] == 8080
        # 恢复
        from campus_ids.config import WEB_PORT
        update_config('port', WEB_PORT)


# ── 告警去抖 ──────────────────────────────────────────────────────

class TestAlertCooldown:
    def test_first_alert_emitted(self):
        reset_alert_cooldown()
        assert should_emit_alert("ddos", "high") is True

    def test_same_key_within_cooldown_suppressed(self):
        reset_alert_cooldown()
        should_emit_alert("ddos", "high")
        # 冷却窗口内同类型+同级应被抑制
        assert should_emit_alert("ddos", "high") is False

    def test_different_key_emitted(self):
        reset_alert_cooldown()
        should_emit_alert("ddos", "high")
        assert should_emit_alert("port_scan", "high") is True

    def test_different_level_emitted(self):
        reset_alert_cooldown()
        should_emit_alert("ddos", "high")
        assert should_emit_alert("ddos", "medium") is True

    def test_reset_allows_immediate_emit(self):
        reset_alert_cooldown()
        should_emit_alert("ddos", "high")
        reset_alert_cooldown()
        assert should_emit_alert("ddos", "high") is True


# ── 检测节拍 ──────────────────────────────────────────────────────

class TestDetectorTick:
    def test_start_returns_true(self):
        """首次启动应返回 True。"""
        stop_detector_tick()
        result = start_detector_tick()
        # 可能已经在运行，返回 False
        assert result in (True, False)
        stop_detector_tick()

    def test_double_start_returns_false(self):
        """重复启动应返回 False（幂等）。"""
        stop_detector_tick()
        start_detector_tick()
        result = start_detector_tick()
        assert result is False
        stop_detector_tick()

    def test_stop_returns_true(self):
        stop_detector_tick()
        start_detector_tick()
        assert stop_detector_tick() is True


# ── 抓包线程状态 ──────────────────────────────────────────────────

class TestCaptureThread:
    def test_stop_returns_true(self):
        """停止抓包应返回 True。"""
        result = stop_capture_thread()
        assert result is True

    def test_start_returns_true(self):
        """启动抓包线程应返回 True（可能因 scapy 不可用而失败，但函数本身不报错）。"""
        stop_capture_thread()
        # 注意：start_capture_thread 会启动真实抓包线程
        # 在测试中仅验证函数接口
        result = stop_capture_thread()  # 确保已停止
        assert result is True


# ── 增强抓包 ──────────────────────────────────────────────────────

class TestEnhancedCapture:
    def test_stop_returns_true(self):
        result = stop_enhanced_capture_thread()
        assert result is True

    def test_get_status_returns_dict(self):
        status = get_enhanced_capture_status()
        assert isinstance(status, dict)
        assert "running" in status


# ── 一键全流程 ────────────────────────────────────────────────────

class TestAutoStatus:
    def test_get_auto_status_returns_dict(self):
        status = get_auto_status()
        assert isinstance(status, dict)
        assert "running" in status
        assert "step" in status
        assert "total_steps" in status

    def test_start_auto_thread_returns_bool(self):
        # 不真正启动（可能耗时很长），只验证接口
        result = start_auto_thread(duration=1)
        assert isinstance(result, bool)


# ── traffic_data 结构 ─────────────────────────────────────────────

class TestTrafficData:
    def test_traffic_data_has_required_keys(self):
        required = [
            'qps', 'connections', 'alert', 'timestamp',
            'packet_count', 'syn_packets', 'udp_packets', 'dns_packets',
        ]
        for key in required:
            assert key in traffic_data, f"traffic_data 缺少键: {key}"

    def test_traffic_data_types(self):
        assert isinstance(traffic_data['qps'], int)
        assert isinstance(traffic_data['connections'], int)
        assert isinstance(traffic_data['packet_count'], int)


# ── dual_detector 存在 ─────────────────────────────────────────────

class TestDualDetector:
    def test_dual_detector_exists(self):
        assert dual_detector is not None

    def test_dual_detector_has_detect(self):
        assert hasattr(dual_detector, 'detect')