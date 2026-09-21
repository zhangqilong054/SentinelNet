# -*- coding: utf-8 -*-
"""T2 阶段验收探针 —— 一条命令复现全部结论（**安全版**）。

    C:/Users/18551/anaconda3/python.exe scripts/probe_t2_acceptance.py

⚠️ **为什么必须走 `_probe_safety`**：
本脚本的前一版（`b55a118` 提交）在任务未接线时无害；T2 接线完成后，
它的 `POST /api/tasks/train/start` 会**真的触发训练**，覆盖
`model.pkl` / `evaluation_report.txt` / `confusion_matrix.png`（三者都在 .gitignore，无副本）。
现在改为：① data_dir 隔离到临时目录；② 业务 service 换 stub；③ 训练函数加哨兵。
→ 本脚本现在是**只读**的，可反复运行。

判定一律由实测算出（`verdict()`），不再把结论写死在文案里 ——
上一版的文案在缺陷修复后全部变成误导。
"""
from __future__ import annotations

from _probe_safety import (  # noqa: E402
    ROOT,
    assert_targets_wired,
    bootstrap,
    guard_no_real_training,
    install_service_stubs,
    summary,
    verdict,
)

TMP = bootstrap()                      # ← 必须最先执行（import 项目模块之前）
guard_no_real_training()               # 兜底哨兵：真有 train() 调用则报错而非覆盖产物

from fastapi.testclient import TestClient  # noqa: E402

from campus_ids.web.app import create_app  # noqa: E402


