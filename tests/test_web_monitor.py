"""bp_monitor 蓝图测试 — 流量监控、告警查询、SSE 实时推送。"""
import os
import pytest

from campus_ids.web.app import app


@pytest.fixture
def client():
    """Flask test client（禁用认证）。"""
    os.environ.setdefault("CAMPUS_IDS_AUTH_ENABLED", "0")
    os.environ.setdefault("CAMPUS_IDS_LOGIN_ENABLED", "0")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── 流量监控 ──────────────────────────────────────────────────────

class TestTrafficData:
    def test_returns_200(self, client):
        resp = client.get("/api/traffic")
        assert resp.status_code == 200

    def test_returns_json_structure(self, client):
        resp = client.get("/api/traffic")
        data = resp.get_json()
        required_keys = [
            'qps', 'connections', 'alert', 'timestamp',
            'packet_count', 'port_count', 'src_ip_count',
            'syn_packets', 'udp_packets', 'dns_packets',
        ]
        for key in required_keys:
            assert key in data, f"缺少键: {key}"


class TestTrafficHistory:
    def test_returns_200(self, client):
        resp = client.get("/api/traffic/history")
        assert resp.status_code == 200

    def test_returns_json_structure(self, client):
        resp = client.get("/api/traffic/history")
        data = resp.get_json()
        assert "history" in data
        assert "limit" in data
        assert "offset" in data

    def test_with_limit_and_offset(self, client):
        resp = client.get("/api/traffic/history?limit=10&offset=0")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["limit"] == 10
        assert data["offset"] == 0

    def test_limit_capped_at_200(self, client):
        resp = client.get("/api/traffic/history?limit=500")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["limit"] <= 200


# ── 告警查询 ──────────────────────────────────────────────────────

class TestAlerts:
    def test_returns_200(self, client):
        resp = client.get("/api/alerts")
        assert resp.status_code == 200

    def test_returns_json_structure(self, client):
        resp = client.get("/api/alerts")
        data = resp.get_json()
        assert "alerts" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data

    def test_with_level_filter(self, client):
        resp = client.get("/api/alerts?level=high")
        assert resp.status_code == 200

    def test_with_pagination(self, client):
        resp = client.get("/api/alerts?limit=5&offset=0")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["limit"] == 5


# ── SSE 实时推送 ──────────────────────────────────────────────────

class TestSSEStream:
    def test_traffic_stream_headers(self, client):
        """SSE 流应返回正确的 Content-Type。"""
        resp = client.get("/api/stream/traffic")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.content_type

    def test_alert_stream_headers(self, client):
        resp = client.get("/api/stream/alerts")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.content_type

    def test_traffic_stream_has_cache_control(self, client):
        resp = client.get("/api/stream/traffic")
        assert resp.headers.get("Cache-Control") == "no-cache"

    def test_alert_stream_has_cache_control(self, client):
        resp = client.get("/api/stream/alerts")
        assert resp.headers.get("Cache-Control") == "no-cache"