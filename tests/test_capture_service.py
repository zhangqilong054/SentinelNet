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

    F1 修复后 finally 块会复位 capture_running，空包立即返回会导致线程瞬间退出、
    标志位复位，测试无法观测 "running" 状态。因此空包时模拟真实 sniff 阻塞：
    等待 stop_filter 返回 True（即 stop_capture 被调用）才退出。
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
            if stop_filter and stop_filter(pkt):
                break
            prn(pkt)
            self.prn_calls += 1
        # 无包时模拟真实 sniff 阻塞：等待 stop_filter 触发
        if not self.packets and stop_filter is not None:
            import time
            for _ in range(500):  # 最多等 5 秒
                if stop_filter(None):
                    break
                time.sleep(0.01)


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


def _wait_enhanced(state: RuntimeState, timeout: float = 2.0) -> None:
    """等待增强抓包线程跑完。

    🔴 2026-09-20：增强抓包 worker 是**真线程**（基础抓包的 sniff 被换成同步假实现，
    这里没有）。只 `start_enhanced()` 不 join 就断言结果是**竞态**：单跑常绿，
    全量套件下线程调度被拖慢就整片变红（本轮实测 7 红）。凡断言 worker 产物
    （结果字典 / 捕获到的调用参数）必须先进本函数。
    """
    if state.enhanced_capture_thread is not None:
        state.enhanced_capture_thread.join(timeout=timeout)


# ══ 基础抓包生命周期 ══════════════════════════════════════════════


