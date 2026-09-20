# -*- coding: utf-8 -*-
"""`services/capture_service.py` 行为测试（R5 覆盖率补齐）。

改动前覆盖率 **44%** —— 未被覆盖的正是**全部真实逻辑**：
`_capture_worker`（抓包线程主体）与 `_enhanced_capture_worker`（增强抓包线程主体）。
之前的测试只碰了 `start/stop/status` 这几个状态翻转函数。

## 测试策略

不 mock 抓包引擎本身，而是 **替换 `scapy.all.sniff` 的驱动方式**：
真实构造 scapy 包（`IP()/TCP(...)`），交给真实的 `_parse_base_fields` 解析，
只有"从网卡取包"这一步被换成"从列表取包"。这样断言到的是**真实解析结果**，
而不是我自己伪造的 tuple —— 后者会在 `_parse_base_fields` 改签名时静默失配。

线程也被换成"同步执行"：`sniff` 的 fake 直接把包喂给 `prn` 后返回，
所以 worker 在调用线程内跑完，测试无需 sleep 等线程。
"""
from __future__ import annotations

import queue
from unittest.mock import MagicMock

import pytest
from scapy.all import ARP, IP, TCP, UDP, Ether

from campus_ids.config import DNS_PORT, TLS_PORTS
from campus_ids.runtime.state import RuntimeState
from campus_ids.services.capture_service import CaptureService

# 选一个确定在 TLS_PORTS 里的端口，避免依赖具体取值
TLS_PORT = sorted(TLS_PORTS)[0]


# ── 依赖替身 ──────────────────────────────────────────────────────


class FakeTlsAnalyzer:
    """记录 `parse_tls_from_packet` 调用。"""

    def __init__(self, *, boom: bool = False) -> None:
        self.calls: list[object] = []
        self._boom = boom

    def parse_tls_from_packet(self, pkt) -> None:
        self.calls.append(pkt)
        if self._boom:
            raise RuntimeError("TLS 解析炸了")


class FakeSniff:
    """替代 `scapy.all.sniff` —— 把预置包喂给 prn，然后立即返回。

    这样 `_capture_worker` 在**调用线程内同步跑完**，测试不需要 sleep 或 join。
    同时记录收到的 `stop_filter`，供"停止条件是否连接了 capture_running"的断言用。
    """

    def __init__(self, packets, *, raises: Exception | None = None) -> None:
        self.packets = packets
        self.raises = raises
        self.prn_calls = 0
        self.stop_filters: list = []
        self.kwargs_list: list[dict] = []  # 记录 sniff 收到的额外 kwargs（iface 等）

    def __call__(self, prn=None, store=None, stop_filter=None, **kwargs):
        self.kwargs_list.append(kwargs)
        self.stop_filters.append(stop_filter)
        if self.raises is not None:
            raise self.raises
        for pkt in self.packets:
            prn(pkt)
            self.prn_calls += 1


# ── fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def state() -> RuntimeState:
    return RuntimeState()


@pytest.fixture
def tls() -> FakeTlsAnalyzer:
    return FakeTlsAnalyzer()


@pytest.fixture
def svc(state, tls) -> CaptureService:
    return CaptureService(state=state, dual_detector=MagicMock(), tls_analyzer=tls)


@pytest.fixture
def patch_sniff(monkeypatch):
    """把 `scapy.all.sniff` 换成 FakeSniff，返回工厂函数。"""

    def _install(packets, *, raises=None) -> FakeSniff:
        fake = FakeSniff(packets, raises=raises)
        monkeypatch.setattr("scapy.all.sniff", fake)
        return fake

    return _install


def _drain(state: RuntimeState) -> list[dict]:
    out = []
    while True:
        try:
            out.append(state.packet_queue.get_nowait())
        except queue.Empty:
            return out


def _wait_for_thread(state: RuntimeState, timeout: float = 2.0) -> None:
    """等待抓包线程处理完毕（FakeSniff 同步执行，join 消除竞态）。"""
    if state.capture_thread is not None:
        state.capture_thread.join(timeout=timeout)


# ══ 基础抓包生命周期 ══════════════════════════════════════════════


