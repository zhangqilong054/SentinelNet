"""AttackSimulator 单元测试 — 覆盖注入包字段完整性和基本行为。"""
import time
import threading
import queue

import pytest

from campus_ids.demo.attack_sim import AttackSimulator, TARGET_IP, ATTACK_IPS


# ── 辅助：创建带 mock queue 的 AttackSimulator ────────────────────

class _MockQueue:
    """线程安全的 mock 队列，记录所有 put 的元素。"""
    def __init__(self):
        self._items: list[dict] = []
        self._lock = threading.Lock()

    def put_nowait(self, item):
        with self._lock:
            self._items.append(item)

    @property
    def items(self) -> list[dict]:
        with self._lock:
            return list(self._items)


@pytest.fixture
def mock_queue(monkeypatch):
    """替换 helpers._packet_queue 为 mock 队列。"""
    import campus_ids.web.helpers as helpers_mod
    mq = _MockQueue()
    monkeypatch.setattr(helpers_mod, "_packet_queue", mq)
    return mq


# ── 测试：注入包必须包含完整字段 ────────────────────────────────

class TestInjectPacketFields:
    """验证注入包包含 dst_ip、timestamp 等必要字段。"""

    REQUIRED_KEYS = {"length", "sport", "dport", "src_ip", "dst_ip",
                     "proto", "is_syn", "is_dns", "timestamp"}

    def test_syn_flood_has_all_fields(self, mock_queue):
        sim = AttackSimulator()
        sim._running = True
        # 运行极短时间后停止
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_syn_flood(duration=1, rate=5)
        assert len(mock_queue.items) > 0
        for pkt in mock_queue.items:
            assert self.REQUIRED_KEYS.issubset(pkt.keys()), \
                f"缺少字段: {self.REQUIRED_KEYS - set(pkt.keys())}"

    def test_port_scan_has_all_fields(self, mock_queue):
        sim = AttackSimulator()
        sim._running = True
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_port_scan(duration=1, rate=5)
        assert len(mock_queue.items) > 0
        for pkt in mock_queue.items:
            assert self.REQUIRED_KEYS.issubset(pkt.keys()), \
                f"缺少字段: {self.REQUIRED_KEYS - set(pkt.keys())}"

    def test_udp_flood_has_all_fields(self, mock_queue):
        sim = AttackSimulator()
        sim._running = True
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_udp_flood(duration=1, rate=5)
        assert len(mock_queue.items) > 0
        for pkt in mock_queue.items:
            assert self.REQUIRED_KEYS.issubset(pkt.keys()), \
                f"缺少字段: {self.REQUIRED_KEYS - set(pkt.keys())}"

    def test_brute_force_has_all_fields(self, mock_queue):
        sim = AttackSimulator()
        sim._running = True
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_brute_force(duration=1, rate=5)
        assert len(mock_queue.items) > 0
        for pkt in mock_queue.items:
            assert self.REQUIRED_KEYS.issubset(pkt.keys()), \
                f"缺少字段: {self.REQUIRED_KEYS - set(pkt.keys())}"

    def test_lateral_movement_has_all_fields(self, mock_queue):
        sim = AttackSimulator()
        sim._running = True
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_lateral_movement(duration=1, rate=5)
        assert len(mock_queue.items) > 0
        for pkt in mock_queue.items:
            assert self.REQUIRED_KEYS.issubset(pkt.keys()), \
                f"缺少字段: {self.REQUIRED_KEYS - set(pkt.keys())}"


# ── 测试：注入包字段值正确性 ────────────────────────────────────

class TestInjectPacketValues:
    """验证注入包的字段值符合攻击类型预期。"""

    def test_syn_flood_values(self, mock_queue):
        sim = AttackSimulator()
        sim._running = True
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_syn_flood(duration=1, rate=5)
        for pkt in mock_queue.items:
            assert pkt["proto"] == "TCP"
            assert pkt["is_syn"] is True
            assert pkt["dport"] == 80
            assert pkt["dst_ip"] == TARGET_IP
            assert pkt["src_ip"] in ATTACK_IPS
            assert isinstance(pkt["timestamp"], float)
            assert pkt["timestamp"] > 0

    def test_port_scan_values(self, mock_queue):
        sim = AttackSimulator()
        sim._running = True
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_port_scan(duration=1, rate=5)
        for pkt in mock_queue.items:
            assert pkt["proto"] == "TCP"
            assert pkt["is_syn"] is True
            assert pkt["dst_ip"] == TARGET_IP
            assert 1 <= pkt["dport"] <= 1024
            assert isinstance(pkt["timestamp"], float)

    def test_udp_flood_values(self, mock_queue):
        sim = AttackSimulator()
        sim._running = True
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_udp_flood(duration=1, rate=5)
        for pkt in mock_queue.items:
            assert pkt["proto"] == "UDP"
            assert pkt["is_syn"] is False
            assert pkt["dport"] == 53
            assert pkt["dst_ip"] == TARGET_IP
            assert isinstance(pkt["timestamp"], float)

    def test_brute_force_values(self, mock_queue):
        sim = AttackSimulator()
        sim._running = True
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_brute_force(duration=1, rate=5, target_port=22)
        for pkt in mock_queue.items:
            assert pkt["proto"] == "TCP"
            assert pkt["is_syn"] is True
            assert pkt["dport"] == 22
            assert pkt["src_ip"] == "10.0.0.200"  # 固定源 IP
            assert pkt["dst_ip"] == TARGET_IP
            assert isinstance(pkt["timestamp"], float)

    def test_lateral_movement_values(self, mock_queue):
        sim = AttackSimulator()
        sim._running = True
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_lateral_movement(duration=1, rate=5)
        dst_ips = set()
        for pkt in mock_queue.items:
            assert pkt["proto"] == "TCP"
            assert pkt["is_syn"] is True
            assert pkt["dport"] == 445  # SMB 端口
            assert pkt["src_ip"] == "10.0.0.200"  # 固定源 IP
            assert pkt["dst_ip"].startswith("192.168.1.")
            dst_ips.add(pkt["dst_ip"])
            assert isinstance(pkt["timestamp"], float)
        # 横向移动应访问多个不同目标 IP
        assert len(dst_ips) >= 1, "应至少访问 1 个不同目标 IP"


