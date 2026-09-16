"""services/scenario_service.py — 演练剧本业务用例。

合并原 attack / auto / demo 三组编排为统一的「剧本」模型。
取代 attack_sim_state.py + helpers.py 的 auto/demo 线程。

剧本定义（ADR-0001 §3.3）：
- demo:    ['capture', 'ml', 'attack']       — 原 /api/demo/start
- full:    ['capture_full', 'train', 'ml']   — 原 /api/auto/start
- attack:  ['attack']                        — 原 /api/attack/start

关键修正（vs 旧实现）：
1. 子步骤任一失败即整体失败（旧 demo/start 在 ML 加载失败时仍返回 success）
2. 不再直接读 _helpers._capture_running 私有变量（改用 TaskRegistry 状态检查）
3. 冲突统一返回 already_running（旧各端点冲突检查不一致）
"""
from __future__ import annotations

import logging
from typing import Any

from campus_ids.runtime.tasks import TaskRegistry

logger = logging.getLogger(__name__)

# 剧本定义：名称 → 子任务序列
SCENARIOS: dict[str, list[str]] = {
    "demo": ["capture", "ml", "attack"],
    "full": ["capture_full", "train", "ml"],
    "attack": ["attack"],
}

# 剧本描述
SCENARIO_DESCRIPTIONS: dict[str, str] = {
    "demo": "演示模式：抓包 + ML 加载 + 攻击模拟",
    "full": "一键全流程：增强抓包 → 训练 → ML 加载",
    "attack": "攻击模拟",
}


class ScenarioService:
    """剧本服务 — 管理多步编排（attack / auto / demo 合并）。

    通过 TaskRegistry 启动/停止子任务，不直接操作线程或全局变量。
    """

    def __init__(self, registry: TaskRegistry) -> None:
        self._registry = registry

    def start_scenario(self, name: str, duration: int | None = None, **kwargs: Any) -> dict:
        """启动剧本（按序执行子任务）。

        Args:
            name: 剧本名称（demo / full / attack）
            duration: 限时子任务的运行秒数，None 使用子任务默认值
            **kwargs: 传递给子任务的参数

        Returns:
            {"status": "started", "steps": N} 或
            {"status": "already_running", "task": "xxx"} 或
            {"status": "error", "task": "xxx", "message": "..."}

        Raises:
            KeyError: 剧本未定义
        """
        if name not in SCENARIOS:
            raise KeyError(f"剧本未定义: {name}")

        steps = SCENARIOS[name]

        # 预检查：任一子任务已在运行则拒绝
        for step in steps:
            try:
                st = self._registry.status(step)
            except KeyError:
                return {"status": "error", "task": step,
                        "message": f"子任务 '{step}' 未注册"}
            if st.get("status") == "running":
                return {"status": "already_running", "task": step,
                        "message": f"子任务 '{step}' 已在运行"}

        # 按序启动子任务
        started: list[str] = []
        for step in steps:
            result = self._registry.start(step, duration=duration, **kwargs)
            if result.get("status") == "already_running":
                # 竞态：预检查与启动之间被其他请求抢先 → 回滚
                self._rollback(started)
                return {"status": "already_running", "task": step,
                        "message": f"子任务 '{step}' 已在运行"}
            if result.get("status") == "error":
                # 子任务启动失败 → 回滚已启动的子任务
                self._rollback(started)
                return {"status": "error", "task": step,
                        "message": result.get("message", f"子任务 '{step}' 启动失败")}
            started.append(step)

        logger.info("剧本 '%s' 已启动，%d 个子任务", name, len(started))
        return {"status": "started", "steps": len(started)}

    def stop_scenario(self, name: str) -> dict:
        """停止剧本（停止所有子任务，幂等）。

        Raises:
            KeyError: 剧本未定义
        """
        if name not in SCENARIOS:
            raise KeyError(f"剧本未定义: {name}")

        steps = SCENARIOS[name]
        stopped: list[str] = []
        for step in steps:
            try:
                result = self._registry.stop(step)
            except KeyError:
                continue  # 子任务未注册，跳过
            if result.get("status") == "stopping":
                stopped.append(step)

        if not stopped:
            return {"status": "not_running", "message": "剧本无运行中的子任务"}
        logger.info("剧本 '%s' 停止请求已发送，%d 个子任务", name, len(stopped))
        return {"status": "stopping", "stopped": stopped}

    def scenario_status(self, name: str) -> dict:
        """查询剧本状态（聚合子任务状态）。

        Raises:
            KeyError: 剧本未定义
        """
        if name not in SCENARIOS:
            raise KeyError(f"剧本未定义: {name}")

        steps = SCENARIOS[name]
        task_statuses: dict[str, dict] = {}
        for step in steps:
            try:
                task_statuses[step] = self._registry.status(step)
            except KeyError:
                task_statuses[step] = {"name": step, "status": "unknown"}

        # 聚合状态：任一 running → running；任一 stopping → stopping；
        # 任一 failed → failed；否则 idle
        running = [s for s, t in task_statuses.items() if t.get("status") == "running"]
        stopping = [s for s, t in task_statuses.items() if t.get("status") == "stopping"]
        failed = [s for s, t in task_statuses.items() if t.get("status") == "failed"]

        if running:
            overall = "running"
        elif stopping:
            overall = "stopping"
        elif failed:
            overall = "failed"
        else:
            overall = "idle"

        return {
            "name": name,
            "status": overall,
            "tasks": task_statuses,
        }

    def list_scenarios(self) -> list[dict]:
        """列出可用剧本。"""
        return [
            {
                "name": name,
                "steps": steps,
                "description": SCENARIO_DESCRIPTIONS.get(name, f"剧本 {name}"),
            }
            for name, steps in SCENARIOS.items()
        ]

    def _rollback(self, started: list[str]) -> None:
        """回滚已启动的子任务。"""
        for step in started:
            try:
                self._registry.stop(step)
                logger.warning("剧本启动失败，回滚子任务 '%s'", step)
            except KeyError:
                pass