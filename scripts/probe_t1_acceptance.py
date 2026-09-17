# -*- coding: utf-8 -*-
"""T1 阶段（新骨架）验收探针 —— 一次跑出结论摘要里全部实测证据。

    C:/Users/18551/anaconda3/python.exe scripts/probe_t1_acceptance.py

⚠️ **安全前提**：本脚本会反复 `POST /api/tasks/capture/start`（限流探针要连发 40 次）。
T1 时期任务未接线（一律 503）所以无害；**T2 接线完成后同样的调用会真的启动抓包**。
因此脚本开头必须先用 `_probe_safety` 做两件事：
  ① `bootstrap()` 把 data_dir 指向临时目录（在 import 项目模块之前！）
  ② `install_service_stubs()` 把业务 service 换成 stub
  ③ `guard_no_real_training()` 给 `train()` 加哨兵
这三步之后本脚本才是只读的。

覆盖：
  A. import 副作用（T1.1/T1.2）            —— 新建文件 / 线程数 / 有无模块级 app
  B. CSRF 签名强度（T1.11 ②）              —— 用公开默认密钥伪造 token
  C. 会话认证可达性（T1.11 ①）             —— auth 开启 + 合法 session cookie
  D. 写端点限流（T1.7）                    —— 连发 40 次应出现 429
  E. 安全响应头（T1.11 ③）                 —— BALANCED 预设逐项
  F. Settings 实例隔离 + 旧键名兼容（T1.3）
  G. 多标签页 CSRF 并发（T1.7 缺口）+ token 是否有过期/会话绑定
  H. 非法输入的任务名响应
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import threading

from _probe_safety import bootstrap, guard_no_real_training, install_service_stubs

TMP = bootstrap()                    # ← 必须在 import 项目模块之前
guard_no_real_training()             # 兜底哨兵：真有 train() 调用则报错而非覆盖产物
print("data_dir 已隔离到:", TMP)

ROOT = os.getcwd()
PUBLIC_DEFAULT_SECRET = "change-me-in-production"  # settings.py 的默认值（探针要验证它不安全）

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

# 业务 service 换 stub（仍然走真实 create_app()，接线事实不动）
REC = install_service_stubs()


def new_client() -> "TestClient":
    """建 TestClient **并进入上下文**，让 lifespan 真正执行。

    ⚠️ 不进入上下文时 lifespan 不跑 → `app.state.task_registry` 未初始化 →
    所有 `/api/tasks/*` 返回 503「任务注册表未初始化」。
    这会让限流/CSRF 段落测到 503 而不是真实语义（旧版探针就踩了这个坑）。
    """
    c = TestClient(create_app())
    c.__enter__()
    return c


print("=" * 74)
print("B. CSRF HMAC 签名强度（T1.11 ②）")
reset_settings()
client = new_client()
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
c2 = new_client()
from itsdangerous import TimestampSigner  # noqa: E402

blob = base64.b64encode(json.dumps({"user": "admin", "authenticated": True}).encode())
c2.cookies.set("campus_ids_session", TimestampSigner(PUBLIC_DEFAULT_SECRET).sign(blob).decode())
sess_code = c2.get("/api/alerts").status_code
bearer_code = c2.get("/api/alerts", headers={"Authorization": "Bearer real-token"}).status_code
print("  auth=1 + 合法 session cookie ->", sess_code)
print("  auth=1 + Bearer              ->", bearer_code)
print("  判定:", "✅ 会话认证可达" if sess_code == 200 else
      f"🔴 会话认证不可达（{sess_code}）—— 回退分支仍是死代码")
os.environ.pop("CAMPUS_IDS_AUTH_ENABLED")
os.environ.pop("CAMPUS_IDS_API_TOKEN")
reset_settings()

print("=" * 74)
print("D. 写端点限流（T1.7）")
c3 = new_client()
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
c4 = new_client()
t_a = c4.get("/api/csrf-token").json()["csrf_token"]
t_b = c4.get("/api/csrf-token").json()["csrf_token"]      # 第二个标签页覆盖 cookie
r = c4.post("/api/tasks/capture/start", json={"duration": 5}, headers={"X-CSRFToken": t_a})
print("  标签页A 用旧 token ->", r.status_code, r.json().get("detail", ""), "（403 = 并发缺陷真实存在）")
print("  同一 token 连用 3 次:",
      [c4.post("/api/tasks/capture/start", json={"duration": 5},
               headers={"X-CSRFToken": t_b}).status_code for _ in range(3)])
c5 = new_client()
c5.cookies.set("csrf_token", t_b)
print("  换新会话复用旧 token ->",
      c5.post("/api/tasks/capture/start", json={"duration": 5}, headers={"X-CSRFToken": t_b}).status_code,
      "（200 = 无会话绑定、无过期）")

print("=" * 74)
print("H. 任务名非法/未知输入（T2 接线后的行为）")
c6 = new_client()
tok6 = c6.get("/api/csrf-token").json()["csrf_token"]
c6.cookies.set("csrf_token", tok6)
for name in ("capture", "不存在的任务", "__bad__"):
    rr = c6.post(f"/api/tasks/{name}/start", json={"duration": 5}, headers={"X-CSRFToken": tok6})
    print("  start %-14s -> %s %s" % (name, rr.status_code, rr.json().get("detail", "") or rr.json().get("message", "")))
print("  （服务已 stub，start 不会有真实副作用）")
print("  GET /api/settings ->", str(c6.get("/api/settings").json())[:120])