class TestBasicLifecycle:
    def test_start_then_status_then_stop(self, svc, state, patch_sniff):
        patch_sniff([])  # 不喂任何包，只检查状态机

        assert svc.capture_status() == {"running": False, "dropped_packets": 0}

        assert svc.start_capture() == {"status": "started"}
        assert state.capture_running is True
        assert state.capture_thread is not None

        assert svc.capture_status()["running"] is True

        assert svc.stop_capture() == {"status": "stopped"}
        assert state.capture_running is False
        assert svc.capture_status()["running"] is False

    def test_start_is_idempotent(self, svc, state, patch_sniff):
        """重复 start 不得起第二个线程（旧实现会）。"""
        patch_sniff([])
        assert svc.start_capture() == {"status": "started"}
        first_thread = state.capture_thread

        assert svc.start_capture() == {"status": "already_running"}
        assert state.capture_thread is first_thread, "第二次 start 覆盖了线程引用"
        assert state.capture_running is True

    def test_stop_when_not_running_is_harmless(self, svc, state):
        """未启动时 stop 不得抛异常（幂等）。"""
        assert svc.stop_capture() == {"status": "stopped"}
        assert state.capture_running is False

    def test_dropped_packets_is_exposed_in_status(self, svc, state):
        state.dropped_packets = 7
        assert svc.capture_status()["dropped_packets"] == 7


# ══ 增强抓包生命周期 ══════════════════════════════════════════════


class TestEnhancedLifecycle:
    @pytest.fixture(autouse=True)
    def _no_real_capture(self, monkeypatch):
        """增强抓包 worker 内部会 import run_enhanced_capture —— 换掉它。

        默认实现只是把 enhanced_capture_running 置回 False（模拟"跑完了"）。
        """

        def _fake(duration, stop_filter=None, iface=None):
            return (0, 0)

        monkeypatch.setattr(
            "campus_ids.capture.enhanced_features.run_enhanced_capture", _fake
        )

    def test_start_initialises_result_and_marks_running(self, svc, state):
        assert svc.start_enhanced(duration=15) == {"status": "started"}
        assert state.enhanced_capture_running is False, "worker 跑完后必须复位"
        result = state.enhanced_capture_result
        assert result["status"] == "completed" and result["duration"] == 15
        assert (result["packets"], result["flows"]) == (0, 0)

    def test_start_is_idempotent_while_running(self, svc, state):
        state.enhanced_capture_running = True
        assert svc.start_enhanced(duration=15) == {"status": "already_running"}
        assert state.enhanced_capture_result == {}, "拒绝启动时不得污染已有结果"

    def test_stop_clears_running_flag(self, svc, state):
        state.enhanced_capture_running = True
        assert svc.stop_enhanced() == {"status": "stopped"}
        assert state.enhanced_capture_running is False

    def test_status_merges_running_flag_over_result(self, svc, state):
        """`running` 必须反映实时标志，而不是结果里那份陈旧快照。"""
        state.enhanced_capture_result = {"status": "running", "packets": 3, "running": False}
        state.enhanced_capture_running = True

        status = svc.enhanced_status()
        assert status["running"] is True
        assert status["packets"] == 3

    def test_status_on_empty_result_is_just_running(self, svc, state):
        assert svc.enhanced_status() == {"running": False}

    def test_status_does_not_mutate_stored_result(self, svc, state):
        """`enhanced_status` 必须拷贝 —— 否则调用方改返回值会污染运行状态。"""
        state.enhanced_capture_result = {"status": "running", "running": False}
        status = svc.enhanced_status()
        status["running"] = "被外部改了"
        status["status"] = "被外部改了"
        assert state.enhanced_capture_result == {"status": "running", "running": False}


# ══ _capture_worker（真实包解析）══════════════════════════════════