class TestBasicLifecycle:
    def test_start_then_status_then_stop(self, svc, state, patch_sniff):
        patch_sniff([])  # 不喂任何包，只检查状态机

        # 2026-09-20：状态新增 iface/iface_source/filter/queue_size（排障自证用）
        assert svc.capture_status() == {
            "running": False,
            "dropped_packets": 0,
            "iface": None,
            "iface_source": "unset",
            "filter": "",
            "queue_size": 0,
        }

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

        def _fake(duration, stop_filter=None, iface=None, bpf=""):
            return (0, 0)

        monkeypatch.setattr(
            "campus_ids.capture.enhanced_features.run_enhanced_capture", _fake
        )

    def test_start_initialises_result_and_marks_running(self, svc, state):
        assert svc.start_enhanced(duration=15) == {"status": "started"}
        _wait_enhanced(state)
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
        # 2026-09-20：状态新增 iface/filter（增强抓包同样要能自证用哪张卡）
        assert svc.enhanced_status() == {"running": False, "iface": None, "filter": ""}

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
        """网卡不可用（权限/接口名错误）时线程必须安静退出、记日志、并复位标志位。

        F1 修复后 finally 块保证 capture_running=False；
        F9 同步补上状态复位断言，防止缺陷被测试固化。
        """
        patch_sniff([], raises=OSError("无法打开网卡"))
        with caplog.at_level("ERROR", logger="campus_ids.services.capture_service"):
            svc.start_capture()  # 不得抛异常
        _wait_for_thread(state)
        assert any("后台抓包线程异常退出" in r.getMessage() for r in caplog.records)
        # F9: 必须断言状态复位 —— 否则 start_capture 返回 already_running，永久锁死
        assert state.capture_running is False, "异常退出后 capture_running 必须复位为 False"

    def test_sniff_failure_allows_restart(self, svc, state, patch_sniff):
        """F1 回归：异常退出后可以重新启动（不会返回 already_running）。"""
        patch_sniff([], raises=OSError("无法打开网卡"))
        svc.start_capture()
        _wait_for_thread(state)
        assert state.capture_running is False

        # 重新安装一个正常的 sniff，应该可以重新启动
        patch_sniff([IP() / TCP(sport=1234, dport=80)])
        result = svc.start_capture()
        assert result["status"] == "started"
        _wait_for_thread(state)


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
            lambda duration, stop_filter=None, iface=None, bpf="": result,
        )

    def test_worker_passes_resolved_iface_to_capture(self, monkeypatch, state):
        """start_enhanced 必须把 _resolve_iface 的结果传给 run_enhanced_capture。

        2026-09-19 教训：scapy sniff 不传 iface 时回退 conf.iface，Windows 上
        常指向非活动适配器 → 增强抓包 0 包（基础抓包同款坑）。
        """
        captured: dict = {}

        def _fake(duration, stop_filter=None, iface=None, bpf=""):
            captured["iface"] = iface
            return (1, 1)

        monkeypatch.setattr(
            "campus_ids.capture.enhanced_features.run_enhanced_capture", _fake
        )
        svc = self._svc(state)
        monkeypatch.setattr(svc, "_resolve_iface", lambda _req: "\\Device\\NPF_FAKE")
        svc.start_enhanced(duration=5)
        _wait_enhanced(state)

        assert captured["iface"] == "\\Device\\NPF_FAKE"

    def test_result_tuple_becomes_completed_status(self, monkeypatch, state):
        self._install(monkeypatch, (1500, 42))
        self._svc(state).start_enhanced(duration=5)
        _wait_enhanced(state)

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
        _wait_enhanced(state)

        result = state.enhanced_capture_result
        assert result["status"] == "error"
        assert result["error"] == "抓包失败"
        assert (result["packets"], result["flows"]) == (0, 0)
        assert state.enhanced_capture_running is False

    def test_worker_publishes_running_snapshot_before_capturing(self, monkeypatch, state):
        """`run_enhanced_capture` 调用期间，结果必须是 `running` —— 否则前端进度条无起点。"""
        seen: dict = {}

        def _fake(duration, stop_filter=None, iface=None, bpf=""):
            seen["during"] = dict(state.enhanced_capture_result)
            seen["running_flag_during"] = state.enhanced_capture_running
            return (5, 6)

        monkeypatch.setattr(
            "campus_ids.capture.enhanced_features.run_enhanced_capture", _fake
        )
        self._svc(state).start_enhanced(duration=30)
        _wait_enhanced(state)

        assert seen["during"]["status"] == "running"
        assert seen["during"]["duration"] == 30
        assert seen["running_flag_during"] is True, "抓包期间必须标记为运行中"
        # 收尾后回转
        assert state.enhanced_capture_result["status"] == "completed"
        assert state.enhanced_capture_running is False

    def test_stop_filter_responds_to_external_stop(self, monkeypatch, state):
        """`stop_enhanced()` 把标志置 False 后，stop_filter 必须立刻返回 True。"""
        captured: dict = {}

        def _fake(duration, stop_filter=None, iface=None, bpf=""):
            captured["stop_filter"] = stop_filter
            captured["during_running"] = stop_filter(None)
            return (1, 1)

        monkeypatch.setattr(
            "campus_ids.capture.enhanced_features.run_enhanced_capture", _fake
        )
        self._svc(state).start_enhanced(duration=60)
        _wait_enhanced(state)

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

        def _fake(duration, stop_filter=None, iface=None, bpf=""):
            captured["running_flag"] = state.enhanced_capture_running
            captured["stop"] = stop_filter(None)
            return (1, 1)

        monkeypatch.setattr(
            "campus_ids.capture.enhanced_features.run_enhanced_capture", _fake
        )
        # duration=0 → stop_time = now → 立即到期
        self._svc(state).start_enhanced(duration=0)
        _wait_enhanced(state)

        assert captured["running_flag"] is True, "前提：求值时仍标记为运行中"
        assert captured["stop"] is True, "duration 到期后应停止"

    def test_exception_resets_running_flag(self, monkeypatch, state):
        """F1/F9: 增强抓包异常退出后 enhanced_capture_running 必须复位为 False。

        run_enhanced_capture 抛异常时（如 BPF 非法 → Scapy_Exception），
        _enhanced_capture_worker 的 except BaseException + finally 必须复位标志位，
        否则后续 start_enhanced 返回 already_running → 永久锁死。
        """
        def _boom(duration, stop_filter=None, iface=None, bpf=""):
            raise RuntimeError("模拟 BPF 编译失败")

        monkeypatch.setattr(
            "campus_ids.capture.enhanced_features.run_enhanced_capture", _boom
        )
        svc = self._svc(state)
        result = svc.start_enhanced(duration=5)
        assert result["status"] == "started"
        _wait_enhanced(state)

        # 标志位必须复位
        assert state.enhanced_capture_running is False, \
            "异常退出后 enhanced_capture_running 必须复位为 False"
        # 结果字典必须是 error
        assert state.enhanced_capture_result["status"] == "error"
        # 异常退出后可以重新启动
        self._install(monkeypatch, (10, 2))
        result2 = svc.start_enhanced(duration=5)
        assert result2["status"] == "started"
        _wait_enhanced(state)


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


