"""T1.3 runtime/settings.py — 三层配置唯一入口测试。

验收：单测覆盖三层优先级；所有阈值可从 API 读写。

三层优先级（高→低）：运行期覆盖 → 环境变量 → 默认值。
DB 配置在 lifespan 中加载，通过 setattr 覆盖环境变量/默认值。
"""
from __future__ import annotations

import os
import pytest

from campus_ids.runtime.settings import Settings, get_settings, reset_settings
from tests.conftest import ISOLATION_ENV_VARS


@pytest.fixture(autouse=True)
def _clean_env():
    """每个测试前后清理环境变量和设置单例。

    注意：**必须豁免 `ISOLATION_ENV_VARS`** —— 否则会把 conftest 的
    `_isolate_artifacts` 设置的 CAMPUS_IDS_DATA_DIR 一并清掉，导致后续
    `reset_settings()` 之后的 Settings 又指回项目根（隔离失效）。
    """
    def _purge() -> None:
        for key in list(os.environ.keys()):
            if key.startswith("CAMPUS_IDS_") and key not in ISOLATION_ENV_VARS:
                del os.environ[key]

    _purge()
    reset_settings()
    yield
    _purge()
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


# ── 阈值热更新端到端（T2.12 回归）─────────────────────────────────


class TestThresholdHotReloadEndToEnd:
    """PUT /api/settings 要让「读回值」和「检测器实际阈值」**同时**改变。

    回归背景（2026-09-17 定为 P0）：`set_override()` 只把值塞进 `_overrides`，
    而全项目仅 `.get()` 读该字典，其余读取点一律直接属性访问 →
    接口返回 200 声称成功，`GET /api/settings` 却仍是旧值，
    `_rebuild_rule_detector()` 也读不到新值。

    当时端点级用例**只断言 HTTP 200**，所以测试全绿而功能不成立。本类补上
    "对外可观测行为" 断言：读回值、检测器实例、dual_detector 引用三者都要变。
    """

    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient

        from campus_ids.web_new.app import create_app
        from campus_ids.web_new.security import limiter

        limiter.reset()
        app = create_app()
        with TestClient(app) as c:
            token = c.get("/api/csrf-token").json()["csrf_token"]
            c.cookies.set("csrf_token", token)
            c.headers["X-CSRFToken"] = token
            yield c
        limiter.reset()

    def _put(self, client, key: str, value):
        return client.put("/api/settings", json={"key": key, "value": value})

    def test_put_then_get_reflects_new_value(self, client):
        before = client.get("/api/settings").json()["thresholds"]["ddos_threshold"]
        resp = self._put(client, "ddos_threshold", 12345)
        assert resp.status_code == 200
        after = client.get("/api/settings").json()["thresholds"]["ddos_threshold"]
        assert after == 12345, f"PUT 返回 200 但 GET 仍是 {after}（改前 {before}）"

    def test_detector_actually_uses_new_threshold(self, client):
        """最关键的一条：检测器实例身上的阈值必须真的变了。"""
        assert self._put(client, "ddos_threshold", 4321).status_code == 200
        assert client.app.state.rule_detector.ddos_threshold == 4321

    def test_dual_detector_reference_synced(self, client):
        assert self._put(client, "port_scan_threshold", 77).status_code == 200
        dual = client.app.state.dual_detector
        assert dual.rule_detector is client.app.state.rule_detector
        assert dual.rule_detector.port_scan_threshold == 77

    def test_settings_singleton_attribute_updated(self, client):
        """app.state.settings 是全局单例，字段本身必须被改写。"""
        assert self._put(client, "syn_flood_threshold", 999).status_code == 200
        assert client.app.state.settings.syn_flood_threshold == 999
        assert get_settings().syn_flood_threshold == 999

    def test_brute_force_window_hot_reload(self, client):
        """暴力破解窗口走的是检测器重建路径，单独验一遍。"""
        assert self._put(client, "brute_force_window", 123).status_code == 200
        assert client.app.state.rule_detector.brute_force_window == 123

    def test_all_threshold_keys_round_trip(self, client):
        """threshold_keys 里每一个键都要能写进去并读回来。"""
        keys = sorted(client.get("/api/settings").json()["thresholds"])
        assert keys, "阈值集合为空"
        for key in keys:
            resp = self._put(client, key, 42)
            assert resp.status_code == 200, f"{key} 更新失败: {resp.text}"
            got = client.get("/api/settings").json()["thresholds"][key]
            assert got == 42, f"{key} 未生效（仍是 {got}）"

    def test_unknown_key_rejected(self, client):
        assert self._put(client, "no_such_threshold", 1).status_code == 400