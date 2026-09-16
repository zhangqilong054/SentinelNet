"""services/scenario_service.py — 演练剧本业务用例。

合并原 attack / auto / demo 三组编排为统一的「剧本」模型。
取代 attack_sim_state.py + helpers.py 的 auto/demo 线程。

剧本定义（ADR-0001 §3.3）：
- demo:    ['capture', 'ml', 'attack']       — 原 /api/demo/start
- full:    ['capture_full', 'train', 'ml']   — 原 /api/auto/start
- attack:  ['attack']                        — 原 /api/attack/start

阶段1空壳，阶段2接入实际编排逻辑。
"""
from __future__ import annotations

from campus_ids.runtime.tasks import TaskKind


# 剧本定义：名称 → 子任务序列
SCENARIOS: dict[str, list[str]] = {
    "demo": ["capture", "ml", "attack"],
    "full": ["capture_full", "train", "ml"],
    "attack": ["attack"],
}


class ScenarioService:
    """剧本服务 — 管理多步编排（attack / auto / demo 合并）。"""

    def start_scenario(self, name: str, **kwargs) -> dict:
        """启动剧本（按序执行子任务）。

        Args:
            name: 剧本名称（demo / full / attack）
            **kwargs: 传递给子任务的参数

        Returns:
            {"status": "started"} 或 {"status": "already_running"} 或错误

        Raises:
            KeyError: 剧本未定义
        """
        raise NotImplementedError("阶段2接入")

    def stop_scenario(self, name: str) -> dict:
        """停止剧本（停止所有子任务）。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def scenario_status(self, name: str) -> dict:
        """查询剧本状态。阶段2实现。"""
        raise NotImplementedError("阶段2接入")

    def list_scenarios(self) -> list[dict]:
        """列出可用剧本。"""
        return [
            {"name": name, "steps": steps, "description": f"剧本 {name}"}
            for name, steps in SCENARIOS.items()
        ]