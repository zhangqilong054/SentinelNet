# -*- coding: utf-8 -*-
"""T1 阶段（新骨架）验收探针 —— 一次跑出结论摘要里全部实测证据。

对应 `docs/T1阶段检查报告-2026-09-16.md`。只读：不建库、不写 DB、不改任何文件。

    C:/Users/18551/anaconda3/python.exe scripts/probe_t1_acceptance.py

覆盖：
  A. import 副作用（T1.1/T1.2）            —— 新建文件 / 线程数 / 有无模块级 app
  B. CSRF 签名强度（T1.11 ②）              —— 用公开默认密钥伪造 token
  C. 会话认证可达性（T1.11 ①）             —— auth 开启 + 合法 session cookie
  D. 写端点限流（T1.7）                    —— 连发 40 次应出现 429
  E. 安全响应头（T1.11 ③）                 —— BALANCED 预设逐项
  F. Settings 实例隔离 + 旧键名兼容（T1.3）
  G. 多标签页 CSRF 并发（T1.7 缺口）+ token 是否有过期/会话绑定
  H. 空壳端点对非法输入的响应（T2 风险提示）
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import threading

sys.path.insert(0, "src")
ROOT = os.getcwd()
PUBLIC_DEFAULT_SECRET = "change-me-in-production"  # settings.py 的默认值

print("=" * 74)
print("A. import 副作用（T1.1 / T1.2）")
before = set(os.listdir(ROOT))
threads_before = threading.active_count()
import campus_ids.web_new.app  # noqa: E402

after = set(os.listdir(ROOT))
import campus_ids.web_new.app as _mod  # noqa: E402,F811

print("  新建文件      :", sorted(after - before) or "无")
print("  线程数        :", threads_before, "->", threading.active_count(),
      "（+1 来自 security.py 模块级 Limiter 实例化）")
print("  模块级 app 对象:", hasattr(_mod, "app"), "（False 才符合 T1.2）")

from fastapi.testclient import TestClient  # noqa: E402
from campus_ids.runtime.settings import Settings, get_settings, reset_settings  # noqa: E402
from campus_ids.web_new.app import create_app  # noqa: E402

print("=" * 74)
print("B. CSRF HMAC 签名强度（T1.11 ②）")
reset_settings()
client = TestClient(create_app())
print("  当前 secret_key :", repr(get_settings().secret_key))
nonce = "a" * 64
forged = nonce + ":" + hmac.new(PUBLIC_DEFAULT_SECRET.encode(), nonce.encode(), hashlib.sha256).hexdigest()
client.cookies.set("csrf_token", forged)
r = client.post("/api/tasks/capture/start", json={"duration": 5}, headers={"X-CSRFToken": forged})
print("  伪造签名 POST   :", r.status_code, "（200 = 公开默认密钥下可伪造）")

print("=" * 74)
print("C. 会话认证可达性（T1.11 ①）")
os.environ["CAMPUS_IDS_AUTH_ENABLED"] = "1"
os.environ["CAMPUS_IDS_API_TOKEN"] = "real-token"
reset_settings()
c2 = TestClient(create_app())
from itsdangerous import TimestampSigner  # noqa: E402

blob = base64.b64encode(json.dumps({"user": "admin", "authenticated": True}).encode())
c2.cookies.set("campus_ids_session", TimestampSigner(PUBLIC_DEFAULT_SECRET).sign(blob).decode())
print("  auth=1 + session cookie ->", c2.get("/api/alerts").status_code, "（401 = 会话认证不生效）")
print("  auth=1 + Bearer         ->",
      c2.get("/api/alerts", headers={"Authorization": "Bearer real-token"}).status_code)
os.environ.pop("CAMPUS_IDS_AUTH_ENABLED")
os.environ.pop("CAMPUS_IDS_API_TOKEN")
reset_settings()

print("=" * 74)
print("D. 写端点限流（T1.7）")
c3 = TestClient(create_app())
from campus_ids.web_new.security import limiter  # noqa: E402

limiter.reset()
tok = c3.get("/api/csrf-token").json()["csrf_token"]
c3.cookies.set("csrf_token", tok)
codes: dict[int, int] = {}
for _ in range(40):
    s = c3.post("/api/tasks/capture/start", json={"duration": 5}, headers={"X-CSRFToken": tok}).status_code
    codes[s] = codes.get(s, 0) + 1
print("  40 连发状态分布:", codes, "（出现 429 = 限流生效）")

print("=" * 74)
print("E. 安全响应头（T1.11 ③）")
h = c3.get("/api/health").headers
for k in ("content-security-policy", "strict-transport-security", "permissions-policy",
          "cross-origin-opener-policy", "cross-origin-resource-policy", "referrer-policy",
          "x-content-type-options", "x-frame-options", "x-xss-protection"):
    print("  %-28s %s" % (k, (h.get(k) or "<absent>")[:72]))

print("=" * 74)
print("F. Settings 实例隔离 + 旧键名兼容（T1.3）")
s1, s2 = Settings(), Settings()
s1.set_override("ddos_threshold", 999)
print("  s1/s2 覆盖隔离  :", s1.get("ddos_threshold"), "/", s2.get("ddos_threshold"),
      "（999 / 500 = 正确隔离）")
os.environ["CAMPUS_IDS_BF_WINDOW"] = "999"
print("  旧键名 BF_WINDOW:", Settings().brute_force_window, "（60 = 旧键名被静默忽略）")
os.environ.pop("CAMPUS_IDS_BF_WINDOW")

print("=" * 74)
print("G. 多标签页 CSRF 并发 + token 生命周期（T1.7 缺口）")
limiter.reset()
c4 = TestClient(create_app())
t_a = c4.get("/api/csrf-token").json()["csrf_token"]
t_b = c4.get("/api/csrf-token").json()["csrf_token"]      # 第二个标签页覆盖 cookie
r = c4.post("/api/tasks/capture/start", json={"duration": 5}, headers={"X-CSRFToken": t_a})
print("  标签页A 用旧 token ->", r.status_code, r.json().get("detail", ""), "（403 = 并发缺陷真实存在）")
print("  同一 token 连用 3 次:",
      [c4.post("/api/tasks/capture/start", json={"duration": 5},
               headers={"X-CSRFToken": t_b}).status_code for _ in range(3)])
c5 = TestClient(create_app())
c5.cookies.set("csrf_token", t_b)
print("  换新会话复用旧 token ->",
      c5.post("/api/tasks/capture/start", json={"duration": 5}, headers={"X-CSRFToken": t_b}).status_code,
      "（200 = 无会话绑定、无过期）")

print("=" * 74)
print("H. 空壳端点对非法输入（T2 风险提示）")
c6 = TestClient(create_app())
tok6 = c6.get("/api/csrf-token").json()["csrf_token"]
c6.cookies.set("csrf_token", tok6)
for name in ("capture", "不存在的任务", "__bad__"):
    rr = c6.post(f"/api/tasks/{name}/start", json={"duration": 5}, headers={"X-CSRFToken": tok6})
    print("  start %-14s -> %s %s" % (name, rr.status_code, rr.json().get("message", "")))
print("  GET /api/settings ->", c6.get("/api/settings").json(),
      "（空壳默认值，auth_enabled=False 与 Settings 默认 True 不一致）")
