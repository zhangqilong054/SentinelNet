"""bp_admin 蓝图端点测试 — 配置/数据管理/攻击模拟/自检/健康检查。"""
import json
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


# ── 配置管理 ──────────────────────────────────────────────────────

class TestConfigGet:
    def test_returns_200(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200

    def test_returns_json(self, client):
        resp = client.get("/api/config")
        data = resp.get_json()
        assert isinstance(data, dict)
        assert "ddos_threshold" in data

    def test_has_expected_keys(self, client):
        resp = client.get("/api/config")
        data = resp.get_json()
        expected = {"port", "refresh_interval", "ddos_threshold", "port_scan_threshold",
                     "syn_flood_threshold", "udp_flood_threshold", "brute_force_threshold",
                     "brute_force_window", "lateral_movement_threshold"}
        assert expected.issubset(set(data.keys()))


class TestConfigPost:
    def test_update_single_threshold(self, client):
        resp = client.post("/api/config", json={"ddos_threshold": 9999})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        assert data["config"]["ddos_threshold"] == 9999

    def test_update_multiple_thresholds(self, client):
        resp = client.post("/api/config", json={
            "ddos_threshold": 500,
            "port_scan_threshold": 200,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["config"]["ddos_threshold"] == 500
        assert data["config"]["port_scan_threshold"] == 200

    def test_invalid_json_returns_400(self, client):
        resp = client.post("/api/config", data="not json", content_type="application/json")
        assert resp.status_code == 400

    def test_empty_body_is_valid(self, client):
        """空 JSON body 不更新任何配置，返回成功。"""
        resp = client.post("/api/config", json={})
        assert resp.status_code == 200


# ── 数据管理 ──────────────────────────────────────────────────────

class TestSaveData:
    def test_returns_200(self, client):
        resp = client.post("/api/save")
        assert resp.status_code == 200

    def test_returns_success(self, client):
        resp = client.post("/api/save")
        data = resp.get_json()
        assert data["status"] == "success"


class TestCleanup:
    def test_returns_200(self, client):
        resp = client.post("/api/cleanup", json={"days": 7})
        assert resp.status_code == 200

    def test_default_days_is_7(self, client):
        resp = client.post("/api/cleanup", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"

    def test_invalid_days_uses_default(self, client):
        resp = client.post("/api/cleanup", json={"days": -1})
        assert resp.status_code == 200

    def test_non_int_days_uses_default(self, client):
        resp = client.post("/api/cleanup", json={"days": "abc"})
        assert resp.status_code == 200


# ── 攻击模拟 ──────────────────────────────────────────────────────

class TestAttackStart:
    def test_start_with_default_params(self, client):
        """启动攻击模拟（默认类型 all，30秒）。"""
        resp = client.post("/api/attack/start", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        # 清理：停止攻击模拟
        client.post("/api/attack/stop")

    def test_start_with_type(self, client):
        resp = client.post("/api/attack/start", json={"type": "syn_flood", "duration": 5})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["type"] == "syn_flood"
        assert data["duration"] == 5
        client.post("/api/attack/stop")

    def test_invalid_attack_type(self, client):
        resp = client.post("/api/attack/start", json={"type": "invalid_type"})
        assert resp.status_code == 400

    def test_duplicate_start_returns_409(self, client):
        """已在运行时再次启动返回 409。"""
        client.post("/api/attack/start", json={"type": "all", "duration": 5})
        resp2 = client.post("/api/attack/start", json={"type": "all", "duration": 5})
        assert resp2.status_code == 409
        client.post("/api/attack/stop")


class TestAttackStop:
    def test_stop_when_not_running(self, client):
        """未运行时停止返回成功。"""
        resp = client.post("/api/attack/stop")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"

    def test_stop_when_running(self, client):
        client.post("/api/attack/start", json={"type": "all", "duration": 30})
        resp = client.post("/api/attack/stop")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["running"] is False


class TestAttackStatus:
    def test_returns_200(self, client):
        resp = client.get("/api/attack/status")
        assert resp.status_code == 200

    def test_has_required_fields(self, client):
        resp = client.get("/api/attack/status")
        data = resp.get_json()
        assert "running" in data
        assert "type" in data
        assert "elapsed_seconds" in data

    def test_not_running_fields(self, client):
        resp = client.get("/api/attack/status")
        data = resp.get_json()
        assert data["running"] is False
        assert data["type"] == ""


# ── 环境自检 ──────────────────────────────────────────────────────

class TestApiCheck:
    def test_returns_200(self, client):
        resp = client.get("/api/check")
        assert resp.status_code == 200

    def test_has_required_sections(self, client):
        resp = client.get("/api/check")
        data = resp.get_json()
        assert "ok" in data
        assert "python" in data
        assert "dependencies" in data
        assert "capture" in data
        assert "model_files" in data
        assert "data_files" in data

    def test_python_version_ok(self, client):
        resp = client.get("/api/check")
        data = resp.get_json()
        assert data["python"]["ok"] is True
        assert "version" in data["python"]


# ── 健康检查 ──────────────────────────────────────────────────────

class TestApiHealth:
    def test_returns_200(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_has_required_fields(self, client):
        resp = client.get("/api/health")
        data = resp.get_json()
        assert "status" in data
        assert "timestamp" in data
        assert "uptime_seconds" in data
        assert "components" in data

    def test_components_present(self, client):
        resp = client.get("/api/health")
        data = resp.get_json()
        comps = data["components"]
        assert "database" in comps
        assert "capture" in comps
        assert "ml_model" in comps
        assert "sse" in comps

    def test_uptime_positive(self, client):
        resp = client.get("/api/health")
        data = resp.get_json()
        assert data["uptime_seconds"] >= 0