class TestCaptureWorker:
    def test_tcp_syn_packet_is_queued_with_flags(self, svc, state, patch_sniff):
        patch_sniff([IP(src="10.0.0.1", dst="10.0.0.2") / TCP(sport=1234, dport=80, flags="S")])
        svc.start_capture()
        _wait_for_thread(state)

        rows = _drain(state)
        assert len(rows) == 1, f"应入队 1 个包，实际 {rows}"
        row = rows[0]
        assert row["src_ip"] == "10.0.0.1" and row["dst_ip"] == "10.0.0.2"
        assert row["sport"] == 1234 and row["dport"] == 80
        assert row["proto"] == "TCP"
        assert row["is_syn"] is True
        assert row["is_dns"] is False
        assert row["length"] > 0 and row["timestamp"] > 0

    def test_non_syn_tcp_is_not_flagged_as_syn(self, svc, state, patch_sniff):
        patch_sniff([IP() / TCP(sport=1, dport=80, flags="A")])
        svc.start_capture()
        _wait_for_thread(state)
        assert _drain(state)[0]["is_syn"] is False

    def test_dns_packet_is_flagged_by_dst_port(self, svc, state, patch_sniff):
        patch_sniff([IP() / UDP(sport=33333, dport=DNS_PORT)])
        svc.start_capture()
        _wait_for_thread(state)

        row = _drain(state)[0]
        assert row["is_dns"] is True
        assert row["proto"] == "UDP"
        assert row["is_syn"] is False

    def test_other_udp_port_is_not_dns(self, svc, state, patch_sniff):
        patch_sniff([IP() / UDP(sport=33333, dport=DNS_PORT + 1)])
        svc.start_capture()
        _wait_for_thread(state)
        assert _drain(state)[0]["is_dns"] is False

    def test_non_ip_packet_is_skipped(self, svc, state, patch_sniff):
        """ARP 没有 IP 层 → `_parse_base_fields` 返回 None → 不得入队、不得抛异常。"""
        patch_sniff([Ether() / ARP()])
        svc.start_capture()
        _wait_for_thread(state)
        assert _drain(state) == []

    def test_stop_filter_reads_live_flag_not_captured_value(self, svc, state, patch_sniff):
        """stop_filter 必须在**每次调用时**读 `capture_running`。

        若写成 `lambda _: not self._state.capture_running` 之外的形式（例如闭包
        捕获了 True），`stop_capture()` 就永远无法让 sniff 退出 —— 抓包线程会一直
        挂着，直到进程结束。这里直接调那个 lambda 来验证它是实时求值的。
        """
        fake = patch_sniff([])
        svc.start_capture()

        assert fake.stop_filters, "sniff 未收到 stop_filter"
        stop_filter = fake.stop_filters[0]

        state.capture_running = True
        assert stop_filter(None) is False, "运行中不应停止"
        state.capture_running = False
        assert stop_filter(None) is True, "capture_running 置 False 后必须能退出"

    def test_full_queue_increments_dropped_counter(self, svc, state, patch_sniff):
        """队列满时必须计数丢弃，而不是抛异常中断抓包线程。"""
        state.packet_queue = queue.Queue(maxsize=1)
        patch_sniff([IP() / TCP(sport=i, dport=80) for i in range(3)])
        svc.start_capture()
        _wait_for_thread(state)

        assert len(_drain(state)) == 1
        assert state.dropped_packets == 2, f"应丢 2 个，实际 {state.dropped_packets}"

    def test_tls_port_triggers_tls_analysis(self, svc, state, tls, patch_sniff):
        pkt = IP() / TCP(sport=1234, dport=TLS_PORT, flags="S")
        patch_sniff([pkt])
        svc.start_capture()
        _wait_for_thread(state)
        assert tls.calls == [pkt], "命中 TLS_PORTS 的包必须交给 tls_analyzer"

    def test_non_tls_port_does_not_trigger_tls_analysis(self, svc, state, tls, patch_sniff):
        patch_sniff([IP() / TCP(sport=1234, dport=80)])
        svc.start_capture()
        _wait_for_thread(state)
        assert tls.calls == []

    def test_dns_udp_does_not_trigger_tls_analysis(self, svc, state, tls, patch_sniff):
        """TLS 只在 TCP 上解析 —— UDP/53 不得触发。"""
        patch_sniff([IP() / UDP(sport=1, dport=DNS_PORT)])
        svc.start_capture()
        _wait_for_thread(state)
        assert tls.calls == []

    def test_tls_analyzer_exception_does_not_break_capture(self, svc, state, patch_sniff):
        """TLS 解析失败必须被吞掉 —— 否则一个坏包会终止整条抓包线程。"""
        tls = FakeTlsAnalyzer(boom=True)
        svc = CaptureService(state=state, dual_detector=MagicMock(), tls_analyzer=tls)

        patch_sniff([
            IP() / TCP(sport=1234, dport=TLS_PORT),
            IP() / TCP(sport=5678, dport=80),
        ])
        svc.start_capture()  # 不得抛异常
        _wait_for_thread(state)

        assert len(tls.calls) == 1, "异常后仍应处理后续包"
        assert len(_drain(state)) == 2

    def test_sniff_failure_is_logged_not_raised(self, svc, state, patch_sniff, caplog):
        """网卡不可用（权限/接口名错误）时线程必须安静退出并记日志。"""
        patch_sniff([], raises=OSError("无法打开网卡"))
        with caplog.at_level("ERROR", logger="campus_ids.services.capture_service"):
            svc.start_capture()  # 不得抛异常
        _wait_for_thread(state)
        assert any("后台抓包线程异常退出" in r.getMessage() for r in caplog.records)


