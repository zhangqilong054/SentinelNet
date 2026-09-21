# -*- coding: utf-8 -*-
"""`web/api/models.py` 行为测试（R5 覆盖率补齐）。

改动前覆盖率 **72%** —— 已覆盖的只是"列表 + 正常启停"。未覆盖的是**全部保护逻辑**，
而 T2.13 收敛的核心恰恰就是这些保护：

- `confirm=true` 必填（覆盖训练产物前的确认位）
- 训练前自动备份三个产物
- `train` 任务状态取自 `TaskRegistry`（不是本地状态机）
- 状态映射与 `progress` 计算（由 elapsed/duration 算出，不是硬编码 0.0）

## 替身策略

`TaskRegistry` 换成本文件的 `FakeRegistry`，挂在 `app.state.task_registry` 上。
这样仍走真实 HTTP 层（含 schema 校验、CSRF、错误协议），但训练状态可以精确编排。
不用真注册表是因为真注册表**无法**在中途返回 `error` / `already_running`,
而这些正是回滚与 409/503 分流的判据。
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from campus_ids.runtime.settings import get_settings, reset_settings
from campus_ids.web.api.models import TRAINING_PRODUCTS, backup_training_products
from campus_ids.web.app import create_app

TRAIN = "train"


# ── 替身 ──────────────────────────────────────────────────────────


class FakeRegistry:
    """`TaskRegistry` 替身：`status` / `start` 均可编排。

    `base` 是每条 status 的基础字段（默认 `{"status": "idle"}`）。
    传 `base={}` 可以模拟"注册表根本没回 status 字段"的退化返回。
    """

    def __init__(self, *, statuses=None, start_results=None, unknown=(), base=None) -> None:
        self.base: dict = {"status": "idle"} if base is None else dict(base)
        self.statuses: dict[str, dict] = dict(statuses or {})
        self.start_results: dict[str, list[dict]] = dict(start_results or {})
        self.unknown = set(unknown)
        self.start_calls: list[tuple[str, dict]] = []

    def status(self, name: str) -> dict:
        if name in self.unknown:
            raise KeyError(name)
        return {"name": name, **self.base, **self.statuses.get(name, {})}

    def start(self, name: str, duration=None, **kwargs) -> dict:
        self.start_calls.append((name, kwargs))
        scripted = self.start_results.get(name)
        if scripted:
            return scripted[0]
        return {"status": "started"}


# ── fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_env():
    def _clear() -> None:
        os.environ.pop("CAMPUS_IDS_API_TOKEN", None)
        os.environ["CAMPUS_IDS_AUTH_ENABLED"] = "0"
        reset_settings()
        from campus_ids.web.security import limiter

        limiter.reset()

    _clear()
    yield
    _clear()


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def registry(app):
    """替换掉 lifespan 建的真注册表。"""
    fake = FakeRegistry()
    app.state.task_registry = fake
    return fake


def _csrf(client: TestClient) -> dict[str, str]:
    token = client.get("/api/csrf-token").json()["csrf_token"]
    client.cookies.set("csrf_token", token)
    return {"X-CSRFToken": token}


def _models_dir():
    return get_settings().data_dir / "models"


def _write_registry(runs: list[dict]) -> None:
    _models_dir().mkdir(parents=True, exist_ok=True)
    (_models_dir() / "registry.json").write_text(
        json.dumps(runs, ensure_ascii=False), encoding="utf-8"
    )


def _read_registry_file() -> list[dict]:
    path = _models_dir() / "registry.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


# ══ GET /api/models ═══════════════════════════════════════════════


class TestListModels:
    def test_missing_registry_returns_empty_list(self, client):
        """注册表不存在是**正常**状态（尚无训练产物），不是错误。"""
        resp = client.get("/api/models")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"models": []}

    def test_reads_and_maps_registry_entries(self, client):
        _write_registry([
            {"run_id": "run-001", "model_type": "random_forest",
             "created_at": "2026-09-17 10:00:00", "metrics": {"accuracy": 0.97}},
        ])

        models = client.get("/api/models").json()["models"]

        assert len(models) == 1
        # is_best：2026-09-19 UI 优化新增透出（前端「当前最佳」徽标），缺省 False
        assert models[0] == {
            "name": "run-001",
            "version": "random_forest",
            "created_at": "2026-09-17 10:00:00",
            "metrics": {"accuracy": 0.97},
            "is_best": False,
        }

    def test_is_best_passthrough_from_registry(self, client):
        """registry 的 is_best 逐条透出（至多一个 True 由 train.py 保证）。"""
        _write_registry([
            {"run_id": "run-a", "model_type": "rf", "created_at": "2026-09-18 10:00:00",
             "metrics": {"f1_score": 0.99}, "is_best": True},
            {"run_id": "run-b", "model_type": "rf", "created_at": "2026-09-19 10:00:00",
             "metrics": {"f1_score": 0.97}, "is_best": False},
        ])

        models = client.get("/api/models").json()["models"]

        by_name = {m["name"]: m["is_best"] for m in models}
        assert by_name == {"run-a": True, "run-b": False}

    def test_missing_fields_fall_back_to_unknown(self, client):
        """字段缺失不得让整个列表 500 —— 单条脏数据不该毁掉整个端点。"""
        _write_registry([{}])

        model = client.get("/api/models").json()["models"][0]

        assert model["name"] == "unknown"
        assert model["version"] == "unknown"
        assert model["created_at"] == ""
        assert model["metrics"] == {}

    def test_corrupt_registry_returns_empty_not_500(self, client):
        """注册表损坏（非法 JSON）→ 空列表 + 日志，而不是 500。"""
        _models_dir().mkdir(parents=True, exist_ok=True)
        (_models_dir() / "registry.json").write_text("{ 这不是 JSON", encoding="utf-8")

        resp = client.get("/api/models")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"models": []}


# ══ POST /api/models/train ════════════════════════════════════════


class TestStartTrainingProtection:
    def test_missing_confirm_is_400_and_does_not_start(self, client, registry):
        """核心保护位：不带 confirm=true 时**不得**启动训练。

        训练会覆盖 model.pkl / evaluation_report.txt / confusion_matrix.png，
        这是唯一的"误调用即破坏产物"的入口。
        """
        resp = client.post("/api/models/train", json={"dataset": "auto"}, headers=_csrf(client))

        assert resp.status_code == 400, resp.text
        assert resp.json()["error"] == "VALIDATION_ERROR"
        assert registry.start_calls == [], "未确认时不得启动训练"

    def test_confirm_false_is_also_rejected(self, client, registry):
        resp = client.post(
            "/api/models/train", json={"confirm": False}, headers=_csrf(client)
        )
        assert resp.status_code == 400
        assert registry.start_calls == []

    def test_already_running_is_409(self, client, registry):
        registry.statuses[TRAIN] = {"status": "running"}

        resp = client.post(
            "/api/models/train", json={"confirm": True}, headers=_csrf(client)
        )

        assert resp.status_code == 409, resp.text
        assert resp.json()["error"] == "CONFLICT"
        assert registry.start_calls == [], "已运行时不得重复启动"

    def test_conflict_judgement_comes_from_task_registry(self, app, client):
        """已运行的判据必须来自 `TaskRegistry` —— 这是 T2.13 收敛的验收点。

        改动前 `api/models.py` 自持一个 `_train_status` 模块级状态机，
        从 `/api/tasks/train/start` 起的训练它看不见，于是这里会返回 200 并**再起一次训练**。
        """
        app.state.task_registry = FakeRegistry(
            statuses={TRAIN: {"status": "running"}}
        )

        resp = client.post(
            "/api/models/train", json={"confirm": True}, headers=_csrf(client)
        )

        assert resp.status_code == 409, "状态真相源不是 TaskRegistry"

    def test_unregistered_train_task_is_404(self, app, client):
        app.state.task_registry = FakeRegistry(unknown={TRAIN})

        resp = client.post(
            "/api/models/train", json={"confirm": True}, headers=_csrf(client)
        )

        assert resp.status_code == 404, resp.text
        assert resp.json()["error"] == "NOT_FOUND"

    def test_missing_task_registry_is_503(self, app, client):
        """lifespan 未执行 → 503，而不是 NoneType 崩溃。"""
        app.state.task_registry = None

        resp = client.post(
            "/api/models/train", json={"confirm": True}, headers=_csrf(client)
        )

        assert resp.status_code == 503, resp.text
        assert resp.json()["error"] == "SERVICE_UNAVAILABLE"

    def test_requires_csrf(self, client):
        assert client.post("/api/models/train", json={"confirm": True}).status_code == 403

    def test_get_is_not_allowed(self, client):
        assert client.get("/api/models/train").status_code == 405


class TestStartTrainingParameters:
    def test_start_delegates_to_task_registry(self, client, registry):
        resp = client.post(
            "/api/models/train",
            json={"dataset": "cicids", "epochs": 20, "confirm": True},
            headers=_csrf(client),
        )

        assert resp.status_code == 200, resp.text
        assert registry.start_calls == [(TRAIN, {"dataset": "cicids", "quick": False})]

    @pytest.mark.parametrize("epochs,expected_quick", [(1, True), (3, True), (4, False)])
    def test_quick_threshold_boundary(self, client, registry, epochs, expected_quick):
        """`epochs <= 3` 视为 quick —— 边界 3/4 必须精确。"""
        client.post(
            "/api/models/train",
            json={"epochs": epochs, "confirm": True},
            headers=_csrf(client),
        )
        assert registry.start_calls[0][1]["quick"] is expected_quick

    def test_default_dataset_is_auto(self, client, registry):
        client.post("/api/models/train", json={"confirm": True}, headers=_csrf(client))
        assert registry.start_calls[0][1]["dataset"] == "auto"

    def test_message_reports_dataset_and_quick(self, client, registry):
        body = client.post(
            "/api/models/train",
            json={"dataset": "nslkdd", "epochs": 2, "confirm": True},
            headers=_csrf(client),
        ).json()

        assert "dataset=nslkdd" in body["message"]
        assert "quick=True" in body["message"]

    def test_epochs_must_be_positive(self, client, registry):
        resp = client.post(
            "/api/models/train", json={"epochs": 0, "confirm": True}, headers=_csrf(client)
        )
        assert resp.status_code == 422
        assert registry.start_calls == []

    def test_start_error_is_503(self, client, registry):
        registry.start_results[TRAIN] = [
            {"status": "error", "message": "训练链路未接入"}
        ]

        resp = client.post(
            "/api/models/train", json={"confirm": True}, headers=_csrf(client)
        )

        assert resp.status_code == 503, resp.text
        assert resp.json()["error"] == "TRAIN_START_FAILED"
        assert resp.json()["detail"] == "训练链路未接入"

    def test_start_error_without_message_has_default_text(self, client, registry):
        registry.start_results[TRAIN] = [{"status": "error"}]
        resp = client.post(
            "/api/models/train", json={"confirm": True}, headers=_csrf(client)
        )
        assert resp.json()["detail"] == "训练任务启动失败"

    def test_race_already_running_after_precheck_is_409(self, client, registry):
        """预检查通过后、启动前被抢先 → 409（不是 200 谎报已启动）。"""
        registry.start_results[TRAIN] = [{"status": "already_running"}]

        resp = client.post(
            "/api/models/train", json={"confirm": True}, headers=_csrf(client)
        )

        assert resp.status_code == 409


class TestTrainingBackup:
    """训练前必须把三个产物备份走 —— 否则重训会静默毁掉上一版结果。"""

    def _seed_products(self) -> None:
        data_dir = get_settings().data_dir
        for name in TRAINING_PRODUCTS:
            (data_dir / name).write_text(f"旧内容:{name}", encoding="utf-8")

    def test_no_products_returns_none(self):
        """首次训练无物可备 → 返回 None，且不创建空备份目录。"""
        assert backup_training_products() is None
        assert not (get_settings().data_dir / "training_backup").exists()

    def test_backs_up_only_existing_products(self):
        data_dir = get_settings().data_dir
        (data_dir / "model.pkl").write_text("旧模型", encoding="utf-8")

        target = backup_training_products()

        assert target is not None
        assert target.parent == data_dir / "training_backup"
        assert [p.name for p in target.iterdir()] == ["model.pkl"], (
            "只应备份存在的产物，不得凭空调创建空文件"
        )
        assert (target / "model.pkl").read_text(encoding="utf-8") == "旧模型"

    def test_backup_preserves_original_files(self):
        """备份是**复制**而非搬移 —— 训练前的产物必须原地还在。"""
        self._seed_products()
        data_dir = get_settings().data_dir

        backup_training_products()

        for name in TRAINING_PRODUCTS:
            assert (data_dir / name).exists(), f"{name} 被搬走了"

    def test_repeated_backups_do_not_overwrite_each_other(self):
        """文档承诺"多次训练不会互相覆盖" —— 同一秒内连续备份也必须成立。

        （时间戳只到秒，所以实现里加了序号后缀消除撞名窗口。）
        """
        data_dir = get_settings().data_dir
        (data_dir / "model.pkl").write_text("第一版", encoding="utf-8")
        first = backup_training_products()

        (data_dir / "model.pkl").write_text("第二版", encoding="utf-8")
        second = backup_training_products()

        assert first != second, "两次备份落在同一目录 → 第一次的备份已被覆盖"
        assert (first / "model.pkl").read_text(encoding="utf-8") == "第一版"
        assert (second / "model.pkl").read_text(encoding="utf-8") == "第二版"

    def test_start_message_mentions_backup_dir(self, client, registry):
        self._seed_products()

        body = client.post(
            "/api/models/train", json={"confirm": True}, headers=_csrf(client)
        ).json()

        assert "已备份至" in body["message"]

    def test_start_message_omits_backup_when_nothing_to_back_up(self, client, registry):
        body = client.post(
            "/api/models/train", json={"confirm": True}, headers=_csrf(client)
        ).json()

        assert "已备份至" not in body["message"], "首次训练不该声称备份了不存在的产物"

    def test_backup_happens_before_start(self, client, registry):
        """顺序要求：必须先备份再启动 —— 反了就等于没备份。"""
        self._seed_products()

        def _start(name, duration=None, **kwargs):
            registry.backup_seen_at_start = list(
                (get_settings().data_dir / "training_backup").iterdir()
            )
            return {"status": "started"}

        registry.start = _start  # type: ignore[method-assign]
        client.post("/api/models/train", json={"confirm": True}, headers=_csrf(client))

        assert registry.backup_seen_at_start, "start 被调用时备份目录还不存在"


# ══ GET /api/models/train/status ══════════════════════════════════


class TestTrainStatus:
    @pytest.mark.parametrize("raw,expected", [
        ("idle", "idle"),
        ("running", "training"),
        ("stopping", "training"),
        ("finished", "completed"),
        ("failed", "failed"),
        ("某个未知状态", "idle"),
    ])
    def test_status_mapping(self, app, client, raw, expected):
        app.state.task_registry = FakeRegistry(statuses={TRAIN: {"status": raw}})
        assert client.get("/api/models/train/status").json()["status"] == expected

    def test_default_status_when_registry_omits_it(self, app, client):
        """注册表没回 status 字段 → 视为 idle（不是崩）。"""
        app.state.task_registry = FakeRegistry(base={}, statuses={TRAIN: {}})

        resp = client.get("/api/models/train/status")

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "idle"

    def test_progress_is_computed_from_elapsed_over_duration(self, app, client):
        """`progress` 必须是算出来的，不是硬编码 0.0（T2.13 前是硬编码）。"""
        app.state.task_registry = FakeRegistry(statuses={
            TRAIN: {"status": "running", "elapsed": 30.0, "duration": 120},
        })

        body = client.get("/api/models/train/status").json()

        assert body["progress"] == 0.25, body
        assert body["elapsed_seconds"] == 30.0
        assert body["duration_seconds"] == 120

    def test_progress_clamped_to_one(self, app, client):
        app.state.task_registry = FakeRegistry(statuses={
            TRAIN: {"status": "running", "elapsed": 500.0, "duration": 120},
        })
        assert client.get("/api/models/train/status").json()["progress"] == 1.0

    def test_progress_falls_back_to_default_duration(self, app, client):
        """`duration` 缺失时用 `default_duration`（兼容旧返回结构）。"""
        app.state.task_registry = FakeRegistry(statuses={
            TRAIN: {"status": "running", "elapsed": 60.0, "default_duration": 120},
        })

        body = client.get("/api/models/train/status").json()

        assert body["progress"] == 0.5
        assert body["duration_seconds"] == 120

    def test_progress_zero_when_not_training(self, app, client):
        app.state.task_registry = FakeRegistry(statuses={
            TRAIN: {"status": "idle", "elapsed": 60.0, "duration": 120},
        })
        assert client.get("/api/models/train/status").json()["progress"] == 0.0

    def test_progress_is_one_when_completed(self, app, client):
        app.state.task_registry = FakeRegistry(statuses={TRAIN: {"status": "finished"}})
        assert client.get("/api/models/train/status").json()["progress"] == 1.0

    def test_progress_zero_without_duration(self, app, client):
        """只有 elapsed 没有 duration → 无法算比例，应为 0 而不是 ZerroDivisionError。"""
        app.state.task_registry = FakeRegistry(statuses={
            TRAIN: {"status": "running", "elapsed": 10.0},
        })
        assert client.get("/api/models/train/status").json()["progress"] == 0.0

    def test_error_is_passed_through(self, app, client):
        app.state.task_registry = FakeRegistry(statuses={
            TRAIN: {"status": "failed", "error": "数据集缺失"},
        })
        body = client.get("/api/models/train/status").json()
        assert body["status"] == "failed"
        assert body["error"] == "数据集缺失"

    def test_epoch_fields_are_always_zero(self, app, client):
        """`epoch` / `total_epochs` 恒为 0 —— 训练链路内部进度在 D4 范围外。

        这条断言是**记录已知限制**而不是认可它：若将来接上真实 epoch 进度，
        本测试会失败，从而强制更新契约文档。
        """
        app.state.task_registry = FakeRegistry(statuses={
            TRAIN: {"status": "running", "elapsed": 1.0, "duration": 10},
        })
        body = client.get("/api/models/train/status").json()
        assert body["epoch"] == 0 and body["total_epochs"] == 0

    def test_unregistered_train_task_is_404(self, app, client):
        app.state.task_registry = FakeRegistry(unknown={TRAIN})
        resp = client.get("/api/models/train/status")
        assert resp.status_code == 404

    def test_missing_task_registry_is_503(self, app, client):
        app.state.task_registry = None
        resp = client.get("/api/models/train/status")
        assert resp.status_code == 503
        assert resp.json()["error"] == "SERVICE_UNAVAILABLE"


# ══ DELETE /api/models/{name} ═════════════════════════════════════


class TestDeleteModel:
    def test_unknown_model_is_404(self, client):
        _write_registry([{"run_id": "run-001"}])
        resp = client.delete("/api/models/does-not-exist", headers=_csrf(client))
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"] == "NOT_FOUND"

    def test_delete_with_no_registry_is_404(self, client):
        assert client.delete("/api/models/anything", headers=_csrf(client)).status_code == 404

    def test_removes_run_dir_and_registry_entry(self, client):
        run_dir = get_settings().data_dir / "runs" / "run-001"
        run_dir.mkdir(parents=True)
        (run_dir / "model.pkl").write_text("x", encoding="utf-8")
        _write_registry([
            {"run_id": "run-001", "run_dir": str(run_dir)},
            {"run_id": "keep-me", "run_dir": ""},
        ])

        resp = client.delete("/api/models/run-001", headers=_csrf(client))

        assert resp.status_code == 200, resp.text
        assert "run-001" in resp.json()["message"]
        assert not run_dir.exists(), "run 目录未被删除"
        assert [r["run_id"] for r in _read_registry_file()] == ["keep-me"], (
            "注册表未正确移除条目（或误删了其他条目）"
        )

    def test_delete_is_scoped_to_the_named_run(self, client):
        """删除只影响目标条目 —— 其余 run 目录必须原样保留。"""
        other = get_settings().data_dir / "runs" / "run-002"
        other.mkdir(parents=True)
        _write_registry([
            {"run_id": "run-002", "run_dir": str(other)},
            {"run_id": "run-003", "run_dir": ""},
        ])

        client.delete("/api/models/run-003", headers=_csrf(client))

        assert other.exists(), "误删了别的 run 目录"
        assert [r["run_id"] for r in _read_registry_file()] == ["run-002"]

    def test_entry_without_run_dir_is_still_deregistered(self, client):
        """`run_dir` 为空/缺失时不得崩溃，条目仍须被移除。"""
        _write_registry([{"run_id": "no-dir"}, {"run_id": "other"}])

        resp = client.delete("/api/models/no-dir", headers=_csrf(client))

        assert resp.status_code == 200, resp.text
        assert [r["run_id"] for r in _read_registry_file()] == ["other"]

    def test_missing_run_dir_on_disk_is_tolerated(self, client):
        """注册表里记的目录已被手工删掉 → 仍应成功并移除条目（幂等）。"""
        _write_registry([
            {"run_id": "ghost", "run_dir": str(get_settings().data_dir / "runs" / "ghost")},
        ])

        resp = client.delete("/api/models/ghost", headers=_csrf(client))

        assert resp.status_code == 200, resp.text
        assert _read_registry_file() == []

    def test_registry_write_failure_returns_500(self, client, monkeypatch, caplog):
        """写注册表失败 → 返回 500（R-16: 不再静默失败，调用方能感知真实状态）。"""
        from pathlib import Path

        _write_registry([{"run_id": "boom", "run_dir": ""}])

        def _explode(self, *args, **kwargs):
            raise OSError("磁盘只读")

        monkeypatch.setattr(Path, "write_text", _explode)

        with caplog.at_level("ERROR", logger="campus_ids.web.api.models"):
            resp = client.delete("/api/models/boom", headers=_csrf(client))

        assert resp.status_code == 500, resp.text
        body = resp.json()
        assert body["error"] == "REGISTRY_WRITE_FAILED"
        assert any("更新注册表失败" in r.getMessage() for r in caplog.records)

    def test_requires_csrf(self, client):
        _write_registry([{"run_id": "run-001"}])
        assert client.delete("/api/models/run-001").status_code == 403

    def test_requires_write_method(self, client):
        _write_registry([{"run_id": "run-001"}])
        assert client.post("/api/models/run-001").status_code == 405


# ══ DELETE 悬挂指针修复（2026-09-19）══════════════════════════════


def _read_json(path) -> dict | None:
    p = _models_dir() / path
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


class TestDeleteModelPointerRecompute:
    """删除 run 后 latest.json / best.json / is_best 必须重算，不许留悬挂指针。

    期望值来自 `model/train.py` 自身规则（外部真相源）：
    - latest = 剩余 run 中 created_at 最大者；
    - best   = 剩余 run 中 f1_score 最大者；无带 f1_score 的 run → 删 best 指针；
    - registry 的 is_best 与 best 对齐（至多 1 个 True）。
    """

    def test_delete_latest_run_repoints_latest_to_remaining(self, client):
        run_dir = get_settings().data_dir / "runs" / "run-new"
        run_dir.mkdir(parents=True)
        _write_registry([
            {"run_id": "run-old", "created_at": "2026-09-01T00:00:00+00:00",
             "run_dir": str(get_settings().data_dir / "runs" / "run-old")},
            {"run_id": "run-new", "created_at": "2026-09-18T00:00:00+00:00",
             "run_dir": str(run_dir)},
        ])
        (_models_dir() / "latest.json").write_text(json.dumps(
            {"run_id": "run-new", "run_dir": str(run_dir),
             "created_at": "2026-09-18T00:00:00+00:00"}), encoding="utf-8")

        resp = client.delete("/api/models/run-new", headers=_csrf(client))

        assert resp.status_code == 200, resp.text
        latest = _read_json("latest.json")
        assert latest is not None and latest["run_id"] == "run-old", (
            "删除 latest 指向的 run 后指针必须重算到剩余最新 run"
        )

    def test_delete_best_run_repoints_best_and_realigns_is_best(self, client):
        _write_registry([
            {"run_id": "run-a", "created_at": "2026-09-01T00:00:00+00:00",
             "metrics": {"f1_score": 0.90, "accuracy": 0.91}, "is_best": False},
            {"run_id": "run-b", "created_at": "2026-09-02T00:00:00+00:00",
             "metrics": {"f1_score": 0.95, "accuracy": 0.96}, "is_best": True},
        ])
        (_models_dir() / "best.json").write_text(json.dumps(
            {"run_id": "run-b", "metrics": {"f1_score": 0.95}}), encoding="utf-8")

        resp = client.delete("/api/models/run-b", headers=_csrf(client))

        assert resp.status_code == 200, resp.text
        best = _read_json("best.json")
        assert best is not None and best["run_id"] == "run-a"
        assert best["metrics"] == {"f1_score": 0.90, "accuracy": 0.91}, (
            "best.json 的 metrics 应按 train.py 的 5 键过滤原样保留"
        )
        flags = {r["run_id"]: r.get("is_best") for r in _read_registry_file()}
        assert flags == {"run-a": True}, "registry 的 is_best 必须与新 best 对齐"

    def test_delete_last_run_removes_pointer_files(self, client):
        _write_registry([{"run_id": "solo", "created_at": "2026-09-01T00:00:00+00:00"}])
        (_models_dir() / "latest.json").write_text("{}", encoding="utf-8")
        (_models_dir() / "best.json").write_text("{}", encoding="utf-8")

        resp = client.delete("/api/models/solo", headers=_csrf(client))

        assert resp.status_code == 200, resp.text
        assert not (_models_dir() / "latest.json").exists()
        assert not (_models_dir() / "best.json").exists()
        assert _read_registry_file() == []

    def test_delete_without_scored_runs_removes_best_pointer(self, client):
        """剩余 run 都没有 f1_score → best.json 删除（train.py 语义：无 best）。"""
        _write_registry([
            {"run_id": "run-x", "created_at": "2026-09-01T00:00:00+00:00",
             "metrics": {"accuracy": 0.9}, "is_best": True},
        ])
        (_models_dir() / "best.json").write_text("{}", encoding="utf-8")

        client.delete("/api/models/run-x", headers=_csrf(client))

        assert not (_models_dir() / "best.json").exists()

    def test_delete_unrelated_run_keeps_pointers_valid(self, client):
        """删除非指针指向的 run → 指针内容保持等价（重算是幂等的）。"""
        _write_registry([
            {"run_id": "run-a", "created_at": "2026-09-01T00:00:00+00:00",
             "metrics": {"f1_score": 0.90}, "is_best": True},
            {"run_id": "run-b", "created_at": "2026-09-02T00:00:00+00:00",
             "metrics": {"f1_score": 0.95}, "is_best": False},
        ])
        best_before = _read_json("best.json") if (_models_dir() / "best.json").exists() else None
        if best_before is None:
            (_models_dir() / "best.json").write_text(json.dumps(
                {"run_id": "run-b", "metrics": {"f1_score": 0.95}}), encoding="utf-8")

        resp = client.delete("/api/models/run-a", headers=_csrf(client))

        assert resp.status_code == 200, resp.text
        best = _read_json("best.json")
        assert best is not None and best["run_id"] == "run-b"

    def test_registry_write_failure_leaves_pointers_untouched(self, client, monkeypatch):
        """注册表写失败（500）→ 指针文件必须原样保留（不留半更新状态）。"""
        from pathlib import Path

        _write_registry([{"run_id": "boom", "run_dir": ""}])
        (_models_dir() / "latest.json").write_text(json.dumps({"run_id": "boom"}), encoding="utf-8")
        before = (_models_dir() / "latest.json").read_text(encoding="utf-8")

        def _explode(self, *args, **kwargs):
            raise OSError("磁盘只读")

        monkeypatch.setattr(Path, "write_text", _explode)

        resp = client.delete("/api/models/boom", headers=_csrf(client))

        assert resp.status_code == 500, resp.text
        assert (_models_dir() / "latest.json").read_text(encoding="utf-8") == before
