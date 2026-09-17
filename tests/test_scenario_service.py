# -*- coding: utf-8 -*-
"""`services/scenario_service.py` 行为测试（R5 覆盖率补齐）。

改动前覆盖率 **65%** —— 已覆盖的只有"正常起停"这一条顺风路径
（`tests/test_web_scenarios.py` 打的是 HTTP 端点）。本文件补的是**失败路径**：
子任务未注册、子任务启动报错、启动竞态、回滚本身失败。

## 为什么这些路径值得单独测

`ScenarioService` 的核心价值就是"多步编排的一致性"——即**任一子步骤失败必须不留残骸**。
旧的 demo/auto 实现在这方面是坏的（ML 加载失败仍返回 success，且已启动的抓包线程
不会回收）。回滚逻辑 `_rollback()` 在顺风路径上永远不执行，所以只有构造失败场景
才能验证它。这也是"单测全绿 ≠ 功能成立"的又一处：65% 覆盖率里没有一行是回滚代码。

## 替身策略

`TaskRegistry` 是唯一依赖，用 `FakeRegistry` 替身：
可以逐步骤脚本化 `start`/`stop` 的返回值，从而精确制造竞态与失败。
不用真注册表是因为真注册表**无法**在中途故意失败。
"""
from __future__ import annotations

import pytest

from campus_ids.services.scenario_service import (
    SCENARIO_DESCRIPTIONS,
    SCENARIOS,
    ScenarioService,
)

DEMO_STEPS = SCENARIOS["demo"]  # ['capture', 'ml', 'attack']
FULL_STEPS = SCENARIOS["full"]  # ['capture_full', 'train', 'ml']


class FakeRegistry:
    """`TaskRegistry` 替身 —— 可脚本化每一步的返回值。

    - `registered`: 已注册任务名集合；`status()` 对未注册者抛 `KeyError`
    - `start_results` / `stop_results`: 逐次返回的脚本；**用完后最后一个值会粘住**
      （模拟注册表在稳态下反复返回同一结果），因此"脚本只写一次"不会被第二次调用
      意外回落成默认值 —— 那种回落是替身的行为，会伪装成服务缺陷。
    - `start_calls` / `stop_calls`: 调用记录，用于断言**顺序**与**是否被调用**
    """

    def __init__(self, registered=None, statuses=None) -> None:
        self.registered = set(registered if registered is not None else set())
        self.statuses: dict[str, str] = dict(statuses or {})
        self.start_results: dict[str, list[dict]] = {}
        self.stop_results: dict[str, list[dict]] = {}
        self._start_cursor: dict[str, int] = {}
        self._stop_cursor: dict[str, int] = {}
        self.start_calls: list[tuple[str, int | None, dict]] = []
        self.stop_calls: list[str] = []

    # ── 脚本化配置 ────────────────────────────────────────────────

    def script_start(self, name: str, *results: dict) -> "FakeRegistry":
        self.start_results[name] = list(results)
        return self

    def script_stop(self, name: str, *results: dict) -> "FakeRegistry":
        self.stop_results[name] = list(results)
        return self

    def set_status(self, name: str, status: str) -> "FakeRegistry":
        self.statuses[name] = status
        return self

    @staticmethod
    def _next(scripted: list[dict], cursor: dict, name: str) -> dict | None:
        """取第 n 个脚本值；越界后粘住最后一个。"""
        if not scripted:
            return None
        idx = min(cursor.get(name, 0), len(scripted) - 1)
        cursor[name] = idx + 1
        return scripted[idx]

    # ── TaskRegistry 接口 ─────────────────────────────────────────

    def status(self, name: str) -> dict:
        if name not in self.registered:
            raise KeyError(name)
        return {"name": name, "status": self.statuses.get(name, "idle")}

    def start(self, name: str, duration: int | None = None, **kwargs) -> dict:
        self.start_calls.append((name, duration, kwargs))
        if name not in self.registered:
            raise KeyError(name)
        scripted = self._next(self.start_results.get(name, []), self._start_cursor, name)
        return scripted if scripted is not None else {"status": "started"}

    def stop(self, name: str) -> dict:
        self.stop_calls.append(name)
        if name not in self.registered:
            raise KeyError(name)
        scripted = self._next(self.stop_results.get(name, []), self._stop_cursor, name)
        return scripted if scripted is not None else {"status": "stopping"}


def _registry(*names: str) -> FakeRegistry:
    return FakeRegistry(registered=set(names) or None)


@pytest.fixture
def registry() -> FakeRegistry:
    return _registry(*DEMO_STEPS, *FULL_STEPS)


@pytest.fixture
def svc(registry) -> ScenarioService:
    return ScenarioService(registry)


# ══ 剧本定义表本身 ════════════════════════════════════════════════