# ══ 网卡自动识别 / BPF 过滤（2026-09-20 抓包方案） ═════════════════


class TestAutodetectIface:
    """`autodetect_iface()` —— 用户不配置网卡时的兜底，取代 conf.iface。

    2026-09-19 实测教训：scapy `conf.iface` 在 Windows 上常指向非活动适配器，
    不传 iface 的 sniff 抓包永远 0 包。自动识别是「零配置也能抓到包」的关键。
    """

    def test_prefers_default_route_iface(self, monkeypatch):
        from campus_ids.services.capture_service import autodetect_iface

        monkeypatch.setattr("scapy.all.get_if_list", lambda: ["\\Device\\NPF_ETH", "WLAN"])
        monkeypatch.setattr(
            "scapy.all.conf.route.route",
            lambda _dst: ("\\Device\\NPF_ETH", "10.36.158.159", "10.36.158.194"),
        )

        assert autodetect_iface() == ("\\Device\\NPF_ETH", "auto:route")

    def test_falls_back_to_active_adapter_scoring(self, monkeypatch):
        """无默认路由（或路由网卡不在 scapy 列表）时按活动度打分。"""
        pytest.importorskip("scapy.arch.windows")
        from campus_ids.services.capture_service import autodetect_iface

        monkeypatch.setattr("scapy.all.get_if_list", lambda: ["eth"])
        monkeypatch.setattr(
            "scapy.all.conf.route.route",
            lambda _dst: (_ for _ in ()).throw(OSError("no route")),
        )
        monkeypatch.setattr(
            "scapy.arch.windows.get_windows_if_list",
            lambda: [
                {
                    "name": "eth",
                    "description": "Realtek PCIe GbE Family Controller",
                    "ips": ["10.36.158.159"],
                }
            ],
        )

        assert autodetect_iface() == ("eth", "auto:active")

    def test_skips_virtual_and_link_local_only_adapters(self, monkeypatch):
        """虚拟网卡（VMware/Hyper-V/蓝牙）与只有 169.254 链路本地地址的网卡一律跳过。"""
        pytest.importorskip("scapy.arch.windows")
        from campus_ids.services.capture_service import autodetect_iface

        monkeypatch.setattr("scapy.all.get_if_list", lambda: ["vmnet8", "eth", "wlan"])
        monkeypatch.setattr(
            "scapy.all.conf.route.route",
            lambda _dst: (_ for _ in ()).throw(OSError("no route")),
        )
        monkeypatch.setattr(
            "scapy.arch.windows.get_windows_if_list",
            lambda: [
                {
                    "name": "vmnet8",
                    "description": "VMware Virtual Ethernet Adapter for VMnet8",
                    "ips": ["192.168.190.1"],
                },
                {
                    "name": "eth",
                    "description": "Realtek PCIe GbE Family Controller",
                    "ips": ["169.254.45.76"],
                },
                {
                    "name": "wlan",
                    "description": "Intel(R) Wi-Fi 6 AX203",
                    "ips": ["2408:8421:b173:2b88::1"],
                },
            ],
        )

        assert autodetect_iface() == ("wlan", "auto:active")

    def test_returns_fallback_when_nothing_usable(self, monkeypatch):
        pytest.importorskip("scapy.arch.windows")
        from campus_ids.services.capture_service import autodetect_iface

        monkeypatch.setattr("scapy.all.get_if_list", lambda: [])
        monkeypatch.setattr(
            "scapy.all.conf.route.route",
            lambda _dst: (_ for _ in ()).throw(OSError("no route")),
        )
        monkeypatch.setattr("scapy.arch.windows.get_windows_if_list", lambda: [])

        assert autodetect_iface() == (None, "fallback")


