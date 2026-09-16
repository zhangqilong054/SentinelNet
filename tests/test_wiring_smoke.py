"""接线冒烟测试 — 验证 create_app() 注册的任务已接入真实 target。

报告 T2 §4.1 发现：8 个 Task 全部 target=None，TaskRegistry.start() 直接返回
503「尚未接入工作函数」。测试全绿是因为 test_web_tasks.py 自己构造 Task
并改写 registry._tasks 内部字典，绕开了真实接线。

本测试只用 create_app()（通过 TestClient 触发 lifespan），不注入任何 target，
断言每个 task 的 target 不为 None。当前预期全部失败，当 services/ 层真正
落地后应逐一移除 xfail。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from campus_ids.web_new.app import create_app


@pytest.fixture
def client():
    """创建 TestClient（触发 lifespan 以初始化 task_registry）。"""
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestTaskWiring:
    """验证 create_app() 注册的任务已接入工作函数（target 非 None）。"""

    # 期望接线的任务名与对应 service
    EXPECTED_TASKS = {
        "capture": "capture_service",
        "capture_full": "capture_service",
        "detection": "detection_service",
        "ml": "detection_service",
        "attack": "scenario_service (attack)",
        "train": "model_service",
        "auto": "scenario_service (full)",
        "demo": "scenario_service (demo)",
    }

    @pytest.mark.parametrize(
        "task_name,service",
        list(EXPECTED_TASKS.items()),
        ids=list(EXPECTED_TASKS.keys()),
    )
    def test_task_has_target(self, client, task_name, service):
        """每个注册任务应有非 None 的 target（工作函数）。"""
        registry = client.app.state.task_registry
        task = registry._tasks.get(task_name)
        assert task is not None, f"任务 '{task_name}' 未注册"
        assert task.target is not None, (
            f"任务 '{task_name}' 的 target 为 None — "
            f"应接入 {service} 的工作函数"
        )