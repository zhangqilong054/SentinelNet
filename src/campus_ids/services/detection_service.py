"""services/detection_service.py — 规则 + ML 双引擎检测业务用例。

合并原 helpers.py 的检测节拍控制 + bp_monitor.py 的双引擎统计。

阶段1空壳，阶段2接入实际检测逻辑。
"""
from __future__ import annotations


class DetectionService:
    """检测服务 — 管理规则检测和 ML 双引擎的生命周期。"""

    def start_detection(self) -> dict:
        """启动检测节拍。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def stop_detection(self) -> dict:
        """停止检测节拍。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def detection_status(self) -> dict:
        """查询检测状态。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def dual_stats(self) -> dict:
        """查询双引擎统计。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def load_ml(self) -> dict:
        """加载 ML 模型。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def stop_ml(self) -> dict:
        """停止 ML 引擎。阶段2实现。"""
        raise NotImplementedError("阶段2接入")