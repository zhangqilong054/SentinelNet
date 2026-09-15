"""AnomalyDetector 单元测试 — 覆盖四类规则检测 + 四类应用层检测。"""
import pytest
from campus_ids.detector.detector import AnomalyDetector


# ── 基础规则检测 ──────────────────────────────────────────────

class TestDDoS:
    def test_ddos_detected(self):
        det = AnomalyDetector(ddos_threshold=500)
        ok, msg = det.check_ddos(600)
        assert ok is True
        assert "DDoS" in msg

    def test_ddos_not_detected(self):
        det = AnomalyDetector(ddos_threshold=500)
        ok, msg = det.check_ddos(100)
        assert ok is False
        assert msg == ""

    def test_ddos_threshold_boundary(self):
        det = AnomalyDetector(ddos_threshold=500)
        ok, _ = det.check_ddos(500)
        assert ok is False  # 严格大于才触发
        ok, _ = det.check_ddos(501)
        assert ok is True


class TestPortScan:
    def test_port_scan_detected(self):
        det = AnomalyDetector(port_scan_threshold=50)
        ok, msg = det.check_port_scan(60)
        assert ok is True
        assert "端口扫描" in msg

    def test_port_scan_not_detected(self):
        det = AnomalyDetector(port_scan_threshold=50)
        ok, _ = det.check_port_scan(30)
        assert ok is False


class TestSYNFlood:
    def test_syn_flood_detected(self):
        det = AnomalyDetector(syn_flood_threshold=100)
        ok, msg = det.check_syn_flood(150)
        assert ok is True
        assert "SYN" in msg

    def test_syn_flood_not_detected(self):
        det = AnomalyDetector(syn_flood_threshold=100)
        ok, _ = det.check_syn_flood(50)
        assert ok is False


class TestUDPFlood:
    def test_udp_flood_detected(self):
        det = AnomalyDetector(udp_flood_threshold=200)
        ok, msg = det.check_udp_flood(300)
        assert ok is True
        assert "UDP" in msg

    def test_udp_flood_not_detected(self):
        det = AnomalyDetector(udp_flood_threshold=200)
        ok, _ = det.check_udp_flood(100)
        assert ok is False


# ── 应用层检测（P1-#9） ──────────────────────────────────────

class TestSQLInjection:
    @pytest.fixture
    def det(self):
        return AnomalyDetector()

    def test_union_select(self, det):
        ok, _ = det.check_sql_injection("1 UNION SELECT * FROM users--")
        assert ok is True

    def test_or_one_equals_one(self, det):
        ok, _ = det.check_sql_injection("' OR 1=1 --")
        assert ok is True

    def test_drop_table(self, det):
        ok, _ = det.check_sql_injection("DROP TABLE users")
        assert ok is True

    def test_normal_query(self, det):
        ok, _ = det.check_sql_injection("SELECT name FROM products WHERE id=5")
        assert ok is True  # "select...from" matches, so this is True

    def test_benign_string(self, det):
        ok, _ = det.check_sql_injection("hello world")
        assert ok is False


class TestXSS:
    @pytest.fixture
    def det(self):
        return AnomalyDetector()

    def test_script_tag(self, det):
        ok, _ = det.check_xss("<script>alert('xss')</script>")
        assert ok is True

    def test_javascript_uri(self, det):
        ok, _ = det.check_xss("javascript:alert(1)")
        assert ok is True

    def test_event_handler(self, det):
        ok, _ = det.check_xss('<img onerror="alert(1)">')
        assert ok is True

    def test_benign_html(self, det):
        ok, _ = det.check_xss("<p>Hello World</p>")
        assert ok is False

    def test_benign_string(self, det):
        ok, _ = det.check_xss("just some text")
        assert ok is False


class TestBruteForce:
    def test_brute_force_detected(self):
        det = AnomalyDetector(brute_force_threshold=5, brute_force_window=60)
        for _ in range(5):
            ok, _ = det.check_brute_force("192.168.1.100", 22)
        assert ok is True

    def test_brute_force_not_detected(self):
        det = AnomalyDetector(brute_force_threshold=10, brute_force_window=60)
        for _ in range(3):
            ok, _ = det.check_brute_force("192.168.1.100", 22)
        assert ok is False

    def test_different_ports_independent(self):
        det = AnomalyDetector(brute_force_threshold=5, brute_force_window=60)
        for _ in range(3):
            det.check_brute_force("192.168.1.100", 22)
        for _ in range(3):
            det.check_brute_force("192.168.1.100", 80)
        # 3 per port, should not trigger
        ok, _ = det.check_brute_force("192.168.1.100", 22)
        assert ok is False