class TestCaptureFilter:
    """BPF 过滤表达式构造 —— 内核态过滤 + 排除面板自身流量。"""

    def test_default_filter_excludes_web_port(self, monkeypatch):
        from campus_ids.runtime.settings import get_settings
        from campus_ids.services.capture_service import build_capture_filter

        s = get_settings()
        monkeypatch.setattr(s, "capture_filter", "tcp or udp")
        monkeypatch.setattr(s, "capture_exclude_web_port", True)
        monkeypatch.setattr(s, "web_port", 5000)

        assert build_capture_filter() == "(tcp or udp) and not port 5000"

    def test_exclude_web_port_can_be_disabled(self, monkeypatch):
        """关闭该开关后必须原样返回用户表达式（检测面板端口攻击的场景）。"""
        from campus_ids.runtime.settings import get_settings
        from campus_ids.services.capture_service import build_capture_filter

        s = get_settings()
        monkeypatch.setattr(s, "capture_filter", "tcp or udp")
        monkeypatch.setattr(s, "capture_exclude_web_port", False)

        assert build_capture_filter() == "tcp or udp"

    def test_empty_filter_means_no_bpf(self, monkeypatch):
        """空表达式 = 不过滤，不得凭空拼出残句 ` and not port X`。"""
        from campus_ids.runtime.settings import get_settings
        from campus_ids.services.capture_service import build_capture_filter

        s = get_settings()
        monkeypatch.setattr(s, "capture_filter", "")
        monkeypatch.setattr(s, "capture_exclude_web_port", True)

        assert build_capture_filter() == ""


class TestFilterWiring:
    """过滤表达式必须真的抵达 sniff —— 否则只是配置摆设。"""

    def test_base_capture_passes_iface_and_filter_to_sniff(
        self, svc, state, patch_sniff, monkeypatch
    ):
        fake = patch_sniff([])
        monkeypatch.setattr(svc, "_resolve_iface", lambda _req: "\\Device\\NPF_FAKE")
        monkeypatch.setattr(svc, "_build_filter", lambda: "tcp or udp")

        svc.start_capture()
        _wait_for_thread(state)

        kwargs = fake.kwargs_list[0]
        assert kwargs["iface"] == "\\Device\\NPF_FAKE"
        assert kwargs["filter"] == "tcp or udp"

    def test_capture_status_exposes_iface_and_filter(
        self, svc, state, patch_sniff, monkeypatch
    ):
        """状态接口要能自证「到底在用哪张卡、什么过滤」—— 排障入口。

        走真实解析路径（只钉住设置与 scapy 接口枚举），这样 `iface_source`
        才会被真实标注 —— 直接 patch `_resolve_iface` 会让来源标签永远 unset。
        """
        from campus_ids.runtime.settings import get_settings

        patch_sniff([])
        monkeypatch.setattr(get_settings(), "capture_iface", "WLAN")
        monkeypatch.setattr("scapy.all.get_if_list", lambda: ["WLAN"])
        monkeypatch.setattr(svc, "_build_filter", lambda: "(tcp or udp) and not port 5000")

        svc.start_capture()
        _wait_for_thread(state)

        status = svc.capture_status()
        assert status["iface"] == "WLAN"
        assert status["filter"] == "(tcp or udp) and not port 5000"
        assert status["iface_source"] == "config"

    def test_capture_status_marks_autodetected_source(
        self, svc, state, patch_sniff, monkeypatch
    ):
        """未配置网卡时，来源必须标成 auto:* —— 让「零配置抓包」可被证实。"""
        from campus_ids.runtime.settings import get_settings

        patch_sniff([])
        monkeypatch.setattr(get_settings(), "capture_iface", "")
        monkeypatch.setattr("scapy.all.get_if_list", lambda: ["\\Device\\NPF_ETH"])
        monkeypatch.setattr(
            "scapy.all.conf.route.route",
            lambda _dst: ("\\Device\\NPF_ETH", "10.0.0.2", "10.0.0.1"),
        )

        svc.start_capture()
        _wait_for_thread(state)

        status = svc.capture_status()
        assert status["iface"] == "\\Device\\NPF_ETH"
        assert status["iface_source"] == "auto:route"

    def test_enhanced_capture_passes_filter(self, monkeypatch, state):
        captured: dict = {}

        def _fake(duration, stop_filter=None, iface=None, bpf=""):
            captured["bpf"] = bpf
            return (1, 1)

        monkeypatch.setattr(
            "campus_ids.capture.enhanced_features.run_enhanced_capture", _fake
        )
        svc = CaptureService(
            state=state, dual_detector=MagicMock(), tls_analyzer=FakeTlsAnalyzer()
        )
        monkeypatch.setattr(svc, "_build_filter", lambda: "tcp or udp")
        svc.start_enhanced(duration=5)
        _wait_enhanced(state)

        assert captured["bpf"] == "tcp or udp"


