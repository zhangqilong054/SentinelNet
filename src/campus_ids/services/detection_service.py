"""services/detection_service.py — 规则 + ML 双引擎检测业务用例。

合并原 helpers.py 的检测节拍控制 + bp_monitor.py 的双引擎统计。

依赖注入：
- RuntimeState: packet_queue / detection_running / detection_thread / traffic_data / recent_packets
- DualDetector: detect / add_packet / get_stats / load_model / start_ml_loop / stop_ml_loop
- AlertService: emit_alert
- TrafficService: 流量统计
- EventBus: 事件广播
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time as _time
from collections import deque

from campus_ids.runtime.timeutil import now_str
from typing import Any

from campus_ids.config import (
    DEMO_CONN_MAX, DEMO_CONN_MIN, DEMO_DNS_MAX, DEMO_DNS_MIN,
    DEMO_PKT_MAX, DEMO_PKT_MIN, DEMO_QPS_MAX, DEMO_QPS_MIN,
    DEMO_SYN_MAX, DEMO_SYN_MIN, DEMO_UDP_MAX, DEMO_UDP_MIN,
    WINDOW_SIZE,
)
from campus_ids.runtime.db import get_connection
from campus_ids.services.alert_service import ALERT_LEVEL_LABELS
from campus_ids.runtime.events import TOPIC_TRAFFIC
from campus_ids.runtime.repositories import TrafficRepository

logger = logging.getLogger(__name__)

# O-07c: 数据自动维护周期（小时）
_DATA_MAINTENANCE_INTERVAL_HOURS = int(os.environ.get("CAMPUS_IDS_MAINTENANCE_HOURS", "6"))


class DetectionService:
    """检测服务 — 管理规则检测和 ML 双引擎的生命周期。

    通过 RuntimeState 管理运行状态，通过注入的依赖访问共享资源。
    """

    def __init__(
        self,
        state: Any,  # RuntimeState
        dual_detector: Any,  # DualDetector
        alert_service: Any,  # AlertService
        event_bus: Any,  # EventBus
        settings: Any,  # Settings
    ) -> None:
        self._state = state
        self._dual_detector = dual_detector
        self._alert_service = alert_service
        self._event_bus = event_bus
        self._settings = settings

        # 检测节拍状态
        self._tick_thread: threading.Thread | None = None
        self._tick_running: bool = False
        self._last_tick_duration_ms: float = 0.0
        self._tick_count: int = 0

        # O-07c: 数据自动维护
        self._last_maintenance_time: float = 0.0

        # 流量窗口（从 config 初始化）
        self._window_size = WINDOW_SIZE
        self._unique_ports: deque = deque(maxlen=self._window_size)
        self._src_ips: deque = deque(maxlen=self._window_size)

        # 初始化 traffic_data
        if not self._state.traffic_data:
            self._init_traffic_data()

    def _init_traffic_data(self) -> None:
        """初始化流量数据字典。"""
        self._state.traffic_data = {
            "qps": 200,
            "connections": 80,
            "alert": None,
            "timestamp": now_str(),
            "packet_count": 0,
            "unique_ports": deque(maxlen=self._window_size),
            "src_ips": deque(maxlen=self._window_size),
            "syn_packets": 0,
            "udp_packets": 0,
            "dns_packets": 0,
        }

    # ── 检测节拍 ──────────────────────────────────────────────────

    def start_detection(self) -> dict:
        """启动检测节拍（幂等）。

        对应旧实现: helpers.start_detector_tick()
        """
        with self._state._state_lock:
            if self._tick_running:
                return {"status": "already_running"}
            self._tick_running = True
            self._state.detection_running = True

        thread = threading.Thread(target=self._detector_tick_loop, daemon=True)
        self._tick_thread = thread
        self._state.detection_thread = thread
        thread.start()
        logger.info("检测节拍已启动")
        return {"status": "started"}

    def stop_detection(self) -> dict:
        """停止检测节拍。

        对应旧实现: helpers.stop_detector_tick()
        """
        self._tick_running = False
        self._state.detection_running = False
        logger.info("检测节拍已停止")
        return {"status": "stopped"}

    def detection_status(self) -> dict:
        """查询检测状态。"""
        return {
            "running": self._tick_running,
            "tick_count": self._tick_count,
            "last_tick_duration_ms": round(self._last_tick_duration_ms, 2),
        }

    def dual_stats(self) -> dict:
        """查询双引擎统计。

        对应旧实现: dual_detector.get_stats()
        """
        return self._dual_detector.get_stats()

    # ── ML 引擎 ──────────────────────────────────────────────────

    def load_ml(self) -> dict:
        """加载 ML 模型并启动预测循环。

        对应旧实现: dual_detector.load_model() + start_ml_loop()
        """
        if not self._dual_detector.load_model(which="best"):
            return {"status": "error", "message": "ML 模型加载失败"}
        self._dual_detector.start_ml_loop()
        self._state.ml_running = True
        logger.info("ML 模型加载完成，预测循环已启动")
        return {"status": "loaded"}

    def stop_ml(self) -> dict:
        """停止 ML 引擎。

        对应旧实现: dual_detector.stop_ml_loop()
        """
        self._dual_detector.stop_ml_loop()
        self._state.ml_running = False
        logger.info("ML 预测循环已停止")
        return {"status": "stopped"}

    # ── 内部工作函数 ──────────────────────────────────────────────

    def _detector_tick_loop(self) -> None:
        """检测节拍守护线程主循环。

        对应旧实现: helpers._detector_tick_loop()
        """
        interval_sec = self._settings.web_refresh_interval_ms / 1000.0
        logger.info("检测节拍线程启动，间隔 %.1fs", interval_sec)

        while self._tick_running:
            try:
                t0 = _time.perf_counter()
                self._update_traffic_data()
                self._last_tick_duration_ms = (_time.perf_counter() - t0) * 1000
                self._tick_count += 1

                # O-07c: 数据自动维护
                now = _time.time()
                if now - self._last_maintenance_time >= _DATA_MAINTENANCE_INTERVAL_HOURS * 3600:
                    self._last_maintenance_time = now
                    self._do_maintenance()

            except Exception as exc:
                logger.error("检测节拍异常: %s", exc)

            _time.sleep(interval_sec)

        logger.info("检测节拍线程已停止")

    def _drain_packets(self) -> dict | None:
        """从队列取出所有包并返回统计信息，无包时返回 None。

        对应旧实现: helpers._drain_packets()
        """
        packets = []
        while True:
            try:
                packets.append(self._state.packet_queue.get_nowait())
            except Exception:
                break
        if not packets:
            return None

        self._state.recent_packets = packets
        for p in packets:
            self._dual_detector.add_packet(p)

        return {
            "count": len(packets),
            "syn_count": sum(1 for p in packets if p.get("is_syn", False)),
            "udp_count": sum(1 for p in packets if p["proto"] == "UDP"),
            "dns_count": sum(1 for p in packets if p.get("is_dns", False)),
            "dports": set(p["dport"] for p in packets),
            "src_ips": set(p["src_ip"] for p in packets),
            "avg_len": sum(p["length"] for p in packets) // len(packets),
        }

    def _update_traffic_data(self) -> None:
        """更新流量数据（优先使用真实抓包数据，无包时回退到随机模拟）。

        对应旧实现: helpers.update_traffic_data()
        """
        stats = self._drain_packets()
        now = _time.time()
        interval = now - self._state.last_update_time if self._state.last_update_time > 0 else 1.0
        self._state.last_update_time = now
        if interval < 0.1:
            interval = 0.1

        td = self._state.traffic_data
        current_port_count = 0

        if stats is not None:
            qps = int(stats["count"] / interval)
            connections = len(stats["src_ips"])
            syn_count = int(stats["syn_count"] / interval)
            udp_count = int(stats["udp_count"] / interval)
            dns_count = int(stats.get("dns_count", 0) / interval)
            current_port_count = len(stats["dports"])
            td["packet_count"] += stats["count"]
            for p in stats["dports"]:
                td["unique_ports"].append(p)
            for ip in stats["src_ips"]:
                td["src_ips"].append(ip)
        else:
            qps = random.randint(DEMO_QPS_MIN, DEMO_QPS_MAX)
            connections = random.randint(DEMO_CONN_MIN, DEMO_CONN_MAX)
            syn_count = random.randint(DEMO_SYN_MIN, DEMO_SYN_MAX)
            udp_count = random.randint(DEMO_UDP_MIN, DEMO_UDP_MAX)
            dns_count = random.randint(DEMO_DNS_MIN, DEMO_DNS_MAX)
            current_port_count = 2
            td["packet_count"] += random.randint(DEMO_PKT_MIN, DEMO_PKT_MAX)
            td["unique_ports"].append(random.randint(1, 65535))
            td["unique_ports"].append(random.randint(1, 65535))
            td["src_ips"].append(
                f"{random.randint(1, 255)}.{random.randint(1, 255)}."
                f"{random.randint(1, 255)}.{random.randint(1, 255)}"
            )

        td["qps"] = qps
        td["connections"] = connections
        td["syn_packets"] = syn_count
        td["udp_packets"] = udp_count
        td["dns_packets"] = dns_count
        td["timestamp"] = now_str()

        packets_for_ml = self._state.recent_packets if stats is not None else []

        result = self._dual_detector.detect(
            qps=qps,
            port_count=current_port_count,
            syn_count=syn_count,
            udp_count=udp_count,
            packets=packets_for_ml,
        )

        if result.is_anomaly:
            level_tag = ALERT_LEVEL_LABELS.get(result.level, "⚠️异常")
            alert_msg = f"{level_tag} {result.description}"
            td["alert"] = alert_msg

            # 通过 AlertService 发送告警（含冷却）
            if self._alert_service:
                self._alert_service.emit_alert(
                    alert_type=result.attack_type,
                    severity=result.level,
                    description=alert_msg,
                    ml_confidence=result.ml_confidence,
                )
        else:
            td["alert"] = None

        # 持久化流量历史
        history_entry = {
            "time": td["timestamp"],
            "qps": qps,
            "connections": connections,
            "packet_count": td["packet_count"],
            "port_count": current_port_count,
            "src_ip_count": len(set(td.get("src_ips", []))),
            "alert": td["alert"],
        }
        try:
            with get_connection() as conn:
                TrafficRepository.insert(conn, **{
                    "time": history_entry["time"],
                    "qps": history_entry["qps"],
                    "connections": history_entry["connections"],
                    "packet_count": history_entry["packet_count"],
                    "port_count": history_entry["port_count"],
                    "src_ip_count": history_entry["src_ip_count"],
                    "alert": history_entry["alert"],
                })
        except Exception as exc:
            logger.warning("流量历史写入数据库失败: %s", exc)

        # 事件广播 —— 必须用 TOPIC_TRAFFIC 常量（值 "traffic"）。
        # 曾写死 "traffic_update"，而 SSE 订阅的是 "traffic"，
        # 于是**订阅了流量主题的客户端永远收不到任何事件**。
        if self._event_bus:
            self._event_bus.publish(TOPIC_TRAFFIC, history_entry)

    def _do_maintenance(self) -> None:
        """O-07c: 数据自动维护（清理 30 天前的历史 + WAL checkpoint）。

        ⚠️ 2026-09-17 修复两处真实缺陷（由 `tests/test_detection_service.py`
        的 `TestMaintenance` 抓出）：

        1. `cleanup_old_data` 返回的是 **tuple** `(alerts_deleted, traffic_deleted)`，
           原代码却写成 `result.get("alerts_deleted", 0)` —— 必然抛
           `AttributeError: 'tuple' object has no attribute 'get'`。
           异常被外层 `except` 吞掉，于是表面上"维护正常"，实际上
           **清理结果永不记录**，而且紧随其后的 WAL checkpoint
           **连执行都轮不到**（异常发生在它之前）→ WAL 文件持续膨胀。
        2. `conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")` 传的是**裸字符串**，
           SQLAlchemy 2.0 起不再接受（需要 `text()`）→ 即使执行到也会抛
           `ObjectNotExecutableError`，又被内层 `except` 静默吞掉。
        """
        from sqlalchemy import text

        try:
            from campus_ids.runtime.repositories import UserRepository
            from campus_ids.runtime.settings import get_settings
            cleanup_days = get_settings().cleanup_days
            with get_connection() as conn:
                alerts_deleted, traffic_deleted = UserRepository.cleanup_old_data(
                    conn, days=cleanup_days
                )
                if alerts_deleted or traffic_deleted:
                    logger.info(
                        "自动维护: 清理完成 alerts=%d traffic=%d",
                        alerts_deleted, traffic_deleted,
                    )
                # WAL checkpoint
                try:
                    conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                except Exception as exc:
                    logger.debug("WAL checkpoint 跳过: %s", exc)
        except Exception as exc:
            logger.warning("自动维护失败: %s", exc)