# ══ _enhanced_capture_worker ═══════════════════════════════════════


class TestEnhancedCaptureWorker:
    """增强抓包 worker —— 结果字典是**唯一**对外可观测产物（旧实现易漏字段）。"""

    def _svc(self, state) -> CaptureService:
        return CaptureService(
            state=state, dual_detector=MagicMock(), tls_analyzer=FakeTlsAnalyzer()
        )

    def _install(self, monkeypatch, result) -> None:
        monkeypatch.setattr(
            "campus_ids.capture.enhanced_features.run_enhanced_capture",
            lambda duration, stop_filter=None, iface=None: result,
        )

    def test_worker_passes_resolved_iface_to_capture(self, monkeypatch, state):
        """start_enhanced 必须把 _resolve_iface 的结果传给 run_enhanced_capture。

        2026-09-19 教训：scapy sniff 不传 iface 时回退 conf.iface，Windows 上
        常指向非活动适配器 → 增强抓包 0 包（基础抓包同款坑）。
        """
        captured: dict = {}

        def _fake(duration, stop_filter=None, iface=None):
            captured["iface"] = iface
            return (1, 1)

        monkeypatch.setattr(
            "campus_ids.capture.enhanced_features.run_enhanced_capture", _fake
        )
        svc = self._svc(state)
        monkeypatch.setattr(svc, "_resolve_iface", lambda _req: "\\Device\\NPF_FAKE")
        svc.start_enhanced(duration=5)

        assert captured["iface"] == "\\Device\\NPF_FAKE"

    def test_result_tuple_becomes_completed_status(self, monkeypatch, state):
        self._install(monkeypatch, (1500, 42))
        self._svc(state).start_enhanced(duration=5)

        result = state.enhanced_capture_result
        assert result == {
            "status": "completed",
            "duration": 5,
            "packets": 1500,
            "flows": 42,
            "error": None,
        }
        assert state.enhanced_capture_running is False

    def test_none_result_becomes_error_status(self, monkeypatch, state):
        """`run_enhanced_capture` 返回 None = 抓包失败 → 必须报 error 而不是 completed。"""
        self._install(monkeypatch, None)
        self._svc(state).start_enhanced(duration=5)

        result = state.enhanced_capture_result
        assert result["status"] == "error"
        assert result["error"] == "抓包失败"
        assert (result["packets"], result["flows"]) == (0, 0)
        assert state.enhanced_capture_running is False

    def test_worker_publishes_running_snapshot_before_capturing(self, monkeypatch, state):
        """`run_enhanced_capture` 调用期间，结果必须是 `running` —— 否则前端进度条无起点。"""
        seen: dict = {}

        def _fake(duration, stop_filter=None, iface=None):
            seen["during"] = dict(state.enhanced_capture_result)
            seen["running_flag_during"] = state.enhanced_capture_running
            return (5, 6)

        monkeypatch.setattr(
            "campus_ids.capture.enhanced_features.run_enhanced_capture", _fake
        )
        self._svc(state).start_enhanced(duration=30)

        assert seen["during"]["status"] == "running"
        assert seen["during"]["duration"] == 30
        assert seen["running_flag_during"] is True, "抓包期间必须标记为运行中"
        # 收尾后回转
        assert state.enhanced_capture_result["status"] == "completed"
        assert state.enhanced_capture_running is False

    def test_stop_filter_responds_to_external_stop(self, monkeypatch, state):
        """`stop_enhanced()` 把标志置 False 后，stop_filter 必须立刻返回 True。"""
        captured: dict = {}

        def _fake(duration, stop_filter=None, iface=None):
            captured["stop_filter"] = stop_filter
            captured["during_running"] = stop_filter(None)
            return (1, 1)

        monkeypatch.setattr(
            "campus_ids.capture.enhanced_features.run_enhanced_capture", _fake
        )
        self._svc(state).start_enhanced(duration=60)

        # 抓包期间：运行中且未到期 → False
        assert captured["during_running"] is False
        # worker 收尾时把标志置回 False → 同一个 lambda 现在必须返回 True
        assert state.enhanced_capture_running is False
        assert captured["stop_filter"](None) is True, "标志置 False 后应停止"

    def test_stop_filter_responds_to_deadline(self, monkeypatch, state):
        """`duration` 到期后 stop_filter 必须返回 True，否则限时抓包变成无限抓包。

        注意这里在**仍标记为运行中**的时刻求值，从而把"到期"与"外部停止"两个
        停止条件隔离开 —— 否则两者都会让断言变绿，测不出到期分支是否真的存在。
        """
        captured: dict = {}

        def _fake(duration, stop_filter=None, iface=None):
            captured["running_flag"] = state.enhanced_capture_running
            captured["stop"] = stop_filter(None)
            return (1, 1)

        monkeypatch.setattr(
            "campus_ids.capture.enhanced_features.run_enhanced_capture", _fake
        )
        # duration=0 → stop_time = now → 立即到期
        self._svc(state).start_enhanced(duration=0)

        assert captured["running_flag"] is True, "前提：求值时仍标记为运行中"
        assert captured["stop"] is True, "duration 到期后应停止"


