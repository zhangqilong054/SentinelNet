"""T2.13 训练状态收敛 + 破坏性操作保护（回归测试）。

对应 §5.3 R3 的验收条件：

1. **从 `/api/tasks/train/start` 起训练后，`GET /api/models/train/status` 不再是 `idle`**
   —— 这是"两份状态真相"的判据。修之前该端点是 `api/models.py` 的独立状态机，
   完全看不见 TaskRegistry 起的训练。
2. **未带 `confirm` 时训练被拒绝，且产物 md5 不变**。
3. 训练前自动备份三个会被覆盖的产物。
4. `dataset` / `quick` 参数**真的透传**到训练链路（此前被静默丢弃）。

⚠️ 本文件一律用 monkeypatch 把 `ModelService.train` 换成记录型假实现 ——
真实训练会跑分钟级并覆盖三个无副本的产物。这也是为什么断言里要检查
"参数传到了 fake"，而不是检查"训练完成了"。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from campus_ids.runtime.settings import get_settings, reset_settings
from campus_ids.services.model_service import ModelService
from campus_ids.web_new.app import create_app


@pytest.fixture(autouse=True)
def _clean_env():
    import os

    from campus_ids.web_new.security import limiter

    def _clear():
        os.environ.pop("CAMPUS_IDS_API_TOKEN", None)
        os.environ.pop("CAMPUS_IDS_AUTH_ENABLED", None)
        reset_settings()
        limiter.reset()

    _clear()
    yield
    _clear()


@pytest.fixture()
def train_calls(monkeypatch):
    """把真实训练换成记录型假实现，返回调用记录列表。"""
    calls: list[dict] = []

    def _fake_train(self, dataset: str = "auto", quick: bool = False) -> dict:
        calls.append({"dataset": dataset, "quick": quick})
        return {"status": "started", "fake": True}

    monkeypatch.setattr(ModelService, "train", _fake_train)
    return calls


@pytest.fixture()
def client(train_calls):
    """带 lifespan 的客户端（task_registry 已初始化）。"""
    with TestClient(create_app()) as c:
        yield c


def _csrf(client: TestClient) -> dict:
    token = client.get("/api/csrf-token").json()["csrf_token"]
    client.cookies.set("csrf_token", token)
    return {"X-CSRFToken": token}


# ── 1. 单一真相源 ─────────────────────────────────────────────────


class TestSingleSourceOfTruth:
    def test_idle_before_any_training(self, client):
        st = client.get("/api/models/train/status").json()
        assert st["status"] == "idle"
        assert st["progress"] == 0.0

    def test_task_start_reflects_in_models_status(self, client):
        """核心判据：任务端点起的训练，状态端点必须看得见。"""
        headers = _csrf(client)
        started = client.post("/api/tasks/train/start", json={"duration": 30}, headers=headers)
        assert started.status_code == 200

        st = client.get("/api/models/train/status").json()
        assert st["status"] == "training", (
            "两份状态真相仍在：TaskRegistry 已启动 train，"
            f"/api/models/train/status 却报 {st['status']}"
        )

    def test_task_stop_reflects_in_models_status(self, client):
        headers = _csrf(client)
        client.post("/api/tasks/train/start", json={"duration": 30}, headers=headers)
        assert client.get("/api/models/train/status").json()["status"] == "training"

        client.post("/api/tasks/train/stop", headers=headers)
        # 停止是异步的：轮询直到任务退出
        deadline = time.time() + 5
        while time.time() < deadline:
            if client.get("/api/models/train/status").json()["status"] != "training":
                break
            time.sleep(0.1)
        assert client.get("/api/models/train/status").json()["status"] in ("completed", "idle")

    def test_progress_is_derived_not_hardcoded(self, client):
        """progress 由任务窗口 elapsed/duration 算出，不是恒 0.0。"""
        headers = _csrf(client)
        client.post("/api/tasks/train/start", json={"duration": 20}, headers=headers)

        progress = 0.0
        deadline = time.time() + 3
        while time.time() < deadline:
            st = client.get("/api/models/train/status").json()
            progress = st["progress"]
            if progress > 0:
                break
            time.sleep(0.1)
        assert progress > 0, "progress 恒为 0 —— 说明没有从 TaskRegistry 取 elapsed"
        assert progress <= 1.0

    def test_status_exposes_task_window_fields(self, client):
        headers = _csrf(client)
        client.post("/api/tasks/train/start", json={"duration": 30}, headers=headers)
        st = client.get("/api/models/train/status").json()
        assert st["duration_seconds"] == 30
        assert st["elapsed_seconds"] is not None


# ── 2. 破坏性保护 ─────────────────────────────────────────────────


class TestDestructiveGuards:
    def test_train_without_confirm_rejected_and_products_untouched(self, client, train_calls):
        data_dir = get_settings().data_dir
        marker = data_dir / "model.pkl"
        marker.write_bytes(b"original-model-bytes")
        before = marker.read_bytes()

        headers = _csrf(client)
        resp = client.post(
            "/api/models/train",
            json={"dataset": "auto", "epochs": 10},
            headers=headers,
        )
        assert resp.status_code == 400
        assert "confirm" in resp.json()["detail"]
        assert train_calls == [], "未确认却仍调用了训练链路"
        assert marker.read_bytes() == before, "未确认的训练改写了 model.pkl"

    def test_train_with_confirm_starts_and_backs_up(self, client, train_calls):
        data_dir = get_settings().data_dir
        (data_dir / "model.pkl").write_bytes(b"original-model-bytes")
        (data_dir / "evaluation_report.txt").write_text("old report", encoding="utf-8")

        headers = _csrf(client)
        resp = client.post(
            "/api/models/train",
            json={"dataset": "synthetic_demo", "epochs": 2, "confirm": True},
            headers=headers,
        )
        assert resp.status_code == 200
        assert "training_backup" in resp.json()["message"]

        backups = list((data_dir / "training_backup").glob("*/model.pkl"))
        assert backups, "未生成产物备份"
        assert backups[0].read_bytes() == b"original-model-bytes"

        reports = list((data_dir / "training_backup").glob("*/evaluation_report.txt"))
        assert reports, "evaluation_report.txt 未备份"
        assert "confusion_matrix.png" not in resp.json()["message"]  # 不存在的产物不该被列

    def test_second_start_conflicts(self, client, train_calls):
        headers = _csrf(client)
        body = {"dataset": "auto", "epochs": 10, "confirm": True}
        assert client.post("/api/models/train", json=body, headers=headers).status_code == 200
        second = client.post("/api/models/train", json=body, headers=headers)
        assert second.status_code == 409, "重复启动未返回 409 —— 判据不是 TaskRegistry 状态"

    def test_confirm_required_even_when_products_absent(self, client, train_calls):
        """产物不存在也要确认 —— 训练本身还会写新产物。"""
        headers = _csrf(client)
        resp = client.post("/api/models/train", json={"dataset": "auto"}, headers=headers)
        assert resp.status_code == 400


# ── 3. 参数真的透传（此前被静默丢弃）───────────────────────────────


class TestParameterPassthrough:
    def test_dataset_and_quick_forwarded(self, client, train_calls):
        headers = _csrf(client)
        resp = client.post(
            "/api/models/train",
            json={"dataset": "cicids2017", "epochs": 2, "confirm": True},
            headers=headers,
        )
        assert resp.status_code == 200

        deadline = time.time() + 3
        while time.time() < deadline and not train_calls:
            time.sleep(0.05)

        assert train_calls, "训练链路未被调用"
        assert train_calls[0]["dataset"] == "cicids2017", "dataset 被静默丢弃"
        assert train_calls[0]["quick"] is True, "epochs<=3 未映射为 quick"

    def test_high_epochs_is_not_quick(self, client, train_calls):
        headers = _csrf(client)
        client.post(
            "/api/models/train",
            json={"dataset": "auto", "epochs": 20, "confirm": True},
            headers=headers,
        )
        deadline = time.time() + 3
        while time.time() < deadline and not train_calls:
            time.sleep(0.05)
        assert train_calls[0]["quick"] is False