def hr(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


print("data_dir 已隔离到:", TMP)

# ── ① 端点清单与编排面计数 ───────────────────────────────────────────
hr("① 端点清单（必须用 app.openapi()['paths'] —— app.routes 在 FastAPI 0.141 下数不到）")

spec = create_app().openapi()
paths = {p: sorted(m.keys()) for p, m in spec["paths"].items()}
print(f"  openapi={spec['openapi']}  路径数={len(paths)}")
orchestration = sorted(p for p in paths if p.startswith(("/api/tasks", "/api/scenarios")))
tasks_family = sorted(p for p in paths if p.startswith("/api/tasks"))
scenarios_family = sorted(p for p in paths if p.startswith("/api/scenarios"))
print(f"  编排面端点 {len(orchestration)} 条（tasks 族 {len(tasks_family)} + scenarios 族 {len(scenarios_family)}）：")
for p in orchestration:
    print(f"     {p:<34} {','.join(paths[p])}")

# 口径说明（2026-09-17）：清单原文的目标是「20 → 3」，但那个 3 只覆盖 tasks 族。
# D3「三合一剧本」是**并列新增**的独立族（T2.4/T2.5 单独列为任务），
# 因此把 scenarios 算进来要求 ≤3 从一开始就不可能成立。
# 这里拆成两个判定，各自的判据都写清楚，不含糊过去。
verdict("编排面 tasks 族 ≤ 3 条（「20 → 3」的字面目标）",
        len(tasks_family) <= 3, f"实际 {len(tasks_family)} 条")
verdict("编排面合计 ≤ 6 条（tasks 3 + scenarios 3，D3 后的实际口径）",
        len(orchestration) <= 6,
        f"实际 {len(orchestration)} 条；相对旧应用 21 条编排端点收敛 {100 - round(len(orchestration) / 21 * 100)}%")

# ── ② 接线断言（T2.1-T2.4）──────────────────────────────────────────
hr("② 接线断言：create_app() 注册的任务 target 是否非 None（只读，不启动任何任务）")

with TestClient(create_app()) as c:
    registry = c.app.state.task_registry
    names = sorted(registry.registered_names)
    print(f"  注册任务 {len(names)} 个")
    for n in names:
        t = registry._tasks[n]
        tgt = getattr(t, "target", None)
        print(f"     {n:<14} kind={t.kind:<11} target={getattr(tgt, '__name__', 'None')}")
    unwired = assert_targets_wired(c.app)
    verdict("T2.2/T2.4 编排域已接入工作函数（target 非 None）",
            not unwired, f"未接线: {unwired}" if unwired else "8/8 已接线")

# ── ③ 任务族与剧本端点契约（stub 版，无副作用）──────────────────────
hr("③ T2.1-T2.5 端点契约（业务 service 已换 stub —— 可安全 start/stop）")

rec = install_service_stubs()

with TestClient(create_app()) as c:
    tok = c.get("/api/csrf-token").json()["csrf_token"]
    c.cookies.set("csrf_token", tok)
    H = {"X-CSRFToken": tok}

    r = c.get("/api/tasks")
    verdict("T2.1 GET /api/tasks 可用", r.status_code == 200,
            f"{r.status_code}，{len(r.json().get('tasks', []))} 个任务")

    started = []
    for n in ["capture", "capture_full", "detection", "ml", "attack", "train"]:
        r = c.post(f"/api/tasks/{n}/start", json={"duration": 3}, headers=H)
        print(f"     start {n:<14} -> {r.status_code} {str(r.json())[:70]}")
        if r.status_code < 300:
            started.append(n)
    verdict("T2.2 启动端点可用（6/6 返回 2xx）", len(started) == 6, f"{len(started)}/6")

    rec.clear()
    r = c.post("/api/tasks/capture/start", json={"duration": 3}, headers=H)
    verdict("T2.2 重复启动返回 409（冲突语义）", r.status_code == 409, f"{r.status_code}")

    r = c.post("/api/tasks/nope/start", json={"duration": 3}, headers=H)
    verdict("T2.2 未知任务返回 404", r.status_code == 404, f"{r.status_code}")

    r = c.post("/api/tasks/capture/stop", headers=H)
    print(f"     stop capture -> {r.status_code} {str(r.json())[:70]}")
    r2 = c.post("/api/tasks/capture/stop", headers=H)
    verdict("T2.3 停止端点幂等（重复停止仍 2xx）", r2.status_code < 300, f"{r2.status_code}")

with TestClient(create_app()) as c:
    tok = c.get("/api/csrf-token").json()["csrf_token"]
    c.cookies.set("csrf_token", tok)
    H = {"X-CSRFToken": tok}
    r = c.get("/api/scenarios")
    scen = [s["name"] for s in r.json().get("scenarios", [])]
    verdict("T2.5 GET /api/scenarios 列出剧本", r.status_code == 200 and set(scen) >= {"demo", "full", "attack"},
            f"{r.status_code} {scen}")
    rec.clear()
    r = c.post("/api/scenarios/start", json={"scenario": "demo", "duration": 3}, headers=H)
    print(f"     start demo -> {r.status_code} {str(r.json())[:80]}")
    print(f"     子任务实际调用: {rec.calls}")
    verdict("T2.4 剧本冷启动成功且真的启动了子任务",
            r.status_code < 300 and (rec.has("capture.start") or rec.has("detection.load_ml")),
            f"{r.status_code}, 子任务调用={rec.calls}")
    r = c.post("/api/scenarios/start", json={"scenario": "demo", "duration": 3}, headers=H)
    verdict("T2.4 重复启动剧本返回 409", r.status_code == 409, f"{r.status_code}")
    r = c.post("/api/scenarios/start", json={"scenario": "nope", "duration": 3}, headers=H)
    verdict("T2.4 未知剧本返回 404", r.status_code == 404, f"{r.status_code}")
    r = c.post("/api/scenarios/stop", json={"scenario": "demo"}, headers=H)
    verdict("T2.4 剧本可停止", r.status_code < 300, f"{r.status_code}")

# ── ④ 各域端点 ──────────────────────────────────────────────────────
hr("④ T2.6-T2.16 各域端点实测")

with TestClient(create_app()) as c:
    tok = c.get("/api/csrf-token").json()["csrf_token"]
    c.cookies.set("csrf_token", tok)
    H = {"X-CSRFToken": tok}
    for task, path in [
        ("T2.6", "/api/traffic"), ("T2.6", "/api/traffic/history?limit=3"),
        ("T2.7", "/api/alerts"), ("T2.7", "/api/alerts/stats"),
        ("T2.8", "/api/tls/stats"), ("T2.8", "/api/tls/suspicious"),
        ("T2.11", "/api/check"), ("T2.12", "/api/settings"), ("T2.13", "/api/models"),
    ]:
        try:
            r = c.get(path)
            verdict(f"{task} GET {path}", r.status_code == 200, f"{r.status_code}")
        except Exception as exc:  # noqa: BLE001
            verdict(f"{task} GET {path}", False, f"{type(exc).__name__}: {exc}")

    r = c.get("/api/health")
    body = r.json()
    verdict("T2.10 /api/health 状态非 degraded", body.get("status") == "healthy",
            f"status={body.get('status')} db={body.get('components', {}).get('database')}")

    for p in ["/api/payload/check", "/api/payload/analyze"]:
        r = c.post(p, json={"payload": "' OR 1=1 --"}, headers=H)
        verdict(f"T2.16 POST {p}", r.status_code == 200, f"{r.status_code}")

    print("     /api/stream 在规格中:", "/api/stream" in paths, "（SSE 无限流，不实际连接）")

# ── ⑤ 页面路由（T2.17）───────────────────────────────────────────────
hr("⑤ T2.17 认证页面路由")

# 期望值来自 tests/contract/mapping.py 的 page 条目（与契约回放同一份真相源），
# 不再写死 200 —— 因为 GET /logout 是**有意**收窄为 405 的（防跨站强制登出）。
_PAGE_EXPECT = {
    "/": [200, 302],
    "/login": [200],
    "/logout": [405],
    "/change-password": [200, 302],
}

with TestClient(create_app()) as c:
    for p, expected in _PAGE_EXPECT.items():
        r = c.get(p)
        verdict(f"T2.17 页面路由 GET {p} 状态符合声明", r.status_code in expected,
                f"{r.status_code}（期望 {expected}）")
    r = c.get("/logout")
    verdict("T2.17 GET /logout 拒绝登出（防跨站强制登出）", r.status_code == 405,
            f"{r.status_code}")
    for p in ["/api/login", "/api/logout", "/api/change-password"]:
        r = c.post(p, json={})
        print(f"     POST {p:<24} -> {r.status_code}（API 版存在，但路径/方法已与旧契约不同）")
    has_tpl = (ROOT / "src" / "campus_ids" / "web" / "templates").exists()
    has_static = (ROOT / "src" / "campus_ids" / "web" / "static").exists()
    verdict("T2.17 web 具备页面渲染能力（templates/static）", has_tpl and has_static,
            f"templates={has_tpl} static={has_static}")

# ── ⑥ cleanup days 语义（T2.14）──────────────────────────────────────
hr("⑥ T2.14 POST /api/admin/cleanup —— days 是否真的生效（造对照数据，不碰真实库）")

from datetime import datetime, timedelta  # noqa: E402

from sqlalchemy import create_engine, insert, select  # noqa: E402

from campus_ids.runtime.db import alerts, metadata as _meta, traffic_history  # noqa: E402
from campus_ids.runtime.repositories import UserRepository  # noqa: E402

old_ts = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
new_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
results: dict[int, tuple[int, int, list[str]]] = {}
# ⚠️ 对照用的临时库必须建在**隔离临时目录**里，不能建在项目根。
#    2026-09-17 实测：建在项目根时，末尾的 `unlink()` 会被宿主级安全删除钩子
#    （`[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]`）拦下 → 项目根残留
#    `_probe_cleanup.db`。探针的契约是"跑完不留痕"，且残留物会污染下一轮的
#    `git status` 检查。放进 TMP（`bootstrap()` 建的临时目录）后，即使删除失败
#    也不会落到工作区。
for days in (7, 9999):
    db_file = TMP / "_probe_cleanup.db"
    db_file.unlink(missing_ok=True)
    try:
        eng = create_engine(f"sqlite:///{db_file.as_posix()}")
        _meta.create_all(eng)
        with eng.connect() as conn:
            conn.execute(insert(alerts), [
                {"time": old_ts, "level": "low", "attack_type": "probe", "message": "30天前"},
                {"time": new_ts, "level": "low", "attack_type": "probe", "message": "刚刚"},
            ])
            conn.execute(insert(traffic_history), [{"time": old_ts, "qps": 1}, {"time": new_ts, "qps": 2}])
            conn.commit()
        with eng.connect() as conn:
            da, dt = UserRepository.cleanup_old_data(conn, days=days)
            left = [row[0] for row in conn.execute(select(alerts.c.message)).all()]
        results[days] = (da, dt, left)
    finally:
        eng.dispose()
        db_file.unlink(missing_ok=True)
    print(f"     days={days:<6} 删除 (alerts={da}, traffic={dt})，剩余 {left}")

verdict("T2.14 cleanup 的 days 参数真的生效（7 与 9999 结果不同）",
        results[7] != results[9999],
        f"days=7 → {results[7]}, days=9999 → {results[9999]}")
verdict("T2.14 days=7 删「30天前」保留「刚刚」",
        "刚刚" in results[7][2] and "30天前" not in results[7][2], f"剩余 {results[7][2]}")
verdict("T2.14 days=9999 一条不删",
        results[9999][0] == 0 and results[9999][1] == 0, f"{results[9999][:2]}")

# ── ⑦ 契约 golden 可用性（T2.18）────────────────────────────────────
hr("⑦ T2.18 契约回放：golden 是否可用作期望值 / 是否有回放 harness")

import json  # noqa: E402

base = ROOT / "tests" / "contract" / "baseline"
goldens = sorted(base.glob("*.json"))

# 判定口径（2026-09-17 修正）：
# 原来的判据是"没有 4xx 录制"，但它是个**过期代理指标** —— 4xx 有两种：
#   (a) 录制时漏带 CSRF token 造成的假 400（真正的问题，必须为 0）
#   (b) 旧应用在默认配置下的**真实行为**（如 auth 关闭时 /login 蓝图未注册 → 404）
# (b) 是有价值的期望值，(a) 才是"期望值不可用"。这里只对 (a) 判红。
csrf_missing = []
real_4xx = []
for p in goldens:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        continue
    sc = d.get("status_code")
    if sc is not None and sc >= 400:
        if "CSRF token is missing" in (d.get("body") or ""):
            csrf_missing.append((p.name, sc))
        else:
            real_4xx.append((p.name, sc))
print(f"     基线 {len(goldens)} 个 json：{len(csrf_missing)} 个 CSRF 伪影、"
      f"{len(real_4xx)} 个旧应用真实拒绝响应")
for n, sc in real_4xx[:12]:
    print(f"       (真实) {n:<32} status_code={sc}")
for n, sc in csrf_missing[:12]:
    print(f"       (伪影) {n:<32} status_code={sc}")
verdict("T2.18 基线无 CSRF 伪影（期望值可用）", not csrf_missing,
        f"{len(csrf_missing)} 个是 'CSRF token is missing' 的假 400（旧基线为 20）")
print(f"     （旧应用真实 4xx 保留为期望值：{len(real_4xx)} 个 —— 它们描述的是旧行为，不是录制缺陷）")

replay_hits = []
for p in (ROOT / "tests").glob("*.py"):
    text = p.read_text(encoding="utf-8", errors="ignore")
    if "contract" in text or "baseline" in text:
        replay_hits.append(p.name)
verdict("T2.18 存在契约回放 harness（测试读取 baseline）", bool(replay_hits),
        f"命中: {replay_hits}" if replay_hits else f"{len(goldens)} 个 golden 录了从不回放")

# 真正跑一遍回放（只读；写端点只做规格存在性断言，不会真的删库/训练）
try:
    import sys as _sys

    _sys.path.insert(0, str(ROOT))
    from tests.contract.replay import run_replay  # noqa: E402

    _failures, _lines = run_replay()
    verdict("T2.18 回放零未解释差异（退出码判据）", _failures == 0,
            f"{_failures} 项未解释" if _failures else "全部差异均已解释")
    for _ln in _lines:
        if _ln.strip().startswith("旧规格路径") or "未解释丢失" in _ln:
            print(f"     {_ln.strip()}")
except Exception as _exc:  # noqa: BLE001
    verdict("T2.18 回放可执行", False, f"{type(_exc).__name__}: {_exc}")

# ── ⑧ CSRF / 限流 / 安全头 ─────────────────────────────────────────
hr("⑧ CSRF / 限流 / 安全头")

from campus_ids.web.security import limiter  # noqa: E402

with TestClient(create_app()) as c:
    r = c.post("/api/tasks/capture/start", json={"duration": 3})
    verdict("无 CSRF 的写操作被拒", r.status_code == 403, f"{r.status_code}")

    limiter.reset()
    tok = c.get("/api/csrf-token").json()["csrf_token"]
    c.cookies.set("csrf_token", tok)
    codes: dict[int, int] = {}
    for _ in range(50):
        s = c.post("/api/tasks/capture/start", json={"duration": 3},
                   headers={"X-CSRFToken": tok}).status_code
        codes[s] = codes.get(s, 0) + 1
    print(f"     写端点 50 连发 -> {codes}")
    verdict("写端点限流生效（出现 429）", 429 in codes, f"{codes}")

    h = c.get("/api/health").headers
    for name in ("strict-transport-security", "permissions-policy",
                 "cross-origin-opener-policy", "content-security-policy",
                 "x-content-type-options", "x-frame-options"):
        print(f"       {name:<32} {(h.get(name) or '<absent>')[:60]}")

raise SystemExit(summary())