# ══ BPF 预校验（F1 修复配套）════════════════════════════════════════


class TestValidateBpf:
    """validate_bpf 在启动前拦截非法 BPF，避免线程启动后锁死。"""

    def test_valid_bpf_returns_none(self):
        from campus_ids.services.capture_service import validate_bpf

        assert validate_bpf("tcp or udp") is None
        assert validate_bpf("tcp port 80") is None

    def test_empty_bpf_returns_none(self):
        from campus_ids.services.capture_service import validate_bpf

        assert validate_bpf("") is None
        assert validate_bpf(None) is None

    def test_invalid_bpf_returns_error(self):
        from campus_ids.services.capture_service import validate_bpf

        result = validate_bpf("tcp and and bogus")
        assert result is not None
        assert "BPF" in result or "非法" in result

    def test_start_capture_rejects_invalid_bpf(self, svc, state, monkeypatch):
        """非法 BPF 必须在 start_capture 入口被拦截，不启动线程。"""
        from campus_ids.runtime.settings import get_settings

        monkeypatch.setattr(get_settings(), "capture_filter", "tcp and and bogus")
        result = svc.start_capture()
        assert result["status"] == "error"
        assert "BPF" in result.get("message", "") or "非法" in result.get("message", "")
        # 线程不应启动
        assert state.capture_running is False

    def test_start_enhanced_rejects_invalid_bpf(self, svc, state, monkeypatch):
        """非法 BPF 必须在 start_enhanced 入口被拦截。"""
        from campus_ids.runtime.settings import get_settings

        monkeypatch.setattr(get_settings(), "capture_filter", "tcp and and bogus")
        result = svc.start_enhanced(duration=10)
        assert result["status"] == "error"
        assert state.enhanced_capture_running is False


# ══ R3.3: dropped_packets 原子性回归 ══════════════════════════════════════


class TestDroppedPacketsAtomicity:
    """R3.3: 验证 dropped_packets 在多读单写下的一致性。

    CPython GIL 保证 int += 1 原子性（单写者 capture 线程），
    此测试确认：并发读取不会读到半写值，且最终计数准确。
    """

    def test_concurrent_read_while_dropping(self, state):
        """多线程并发读 dropped_packets，单线程写入，最终值必须一致。"""
        import threading

        state.dropped_packets = 0
        N_WRITES = 5000
        reads: list[int] = []

        def writer() -> None:
            for _ in range(N_WRITES):
                state.dropped_packets += 1

        def reader() -> None:
            for _ in range(N_WRITES):
                reads.append(state.dropped_packets)

        w = threading.Thread(target=writer)
        r = threading.Thread(target=reader)
        w.start()
        r.start()
        w.join()
        r.join()

        # 最终值必须精确
        assert state.dropped_packets == N_WRITES
        # 所有读取值必须在 [0, N_WRITES] 范围内（不会读到半写值）
        assert all(0 <= v <= N_WRITES for v in reads)


# ══ R3.1/R3.2: health 端点 capture 段新字段 ═══════════════════════════════