# ══ 网卡选择（2026-09-19 真机缺陷修复）════════════════════════════
#
# 缺陷：`sniff()` 不传 iface → scapy 用 conf.iface（Windows 上常为非活动
# 适配器）→ 抓包永远 0 包（原生 pcap 同机同协议 8s 可抓 548 包）。
# 修复：`start_capture(interface=...)` / `Settings.capture_iface` 解析后
# 显式传给 `sniff(iface=...)`。以下用例断言透传链路真实存在。


class TestCaptureIface:
    def test_friendly_name_resolved_and_passed_to_sniff(
        self, svc, state, patch_sniff, monkeypatch
    ):
        """友好名（如 WLAN）应被解析成 scapy 接口名并显式传给 sniff。"""
        fake = patch_sniff([])
        monkeypatch.setattr(
            "scapy.all.get_if_list",
            lambda: [r"\\Device\\NPF_Loopback", r"\\Device\\NPF_{WXYZ}"],
        )
        monkeypatch.setattr(
            "scapy.arch.windows.get_windows_if_list",
            lambda: [{"name": r"\\Device\\NPF_{WXYZ}", "description": "WLAN", "ips": []}],
        )

        svc.start_capture(interface="WLAN")
        _wait_for_thread(state)

        assert fake.kwargs_list, "sniff 未被调用"
        assert fake.kwargs_list[0].get("iface") == r"\\Device\\NPF_{WXYZ}"

    def test_scapy_native_name_used_directly(self, svc, state, patch_sniff, monkeypatch):
        """已是 scapy 接口名的输入直接透传，不再做友好名匹配。"""
        fake = patch_sniff([])
        monkeypatch.setattr(
            "scapy.all.get_if_list", lambda: [r"\\Device\\NPF_Loopback"]
        )

        svc.start_capture(interface=r"\\Device\\NPF_Loopback")
        _wait_for_thread(state)

        assert fake.kwargs_list[0].get("iface") == r"\\Device\\NPF_Loopback"

    def test_unresolvable_name_falls_back_to_none(self, svc, state, patch_sniff, monkeypatch):
        """无法解析的网卡名回退 None（scapy 默认）并告警，不阻断启动。"""
        fake = patch_sniff([])
        monkeypatch.setattr("scapy.all.get_if_list", lambda: [r"\\Device\\NPF_Loopback"])
        monkeypatch.setattr("scapy.arch.windows.get_windows_if_list", lambda: [])

        assert svc.start_capture(interface="NoSuchNic") == {"status": "started"}
        _wait_for_thread(state)

        assert fake.kwargs_list[0].get("iface", "missing") is None

    def test_no_config_uses_scapy_default(self, svc, state, patch_sniff, monkeypatch):
        """未配置任何网卡 → iface=None（与旧行为兼容，显式可见）。"""
        fake = patch_sniff([])
        monkeypatch.setattr("scapy.all.get_if_list", lambda: [])

        svc.start_capture()
        _wait_for_thread(state)

        assert fake.kwargs_list[0].get("iface", "missing") is None
