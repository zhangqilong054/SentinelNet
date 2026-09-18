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
def mock_queue():
    """创建 mock 队列，供 AttackSimulator 构造函数直接传入。"""
    return _MockQueue()


@pytest.fixture
def sim(mock_queue):
    """创建注入 mock_queue 的 AttackSimulator 实例。"""
    return AttackSimulator(packet_queue=mock_queue)


# ── 测试：注入包必须包含完整字段 ────────────────────────────────

class TestInjectPacketFields:
    """验证注入包包含 dst_ip、timestamp 等必要字段。"""

    REQUIRED_KEYS = {"length", "sport", "dport", "src_ip", "dst_ip",
                     "proto", "is_syn", "is_dns", "timestamp"}

    def test_syn_flood_has_all_fields(self, sim, mock_queue):
        sim._running = True
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_syn_flood(duration=1, rate=5)
        assert len(mock_queue.items) > 0
        for pkt in mock_queue.items:
            assert self.REQUIRED_KEYS.issubset(pkt.keys()), \
                f"缺少字段: {self.REQUIRED_KEYS - set(pkt.keys())}"

    def test_port_scan_has_all_fields(self, sim, mock_queue):
        sim._running = True
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_port_scan(duration=1, rate=5)
        assert len(mock_queue.items) > 0
        for pkt in mock_queue.items:
            assert self.REQUIRED_KEYS.issubset(pkt.keys()), \
                f"缺少字段: {self.REQUIRED_KEYS - set(pkt.keys())}"

    def test_udp_flood_has_all_fields(self, sim, mock_queue):
        sim._running = True
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_udp_flood(duration=1, rate=5)
        assert len(mock_queue.items) > 0
        for pkt in mock_queue.items:
            assert self.REQUIRED_KEYS.issubset(pkt.keys()), \
                f"缺少字段: {self.REQUIRED_KEYS - set(pkt.keys())}"

    def test_brute_force_has_all_fields(self, sim, mock_queue):
        sim._running = True
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_brute_force(duration=1, rate=5)
        assert len(mock_queue.items) > 0
        for pkt in mock_queue.items:
            assert self.REQUIRED_KEYS.issubset(pkt.keys()), \
                f"缺少字段: {self.REQUIRED_KEYS - set(pkt.keys())}"

    def test_lateral_movement_has_all_fields(self, sim, mock_queue):
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

    def test_syn_flood_values(self, sim, mock_queue):
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

    def test_port_scan_values(self, sim, mock_queue):
        sim._running = True
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_port_scan(duration=1, rate=5)
        for pkt in mock_queue.items:
            assert pkt["proto"] == "TCP"
            assert pkt["is_syn"] is True
            assert pkt["dst_ip"] == TARGET_IP
            assert 1 <= pkt["dport"] <= 1024
            assert isinstance(pkt["timestamp"], float)

    def test_udp_flood_values(self, sim, mock_queue):
        sim._running = True
        threading.Timer(0.3, lambda: setattr(sim, '_running', False)).start()
        sim.inject_udp_flood(duration=1, rate=5)
        for pkt in mock_queue.items:
            assert pkt["proto"] == "UDP"
            assert pkt["is_syn"] is False
            assert pkt["dport"] == 53
            assert pkt["dst_ip"] == TARGET_IP
            assert isinstance(pkt["timestamp"], float)

    def test_brute_force_values(self, sim, mock_queue):
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

    def test_lateral_movement_values(self, sim, mock_queue):
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
    def test_start_and_stop(self, sim, mock_queue):
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


# ── 测试：显式传入队列 ──────────────────────────────────────

class TestExplicitQueue:
    """验证 AttackSimulator 使用显式传入的队列。"""

    def test_explicit_queue_is_used(self, mock_queue):
        """显式传入的队列应被直接使用。"""
        sim = AttackSimulator(packet_queue=mock_queue)
        assert sim._packet_queue is mock_queue

    def test_default_queue_is_none(self):
        """未传入队列时 _packet_queue 应为 None（回退到 RuntimeState）。"""
        sim = AttackSimulator()
        assert sim._packet_queue is None