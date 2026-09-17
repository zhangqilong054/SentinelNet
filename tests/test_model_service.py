# -*- coding: utf-8 -*-
"""`services/model_service.py` 行为测试（R5 覆盖率补齐）。

改动前覆盖率 **22%**。本文件覆盖训练后台线程的状态机、参数透传、
注册表读取容错与模型删除。

⚠️ 训练一律用 monkeypatch 替换 `campus_ids.model.train.train` —— 真实训练会跑分钟级
并覆盖 `model.pkl` / `evaluation_report.txt` / `confusion_matrix.png`（无版本控制兜底）。
"""
from __future__ import annotations

import json
import logging

import pytest

import campus_ids.model.train as train_mod
import campus_ids.services.model_service as mod
from campus_ids.runtime.state import RuntimeState
from campus_ids.services.model_service import ModelService


@pytest.fixture
def models_dir(tmp_path, monkeypatch):
    """把 MODEL_DIR 指向 tmp —— `MODELS_DIR` 是 import 期按值绑定的常量。"""
    path = tmp_path / "models"
    path.mkdir()
    monkeypatch.setattr(mod, "MODELS_DIR", path)
    return path


@pytest.fixture
def fake_train(monkeypatch):
    """记录型假训练（默认成功）。返回调用记录列表。"""
    calls: list[dict] = []

    def _train(dataset_type: str = "auto", quick: bool = False) -> dict:
        calls.append({"dataset_type": dataset_type, "quick": quick})
        return {"status": "ok"}

    monkeypatch.setattr(train_mod, "train", _train)
    return calls


def _service() -> tuple[ModelService, RuntimeState]:
    state = RuntimeState()
    return ModelService(state), state


def _write_registry(models_dir, entries: list[dict]) -> None:
    (models_dir / "registry.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )


# ── 训练 ──────────────────────────────────────────────────────────

class TestTrain:
    def test_starts_background_thread_and_forwards_params(self, fake_train):
        svc, state = _service()
        assert svc.train(dataset="demo", quick=True) == {"status": "started"}

        svc._train_thread.join(timeout=5)
        assert not svc._train_thread.is_alive(), "训练线程未结束"

        assert fake_train == [{"dataset_type": "demo", "quick": True}], (
            "dataset / quick 必须透传到 train()（此前被静默丢弃）"
        )
        assert state.train_running is False, "结束后必须清掉 running 标志"
        assert state.train_status == "completed"
        assert state.train_progress == 1.0

    def test_defaults(self, fake_train):
        svc, _state = _service()
        svc.train()
        svc._train_thread.join(timeout=5)
        assert fake_train == [{"dataset_type": "auto", "quick": False}]

    def test_rejected_when_already_running(self):
        svc, state = _service()
        state.train_running = True
        assert svc.train() == {"status": "already_running"}

    def test_does_not_spawn_thread_when_already_running(self, fake_train):
        svc, state = _service()
        state.train_running = True
        svc.train()
        assert svc._train_thread is None

    def test_failure_marks_failed_and_clears_running(self, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("数据集缺失")

        monkeypatch.setattr(train_mod, "train", _boom)
        svc, state = _service()
        svc.train()
        svc._train_thread.join(timeout=5)

        assert state.train_running is False
        assert state.train_status == "failed"
        assert state.train_progress == 0.0

    def test_failure_is_logged(self, monkeypatch, caplog):
        def _boom(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(train_mod, "train", _boom)
        svc, _state = _service()
        with caplog.at_level(logging.ERROR, logger="campus_ids.services.model_service"):
            svc.train()
            svc._train_thread.join(timeout=5)
        assert any("模型训练失败" in r.getMessage() for r in caplog.records)

    def test_status_reflects_state(self, fake_train):
        svc, state = _service()
        assert svc.train_status() == {"running": False, "status": "idle", "progress": 0.0}
        svc.train()
        svc._train_thread.join(timeout=5)
        assert svc.train_status() == {"running": False, "status": "completed", "progress": 1.0}


# ── 注册表 ────────────────────────────────────────────────────────

class TestListModels:
    def test_missing_registry_returns_empty(self, models_dir):
        svc, _state = _service()
        assert svc.list_models() == []

    def test_reads_registry(self, models_dir):
        _write_registry(models_dir, [{"run_id": "r1"}, {"run_id": "r2"}])
        svc, _state = _service()
        assert [m["run_id"] for m in svc.list_models()] == ["r1", "r2"]

    def test_corrupt_registry_returns_empty_and_logs(self, models_dir, caplog):
        """注册表损坏不得抛异常（页面会整个 500），应降级为空列表 + 错误日志。"""
        (models_dir / "registry.json").write_text("{ not json", encoding="utf-8")
        svc, _state = _service()
        with caplog.at_level(logging.ERROR, logger="campus_ids.services.model_service"):
            assert svc.list_models() == []
        assert any("读取模型注册表失败" in r.getMessage() for r in caplog.records)


# ── 删除 ──────────────────────────────────────────────────────────

class TestDeleteModel:
    def test_unknown_id_returns_error(self, models_dir):
        svc, _state = _service()
        result = svc.delete_model("nope")
        assert result["status"] == "error"
        assert "nope" in result["message"]

    def test_removes_run_dir_and_registry_entry(self, models_dir):
        run_dir = models_dir / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "meta.json").write_text("{}", encoding="utf-8")
        _write_registry(models_dir, [
            {"run_id": "r1", "run_dir": str(run_dir)},
            {"run_id": "r2"},
        ])

        svc, _state = _service()
        assert svc.delete_model("r1") == {"status": "deleted", "model_id": "r1"}

        assert not run_dir.exists(), "run 目录未被删除"
        assert [m["run_id"] for m in svc.list_models()] == ["r2"], "注册表未更新"

    def test_missing_run_dir_is_tolerated(self, models_dir):
        """registry 里记的目录已不存在时不得报错，仍应摘掉注册项。"""
        _write_registry(models_dir, [{"run_id": "ghost", "run_dir": str(models_dir / "gone")}])
        svc, _state = _service()
        assert svc.delete_model("ghost")["status"] == "deleted"
        assert svc.list_models() == []

    def test_entry_without_run_dir_still_deregistered(self, models_dir):
        _write_registry(models_dir, [{"run_id": "x"}])
        svc, _state = _service()
        assert svc.delete_model("x")["status"] == "deleted"
        assert svc.list_models() == []

    def test_registry_write_failure_is_logged_not_raised(self, models_dir, monkeypatch, caplog):
        """注册表写失败（如只读文件系统）应记错误日志，不把异常抛给调用方。"""
        _write_registry(models_dir, [{"run_id": "x"}])
        svc, _state = _service()

        def _boom(*args, **kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(type(models_dir), "write_text", _boom, raising=False)
        with caplog.at_level(logging.ERROR, logger="campus_ids.services.model_service"):
            result = svc.delete_model("x")
        assert result["status"] == "deleted"
        assert any("更新注册表失败" in r.getMessage() for r in caplog.records)
