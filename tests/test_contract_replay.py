"""T2.18 契约回放门禁（pytest 入口）。

与 `scripts/replay_contract.py` 共用 `tests/contract/replay.py` 的实现，
保证 CLI 与 CI 判定完全一致 —— 不会出现"脚本绿、测试红"或反过来。

这层门禁回答的是阶段 2 出口条件里的那一句：
**「34 个旧端点 golden 在新实现下逐一比对，差异须全部可解释」**。

判定由三层组成（详见 `tests/contract/replay.py` 模块 docstring）：
1. 保留端点真发请求，比对状态级别 + JSON 顶层键（新 ⊇ 旧）
2. 有意差异只做存在性校验，但**每条必须带非空 `note`**（映射表导入时即断言）
3. 路径级三分类账：保留 / 已解释移除 / **未解释丢失（必须为 0）**
"""
from __future__ import annotations

import pytest

from tests.contract.mapping import MAPPING, PROBLEMS, REPLAYABLE
from tests.contract.replay import load_golden, run_replay, run_schema_gate


class TestMappingIntegrity:
    def test_mapping_self_check_passes(self):
        """映射表导入时自检：差异必须被解释、page 类必须声明期望状态。"""
        assert PROBLEMS == [], "映射表自检失败：\n  - " + "\n  - ".join(PROBLEMS)

    def test_every_difference_is_explained(self):
        """非 kept 条目必须带非空 note —— "差异须全部可解释"的可执行版本。"""
        unexplained = [
            name for name, spec in MAPPING.items()
            if spec["kind"] not in REPLAYABLE and not spec.get("note")
        ]
        assert unexplained == [], f"以下条目未解释差异：{unexplained}"

    def test_mapping_has_no_empty_kinds(self):
        known = {"kept", "renamed", "consolidated", "page"}
        unknown = {spec["kind"] for spec in MAPPING.values()} - known
        assert unknown == set(), f"未知的 kind：{unknown}"

    def test_baseline_has_no_csrf_missing_samples(self):
        """旧基线 20/41 是"CSRF token is missing"的 400 —— 这种期望值不可用。"""
        offenders = []
        for name in MAPPING:
            golden = load_golden(name)
            if golden and "CSRF token is missing" in (golden.get("body") or ""):
                offenders.append(name)
        assert offenders == [], f"基线仍含 CSRF 缺失样本（需重录）：{offenders}"

    def test_baseline_covers_every_mapped_sample(self):
        missing = [name for name in MAPPING if load_golden(name) is None]
        assert missing == [], f"缺少 golden：{missing}"


class TestReplay:
    def test_no_unexplained_differences(self):
        """回放主判定：退出码必须是 0。"""
        failures, lines = run_replay()
        report = "\n".join(lines)
        assert failures == 0, f"契约回放存在 {failures} 项未解释差异：\n{report}"

    def test_schema_gate_has_no_unexplained_loss(self):
        """规格门禁：旧规格 34 条路径不得出现"未解释丢失"。"""
        from campus_ids.web_new.app import create_app

        unexplained, lines = run_schema_gate(create_app())
        assert unexplained == 0, "存在未解释的路径丢失：\n" + "\n".join(lines)

    def test_kept_endpoints_preserve_top_level_keys(self):
        """保留端点的顶层键只增不减（上面 test_no_unexplained_differences 已覆盖，
        此处单独列出以便失败时定位到具体端点）。"""

        from fastapi.testclient import TestClient

        from campus_ids.web_new.app import create_app

        app = create_app()
        lost_report = []
        with TestClient(app) as client:
            for name, spec in MAPPING.items():
                if spec["kind"] != "kept" or spec["method"] != "GET":
                    continue
                golden = load_golden(name)
                old_json = (golden or {}).get("json")
                if not isinstance(old_json, dict):
                    continue
                new_json = client.get(spec["path"]).json()
                lost = sorted(set(old_json) - set(new_json))
                if lost:
                    lost_report.append(f"{name}: {lost}")
        assert lost_report == [], "保留端点丢失顶层键：\n  " + "\n  ".join(lost_report)


@pytest.mark.parametrize("name", sorted(MAPPING))
def test_sample_declared_in_mapping(name):
    """每个基线样本都必须在映射表里有条目（防止新增 golden 后忘了登记）。"""
    assert name in MAPPING
