"""契约回放核心（T2.18）。

被两处复用，保证 CLI 与 pytest 走同一套判定：

- `scripts/replay_contract.py`（CLI，退出码 = 未解释差异数）
- `tests/test_contract_replay.py`（pytest 门禁）

## 判定三层

1. **保留端点行为比对**（`kept` + GET）—— 真发请求，断言
   ① 状态级别相同 ② 旧响应的顶层 JSON 键在新响应里**一个都不少**（新 ⊇ 旧）。
   这是"行为不变"唯一能廉价验证的部分。
2. **有意差异的存在性校验**（`renamed` / `consolidated` / `page`）——
   只断言替代端点在 `app.openapi()` 里存在，不真发请求
   （旧基线里 `/api/cleanup`、`/api/save`、`/api/model/train` 都是破坏性的）。
   每个条目的 `note` 由 `mapping.validate_mapping()` 强制非空 = 差异必须可解释。
3. **规格门禁**（`--schema-only` 只跑这一层）—— 用旧 `apispec.json`（T0.2 冻结的
   Swagger 2.0）对新 `app.openapi()`（OpenAPI 3.1）做三分类账：
   保留 / 已解释移除 / **未解释丢失**。未解释丢失必须为 0。

   为什么不直接用 `oasdiff`：它是 Go 二进制，本机未安装。本层是等价目的下的
   Python 实现（覆盖面较窄：只做路径级三分类账 + 保留端点的键集比对，
   不做参数级 / 类型级破坏性判定）。**这是已知的能力差距，不假装等价。**
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.contract.mapping import MAPPING, REPLAYABLE

CONTRACT_DIR = Path(__file__).resolve().parent
BASELINE_DIR = CONTRACT_DIR / "baseline"


def load_golden(name: str) -> dict | None:
    path = BASELINE_DIR / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _status_class(code: int) -> int:
    return code // 100


def _spec_paths(app) -> set[str]:
    """新应用的路径集合。

    ⚠️ 必须用 `app.openapi()["paths"]` —— FastAPI 0.141 下 `include_router`
    在 `app.routes` 里是 `_IncludedRouter` 占位对象（无 `.path`），
    遍历 `app.routes` 会数到 0 个 `/api` 路由。
    """
    return set(app.openapi()["paths"])


def _old_spec_paths() -> set[str]:
    spec = json.loads((BASELINE_DIR / "apispec.json").read_text(encoding="utf-8"))
    return set(spec.get("paths", {}))


def _explained_old_paths() -> set[str]:
    """映射表里登记过的旧路径（这些路径的消失/更名已被解释）。"""
    out = set()
    for spec in MAPPING.values():
        old = spec["old"]
        if " " in old:
            out.add(old.split(" ", 1)[1])
    return out


def compare_kept(client, name: str, spec: dict) -> list[str]:
    """对保留端点做真实请求比对，返回问题列表。"""
    golden = load_golden(name)
    if golden is None:
        return [f"{name}: 缺少 golden 文件"]

    resp = client.get(spec["path"])

    problems: list[str] = []
    old_code = golden["status_code"]
    if _status_class(old_code) != _status_class(resp.status_code):
        problems.append(
            f"{name}: 状态级别变化 旧 {old_code} → 新 {resp.status_code} ({spec['path']})"
        )

    old_json = golden.get("json")
    if isinstance(old_json, dict):
        try:
            new_json = resp.json()
        except Exception:
            return problems + [f"{name}: 新响应不是 JSON（{spec['path']}）"]
        if not isinstance(new_json, dict):
            problems.append(f"{name}: 新响应类型由 object 变为 {type(new_json).__name__}")
        else:
            lost = sorted(set(old_json) - set(new_json))
            if lost:
                problems.append(f"{name}: 新响应丢失顶层键 {lost}")
    return problems


def check_page(client, name: str, spec: dict) -> list[str]:
    """页面路由的存在性判定 —— **必须真发请求**。

    页面路由有意设了 `include_in_schema=False`（HTML 不该进阶段 3 的 TS 类型生成），
    所以用 OpenAPI 查它们会一律"缺失"。判据改为状态码落在 `expect_status` 内：
    404 才是"页面不存在"，302/405/400 都是"路由存在且行为符合声明"。
    """
    if spec["method"] == "GET":
        resp = client.get(spec["path"])
    else:
        # 只发空 body：唯一的副作用是渲染一个错误页，不会改任何状态
        resp = client.post(spec["path"], follow_redirects=False)

    if resp.status_code in spec["expect_status"]:
        return []
    return [
        f"{name}: {spec['path']} 期望状态 {spec['expect_status']}，实测 {resp.status_code}"
    ]


def run_schema_gate(app) -> tuple[int, list[str]]:
    """路径级三分类账。返回 (未解释丢失数, 输出行)。"""
    old_paths = _old_spec_paths()
    new_paths = _spec_paths(app)
    explained = _explained_old_paths()

    kept = sorted(old_paths & new_paths)
    gone_explained = sorted((old_paths - new_paths) & explained)
    gone_unexplained = sorted((old_paths - new_paths) - explained)
    added = sorted(new_paths - old_paths)

    lines = [
        f"旧规格路径            : {len(old_paths)}",
        f"  保留（新旧都有）    : {len(kept)}  {kept if len(kept) <= 12 else kept[:12] + ['…']}",
        f"  已解释的移除/更名    : {len(gone_explained)}  {gone_explained if len(gone_explained) <= 12 else gone_explained[:12] + ['…']}",
        f"  未解释丢失 🔴        : {len(gone_unexplained)}  {gone_unexplained}",
        f"新规格新增            : {len(added)}  {added if len(added) <= 8 else added[:8] + ['…']}",
    ]
    return len(gone_unexplained), lines


def run_replay(*, schema_only: bool = False) -> tuple[int, list[str]]:
    """执行回放。返回 (失败数, 输出行)。**调用前必须已完成 data_dir 隔离。**"""
    from campus_ids.web_new.app import create_app

    app = create_app()
    lines: list[str] = []
    failures = 0

    new_paths = _spec_paths(app)

    # ── 第 1、2 层：逐样本 ────────────────────────────────────
    lines.append("── 逐样本回放 ──────────────────────────────────────────")
    kept_checked = existence_checked = page_checked = 0

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        for name in sorted(MAPPING):
            spec = MAPPING[name]
            kind = spec["kind"]

            if kind == "page":
                problems = check_page(client, name, spec)
                page_checked += 1
            elif kind in REPLAYABLE and spec["method"] == "GET" and not schema_only:
                problems = compare_kept(client, name, spec)
                kept_checked += 1
            else:
                problems = [] if spec["path"] in new_paths else [
                    f"{name}: 替代端点缺失 {spec['path']}"
                ]
                existence_checked += 1

            if problems:
                failures += len(problems)
                lines.extend(f"  🔴 {p}" for p in problems)
            elif kind == "page":
                lines.append(
                    f"  ✅ {name:<24} page         {spec['method']} {spec['path']} "
                    f"→ {spec['expect_status']}"
                )
            elif kind in REPLAYABLE and spec["method"] == "GET" and not schema_only:
                lines.append(f"  ✅ {name:<24} 行为一致（{spec['path']}）")
            else:
                lines.append(f"  ✅ {name:<24} {kind:<12} → {spec['path']}")

    # ── 第 3 层：规格门禁 ─────────────────────────────────────
    lines.append("")
    lines.append("── 规格门禁（旧 apispec.json → 新 openapi）──────────────")
    unexplained, gate_lines = run_schema_gate(app)
    lines.extend(f"  {ln}" for ln in gate_lines)
    failures += unexplained

    # ── golden 覆盖检查 ───────────────────────────────────────
    lines.append("")
    lines.append("── 基线覆盖 ────────────────────────────────────────────")
    missing = [n for n in MAPPING if load_golden(n) is None]
    if missing:
        failures += len(missing)
        lines.append(f"  🔴 缺少 golden：{missing}")
    else:
        lines.append(f"  ✅ {len(MAPPING)} 个样本均有 golden（{BASELINE_DIR}）")

    meta = BASELINE_DIR / "_meta.json"
    if meta.exists():
        info = json.loads(meta.read_text(encoding="utf-8"))
        csrf_missing = info.get("csrf_missing_samples", "?")
        lines.append(f"  基线录制时间 {info.get('recorded_at')}，CSRF 缺失样本 {csrf_missing}（必须 0）")
        if csrf_missing not in (0, "0"):
            failures += 1
            lines.append("  🔴 基线含 CSRF 缺失样本，期望值不可用 → 运行 scripts/record_golden.py")

    lines.append("")
    lines.append(
        f"合计：真实请求比对 {kept_checked} 项 / 页面请求校验 {page_checked} 项 / "
        f"存在性校验 {existence_checked} 项 / 未解释差异 {failures} 项"
    )
    return failures, lines
