"""services/traffic_service.py — 流量窗口、QPS 计算、落库业务用例。

合并原 helpers.py 的 update_traffic_data / _drain_packets / 流量统计逻辑。

阶段1空壳，阶段2接入实际流量处理逻辑。
"""
from __future__ import annotations


class TrafficService:
    """流量服务 — 管理流量数据窗口和统计。"""

    def update_traffic_data(self) -> dict:
        """更新流量数据（从队列消费 + 计算 QPS + 落库）。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def get_traffic(self) -> dict:
        """获取当前流量数据。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def get_traffic_history(self, limit: int = 60) -> list[dict]:
        """获取历史流量数据。阶段2实现。"""
        raise NotImplementedError("阶段2接入")