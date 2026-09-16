"""T1.3 runtime/settings.py — 三层配置唯一入口测试。

验收：单测覆盖三层优先级；所有阈值可从 API 读写。

三层优先级（高→低）：运行期覆盖 → 环境变量 → 默认值。
DB 配置在 lifespan 中加载，通过 setattr 覆盖环境变量/默认值。
"""
from __future__ import annotations

import os
import pytest

from campus_ids.runtime.settings import Settings, get_settings, reset_settings


@pytest.fixture(autouse=True)
def _clean_env():
    """每个测试前后清理环境变量和设置单例。"""
    # 清理可能影响测试的环境变量
    for key in list(os.environ.keys()):
        if key.startswith("CAMPUS_IDS_"):
            del os.environ[key]
    reset_settings()
    yield
    for key in list(os.environ.keys()):
        if key.startswith("CAMPUS_IDS_"):
            del os.environ[key]
    reset_settings()


# ── 默认值层 ─────────────────────────────────────────────────────


class TestDefaults:
    """验证默认值正确。"""

    def test_default_ddos_threshold(self):
        s = Settings()
        assert s.ddos_threshold == 500

    def test_default_port_scan_threshold(self):
        s = Settings()
        assert s.port_scan_threshold == 50

    def test_default_web_port(self):
        s = Settings()
        assert s.web_port == 5000

    def test_default_auth_enabled(self):
        s = Settings()
        assert s.auth_enabled is True

    def test_default_cors_origins(self):
        s = Settings()
        assert s.cors_origins == ["*"]

    def test_threshold_keys_includes_all(self):
        """threshold_keys 应包含所有需要持久化的配置键。"""
        s = Settings()
        expected = {
            "ddos_threshold", "port_scan_threshold", "syn_flood_threshold",
            "udp_flood_threshold", "brute_force_threshold",
            "brute_force_window", "lateral_movement_threshold",
            "high_freq_ip_threshold",
        }
        assert s.threshold_keys == frozenset(expected)


# ── 环境变量层 ───────────────────────────────────────────────────


class TestEnvOverride:
    """验证环境变量覆盖默认值。"""

    def test_env_overrides_default(self):
        os.environ["CAMPUS_IDS_DDOS_THRESHOLD"] = "1000"
        s = Settings()
        assert s.ddos_threshold == 1000

    def test_env_web_port(self):
        os.environ["CAMPUS_IDS_WEB_PORT"] = "8080"
        s = Settings()
        assert s.web_port == 8080

    def test_env_auth_enabled_false(self):
        os.environ["CAMPUS_IDS_AUTH_ENABLED"] = "0"
        s = Settings()
        assert s.auth_enabled is False

    def test_env_cors_origins(self):
        os.environ["CAMPUS_IDS_CORS_ORIGINS"] = '["http://localhost:3000"]'
        s = Settings()
        assert s.cors_origins == ["http://localhost:3000"]


# ── 运行期覆盖层 ─────────────────────────────────────────────────


class TestRuntimeOverride:
    """验证运行期覆盖优先级最高。"""

    def test_override_takes_precedence(self):
        """运行期覆盖优先于默认值和环境变量。"""
        os.environ["CAMPUS_IDS_DDOS_THRESHOLD"] = "1000"
        s = Settings()
        s.set_override("ddos_threshold", 2000)
        assert s.get("ddos_threshold") == 2000

    def test_get_returns_override_first(self):
        """get() 方法优先返回运行期覆盖。"""
        s = Settings()
        s.set_override("web_port", 9000)
        assert s.get("web_port") == 9000

    def test_get_returns_default_without_override(self):
        """无覆盖时 get() 返回默认值。"""
        s = Settings()
        assert s.get("ddos_threshold") == 500

    def test_clear_overrides(self):
        """clear_overrides 清除所有运行期覆盖。"""
        s = Settings()
        s.set_override("ddos_threshold", 9999)
        s.clear_overrides()
        assert s.get("ddos_threshold") == 500

    def test_override_does_not_persist(self):
        """运行期覆盖不持久化到环境变量或文件。"""
        s = Settings()
        s.set_override("web_port", 9999)
        # 创建新实例不应有覆盖
        s2 = Settings()
        assert s2.get("web_port") == 5000


# ── 全局单例 ─────────────────────────────────────────────────────


class TestSingleton:
    """验证全局单例行为。"""

    def test_get_settings_returns_same_instance(self):
        """get_settings() 返回同一实例。"""
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_reset_settings_creates_new_instance(self):
        """reset_settings() 后 get_settings() 返回新实例。"""
        s1 = get_settings()
        reset_settings()
        s2 = get_settings()
        assert s1 is not s2

    def test_get_settings_no_db_read(self):
        """get_settings() 不触发 DB 读取。"""
        # 即使无数据库文件也应成功
        s = get_settings()
        assert s is not None