class TestHealthCaptureFields:
    """R3.1/R3.2: 验证 /api/health capture 段新增字段。

    - R3.1: resolved 标记 + 未启动时回退显示候选口径
    - R3.2: queue_capacity / queue_usage / backlog_level
    """

    def test_stopped_capture_shows_resolved_false_and_preview(
        self, svc, state, monkeypatch
    ):
        """抓包未启动时：resolved=false，显示候选口径（autodetect/config）。"""
        from campus_ids.web_new.api.system import health_check
        from unittest.mock import MagicMock

        # 确保 capture_service 存在且 capture_status 返回 running=False
        state.capture_running = False
        monkeypatch.setattr(
            svc, "capture_status",
            lambda: {
                "running": False, "dropped_packets": 0,
                "iface": None, "iface_source": "unset", "filter": "",
                "queue_size": 0,
            },
        )

        # mock autodetect_iface / build_capture_filter（源模块级函数）
        monkeypatch.setattr(
            "campus_ids.services.capture_service.autodetect_iface",
            lambda: ("\\Device\\NPF_ETH", "auto:route"),
        )
        monkeypatch.setattr(
            "campus_ids.services.capture_service.build_capture_filter",
            lambda: "tcp or udp",
        )

        # 构造 mock request
        mock_request = MagicMock()
        mock_request.app.state.runtime_state = state
        mock_request.app.state.capture_service = svc

        import asyncio
        result = asyncio.run(health_check(mock_request))

        cap = result.components.get("capture", {})
        assert cap.get("resolved") is False, "未启动时应 resolved=false"
        assert cap.get("iface") == "\\Device\\NPF_ETH"
        assert cap.get("iface_source") == "auto:route"
        assert cap.get("filter") == "tcp or udp"

    def test_running_capture_shows_resolved_true(self, svc, state, monkeypatch):
        """抓包运行中：resolved=true，显示 service 快照口径。"""
        from campus_ids.web_new.api.system import health_check
        from unittest.mock import MagicMock

        state.capture_running = True
        monkeypatch.setattr(
            svc, "capture_status",
            lambda: {
                "running": True, "dropped_packets": 0,
                "iface": "\\Device\\NPF_REAL", "iface_source": "config",
                "filter": "tcp port 80", "queue_size": 5,
            },
        )

        mock_request = MagicMock()
        mock_request.app.state.runtime_state = state
        mock_request.app.state.capture_service = svc

        import asyncio
        result = asyncio.run(health_check(mock_request))

        cap = result.components.get("capture", {})
        assert cap.get("resolved") is True, "运行中应 resolved=true"
        assert cap.get("iface") == "\\Device\\NPF_REAL"
        assert cap.get("iface_source") == "config"

    def test_backlog_level_ok_when_queue_low(self, state, monkeypatch):
        """R3.2: 队列占用 <50% 时 backlog_level=ok。"""
        from campus_ids.web_new.api.system import health_check
        from unittest.mock import MagicMock

        # queue_size=10, capacity=20000 → usage=0.05% → ok
        state.packet_queue = queue.Queue(maxsize=20000)
        for _ in range(10):
            state.packet_queue.put_nowait({"test": True})

        mock_request = MagicMock()
        mock_request.app.state.runtime_state = state
        mock_request.app.state.capture_service = None

        import asyncio
        result = asyncio.run(health_check(mock_request))

        cap = result.components.get("capture", {})
        assert cap.get("queue_capacity") == 20000
        assert cap.get("queue_usage") == 10 / 20000
        assert cap.get("backlog_level") == "ok"

    def test_backlog_level_warn_when_queue_half(self, state, monkeypatch):
        """R3.2: 队列占用 >=50% 时 backlog_level=warn。"""
        from campus_ids.web_new.api.system import health_check
        from unittest.mock import MagicMock

        # queue_size=10001, capacity=20000 → usage=50.005% → warn
        state.packet_queue = queue.Queue(maxsize=20000)
        for _ in range(10001):
            state.packet_queue.put_nowait({"test": True})

        mock_request = MagicMock()
        mock_request.app.state.runtime_state = state
        mock_request.app.state.capture_service = None

        import asyncio
        result = asyncio.run(health_check(mock_request))

        cap = result.components.get("capture", {})
        assert cap.get("backlog_level") == "warn"

    def test_backlog_level_critical_when_queue_near_full(self, state, monkeypatch):
        """R3.2: 队列占用 >=80% 时 backlog_level=critical。"""
        from campus_ids.web_new.api.system import health_check
        from unittest.mock import MagicMock

        # queue_size=16001, capacity=20000 → usage=80.005% → critical
        state.packet_queue = queue.Queue(maxsize=20000)
        for _ in range(16001):
            state.packet_queue.put_nowait({"test": True})

        mock_request = MagicMock()
        mock_request.app.state.runtime_state = state
        mock_request.app.state.capture_service = None

        import asyncio
        result = asyncio.run(health_check(mock_request))

        cap = result.components.get("capture", {})
        assert cap.get("backlog_level") == "critical"
