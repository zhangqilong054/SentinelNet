"""services/alert_service.py — 告警冷却、落库、广播业务用例。

合并原 helpers.py 的 should_emit_alert / 告警回调 + database.py 的告警写入。

数据源：
- AlertRepository：告警 CRUD（SQLite）
- EventBus：实时广播（SSE 推送）
"""
from __future__ import annotations

import logging
import time
from typing import Any

from campus_ids.runtime.db import get_connection
from campus_ids.runtime.timeutil import now_str
from campus_ids.runtime.events import TOPIC_ALERT, EventBus
from campus_ids.runtime.repositories import AlertRepository

logger = logging.getLogger(__name__)

# 告警冷却窗口（秒）：同一类型在窗口内不重复告警
ALERT_COOLDOWN_SECONDS = 60

# R-15: 告警级别常量，消除散落在多处的字面量
ALERT_LEVELS = ("high", "medium", "low")

# 告警级别 → 显示标签映射
ALERT_LEVEL_LABELS: dict[str, str] = {
    "high": "🔴高危",
    "medium": "🟠中危",
    "low": "🟡低危",
}


class AlertService:
    """告警服务 — 管理告警生成、冷却和广播。

    通过 DB 持久化告警，通过 EventBus 广播实时告警。
    """

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus
        self._last_alert_time: dict[str, float] = {}  # attack_type → last emit time

    def emit_alert(self, alert_type: str, severity: str, description: str,
                   ml_confidence: float = 0.0, **kwargs: Any) -> dict | None:
        """生成告警（经冷却判断后落库 + 广播）。

        Returns:
            告警数据 dict（如果已落库），None（如果被冷却过滤）
        """
        # 冷却检查
        now = time.time()
        last_time = self._last_alert_time.get(alert_type, 0)
        if now - last_time < ALERT_COOLDOWN_SECONDS:
            logger.debug("告警冷却中: %s（距上次 %.1fs）", alert_type, now - last_time)
            return None

        self._last_alert_time[alert_type] = now

        # 落库
        timestamp = now_str()
        with get_connection() as conn:
            alert_id = AlertRepository.insert(
                conn,
                time=timestamp,
                level=severity,
                attack_type=alert_type,
                message=description,
                ml_confidence=ml_confidence,
            )

        alert_data = {
            "id": alert_id,
            "time": timestamp,
            "level": severity,
            "attack_type": alert_type,
            "message": description,
            "ml_confidence": ml_confidence,
        }

        # 广播 —— 必须用 TOPIC_ALERT 常量（值 "alert"，与旧契约
        # `_broadcast_sse("alert", ...)` 及 SSE 帧 `event: alert` 一致）。
        # 曾写死 "alerts"（复数）导致帧名与旧前端监听器不匹配。
        if self._event_bus:
            self._event_bus.publish(TOPIC_ALERT, alert_data)

        logger.info("告警已生成: %s (%s)", alert_type, severity)
        return alert_data

    def get_alerts(self, level: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """查询告警列表。

        Returns:
            {"alerts": [...], "total": N, "limit": limit, "offset": offset}
        """
        limit = min(limit, 500)
        level_filter = level if level and level != "all" else None
        with get_connection() as conn:
            rows = AlertRepository.query(conn, level=level_filter, limit=limit, offset=offset)
            total = AlertRepository.count(conn, level=level_filter)
            alerts = [
                {
                    "id": row.id,
                    "time": row.time,
                    "level": row.level,
                    "attack_type": row.attack_type,
                    "message": row.message,
                    "ml_confidence": row.ml_confidence,
                }
                for row in rows
            ]
        return {"alerts": alerts, "total": total, "limit": limit, "offset": offset}

    def get_alert_stats(self) -> dict[str, Any]:
        """查询告警统计（R-15: 单次 GROUP BY 替代 4 次 COUNT）。"""
        with get_connection() as conn:
            by_level = AlertRepository.count_by_level(conn)
            total = sum(by_level.values())
            type_dist = AlertRepository.get_type_distribution(conn)
        return {
            "total_alerts": total,
            "alerts_by_type": {row.attack_type: row.count for row in type_dist},
            "alerts_by_severity": {lv: by_level.get(lv, 0) for lv in ALERT_LEVELS},
        }