"""T2.1-T2.3 任务 API 集成测试 — 验证 GET /api/tasks, POST start/stop。

测试覆盖：
- GET /api/tasks：返回所有已注册任务状态
- POST /api/tasks/{name}/start：启动成功、已在运行(409)、任务不存在(404)、无工作函数(503)
- POST /api/tasks/{name}/stop：停止成功、未运行(幂等200)、任务不存在(404)
- duration 参数传递
- description/default_duration 字段
"""
from __future__ import annotations

import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from campus_ids.web.app import create_app
from campus_ids.runtime.settings import reset_settings
from campus_ids.runtime.tasks import Task, TaskKind, TaskRegistry


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_env():
    """每个测试前后清理环境变量和设置单例。"""
    os.environ.pop("CAMPUS_IDS_AUTH_ENABLED", None)
    os.environ.pop("CAMPUS_IDS_API_TOKEN", None)
    reset_settings()
    from campus_ids.web.security import limiter
    limiter.reset()
    yield
    os.environ.pop("CAMPUS_IDS_AUTH_ENABLED", None)
    os.environ.pop("CAMPUS_IDS_API_TOKEN", None)
    reset_settings()
    limiter.reset()


@pytest.fixture()
def app():
    """创建测试应用实例。"""
    return create_app()


@pytest.fixture()
def client(app):
    """认证关闭的 TestClient（触发 lifespan 以初始化 task_registry）。"""
    with TestClient(app) as c:
        yield c


def _get_csrf_token(client: TestClient) -> str:
    """获取 CSRF token 并设置 cookie。"""
    resp = client.get("/api/csrf-token")
    assert resp.status_code == 200
    csrf_token = resp.json()["csrf_token"]
    client.cookies.set("csrf_token", csrf_token)
    return csrf_token


def _post_with_csrf(client: TestClient, url: str, json: dict | None = None) -> object:
    """带 CSRF 的 POST 请求。"""
    csrf_token = _get_csrf_token(client)
    return client.post(url, json=json or {}, headers={"X-CSRFToken": csrf_token})


# ── 辅助：注册带真实 target 的测试任务 ──────────────────────────────


def _register_test_task(app, name: str = "_test_task", kind: TaskKind = TaskKind.CONTINUOUS,
                        duration: int | None = None) -> threading.Event:
    """在应用的 TaskRegistry 中注册一个带真实 target 的测试任务。

    返回 stop_event，测试可用来控制任务生命周期。
    """
    stop_event = threading.Event()

    def _worker(stop_event: threading.Event = None, **kwargs):
        while stop_event and not stop_event.is_set():
            time.sleep(0.05)

    task = Task(name=name, kind=kind, target=_worker,
                default_duration=duration, description="测试任务")
    registry: TaskRegistry = app.state.task_registry
    # 先尝试注销已有同名任务（测试重复注册）
    if name in registry._tasks:
        del registry._tasks[name]
    registry.register(task)
    return stop_event


def _register_timed_test_task(app, name: str = "_test_timed",
                              default_duration: int = 2) -> None:
    """注册一个限时测试任务。"""

    def _worker(stop_event: threading.Event = None, duration: int = 2, **kwargs):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and stop_event and not stop_event.is_set():
            time.sleep(0.05)

    task = Task(name=name, kind=TaskKind.TIMED, target=_worker,
                default_duration=default_duration, description="限时测试任务")
    registry: TaskRegistry = app.state.task_registry
    if name in registry._tasks:
        del registry._tasks[name]
    registry.register(task)


# ── T2.1: GET /api/tasks ──────────────────────────────────────────


