# -*- coding: utf-8 -*-
"""新骨架（web_new）验收探针：启动 / 安全头 / 限流 / CSRF / 契约规模。

用途：T1.7、T1.11、T1.12 的**可复现证据**。对应 2026-09-16 GitHub 调研报告
`docs/GitHub成熟项目调研-2026-09-16.md` §2.1 / §2.3 / §6 的实测结论。

用法（不触发 lifespan，因此**不建库、不写 DB**）：
    C:/Users/18551/anaconda3/python.exe scripts/probe_web_new.py

预期（当前实现下会暴露的问题，见调研报告 §6）：
    - security headers 缺 strict-transport-security / permissions-policy /
      cross-origin-opener-policy
    - rate-limit probe 全部 200、无 429（限流未生效）
    - POST 写端点无 CSRF -> 403（这一项是正确的）
"""
import collections
import sys

sys.path.insert(0, "src")

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
