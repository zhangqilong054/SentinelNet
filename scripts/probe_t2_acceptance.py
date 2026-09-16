"""T2 阶段验收探针 —— 一条命令复现全部结论。

安全约束（务必保持）：
- **绝不调用**会改写产物或数据的端点：`/api/models/train`（触发真实训练，覆盖
  model.pkl / evaluation_report.txt / confusion_matrix.png）、`/api/admin/export`
  （覆盖 traffic_stats.csv）。
- `cleanup_old_data` 的行为验证**只在 DB 副本上**进行，真实库以只读方式打开。
- 不触发 `lifespan` 之外的写操作；不建表、不删数据。

用法：
    /c/Users/18551/anaconda3/python.exe scripts/probe_t2_acceptance.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("CAMPUS_IDS_DEBUG", "1")  # 允许默认 SECRET_KEY（仅本机开发）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def hr(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


# ── ① 端点清单（新旧对比）────────────────────────────────────────────
hr("① 端点清单：旧 Flask 34 个业务端点 → 新 FastAPI")

from campus_ids.web_new.app import create_app  # noqa: E402

app = create_app()
spec = app.openapi()
new_paths = {p: sorted(m.keys()) for p, m in spec["paths"].items()}
print(f"  新规格: openapi={spec['openapi']}  路径数={len(new_paths)}")
print("  注意：FastAPI 0.141 的 app.routes 里 include_router 是 _IncludedRouter 占位，")
print("        遍历 app.routes 会数到 0 个 /api 路由 —— 必须用 app.openapi()['paths']。")
for p in sorted(new_paths):
    print(f"     {p:<34} {','.join(new_paths[p])}")

# ── ② 任务族与剧本（T2.1-T2.5）真实接线 ──────────────────────────────
hr("② T2.1-T2.5 任务族与剧本 —— 真实接线（不注入假 target）")

from fastapi.testclient import TestClient  # noqa: E402


def new_client() -> TestClient:
    return TestClient(create_app())


with new_client() as c:
    tok = c.get("/api/csrf-token").json()["csrf_token"]
    c.cookies.set("csrf_token", tok)
    H = {"X-CSRFToken": tok}

    r = c.get("/api/tasks")
    print(f"  T2.1 GET /api/tasks -> {r.status_code}，注册任务 {len(r.json()['tasks'])} 个:")
    for t in r.json()["tasks"]:
        print(f"       {t['name']:<14} {t['status']:<8} kind={t['kind']:<10} target 未接线")

    print("  T2.2 POST /api/tasks/{name}/start:")
    for n in ["capture", "capture_full", "detection", "ml", "train", "attack", "auto", "demo"]:
        r = c.post(f"/api/tasks/{n}/start", json={"duration": 5}, headers=H)
        print(f"       {n:<14} -> {r.status_code}  {r.json().get('detail', '')}")

    print("  T2.3 POST /api/tasks/{name}/stop:")
    for n in ["capture", "nope"]:
        r = c.post(f"/api/tasks/{n}/stop", headers=H)
        print(f"       {n:<14} -> {r.status_code}  {r.json()}")

    print("  T2.4/T2.5 剧本:")
    r = c.get("/api/scenarios")
    print(f"       GET /api/scenarios -> {r.status_code} "
          f"{[s['name'] for s in r.json()['scenarios']]}")
    for s in ["demo", "full", "attack", "nope"]:
        r = c.post("/api/scenarios/start", json={"scenario": s, "duration": 5}, headers=H)
        print(f"       start {s:<8} -> {r.status_code}  {r.json().get('detail', '')}")

# ── ③ 各域端点实测（T2.6-T2.17）─────────────────────────────────────
hr("③ T2.6-T2.17 各域端点实测")

with new_client() as c:
    tok = c.get("/api/csrf-token").json()["csrf_token"]
    c.cookies.set("csrf_token", tok)
    H = {"X-CSRFToken": tok}

    reads = [
        ("T2.6", "/api/traffic"), ("T2.6", "/api/traffic/history?limit=3"),
        ("T2.7", "/api/alerts"), ("T2.8", "/api/tls/stats"),
        ("T2.8", "/api/tls/suspicious"), ("T2.10", "/api/health"),
        ("T2.11", "/api/check"), ("T2.12", "/api/settings"), ("T2.13", "/api/models"),
    ]
    for task, path in reads:
        r = c.get(path)
        print(f"  {task:<6} GET {path:<30} -> {r.status_code}  {r.text[:90]}")

    # SSE 是无限流，TestClient 会一直读下去 → 只查规格不实际连接
    print(f"  T2.9  GET /api/stream                        -> "
          f"{'在规格中' if '/api/stream' in new_paths else '缺失'}（SSE 为无限流，不实际连接）")
    print(f"  T2.17 GET /  （页面路由）                    -> {c.get('/').status_code}")
    for p in ["/login", "/logout", "/change-password"]:
        print(f"  T2.17 GET {p:<24} -> {c.get(p).status_code}")
    for p in ["/api/login", "/api/logout", "/api/change-password"]:
        print(f"  T2.17 POST {p:<23} -> {c.post(p, json={}).status_code}")

    print("  T2.16 载荷送检：")
    for p in ["/api/payload/check", "/api/payload/analyze"]:
        r = c.post(p, json={"payload": "' OR 1=1 --"}, headers=H)
        print(f"       POST {p:<26} -> {r.status_code}  {r.text[:90]}")

# ── ④ cleanup_old_data 语义（只在副本上）────────────────────────────
hr("④ T2.14 /api/admin/cleanup 底层语义 —— 在 DB 副本上验证，不碰真实库")

from sqlalchemy import create_engine, func, select  # noqa: E402

from campus_ids.runtime.db import alerts, traffic_history  # noqa: E402
from campus_ids.runtime.repositories import UserRepository  # noqa: E402

real_db = ROOT / "sentinelnet.db"
for days in (7, 9999, 36500):
    tmp = Path(tempfile.mkdtemp()) / "copy.db"
    shutil.copy2(real_db, tmp)
    eng = create_engine(f"sqlite:///{tmp.as_posix()}")
    with eng.connect() as conn:
        a0 = conn.execute(select(func.count()).select_from(alerts)).scalar()
        t0 = conn.execute(select(func.count()).select_from(traffic_history)).scalar()
        deleted = UserRepository.cleanup_old_data(conn, days=days)
        a1 = conn.execute(select(func.count()).select_from(alerts)).scalar()
        t1 = conn.execute(select(func.count()).select_from(traffic_history)).scalar()
    print(f"  days={days:<6} 删除 {deleted:<5} 条，剩余 alerts={a1}/{a0} traffic={t1}/{t0}")
    eng.dispose()
print("  → 三个 days 值删除数完全相同 = days 参数被完全忽略")
print("  → 旧实现 cutoff = now - timedelta(days=days)；新实现 cutoff = utcnow()")

# ── ⑤ SQLAlchemy 2.0 raw SQL 写法 ──────────────────────────────────
hr("⑤ /api/health 的 DB 探针写法（SQLAlchemy 2.0 Core 要求 text()）")

from sqlalchemy import text as sql_text  # noqa: E402

eng = create_engine(f"sqlite:///{real_db.as_posix()}")
with eng.connect() as conn:
    for label, stmt in [("裸字符串 \"SELECT 1\"", "SELECT 1"), ("text(\"SELECT 1\")", sql_text("SELECT 1"))]:
        try:
            conn.execute(stmt)
            print(f"  {label:<26} -> 通过")
        except Exception as exc:  # noqa: BLE001
            print(f"  {label:<26} -> {type(exc).__name__}: {exc}")
eng.dispose()
print("  → web_new/api/system.py:61 用的是裸字符串 → /api/health 永远 degraded")

# ── ⑥ CSRF / 限流 / 安全头 ──────────────────────────────────────────
hr("⑥ CSRF / 限流 / 安全头")

with new_client() as c:
    r = c.post("/api/tasks/capture/start", json={"duration": 5})
    print(f"  无 CSRF 写操作            -> {r.status_code} {r.json()}")

    codes: dict[int, int] = {}
    for _ in range(50):
        s = c.post("/api/tasks/capture/start", json={"duration": 5}).status_code
        codes[s] = codes.get(s, 0) + 1
    print(f"  写端点（无 token）x50     -> {codes}")

    tok = c.get("/api/csrf-token").json()["csrf_token"]
    c.cookies.set("csrf_token", tok)
    codes = {}
    for _ in range(50):
        s = c.post("/api/tasks/capture/start", json={"duration": 5},
                   headers={"X-CSRFToken": tok}).status_code
        codes[s] = codes.get(s, 0) + 1
    print(f"  写端点（带 token）x50     -> {codes}   ← 期望出现 429")

    codes = {}
    for _ in range(80):
        s = c.get("/api/tasks").status_code
        codes[s] = codes.get(s, 0) + 1
    print(f"  GET /api/tasks x80        -> {codes}   ← default_limits 60/min 未生效")

    h = c.get("/api/health").headers
    for name in ["strict-transport-security", "permissions-policy",
                 "cross-origin-opener-policy", "content-security-policy",
                 "x-content-type-options", "x-frame-options"]:
        print(f"     {name:<32} {h.get(name, '<absent>')[:64]}")

hr("完成 —— 以上全部结论均可用本脚本复现")
