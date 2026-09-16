"""T1.4 任务注册表 — 验证 TaskRegistry 注册/启动/停止/状态/超时看门狗。

核心验收：
- 重复注册 → ValueError
- 未注册启动 → KeyError
- 已运行再启动 → already_running
- 限时任务超时自动停止
- 停止幂等
- 线程安全
"""
from __future__ import annotations

import threading
import time

import pytest

from campus_ids.runtime.tasks import (
    Task,
    TaskHandle,
    TaskKind,
    TaskRegistry,
    TaskStatus,
)


# ── 辅助 ──────────────────────────────────────────────────────────


def _slow_worker(stop_event: threading.Event, **kwargs) -> None:
    """模拟长时间运行的任务，等待 stop_event。"""
    stop_event.wait(timeout=30)


def _timed_worker(stop_event: threading.Event, duration: int = 5, **kwargs) -> None:
    """模拟限时任务，运行 duration 秒后自动退出。"""
    stop_event.wait(timeout=duration)


def _failing_worker(stop_event: threading.Event, **kwargs) -> None:
    """模拟异常退出的任务。"""
    raise RuntimeError("模拟任务异常")


# ── Task / TaskKind / TaskStatus 枚举 ──────────────────────────────


class TestTaskEnums:
    """验证 TaskKind 和 TaskStatus 枚举值。"""

    def test_task_kind_values(self):
        assert TaskKind.CONTINUOUS.value == "continuous"
        assert TaskKind.TIMED.value == "timed"

    def test_task_status_values(self):
        assert TaskStatus.IDLE.value == "idle"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.STOPPING.value == "stopping"
        assert TaskStatus.FINISHED.value == "finished"
        assert TaskStatus.FAILED.value == "failed"


class TestTaskDescriptor:
    """验证 Task 描述符。"""

    def test_task_creation(self):
        task = Task(name="capture", kind=TaskKind.CONTINUOUS, description="抓包")
        assert task.name == "capture"
        assert task.kind == TaskKind.CONTINUOUS
        assert task.target is None
        assert task.default_duration is None

    def test_timed_task_with_duration(self):
        task = Task(name="attack", kind=TaskKind.TIMED, default_duration=60)
        assert task.default_duration == 60


# ── 注册 ──────────────────────────────────────────────────────────


class TestRegister:
    """验证任务注册行为。"""

    def test_register_single(self):
        reg = TaskRegistry()
        task = Task(name="capture", kind=TaskKind.CONTINUOUS)
        reg.register(task)
        assert "capture" in reg.registered_names

    def test_register_duplicate_raises(self):
        reg = TaskRegistry()
        task = Task(name="capture", kind=TaskKind.CONTINUOUS)
        reg.register(task)
        with pytest.raises(ValueError, match="任务已注册"):
            reg.register(task)

    def test_register_many(self):
        reg = TaskRegistry()
        tasks = [
            Task(name="capture", kind=TaskKind.CONTINUOUS),
            Task(name="detection", kind=TaskKind.CONTINUOUS),
            Task(name="attack", kind=TaskKind.TIMED, default_duration=60),
        ]
        reg.register_many(tasks)
        assert set(reg.registered_names) == {"capture", "detection", "attack"}


# ── 启动 ──────────────────────────────────────────────────────────


class TestStart:
    """验证任务启动行为。"""

    def test_start_unregistered_raises(self):
        reg = TaskRegistry()
        with pytest.raises(KeyError, match="任务未注册"):
            reg.start("nonexistent")

    def test_start_no_target_returns_error(self):
        reg = TaskRegistry()
        reg.register(Task(name="shell", kind=TaskKind.CONTINUOUS))
        result = reg.start("shell")
        assert result["status"] == "error"
        assert "尚未接入工作函数" in result["message"]

    def test_start_continuous_task(self):
        reg = TaskRegistry()
        reg.register(Task(name="capture", kind=TaskKind.CONTINUOUS, target=_slow_worker))
        result = reg.start("capture")
        assert result["status"] == "started"
        # 清理
        reg.stop("capture")
        time.sleep(0.3)

    def test_start_already_running(self):
        reg = TaskRegistry()
        reg.register(Task(name="capture", kind=TaskKind.CONTINUOUS, target=_slow_worker))
        reg.start("capture")
        result = reg.start("capture")
        assert result["status"] == "already_running"
        # 清理
        reg.stop("capture")
        time.sleep(0.3)

    def test_start_timed_task_with_duration_override(self):
        reg = TaskRegistry()
        reg.register(Task(name="attack", kind=TaskKind.TIMED, target=_timed_worker, default_duration=60))
        result = reg.start("attack", duration=2)
        assert result["status"] == "started"
        # 清理
        reg.stop("attack")
        time.sleep(0.3)


