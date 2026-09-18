"""runtime/state.py — 运行时句柄容器。

取代 helpers.py 的 12 个模块级赋值（其中 7 个可变），
以及 attack_sim_state.py 的线程/队列状态。

设计原则：
- 运行时可变状态只允许存在于 RuntimeState 容器内（ADR-0001 §4.2）
- 不允许模块级裸全局
- 跨线程通信统一走显式队列/事件总线

本模块 import 不产生任何 I/O 副作用。
"""
from __future__ import annotations

import queue
import threading
from typing import Any

from campus_ids.runtime.events import EventBus


class RuntimeState:
    """运行时状态容器 — 所有可变运行时状态的唯一归属。

    由 create_app() 创建并注入到 FastAPI app.state。
    services/ 和 web/api/ 通过依赖注入获取，不直接 import 全局。
    """

    def __init__(self) -> None:
        # ── 全局状态锁 ────────────────────────────────────────────
        self._state_lock = threading.Lock()

        # ── 抓包状态 ──────────────────────────────────────────────
        self.packet_queue: queue.Queue[dict] = queue.Queue(maxsize=20000)
        self.dropped_packets: int = 0
        self.capture_running: bool = False
        self.capture_thread: threading.Thread | None = None

        # ── 增强抓包状态 ──────────────────────────────────────────
        self.enhanced_capture_running: bool = False
        self.enhanced_capture_thread: threading.Thread | None = None
        self.enhanced_capture_result: dict = {}

        # ── 检测状态 ──────────────────────────────────────────────
        self.detection_running: bool = False
        self.detection_thread: threading.Thread | None = None

        # ── ML 引擎状态 ──────────────────────────────────────────
        self.ml_running: bool = False

        # ── 流量数据 ──────────────────────────────────────────────
        self.traffic_data: dict[str, Any] = {}
        self.recent_packets: list[dict] = []
        self.last_update_time: float = 0.0

        # ── 事件总线 ──────────────────────────────────────────────
        self.event_bus = EventBus()