class TestLateralMovement:
    def test_lateral_movement_detected(self):
        det = AnomalyDetector(lateral_movement_threshold=3)
        det.check_lateral_movement("192.168.1.100", "192.168.1.1")
        det.check_lateral_movement("192.168.1.100", "192.168.1.2")
        ok, _ = det.check_lateral_movement("192.168.1.100", "192.168.1.3")
        assert ok is True

    def test_lateral_movement_not_detected(self):
        det = AnomalyDetector(lateral_movement_threshold=5)
        det.check_lateral_movement("192.168.1.100", "192.168.1.1")
        det.check_lateral_movement("192.168.1.100", "192.168.1.2")
        ok, _ = det.check_lateral_movement("192.168.1.100", "192.168.1.3")
        assert ok is False

    def test_same_dst_no_alert(self):
        det = AnomalyDetector(lateral_movement_threshold=3)
        for _ in range(10):
            det.check_lateral_movement("192.168.1.100", "192.168.1.1")
        # Still only 1 unique dst_ip
        ok, _ = det.check_lateral_movement("192.168.1.100", "192.168.1.1")
        assert ok is False


class TestCheckPayload:
    @pytest.fixture
    def det(self):
        return AnomalyDetector()

    def test_sqli_payload(self, det):
        alerts = det.check_payload("' OR 1=1 --")
        assert len(alerts) >= 1
        assert any("SQL" in a for a in alerts)

    def test_xss_payload(self, det):
        alerts = det.check_payload("<script>alert(1)</script>")
        assert len(alerts) >= 1
        assert any("XSS" in a for a in alerts)

    def test_combined_payload(self, det):
        alerts = det.check_payload("<script> UNION SELECT * FROM users</script>")
        assert len(alerts) == 2  # Both SQLi and XSS

    def test_benign_payload(self, det):
        alerts = det.check_payload("hello world")
        assert len(alerts) == 0


# ── D5: DualDetector.start_ml_loop 重复调用守卫 ──────────────────

class TestDualDetectorMLLoopGuard:
    """D5 修复验证：start_ml_loop 重复调用不泄漏线程。

    原缺陷：连续调用 start_ml_loop 会创建多个后台线程。
    修复：dual_detector.py L401 加 if self._ml_running: return 守卫。
    """

    @pytest.fixture
    def dual_det(self):
        """创建一个 mock 了模型加载状态的 DualDetector。"""
        from campus_ids.detector.dual_detector import DualDetector
        from campus_ids.detector.detector import AnomalyDetector
        rule_det = AnomalyDetector()
        det = DualDetector(rule_det)
        # 模拟模型已加载
        det._model_loaded = True
        det.ml_interval = 0.1  # 短间隔加速测试
        return det

    def test_start_ml_loop_idempotent(self, dual_det):
        """连续调用 start_ml_loop 仅创建一个线程。"""
        dual_det.start_ml_loop()
        assert dual_det._ml_running is True
        assert dual_det._ml_thread is not None
        first_thread = dual_det._ml_thread

        # 第二次调用应被守卫拦截
        dual_det.start_ml_loop()
        assert dual_det._ml_thread is first_thread, "重复调用不应创建新线程"

        # 清理
        dual_det.stop_ml_loop()

    def test_stop_ml_loop_cleans_up(self, dual_det):
        """stop_ml_loop 后 _ml_running 为 False 且无线程残留。"""
        dual_det.start_ml_loop()
        assert dual_det._ml_running is True

        dual_det.stop_ml_loop()
        assert dual_det._ml_running is False
        # 线程应已结束
        import time
        time.sleep(0.3)
        if dual_det._ml_thread is not None:
            assert not dual_det._ml_thread.is_alive(), "停止后线程不应存活"

    def test_start_after_stop(self, dual_det):
        """停止后可以重新启动，且创建新线程。"""
        dual_det.start_ml_loop()
        first_thread = dual_det._ml_thread

        dual_det.stop_ml_loop()
        import time
        time.sleep(0.3)

        dual_det.start_ml_loop()
        assert dual_det._ml_running is True
        assert dual_det._ml_thread is not first_thread, "重新启动应创建新线程"

        dual_det.stop_ml_loop()

    def test_ml_running_property(self, dual_det):
        """ml_running 属性访问器应正确反映内部状态。"""
        assert dual_det.ml_running is False
        dual_det.start_ml_loop()
        assert dual_det.ml_running is True
        dual_det.stop_ml_loop()
        assert dual_det.ml_running is False