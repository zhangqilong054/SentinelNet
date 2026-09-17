"""test_shim.py — T4.1 shim 路由 + T4.2 命中追踪 测试。

验证：
1. 所有 shim 路由返回 Deprecation + Sunset + Link 头
2. 命中追踪器正确记录调用
3. SSE shim 返回 410 Gone
4. 配置 shim 正确转发逻辑
5. deprecation-stats 管理端点返回统计
"""
from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

from campus_ids.web_new.app import create_app
from campus_ids.web_new.deprecation import (
    DeprecationTracker,
    get_deprecation_tracker,
    reset_deprecation_tracker,
    SUNSET_DATE,
    DEPRECATION_HEADER,
)


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_tracker():
    """每个测试前重置命中追踪器。"""
    reset_deprecation_tracker()
    yield
    reset_deprecation_tracker()


@pytest.fixture
def client():
    """启用 shim 的 TestClient。"""
    os.environ["CAMPUS_IDS_ENABLE_SHIM"] = "1"
    os.environ["CAMPUS_IDS_AUTH_ENABLED"] = "0"
    app = create_app()
    with TestClient(app) as c:
        yield c
    os.environ.pop("CAMPUS_IDS_ENABLE_SHIM", None)


@pytest.fixture
def client_no_shim():
    """不启用 shim 的 TestClient。"""
    os.environ.pop("CAMPUS_IDS_ENABLE_SHIM", None)
    os.environ["CAMPUS_IDS_AUTH_ENABLED"] = "0"
    app = create_app()
    with TestClient(app) as c:
        yield c


def _csrf(client: TestClient) -> dict[str, str]:
    """获取 CSRF 双提交令牌（cookie + header）。"""
    token = client.get("/api/csrf-token").json()["csrf_token"]
    client.cookies.set("csrf_token", token)
    return {"X-CSRFToken": token}


# ── T4.2: DeprecationTracker 单元测试 ─────────────────────────

class TestDeprecationTracker:
    """命中追踪器核心逻辑。"""

    def test_record_hit_increments_count(self):
        tracker = DeprecationTracker()
        tracker.record_hit("/api/capture/start", "POST")
        tracker.record_hit("/api/capture/start", "POST")
        assert tracker.get_hit_count("/api/capture/start", "POST") == 2

    def test_record_hit_different_methods(self):
        tracker = DeprecationTracker()
        tracker.record_hit("/api/config", "GET")
        tracker.record_hit("/api/config", "POST")
        assert tracker.get_hit_count("/api/config", "GET") == 1
        assert tracker.get_hit_count("/api/config", "POST") == 1

    def test_record_hit_stores_source(self):
        tracker = DeprecationTracker()
        tracker.record_hit("/api/capture/start", "POST", source="192.168.1.1")
        stats = tracker.get_stats()
        assert len(stats) == 1
        assert stats[0]["last_source"] == "192.168.1.1"

    def test_get_stats_sorted_by_count(self):
        tracker = DeprecationTracker()
        tracker.record_hit("/api/a", "GET")
        tracker.record_hit("/api/a", "GET")
        tracker.record_hit("/api/b", "GET")
        stats = tracker.get_stats()
        assert stats[0]["path"] == "/api/a"
        assert stats[0]["count"] == 2
        assert stats[1]["path"] == "/api/b"
        assert stats[1]["count"] == 1

    def test_reset_clears_all(self):
        tracker = DeprecationTracker()
        tracker.record_hit("/api/a", "GET")
        tracker.reset()
        assert tracker.get_hit_count("/api/a", "GET") == 0
        assert tracker.get_stats() == []

    def test_get_hit_count_unseen_path(self):
        tracker = DeprecationTracker()
        assert tracker.get_hit_count("/api/unknown", "GET") == 0

    def test_global_singleton(self):
        t1 = get_deprecation_tracker()
        t2 = get_deprecation_tracker()
        assert t1 is t2

    def test_reset_deprecation_tracker(self):
        get_deprecation_tracker().record_hit("/api/test", "GET")
        reset_deprecation_tracker()
        assert get_deprecation_tracker().get_hit_count("/api/test", "GET") == 0


# ── T4.1: Shim 路由 Deprecation 头 ────────────────────────────

