"""services/traffic_service.py — 流量窗口、QPS 计算、落库业务用例。

合并原 helpers.py 的 update_traffic_data / _drain_packets / 流量统计逻辑。

数据源：
- RuntimeState.traffic_data：实时流量统计（内存）
- TrafficRepository：流量历史（SQLite）
"""
from __future__ import annotations

import logging
from typing import Any

from campus_ids.runtime.db import get_connection
from campus_ids.runtime.repositories import TrafficRepository
from campus_ids.runtime.state import RuntimeState

logger = logging.getLogger(__name__)


class TrafficService:
    """流量服务 — 管理流量数据窗口和统计。

    通过 RuntimeState 获取实时数据，通过 DB 获取历史数据。
    """

    def __init__(self, state: RuntimeState) -> None:
        self._state = state

    def get_traffic(self) -> dict[str, Any]:
        """获取当前实时流量统计数据。

        对应旧端点 GET /api/traffic 返回的字段。

        F2 修复：新增 data_source 字段，标记数据来源。
        """
        with self._state._state_lock:
            td = self._state.traffic_data
            return {
                "qps": float(td.get("qps") or 0),
                "connections": int(td.get("connections") or 0),
                "alert": td.get("alert") or "",
                "timestamp": td.get("timestamp") or "",
                "packet_count": int(td.get("packet_count") or 0),
                "port_count": len(set(td.get("unique_ports") or [])),
                "src_ip_count": len(set(td.get("src_ips") or [])),
                "syn_packets": int(td.get("syn_packets") or 0),
                "udp_packets": int(td.get("udp_packets") or 0),
                "dns_packets": int(td.get("dns_packets") or 0),
                "data_source": td.get("data_source") or "demo",
            }

    def get_traffic_history(self, limit: int = 60, offset: int = 0) -> dict[str, Any]:
        """获取流量历史数据。

        Args:
            limit: 返回条数上限（最大200）
            offset: 偏移量

        Returns:
            {"history": [...], "total": N, "limit": limit, "offset": offset}
        """
        limit = min(limit, 200)
        with get_connection() as conn:
            rows = TrafficRepository.query(conn, limit=limit, offset=offset)
            history = [
                {
                    "id": row.id,
                    "time": row.time,
                    "qps": row.qps,
                    "connections": row.connections,
                    "packet_count": row.packet_count,
                    "port_count": row.port_count,
                    "src_ip_count": row.src_ip_count,
                    "alert": row.alert,
                }
                for row in rows
            ]
            total = TrafficRepository.count(conn)
        return {
            "history": history,
            "total": total,
            "limit": limit,
            "offset": offset,
        }