class TestScenarioDefinitions:
    def test_steps_match_adr(self):
        """ADR-0001 §3.3 钉死的三个剧本与步骤顺序。"""
        assert SCENARIOS["demo"] == ["capture", "ml", "attack"]
        assert SCENARIOS["full"] == ["capture_full", "train", "ml"]
        assert SCENARIOS["attack"] == ["attack"]

    def test_every_scenario_has_description(self):
        """漏写描述会让 `list_scenarios()` 回落成 "剧本 xxx"，前端展示降级。"""
        assert set(SCENARIO_DESCRIPTIONS) == set(SCENARIOS)

    def test_list_scenarios_shape(self, svc):
        listed = {s["name"]: s for s in svc.list_scenarios()}
        assert set(listed) == set(SCENARIOS)
        for name, item in listed.items():
            assert item["steps"] == SCENARIOS[name]
            assert item["description"] == SCENARIO_DESCRIPTIONS[name]


# ══ start_scenario ════════════════════════════════════════════════


class TestStartScenario:
    def test_unknown_scenario_raises_keyerror(self, svc):
        with pytest.raises(KeyError):
            svc.start_scenario("不存在")

    def test_happy_path_starts_all_steps_in_order(self, svc, registry):
        result = svc.start_scenario("demo")

        assert result == {"status": "started", "steps": 3}
        assert [name for name, _, _ in registry.start_calls] == DEMO_STEPS, (
            "子任务必须按剧本声明的顺序启动（先抓包再加载 ML）"
        )

    def test_duration_is_forwarded_to_every_step(self, svc, registry):
        svc.start_scenario("demo", duration=45)
        assert [d for _, d, _ in registry.start_calls] == [45, 45, 45]

    def test_extra_kwargs_are_forwarded(self, svc, registry):
        svc.start_scenario("full", dataset="cicids", quick=True)
        assert [kw for _, _, kw in registry.start_calls] == [
            {"dataset": "cicids", "quick": True},
            {"dataset": "cicids", "quick": True},
            {"dataset": "cicids", "quick": True},
        ]

    def test_default_duration_is_none(self, svc, registry):
        svc.start_scenario("attack")
        assert registry.start_calls == [("attack", None, {})]

    def test_precheck_rejects_when_step_already_running(self, svc, registry):
        """任一子任务在跑 → 整体拒绝，且**不得**启动任何子任务。"""
        registry.set_status("ml", "running")

        result = svc.start_scenario("demo")

        assert result["status"] == "already_running"
        assert result["task"] == "ml"
        assert registry.start_calls == [], "预检查失败后不得启动任何子任务"

    def test_precheck_covers_every_step(self, svc, registry):
        """预检查必须遍历**所有**子任务，不能只看第一个。"""
        registry.set_status("attack", "running")  # demo 的最后一个步骤
        result = svc.start_scenario("demo")
        assert result == {
            "status": "already_running",
            "task": "attack",
            "message": "子任务 'attack' 已在运行",
        }

    def test_unregistered_step_returns_error_without_starting(self, svc, registry):
        """子任务未注册 → 显式报错（旧实现会直接 KeyError 冒成 500）。"""
        registry.registered.discard("ml")

        result = svc.start_scenario("demo")

        assert result["status"] == "error"
        assert result["task"] == "ml"
        assert "未注册" in result["message"]
        assert registry.start_calls == []

    def test_step_start_error_rolls_back_started_steps(self, svc, registry):
        """第 2 步启动失败 → 第 1 步必须被停掉（否则留下孤儿抓包线程）。"""
        registry.script_start("ml", {"status": "error", "message": "ML 模型文件缺失"})

        result = svc.start_scenario("demo")

        assert result == {
            "status": "error",
            "task": "ml",
            "message": "ML 模型文件缺失",
        }
        assert registry.stop_calls == ["capture"], (
            f"应只回滚已启动的 capture，实际 {registry.stop_calls}"
        )

    def test_step_error_without_message_falls_back(self, svc, registry):
        registry.script_start("ml", {"status": "error"})
        result = svc.start_scenario("demo")
        assert result["message"] == "子任务 'ml' 启动失败"

    def test_first_step_error_stops_nothing(self, svc, registry):
        """第 1 步就失败 → 无物可回滚。"""
        registry.script_start("capture", {"status": "error", "message": "网卡占用"})

        result = svc.start_scenario("demo")

        assert result["status"] == "error" and result["task"] == "capture"
        assert registry.stop_calls == []

    def test_race_already_running_midway_rolls_back(self, svc, registry):
        """预检查通过后、启动过程中被其他请求抢先 → 必须回滚。

        这是预检查与启动之间的真实竞态窗口（两个请求同时打 start）。
        旧实现不回滚，会留下半启动的剧本。
        """
        registry.script_start("ml", {"status": "already_running"})

        result = svc.start_scenario("demo")

        assert result["status"] == "already_running"
        assert result["task"] == "ml"
        assert registry.stop_calls == ["capture"]

    def test_rollback_tolerates_stop_raising_keyerror(self, svc, registry):
        """回滚过程中子任务被注销不得让异常冒出去。"""
        registry.script_start("ml", {"status": "error", "message": "boom"})

        def exploding_stop(name):
            registry.stop_calls.append(name)
            raise KeyError(name)

        registry.stop = exploding_stop  # type: ignore[method-assign]

        result = svc.start_scenario("demo")  # 不得抛异常
        assert result["status"] == "error"
        assert registry.stop_calls == ["capture"]

    def test_single_step_scenario(self, svc, registry):
        assert svc.start_scenario("attack") == {"status": "started", "steps": 1}