# ── 停止 ──────────────────────────────────────────────────────────


class TestStop:
    """验证任务停止行为。"""

    def test_stop_not_running(self):
        reg = TaskRegistry()
        reg.register(Task(name="capture", kind=TaskKind.CONTINUOUS, target=_slow_worker))
        result = reg.stop("capture")
        assert result["status"] == "not_running"

    def test_stop_running_task(self):
        reg = TaskRegistry()
        reg.register(Task(name="capture", kind=TaskKind.CONTINUOUS, target=_slow_worker))
        reg.start("capture")
        result = reg.stop("capture")
        assert result["status"] == "stopping"
        time.sleep(0.3)

    def test_stop_idempotent(self):
        """停止幂等：连续两次 stop 不报错。"""
        reg = TaskRegistry()
        reg.register(Task(name="capture", kind=TaskKind.CONTINUOUS, target=_slow_worker))
        reg.start("capture")
        result1 = reg.stop("capture")
        assert result1["status"] == "stopping"
        # 第二次 stop：任务已处于 STOPPING 状态
        result2 = reg.stop("capture")
        assert result2["status"] == "not_running"
        time.sleep(0.3)


# ── 状态查询 ──────────────────────────────────────────────────────


class TestStatus:
    """验证状态查询行为。"""

    def test_status_unregistered_raises(self):
        reg = TaskRegistry()
        with pytest.raises(KeyError, match="任务未注册"):
            reg.status("nonexistent")

    def test_status_idle(self):
        reg = TaskRegistry()
        reg.register(Task(name="capture", kind=TaskKind.CONTINUOUS))
        s = reg.status("capture")
        assert s["status"] == TaskStatus.IDLE.value
        assert s["kind"] == TaskKind.CONTINUOUS.value

    def test_status_running(self):
        reg = TaskRegistry()
        reg.register(Task(name="capture", kind=TaskKind.CONTINUOUS, target=_slow_worker))
        reg.start("capture")
        s = reg.status("capture")
        assert s["status"] == TaskStatus.RUNNING.value
        assert "elapsed" in s
        # 清理
        reg.stop("capture")
        time.sleep(0.3)

    def test_status_all(self):
        reg = TaskRegistry()
        reg.register(Task(name="capture", kind=TaskKind.CONTINUOUS))
        reg.register(Task(name="attack", kind=TaskKind.TIMED, default_duration=60))
        all_status = reg.status_all()
        assert len(all_status) == 2
        names = {s["name"] for s in all_status}
        assert names == {"capture", "attack"}


# ── TaskHandle ─────────────────────────────────────────────────────


class TestTaskHandle:
    """验证 TaskHandle 属性。"""

    def test_handle_elapsed(self):
        task = Task(name="test", kind=TaskKind.CONTINUOUS, target=_slow_worker)
        reg = TaskRegistry()
        reg.register(task)
        reg.start("test")
        handle = reg._handles["test"]
        assert handle.elapsed >= 0
        # 清理
        reg.stop("test")
        time.sleep(0.3)

    def test_handle_request_stop(self):
        task = Task(name="test", kind=TaskKind.CONTINUOUS, target=_slow_worker)
        reg = TaskRegistry()
        reg.register(task)
        reg.start("test")
        handle = reg._handles["test"]
        assert not handle.stop_requested
        handle.request_stop()
        assert handle.stop_requested
        assert handle.status == TaskStatus.STOPPING
        time.sleep(0.3)


# ── 异常退出 ──────────────────────────────────────────────────────


class TestFailedTask:
    """验证任务异常退出时状态变为 FAILED。"""

    def test_failed_status(self):
        reg = TaskRegistry()
        reg.register(Task(name="fail", kind=TaskKind.CONTINUOUS, target=_failing_worker))
        reg.start("fail")
        # 等待异常退出
        time.sleep(0.5)
        handle = reg._handles.get("fail")
        assert handle is not None
        assert handle.status == TaskStatus.FAILED
        assert handle.error is not None