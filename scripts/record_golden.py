# -*- coding: utf-8 -*-
"""T0.3 / T2.18 端点响应样本录制（golden files）—— **带安全底座的重写版**。

产物：`tests/contract/baseline/*.json`（34 个业务端点 + 5 个页面路由 + 2 个 SSE 占位）

## 与旧版的区别（为什么必须重录）

旧版录制时**没带 CSRF token**，所以全部 21 个写端点都被拦截成

    400 / 403 CSRF token missing/invalid

→ 41 个 golden 里大量是这种错误响应，期望值本身不可用（报告的 P0 结论）。

补上 token 后同一条脚本立刻变**破坏性**（真抓包 / 真训练 / 真按 days=7 删数据），
因此本版把 `_recorder_safety` 的三道保险列为**不可绕过的前置**：

  1. `bootstrap()`   —— data_dir 隔离到临时目录（须在 import 项目模块之前）
  2. `neutralize()`  —— 抓包/检测/演练/训练入口换成记录型 stub
  3. `assert_products_untouched()` —— 录制前后对 6 个真实产物做 md5 比对，失守即抛错

## 保真度说明（写进 `_meta.json`，不藏）

被 stub 的入口在 golden 里体现的是"成功分支"而非真实副作用
（例如 `/api/model/train` 返回 200 但没有真的训练）。
这是安全的代价，属于**已知且已声明**的偏差；stub 清单随 golden 一起落盘。

运行：

    python scripts/record_golden.py            # 重录并覆盖 tests/contract/baseline/
    python scripts/record_golden.py --check    # 只校验现有 golden 是否含 CSRF 缺失的 400
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ① 第一件事：隔离 data_dir。必须在 import campus_ids 之前。
from _recorder_safety import (  # noqa: E402
    ROOT,
    assert_products_untouched,
    bootstrap,
    get_csrf_token,
    neutralized_report,
    neutralize,
    snapshot_products,
)

TMP_DIR = bootstrap()

# ② 之后才能 import 项目模块
import campus_ids.logging_config as lc  # noqa: E402
lc.setup_logging()
from campus_ids.web.app import create_app  # noqa: E402

OUT_DIR = ROOT / "tests" / "contract" / "baseline"


# ── 端点清单（34 业务 + 5 页面）────────────────────────────────────

ENTRIES: list[dict] = [
    # ── 页面路由 ──────────────────────────────────────────────
    {"name": "page_index", "method": "GET", "url": "/", "kind": "page"},
    {"name": "page_apidocs", "method": "GET", "url": "/apidocs/", "kind": "page"},
    {"name": "page_login", "method": "GET", "url": "/login", "kind": "page"},
    {"name": "page_change_password", "method": "GET", "url": "/change-password", "kind": "page"},
    {"name": "page_logout", "method": "GET", "url": "/logout", "kind": "page"},

    # ── 公开端点 ──────────────────────────────────────────────
    {"name": "health", "method": "GET", "url": "/api/health"},
    {"name": "check", "method": "GET", "url": "/api/check"},

    # ── 观测类 ────────────────────────────────────────────────
    {"name": "traffic", "method": "GET", "url": "/api/traffic"},
    {"name": "traffic_history", "method": "GET", "url": "/api/traffic/history"},
    {"name": "alerts", "method": "GET", "url": "/api/alerts"},
    {"name": "tls_stats", "method": "GET", "url": "/api/tls/stats"},
    {"name": "tls_suspicious", "method": "GET", "url": "/api/tls/suspicious"},

    # ── 配置 ──────────────────────────────────────────────────
    {"name": "config_get", "method": "GET", "url": "/api/config"},
    {"name": "config_post", "method": "POST", "url": "/api/config",
     "json": {"brute_force_threshold": 5}},

    # ── 抓包 ──────────────────────────────────────────────────
    {"name": "capture_status", "method": "GET", "url": "/api/capture/status"},
    {"name": "capture_start", "method": "POST", "url": "/api/capture/start",
     "json": {"interface": "eth0", "duration": 60}},
    {"name": "capture_enhanced_status", "method": "GET", "url": "/api/capture/enhanced-status"},
    {"name": "capture_start_enhanced", "method": "POST", "url": "/api/capture/start-enhanced",
     "json": {"interface": "eth0", "duration": 60}},
    {"name": "capture_stop_enhanced", "method": "POST", "url": "/api/capture/stop-enhanced"},
    {"name": "capture_stop", "method": "POST", "url": "/api/capture/stop"},

    # ── 检测 ──────────────────────────────────────────────────
    {"name": "detector_status", "method": "GET", "url": "/api/detector/status"},
    {"name": "detector_start", "method": "POST", "url": "/api/detector/start"},
    {"name": "detector_stop", "method": "POST", "url": "/api/detector/stop"},

    # ── 攻击模拟 ──────────────────────────────────────────────
    # ⚠️ 入参键是 `type`（不是 `attack_type`）—— 旧实现 `data.get('type', 'all')`
    {"name": "attack_status", "method": "GET", "url": "/api/attack/status"},
    {"name": "attack_start", "method": "POST", "url": "/api/attack/start",
     "json": {"type": "port_scan", "duration": 10}},
    {"name": "attack_stop", "method": "POST", "url": "/api/attack/stop"},

    # ── 一键流程 / 演示 ────────────────────────────────────────
    {"name": "auto_status", "method": "GET", "url": "/api/auto/status"},
    {"name": "auto_start", "method": "POST", "url": "/api/auto/start",
     "json": {"duration": 60}},
    # ⚠️ 这两个端点文档写 `body: required: false`，但实现里 `request.get_json()`
    #    在缺 Content-Type 时抛 415 —— **文档与行为不一致**（已记入 _meta）。
    #    录制按其**实际可用**用法（带 JSON body）取样。
    {"name": "demo_start", "method": "POST", "url": "/api/demo/start", "json": {},
     "note": "无 Content-Type 时该端点返回 415，与文档 'required: false' 不符"},

    # ── ML 双引擎 ─────────────────────────────────────────────
    {"name": "dual_stats", "method": "GET", "url": "/api/dual/stats"},
    {"name": "dual_load", "method": "POST", "url": "/api/dual/load", "json": {},
     "note": ("无 Content-Type 时该端点返回 415，与文档 'required: false' 不符。"
              "另：本样本的 400 不是干净失败 —— data_dir 内没有 models/best.json 时，"
              "train.py:550 的 `return load_model()`（无参自调用）会无限递归，"
              "RecursionError 被 dual_detector.load_model 的 except 吞掉。"
              "项目根目录存在 models/best.json 所以生产不触发，属旧应用潜伏缺陷。")},
    {"name": "dual_stop", "method": "POST", "url": "/api/dual/stop"},

    # ── 模型管理 ──────────────────────────────────────────────
    {"name": "model_list", "method": "GET", "url": "/api/model/list"},
    {"name": "model_train", "method": "POST", "url": "/api/model/train",
     "json": {"data_source": "synthetic_demo"}, "stubbed": True},
    {"name": "model_train_status", "method": "GET", "url": "/api/model/train-status"},

    # ── 运维 ──────────────────────────────────────────────────
    {"name": "cleanup", "method": "POST", "url": "/api/cleanup", "json": {"days": 7}},
    {"name": "save", "method": "POST", "url": "/api/save"},

    # ── 载荷送检 ──────────────────────────────────────────────
    {"name": "payload_check", "method": "POST", "url": "/api/payload/check",
     "json": {"payload": "GET /index.php?id=1' OR '1'='1"}},

    # ── 认证（旧应用的表单登录；默认配置下 auth_bp 未注册 → 404 是真实行为）──
    {"name": "login_form", "method": "POST", "url": "/login",
     "form": {"username": "admin", "password": "admin"}, "kind": "page"},
]

SSE_PLACEHOLDERS = ("stream_alerts", "stream_traffic")


# ── 录制 ──────────────────────────────────────────────────────────


def _save(name: str, resp, entry: dict) -> dict:
    payload = {
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "request": {
            "method": entry["method"],
            "url": entry["url"],
            "json": entry.get("json"),
            "form": entry.get("form"),
        },
    }
    try:
        payload["json"] = resp.get_json()
    except Exception:
        payload["json"] = None
    raw = resp.get_data(as_text=True)
    if payload["json"] is None and raw:
        payload["body"] = raw[:2000]
    if "json" not in payload:
        payload["json"] = None
    if entry.get("note"):
        payload["note"] = entry["note"]

    (OUT_DIR / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def record() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    before = snapshot_products()
    # `start_attack_sim` / `stop_attack_sim` 的成功返回值是 **None**
    # （失败才返回 `(payload, status)` 二元组，调用方 `if err: return jsonify(err[0]), err[1]`）。
    # stub 必须复刻这个契约，否则端点在 `err[0]` 处 TypeError → 500。
    neutralize({"start_attack_sim": None, "stop_attack_sim": None})
    client = TestClient(create_app())
    csrf = get_csrf_token(client)

    print(f"安全底座：data_dir={TMP_DIR}")
    print(f"CSRF token 已获取（前 12 位）：{csrf[:12]}…")
    print(f"已中性化的绑定（{len(neutralize.calls)} 次调用记录器就绪）：")
    print(neutralized_report())
    print()

    failures = 0
    csrf_missing = 0
    results = []

    for entry in ENTRIES:
        headers = {"X-CSRFToken": csrf}
        kwargs: dict = {"headers": headers}
        if entry.get("json") is not None:
            kwargs["json"] = entry["json"]
        if entry.get("form") is not None:
            kwargs["data"] = entry["form"]

        resp = client.get(entry["url"], **kwargs) if entry["method"] == "GET" \
            else client.post(entry["url"], **kwargs)

        payload = _save(entry["name"], resp, entry)
        body = (payload.get("body") or "")
        missing = "CSRF token is missing" in body
        if missing:
            csrf_missing += 1
        results.append((entry["name"], resp.status_code, missing))
        print(f"  {resp.status_code:>3}  {entry['name']:<24} {entry['method']:<4} {entry['url']}"
              + ("   ⚠️ CSRF 缺失" if missing else ""))

    # SSE 端点：test_client 会阻塞，只落占位（含真实路径，便于回放时对账）
    for name in SSE_PLACEHOLDERS:
        path = "/api/stream/alerts" if name.endswith("alerts") else "/api/stream/traffic"
        (OUT_DIR / f"{name}.json").write_text(
            json.dumps({
                "status_code": 200,
                "headers": {"Content-Type": "text/event-stream"},
                "json": None,
                "request": {"method": "GET", "url": path},
                "note": "SSE 端点：只记录连接契约，不录事件流（test_client 下会阻塞）",
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  200  {name:<24} GET  {path}  (SSE 占位)")

    # 产物未触碰 = 安全底座成立的证据
    assert_products_untouched(before)

    meta = {
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "recorder": "scripts/record_golden.py",
        "target": "FastAPI 应用 campus_ids.web.app（基线对照）",
        "config": {
            "CAMPUS_IDS_DATA_DIR": str(TMP_DIR),
            "auth": "默认（api_token 为空 → 认证关闭，CSRF 强制生效）",
        },
        "csrf_token_sent": True,
        "csrf_missing_samples": csrf_missing,
        "stubbed_bindings": neutralized_report().splitlines(),
        "fidelity_note": (
            "被 stub 的入口在 golden 里呈现成功分支而非真实副作用"
            "（如 /api/model/train 返回 200 但未真训练）。data_dir 已隔离到临时目录，"
            "录制前后 6 个真实产物 md5 一致。"
        ),
        "samples": len(results) + len(SSE_PLACEHOLDERS),
        "status_breakdown": {
            "2xx": sum(1 for _n, s, _m in results if 200 <= s < 300),
            "3xx": sum(1 for _n, s, _m in results if 300 <= s < 400),
            "4xx": sum(1 for _n, s, _m in results if 400 <= s < 500),
            "5xx": sum(1 for _n, s, _m in results if s >= 500),
        },
    }
    (OUT_DIR / "_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print("=" * 74)
    print(f"录制 {meta['samples']} 个样本 → {OUT_DIR}")
    print(f"状态分布：{meta['status_breakdown']}")
    print(f"因 CSRF 缺失产生的 400：{csrf_missing}  （旧基线是 20，必须为 0）")
    print("真实产物 6/6 md5 未变 ✅")
    print("=" * 74)

    if csrf_missing:
        print("🔴 仍有 CSRF 缺失样本 —— 期望值不可用，需排查 token 传递")
        failures += 1
    return failures


def check() -> int:
    """只校验现有 golden：不应再出现 CSRF 缺失造成的 400。"""
    if not OUT_DIR.exists():
        print("🔴 baseline 目录不存在")
        return 1
    total = csrf_missing = 0
    for path in sorted(OUT_DIR.glob("*.json")):
        if path.name == "_meta.json":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        total += 1
        if "CSRF token is missing" in (payload.get("body") or ""):
            csrf_missing += 1
            print(f"  🔴 {path.name}")
    print(f"样本 {total} 个，CSRF 缺失 {csrf_missing} 个")
    if csrf_missing:
        print("🔴 需重录：python scripts/record_golden.py")
        return 1
    print("✅ 无 CSRF 缺失样本")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="契约基线录制（带安全底座）")
    parser.add_argument("--check", action="store_true", help="只校验现有基线，不重录")
    args = parser.parse_args()
    sys.exit(check() if args.check else record())