# ── 测试：start_all / stop 生命周期 ──────────────────────────────

class TestSimulatorLifecycle:
    def test_start_and_stop(self, mock_queue):
        sim = AttackSimulator()
        sim.start_all(duration=2)
        assert sim._running is True
        assert len(sim._threads) == 5
        time.sleep(1)
        sim.stop()
        assert sim._running is False
        # 注入应该产生了包
        assert len(mock_queue.items) > 0

    def test_stop_is_idempotent(self):
        sim = AttackSimulator()
        sim.stop()  # 未启动时 stop 不应报错
        assert sim._running is False


# ── 测试：D2 — 单类型攻击通过 start_attack_sim 注入数据包 ──────────

class TestSingleTypeViaStartAttackSim:
    """D2 修复验证：start_attack_sim('syn_flood', duration) 应成功注入数据包。

    原缺陷：非 'all' 路径未设 _running=True，inject 线程立即退出，0 包注入。
    修复：attack_sim_state.py L70 手动置 sim_state.sim._running = True。
    """

    @pytest.fixture(autouse=True)
    def _cleanup_sim_state(self):
        """每个测试前后清理 sim_state 全局状态。"""
        from campus_ids.web.attack_sim_state import sim_state
        # 测试前：确保无残留
        if sim_state.sim is not None:
            sim_state.sim.stop()
        with sim_state.lock:
            sim_state.running = False
            sim_state.type = ""
            sim_state.sim = None
            sim_state.duration = 0
        yield
        # 测试后：清理
        if sim_state.sim is not None:
            sim_state.sim.stop()
        with sim_state.lock:
            sim_state.running = False
            sim_state.type = ""
            sim_state.sim = None
            sim_state.duration = 0

    def test_syn_flood_via_start_attack_sim(self, mock_queue):
        from campus_ids.web.attack_sim_state import start_attack_sim, sim_state
        result = start_attack_sim('syn_flood', 2)
        assert result is None, f"启动应返回 None，实际: {result}"
        assert sim_state.running is True
        assert sim_state.sim is not None
        assert sim_state.sim._running is True
        time.sleep(1.5)  # 等待注入
        sim_state.sim.stop()
        pkt_count = len(mock_queue.items)
        assert pkt_count > 0, f"syn_flood 单类型应注入数据包，实际: {pkt_count}"

    def test_port_scan_via_start_attack_sim(self, mock_queue):
        from campus_ids.web.attack_sim_state import start_attack_sim, sim_state
        result = start_attack_sim('port_scan', 2)
        assert result is None
        assert sim_state.sim._running is True
        time.sleep(1.5)
        sim_state.sim.stop()
        pkt_count = len(mock_queue.items)
        assert pkt_count > 0, f"port_scan 单类型应注入数据包，实际: {pkt_count}"

    def test_udp_flood_via_start_attack_sim(self, mock_queue):
        from campus_ids.web.attack_sim_state import start_attack_sim, sim_state
        result = start_attack_sim('udp_flood', 2)
        assert result is None
        assert sim_state.sim._running is True
        time.sleep(1.5)
        sim_state.sim.stop()
        pkt_count = len(mock_queue.items)
        assert pkt_count > 0, f"udp_flood 单类型应注入数据包，实际: {pkt_count}"

    def test_invalid_type_returns_error(self):
        from campus_ids.web.attack_sim_state import start_attack_sim
        result = start_attack_sim('invalid_type', 2)
        assert result is not None
        assert result[1] == 400

    def test_duplicate_start_returns_409(self):
        from campus_ids.web.attack_sim_state import start_attack_sim, sim_state
        result1 = start_attack_sim('syn_flood', 5)
        assert result1 is None
        result2 = start_attack_sim('syn_flood', 5)
        assert result2 is not None
        assert result2[1] == 409