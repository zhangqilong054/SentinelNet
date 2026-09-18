"""services/capture_service.py — 基础 + 增强抓包业务用例。

合并原 helpers.py 的 start_capture_thread / stop_capture_thread /
start_enhanced_capture / stop_enhanced_capture 为统一接口。

依赖注入：
- RuntimeState: packet_queue / capture_running / capture_thread / enhanced_capture_*
- DualDetector: add_packet
- tls_analyzer: parse_tls_from_packet
"""
from __future__ import annotations

import logging
import queue
import threading
import time as _time
from typing import Any

from campus_ids.config import DNS_PORT, TLS_PORTS
from campus_ids.capture.enhanced_features import _parse_base_fields

logger = logging.getLogger(__name__)


class CaptureService:
    """抓包服务 — 管理基础抓包和增强抓包的生命周期。

    通过 RuntimeState 管理运行状态，通过注入的依赖访问共享资源。
    """

    def __init__(
        self,
        state: Any,  # RuntimeState
        dual_detector: Any,  # DualDetector
        tls_analyzer: Any,  # tls_analyzer 单例
        alert_service: Any | None = None,  # AlertService
        traffic_service: Any | None = None,  # TrafficService
    ) -> None:
        self._state = state
        self._dual_detector = dual_detector
        self._tls_analyzer = tls_analyzer
        self._alert_service = alert_service
        self._traffic_service = traffic_service

    # ── 基础抓包 ──────────────────────────────────────────────────

    def start_capture(self) -> dict:
        """启动基础抓包（幂等）。

        对应旧实现: helpers.start_capture_thread()
        """
        with self._state._state_lock:
            if self._state.capture_running:
                return {"status": "already_running"}
            self._state.capture_running = True

        thread = threading.Thread(target=self._capture_worker, daemon=True)
        self._state.capture_thread = thread
        thread.start()
        logger.info("基础抓包已启动")
        return {"status": "started"}

    def stop_capture(self) -> dict:
        """停止基础抓包。

        对应旧实现: helpers.stop_capture_thread()
        """
        with self._state._state_lock:
            self._state.capture_running = False
        logger.info("基础抓包已停止")
        return {"status": "stopped"}

    def capture_status(self) -> dict:
        """查询基础抓包状态。"""
        return {
            "running": self._state.capture_running,
            "dropped_packets": self._state.dropped_packets,
        }

    # ── 增强抓包 ──────────────────────────────────────────────────

    def start_enhanced(self, duration: int = 60) -> dict:
        """启动增强抓包（限时）。

        对应旧实现: helpers.start_enhanced_capture_thread()
        """
        with self._state._state_lock:
            if self._state.enhanced_capture_running:
                return {"status": "already_running"}
            self._state.enhanced_capture_running = True

        self._state.enhanced_capture_result = {
            "status": "running",
            "duration": duration,
            "packets": 0,
            "flows": 0,
            "error": None,
        }

        thread = threading.Thread(
            target=self._enhanced_capture_worker,
            args=(duration,),
            daemon=True,
        )
        self._state.enhanced_capture_thread = thread
        thread.start()
        logger.info("增强抓包已启动 (duration=%ds)", duration)
        return {"status": "started"}

    def stop_enhanced(self) -> dict:
        """停止增强抓包。

        对应旧实现: helpers.stop_enhanced_capture_thread()
        """
        with self._state._state_lock:
            self._state.enhanced_capture_running = False
        logger.info("增强抓包已停止")
        return {"status": "stopped"}

    def enhanced_status(self) -> dict:
        """查询增强抓包状态。

        对应旧实现: helpers.get_enhanced_capture_status()
        """
        running = self._state.enhanced_capture_running
        result = dict(self._state.enhanced_capture_result) if self._state.enhanced_capture_result else {}
        result["running"] = running
        return result

    # ── 内部工作函数 ──────────────────────────────────────────────

    def _capture_worker(self) -> None:
        """后台抓包线程：持续抓包并把关键信息放入队列。

        对应旧实现: helpers._capture_worker()
        """
        from scapy.all import IP, TCP, UDP, sniff

        def _on_pkt(pkt):
            if not self._state.capture_running:
                return
            base = _parse_base_fields(pkt)
            if base is not None:
                _, l4, proto, src_ip, dst_ip, _, dst_port, pkt_len, timestamp = base
                is_syn = bool(pkt.haslayer(TCP) and pkt[TCP].flags & 0x02)
                is_dns = bool(pkt.haslayer(UDP) and dst_port == DNS_PORT)
                try:
                    self._state.packet_queue.put_nowait({
                        "length": pkt_len,
                        "sport": int(l4.sport),
                        "dport": dst_port,
                        "src_ip": src_ip,
                        "dst_ip": dst_ip,
                        "proto": proto,
                        "is_syn": is_syn,
                        "is_dns": is_dns,
                        "timestamp": timestamp,
                    })
                except queue.Full:
                    self._state.dropped_packets += 1

                # TLS 解析
                if pkt.haslayer(TCP) and dst_port in TLS_PORTS:
                    try:
                        self._tls_analyzer.parse_tls_from_packet(pkt)
                    except Exception as exc:
                        logger.debug("TLS 解析失败: %s", exc)

        try:
            sniff(prn=_on_pkt, store=False,
                  stop_filter=lambda _: not self._state.capture_running)
        except Exception as exc:
            logger.error("后台抓包线程异常退出: %s", exc)

    def _enhanced_capture_worker(self, duration: int) -> None:
        """后台增强抓包线程：提取 18 维流特征 + TLS 分析，保存 CSV。

        对应旧实现: helpers._enhanced_capture_worker()
        """
        from campus_ids.capture.enhanced_features import run_enhanced_capture

        logger.info("增强抓包线程启动，持续 %d 秒", duration)
        self._state.enhanced_capture_result = {
            "status": "running",
            "duration": duration,
            "packets": 0,
            "flows": 0,
            "error": None,
        }

        stop_time = _time.time() + duration
        stop_filter = lambda _: not self._state.enhanced_capture_running or _time.time() >= stop_time

        result = run_enhanced_capture(duration, stop_filter=stop_filter)

        if result is None:
            self._state.enhanced_capture_result = {
                "status": "error",
                "duration": duration,
                "packets": 0,
                "flows": 0,
                "error": "抓包失败",
            }
        else:
            packets, flows = result
            self._state.enhanced_capture_result = {
                "status": "completed",
                "duration": duration,
                "packets": packets,
                "flows": flows,
                "error": None,
            }
        self._state.enhanced_capture_running = False