"""runtime/tasks.py — 任务注册表。

统一管理 8 组编排任务（capture / capture_full / detection / ml / attack / auto / demo / train），
取代 helpers.py 的 4 处线程创建点 + attack_sim_state.py 的 2 处。

核心抽象：
- Task(kind=continuous|timed) — 连续运行 / 限时运行
- TaskRegistry — 注册/启动/停止/状态/超时看门狗

本模块 import 不产生任何 I/O 副作用。
"""
from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class TaskKind(str, Enum):
    """任务类型。"""
    CONTINUOUS = "continuous"  # 持续运行，直到显式停止（如抓包、检测节拍）
    TIMED = "timed"            # 限时运行，超时自动停止（如增强抓包、攻击模拟、一键流程）


class TaskStatus(str, Enum):
    """任务状态。"""
    IDLE = "idle"          # 未启动
    RUNNING = "running"    # 运行中
    STOPPING = "stopping"  # 正在停止
    FINISHED = "finished"  # 正常结束（限时任务到期）
    FAILED = "failed"      # 异常退出


class Task:
    """任务描述符（注册时声明，运行时由 TaskRegistry 管理）。"""

    def __init__(
        self,
        name: str,
        kind: TaskKind,
        target: Callable[..., None] | None = None,
        default_duration: int | None = None,
        description: str = "",
    ) -> None:
        self.name = name
        self.kind = kind
        self.target = target  # 工作函数（阶段1为空壳，阶段2接入 services/）
        self.default_duration = default_duration  # 限时任务默认时长(秒)
        self.description = description


class TaskHandle:
    """运行中的任务句柄（内部使用）。"""

    def __init__(self, task: Task, thread: threading.Thread, started_at: float,
                 actual_duration: int | None = None) -> None:
        self.task = task
        self.thread = thread
        self.started_at = started_at
        self.status: TaskStatus = TaskStatus.RUNNING
        self.error: str | None = None
        self.actual_duration = actual_duration  # 启动时实际使用的超时时长(秒)
        self._stop_event = threading.Event()

    @property
    def elapsed(self) -> float:
        """已运行时长(秒)。"""
        return time.monotonic() - self.started_at

    def request_stop(self) -> None:
        """请求停止（非阻塞）。"""
        self._stop_event.set()
        self.status = TaskStatus.STOPPING

    @property
    def stop_requested(self) -> bool:
        """是否已请求停止。"""
        return self._stop_event.is_set()