class TestListTasks:
    """GET /api/tasks — 获取所有任务状态。"""

    def test_returns_list_of_registered_tasks(self, client, app):
        """返回所有已注册任务，包含默认 8 个任务。"""
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        tasks = data["tasks"]
        # 默认注册 8 个任务：capture, capture_full, detection, ml, attack, train, auto, demo
        assert len(tasks) >= 8
        names = {t["name"] for t in tasks}
        expected = {"capture", "capture_full", "detection", "ml", "attack", "train", "auto", "demo"}
        assert expected.issubset(names)

    def test_task_status_fields(self, client):
        """每个任务状态包含必要字段。"""
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        for t in tasks:
            assert "name" in t
            assert "kind" in t
            assert t["kind"] in ("continuous", "timed")
            assert "status" in t
            assert t["status"] in ("idle", "running", "stopping", "finished", "failed")
            assert "elapsed" in t
            assert "description" in t
            assert "default_duration" in t

    def test_idle_tasks_have_zero_elapsed(self, client):
        """空闲任务的 elapsed 为 0。"""
        resp = client.get("/api/tasks")
        tasks = resp.json()["tasks"]
        idle_tasks = [t for t in tasks if t["status"] == "idle"]
        assert len(idle_tasks) > 0
        for t in idle_tasks:
            assert t["elapsed"] == 0.0

    def test_description_and_default_duration(self, client):
        """任务包含 description 和 default_duration 字段。"""
        resp = client.get("/api/tasks")
        tasks = resp.json()["tasks"]
        # capture 是连续任务，无 default_duration
        capture = next(t for t in tasks if t["name"] == "capture")
        assert capture["description"] == "基础抓包"
        assert capture["default_duration"] is None
        # capture_full 是限时任务，有 default_duration
        capture_full = next(t for t in tasks if t["name"] == "capture_full")
        assert capture_full["description"] == "增强抓包（18维流特征+TLS）"
        assert capture_full["default_duration"] == 60

    def test_running_task_shows_elapsed(self, client, app):
        """运行中的任务显示 elapsed > 0。"""
        _register_test_task(app)
        registry = app.state.task_registry
        registry.start("_test_task")
        try:
            time.sleep(0.15)  # 等待任务启动
            resp = client.get("/api/tasks")
            tasks = resp.json()["tasks"]
            test_task = next(t for t in tasks if t["name"] == "_test_task")
            assert test_task["status"] == "running"
            assert test_task["elapsed"] > 0
        finally:
            registry.stop("_test_task")
            # 清理测试任务
            del registry._tasks["_test_task"]
            if "_test_task" in registry._handles:
                del registry._handles["_test_task"]


# ── T2.2: POST /api/tasks/{name}/start ────────────────────────────


class TestStartTask:
    """POST /api/tasks/{name}/start — 启动指定任务。"""

    def test_start_task_not_found(self, client):
        """启动不存在的任务返回 404。"""
        resp = _post_with_csrf(client, "/api/tasks/nonexistent/start", {"duration": 10})
        assert resp.status_code == 404
        assert "不存在" in resp.json()["detail"]

    def test_start_task_no_target_returns_503(self, client, app):
        """启动无工作函数的任务返回 503。

        P0-1 后默认任务已接入真实 target，需显式注册无 target 任务来验证 503 行为。
        """
        # 注册一个无 target 的自定义任务
        registry: TaskRegistry = app.state.task_registry
        registry.register(Task(name="_test_no_target", kind=TaskKind.CONTINUOUS,
                               target=None, description="测试空壳任务"))
        resp = _post_with_csrf(client, "/api/tasks/_test_no_target/start")
        assert resp.status_code == 503
        assert "暂不可用" in resp.json()["detail"] or "尚未接入" in resp.json()["detail"]
        # 清理
        del registry._tasks["_test_no_target"]

    def test_start_continuous_task_success(self, client, app):
        """启动连续任务成功。"""
        _register_test_task(app, name="_test_continuous", kind=TaskKind.CONTINUOUS)
        try:
            resp = _post_with_csrf(client, "/api/tasks/_test_continuous/start")
            assert resp.status_code == 200
            assert "已启动" in resp.json()["message"]
        finally:
            registry = app.state.task_registry
            registry.stop("_test_continuous")
            del registry._tasks["_test_continuous"]
            if "_test_continuous" in registry._handles:
                del registry._handles["_test_continuous"]

    def test_start_timed_task_with_duration(self, client, app):
        """启动限时任务并指定 duration。"""
        _register_timed_test_task(app, name="_test_timed_start", default_duration=5)
        try:
            resp = _post_with_csrf(client, "/api/tasks/_test_timed_start/start",
                                   {"duration": 10})
            assert resp.status_code == 200
            assert "已启动" in resp.json()["message"]
        finally:
            registry = app.state.task_registry
            registry.stop("_test_timed_start")
            del registry._tasks["_test_timed_start"]
            if "_test_timed_start" in registry._handles:
                del registry._handles["_test_timed_start"]

    def test_start_already_running_returns_409(self, client, app):
        """重复启动已在运行的任务返回 409。"""
        _register_test_task(app, name="_test_dup", kind=TaskKind.CONTINUOUS)
        registry = app.state.task_registry
        try:
            # 第一次启动
            resp1 = _post_with_csrf(client, "/api/tasks/_test_dup/start")
            assert resp1.status_code == 200
            # 第二次启动 → 409
            resp2 = _post_with_csrf(client, "/api/tasks/_test_dup/start")
            assert resp2.status_code == 409
            assert "已在运行" in resp2.json()["detail"]
        finally:
            registry.stop("_test_dup")
            del registry._tasks["_test_dup"]
            if "_test_dup" in registry._handles:
                del registry._handles["_test_dup"]


# ── T2.3: POST /api/tasks/{name}/stop ─────────────────────────────


