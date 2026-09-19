"""旧 → 新 端点映射表（T2.18 的唯一真相源）。

回放 harness（`scripts/replay_contract.py` 与 `tests/test_contract_replay.py`）
共用本表，避免两处漂移。

## 判定类别

| kind | 含义 | 回放动作 |
|---|---|---|
| `kept` | 路径与方法都不变 | **真发请求**，比对状态级别 + JSON 顶层键（新 ⊇ 旧） |
| `renamed` | 路径变了，语义基本保留 | 只断言新路径在 OpenAPI 存在；**必须写 `note` 解释差异** |
| `consolidated` | 被任务/剧本端点合并取代 | 只断言替代端点存在；**必须写 `note`** |
| `page` | 页面路由（HTML） | **真发请求**并断言 `expect_status`；**必须写 `note`** |

## 三条硬规则

1. **非 `kept` / 非 `page` 的条目必须带非空 `note`** —— 这就是"差异须全部可解释"的可执行版本，
   由 `validate_mapping()` 在导入时断言。空解释 = 无法解释的破坏性变更。
2. **`page` 条目不能用 OpenAPI 做存在性判定** —— 页面路由有意设了
   `include_in_schema=False`（HTML 不该污染阶段 3 的 TS 类型生成），
   所以它们**不在 `app.openapi()["paths"]` 里**。必须用真实请求 + `expect_status` 判定。
3. **写操作一律不真发请求** —— 旧基线里 `/api/cleanup`、`/api/save`、`/api/model/train`
   都会真删数据 / 真写文件 / 真训练；回放只做规格层面存在性断言。
   例外：`page` 类的 POST 只带空 body（唯一的副作用是渲染一个错误页）。
"""
from __future__ import annotations

# 需要真实请求比对（只读）的类别
REPLAYABLE = frozenset({"kept"})