class TaskRegistry:
    """任务注册表 — 统一管理所有后台任务的生命周期。

    取代 8 组各自实现的 start/stop/status 端点（21 个端点 → 3 个）。
    线程安全：所有写操作由内部锁保护。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}         # 注册的任务描述符
        self._handles: dict[str, TaskHandle] = {}  # 运行中的句柄
        self._watchdog: threading.Thread | None = None

    # ── 注册 ──────────────────────────────────────────────────────

    def register(self, task: Task) -> None:
        """注册任务描述符。"""
        with self._lock:
            if task.name in self._tasks:
                raise ValueError(f"任务已注册: {task.name}")
            self._tasks[task.name] = task

    def register_many(self, tasks: list[Task]) -> None:
        """批量注册。"""
        for task in tasks:
            self.register(task)

    # ── 启动 ──────────────────────────────────────────────────────

    def start(self, name: str, duration: int | None = None, **kwargs: Any) -> dict:
        """启动任务。

        Args:
            name: 任务名称
            duration: 限时任务时长(秒)，None 使用默认值
            **kwargs: 传递给任务工作函数的参数

        Returns:
            {"status": "started"} 或 {"status": "already_running"} 或 {"status": "error", "message": ...}

        Raises:
            KeyError: 任务未注册
        """
        with self._lock:
            if name not in self._tasks:
                raise KeyError(f"任务未注册: {name}")
            if name in self._handles and self._handles[name].status == TaskStatus.RUNNING:
                return {"status": "already_running"}
            task = self._tasks[name]
            if task.target is None:
                return {"status": "error", "message": f"任务 {name} 尚未接入工作函数（阶段1空壳）"}

        # 在锁外启动线程
        stop_event = threading.Event()
        actual_duration = duration or task.default_duration

        def _worker() -> None:
            handle = self._handles.get(name)
            try:
                if task.kind == TaskKind.TIMED and actual_duration:
                    # 限时任务：工作函数需检查 stop_requested
                    task.target(stop_event=stop_event, duration=actual_duration, **kwargs)
                else:
                    task.target(stop_event=stop_event, **kwargs)
            except Exception as exc:
                logger.exception("任务 %s 异常退出", name)
                if handle:
                    handle.status = TaskStatus.FAILED
                    handle.error = str(exc)
                return
            # 正常结束
            handle = self._handles.get(name)
            if handle and handle.status != TaskStatus.STOPPING:
                handle.status = TaskStatus.FINISHED

        thread = threading.Thread(target=_worker, daemon=True, name=f"task-{name}")
        handle = TaskHandle(task, thread, started_at=time.monotonic(),
                            actual_duration=actual_duration if task.kind == TaskKind.TIMED else None)
        handle._stop_event = stop_event

        with self._lock:
            self._handles[name] = handle
        thread.start()

        # 限时任务启动后确保看门狗运行
        if task.kind == TaskKind.TIMED:
            self._start_watchdog()

        logger.info("任务 %s 已启动", name)
        return {"status": "started"}

    # ── 停止 ──────────────────────────────────────────────────────

    def stop(self, name: str) -> dict:
        """请求停止任务（非阻塞，幂等）。"""
        with self._lock:
            handle = self._handles.get(name)
            if handle is None or handle.status != TaskStatus.RUNNING:
                return {"status": "not_running"}
            handle.request_stop()
        logger.info("任务 %s 停止请求已发送", name)
        return {"status": "stopping"}

    # ── 状态查询 ──────────────────────────────────────────────────

    def status(self, name: str) -> dict:
        """查询单个任务状态。"""
        with self._lock:
            task = self._tasks.get(name)
            if task is None:
                raise KeyError(f"任务未注册: {name}")
            handle = self._handles.get(name)
            if handle is None:
                return {"name": name, "status": TaskStatus.IDLE.value, "kind": task.kind.value}
            return {
                "name": name,
                "status": handle.status.value,
                "kind": task.kind.value,
                "elapsed": round(handle.elapsed, 1),
                "error": handle.error,
            }

    def status_all(self) -> list[dict]:
        """查询所有已注册任务的状态。"""
        with self._lock:
            result = []
            for name, task in self._tasks.items():
                handle = self._handles.get(name)
                if handle is None:
                    result.append({"name": name, "status": TaskStatus.IDLE.value, "kind": task.kind.value})
                else:
                    result.append({
                        "name": name,
                        "status": handle.status.value,
                        "kind": task.kind.value,
                        "elapsed": round(handle.elapsed, 1),
                        "error": handle.error,
                    })
            return result

    # ── 超时看门狗 ──────────────────────────────────────────────

    def _start_watchdog(self) -> None:
        """启动超时看门狗线程（限时任务到期自动停止）。"""
        if self._watchdog and self._watchdog.is_alive():
            return
        stop_event = threading.Event()

        def _watchdog_loop() -> None:
            while not stop_event.wait(timeout=1.0):
                with self._lock:
                    for name, handle in self._handles.items():
                        if handle.status != TaskStatus.RUNNING:
                            continue
                        if handle.task.kind == TaskKind.TIMED and handle.actual_duration:
                            if handle.elapsed >= handle.actual_duration:
                                handle.request_stop()
                                logger.info("任务 %s 超时自动停止（时长 %ds）", name, handle.actual_duration)

        self._watchdog = threading.Thread(target=_watchdog_loop, daemon=True, name="task-watchdog")
        self._watchdog.start()

    @property
    def registered_names(self) -> list[str]:
        """已注册的任务名称列表。"""
        with self._lock:
            return list(self._tasks.keys())