# -*- coding: utf-8 -*-
"""新骨架（web_new）验收探针：契约规模 / 安全头 / 限流 / CSRF。

用法：
    C:/Users/18551/anaconda3/python.exe scripts/probe_web_new.py

安全：不进入 `with TestClient(...)`，因此 lifespan 不执行 → 不建库、不写 DB。
      另外 `_probe_safety.bootstrap()` 把 data_dir 指向临时目录兜底。
      只发 `GET` 与一个**无 CSRF 的** POST（必被 403 拦下，不会真的启动任务）。

📌 2026-09-17 更新：本脚本原先的"预期"文档写的是
   「缺 HSTS / 限流全部 200」—— 这两条**已修复**，旧文案会误导。
   现在不再预测结论，一律看输出。T2 及以后的验收请以
   `scripts/probe_t2_acceptance.py` 为准（它带 `verdict()` 自判定）。
"""
import collections
import sys

from _probe_safety import bootstrap, guard_no_real_training

TMP = bootstrap()                    # 必须在 import 项目模块之前
guard_no_real_training()             # 兜底哨兵
print("data_dir 已隔离到:", TMP)

from fastapi.testclient import TestClient  # noqa: E402

from campus_ids.web_new.app import create_app  # noqa: E402

app = create_app()
spec = app.openapi()
print("openapi version :", spec.get("openapi"))
print("paths           :", len(spec["paths"]))
print("paths list      :", sorted(spec["paths"]))

# TestClient without `with` -> lifespan does NOT run (no DB side effects)
client = TestClient(app)

r = client.get("/api/health")
print("\nGET /api/health ->", r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:120])
print("security headers:")
for k in ("content-security-policy", "x-content-type-options", "x-frame-options",
          "strict-transport-security", "referrer-policy", "permissions-policy",
          "cross-origin-opener-policy", "cross-origin-resource-policy", "server"):
    print("   %-28s %s" % (k, r.headers.get(k, "<absent>")))

# Rate limit probe: 80 rapid requests against a cheap endpoint
codes = collections.Counter()
for _ in range(80):
    codes[client.get("/api/health").status_code] += 1
print("\nrate-limit probe (80x GET /api/health):", dict(codes))

# CSRF-token endpoint + a write endpoint
rt = client.get("/api/csrf-token")
print("GET /api/csrf-token ->", rt.status_code, (rt.text or "")[:160])
print("   set-cookie:", rt.headers.get("set-cookie", "<absent>")[:120])

pw = client.post("/api/tasks/capture/start")
print("POST /api/tasks/capture/start (no CSRF) ->", pw.status_code, (pw.text or "")[:160])