# ══ stop_scenario ═════════════════════════════════════════════════


class TestStopScenario:
    def test_unknown_scenario_raises_keyerror(self, svc):
        with pytest.raises(KeyError):
            svc.stop_scenario("不存在")

    def test_stops_every_running_step(self, svc, registry):
        """`stop` 返回 stopping 的步骤才计入 stopped。"""
        result = svc.stop_scenario("demo")

        assert result == {"status": "stopping", "stopped": DEMO_STEPS}
        assert registry.stop_calls == DEMO_STEPS

    def test_not_running_when_nothing_was_stopping(self, svc, registry):
        for step in DEMO_STEPS:
            registry.script_stop(step, {"status": "not_running"})

        result = svc.stop_scenario("demo")

        assert result["status"] == "not_running"
        assert "stopped" not in result

    def test_partial_stop_reports_only_actually_stopped(self, svc, registry):
        """只有部分子任务在跑 → stopped 只能列真正在跑的那些。"""
        registry.script_stop("capture", {"status": "stopping"})
        registry.script_stop("ml", {"status": "not_running"})
        registry.script_stop("attack", {"status": "stopping"})

        result = svc.stop_scenario("demo")

        assert result == {"status": "stopping", "stopped": ["capture", "attack"]}

    def test_unregistered_step_is_skipped_not_fatal(self, svc, registry):
        """子任务被注销时 stop 应跳过（幂等），而不是抛 KeyError 变成 500。"""
        registry.registered.discard("ml")

        result = svc.stop_scenario("demo")

        assert result["status"] == "stopping"
        assert result["stopped"] == ["capture", "attack"]

    def test_stop_is_idempotent(self, svc, registry):
        for step in DEMO_STEPS:
            registry.script_stop(step, {"status": "not_running"})

        assert svc.stop_scenario("demo")["status"] == "not_running"
        assert svc.stop_scenario("demo")["status"] == "not_running"


# ══ scenario_status ═══════════════════════════════════════════════


class TestScenarioStatus:
    def test_unknown_scenario_raises_keyerror(self, svc):
        with pytest.raises(KeyError):
            svc.scenario_status("不存在")

    def test_idle_when_all_idle(self, svc):
        result = svc.scenario_status("demo")
        assert result["status"] == "idle"
        assert set(result["tasks"]) == set(DEMO_STEPS)
        assert result["name"] == "demo"

    def test_running_wins_over_everything(self, svc, registry):
        """聚合优先级：running > stopping > failed > idle。"""
        registry.set_status("capture", "running")
        registry.set_status("ml", "stopping")
        registry.set_status("attack", "failed")
        assert svc.scenario_status("demo")["status"] == "running"

    def test_stopping_wins_over_failed(self, svc, registry):
        registry.set_status("capture", "idle")
        registry.set_status("ml", "stopping")
        registry.set_status("attack", "failed")
        assert svc.scenario_status("demo")["status"] == "stopping"

    def test_failed_reported_when_nothing_active(self, svc, registry):
        registry.set_status("attack", "failed")
        assert svc.scenario_status("demo")["status"] == "failed"

    def test_unregistered_step_reported_as_unknown(self, svc, registry):
        registry.registered.discard("ml")

        result = svc.scenario_status("demo")

        assert result["tasks"]["ml"] == {"name": "ml", "status": "unknown"}
        # unknown 既不属 running/stopping/failed → 不影响聚合
        assert result["status"] == "idle"

    def test_finished_step_counts_as_idle(self, svc, registry):
        registry.set_status("capture", "finished")
        assert svc.scenario_status("demo")["status"] == "idle"

    def test_status_carries_per_task_detail(self, svc, registry):
        registry.set_status("ml", "running")
        tasks = svc.scenario_status("demo")["tasks"]
        assert tasks["ml"]["status"] == "running"
        assert tasks["capture"]["status"] == "idle"
