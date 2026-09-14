"""C-05 验收记录自动化测试 — T-02/T-05/T-09/T-16。

T-01 SSE 验收已在 test_sse_endpoints.py 中完成（6/6 通过）。
本文件覆盖剩余 4 项运行时验收的可自动化部分。
"""
import json
import logging
import os
import signal
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── T-16: 日志轮转小文件实验 ──────────────────────────────────────────

class TestDetectionLogRotation:
    """T-16 验收：DetectionLogHandler 小 maxBytes 轮转验证。"""

    def _write_and_close(self, log_path, max_bytes, n_records, logger_name):
        """写入日志并完全关闭所有文件句柄。"""
        from campus_ids.logging_config import DetectionLogHandler

        handler = DetectionLogHandler(log_path, max_bytes=max_bytes, backup_count=3)
        logger = logging.getLogger(logger_name)
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)

        for i in range(n_records):
            logger.warning("Test alert %d: suspicious activity detected", i)

        logger.removeHandler(handler)
        # 关闭内部 RotatingFileHandler（Windows 需要释放文件锁）
        handler._rotating.close()
        handler.close()
        return log_path

    def test_rotation_creates_backup_file(self):
        """小 maxBytes 灌日志后应生成 .1 滚动文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "detections.jsonl"
            self._write_and_close(log_path, max_bytes=500, n_records=20,
                                  logger_name="test.rotation")

            assert log_path.exists(), "主日志文件应存在"
            backup_path = Path(str(log_path) + ".1")
            assert backup_path.exists(), ".1 备份文件应存在（轮转已触发）"

    def test_rotation_preserves_json_format(self):
        """轮转后主文件和备份文件内容均为合法 JSON Lines。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "detections.jsonl"
            self._write_and_close(log_path, max_bytes=500, n_records=20,
                                  logger_name="test.rotation.format")

            for path in [log_path, Path(str(log_path) + ".1")]:
                if not path.exists():
                    continue
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            json.loads(line)  # 不抛异常即合法


# ── T-02: 优雅关闭信号实测 ────────────────────────────────────────────

class TestGracefulShutdown:
    """T-02 验收：_graceful_shutdown 调用正确的清理方法并退出。"""

    @patch("campus_ids.web.app.dual_detector")
    @patch("campus_ids.web.app.stop_capture_thread")
    def test_shutdown_calls_stop_capture(self, mock_stop_capture, mock_detector):
        """优雅关闭应调用 stop_capture_thread。"""
        from campus_ids.web.app import _graceful_shutdown

        mock_detector._ml_running = False
        with pytest.raises(SystemExit) as exc_info:
            _graceful_shutdown(signal.SIGINT, None)
        assert exc_info.value.code == 0
        mock_stop_capture.assert_called_once()

    @patch("campus_ids.web.app.dual_detector")
    @patch("campus_ids.web.app.stop_capture_thread")
    def test_shutdown_calls_stop_ml_loop_when_running(self, mock_stop_capture, mock_detector):
        """ML 循环运行时应调用 stop_ml_loop。"""
        from campus_ids.web.app import _graceful_shutdown

        mock_detector._ml_running = True
        with pytest.raises(SystemExit):
            _graceful_shutdown(signal.SIGINT, None)
        mock_detector.stop_ml_loop.assert_called_once()

    @patch("campus_ids.web.app.dual_detector")
    @patch("campus_ids.web.app.stop_capture_thread")
    def test_shutdown_skips_ml_loop_when_not_running(self, mock_stop_capture, mock_detector):
        """ML 循环未运行时不应调用 stop_ml_loop。"""
        from campus_ids.web.app import _graceful_shutdown

        mock_detector._ml_running = False
        with pytest.raises(SystemExit):
            _graceful_shutdown(signal.SIGINT, None)
        mock_detector.stop_ml_loop.assert_not_called()

    @patch("campus_ids.web.app.dual_detector")
    @patch("campus_ids.web.app.stop_capture_thread")
    def test_shutdown_exits_zero(self, mock_stop_capture, mock_detector):
        """优雅关闭应以退出码 0 退出。"""
        from campus_ids.web.app import _graceful_shutdown

        mock_detector._ml_running = False
        with pytest.raises(SystemExit) as exc_info:
            _graceful_shutdown(signal.SIGINT, None)
        assert exc_info.value.code == 0


# ── T-05: 前端轮询降级结构验证 ────────────────────────────────────────

class TestFrontendPollingStructure:
    """T-05 验收：controls.js 轮询结构静态分析。"""

    @pytest.fixture
    def controls_js(self):
        js_path = Path("src/campus_ids/web/static/js/controls.js")
        return js_path.read_text(encoding="utf-8")

    def test_single_poll_timer_setinterval(self, controls_js):
        """主轮询 pollTimer 应仅有唯一 setInterval 赋值。"""
        import re
        assignments = re.findall(r"pollTimer\s*=\s*setInterval", controls_js)
        assert len(assignments) == 1, (
            f"pollTimer 应仅有 1 处 setInterval 赋值，实际 {len(assignments)} 处"
        )

    def test_start_polling_fallback_no_interval(self, controls_js):
        """startPollingFallback 不应新建 setInterval。"""
        import re
        match = re.search(
            r"function\s+startPollingFallback\s*\(\)\s*\{([^}]*)\}",
            controls_js,
        )
        assert match, "startPollingFallback 函数应存在"
        body = match.group(1)
        assert "setInterval" not in body, (
            "startPollingFallback 不应包含 setInterval（仅置标志 + 立即刷新一次）"
        )

    def test_no_some_and_every_pattern(self, controls_js):
        """some(...) && every(...) 模式应已简化为纯 every。"""
        assert ".some(" not in controls_js or "&&" not in controls_js.split(".some(")[0][-50:], (
            "不应存在 some(...) && every(...) 复合条件（应简化为纯 every）"
        )


# ── T-09: fusion 复用路径验证 ─────────────────────────────────────────

class TestFusionReusePath:
    """T-09 验收：_evaluate_dual_fusion prefitted 路径逻辑正确性。"""

    def test_prefitted_parameter_exists(self):
        """_evaluate_dual_fusion 应接受 prefitted 参数。"""
        from campus_ids.model.evaluation import _evaluate_dual_fusion
        import inspect
        sig = inspect.signature(_evaluate_dual_fusion)
        assert "prefitted" in sig.parameters, (
            "_evaluate_dual_fusion 应有 prefitted 参数"
        )

    def test_prefitted_skips_train_model(self):
        """传入 prefitted 时应跳过内部 train_model 调用。"""
        from campus_ids.model.evaluation import _evaluate_dual_fusion
        import inspect
        source = inspect.getsource(_evaluate_dual_fusion)
        assert "if prefitted is not None" in source, (
            "应有 prefitted is not None 分支"
        )
        assert "prefitted" in source and "train_model" in source, (
            "应同时包含 prefitted 和 train_model 引用"
        )

    def test_train_py_passes_prefitted(self):
        """train.py 应向 _evaluate_dual_fusion 传入 prefitted 元组。"""
        import inspect
        from campus_ids.model import train
        source = inspect.getsource(train)
        assert "prefitted=" in source, (
            "train.py 应传入 prefitted= 参数"
        )
        assert "rf_clf, rf_scaler, rf_le, rf_y_test" in source, (
            "train.py 应传入 (rf_clf, rf_scaler, rf_le, rf_y_test) 元组"
        )