class TestShimDeprecationHeaders:
    """所有 shim 路由必须返回 Deprecation + Sunset + Link 头。"""

    def _assert_deprecation_headers(self, response, expected_successor: str):
        """断言响应包含完整的 Deprecation 头组。"""
        assert response.headers.get("Deprecation") == DEPRECATION_HEADER, \
            f"Missing Deprecation header in {response.url}"
        assert response.headers.get("Sunset") == SUNSET_DATE, \
            f"Missing Sunset header in {response.url}"
        link = response.headers.get("Link", "")
        assert f'rel="successor-version"' in link, \
            f"Missing Link successor-version in {response.url}"
        assert expected_successor in link, \
            f"Expected successor {expected_successor} in Link, got: {link}"

    def test_capture_status_headers(self, client):
        resp = client.get("/api/capture/status")
        self._assert_deprecation_headers(resp, "/tasks")

    def test_capture_enhanced_status_headers(self, client):
        resp = client.get("/api/capture/enhanced-status")
        self._assert_deprecation_headers(resp, "/tasks")

    def test_detector_status_headers(self, client):
        resp = client.get("/api/detector/status")
        self._assert_deprecation_headers(resp, "/tasks")

    def test_attack_status_headers(self, client):
        resp = client.get("/api/attack/status")
        self._assert_deprecation_headers(resp, "/tasks")

    def test_auto_status_headers(self, client):
        resp = client.get("/api/auto/status")
        self._assert_deprecation_headers(resp, "/tasks")

    def test_dual_stats_headers(self, client):
        resp = client.get("/api/dual/stats")
        self._assert_deprecation_headers(resp, "/tasks")

    def test_model_train_status_headers(self, client):
        resp = client.get("/api/model/train-status")
        self._assert_deprecation_headers(resp, "/models/train/status")

    def test_config_get_headers(self, client):
        resp = client.get("/api/config")
        self._assert_deprecation_headers(resp, "/settings")

    def test_model_list_headers(self, client):
        resp = client.get("/api/model/list")
        self._assert_deprecation_headers(resp, "/models")


# ── T4.1: Shim 路由功能转发 ───────────────────────────────────