class TestStopTask:
    """POST /api/tasks/{name}/stop — 停止指定任务。"""

    def test_stop_task_not_found(self, client):
        """停止不存在的任务返回 404。"""
        resp = _post_with_csrf(client, "/api/tasks/nonexistent/stop")
        assert resp.status_code == 404
        assert "不存在" in resp.json()["detail"]

    def test_stop_idle_task_idempotent(self, client):
        """停止未运行的任务返回 200（幂等）。"""
        # capture 是默认注册的连续任务，初始状态为 idle
        resp = _post_with_csrf(client, "/api/tasks/capture/stop")
        assert resp.status_code == 200
        assert "未在运行" in resp.json()["message"]

    def test_stop_running_task_success(self, client, app):
        """停止运行中的任务成功。"""
        _register_test_task(app, name="_test_stop", kind=TaskKind.CONTINUOUS)
        registry = app.state.task_registry
        registry.start("_test_stop")
        time.sleep(0.1)  # 等待任务启动
        try:
            resp = _post_with_csrf(client, "/api/tasks/_test_stop/stop")
            assert resp.status_code == 200
            assert "停止请求已发送" in resp.json()["message"]
        finally:
            del registry._tasks["_test_stop"]
            if "_test_stop" in registry._handles:
                del registry._handles["_test_stop"]

    def test_stop_is_idempotent(self, client, app):
        """多次停止同一任务均返回 200。"""
        _register_test_task(app, name="_test_idem", kind=TaskKind.CONTINUOUS)
        registry = app.state.task_registry
        registry.start("_test_idem")
        time.sleep(0.1)
        try:
            resp1 = _post_with_csrf(client, "/api/tasks/_test_idem/stop")
            assert resp1.status_code == 200
            # 第二次停止 → 幂等，返回 200
            time.sleep(0.1)
            resp2 = _post_with_csrf(client, "/api/tasks/_test_idem/stop")
            assert resp2.status_code == 200
        finally:
            del registry._tasks["_test_idem"]
            if "_test_idem" in registry._handles:
                del registry._handles["_test_idem"]


# ── 端到端：启动→查询→停止 ────────────────────────────────────────


class TestTaskLifecycle:
    """任务生命周期：启动 → 查询状态 → 停止。"""

    def test_start_query_stop_lifecycle(self, client, app):
        """完整生命周期：启动→查询显示running→停止→查询显示非running。"""
        _register_test_task(app, name="_test_lifecycle", kind=TaskKind.CONTINUOUS)
        registry = app.state.task_registry
        try:
            # 1. 启动
            resp_start = _post_with_csrf(client, "/api/tasks/_test_lifecycle/start")
            assert resp_start.status_code == 200
            time.sleep(0.15)

            # 2. 查询 → running
            resp_query = client.get("/api/tasks")
            tasks = resp_query.json()["tasks"]
            lifecycle_task = next(t for t in tasks if t["name"] == "_test_lifecycle")
            assert lifecycle_task["status"] == "running"
            assert lifecycle_task["elapsed"] > 0

            # 3. 停止
            resp_stop = _post_with_csrf(client, "/api/tasks/_test_lifecycle/stop")
            assert resp_stop.status_code == 200
            time.sleep(0.15)

            # 4. 查询 → 非 running
            resp_query2 = client.get("/api/tasks")
            tasks2 = resp_query2.json()["tasks"]
            lifecycle_task2 = next(t for t in tasks2 if t["name"] == "_test_lifecycle")
            assert lifecycle_task2["status"] in ("stopping", "finished", "idle")
        finally:
            del registry._tasks["_test_lifecycle"]
            if "_test_lifecycle" in registry._handles:
                del registry._handles["_test_lifecycle"]

    def test_timed_task_auto_finish(self, client, app):
        """限时任务超时后自动结束。"""
        _register_timed_test_task(app, name="_test_auto_finish", default_duration=1)
        registry = app.state.task_registry
        try:
            # 启动限时任务（1秒）
            resp = _post_with_csrf(client, "/api/tasks/_test_auto_finish/start",
                                   {"duration": 1})
            assert resp.status_code == 200
            time.sleep(0.15)

            # 确认 running
            tasks = client.get("/api/tasks").json()["tasks"]
            t = next(x for x in tasks if x["name"] == "_test_auto_finish")
            assert t["status"] == "running"

            # 等待超时（看门狗 1 秒轮询 + 任务 1 秒时长 + 余量）
            time.sleep(4.0)

            # 确认已结束（finished 或 idle，stopping 是过渡态需再等）
            for _ in range(5):
                tasks2 = client.get("/api/tasks").json()["tasks"]
                t2 = next(x for x in tasks2 if x["name"] == "_test_auto_finish")
                if t2["status"] in ("finished", "idle"):
                    break
                time.sleep(0.3)
            assert t2["status"] in ("finished", "idle"), f"实际状态: {t2['status']}"
        finally:
            del registry._tasks["_test_auto_finish"]
            if "_test_auto_finish" in registry._handles:
                del registry._handles["_test_auto_finish"]