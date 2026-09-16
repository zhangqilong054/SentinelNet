"""bp_capture 蓝图端点测试 — 抓包/检测/TLS/双引擎/载荷检测。"""
import os
import pytest

from campus_ids.web.app import app


@pytest.fixture
def client():
    """Flask test client（禁用认证和CSRF）。"""
    os.environ.setdefault("CAMPUS_IDS_AUTH_ENABLED", "0")
    os.environ.setdefault("CAMPUS_IDS_LOGIN_ENABLED", "0")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c


# ── 抓包控制 ──────────────────────────────────────────────────────

class TestCaptureStart:
    def test_returns_200(self, client):
        resp = client.post("/api/capture/start")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        assert data["running"] is True
        # 清理
        client.post("/api/capture/stop")


class TestCaptureStop:
    def test_returns_200(self, client):
        resp = client.post("/api/capture/stop")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["running"] is False


class TestCaptureStatus:
    def test_returns_200(self, client):
        resp = client.get("/api/capture/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "running" in data


# ── 检测节拍 ──────────────────────────────────────────────────────

class TestDetectorStart:
    def test_returns_200(self, client):
        resp = client.post("/api/detector/start")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["running"] is True
        # 清理
        client.post("/api/detector/stop")


class TestDetectorStop:
    def test_returns_200(self, client):
        resp = client.post("/api/detector/stop")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["running"] is False


class TestDetectorStatus:
    def test_returns_200(self, client):
        resp = client.get("/api/detector/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "running" in data


# ── 增强抓包 ──────────────────────────────────────────────────────

class TestEnhancedCapture:
    def test_start_returns_200(self, client):
        resp = client.post("/api/capture/start-enhanced", json={"duration": 10})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        # 清理
        client.post("/api/capture/stop-enhanced")

    def test_stop_returns_200(self, client):
        resp = client.post("/api/capture/stop-enhanced")
        assert resp.status_code == 200

    def test_status_returns_200(self, client):
        resp = client.get("/api/capture/enhanced-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "running" in data


# ── TLS 分析 ──────────────────────────────────────────────────────

class TestTlsStats:
    def test_returns_200(self, client):
        resp = client.get("/api/tls/stats")
        assert resp.status_code == 200


class TestTlsSuspicious:
    def test_returns_200(self, client):
        resp = client.get("/api/tls/suspicious")
        assert resp.status_code == 200

    def test_with_limit(self, client):
        resp = client.get("/api/tls/suspicious?limit=10")
        assert resp.status_code == 200


# ── 双引擎检测 ────────────────────────────────────────────────────

class TestDualStats:
    def test_returns_200(self, client):
        resp = client.get("/api/dual/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "total_alerts" in data
        assert "attack_type_distribution" in data


class TestDualStop:
    def test_returns_200(self, client):
        resp = client.post("/api/dual/stop")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ml_running"] is False


class TestDualLoad:
    def test_invalid_which_param(self, client):
        resp = client.post("/api/dual/load", json={"which": "invalid"})
        assert resp.status_code == 400

    def test_invalid_run_id(self, client):
        resp = client.post("/api/dual/load", json={"run_id": ""})
        assert resp.status_code == 400

    def test_invalid_model_path(self, client):
        resp = client.post("/api/dual/load", json={"model_path": ""})
        assert resp.status_code == 400


# ── 载荷检测 ──────────────────────────────────────────────────────

class TestPayloadCheck:
    def test_empty_payload(self, client):
        resp = client.post("/api/payload/check", json={"payload": ""})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["is_anomaly"] is False
        assert data["alerts"] == []

    def test_normal_payload(self, client):
        resp = client.post("/api/payload/check", json={"payload": "hello world"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "is_anomaly" in data
        assert "payload_length" in data

    def test_invalid_payload_type(self, client):
        resp = client.post("/api/payload/check", json={"payload": 123})
        assert resp.status_code == 400

    def test_sqli_payload(self, client):
        resp = client.post("/api/payload/check", json={"payload": "' OR 1=1 --"})
        assert resp.status_code == 200
        data = resp.get_json()
        # SQL 注入应被检测到
        assert data["is_anomaly"] is True