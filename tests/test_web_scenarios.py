"""T2.4-T2.5 剧本 API 集成测试 — 验证 GET /api/scenarios, POST start/stop。

测试覆盖：
- GET /api/scenarios：返回 3 个剧本（demo/full/attack），含 name/steps/description
- POST /api/scenarios/start：启动成功、404 不存在、409 子任务冲突、503 空壳
- POST /api/scenarios/stop：停止成功、幂等未运行、404 不存在
- 剧本生命周期：启动 → 查询任务状态 → 停止
- duration 参数传递
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
    """认证关闭的 TestClient（触发 lifespan 以初始化 task_registry + scenario_service）。"""
    with TestClient(app) as c:
        yield c


def _get_csrf_token(client: TestClient) -> str:
    """获取 CSRF token 并设置 cookie。"""
    resp = client.get("/api/csrf-token")
    assert resp.status_code == 200
    csrf_token = resp.json()["csrf_token"]
    client.cookies.set("csrf_token", csrf_token)
    return csrf_token


def _post_with_csrf(client: TestClient, url: str, json: dict | None = None):
    """带 CSRF 的 POST 请求。"""
    csrf_token = _get_csrf_token(client)
    return client.post(url, json=json or {}, headers={"X-CSRFToken": csrf_token})


# ── 辅助：注册带真实 target 的测试任务 ──────────────────────────────


def _register_test_task(app, name: str, kind: TaskKind = TaskKind.CONTINUOUS,
                        duration: int | None = None) -> threading.Event:
    """在应用的 TaskRegistry 中注册一个带真实 target 的测试任务。"""
    stop_event = threading.Event()

    def _worker(stop_event: threading.Event = None, **kwargs):
        while stop_event and not stop_event.is_set():
            time.sleep(0.05)

    task = Task(name=name, kind=kind, target=_worker,
                default_duration=duration, description=f"测试任务 {name}")
    registry: TaskRegistry = app.state.task_registry
    if name in registry._tasks:
        del registry._tasks[name]
    registry.register(task)
    return stop_event


def _register_timed_test_task(app, name: str, default_duration: int = 2) -> None:
    """注册一个限时测试任务。"""

    def _worker(stop_event: threading.Event = None, duration: int = 2, **kwargs):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and stop_event and not stop_event.is_set():
            time.sleep(0.05)

    task = Task(name=name, kind=TaskKind.TIMED, target=_worker,
                default_duration=default_duration, description=f"限时测试任务 {name}")
    registry: TaskRegistry = app.state.task_registry
    if name in registry._tasks:
        del registry._tasks[name]
    registry.register(task)


# ── T2.5: GET /api/scenarios ─────────────────────────────────────


class TestListScenarios:
    """GET /api/scenarios — 获取可用剧本列表。"""

    def test_returns_three_scenarios(self, client):
        """返回 3 个剧本：demo、full、attack。"""
        resp = client.get("/api/scenarios")
        assert resp.status_code == 200
        data = resp.json()
        scenarios = data["scenarios"]
        assert len(scenarios) == 3
        names = {s["name"] for s in scenarios}
        assert names == {"demo", "full", "attack"}

    def test_scenario_has_required_fields(self, client):
        """每个剧本包含 name、steps、description 字段。"""
        resp = client.get("/api/scenarios")
        assert resp.status_code == 200
        scenarios = resp.json()["scenarios"]
        for s in scenarios:
            assert "name" in s
            assert "steps" in s
            assert "description" in s
            assert isinstance(s["steps"], list)

    def test_demo_scenario_steps(self, client):
        """demo 剧本包含 capture、ml、attack 三步。"""
        resp = client.get("/api/scenarios")
        scenarios = resp.json()["scenarios"]
        demo = next(s for s in scenarios if s["name"] == "demo")
        assert demo["steps"] == ["capture", "ml", "attack"]

    def test_full_scenario_steps(self, client):
        """full 剧本包含 capture_full、train、ml 三步。"""
        resp = client.get("/api/scenarios")
        scenarios = resp.json()["scenarios"]
        full = next(s for s in scenarios if s["name"] == "full")
        assert full["steps"] == ["capture_full", "train", "ml"]

    def test_attack_scenario_steps(self, client):
        """attack 剧本仅包含 attack 一步。"""
        resp = client.get("/api/scenarios")
        scenarios = resp.json()["scenarios"]
        attack = next(s for s in scenarios if s["name"] == "attack")
        assert attack["steps"] == ["attack"]


# ── T2.4: POST /api/scenarios/start ──────────────────────────────


class TestStartScenario:
    """POST /api/scenarios/start — 启动剧本。"""

    def test_404_unknown_scenario(self, client):
        """启动不存在的剧本返回 404。"""
        resp = _post_with_csrf(client, "/api/scenarios/start", {"scenario": "nonexistent"})
        assert resp.status_code == 404
        assert "不存在" in resp.json()["detail"]

    def test_503_empty_shell_scenario(self, client, app):
        """启动空壳剧本（子任务无 target）返回 503。

        P0-1 后默认任务已接入真实 target，需显式注册无 target 任务来验证 503 行为。
        """
        # 将 attack 任务替换为无 target 的空壳
        registry: TaskRegistry = app.state.task_registry
        from campus_ids.runtime.tasks import Task
        registry._tasks["attack"] = Task(
            name="attack", kind=TaskKind.TIMED, target=None,
            default_duration=5, description="测试空壳任务",
        )
        resp = _post_with_csrf(client, "/api/scenarios/start", {"scenario": "attack"})
        assert resp.status_code == 503
        # attack 剧本只有 1 个子任务，无需回滚
        assert "启动失败" in resp.json()["detail"] or "尚未接入" in resp.json()["detail"]

    def test_200_start_with_real_targets(self, client, app):
        """启动剧本（子任务有真实 target）返回 200。"""
        # 为 attack 剧本的子任务注册真实 target
        _register_test_task(app, "attack", TaskKind.TIMED, duration=5)
        resp = _post_with_csrf(client, "/api/scenarios/start", {"scenario": "attack"})
        assert resp.status_code == 200
        assert "已启动" in resp.json()["message"]
        # 清理：停止任务
        app.state.task_registry.stop("attack")
        time.sleep(0.3)

    def test_200_start_with_duration(self, client, app):
        """启动剧本并传递 duration 参数。"""
        _register_timed_test_task(app, "attack", default_duration=10)
        resp = _post_with_csrf(client, "/api/scenarios/start",
                               {"scenario": "attack", "duration": 3})
        assert resp.status_code == 200
        # 清理
        app.state.task_registry.stop("attack")
        time.sleep(0.3)

    def test_409_conflict_subtask_running(self, client, app):
        """子任务已在运行时启动剧本返回 409。"""
        # 先启动 attack 任务
        _register_test_task(app, "attack", TaskKind.TIMED, duration=30)
        app.state.task_registry.start("attack", duration=30)
        time.sleep(0.1)  # 等待线程启动
        try:
            resp = _post_with_csrf(client, "/api/scenarios/start", {"scenario": "attack"})
            assert resp.status_code == 409
            assert "已在运行" in resp.json()["detail"]
        finally:
            app.state.task_registry.stop("attack")
            time.sleep(0.3)

    def test_rollback_on_failure(self, client, app):
        """多步剧本中某步失败时，已启动的子任务被回滚（停止）。

        demo 剧本：capture(连续) + ml(连续) + attack(限时)
        capture 和 ml 有真实 target，attack 被替换为空壳 → attack 失败 → capture/ml 被回滚
        """
        _register_test_task(app, "capture", TaskKind.CONTINUOUS)
        _register_test_task(app, "ml", TaskKind.CONTINUOUS)
        # 将 attack 替换为无 target 的空壳以触发失败
        registry: TaskRegistry = app.state.task_registry
        registry._tasks["attack"] = Task(
            name="attack", kind=TaskKind.TIMED, target=None,
            default_duration=5, description="测试空壳任务",
        )

        resp = _post_with_csrf(client, "/api/scenarios/start", {"scenario": "demo"})
        assert resp.status_code == 503

        # 验证 capture 和 ml 已被回滚（不再运行）
        capture_status = app.state.task_registry.status("capture")
        ml_status = app.state.task_registry.status("ml")
        assert capture_status["status"] != "running"
        assert ml_status["status"] != "running"


# ── T2.4: POST /api/scenarios/stop ───────────────────────────────


class TestStopScenario:
    """POST /api/scenarios/stop — 停止剧本。"""

    def test_404_unknown_scenario(self, client):
        """停止不存在的剧本返回 404。"""
        resp = _post_with_csrf(client, "/api/scenarios/stop", {"scenario": "nonexistent"})
        assert resp.status_code == 404

    def test_200_idempotent_not_running(self, client):
        """停止未运行的剧本返回 200（幂等）。"""
        resp = _post_with_csrf(client, "/api/scenarios/stop", {"scenario": "attack"})
        assert resp.status_code == 200
        assert "无运行中的子任务" in resp.json()["message"]

    def test_200_stop_running_scenario(self, client, app):
        """停止运行中的剧本返回 200。"""
        _register_test_task(app, "attack", TaskKind.TIMED, duration=30)
        app.state.task_registry.start("attack", duration=30)
        time.sleep(0.1)  # 等待线程启动
        try:
            resp = _post_with_csrf(client, "/api/scenarios/stop", {"scenario": "attack"})
            assert resp.status_code == 200
            assert "停止请求已发送" in resp.json()["message"]
        finally:
            time.sleep(0.3)

    def test_200_idempotent_repeat_stop(self, client, app):
        """重复停止同一剧本均返回 200（幂等）。"""
        _register_test_task(app, "attack", TaskKind.TIMED, duration=30)
        app.state.task_registry.start("attack", duration=30)
        time.sleep(0.1)
        try:
            resp1 = _post_with_csrf(client, "/api/scenarios/stop", {"scenario": "attack"})
            assert resp1.status_code == 200
            time.sleep(0.2)
            resp2 = _post_with_csrf(client, "/api/scenarios/stop", {"scenario": "attack"})
            assert resp2.status_code == 200
        finally:
            time.sleep(0.3)


# ── T2.4: 剧本生命周期 ──────────────────────────────────────────


class TestScenarioLifecycle:
    """剧本完整生命周期测试。"""

    def test_start_query_stop_lifecycle(self, client, app):
        """启动剧本 → 查询任务状态 → 停止剧本。"""
        # 注册 attack 子任务的真实 target
        _register_test_task(app, "attack", TaskKind.TIMED, duration=30)

        # 1. 启动 attack 剧本
        resp = _post_with_csrf(client, "/api/scenarios/start", {"scenario": "attack"})
        assert resp.status_code == 200
        time.sleep(0.1)

        # 2. 查询任务状态，attack 应为 running
        tasks_resp = client.get("/api/tasks")
        tasks = tasks_resp.json()["tasks"]
        attack_task = next(t for t in tasks if t["name"] == "attack")
        assert attack_task["status"] == "running"

        # 3. 停止剧本
        stop_resp = _post_with_csrf(client, "/api/scenarios/stop", {"scenario": "attack"})
        assert stop_resp.status_code == 200
        time.sleep(0.3)

        # 4. 验证 attack 已停止
        tasks_resp2 = client.get("/api/tasks")
        tasks2 = tasks_resp2.json()["tasks"]
        attack_task2 = next(t for t in tasks2 if t["name"] == "attack")
        assert attack_task2["status"] in ("idle", "stopping", "finished")

    def test_multi_step_scenario_lifecycle(self, client, app):
        """多步剧本（demo）生命周期：启动 → 验证子任务 → 停止。

        demo: capture(连续) + ml(连续) + attack(限时)
        """
        _register_test_task(app, "capture", TaskKind.CONTINUOUS)
        _register_test_task(app, "ml", TaskKind.CONTINUOUS)
        _register_test_task(app, "attack", TaskKind.TIMED, duration=30)

        # 1. 启动 demo 剧本
        resp = _post_with_csrf(client, "/api/scenarios/start", {"scenario": "demo"})
        assert resp.status_code == 200
        assert "3 个子任务" in resp.json()["message"]
        time.sleep(0.2)

        # 2. 验证所有子任务都在运行
        tasks_resp = client.get("/api/tasks")
        tasks = tasks_resp.json()["tasks"]
        for name in ["capture", "ml", "attack"]:
            task = next(t for t in tasks if t["name"] == name)
            assert task["status"] == "running", f"{name} 应为 running，实际为 {task['status']}"

        # 3. 停止 demo 剧本
        stop_resp = _post_with_csrf(client, "/api/scenarios/stop", {"scenario": "demo"})
        assert stop_resp.status_code == 200
        time.sleep(0.3)