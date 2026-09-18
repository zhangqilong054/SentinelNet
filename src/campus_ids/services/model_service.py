"""services/model_service.py — 模型训练业务用例。

R-02: 删除双层线程（TaskRegistry 已在线程中调用 train_target），
      train() 改为同步阻塞风格。
R-04: 删除 list_models/delete_model（与 api/models.py 重复），
      删除 train_status()（零生产调用，真相源是 TaskRegistry）。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ModelService:
    """模型服务 — 管理模型训练。

    通过注入的依赖访问共享资源。训练由 TaskRegistry 编排，
    train() 为同步阻塞调用（在 TaskRegistry 线程内执行）。
    """

    def __init__(
        self,
        state: Any,  # RuntimeState
        dual_detector: Any | None = None,  # DualDetector
    ) -> None:
        self._state = state
        self._dual_detector = dual_detector

    def train(self, dataset: str = "auto", quick: bool = False) -> dict:
        """同步执行模型训练（由 TaskRegistry 在后台线程中调用）。"""
        from campus_ids.model.train import train

        try:
            result = train(dataset_type=dataset, quick=quick)
            logger.info("模型训练完成")
            return result
        except Exception as exc:
            logger.error("模型训练失败: %s", exc)
            raise