"""services/model_service.py — 模型训练 / 加载 / 注册表业务用例。

合并原 bp_model.py 的训练、加载、删除、列表功能。

阶段1空壳，阶段2接入实际模型管理逻辑。
"""
from __future__ import annotations


class ModelService:
    """模型服务 — 管理模型训练、加载和注册表。"""

    def train(self, dataset: str = "cicids") -> dict:
        """启动模型训练。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def train_status(self) -> dict:
        """查询训练状态。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def list_models(self) -> list[dict]:
        """列出已训练模型。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def delete_model(self, model_id: str) -> dict:
        """删除指定模型。阶段2实现。"""
        raise NotImplementedError("阶段2接入")