MAPPING: dict[str, dict] = {
    # ── 页面路由（T2.17 补齐）──────────────────────────────────
    # `page` 类的判定方式是「真发请求 + 断言 expect_status」，
    # 因为页面路由有意不进 OpenAPI（见文件头规则 2）。
    # 302 表示"认证开启时跳登录页"，也是页面存在的证据（区别于 404）。
    "page_index": {
        "kind": "page", "old": "GET /", "new": "GET /", "method": "GET", "path": "/",
        "expect_status": [200, 302],
        "note": "路径不变；新应用此前 404，T2.17 起返回控制台壳（旧为服务端渲染的完整仪表盘，阶段 3 再重写）",
    },
    "page_apidocs": {
        "kind": "page", "old": "GET /apidocs/", "new": "GET /docs",
        "method": "GET", "path": "/docs",
        "expect_status": [200],
        "note": "Swagger UI 路径由 /apidocs/ 变为 FastAPI 默认 /docs（OpenAPI 3.1 取代 Swagger 2.0）",
    },
    "page_login": {
        "kind": "page", "old": "GET /login", "new": "GET /login",
        "method": "GET", "path": "/login",
        "expect_status": [200],
        "note": "路径不变；旧应用默认配置（认证关闭）下该蓝图未注册故基线是 404，新应用始终提供",
    },
    "page_change_password": {
        "kind": "page", "old": "GET /change-password", "new": "GET /change-password",
        "method": "GET", "path": "/change-password",
        "expect_status": [200, 302],
        "note": "路径不变；同上，旧基线 404 是'认证关闭时不注册蓝图'所致",
    },
    "page_logout": {
        "kind": "page", "old": "GET /logout", "new": "GET /logout",
        "method": "GET", "path": "/logout",
        "expect_status": [405],
        "note": "**有意的破坏性变更**：方法由 GET|POST 收窄为 POST（+CSRF），GET 返回 405 —— 防跨站强制登出",
    },
    "login_form": {
        "kind": "page", "old": "POST /login", "new": "POST /login",
        "method": "POST", "path": "/login",
        "expect_status": [400, 405],
        "note": "**随前端默认模式切换的有意变更（2026-09-19 翻转 FRONTEND 默认值后由回放门禁抓出）**："
                "legacy 模式挂 pages.router，空 body POST /login → 400（双提交表单校验，失败渲染页面）；"
                "SPA 模式（现为默认）不挂 pages.router，登录走 POST /api/login，POST /login 无路由 → 405。"
                "两种模式均为设计内行为，期望值并列。",
    },

    # ── 路径与方法都不变的端点 ─────────────────────────────────
    "health": {"kind": "kept", "old": "GET /api/health", "new": "GET /api/health",
               "method": "GET", "path": "/api/health"},
    "check": {"kind": "kept", "old": "GET /api/check", "new": "GET /api/check",
              "method": "GET", "path": "/api/check"},
    "traffic": {"kind": "kept", "old": "GET /api/traffic", "new": "GET /api/traffic",
                "method": "GET", "path": "/api/traffic"},
    "traffic_history": {"kind": "kept", "old": "GET /api/traffic/history",
                        "new": "GET /api/traffic/history",
                        "method": "GET", "path": "/api/traffic/history"},
    "alerts": {"kind": "kept", "old": "GET /api/alerts", "new": "GET /api/alerts",
               "method": "GET", "path": "/api/alerts"},
    "tls_stats": {"kind": "kept", "old": "GET /api/tls/stats", "new": "GET /api/tls/stats",
                  "method": "GET", "path": "/api/tls/stats"},
    "tls_suspicious": {"kind": "kept", "old": "GET /api/tls/suspicious",
                       "new": "GET /api/tls/suspicious",
                       "method": "GET", "path": "/api/tls/suspicious"},
    "payload_check": {
        "kind": "kept", "old": "POST /api/payload/check", "new": "POST /api/payload/check",
        "method": "POST", "path": "/api/payload/check",
        "note": "路径保留；新应用另加 /api/payload/analyze 别名",
    },

    # ── 配置：读写都变了形态 ──────────────────────────────────
    "config_get": {
        "kind": "renamed", "old": "GET /api/config", "new": "GET /api/settings",
        "method": "GET", "path": "/api/settings",
        "note": "响应由扁平 CONFIG 改为分组（thresholds/ml_config/web_config/alert_config），且新增 high_freq_ip_threshold",
    },
    "config_post": {
        "kind": "consolidated", "old": "POST /api/config", "new": "PUT /api/settings",
        "method": "PUT", "path": "/api/settings",
        "note": "**有意变更**：POST 多键字典 → PUT 单键 {key,value}（N 个阈值发 N 次）；热更新已修（重建 rule_detector 并迁移 tracker）",
    },

    # ── 编排域：20 个端点收敛为 tasks / scenarios ──────────────
    "capture_status": {
        "kind": "consolidated", "old": "GET /api/capture/status", "new": "GET /api/tasks",
        "method": "GET", "path": "/api/tasks",
        "note": "7 个状态端点收敛为单一 GET /api/tasks（含 kind/status/description）",
    },
    "capture_enhanced_status": {
        "kind": "consolidated", "old": "GET /api/capture/enhanced-status", "new": "GET /api/tasks",
        "method": "GET", "path": "/api/tasks",
        "note": "同上，增强抓包状态由 tasks 中 capture_full 一项体现",
    },
    "detector_status": {
        "kind": "consolidated", "old": "GET /api/detector/status", "new": "GET /api/tasks",
        "method": "GET", "path": "/api/tasks",
        "note": "同上，检测节拍状态由 tasks 中 detection 一项体现",
    },
    "attack_status": {
        "kind": "consolidated", "old": "GET /api/attack/status", "new": "GET /api/tasks",
        "method": "GET", "path": "/api/tasks",
        "note": "同上，演练状态由 tasks 中 attack 一项体现",
    },
    "auto_status": {
        "kind": "consolidated", "old": "GET /api/auto/status", "new": "GET /api/tasks",
        "method": "GET", "path": "/api/tasks",
        "note": "同上；一键全流程状态由 tasks 中 auto 一项体现",
    },
    "dual_stats": {
        "kind": "consolidated", "old": "GET /api/dual/stats", "new": "GET /api/tasks",
        "method": "GET", "path": "/api/tasks",
        "note": "ML 侧指标改由 GET /api/health 的 components.ml_model 暴露，启停由 tasks 的 ml 体现",
    },
    "capture_start": {
        "kind": "consolidated", "old": "POST /api/capture/start",
        "new": "POST /api/tasks/capture/start",
        "method": "POST", "path": "/api/tasks/{name}/start",
        "note": "8 个启动端点收敛为 POST /api/tasks/{name}/start；重复启动返回 409，未知任务 404",
    },
    "capture_stop": {
        "kind": "consolidated", "old": "POST /api/capture/stop",
        "new": "POST /api/tasks/capture/stop",
        "method": "POST", "path": "/api/tasks/{name}/stop",
        "note": "5 个停止端点收敛为 POST /api/tasks/{name}/stop；重复停止幂等返回 200",
    },
    "capture_start_enhanced": {
        "kind": "consolidated", "old": "POST /api/capture/start-enhanced",
        "new": "POST /api/tasks/capture_full/start",
        "method": "POST", "path": "/api/tasks/{name}/start",
        "note": "增强抓包改由任务名 capture_full 表达（不再有 /start-enhanced 这种后缀）",
    },
    "capture_stop_enhanced": {
        "kind": "consolidated", "old": "POST /api/capture/stop-enhanced",
        "new": "POST /api/tasks/capture_full/stop",
        "method": "POST", "path": "/api/tasks/{name}/stop",
        "note": "同 capture_start_enhanced",
    },
    "detector_start": {
        "kind": "consolidated", "old": "POST /api/detector/start",
        "new": "POST /api/tasks/detection/start",
        "method": "POST", "path": "/api/tasks/{name}/start",
        "note": "检测节拍启动改由任务名 detection 表达",
    },
    "detector_stop": {
        "kind": "consolidated", "old": "POST /api/detector/stop",
        "new": "POST /api/tasks/detection/stop",
        "method": "POST", "path": "/api/tasks/{name}/stop",
        "note": "同 detector_start",
    },
    "attack_start": {
        "kind": "consolidated", "old": "POST /api/attack/start",
        "new": "POST /api/tasks/attack/start",
        "method": "POST", "path": "/api/tasks/{name}/start",
        "note": "演练启动改由任务名 attack 表达（旧实现入参键为 type/duration）",
    },
    "attack_stop": {
        "kind": "consolidated", "old": "POST /api/attack/stop",
        "new": "POST /api/tasks/attack/stop",
        "method": "POST", "path": "/api/tasks/{name}/stop",
        "note": "同 attack_start",
    },
    "auto_start": {
        "kind": "consolidated", "old": "POST /api/auto/start",
        "new": 'POST /api/scenarios/start {"scenario":"full"}',
        "method": "POST", "path": "/api/scenarios/start",
        "note": "一键全流程变为剧本 full=[capture_full, train, ml]；子步骤任一失败即整体失败（旧实现 ML 失败仍返回 success）",
    },
    "demo_start": {
        "kind": "consolidated", "old": "POST /api/demo/start",
        "new": 'POST /api/scenarios/start {"scenario":"demo"}',
        "method": "POST", "path": "/api/scenarios/start",
        "note": "一键演示变为剧本 demo=[capture, ml, attack]；旧实现直接读 _capture_running 私有变量绕过状态检查，且缺 body 会 415",
    },
    "dual_load": {
        "kind": "consolidated", "old": "POST /api/dual/load",
        "new": "POST /api/tasks/ml/start",
        "method": "POST", "path": "/api/tasks/{name}/start",
        "note": "模型加载并入 ml 任务；旧实现在缺 models/best.json 时 train.py:550 无参自调用会无限递归（潜伏缺陷）",
    },
    "dual_stop": {
        "kind": "consolidated", "old": "POST /api/dual/stop",
        "new": "POST /api/tasks/ml/stop",
        "method": "POST", "path": "/api/tasks/{name}/stop",
        "note": "ML 循环停止并入 ml 任务",
    },

    # ── 模型 / 运维 / 流 ──────────────────────────────────────
    "model_list": {
        "kind": "renamed", "old": "GET /api/model/list", "new": "GET /api/models",
        "method": "GET", "path": "/api/models",
        "note": "单数 /api/model/list → 复数 /api/models；字段由注册表条目映射为 name/version/created_at/metrics",
    },
    "model_train": {
        "kind": "renamed", "old": "POST /api/model/train", "new": "POST /api/models/train",
        "method": "POST", "path": "/api/models/train",
        "note": "**越界项（见 R3）**：T2.13 范围外，两份训练状态真相待统一，且当前无产物备份/二次确认",
    },
    "model_train_status": {
        "kind": "renamed", "old": "GET /api/model/train-status",
        "new": "GET /api/models/train/status",
        "method": "GET", "path": "/api/models/train/status",
        "note": "**越界项（见 R3）**：与 TaskRegistry 的 train 任务互不感知，训练状态有两份真相",
    },
    "cleanup": {
        "kind": "renamed", "old": "POST /api/cleanup", "new": "POST /api/admin/cleanup",
        "method": "POST", "path": "/api/admin/cleanup",
        "note": "移入 admin 族；days 语义已修（曾忽略参数），响应返回两表真实删除数",
    },
    "save": {
        "kind": "renamed", "old": "POST /api/save", "new": "POST /api/admin/export",
        "method": "POST", "path": "/api/admin/export",
        "note": "移入 admin 族并更名 export（语义由'保存'变为'导出'）",
    },
    "stream_alerts": {
        "kind": "consolidated", "old": "GET /api/stream/alerts", "new": "GET /api/stream?topics=alerts",
        "method": "GET", "path": "/api/stream",
        "note": "两条 SSE 连接合并为单条 + topics 参数（旧应用每客户端占 2 个线程）",
    },
    "stream_traffic": {
        "kind": "consolidated", "old": "GET /api/stream/traffic", "new": "GET /api/stream?topics=traffic",
        "method": "GET", "path": "/api/stream",
        "note": "同 stream_alerts",
    },
}


def validate_mapping() -> list[str]:
    """导入时自检：差异必须被解释；page 类必须有 expect_status；基础字段必填。"""
    problems: list[str] = []
    for name, spec in MAPPING.items():
        for field in ("kind", "old", "new", "method", "path"):
            if not spec.get(field):
                problems.append(f"{name}: 缺少字段 {field}")
        kind = spec.get("kind")
        if kind not in REPLAYABLE and not spec.get("note"):
            problems.append(f"{name}: kind={kind} 必须写 note 解释差异")
        if kind == "page" and not spec.get("expect_status"):
            problems.append(f"{name}: page 类必须声明 expect_status（不看 OpenAPI，只能真发请求判定）")
    return problems


def sample_names() -> list[str]:
    """基线里应有 golden 的样本名（不含 SSE 占位与 _meta）。"""
    return sorted(MAPPING)


PROBLEMS = validate_mapping()
if PROBLEMS:  # 让问题在 import 时就暴露，而不是等到回放跑完
    raise RuntimeError("映射表自检失败:\n  - " + "\n  - ".join(PROBLEMS))
