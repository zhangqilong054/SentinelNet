"""services/alert_service.py — 告警冷却、落库、广播业务用例。

合并原 helpers.py 的 should_emit_alert / 告警回调 + database.py 的告警写入。

阶段1空壳，阶段2接入实际告警逻辑。
"""
from __future__ import annotations


class AlertService:
    """告警服务 — 管理告警生成、冷却和广播。"""

    def emit_alert(self, alert_type: str, severity: str, description: str, **kwargs) -> dict | None:
        """生成告警（经冷却判断后落库 + 广播）。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def get_alerts(self, severity: str | None = None, limit: int = 20, offset: int = 0) -> list[dict]:
        """查询告警列表。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def get_alert_stats(self) -> dict:
        """查询告警统计。阶段2实现。"""
        raise NotImplementedError("阶段2接入")