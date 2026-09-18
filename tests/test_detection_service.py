# -*- coding: utf-8 -*-
"""`services/detection_service.py` 行为测试（R5 覆盖率补齐）。

改动前该模块覆盖率 **13%**（164 语句中 138 条未覆盖）—— 它是"规则 + ML 双引擎"
的检测节拍核心，几乎是裸奔状态。识别到的问题：原先只有经过编排端点冒烟时
顺带碰到几行，没有针对节拍、拿包、流量更新的直接测试。

本文件用**真实 `RuntimeState`** + 记录型 fake（detector / alert / event_bus），
断言的都是对外可观测行为：状态字段、告警发射、事件广播、落库内容。

⚠️ 本文件第一次运行就抓到 `_do_maintenance` 的真实缺陷（见该测试的 docstring）。
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from campus_ids.config import (
    DEMO_CONN_MAX, DEMO_CONN_MIN, DEMO_QPS_MAX, DEMO_QPS_MIN,
)
from campus_ids.runtime.db import init_db
from campus_ids.runtime.events import TOPIC_TRAFFIC
from campus_ids.runtime.state import RuntimeState
from campus_ids.services.detection_service import DetectionService


# ── 数据库建表 ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _schema():
    """隔离库需要建表才能落库（conftest 只重置引擎，不建表）。"""
    init_db()


# ── 记录型 fake 依赖 ──────────────────────────────────────────────

class FakeDetectionResult:
    def __init__(self, *, is_anomaly: bool = False, level: str = "low",
                 attack_type: str | None = None, description: str = "",
                 ml_confidence: float = 0.0) -> None:
        self.is_anomaly = is_anomaly
        self.level = level
        self.attack_type = attack_type
        self.description = description
        self.ml_confidence = ml_confidence


class FakeDualDetector:
    def __init__(self, *, load_ok: bool = True, results: list | None = None) -> None:
        self.load_ok = load_ok
        self._results = list(results or [])
        self.added: list[dict] = []
        self.detect_calls: list[dict] = []
        self.load_calls: list[str] = []
        self.ml_loop_started = False
        self.ml_loop_stopped = False

    def add_packet(self, packet: dict) -> None:
        self.added.append(packet)

    def detect(self, **kwargs: Any) -> FakeDetectionResult:
        self.detect_calls.append(kwargs)
        if self._results:
            return self._results.pop(0)
        return FakeDetectionResult()

    def get_stats(self) -> dict:
        return {"detect_calls": len(self.detect_calls)}

    def load_model(self, which: str = "best") -> bool:
        self.load_calls.append(which)
        return self.load_ok

    def start_ml_loop(self) -> None:
        self.ml_loop_started = True

    def stop_ml_loop(self) -> None:
        self.ml_loop_stopped = True


class FakeAlertService:
    def __init__(self) -> None:
        self.emitted: list[dict] = []

    def emit_alert(self, **kwargs: Any) -> None:
        self.emitted.append(kwargs)


class FakeEventBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, Any]] = []

    def publish(self, topic: str, data: Any = None) -> None:
        self.published.append((topic, data))


@dataclass
class Harness:
    service: DetectionService
    state: RuntimeState
    detector: FakeDualDetector
    alerts: FakeAlertService
    bus: FakeEventBus
    settings: SimpleNamespace = field(default_factory=lambda: SimpleNamespace(
        web_refresh_interval_ms=50,
    ))


def _make(*, load_ok: bool = True, results: list | None = None,
          preload_traffic: bool = False) -> Harness:
    state = RuntimeState()
    if preload_traffic:
        state.traffic_data = {"qps": 1, "marker": "pre-existing"}
    detector = FakeDualDetector(load_ok=load_ok, results=results)
    alerts = FakeAlertService()
    bus = FakeEventBus()
    settings = SimpleNamespace(web_refresh_interval_ms=50)
    service = DetectionService(state, detector, alerts, bus, settings)
    return Harness(service, state, detector, alerts, bus, settings)


def _packet(*, src_ip: str = "10.0.0.1", dport: int = 80, proto: str = "TCP",
            length: int = 100, is_syn: bool = False, is_dns: bool = False) -> dict:
    return {"src_ip": src_ip, "dport": dport, "proto": proto, "length": length,
            "is_syn": is_syn, "is_dns": is_dns}


# ── 构造与状态 ────────────────────────────────────────────────────

class TestInit:
    def test_seeds_traffic_data_when_empty(self):
        h = _make()
        td = h.state.traffic_data
        assert td["qps"] == 200 and td["connections"] == 80
        assert td["alert"] is None
        assert td["packet_count"] == 0
        assert td["syn_packets"] == td["udp_packets"] == td["dns_packets"] == 0
        # 窗口容器必须是 deque 且容量 = WINDOW_SIZE（否则 append 无界增长）
        assert td["unique_ports"].maxlen is not None
        assert td["src_ips"].maxlen is not None

    def test_does_not_clobber_existing_traffic_data(self):
        h = _make(preload_traffic=True)
        assert h.state.traffic_data == {"qps": 1, "marker": "pre-existing"}

    def test_detection_status_initial(self):
        h = _make()
        st = h.service.detection_status()
        assert st == {"running": False, "tick_count": 0, "last_tick_duration_ms": 0.0}

    def test_dual_stats_passthrough(self):
        h = _make()
        assert h.service.dual_stats() == {"detect_calls": 0}


class TestDetectionLifecycle:
    def test_start_sets_flags_and_starts_thread(self, monkeypatch):
        h = _make()
        ran = threading.Event()
        monkeypatch.setattr(h.service, "_detector_tick_loop", ran.set)

        assert h.service.start_detection() == {"status": "started"}
        assert ran.wait(timeout=3), "节拍线程未启动"
        assert h.state.detection_running is True
        assert h.state.detection_thread is not None
        assert h.service.detection_status()["running"] is True

    def test_start_is_idempotent(self, monkeypatch):
        h = _make()
        monkeypatch.setattr(h.service, "_detector_tick_loop", lambda: None)
        assert h.service.start_detection() == {"status": "started"}
        assert h.service.start_detection() == {"status": "already_running"}

    def test_stop_clears_flags(self, monkeypatch):
        h = _make()
        monkeypatch.setattr(h.service, "_detector_tick_loop", lambda: None)
        h.service.start_detection()
        assert h.service.stop_detection() == {"status": "stopped"}
        assert h.state.detection_running is False
        assert h.service.detection_status()["running"] is False


class TestMlLifecycle:
    def test_load_ml_success_starts_loop(self):
        h = _make()
        assert h.service.load_ml() == {"status": "loaded"}
        assert h.detector.load_calls == ["best"], "必须以 which='best' 加载"
        assert h.detector.ml_loop_started is True
        assert h.state.ml_running is True

    def test_load_ml_failure_does_not_start_loop(self):
        """模型加载失败时必须返回 error 且**不得**启动预测循环。"""
        h = _make(load_ok=False)
        result = h.service.load_ml()
        assert result["status"] == "error"
        assert "失败" in result["message"]
        assert h.detector.ml_loop_started is False
        assert h.state.ml_running is False

    def test_stop_ml(self):
        h = _make()
        h.service.load_ml()
        assert h.service.stop_ml() == {"status": "stopped"}
        assert h.detector.ml_loop_stopped is True
        assert h.state.ml_running is False


# ── 拿包 ──────────────────────────────────────────────────────────

class TestDrainPackets:
    def test_empty_queue_returns_none(self):
        assert _make().service._drain_packets() is None

    def test_aggregates_counts_and_updates_recent_packets(self):
        h = _make()
        packets = [
            _packet(src_ip="10.0.0.1", dport=80, is_syn=True, length=100),
            _packet(src_ip="10.0.0.2", dport=443, proto="UDP", is_dns=True, length=200),
            _packet(src_ip="10.0.0.1", dport=80, length=300),
        ]
        for p in packets:
            h.state.packet_queue.put(p)

        stats = h.service._drain_packets()
        assert stats is not None
        assert stats["count"] == 3
        assert stats["syn_count"] == 1
        assert stats["udp_count"] == 1
        assert stats["dns_count"] == 1
        assert stats["dports"] == {80, 443}
        assert stats["src_ips"] == {"10.0.0.1", "10.0.0.2"}
        assert stats["avg_len"] == 200  # (100+200+300)//3

        assert h.state.recent_packets == packets, "必须把本批包写入 recent_packets"
        assert h.detector.added == packets, "每个包都要交给 detector"
        assert h.state.packet_queue.empty(), "队列必须被取空"

    def test_queue_is_fully_drained_on_repeated_calls(self):
        h = _make()
        h.state.packet_queue.put(_packet())
        assert h.service._drain_packets()["count"] == 1
        assert h.service._drain_packets() is None


# ── 流量更新（核心） ──────────────────────────────────────────────

class TestUpdateTrafficData:
    def test_no_packets_falls_back_to_simulation(self):
        """无包时必须回退到模拟数据，且取值落在 config 声明的区间内。"""
        h = _make()
        h.service._update_traffic_data()
        td = h.state.traffic_data
        assert DEMO_QPS_MIN <= td["qps"] <= DEMO_QPS_MAX
        assert DEMO_CONN_MIN <= td["connections"] <= DEMO_CONN_MAX
        assert td["alert"] is None, "无告警时 alert 必须是 None（不是空串）"
        assert h.detector.detect_calls[0]["packets"] == [], "模拟数据不得送 ML 推理"

    def test_packets_drive_qps_and_packet_count(self):
        """有包时 qps 必须由 包数/间隔 算出，而不是随机数。"""
        h = _make()
        h.state.last_update_time = time.time() - 2.0  # 固定 2 秒窗口
        for _ in range(10):
            h.state.packet_queue.put(_packet())

        h.service._update_traffic_data()
        td = h.state.traffic_data
        assert td["packet_count"] == 10
        assert 1 <= td["qps"] <= 10, f"qps 应由 count/interval 推导，实际 {td['qps']}"
        assert h.detector.detect_calls[0]["packets"] == h.state.recent_packets

    def test_interval_floor_prevents_division_blowup(self):
        """间隔极小时必须被夹到 0.1s，否则 qps 会虚高到离谱。"""
        h = _make()
        h.state.last_update_time = time.time()  # 间隔≈0
        for _ in range(5):
            h.state.packet_queue.put(_packet())
        h.service._update_traffic_data()
        assert h.state.traffic_data["qps"] <= 50

    def test_anomaly_emits_alert_and_tags_message(self):
        h = _make(results=[FakeDetectionResult(
            is_anomaly=True, level="high", attack_type="SYN_FLOOD",
            description="SYN 洪泛", ml_confidence=0.93,
        )])
        h.service._update_traffic_data()

        assert len(h.alerts.emitted) == 1, "异常必须发一条告警"
        emitted = h.alerts.emitted[0]
        assert emitted["alert_type"] == "SYN_FLOOD"
        assert emitted["severity"] == "high"
        assert emitted["ml_confidence"] == 0.93
        assert "高危" in emitted["description"]

        alert_text = h.state.traffic_data["alert"]
        assert alert_text is not None and "高危" in alert_text

    def test_normal_traffic_does_not_emit_alert(self):
        h = _make()
        h.service._update_traffic_data()
        assert h.alerts.emitted == []
        assert h.state.traffic_data["alert"] is None

    def test_alert_message_uses_level_specific_tag(self):
        for level, tag in (("medium", "中危"), ("low", "低危"), ("unknown", "异常")):
            h = _make(results=[FakeDetectionResult(
                is_anomaly=True, level=level, attack_type="X", description="d",
            )])
            h.service._update_traffic_data()
            assert tag in h.state.traffic_data["alert"], f"level={level} 的标签不对"

    def test_publishes_traffic_event_with_history_entry(self):
        """SSE 流量事件必须发在 TOPIC_TRAFFIC 上（曾错发 traffic_update）。"""
        h = _make()
        h.service._update_traffic_data()

        topics = [t for t, _ in h.bus.published]
        assert topics == [TOPIC_TRAFFIC], f"事件 topic 应为 {TOPIC_TRAFFIC}，实际 {topics}"

        entry = h.bus.published[0][1]
        assert set(entry) == {"time", "qps", "connections", "packet_count",
                              "port_count", "src_ip_count", "alert"}
        assert entry["time"] == h.state.traffic_data["timestamp"]

    def test_history_is_persisted_to_db(self):
        """流量历史必须落库 —— 用一个可查的副本库验证。"""
        from campus_ids.runtime.db import get_connection
        from campus_ids.runtime.repositories import TrafficRepository

        h = _make()
        h.service._update_traffic_data()

        with get_connection() as conn:
            rows = TrafficRepository.query(conn, limit=10)
        assert len(rows) == 1
        assert rows[0].qps == h.state.traffic_data["qps"]

    def test_db_failure_is_swallowed_but_event_still_published(self, monkeypatch):
        """落库失败不得中断节拍，也不得吞掉事件广播。"""
        import campus_ids.services.detection_service as mod

        class _Boom:
            def __enter__(self):
                raise RuntimeError("disk on fire")

            def __exit__(self, *_exc):
                return False

        monkeypatch.setattr(mod, "get_connection", lambda: _Boom())
        h = _make()
        h.service._update_traffic_data()  # 不应抛异常
        assert len(h.bus.published) == 1


# ── 节拍线程 ──────────────────────────────────────────────────────

class TestTickLoop:
    def test_runs_one_round_then_exits_when_flag_cleared(self, monkeypatch):
        h = _make()
        calls: list[int] = []

        def fake_update() -> None:
            calls.append(1)
            h.service._tick_running = False  # 让循环下一轮退出

        monkeypatch.setattr(h.service, "_update_traffic_data", fake_update)
        monkeypatch.setattr(h.service, "_do_maintenance", lambda: None)
        h.service._last_maintenance_time = time.time()  # 不触发维护
        h.service._tick_running = True

        h.service._detector_tick_loop()
        assert calls == [1]
        assert h.service._tick_count == 1
        assert h.service._last_tick_duration_ms >= 0

    def test_tick_exception_is_caught_and_loop_continues(self, monkeypatch):
        """单次节拍异常不得杀死线程。"""
        h = _make()
        attempts: list[int] = []

        def boom() -> None:
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("第一次失败")
            h.service._tick_running = False

        monkeypatch.setattr(h.service, "_update_traffic_data", boom)
        monkeypatch.setattr(h.service, "_do_maintenance", lambda: None)
        h.service._last_maintenance_time = time.time()
        h.service._tick_running = True

        h.service._detector_tick_loop()
        assert len(attempts) == 2, "异常后应继续下一轮"

    def test_maintenance_triggered_when_interval_elapsed(self, monkeypatch):
        h = _make()
        ran: list[int] = []
        monkeypatch.setattr(h.service, "_do_maintenance", lambda: ran.append(1))
        monkeypatch.setattr(h.service, "_update_traffic_data",
                            lambda: setattr(h.service, "_tick_running", False))
        h.service._last_maintenance_time = 0.0  # 远超维护周期
        h.service._tick_running = True

        h.service._detector_tick_loop()
        assert ran == [1]


# ── 自动维护（含真实缺陷回归） ────────────────────────────────────

class TestMaintenance:
    def test_maintenance_records_cleanup_result(self, monkeypatch, caplog):
        """🔴 回归：`cleanup_old_data` 返回 **tuple**，`_do_maintenance` 却按 dict 用。

        原实现：
            result = UserRepository.cleanup_old_data(conn, days=30)
            if result.get("alerts_deleted", 0) > 0 or ...   # ← tuple 没有 .get
        结果是 `AttributeError` 被外层 `except Exception` 吞掉 —— 表面上"维护正常"，
        实际上：① 清理结果**永不记录**；② 紧随其后的 WAL checkpoint **被连带跳过**
        （异常发生在 checkpoint 之前），WAL 文件会持续膨胀。

        本测试断言"清理结果真的被记录"，所以它必须能看见删除数。
        """
        import logging

        import campus_ids.runtime.repositories as repos

        monkeypatch.setattr(
            repos.UserRepository, "cleanup_old_data",
            staticmethod(lambda conn, days=30: (2, 3)),
        )
        h = _make()
        with caplog.at_level(logging.INFO, logger="campus_ids.services.detection_service"):
            h.service._do_maintenance()

        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "自动维护" in messages, (
            f"清理结果未被记录（tuple/dict 不匹配？）实际日志: {messages!r}"
        )
        assert "2" in messages and "3" in messages, "应记录两表各自的删除数"
        assert "失败" not in messages, f"维护不应报失败：{messages!r}"

    def test_maintenance_runs_wal_checkpoint(self, monkeypatch):
        """WAL checkpoint 必须真的执行（曾被上游异常连带跳过）。"""
        import campus_ids.runtime.repositories as repos
        import campus_ids.services.detection_service as mod

        monkeypatch.setattr(
            repos.UserRepository, "cleanup_old_data",
            staticmethod(lambda conn, days=30: (0, 0)),
        )

        class _SpyConn:
            def __init__(self) -> None:
                self.executed: list[str] = []

            def execute(self, sql, *args):
                self.executed.append(str(sql))
                return SimpleNamespace()

        class _SpyCtx:
            def __init__(self) -> None:
                self.conn = _SpyConn()

            def __enter__(self):
                return self.conn

            def __exit__(self, *_exc):
                return False

        spy = _SpyCtx()
        monkeypatch.setattr(mod, "get_connection", lambda: spy)
        h = _make()
        h.service._do_maintenance()
        assert any("wal_checkpoint" in sql for sql in spy.conn.executed), (
            f"未执行 WAL checkpoint，实际执行: {spy.conn.executed}"
        )

    def test_maintenance_swallows_db_errors(self, monkeypatch, caplog):
        """维护失败不得抛出（它在节拍线程里跑，抛了会打断循环）。

        它**应该**记一条 warning —— 静默失败会让"维护其实没跑"长期不被发现。
        """
        import logging

        import campus_ids.runtime.repositories as repos

        def boom(conn, days=30):
            raise RuntimeError("db locked")

        monkeypatch.setattr(repos.UserRepository, "cleanup_old_data", staticmethod(boom))
        h = _make()
        with caplog.at_level(logging.WARNING, logger="campus_ids.services.detection_service"):
            h.service._do_maintenance()
        assert any("自动维护失败" in r.getMessage() for r in caplog.records)
