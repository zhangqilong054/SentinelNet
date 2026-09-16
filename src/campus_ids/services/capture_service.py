"""services/capture_service.py — 基础 + 增强抓包业务用例。

合并原 helpers.py 的 start_capture_thread / stop_capture_thread /
start_enhanced_capture / stop_enhanced_capture 为统一接口。

阶段1空壳，阶段2接入实际抓包逻辑。
"""
from __future__ import annotations


class CaptureService:
    """抓包服务 — 管理基础抓包和增强抓包的生命周期。"""

    def start_capture(self) -> dict:
        """启动基础抓包。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def stop_capture(self) -> dict:
        """停止基础抓包。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def capture_status(self) -> dict:
        """查询基础抓包状态。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def start_enhanced(self, duration: int = 60) -> dict:
        """启动增强抓包（限时）。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def stop_enhanced(self) -> dict:
        """停止增强抓包。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def enhanced_status(self) -> dict:
        """查询增强抓包状态。阶段2实现。"""
        raise NotImplementedError("阶段2接入")