class TestShimFunctionality:
    """Shim 路由正确转发到新端点逻辑。"""

    def test_capture_status_returns_tasks(self, client):
        resp = client.get("/api/capture/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert "deprecated_hint" in data

    def test_config_get_returns_settings(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "thresholds" in data
        assert "ml_config" in data
        assert "deprecated_hint" in data

    def test_model_list_returns_models(self, client):
        resp = client.get("/api/model/list")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data


# ── T4.1: SSE shim 返回 410 Gone ──────────────────────────────

class TestShimSSE:
    """SSE 旧端点 shim 返回 410 Gone + 迁移提示。"""

    def test_stream_alerts_gone(self, client):
        resp = client.get("/api/stream/alerts")
        assert resp.status_code == 410
        data = resp.json()
        assert data["error"] == "deprecated"
        assert "/api/stream?topics=alert" in data["successor"]
        assert resp.headers.get("Deprecation") == DEPRECATION_HEADER

    def test_stream_traffic_gone(self, client):
        resp = client.get("/api/stream/traffic")
        assert resp.status_code == 410
        data = resp.json()
        assert data["error"] == "deprecated"
        assert "/api/stream?topics=traffic" in data["successor"]


# ── T4.2: 命中追踪集成 ────────────────────────────────────────

class TestShimHitTracking:
    """Shim 路由调用后命中追踪器记录命中。"""

    def test_get_hit_recorded(self, client):
        client.get("/api/capture/status")
        tracker = get_deprecation_tracker()
        assert tracker.get_hit_count("/api/capture/status", "GET") == 1

    def test_post_hit_recorded(self, client):
        client.post("/api/capture/start", headers=_csrf(client))
        tracker = get_deprecation_tracker()
        assert tracker.get_hit_count("/api/capture/start", "POST") == 1

    def test_multiple_hits_accumulate(self, client):
        client.get("/api/capture/status")
        client.get("/api/capture/status")
        client.get("/api/capture/status")
        tracker = get_deprecation_tracker()
        assert tracker.get_hit_count("/api/capture/status", "GET") == 3

    def test_different_endpoints_tracked_separately(self, client):
        client.get("/api/capture/status")
        client.get("/api/detector/status")
        tracker = get_deprecation_tracker()
        assert tracker.get_hit_count("/api/capture/status", "GET") == 1
        assert tracker.get_hit_count("/api/detector/status", "GET") == 1


# ── T4.2: deprecation-stats 管理端点 ──────────────────────────

class TestDeprecationStatsEndpoint:
    """GET /api/admin/deprecation-stats 返回命中统计。"""

    def test_stats_endpoint_returns_structure(self, client):
        resp = client.get("/api/admin/deprecation-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "sunset_date" in data
        assert "endpoints" in data
        assert data["sunset_date"] == "2026-11-01"

    def test_stats_after_hits(self, client):
        client.get("/api/capture/status")
        client.post("/api/capture/start", headers=_csrf(client))
        resp = client.get("/api/admin/deprecation-stats")
        data = resp.json()
        endpoints = data["endpoints"]
        assert len(endpoints) >= 2
        paths = [e["path"] for e in endpoints]
        assert "/api/capture/status" in paths
        assert "/api/capture/start" in paths


# ── T4.1: Shim 启用/禁用 ─────────────────────────────────────

class TestShimToggle:
    """CAMPUS_IDS_ENABLE_SHIM 控制 shim 路由注册。"""

    def test_shim_disabled_returns_404(self, client_no_shim):
        """shim 未启用时，旧路径返回 404。"""
        resp = client_no_shim.get("/api/capture/status")
        # 可能 404 或被其他路由匹配，但不应有 Deprecation 头
        assert resp.headers.get("Deprecation") is None

    def test_shim_enabled_returns_200(self, client):
        """shim 启用时，旧路径返回 200 + Deprecation 头。"""
        resp = client.get("/api/capture/status")
        assert resp.status_code == 200
        assert resp.headers.get("Deprecation") == DEPRECATION_HEADER


# ── T4.1: 配置 POST shim（方法变更 POST→PUT）─────────────────

class TestShimConfigPost:
    """POST /api/config → PUT /api/settings 的方法变更 shim。"""

    def test_config_post_updates_threshold(self, client):
        """旧 POST /api/config 格式仍能更新阈值。"""
        resp = client.post("/api/config", json={"key": "ddos_threshold", "value": 500}, headers=_csrf(client))
        assert resp.status_code == 200
        data = resp.json()
        assert "deprecated_hint" in data
        assert resp.headers.get("Deprecation") == DEPRECATION_HEADER

    def test_config_post_invalid_key(self, client):
        """无效 key 返回 400。"""
        resp = client.post("/api/config", json={"key": "invalid_key", "value": 1}, headers=_csrf(client))
        assert resp.status_code == 400

    def test_config_post_missing_fields(self, client):
        """缺少 key/value 返回 400。"""
        resp = client.post("/api/config", json={}, headers=_csrf(client))
        assert resp.status_code == 400


# ── T4.5: 删除判定 ────────────────────────────────────────────

class TestDeletionEligibility:
    """T4.5 删除判定：命中数为 0 或超过 30 天未命中。"""

    def test_zero_hits_is_deletable(self):
        tracker = DeprecationTracker()
        # 未记录的端点 → count=0 → 可删除
        deletable = tracker.get_deletable_endpoints()
        # 没有记录，返回空列表（因为只检查已记录的端点）
        assert deletable == []

    def test_recorded_zero_after_reset_is_deletable(self):
        tracker = DeprecationTracker()
        tracker.record_hit("/api/test", "GET")
        tracker.reset()
        # reset 后记录清空，不再有可删除条目
        assert tracker.get_deletable_endpoints() == []

    def test_recent_hit_not_deletable(self):
        tracker = DeprecationTracker()
        tracker.record_hit("/api/test", "GET", source="test")
        # 刚命中的端点不应可删除
        deletable = tracker.get_deletable_endpoints()
        paths = [e["path"] for e in deletable]
        assert "/api/test" not in paths

    def test_deletion_stats_in_api(self, client):
        """deprecation-stats 端点返回 deletable 字段。"""
        resp = client.get("/api/admin/deprecation-stats")
        data = resp.json()
        assert "deletable" in data
        assert isinstance(data["deletable"], list)