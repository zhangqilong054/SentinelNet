# -*- coding: utf-8 -*-
"""`services/model_service.py` 行为测试。

R-02: train() 改为同步阻塞（由 TaskRegistry 在后台线程中调用），
      删除 train_status()（真相源是 TaskRegistry）。
R-04: 删除 list_models/delete_model（与 api/models.py 重复）。

训练一律用 monkeypatch 替换 `campus_ids.model.train.train` ——
真实训练会跑分钟级并覆盖产物文件。
"""
from __future__ import annotations

import logging

import pytest

import campus_ids.model.train as train_mod
from campus_ids.runtime.state import RuntimeState
from campus_ids.services.model_service import ModelService


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


# ── 训练 ──────────────────────────────────────────────────────────

class TestTrain:
    def test_sync_call_forwards_params(self, fake_train):
        """train() 同步调用并透传 dataset/quick 参数。"""
        svc, _state = _service()
        result = svc.train(dataset="demo", quick=True)
        assert result == {"status": "ok"}
        assert fake_train == [{"dataset_type": "demo", "quick": True}]

    def test_defaults(self, fake_train):
        svc, _state = _service()
        svc.train()
        assert fake_train == [{"dataset_type": "auto", "quick": False}]

    def test_failure_raises_and_logs(self, monkeypatch, caplog):
        """训练失败时 raise 原始异常并记录错误日志。"""
        def _boom(**kwargs):
            raise RuntimeError("数据集缺失")

        monkeypatch.setattr(train_mod, "train", _boom)
        svc, _state = _service()
        with caplog.at_level(logging.ERROR, logger="campus_ids.services.model_service"):
            with pytest.raises(RuntimeError, match="数据集缺失"):
                svc.train()
        assert any("模型训练失败" in r.getMessage() for r in caplog.records)

    def test_success_is_logged(self, fake_train, caplog):
        svc, _state = _service()
        with caplog.at_level(logging.INFO, logger="campus_ids.services.model_service"):
            svc.train()
        assert any("模型训练完成" in r.getMessage() for r in caplog.records)