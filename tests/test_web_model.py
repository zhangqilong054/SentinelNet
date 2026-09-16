"""bp_model 蓝图端点测试 — 模型管理/训练/全流程/演示模式。"""
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


# ── 模型列表 ──────────────────────────────────────────────────────

class TestModelList:
    def test_returns_200(self, client):
        resp = client.get("/api/model/list")
        assert resp.status_code == 200

    def test_returns_json_structure(self, client):
        resp = client.get("/api/model/list")
        data = resp.get_json()
        assert "runs" in data
        assert "count" in data
        assert isinstance(data["runs"], list)


# ── 模型训练 ──────────────────────────────────────────────────────

class TestModelTrain:
    def test_train_starts(self, client):
        """训练请求应返回 200（异步启动）。"""
        resp = client.post("/api/model/train", json={"quick": True})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        # 等待训练完成或超时
        import time
        for _ in range(30):
            status_resp = client.get("/api/model/train-status")
            status = status_resp.get_json()
            if not status["running"]:
                break
            time.sleep(0.5)

    def test_train_status_returns_200(self, client):
        resp = client.get("/api/model/train-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "running" in data
        assert "progress" in data


# ── 一键全流程 ────────────────────────────────────────────────────

class TestAutoStart:
    def test_returns_200_or_409(self, client):
        """全流程启动返回 200（成功）或 409（已在运行）。"""
        resp = client.post("/api/auto/start", json={"duration": 10})
        assert resp.status_code in (200, 409)
        if resp.status_code == 200:
            data = resp.get_json()
            assert data["status"] == "success"


class TestAutoStatus:
    def test_returns_200(self, client):
        resp = client.get("/api/auto/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "running" in data


# ── 演示模式 ──────────────────────────────────────────────────────

class TestDemoStart:
    def test_returns_200_or_409(self, client):
        """演示模式启动返回 200 或 409（攻击模拟已在运行）。"""
        # 先确保没有攻击模拟在运行
        client.post("/api/attack/stop")
        resp = client.post("/api/demo/start", json={"duration": 5})
        assert resp.status_code in (200, 409)
        # 清理
        client.post("/api/attack/stop")
        client.post("/api/capture/stop")