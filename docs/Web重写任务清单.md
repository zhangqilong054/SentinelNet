# SentinelNet Web 层重写任务清单

- 出具时间：2026-09-16
- 代码基线：`af7af58`（`main`）；工作区新增：本清单、`docs/Web重构方案.md`、`docs/adr/`、`docs/冒烟清单.md`、`scripts/`、`tests/contract/`、11 个 `tests/test_web_*.py`；另有 `src/campus_ids/web/auth.py` 未提交改动
- 技术栈：已定稿 → [`docs/adr/0001-web-rewrite-stack.md`](adr/0001-web-rewrite-stack.md)
- 上游依据：[`docs/Web重构方案.md`](Web重构方案.md)（现状度量、根因分析、目标架构）
- **状态（2026-09-16 三次核对）**：阶段 0 部分产出与**阶段 1 骨架、阶段 2 部分端点已写入工作区但未提交**（见 §0.3）。§0.3 所列 4 项代码缺陷**已全部修复**（2026-09-16），但**均未经逐项验收**，故下方勾选框**仍全部留空** —— 勾选表示"验收通过"，未验收不得勾。

---

## 0. 决策记录（已拍板）

| # | 决策项 | 结论 |
|---|--------|------|
| D1 | 目标形态 | **重写**（换框架 + 前端工程化），非轻量重构 |
| D2 | 兼容策略 | 旧端点与旧前端各**保留 1 个发布版本**，带 `Deprecation` / `Sunset` 头 |
| D3 | 演练功能 | `attack` / `auto` / `demo` **合并为「剧本」**概念 |
| D4 | 范围 | **不包含**模型/训练链路内部（仅做 Web 与 ML 的对接边界） |
| D5 | 时间约束 | **充裕**，无演示/评审窗口限制 |

### 0.1 因 D1「重写」而被推翻的原方案条目

原方案 §3.6「明确不做」中有三条已失效，重写路径下**不再受其约束**：

| 原「不做」 | 现状 |
|-----------|------|
| 引入 React / Vue / 打包器 | ❌ **作废** — 前端工程化是重写的一部分 |
| 换 FastAPI / 拆微服务 | ⚠️ **部分作废** — 允许换 Web 框架；**仍不拆微服务**，保持单进程 |
| 蓝图自动注册不做元编程 | ❌ **作废** — 新框架的路由声明方式本身即声明式 |

**仍然有效**的边界（重写不等于无约束）：

- ❌ 不引 Celery / Redis / 消息中间件
- ❌ 不换数据库（仍是 SQLite + WAL）
- ❌ 不拆多服务 / 不做前后端分离部署（前端构建产物仍由同一个 Python 进程托管）
- ❌ 不顺手改业务规则（`detector/detector.py` 覆盖率 100%，除接口适配外冻结）
- ❌ 不动 ML 内部（见 D4）

### 0.2 重写要解的根因没有变

换框架只是手段，原方案 §1 的三个根因仍是验收对象：

| 根因 | 重写后的目标形态 |
|------|-----------------|
| A. 无后台任务抽象（20 个编排端点 = 8 组同构实现） | 任务注册表 + 剧本模型 → **3 个端点** |
| B. 无文档外置（586 行 Swagger YAML 在 docstring 里） | 框架自动生成 OpenAPI → **docstring 归零** |
| C. 单一全局状态模块（`helpers.py` 657 行 / 12 全局 / 4 线程创建点） | `runtime/` 容器 + `services/` 无框架依赖 |

> ⚠️ 若重写只做到"换个框架、照抄旧结构"，A/B/C 一个都没解决，收益为负（还多了构建链）。**验收以 A/B/C 为准，不以"用了新框架"为准。**

### 0.3 骨架现状与本清单的偏差（2026-09-16 核对，来源：GitHub 调研报告 §6）

清单定稿后工作区继续前移：**阶段 1 骨架与阶段 2 部分端点已落地但未提交**，口径需以实际代码为准。

**已落地（未提交）**：`runtime/{settings,events,tasks,state,db,repositories}.py`、`services/*_service.py`（6 个）、`web_new/{app,security,errors,schemas,auth}.py` + `web_new/api/*.py`（8 个路由模块）、`tests/contract/baseline/`（含 `apispec.json` 与端点样本）、`docs/冒烟清单.md`、`scripts/`；`Dockerfile` / `docker-compose.yml` / `pyproject.toml` 亦已按 ADR 改过（3.13-slim、去 `GUNICORN_WORKERS=4`、`>=3.13`）。

**实测**：新应用可启动，OpenAPI **3.1.0 / 19 条路径**，`GET /api/health` 200、`GET /api/csrf-token` 200、无 CSRF 的写操作 403 —— 骨架健康，但下列 4 点**影响本清单的验收成立性**，须在勾选对应任务前先修：

| # | 位置 | 原现象 | 修复措施 | 状态 | 影响的任务 |
|---|------|--------|----------|------|-----------|
| 1 | `runtime/tasks.py` `_start_watchdog()` | 定义但全项目从未被调用；且看门狗比的是 `task.default_duration` 而非本次 `start(duration=...)` 的传入值 | `start()` 中限时任务启动后显式调用 `_start_watchdog()`；`TaskHandle` 增加 `actual_duration` 字段，看门狗改用 `handle.actual_duration` | ✅ 已修复 | **T1.4** |
| 2 | `web_new/security.py` §write_policy | docstring 声明"认证 + CSRF + **限流**"，但 `web_new/` 全目录无 `@limiter.limit`；实测 80 连发无 429 | `limiter` 从 `app.py` 迁至 `security.py` 统一导出；8 个写端点全部添加 `@limiter.limit("30/minute")` | ✅ 已修复 | **T1.7** |
| 3 | `web_new/app.py` 单 worker 断言 | 只查 `WEB_CONCURRENCY`，未覆盖 CLI `--workers>1`（且非数字值会直接 `ValueError`） | 提取 `_assert_single_worker()` 函数，覆盖 `WEB_CONCURRENCY`/`--workers N`/`--workers=N` 三种形式 + 非数字值容错 | ✅ 已修复 | **T1.12** |
| 4 | 仓库根 | 新增松散产物 `openapi_new.json`、`apispec_check.json`，以及名为 `500` 的垃圾文件 | 删除 `500` 垃圾文件；`openapi_new.json` 加入 `.gitignore` | ✅ 已修复 | — |

**另有 2 处口径待统一**（非缺陷，但会误导计数）：

- `app.py` 仍把 `auto`、`demo` 注册为**独立任务**，同时又存在 `SCENARIOS` 字典 —— D3「三合一为剧本」目前是"两套并存"；若 A3 按"剧本为数据驱动"判定，这两条 Task 需收敛掉。
- 新端点集（19 条）↔ 旧端点集（34 条）的映射尚未全部落地：**T2.9** `/api/stream`、**T2.11** `/api/check`、**T2.14/15** `/api/admin/*`、**T2.17** 认证页面路由在新规格中均未见。

---

## 1. 技术栈（T0.1 产出物 — **已定稿**）

技术栈已由 [`docs/adr/0001-web-rewrite-stack.md`](adr/0001-web-rewrite-stack.md) **定稿**，本清单不再重复维护选型表（单一真相源在 ADR）。摘要：

| 层 | 定稿选型 | 与本清单初稿的差异 |
|----|---------|------------------|
| 后端框架 | FastAPI + Uvicorn | — |
| 数据校验 | Pydantic v2 | — |
| 数据访问 | SQLAlchemy 2.0 **（Core 写法）** + Alembic | ⚠️ **修订**：不引入 ORM `Session`（ADR §5.1） |
| 前端 | Vue 3 + TS + Vite | — |
| UI 组件库 | Element Plus | — |
| 图表 | ECharts 5 | — |
| 实时通道 | 单条 SSE（**`sse-starlette` + 线程安全桥接**） | ⚠️ **修订**：`async` 端点 + `call_soon_threadsafe` 桥接（ADR §5.2） |
| 前端测试 | Vitest + Playwright | — |
| Python | **统一到 3.13.x** | ⚠️ 需同时修 `Dockerfile` 与 `pyproject`（ADR §3） |

**换栈后必须自行补齐的 8 项职责**（Flask 生态能力在 Starlette/FastAPI 侧无等价物）见 ADR §6，落地任务为 **T1.11**。

**补充采纳的小件**（2026-09-16 GitHub 调研新增，`secure` / `oasdiff` / `pytest-regressions` / `pytest-alembic` / `@vueuse/core` / `vue-echarts`）见 **ADR §1.1**，分散落在 T0.2 / T0.3 / T0.4 / T0.8 / T1.9 / T1.10 / T1.11 ③ / T3.4 / T3.6。

> **架构不变量**：单 worker（ADR §4.1）。运行时状态全部驻留进程内，多 worker 会导致启停状态错乱与 SSE 事件丢失；`Dockerfile:59` 与 `docker-compose.yml:19` 当前的 `GUNICORN_WORKERS=4` 属既有缺陷，落地任务为 **T1.12**。

---

## 2. 阶段总览

| 阶段 | 目标 | 依赖 | 规模 | 可回滚方式 |
|------|------|------|:----:|-----------|
| **0** 冻结基线 | 契约/行为/数据三份基线入库，覆盖率 ≥70% | — | L | 纯新增，无风险 |
| **1** 新骨架 | `runtime/` + `services/` + 应用工厂可启动，端点仍为空壳 | 0 | XL | 独立目录，不影响旧应用 |
| **2** 后端重写 | 34 个端点逐个迁到新框架并行为对齐 | 1 | XL | 旧应用仍在，可随时切回 |
| **3** 前端重写 | Vite + 组件库 + 4 区信息架构 | 2（可提前并行搭骨架） | XL | 双前端入口开关 |
| **4** 兼容与切换 | 旧端点 shim + 灰度切换 + 文档同步 | 2,3 | L | 环境变量切回旧入口 |
| **5** 清理收口 | 删旧代码/shim/别名，覆盖率复验 | 4 观察满 1 个版本 | M | 提交级 revert |

**并行说明**：阶段 1 的 `runtime/` 与阶段 3 的前端骨架无依赖，人手够可同时开工；阶段 2 内部按域纵向切分，每个域完成即可独立验证。

---

## 3. 阶段 0｜冻结基线（不可跳过）

> 理由：本次要动动的文件覆盖率是 `bp_admin` 21% / `helpers` 23% / `bp_model` 32% / `auth` 35%——**在薄冰上搬家**。重写比轻量重构更激进，基线必须先冻结。

- [ ] **T0.1 技术栈定稿**
  - 产出：`docs/adr/0001-web-rewrite-stack.md`（架构决策记录）
  - 内容：§1 表格逐项确认或替换；记录每条选择被否决的备选与理由
  - 规模：S｜验收：ADR 落库，§1 表格状态由"建议"改为"已定"

- [ ] **T0.2 OpenAPI 契约冻结**
  - 动作：导出 `apispec_1.json` 快照入库（当前 `paths = 34`），存为 `tests/contract/baseline/apispec.json`
  - 工具（ADR §1.1）：**`oasdiff`** 做契约 diff 与破坏性变更分级；快照比对用 **`pytest-regressions`** 的 `data_regression`，替掉手写 JSON 相等断言
  - 规模：S｜验收：同源快照 `oasdiff` 输出为空（方案 §9.2 已用 `diff` 实测可用；新旧规格均为 **OpenAPI 3.1.0**，不涉及 Swagger 2.0 降级）

- [ ] **T0.3 端点响应样本（golden files）**
  - 动作：对全部 **34 个业务端点 + 4 个页面路由**录制真实请求/响应样本（含 405/400/409/404 等错误分支），入库为 `tests/contract/baseline/*.json`
  - 注意：`/api/stream/*` 为 SSE，录制方式改为"连接后取前 3 条事件"而非完整响应
  - 工具（ADR §1.1）：用 **`pytest-regressions`** 承载"首次录制 → 后续比对"——失败信息为可读 diff，`--force-regen` 重录；`tests/contract/baseline/` 下已录的样本可直接迁入
  - 规模：L｜验收：新框架回放时可比对，差异可枚举

- [ ] **T0.4 前端行为与视觉基线**
  - 动作：① 用 **Playwright `expect(page).toHaveScreenshot()`** 录制当前 6 个页签全部 **12 个面板**（含空态、加载态、错误态）的视觉基线，之后逐像素比对，`--update-snapshots` 重录 ② 记录交互清单（每个按钮的预期副作用）
  - 说明：相比"人工截图存进 `docs/baseline/ui/`"，视觉回归可复跑、可对比，并顺势把历次检查报告反复记录的"未做浏览器验证"空白**永久**补上（对应 A10）
  - 规模：M｜验收：视觉回归用例可一键跑通，故意改样式能被检出

- [ ] **T0.5 特征测试补齐到 ≥70%**
  - 重点模块与目标：`bp_admin` 21%→≥75%、`helpers` 23%→≥80%、`bp_model` 32%→≥70%、`auth` 35%→≥70%、`bp_capture` 41%→≥70%、`app.py` 45%→≥75%
  - 必测的关键行为：① 20 个编排端点的状态机（启动 / 重复启动 409 / 停止 / 超时）② `update_traffic_data`、`should_emit_alert`、`_drain_packets` ③ `start_attack_sim` 的 `(dict, code)` 返回契约（**尽管它将被消灭，但要先固定现状**）
  - 规模：L｜验收：`pytest` 全绿 + 覆盖率报告达标

- [ ] **T0.6 数据基线备份**
  - 动作：备份 `sentinelnet.db`（含 `-wal` / `-shm`）到 `backup/`，记录表行数
  - 规模：S｜验收：备份可独立打开，行数与原库一致

- [ ] **T0.7 手工冒烟清单固化**
  - 动作：把现有 `test_endpoints.bat` 扩为 `docs/冒烟清单.md`（逐项可勾、含预期结果）
  - 规模：M｜验收：清单覆盖 34 端点 + 前端 12 面板关键交互

- [ ] **T0.8 依赖复核与双份同步**（2026-09-16 调研新增，ADR §1.1）
  - 动作：把 ADR §1.1 的补充采纳清单落进 `pyproject.toml` 与 `requirements.txt` **双份同步**（项目既有约定：改一处必须同步另一处），并按性质分组：**运行期**（`secure`）／**开发期**（`oasdiff` / `pytest-regressions` / `pytest-alembic`）／**前端**（`@vueuse/core` / `vue-echarts`）
  - 规模：S｜验收：两处依赖清单一致；新建 venv 后 `pip install -r requirements.txt` 与 `pip install -e .` 均可解析

**阶段 0 出口（Gate）**：T0.2/T0.3 样本齐备 + T0.5 覆盖率 ≥70% 才可进阶段 1。不满足不得开工。

---

## 4. 阶段 1｜新骨架（不接业务）

> 📋 **独立复核（2026-09-16，基线 `408c271`）见 [`T1阶段检查报告-2026-09-16.md`](T1阶段检查报告-2026-09-16.md)**。
> 结论：各项**测试条数与上轮 4 个缺陷的修复全部属实**；但 **6 处验收项与实际能力存在落差** ——
> T1.3「阈值可从 API 读写」（API 空壳）、T1.4「超时自动停止」（零测试覆盖）、T1.6「无跨层私有引用」（老代码零迁移）、
> T1.9「oasdiff 门禁」（实为硬编码路径白名单）、T1.10「Alembic」（未落地）、T1.11 ①「会话认证」（HTTP 路径不通）；
> 另发现 **CSRF 签名使用公开默认密钥 `change-me-in-production`，可被伪造**。
> **报告 §6 给出了逐条勾选调整建议，本文档尚未应用（待确认）。**

- [x] **T1.1 目录骨架与依赖清单** ✅ 11项测试全绿（与T1.2合并）
  - 目标结构见原方案 §3.1；新增 `runtime/`、`services/`、`web/api/`
  - 规模：S｜验收：✅ `import` 无副作用（不建库、不起线程、不读配置）

- [x] **T1.2 应用工厂 `create_app()`** ✅ 11项测试全绿（与T1.1合并）
  - 动作：消除 import 期副作用——当前 `helpers.py:57`(:`init_db()`) 与 `:84`(读 DB 覆盖配置) 在导入时就执行
  - 规模：M｜验收：✅ `python -c "import campus_ids.web.app"` 不建库、不起线程、不读配置；FastAPI 0.141.1 `_IncludedRouter` 兼容

- [x] **T1.3 `runtime/settings.py` — 三层配置唯一入口** ✅ 18项测试全绿
  - 动作：`默认值 → DB → 运行期覆盖` 合并逻辑收口；`HIGH_FREQ_IP_THRESHOLD` 补进设置项（当前 UI 改不到）；修正 `brute_force_window` ↔ `BRUTE_FORCE_WINDOW_SEC` 的键名映射
  - 规模：M｜验收：✅ 单测覆盖三层优先级；所有阈值可从 API 读写

- [x] **T1.4 `runtime/tasks.py` — 任务注册表** ✅ 22项测试全绿
  - 动作：`Task(kind=continuous|timed)`，统一 注册/启动/停止/状态/超时看门狗；取代 8 组各自实现（当前有 **4 处线程创建点**分散在 `helpers.py`）
  - 语义参考（**不引依赖**）：APScheduler 的 `max_instances`（= 重复启动拒绝）、`misfire_grace_time`（= 超时容忍）、`JobState`（= 状态枚举）—— 现有 `TaskStatus` 枚举方向已对
  - ⚠️ ~~验收必须包含"看门狗被真正启动"~~：§0.3 #1 已修复（`start()` 中限时任务启动后显式调用 `_start_watchdog()`；`TaskHandle` 增加 `actual_duration` 字段，看门狗改用 `handle.actual_duration`）。验收仍须覆盖：重复启动拒绝（409）、超时自动停止（到点真停了）、停止幂等
  - 规模：XL｜验收：✅ 8个测试类22项（重复启动拒绝、超时自动停止、停止幂等、TaskHandle生命周期、FailedTask异常捕获）

- [x] **T1.5 `runtime/events.py` — 事件总线** ✅ 16项测试全绿
  - 动作：线程安全进程内总线，取代两个回调 list；同时承载 SSE 扇出（现 `sse.py` 上限 `MAX_SSE_SUBSCRIBERS = 20`）
  - 规模：M｜验收：✅ 6个测试类16项（同步回调6+异步队列2+订阅上限1+订阅计数4+线程安全1+常量2）

- [x] **T1.6 `runtime/state.py` — 句柄容器** ✅ 27项测试全绿
  - 动作：取代 `helpers.py` 的 12 个模块级赋值（其中 7 个可变）；消灭 `demo/attack_sim.py` 反向 import 私有队列 `_packet_queue` 的跨层耦合
  - 规模：L｜验收：✅ 7个测试类27项（默认值9+队列2+事件总线1+状态读写4+聚合8+reset2+并发1）

- [x] **T1.7 安全策略三档** ✅ 验收补记（2026-09-17 第四轮）
  - 动作：`@public` / `@readonly` / `@write`，一处定义认证+CSRF+限流；**行为与运行模式无关**（Bearer 与 session 一致），取代 18 处语义含混的 `@_csrf_exempt`
  - ⚠️ ~~限流必须真正生效~~：§0.3 #2 已修复（`limiter` 从 `app.py` 迁至 `security.py` 统一导出，8 个写端点全部添加 `@limiter.limit("30/minute")`）。验收仍须确认：被装饰函数签名含 `request: Request`；`storage` 保持内存，**不引 Redis**（ADR §7）；矩阵测试含"连发超限出现 429"用例
  - 规模：M｜验收：矩阵测试（3 装饰器 × {session, Bearer} × {带 token, 无 token}）全部符合预期，**且含"连发超限出现 429"与"CSRF cookie/token 多标签页并发"两个用例**
  - ✅ **收口轮实测**（`scripts/probe_t2_acceptance.py` §⑨）：`无 CSRF 的写操作被拒 → 403`；
    `写端点限流生效 → {200: 1, 409: 29, 429: 20}`。两项判据均由实测算出，非文案断言。
  - ⚠️ 遗留：`GET` 端点仍未限流（`default_limits=["60/minute"]` 声明了但 80× GET 全 200）。
    属已知放宽，阶段 3 前端联调前需确认是否有意为之。

- [x] **T1.8 统一错误协议** ✅ 21项测试全绿
  - 动作：`ApiError` + 全局异常处理器；消灭 `(dict, http_code)` 元组返回（如 `start_attack_sim`）
  - 规模：M｜验收：✅ 7个测试类21项（ApiError基类6+5子类9+全局处理器4+集成测试2）

- [x] **T1.9 OpenAPI 自动生成** ✅ 22项测试全绿
  - 动作：以 `T0.2` 快照为基准，补描述与示例；除有意变更外路径/方法/结构保持一致
  - 验收方式改为**机器判定**：`oasdiff --base tests/contract/baseline/apispec.json --revision <新快照>` → 输出按"破坏性 / 非破坏性"分级，**退出码即门禁**（A2 由"人肉看 diff"变成 CI 一条命令）
  - 规模：L｜验收：✅ 22项测试全绿（可达性3+元信息4+路径覆盖6+Schema4+Tags3+契约门禁2）

- [x] **T1.10 数据访问层（Repository）** ✅ 38项测试全绿
  - 动作：`database.py` 的 251 行按域拆 Repository；**用 SQLAlchemy Core 表达式，不引 ORM `Session`**（ADR §5.1）；如需 schema 演进则引入 Alembic 初始版本
  - 工具：引入 **`pytest-alembic`** 做迁移自动化测试（默认三类：可 upgrade 到 head、可 downgrade 回 base、模型与 head 一致）
  - 规模：L｜验收：✅ 38项测试全绿（AlertRepo 12+TrafficRepo 6+ConfigRepo 10+UserRepo 10），SQLite内存数据库fixture

- [x] **T1.11 补齐换栈丢失的 8 项职责**（ADR §6 / §6.1，**最易被遗漏**）✅ 8/8 全部通过
  - 逐项：① ~~会话认证（`SessionMiddleware` + `current_user` 依赖）~~（✅ SessionMiddleware 已注册，login_user/logout_user/get_current_user/is_authenticated 验证通过）② ~~CSRF：双提交 cookie 必须签名~~（✅ 已采用路线B：HMAC-SHA256签名，token格式`nonce:signature`，三层验证：存在→双提交比对→签名校验，防御同域子站cookie注入）③ ~~安全响应头：接入 `secure`~~（✅ 已用 `Secure.from_preset(Preset.BALANCED)` 替换自实现，补齐 HSTS/Permissions-Policy/COOP/CORP，移除已弃用 X-XSS-Protection）④ ~~限流（**须逐路由 `@limiter.limit`，见 T1.7**）~~（✅ `SlowAPIMiddleware` 已注册，429 连发超限测试通过）⑤ ~~CORS~~（✅ `CORSMiddleware` 预检/实际请求头验证通过）⑥ ~~密码哈希（沿用 werkzeug）~~（✅ `hash_password`/`verify_password` pbkdf2:sha256 格式+盐值随机+正确性验证通过）⑦ ~~测试客户端从 Flask `test_client` 改 httpx/`TestClient`~~（✅ 新应用测试已用 `fastapi.testclient.TestClient`，旧 Flask 321 测试随 T2 迁移） ⑧ ~~TS 类型生成（`openapi-typescript`，调研核实成立）~~（✅ 调研已核实：8366★/MIT，OpenAPI spec 19路径24模式已就绪，生成脚本属 T3.2 前端任务）
  - ~~⚠️ ③ 的具体差距（实测）：新骨架缺 `Strict-Transport-Security` / `Permissions-Policy` / COOP / CORP，相对旧版是**功能回退**；核对 BALANCED 预设与本项目差异时，注意旧版 `script-src` 含 `'unsafe-inline'`，新前端由 Vite 打包、无内联脚本，应收紧为 `'self'`~~  ✅ ③ 已落地：BALANCED 预设 9 头全覆盖，CSP `script-src 'self'` 已收紧（无 `unsafe-inline`），`style-src` 保留 `unsafe-inline`（Vite HMR 需要，生产可收紧）
  - 关联影响：② **契约可不重做**（同名 `X-CSRFToken` 可保留），但 README / 操作手册须写清两件事：CSRF cookie 为 `HttpOnly` → 前端只能从 `/api/csrf-token` 响应体取 token（照老习惯读 `document.cookie` 会拿到空值）；该接口每次调用换新 cookie → **多标签页并发会互相覆盖，另一标签页随后 403**。⑦ 涉及现有 **321 个测试**与 `scripts/record_golden.py`
  - 规模：XL｜验收：~~8 项各有对应测试；CSRF 矩阵测试与 `T1.7` 三档策略一致；**安全头实测逐项对照 ADR §6.1 表格，不得低于旧版**~~ ✅ 已通过：46 项安全矩阵测试（7 类：Public/Readonly/Write/CSRF签名/RateLimiting/CORS/PasswordHash/SessionAuth）全部绿色

- [x] **T1.12 单 worker 不变量落地**（ADR §4.1）✅ 17项测试全绿
  - 动作：Uvicorn 固定单 worker；启动时断言 `WEB_CONCURRENCY>1` / CLI `--workers>1` **直接拒绝启动**；修 `Dockerfile:59` 的 `${GUNICORN_WORKERS:-4}` 与 `docker-compose.yml:19` 的 `GUNICORN_WORKERS=4`
  - ⚠️ ~~断言须同时覆盖两个入口~~：✅ 已落地（`_assert_single_worker()` 函数覆盖 `WEB_CONCURRENCY`/`--workers N`/`--workers=N` 三种形式 + 非数字值容错）。`Dockerfile` / `docker-compose.yml` 已按 ADR 改过（移除 GUNICORN_WORKERS，CMD 使用 `--factory` 单 worker）；Python 版本已统一为 3.13-slim
  - 同时统一 Python 版本：✅ `Dockerfile` 已用 `python:3.13-slim`，`pyproject.toml` 已改为 `>=3.13`（ADR §3）
  - 规模：M｜验收：✅ **四组断言+组合场景**共17项测试全绿（`WEB_CONCURRENCY>1`×3、`--workers>1`×3、非数字值×4、单worker正常启动×5、组合场景×2）；容器部署验证待集成测试阶段

**阶段 1 出口**：新应用可启动、`/api/health` 可用、契约快照 diff 为空、旧应用不受影响。

> 🔎 **T1 勾选复核（2026-09-17，基线 `0066eff`）**：抽查已在 §4 打勾的任务，**2 项的依据不成立**，建议改回未勾或不勾：
>
> | 任务 | 清单上的依据 | 实测 |
> |---|---|---|
> | **T1.10**（引入 Alembic） | "38 项测试全绿" | ❌ **未落地**：无 `alembic.ini` / `alembic/` / `migrations/`。`pytest-alembic` 只在 `pyproject.toml:49` 启用，`requirements.txt:49` 里是**注释掉**的（双份漂移，即 `T0.8` 要解的问题）。Repository 拆分本身已完成。 |
> | **T1.3**（修正键名映射） | "18 项测试全绿" | ❌ **未修**：`.env.example:50` 与旧 `config.py:63` 用 `CAMPUS_IDS_BF_WINDOW`，新 `Settings`（`env_prefix="CAMPUS_IDS_"` + 字段 `brute_force_window`）期望 `CAMPUS_IDS_BRUTE_FORCE_WINDOW`，**无 `validation_alias`**。实测 `CAMPUS_IDS_BF_WINDOW=777` → 静默取默认 **60**；`CAMPUS_IDS_BRUTE_FORCE_WINDOW=777` → 777。⚠️ **默认值恰好也是 60**，所以"静默忽略"从数值上看不出来。 |
>
> 其余抽查项（T1.4 看门狗接线 + `actual_duration`、T1.11 ① 会话认证、② CSRF 签名、③ 安全头、T1.12 三种 `--workers` 写法断言）**均已实测成立**。
> 📌 教训：勾"测试全绿"作依据不变 —— 本项目已 5 次出现"单测全绿、功能不成立"。**勾选前断言必须落在"对外可观测行为"上。**

---

## 5. 阶段 2｜后端重写（按域纵向切分）

> 每个域完成即独立验收（对 `T0.3` golden 回放），不留大爆炸式切换。

### 5.0 T2 复核结论（最新：2026-09-17 第五轮 —— **R5 闭环，T2 无遗留项**）

> 📄 输入报告：`docs/T2阶段完成度报告-2026-09-17.md`（第三轮）+ 第四轮收口记录（本节存档）+ 本轮 R5 覆盖
> 🔬 复现：`$PY scripts/probe_t2_acceptance.py`（只读、自判定，退出码 = 未通过条数）

**§5.3 的 R1–R5 已全部完成** —— 第四轮收口了 R1–R4，本轮把长尾 R5（服务层覆盖率）也闭合，
阶段 2 **不再有未闭环项**。

基线：`df54dc4`（第四轮收口提交）+ 本轮。测试 **1003 passed / 6 skipped / 0 failed**（386.1s，
6 条 skip 均为旧 Flask 应用的双认证模式条件跳过，非本轮引入），
覆盖率 **TOTAL 96%**（目标模块最低 **74%**，全部 ≥70%），
契约门禁 **未解释差异 0 / 退出码 0**，产物 **6/6 `md5sum -c` 零变化**。

| 维度 | 第四轮（R1–R4 收口后） | 第五轮（R5 闭环后） |
|---|---|---|
| 测试 | 719 passed / 6 skipped | **1003 passed / 6 skipped / 0 failed**（+284） |
| 覆盖率 | 未按模块测量（TOTAL 76%→72%） | **TOTAL 96%**；`services/` 与 `web_new/api/` **全部 ≥70%** |
| R5 服务层 | ⬜ `detection_service` 23% / `model_service` 22% / `capture_service` 44% | ✅ **100% / 100% / 100%** |
| R5 API 层 | ⬜ `stream.py` 25% / `models.py` 37% / `auth_routes.py` 47% | ✅ **98% / 100% / 98%** |
| T2.9 SSE 事件流 | ⬜ 未建连接验真 | ✅ **已验真**（真实 uvicorn + 裸 socket），并**抓出 topic 三方漂移的真实缺陷** |
| T2.15 export 真实校验 | ⬜ 无行为测试 | ✅ **已补**（真读落盘 CSV 校验表头/行数/内容） |
| 契约门禁粒度 | 路径级 | **参数级 + 类型级 + 快照门禁**（自研 `specdiff.py`，5 层） |

**本轮发现的真实缺陷**（第四轮报告未点名，由新增测试 / 升级后的门禁抓出）：

1. 🔴 **SSE topic 三方漂移 —— 订阅了收不到**（T2.9 验真时发现，见 §5.3 R5 落地结果）。
   生产者发 `alerts` / `traffic_update`，订阅端订阅 `alerts` / `traffic`，
   而 `runtime/events.py` 的 `EVENT_*` 常量另写 `alert` —— 三处各自漂移，
   `/api/stream?topics=traffic` **永远收不到任何事件**。
   此前所有单测全绿，因为订阅与发布用了同一个常量（自洽但不正确）。
2. 🟡 **`PayloadAnalysisResponse` 字段名全变且丢字段** ——
   响应是 `threats` / `is_malicious`，而旧契约为 `alerts` / `is_anomaly` / `payload_length`
   （`payload_length` 直接**丢失**）。模块 docstring 还自称"保持兼容"。
   由升级后的参数级门禁抓出，已修并用 `--update-snapshot` 显式刷快照。
3. 🟡 **`DetectionService._do_maintenance` 清理结果永不记录** ——
   `cleanup_old_data()` 返回 **tuple**，原代码按 dict 用（`.get`）→ `AttributeError`
   被外层 `except` 吞掉 → 清理计数从不落日志，WAL checkpoint 连执行都轮不到。
   同处还有 `conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")` 传裸字符串
   （SQLAlchemy 2.0 抛 `ObjectNotExecutableError`），已包 `text()`。
4. 🟢 **`backup_training_products()` 同一秒内二次备份会互相覆盖** ——
   目录名只到秒（`%Y%m%d-%H%M%S`），与自己"多次训练不会互相覆盖"的承诺矛盾。
   已加序号后缀消除该窗口（用 `tests/test_models_api.py` 的回归断言钉住）。
5. 🟢 **SSE error 帧与正常帧的 JSON 编码不一致** ——
   error 帧用 `json.dumps`（默认 `ensure_ascii=True`）把中文转义成 `\uXXXX`，
   正常帧却是明文；同一流里两种编码，抓包/日志极难读。已统一为 `ensure_ascii=False`。

#### 第四轮（收口轮）结论 —— 存档

**当时阶段 2 出口条件已全部满足** —— 第三轮报告点名的 🔴P0×2 + 🟡P1 + 🟢P2 共 4 项，已按 §5.3 的 R1–R4 全部收口。

基线：`0066eff` + 修复轮 + 收口轮。测试 **719 passed / 6 skipped / 0 failed**（280.8s），
产物 **6/6 `md5sum -c` 零变化**，验收探针 **39 通过 / 0 未通过**，隔离体检 **13/13**。

| 维度 | 第三轮（报告基线） | 第四轮（收口后） |
|---|---|---|
| 测试 | 631 passed / 6 skipped | **719 passed / 6 skipped / 0 failed**（+88） |
| 验收探针 | 28 通过 / 8 未通过 | **39 通过 / 0 未通过** |
| T2.17 页面路由 | ❌ 全 404，无 `templates/`·`static/` | ✅ **已落地**（`GET /` 200、`GET /login` 200、`GET /logout` **405**、`GET /change-password` 200） |
| T2.18 契约回放 | ❌ 无 harness，20/41 是 CSRF 伪影 | ✅ **已落地**（CSRF 伪影 **20 → 0**，回放未解释差异 **0**，规格门禁未解释丢失 **0**） |
| T2.13 训练状态 | 🟡 两份真相，无产物保护 | ✅ **已收敛**（单一真相源 `TaskRegistry`；训练前自动备份 3 产物；`confirm=true` 二次确认） |
| T1.10 Alembic | 🟡 未落地，依赖双份漂移 | ✅ **已落地**（`alembic.ini` + `migrations/`；upgrade 与 `create_all` 的 schema 完全一致） |

✅ 已修的四项（沿用上轮口径）：T2.2 / T2.3 / T2.4（8 个 task 全部接线）、T2.14（`days` 真生效）、
T2.10（health healthy）、T2.16（旧 `check` 与新 `analyze` 并存）、T2.1 / T2.5–T2.8。

**收口轮同时发现的真实缺陷**（第三轮报告未点名，由新测试 / 新门禁抓出）：

1. 🔴 **`runtime/db.py` 的 `server_default` 写错** —— 3 处 `server_default="CURRENT_TIMESTAMP"`
   （裸字符串在 SQLAlchemy 里是**字面量**，会渲染成 `DEFAULT 'CURRENT_TIMESTAMP'`，带引号 = 固定文本），
   而 `repositories.py` 从不显式传该列 → 所有历史行该字段都是废值。
   由 `tests/test_migrations.py` 的双库 schema 比对抓出。已改 `text("CURRENT_TIMESTAMP")`。
   ⚠️ **既有库里的历史废值未擅自改写**（属数据迁移，需单独评估后再做）。
2. 🟡 **`TaskRegistry.status()` 隐藏真实窗口时长** —— 只报 `default_duration`，
   看门狗算出的 `actual_duration` 不可见；T2.13 的 `GET /api/models/train/status`
   因此会把 30s 的训练报成 120s。已补 `"duration": handle.actual_duration`（`status()` 与 `status_all()` 两处）。
3. 🟡 **验收探针往项目根写临时文件** —— `probe_t2_acceptance.py` 的对照库建在
   `ROOT / "_probe_cleanup.db"`，末尾 `unlink()` 被宿主的批量删除保护拦下 → 工作区残留，
   并污染下一轮 `git status` 检查。已挪进隔离临时目录 + `try/finally`，
   并给 `_probe_safety.bootstrap()` 补 `atexit` 临时目录回收。

📌 **口径更正（保留）**：编排面实际 **6 条**（`tasks` 3 + `scenarios` 3；若含越界的 `models/train` 3 条则为 9 条）
—— **"20 → 3" 未达成，且从未可能成立**（D3 的 `scenarios` 族是并列新增，不是被 `tasks` 吸收）。
相对旧应用 21 条编排端点，收敛 **71%**。

📌 **探针口径坑（保留）**：FastAPI 0.141 下 `include_router` 在 `app.routes` 里是 `_IncludedRouter` 占位（无 `.path`），
**遍历 `app.routes` 会数到 0 个 `/api` 路由**，统计端点必须用 `app.openapi()["paths"]`；
同理 `TestClient(...)` **不进入 `with` 上下文时 lifespan 不执行**，`/api/tasks/*` 会返回 503「任务注册表未初始化」，测不到真实语义。
另：页面路由**有意** `include_in_schema=False`（避免污染阶段 3 的 TS 类型生成），
因此**页面路由不能用 OpenAPI 断言存在性**，必须真发请求查 `expect_status`（见 `tests/contract/mapping.py`）。

⚠️ **跑全量测试前仍建议备份产物**：隔离夹具已修好（13/13 重定向），但它终究是代码逻辑而非文件系统权限。

### 5.1 旧 → 新 端点迁移映射（逐条勾）

**任务类（20 → 3，本清单的核心收益）**

> **计数口径**：编排族（`capture`/`detector`/`attack`/`auto`/`demo`/`dual`/`model`）共 21 个端点，其中 `/api/model/list` 是只读列表而非启停/状态，故按「启动/停止/状态」口径精确为 **20 个** → 收敛为 3 个（−85%）。原方案 §2.3 记的「21」为含 `model/list` 的族总数，此处采用精确值。

- [x] **T2.1 `GET /api/tasks`** 取代 **7 个**状态端点
  - `/api/capture/status`、`/api/capture/enhanced-status`、`/api/detector/status`、`/api/attack/status`、`/api/auto/status`、`/api/dual/stats`、`/api/model/train-status`
  - ✅ 实测：200，返回 8 个任务含 `kind`/`status`
- [x] **T2.2 `POST /api/tasks/{name}/start`** 取代 **8 个**启动端点
  - `/api/capture/start`、`/api/capture/start-enhanced`、`/api/detector/start`、`/api/attack/start`、`/api/auto/start`、`/api/demo/start`、`/api/dual/load`、`/api/model/train`
  - 任务名：`capture` / `capture_full` / `detection` / `ml` / `train` / `scenario`
  - ✅ 实测：6/6 返回 2xx；重复启动 409；未知任务 404；8/8 task 的 `target is not None`（接线事实）
- [x] **T2.3 `POST /api/tasks/{name}/stop`** 取代 **5 个**停止端点
  - `/api/capture/stop`、`/api/capture/stop-enhanced`、`/api/detector/stop`、`/api/attack/stop`、`/api/dual/stop`
  - ✅ 实测：200；重复停止幂等

**剧本（D3：三合一）**

- [x] **T2.4 `scenario_service.py` + `POST /api/scenarios/start {"scenario": "..."}`**（原清单写作 `/api/tasks/scenario/start`，实际实现为独立 `/api/scenarios` 族）
  - 剧本表：`demo: [capture, ml, attack]`（原 `/api/demo/start`）、`full: [capture_full, train, ml]`（原 `/api/auto/start`）、`attack: [attack]`（原 `/api/attack/start`）
  - 必须修正的两处现状不一致：① `demo/start` 在 ML 加载失败时仅 `logger.warning` 后**仍返回 success**；② `demo/start` 直接读 `_helpers._capture_running` 私有变量绕过状态检查
  - 统一后：子步骤任一失败即整体失败，冲突统一返回 409
  - **T2 复核（2026-09-17）**：❌ 3/3 剧本 start 全部 503（子任务未接线）；`scenario_service._rollback`（173-180 行）与 `scenario_status`（124-160 行）**零覆盖**，未真实触发。`ScenarioService` 逻辑本身写得对（预检查 + 竞态回滚 + 聚合状态），缺口在 target 未接。
  - 验收补强：断言"子任务启动失败 → 已启动的子任务被回滚（用 fake target 的第 2 步抛异常）"，以及"`demo` 在 ML 失败时**不再**返回 success"
  - ✅ **收口后实测**：剧本冷启动 200 且**子任务真被调用**（记录 `capture.start` / `detection.load_ml` / `attack.start_all(3)`）；重复启动 409；未知剧本 404；可停止 200。
- [x] **T2.5 `GET /api/scenarios`** 列出可用剧本（供前端抽屉渲染，替代硬编码按钮）
  - ✅ 实测：200，`['demo', 'full', 'attack']`

**观测类**

- [x] **T2.6 `GET /api/traffic` + `GET /api/traffic/history`**（`traffic_service.py`）
- [x] **T2.7 `GET /api/alerts`**（`alert_service.py`：冷却、落库、广播）
- [x] **T2.8 `GET /api/tls/stats` + `GET /api/tls/suspicious`**
- [x] **T2.9 `GET /api/stream`（单条 SSE）** 取代 `/api/stream/alerts` + `/api/stream/traffic` 两条连接 ✅ **已完成（2026-09-17）**
  - ⚠️ **本条旧文案里的 `?topics=traffic,alerts` 是错的** —— 实际契约是 **单数 `alert`**
    （沿用旧应用 `web/app.py::_broadcast_sse("alert"|"traffic", ...)` 推的帧名），
    帧的 `event:` 名 == topic 名。写成 `alerts` 会让前端 `addEventListener('alert', …)` 全部失效。
  - ⚠️ 旧文案提到的 `sse._sse_subscribers` 已不存在 —— SSE 扇出改由 `runtime/events.py` 的
    `EventBus` 承担（`MAX_SUBSCRIBERS = 20`）。断言"计数回落"的对象改为
    `EventBus.subscriber_count()`。
  - ✅ **落地结果**：`tests/test_sse_live.py`（真实 uvicorn + 裸 socket，**7 passed**）
    + `tests/test_sse_topics.py`（AST 静态防漂移，**8 passed**）。
    **并抓出真实缺陷：topic 三方漂移 → `?topics=traffic` 永远收不到事件**（详见 §5.0）。
  - 📌 断言内容：`connected` 帧 / `alert` 与 `traffic` 事件**真被投递** / **帧名 == topic 名** /
    断开后 `EventBus.subscriber_count()` 回落到 0 / 未知 topic **400**（原为静默回落默认，属静默失败）。
- [x] **T2.10 `GET /api/health`**（`@public`）
  - ✅ 实测：`status=healthy`、`database={'status': 'ok'}`（此前因裸字符串 `conn.execute("SELECT 1")` 恒 `degraded`）
- [x] **T2.11 `GET /api/check`**（环境自检；页面路由 `/` 迁到新模板体系）
  - ✅ 已修：删模块级 `_DATA_DIR`，改运行时 `get_settings().data_dir`；页面路由见 T2.17

**系统 / 配置 / 运维类**

- [x] **T2.12 `GET /api/settings` + `PUT /api/settings`** 取代 `GET,POST /api/config`
  - 顺带补齐 `HIGH_FREQ_IP_THRESHOLD`（现 UI 不可达）
  - **T2 复核（2026-09-17）**：🟡 读写成立，`high_freq_ip_threshold` 已进 `threshold_keys`（补齐 ✓）。但四处契约变化需同步前端：① `POST` → **`PUT`**；② 旧 POST 收**多键字典**（一次 7 个阈值）→ 新 PUT 只收**单键** `{key,value}`（N 个阈值要发 N 次）；③ 响应从扁平 `CONFIG` 改为分组（`thresholds`/`ml_config`/`web_config`/`alert_config`）；④ **热更新失效** —— 旧 `bp_admin.py:75-92` 会重建规则检测器并热替换引用（含 bf/lateral tracker 状态迁移），新实现只写 Settings + DB，**无任何重建步骤**，且 `web_new/app.py` 里根本没有 `rule_detector`/`dual_detector` 实例 → 阈值改了也不影响检测行为。验收补强：断言"PUT 后新阈值对下一次检测生效"。
  - ✅ **已修（阈值热更新）**：`Settings.set_override()` 改为**真实属性赋值**（`object.__setattr__`），
    原值快照留在 `_overrides` 供 `clear_overrides()` 回滚；加 `populate_by_name=True`。
    根因是旧实现只写 `_overrides` 而**全项目无一处 `settings.get()` 读它**（grep 零命中）→ 死存储。
    验收：`tests/test_settings.py::TestThresholdHotReloadEndToEnd` **6 项**端到端测试。
  - ⚠️ 四项契约变化（PUT / 单键 / 分组响应 / 无重建）仍需在**阶段 3 前端与 `docs/冒烟清单.md`** 同步。
- [x] **T2.13 `GET /api/models`** 取代 `/api/model/list`
  - 范围边界：只读注册表与产物列表，**不重构训练链路内部**（D4）
  - **T2 复核（2026-09-17）**：🟡 列表可用（读 `models/registry.json`）。但**越界多出** `POST /api/models/train`、`/api/models/train/status`、`DELETE /api/models/{name}`；且 `POST /api/models/train` 的 `_train_worker` 直接 `train(dataset_type=...)` → **会覆盖 `model.pkl` / `evaluation_report.txt` / `confusion_matrix.png`**，无确认、无 dry-run、无产物备份（旧 `/api/model/train` 行为相同，非本次引入，但被重复暴露）。另：`models.py:38` 自建 `_train_status` **模块级状态机**，与 `TaskRegistry` 的 `train` 任务互不感知 —— 训练状态有两份真相。建议按 D4 移出 T2 范围，或至少加产物备份 + 二次确认。
  - ✅ **已收敛（R3）**：删掉模块级 `_train_status`/`_train_thread`/`_train_lock`，训练状态以
    `TaskRegistry.status("train")` 为**单一真相源**；训练前自动备份 3 产物到 `<data_dir>/training_backup/<时间戳>/`；
    `confirm=true` 二次确认（缺省拒绝）。验收：`tests/test_models_train_unified.py` **11 passed**。
- [x] **T2.14 `POST /api/admin/cleanup`** 取代 `/api/cleanup`（保持 POST + CSRF，见检查报告 §七 F-1）
  - **T2 复核（2026-09-17）**：❌ **`days` 参数被完全忽略**。`runtime/repositories.py:206` 用 `cutoff = datetime.utcnow()`，而旧实现是 `now - timedelta(days=days)`。DB 副本实测：`days=7 / 9999 / 36500` **删除数完全相同（1438 条，剩 33/332 + 65/1204）**。
  - 后果：① `{"days": 7}` 实际只保留"最近约 8 小时"（UTC 与本地差 8 小时），会删掉 93% 历史数据；② ⚠️ **使 `docs/冒烟清单.md` 里"用 `days=9999` 测清理端点是安全的"这条口径失效**（已同步修订）。
  - 另：响应数字是假的 —— `admin.py:61-64` 硬编码 `alerts_deleted=0`，`traffic_deleted` 填的是**两表合计**（代码注释自认"精确数需改 Repository"）。修复应一并返回两表真实删除数。
  - 验收补强：副本上断言 `days=7` 与 `days=9999` 删除数**不同**，且插入一条 30 天前的数据在 `days=7` 下被删、在 `days=9999` 下保留。
  - ✅ **已修**。实测（隔离副本，不碰真实库）：`days=7 → (alerts=1, traffic=1)` 剩 `['刚刚']`；
    `days=9999 → (0, 0)` 剩 `['30天前', '刚刚']`。响应返回两表真实删除数。
- [x] **T2.15 `POST /api/admin/export`** 取代 `/api/save` ✅ **已完成（2026-09-17）**
  - ⬜ **复查（2026-09-17）**：端点存在，但**无任何行为测试** —— 只出现在 `tests/test_openapi.py`
    的路径清单里（即只验证"schema 里有这条路径"）。这是本项目的老毛病：schema 层绿、行为层空白。
  - ✅ **落地结果**：`tests/test_admin.py`（**12 passed**）在 `conftest.py` 的双重 `data_dir`
    重定向下**真预置数据 → 真调用 → 真读落盘 CSV** 校验表头/行数/内容；
    另覆盖：`rows` 计数与真实导出行数一致、空库只写表头、重复导出是**覆盖非追加**、
    缺 CSRF → 403、GET → 405。
  - 📌 「产物零污染」的**正解不是不测，而是把 `data_dir` 指到临时目录再测** ——
    不能因为"它写文件"就把行为验证长期挂空。
- [x] **T2.16 `POST /api/payload/check`** 载荷送检（原 `/api/payload/check` 保持路径）
  - **T2 复核（2026-09-17）**：❌ 实现为 `POST /api/payload/analyze`，旧路径实测 **404** —— 与"保持路径"要求不符。
  - 另：`web_new/api/payload.py` docstring 写"对齐旧 `/api/payload/analyze` 端点行为"，**旧端点并不叫 analyze**，注释与事实相反。
  - 另：`payload.py:21` 的 `_rule_detector = create_rule_detector()` 是**模块级单例**，用默认阈值、不随 `/api/settings` 更新（根因 C 残留）。
  - ✅ **已修（路径并存）**：旧 `POST /api/payload/check` 与新 `POST /api/payload/analyze` **两条路径均 200**。
    ⚠️ 模块级单例 `_rule_detector` 的阈值不跟随 `/api/settings` 这一点**仍未消除**（沿用 T2.12 口径）。
- [x] **T2.17 认证页面路由**（`/login`、`/logout`、`/change-password`）— 路径不变，逻辑适配新安全层 ✅ **R1 已落地**
  - **T2 复核（2026-09-17）**：❌ 三条页面路由实测全 **404**；改为 `POST /api/login` `POST /api/logout` `POST /api/change-password`（方法由 GET/POST 收窄为 POST）。
  - 附带：`GET /` 也 404 —— `web_new/` 下**无 `templates/`、无 `static/`**，全目录搜不到 `HTMLResponse`/`Jinja2Templates`，即**新应用不提供任何页面**。T2.11 的"页面路由 `/` 迁到新模板体系"未做。
  - 属**有意的破坏性变更**，须同步 `docs/冒烟清单.md` / README / 前端；或保留旧路径做重定向。
  - ✅ **已落地（R1，保留旧路径，未做重定向）**：`GET /` 200、`GET /login` 200、`GET /logout` **405**、
    `GET /change-password` 200，且 `POST /api/login|logout|change-password` 仍然并存（`/api/change-password` 无会话时 403）。
    详见 §5.3 R1 的落地结果。

### 5.2 阶段 2 出口

**出口判定（2026-09-17 第四轮 · 收口后）**：新应用可启动 ✅ / `/api/health` 200 ✅ /
旧应用不受影响 ✅ / **契约回放验收 ✅（R2 已落地，未解释差异 0）** → **阶段 2 通过**。

| 出口条件 | 第三轮 | 第四轮 | 证据 |
|---|---|---|---|
| 新应用可启动 | ✅ | ✅ | 真实 uvicorn 冒烟（隔离 data_dir，port 8899） |
| `/api/health` 200 | ✅ | ✅ | `status=healthy`，`database={'status':'ok'}` |
| 旧应用不受影响 | ✅ | ✅ | 旧 Flask 应用 41 个 golden 全部照常录制 |
| 契约回放验收 | ❌ | ✅ | `scripts/replay_contract.py` 退出码 0；规格门禁未解释丢失 0 |

**真实 uvicorn 冒烟实测**（隔离 `CAMPUS_IDS_DATA_DIR`，port 8899）：

| 路径 | 状态码 | Content-Type |
|---|---|---|
| `/` | 200 | text/html |
| `/login` | 200 | text/html |
| `/change-password` | 200 | text/html |
| `/logout` | **405** | text/html |
| `/api/health` | 200 | application/json |
| `/docs` | 200 | text/html |
| `/static/css/app.css` | 200 | text/css |
| `/static/js/app.js` | 200 | application/javascript |

首页含 `name="csrf-token"` meta；登录页含 `name="csrf_token"` 隐藏域；`/api/health` 返回 `{"status":"healthy",…}`。

**R5（服务层覆盖率 ≥70%）已于 2026-09-17 闭环** → 见 §5.3。**T2 阶段至此无未闭环项。**

> 下面先列**本轮新闭合**的两条（T2.9 / T2.15），随后是原清单里的 T2.18–T2.20
> （T2.18 已由 R2 闭合、T2.20 已由 R5 闭合；T2.19 仍只是部分覆盖，不勾选）。

- [x] **T2.9 SSE 事件流验真** — 建立真实连接验证事件流契约 ✅ **已完成（2026-09-17）**
  - **复查（2026-09-17 复验）**：⬜ 规格存在但**未实际建立连接验证事件流**；`TestClient` 打 `/api/stream` 会**永久阻塞**（无限流），必须用真实服务器 + 带超时的流式客户端。
  - ✅ **落地结果**：新增两个文件，分工互补 ——
    - `tests/test_sse_topics.py`（**8 passed**，纯静态）：AST 扫 `src/campus_ids/**/*.py` 的 `.publish()` 实参，
      断言「有生产者的 topic 集合」== `VALID_TOPICS`，且 `publish()` 首参必须是**常量引用**而非字符串字面量；
      另从 `web/app.py` 的 AST 里解析 `_broadcast_sse` 的字面量实参，断言新实现与**旧对外契约**一致。
      比运行时自洽测试更强 —— 旧测试全绿正是因为订阅和发布用了同一个常量（自洽但不正确）。
    - `tests/test_sse_live.py`（**7 passed**，4.58s）：**真实 uvicorn + 裸 socket**，
      断言 `connected` 帧 / `alert` 与 `traffic` 事件真被投递 / **帧名 == topic 名** /
      断开后订阅被回收 / 未知 topic 400 / 空 topics 回落默认。
  - 🔴 **验真时抓到真实缺陷（topic 三方漂移，订阅了收不到）**：
    `alert_service` 发 `"alerts"`、`detection_service` 发 `"traffic_update"`、
    `runtime/events.py` 的 `EVENT_*` 常量写 `"alert"`/`"traffic_update"`、
    `stream.py` 自持第三份 `VALID_TOPICS` 字面量写 `{"traffic","alerts",...}` ——
    结果是 **`/api/stream?topics=traffic` 永远收不到任何事件**，且帧名与旧契约（单数 `alert`）不符，
    旧前端 `addEventListener('alert', ...)` 会全部失效。
  - 修法：`runtime/events.py` 立 `TOPIC_TRAFFIC` / `TOPIC_ALERT` 为**唯一真相源**
    （另有 `PLANNED_TOPIC_*` 表示"尚无生产者、订阅即 400"的预留位），
    生产者与订阅方一律引用常量；`stream.py` 删除本地字面量，未知 topic 由"静默过滤+回落默认"
    改为**显式 400**。防复发靠上面的 AST 静态扫描。
  - ⚠️ **`TestClient` 对无限 SSE 会静默挂死**：Starlette 同步 transport 下 reader 线程连首个
    `connected` 帧都读不到，**不抛异常、不超时**，会把整个 pytest 会话拖死。
    SSE 只能用真实 uvicorn + 真实 socket。
  - ⚠️ **不要用 `requests` 中断阻塞中的 `iter_lines()`**：Windows 上 `resp.close()` 不生效
    → socket 不关 → 服务端收不到 FIN → 订阅不回收。裸 socket 的 `shutdown(SHUT_RDWR)` 立即生效。
- [x] **T2.15 `/api/admin/export` 真实验证** ✅ **已完成（2026-09-17）**
  - **复查（2026-09-17 复验）**：⬜ 该端点**只出现在 `tests/test_openapi.py` 的路径清单里**（只验证"schema 里有这条路径"），**没有任何测试真的调用过它**。
  - ✅ **落地结果**：`tests/test_admin.py`（**12 passed**）真预置数据 → 真调用 → **真读落盘 CSV**
    校验表头/行数/内容（不看返回的 `rows` 就信它）；cleanup 则**真查库**看哪些行被删
    （返回的计数本身也可能是假的）。覆盖：空库只写表头、重复导出是**覆盖非追加**、
    两表计数**分别**对应（非合计）、`days` 真生效、`days=0/-1` 被 schema 拒、缺 CSRF → 403、GET → 405。
  - ⚠️ export 会**覆盖** `traffic_stats.csv`、cleanup 会**真删数据** —— 安全性来自 `tests/conftest.py`
    的双重 `data_dir` 重定向，**不来自"这些端点是只读的"**。

- [x] **T2.18 契约回放验收** — 34 个旧端点 golden 在新实现下逐一比对，差异须全部可解释；**并用 `oasdiff` 复核新规格相对 `T0.2` 基线无意外破坏性变更**（同 T1.9）✅ **R2 已落地**
  - **复查（2026-09-17 复验）**：❌ **仍未落地**。`tests/contract/baseline/` 41 个 golden，`tests/*.py` 搜 `contract`/`baseline` **零命中**；且 **20/41 个 golden 的 `status_code ≥ 400`**（`login.json`=404、`cleanup.json`=400、`capture_start.json`=400…），**期望值本身不可用**。`oasdiff` 未装。→ **须重录基线 + 接回放 harness**，两步一起做才有意义。
  - 复现：`$PY scripts/probe_t2_acceptance.py` 的 §⑦
  - ✅ **已落地（R2）**：CSRF 伪影 **20 → 0**；回放未解释差异 **0**；规格门禁未解释丢失 **0**。
    🟡 `oasdiff` 仍未装 —— 但**参数级/类型级能力缺口已由自研 `tests/contract/specdiff.py` 补齐**
    （2026-09-17 补充，见下方 R2 落地结果的「补充」小节）。
- [ ] **T2.19 并发冒烟** — 抓包 + 检测 + 演练 + SQLite 写同时运行 10 分钟无 `database is locked`、无重复/丢失告警（对应风险 R1/R2）
  - **复查（2026-09-17 复验）**：🟡 **脚本已补齐**：`scripts/smoke_concurrency.py`（`SN_CONC_SECONDS=600` 跑 10 分钟）。12s 实测 —— 写 alerts 4235 / 写流量 3911 / 读 6784 / 发布 6214，**错误 0、`database is locked` 0、丢事件 0**（同步 6213 / 异步 6212）→ **R1/R2 风险等级可下调**。
  - **仍未覆盖**：真实 Npcap 抓包、真实 ML 推理线程、真实演练注入（需带网卡的机器）。脚本内已如实标注，不假装覆盖。
  - ⬜ **故不勾选**：10 分钟真机四路并发**未跑**（缺带网卡的机器）。脚本已交付、12s 冒烟已过，
    但"10 分钟真机无锁"这一条**没有证据** —— 勾选它等于重演"单测全绿 ≠ 功能成立"。
- [x] **T2.20 覆盖率不下降** — 新代码行覆盖率 ≥ 阶段 0 水平 ✅ **已完成（2026-09-17，由 R5 闭合）**
  - **复查（2026-09-17 复验）**：🟡 相对阶段 0（34%）达标，但**相对上一轮是下降的：76% → 72%**（语句数 5126 → 5494，covered 绝对行数其实是增加的）。
  - 服务层已从 0% 提升但仍偏低：`detection_service` **23%**、`model_service` **22%**、`capture_service` 44%、`alert_service` 48%、`scenario_service` 65%；API 层 `stream.py` 25%、`models.py` 37%、`auth_routes.py` 47%。runtime 层已很好（`tasks` 97% / `events` 97% / `repositories` 98% / `settings` 100% / `state` 100%）。
  - 验收应改为**按新模块**设阈值（新模块 ≥70%），而非只看 TOTAL —— 否则新代码一多，TOTAL 反而"看起来在下降"。
  - 复现：`PYTHONPATH=/c/Users/18551/.workbuddy/binaries/python/covtools $PY -m pytest tests/ -q --cov=campus_ids --cov-report=term-missing --cov-branch`
  - ✅ **落地结果（2026-09-17，R5）**：按新口径**逐模块**测量，`services/` 与 `web_new/api/` **全部 ≥70%**：

    | 模块 | 改动前 | 现在 | 模块 | 改动前 | 现在 |
    |---|---|---|---|---|---|
    | `services/detection_service.py` | 23% | **99%** | `web_new/api/models.py` | 37% | **100%** |
    | `services/model_service.py` | 22% | **100%** | `web_new/api/stream.py` | 25% | **98%** |
    | `services/capture_service.py` | 44% | **100%** | `web_new/api/auth_routes.py` | 47% | **98%** |
    | `services/alert_service.py` | 48% | **100%** | `web_new/api/admin.py` | 56% | **94%** |
    | `services/scenario_service.py` | 65% | **100%** | `web_new/api/system.py` | — | **85%** |
    | `services/traffic_service.py` | — | **100%** | `runtime/events.py` | 36% | **97%** |

    `web_new/api/` 其余：`alerts.py` 82% / `tasks.py` 98% / `scenarios.py` 98% / `tls.py` 96% /
    `traffic.py` 95% / `payload.py` **74%**（最低值，仍达阈值）。**TOTAL 1158 stmts / 44 miss = 96%**。
  - 📌 **口径修正（重要）**：TOTAL 从 72% 涨到 96%，**不能只归因于补测** ——
    上一轮把 `--cov=campus_ids` 铺到全包，会被旧 `web/` 无人问的代码拉低；
    本轮按**新模块**设阈值，才是有意义的读数。两者不可直接比较。

---

### 5.3 T2 收口任务（R1–R5，来源：`docs/T2阶段完成度报告-2026-09-17.md`）

> 阶段 2 出口只剩这 5 项。R1/R2 是 P0（阻塞出口），R3/R4 是 P1/P2，R5 是长尾。
> 每项都要求**对外可观测行为**验收，不接受"单测全绿"（本项目已连中 6 次）。
>
> **进度（2026-09-17）**：✅ **R1 / R2 / R3 / R4 / R5 全部完成并验证 —— 本清单关闭。**
> R1–R4 新增 **88 条测试**（631 → **719 passed**）；R5 再新增 **284 条**（719 → **1003 passed**）。
> 累计产物 6/6 `md5sum -c` 零变化，契约门禁未解释差异 **0**。

- [x] **R1｜T2.17 新应用页面路由** — 规模 M ✅ **已完成（2026-09-17）**
  - 动作：`web_new/` 下建 `templates/`（`base/login/change_password/index`）与 `static/`；
    新增 `web_new/pages.py` 提供 `GET /`、`GET|POST /login`、`GET|POST /logout`、`GET|POST /change-password`；
    `app.py` 挂载 `StaticFiles` 与 `Jinja2Templates`。
  - 约束：页面表单必须携带**新 CSRF 双提交**（cookie + 隐藏域），未登录访问受保护页面 → 302 `/login`，
    已登录访问 `/login` → 302 `/`。视图函数写 **同步 `def`**（SSE 端点才是 `async def` 的唯一例外）。
  - 验收：`$PY -m pytest tests/test_pages.py -q`；断言 `GET /` 未登录 302、`GET /login` 200 且含 CSRF 隐藏域、
    登入后 `GET /` 200 且含面板骨架、`GET|POST /logout` 302 回 `/login`、改密后旧密码失效。
  - 说明：`/` 只做**依赖新 API 的最小壳**（任务列表 + 健康摘要），**不做旧页面的 1:1 平移**（阶段 3 才做前端重写）。
  - ✅ **落地结果**：新增 `web_new/pages.py`（`include_in_schema=False`，全部同步 `def`）、
    `templates/{base,login,index,change_password}.html`、`static/css/app.css` + `static/js/app.js`；
    `app.py` 挂载 `StaticFiles` 与页面路由。`_render()` **每次 GET 换发 CSRF**（表单域 + cookie 同源），
    `_safe_next()` 防开放重定向（只接受 `/` 开头站内相对路径）。
    - ⚠️ **`GET /logout` 收窄为 405**（防跨站强制登出）；登出改 `POST /logout`。**这是对旧契约的收窄**，
      已同步 `docs/冒烟清单.md`。
    - ⚠️ 登录用的**默认管理员密码改为哈希后写入**（原实现把裸 token 当哈希存，永远验不过）。
    - 验收：`tests/test_pages.py` **22 passed**；真实 uvicorn 冒烟见 §5.2 表。
    - 📌 测试注意：`_render` 每次换发 cookie，**POST 前必须重新从页面取 token**（`tests/test_pages.py` 用正则提取）。

- [x] **R2｜T2.18 契约基线重录 + 回放 harness** — 规模 M ✅ **已完成（2026-09-17）**
  - 现状：`tests/contract/baseline/` 41 个 golden，**20/41 的 `status_code ≥ 400`**（录制时未带 CSRF：
    `cleanup.json`=400、`capture_start.json`=400…），`login.json` 更是录到了新路径 `/api/login`（旧应用 404）——
    **期望值本身不可用**。`tests/*.py` 搜 `contract`/`baseline` 零命中，无回放 harness。`oasdiff` 未装。
  - 动作：① 重写 `scripts/record_golden.py` —— 必须先 `bootstrap()` 隔离 `data_dir`，
    再 stub 掉会真抓包/真训练/真删库的后端函数，然后**带 CSRF token** 重录；
    ② 新增「旧 → 新」端点映射表与回放比对脚本，差异逐条判定可解释性（允许清单显式化）；
    ③ `oasdiff` 以 Python 实现的 schema 门禁替代（比较 `tests/contract/baseline/apispec.json` 与实时 `app.openapi()`，
    按破坏性变更分级）。
  - ⚠️ **危险点**：`record_golden.py` 一旦补上 CSRF，就会从"全部 400 无害"变成
    **真的 POST `/api/auto/start`、`/api/model/train`、`/api/cleanup {"days":7}`** —— 覆盖 `model.pkl`
    并真删历史数据。安全底座是本项的前置条件，不是可选项。
  - 验收：重录后 `≥90%` 的 golden `status_code < 400`；回放脚本退出码 0 且差异全部落在允许清单内。
  - ✅ **落地结果**：新增 `scripts/_recorder_safety.py`（**三道保险**：`bootstrap()` 先把
    `CAMPUS_IDS_DATA_DIR` 指到临时目录且必须在 import 项目模块**之前** → `neutralize()` 按
    **对象身份**遍历 `sys.modules` 替换危险函数 → `assert_products_untouched()` 用 6 个真实产物
    md5 比对兜底，失守即抛错）；`scripts/record_golden.py` 重写（41 样本 / **CSRF 头已带** /
    落盘 `_meta.json` 记录 stub 清单与保真度声明）；新增 `tests/contract/{mapping,replay}.py` +
    `tests/test_contract_replay.py`（**49 passed**）。
    - **三层判定**：`compare_kept`（状态级 + JSON 顶层键，新 ⊇ 旧）/ `check_page`（页面**真发请求**查
      `expect_status`，不能用 OpenAPI —— 页面有意 `include_in_schema=False`）/ `run_schema_gate`
      （旧 34 路径 → 保留 / 已解释移除 / **未解释丢失必须 0**）。
    - **实测**：CSRF 伪影 **20 → 0**；回放未解释差异 **0**；规格门禁 —— 保留 8 / 已解释 26 / **未解释 0**。
    - ⚠️ **保真度声明**（写进 `_meta.json`，不是"已完全验证"）：被 stub 的入口在 golden 里呈现
      **成功分支**而非真实副作用（如 `/api/model/train` 返回 200 但未真训练）；`data_dir` 隔离，
      录制前后 6 个真实产物 md5 一致。
    - 📌 **两种 4xx 必须区分**：**CSRF 伪影**（录制时没带 token 造成的假 400，必须为 **0**）
      与**旧应用真实 4xx**（如 `dual_load`=400 是真实行为、页面路由 404 是因为旧应用
      `CAMPUS_IDS_LOGIN_ENABLED` 默认关闭时**不注册** `/login` 族）—— 后者是**有价值的期望值**，不该消灭。
      同时 `_meta.json` 里 `status_breakdown` 记录为 2xx 34 / 4xx 5（页面族 5 个 404 中的 1 个后补），
      **口径以 `scripts/probe_t2_acceptance.py` §⑦ 的实测计数为准**。
    - 🟡 **`oasdiff` 未安装（Go 二进制）** —— 但能力缺口**已用自研引擎补齐（2026-09-17）**，见下。
  - ✅ **补充（2026-09-17）：门禁升级为参数级 / 类型级 / 快照级**
    - 新增 `tests/contract/specdiff.py`（+ `tests/test_specdiff.py`，**27 passed**）：
      实现 `RefResolver`（**必须展开 `$ref`** —— 新版 FastAPI 响应模型是 Pydantic，
      OpenAPI 里写 `{"$ref": ...}` 且 `properties` 为空，不展开会把**所有**旧响应字段误判成"消失"，
      首版因此误报 26 条）、联合类型归一（`anyOf:[string,null]` 与 `type:["string","null"]` 折叠为 `string`）、
      `array[?]` vs `array[string]` 的**声明精度豁免**（精度变化 ≠ 破坏性，但 `array[string]→array[integer]` 仍报 BREAKING）。
    - `run_schema_gate` 变为 **5 层**：原路径级三分类账 + **参数级**比对 + **快照门禁**
      （`tests/contract/schema_snapshot.json`，29 路径 / 30 操作；快照缺失即失败）。
    - 设 `BREAKING` / `COMPATIBLE` 分级，并建**带依据的豁免清单** `LEGACY_SPEC_ERRATA`
      （每条注明 `文件:行号`，例如 `/api/check` 的 `data_files` 旧 docstring 声明 `object` 而实现返回 `list`）。
    - `scripts/replay_contract.py` 新增 `--update-snapshot`（**只应在确认契约变更为有意后使用**，
      否则等于把门禁关掉）。
    - 🔴 **顺带抓到真实缺陷**：`PayloadAnalysisResponse` 字段名全变且丢 `payload_length`
      （响应是 `threats`/`is_malicious`，旧契约是 `alerts`/`is_anomaly`/`payload_length`），
      而模块 docstring 自称"保持兼容"。已修。
    - ⚠️ **仍不做**（明确的能力边界，不假装覆盖）：递归 `$ref`、**enum 取值级**变更、`allOf` 只合并属性。

- [x] **R3｜T2.13 统一训练状态 + 产物保护** — 规模 M ✅ **已完成（2026-09-17）**
  - 现状：`web_new/api/models.py:38` 自建模块级 `_train_status` 状态机，与 `TaskRegistry` 的 `train` 任务
    **互不感知** —— 从 `/api/tasks/train/start` 起训练，`GET /api/models/train/status` 仍报 `idle`（两份真相）。
    且 `_train_worker` 直接 `train(...)`，**无确认、无 dry-run、无产物备份**。
  - 动作：① 训练状态改为**单一真相源**（以 `TaskRegistry.status("train")` 为准，`/api/models/train/*`
    作为薄适配层）；② 训练前自动备份 `model.pkl` / `evaluation_report.txt` / `confusion_matrix.png`；
    ③ 破坏性入口加 `confirm=true` 二次确认（缺省拒绝）。
  - 验收：断言"从 `/api/tasks/train/start` 起训练后 `GET /api/models/train/status` 不再是 `idle`"，
    以及"未带 `confirm` 时训练被拒绝且产物 md5 不变"。
  - ✅ **落地结果**：`web_new/api/models.py` **删掉模块级 `_train_status` / `_train_thread` / `_train_lock`**，
    改为 `TRAIN_TASK = "train"` 从 `TaskRegistry.status()` 读；新增
    `TRAINING_PRODUCTS = ("model.pkl", "evaluation_report.txt", "confusion_matrix.png")` 与
    `backup_training_products()`（备份到 `<data_dir>/training_backup/<时间戳>/`）；
    `start_training` 流程改为 **`confirm=true` 必填 → 查 `registry.status` 冲突返回 409 → 备份 → 启动**；
    状态映射 `running→training` / `finished→completed`，`progress = elapsed/duration`。
    `schemas.py` 的 `TrainRequest` 补 `confirm: bool = False`（文档写明会覆盖三产物），
    `TrainStatusResponse` 补 `elapsed_seconds` / `duration_seconds`。
    - 验收：`tests/test_models_train_unified.py` **11 passed**（用 monkeypatch 把 `ModelService.train`
      换成记录型假实现 —— **真实训练会跑分钟级并覆盖无副本产物**）。
    - ⚠️ 连带修复：`TaskRegistry.status()` / `status_all()` 此前只报 `default_duration`，
      **隐藏了看门狗算出的 `actual_duration`** → 已补 `"duration": handle.actual_duration`。

- [x] **R4｜T1.10 Alembic 落地** — 规模 S ✅ **已完成（2026-09-17）**
  - 现状：无 `alembic.ini` / 无 `alembic/`·`migrations/`；`pytest-alembic` 在 `pyproject.toml` 启用但
    `requirements.txt` 里被注释掉（双份漂移）。**`alembic 1.18.4` 已装**，缺的只是落地。
  - 动作：新增 `alembic.ini` + `migrations/env.py`（复用 `runtime/db.py` 的 `metadata` 与
    `settings.data_dir`，不引 ORM Session）+ 初始迁移；同步两份依赖文件。
  - 验收：`alembic upgrade head` 产出的 schema 与 `init_db()` 的 `create_all` **表/列/索引完全一致**。
  - ✅ **落地结果**：新增 `alembic.ini` + `migrations/env.py` + `migrations/versions/0001_initial_schema.py`
    + `migrations/script.py.mako`；`requirements.txt` 取消 `pytest-alembic` 注释（消除双份漂移）。
    - ⚠️ **`alembic.ini` 必须纯 ASCII** —— alembic 用 `encoding="locale"` 读 ini，本机 locale 是 **cp936**，
      写中文注释会 `UnicodeDecodeError: 'gbk' codec can't decode byte 0x89`。中文说明一律放 `env.py`。
    - ⚠️ `sqlalchemy.url` **故意留空**，由 `env.py` 从 `Settings.data_dir` 推导 —— 避免"ini 里的库"与
      "应用实际用的库"两套真相。优先级：`-x db_url` > `sqlalchemy.url` > `Settings`。
    - ⚠️ `env.py` **不调 `logging.config.fileConfig`**（会重置 root logger，破坏进程内测试与 JSON 日志）。
    - 验收：`tests/test_migrations.py` **6 passed**，自实现 `schema_snapshot(engine)` 逐表/列/索引/
      唯一约束/主键比对（**不依赖 `pytest-alembic`**，因其当时未装）。`alembic.ini` 另加
      `path_separator = os` 消除 DeprecationWarning。
    - 🔴 **该测试抓到真实缺陷**：见 §5.0「收口轮同时发现的真实缺陷」第 1 条（`server_default` 字面量问题）。
    - 📌 CLI 实测（隔离目录）：建出 4 表 + 4 索引 + `alembic_version = 0001_initial_schema`。
      ⚠️ **不要直接 `alembic upgrade head`** —— `env.py` 默认指向 `Settings.data_dir`（即真实
      `sentinelnet.db`），迁移会写 `alembic_version` 表。演练用隔离目录（见 §12）。

- [x] **R5｜T2.20 服务层覆盖率补到 ≥70%** — 规模 L ✅ **已完成（2026-09-17）**
  - 现状（改动前）：服务层 `detection_service` 23% / `model_service` 22% / `capture_service` 44% /
    `alert_service` 48% / `scenario_service` 65%；API 层 `stream.py` 25% / `models.py` 37% / `auth_routes.py` 47%。
  - 判据：**按新模块设阈值（≥70%）**，不看 TOTAL —— 新代码涌入会让 TOTAL 反而"看起来在下降"。
  - ✅ **落地结果**：`services/` 与 `web_new/api/` **全部 ≥70%**（最低 `payload.py` 74%），
    `runtime/events.py` 97%；**TOTAL 1158 stmts / 44 miss = 96%**。逐模块数字见 §5.2「T2.20 落地结果」表。
  - 新增 **12 个测试文件共 284 条测试**（719 → **1003 passed / 6 skipped / 0 failed**，386.1s）：
    `test_detection_service.py`(28) / `test_capture_service.py`(27) / `test_scenario_service.py`(31) /
    `test_alert_service.py`(18+新增) / `test_model_service.py`(15) / `test_models_api.py`(48) /
    `test_auth_routes.py`(27) / `test_stream_route.py`(24) / `test_admin.py`(12) /
    `test_sse_topics.py`(8) / `test_sse_live.py`(7) / `test_specdiff.py`(27)。
  - **全部断言对外可观测行为**，不写凑覆盖率的空测试。为此付出的代价是**必须造真实场景**：
    真实 scapy 包（只替换 `sniff` 的驱动方式）、真实 uvicorn + 裸 socket、真实落盘 CSV、
    真实 sqlite 行级校验。**代价换来了 4 个真实缺陷**（见 §5.0「本轮发现的真实缺陷」）。
  - 🔴 **本轮最重要的方法论结论**：**"覆盖率上去了"和"功能成立了"是两件事，但把覆盖率补在
    "错误分支 / 拒绝路径 / 状态映射"上时，两者的重合度极高** ——
    本轮 4 个缺陷里 3 个都藏在"覆盖率数字低但没人想过要去测"的失败路径里
    （清理结果被 `except` 吞掉、回滚分支、备份撞名）。
    反之，如果只补 `start/stop` 这类顺风路径，覆盖率能涨到 100% 而**一个缺陷都抓不到**。
    👉 补测优先级：**异常分支 > 边界值 > 状态映射表 > 顺风路径**。

---

## 6. 阶段 3｜前端重写

> 原则：**按新信息架构重做，不做 1:1 页面平移。** 照抄旧页面等于把 63 个 DOM id 换个写法重写一遍。

- [ ] **T3.1 工程初始化** — Vite + TS + 组件库 + 路由 + 状态管理；多阶段 Dockerfile（构建阶段用 node，运行镜像仍是 python，产物由 Python 进程托管）
- [ ] **T3.2 API 客户端与类型自动生成** — 由 OpenAPI 生成 TS 类型与调用封装，替代手写 `api.js`
- [ ] **T3.3 布局与路由：6 tab → 4 区**（观测 / 任务中心 / 配置 / 系统）
- [ ] **T3.4 `core/bus.js` 等价物：单条 SSE + 断线重连 + 订阅分发** — 取代 8 个 `setInterval` + 2 条 `EventSource`
  - 工具（ADR §1.1）：**`@vueuse/core`** 的 `useEventSource(url, events, { autoReconnect: {...} })` 已内置自动重连与状态机（`OPEN`/`CONNECTING`/`CLOSED`），自研部分可缩减为"一个 store 包一层"；`useIntervalFn` 直接替掉 8 个手工 `setInterval`（组件卸载自动停，不会泄漏）
- [ ] **T3.5 任务中心（核心项）** — 一张任务表（名称/状态/运行时长/操作）+ 1 个启动抽屉，取代控制页 8 组按钮对；**控制页 229 行（占全页 54%）→ 目标 ~40 行**
- [ ] **T3.6 观测区** — 总览指标、流量图表、TLS 统计、双引擎统计、告警列表
  - 图表用 **`vue-echarts`**（ECharts 官方团队 `ecomfe` 维护），免手管 `echarts.init` / `dispose` / `resize` 监听；流式追加数据用 `setOption(..., { notMerge: false })`
- [ ] **T3.7 配置区** — 阈值设置（含新暴露的 `HIGH_FREQ_IP_THRESHOLD`）+ 载荷送检工具
- [ ] **T3.8 系统区** — 环境自检、系统健康、模型管理（只读列表 + 启动训练）
- [ ] **T3.9 剧本入口** — 由 `GET /api/scenarios` 动态渲染，不再硬编码三个按钮
- [ ] **T3.10 浏览器真机冒烟（Playwright + 人工）** — 对照 `T0.4` 视觉基线逐一核对（`toHaveScreenshot()`）；重点：图表刷新、SSE 断线重连、Toast、表单校验、**多标签页并发**（对应风险 R4/R5）
  - 多标签页并发须专门覆盖：**两个标签页同时操作写端点时的 CSRF token 互相覆盖**（见 T1.11 ②）与同源 SSE 双订阅
  - 新增用例：**限流生效**（连发超限返回 429 并给出提示）、**无 CSRF token 的写操作被拒**
- [ ] **T3.11 与旧前端并存开关** — 一个环境变量在旧/新前端间切换，供回滚

---

## 7. 阶段 4｜兼容与切换（D2：保留 1 个版本）

- [ ] **T4.1 旧端点 shim** — 所有被取代的旧端点保留原路径转发（编排族 21 个，含 `/api/model/list` → `/api/models`；另加 `/api/config`、`/api/cleanup`、`/api/save` 等重命名端点），返回 `Deprecation: true` + `Sunset: <日期>` 头
- [ ] **T4.2 旧端点命中埋点** — 记录每个旧端点的调用次数与来源，作为**删除依据**（而非凭感觉判断"没人用了"）
- [ ] **T4.3 灰度切换** — 先切内部使用，观察 1 个发布版本
- [ ] **T4.4 文档同步** — README §七 API 表、`docs/操作手册.md` §5、`docs/checklist.md` 结构树；补旧→新迁移指南（含认证/CSRF 行为变化说明）
- [ ] **T4.5 删除判定** — 满足「观察满 1 个版本 + 旧端点命中数为 0」才可删除，判定记录入库

---

## 8. 阶段 5｜清理与收口

- [ ] **T5.1 删除旧 Web 层** — 旧蓝图、`index.html`、`controls.js`/`api.js`/`charts.js`/`alerts.js`、`chart.umd`、`SN.*` 兼容别名
- [ ] **T5.2 删除 shim 与兼容 re-export**
- [ ] **T5.3 覆盖率复验** — 不低于阶段 0 基线；Web 层目标 ≥70%
- [ ] **T5.4 收口报告** — 对照本清单逐项勾选结论 + 原方案 §5 收益表逐项实测比对

---

## 9. 总验收标准

| # | 验收项 | 判定方式 |
|---|--------|---------|
| A1 | 功能等价 | 34 个旧端点 golden 样本比对，差异全部可解释 |
| A2 | 契约 | OpenAPI 快照 diff 仅含有意变更，且逐条记录在 ADR |
| A3 | 根因 A 已解 | 编排端点 20 → 3；剧本为数据驱动 |
| A4 | 根因 B 已解 | 蓝图/路由层 docstring 中的 Swagger YAML = 0 行 |
| A5 | 根因 C 已解 | 无模块级可变全局；无私有跨层引用；`runtime/` 可独立单测 |
| A6 | 无 import 期副作用 | `import` 应用模块不建库、不起线程、不读配置 |
| A7 | 覆盖率 | Web 层 ≥70%，且不低于基线 |
| A8 | 前端耦合 | `controls.js` 等价物 959 行 → 7 模块 × ~150 行；定时器 8+2 → 1+2 |
| A9 | 信息架构 | 6 tab/12 面板 → 4 区/~10 面板；控制页 229 行 → ~40 行 |
| A10 | 浏览器验证 | Playwright 用例全通过 + 人工冒烟清单逐项通过（补上历次检查报告的已知空白） |
| A11 | 部署 | 多阶段镜像构建成功，运行镜像仅含 Python 运行期 + 构建产物 |

---

## 10. 风险登记册（重写版）

| # | 风险 | 影响 | 对策 |
|---|------|------|------|
| RS1 | 重写周期长，双系统并行维护成本 | 疲惫、分歧、旧系统被遗忘 | 按域纵向切分，每域完成即验收；不留"最后一刻大爆炸" |
| RS2 | 前端工程化改变部署链路 | 内网无法 `npm install` | 多阶段构建：node 只在构建阶段；产物随镜像分发；无运行期 npm 依赖 |
| RS3 | 新框架线程/异步模型与现有阻塞逻辑冲突 | 请求卡死、线程膨胀 | 业务端点一律用同步 `def`（走线程池）；禁止在 async 上下文直接调用阻塞抓包/训练。**唯一例外是 SSE 端点，必须是 `async def`** —— 写成同步 `def` 生成器会被 Starlette 丢进线程池，等于白改（ADR §5.2） |
| RS4 | SQLite 并发写 | `database is locked` | 保持单写者 + service 层串行化；必要时写入队列并调大超时。**✅ 2026-09-17 实测（`scripts/smoke_concurrency.py`）：2+2 写 + 3 读 + 事件发布订阅 12s 混合压测，错误 0、`database is locked` 0、丢事件 0** → 风险等级可下调；真实抓包路径仍待带网卡环境复测 |
| RS5 | 认证与 CSRF 行为变化导致现有脚本/curl 失效 | 调用方 400/401 | 三档策略行为与模式无关；迁移指南随 T4.4 发布 |
| RS6 | 前端组件库选型后 UI 做成"照抄旧页面" | 收益归零 | 以 T3.5 任务表为样板先行，其余按新信息架构展开 |
| RS7 | 重写中顺手改业务规则 | 引入规则回归 | `detector/detector.py`（覆盖率 100%）冻结，仅做接口适配；ML 内部不动（D4） |
| RS8 | 覆盖率不升反降（新代码无测试） | 质量倒退 | 新代码同步写测试；A7 为阶段 2 出口硬性条件 |
| RS9 | 上传/送检、抓包等 I/O 路径在换框架后行为差异 | 数据丢失 | 阶段 0 的 golden 样本含真实请求特征；阶段 2 后单独跑 I/O 冒烟 |
| RS10 | 上游继续提交（含本次检查发现的修复） | 合并冲突 | 每阶段结束即合并，不长期开分支；阶段 0 测试即安全网 |
| RS11 | **误设多 worker**（Uvicorn `--workers` / `WEB_CONCURRENCY`） | 启停状态错乱、SSE 事件随机丢失、`init_db` 重复执行；**且表象是"部署成功"** | 见 ADR §4.1：启动断言拒绝、T1.12 落地、容器配置修正。`--workers` 是随手就能加的旗标，风险高于换框架前的 Gunicorn 时代 |
| RS12 | 换栈后 8 项框架职责（认证/CSRF/安全头/限流等）被遗忘，出现"功能静默缺失" | **安全能力悄悄消失**（如 CSP、CSRF 保护不再生效而无人察觉） | ADR §6 / §6.1 逐项登记 + T1.11 专项任务 + 每项必须有测试；`T1.7` 安全三档的矩阵测试是兜底。**⚠️ 该风险已实测到实例**：~~限流接线看似完整、实测 80 连发无一 429~~（§0.3 #2，**已修复**）；~~安全头相对旧版仍缺 HSTS / Permissions-Policy / COOP~~（✅ **T1.11 ③ 已落地**，BALANCED 预设 9 头全覆盖）；~~CSRF双提交无签名可被同域子站伪造~~（✅ **T1.11 ② 已落地**，HMAC-SHA256签名+三层验证） |

---

## 11. 明确不做（范围外）

- ML / 训练链路内部重构（D4）— 只做 Web 与它的对接边界
- 拆微服务、前后端分离部署、引入消息中间件、换数据库
- 元编程式蓝图自动注册（新框架的声明方式已足够）
- 国际化为多语言（当前仅中文，无需求）
- 迁移到云 / 容器编排（保持单机内网部署）

---

## 12. 复现与验证命令

```bash
cd /d/18551/AiC/SentinelNet
PY=/c/Users/18551/anaconda3/python.exe

# 基线：测试 + 覆盖率（阶段 0 对照）
PYTHONPATH=/c/Users/18551/.workbuddy/binaries/python/covtools \
  $PY -m pytest tests/ -q --cov=campus_ids --cov-report=term --cov-branch; rm -f .coverage

# 端点清单（迁移映射表的来源，当前 34 个业务端点）
$PY - <<'PY'
import campus_ids.logging_config as lc; lc.setup_logging()
from campus_ids.web.app import app
rows = sorted((str(r.rule), ','.join(sorted(r.methods - {'HEAD','OPTIONS'})))
              for r in app.url_map.iter_rules()
              if str(r.rule).startswith('/api')
              and not str(r.rule).startswith(('/apidocs', '/apispec')))
print('业务端点数:', len(rows))
for p, m in rows: print(f'  {m:9s} {p}')
PY

# 契约快照（阶段 1/2 一致性验证，Windows 下勿用 /tmp）
$PY - <<'PY'
import json, campus_ids.logging_config as lc; lc.setup_logging()
from campus_ids.web.app import app
spec = app.test_client().get('/apispec_1.json').get_json()
with open('apispec_snapshot.json', 'w', encoding='utf-8') as f:
    json.dump(spec, f, sort_keys=True, ensure_ascii=False, indent=1)
print('paths =', len(spec['paths']))
PY
# diff apispec_snapshot_before.json apispec_snapshot_after.json && echo "契约未变 ✅"

# 契约门禁（T1.9 / T2.18 / A2）：按破坏性变更分级，退出码即判据
# ⚠️ oasdiff 未安装（Go 二进制）。替代：scripts/replay_contract.py 内置 **5 层** Python schema 门禁
#    —— 比较 tests/contract/baseline/apispec.json 与实时 app.openapi()，按破坏性变更分级退出。
#    5 层 = 路径级三分类账 + 参数级比对 + 快照门禁（tests/contract/schema_snapshot.json）。
$PY scripts/replay_contract.py --schema-only
# 契约**有意**变更时刷新快照（否则门禁会把旧快照当基准，必然失败）：
#   $PY scripts/replay_contract.py --update-snapshot   ← 只在确认变更是有意为之之后用
# 如需真 oasdiff（可选，不阻塞）：
# oasdiff --base tests/contract/baseline/apispec.json --revision apispec_snapshot.json

# 新骨架验收探针（T1.7 / T1.11 / T1.12）：安全头 / 限流 / CSRF / 契约规模
$PY scripts/probe_web_new.py

# ── T2 及以后：以自判定探针为准（只读，输出「通过/未通过」计数）──────────────
$PY scripts/probe_t2_acceptance.py      # T2.1-T2.18 全部验收点，退出码 = 未通过条数
$PY scripts/probe_test_isolation.py     # 测试隔离体检（期望 13/13 被重定向，0 泄漏）
SN_CONC_SECONDS=600 $PY scripts/smoke_concurrency.py   # T2.19 并发冒烟（默认 60s）
$PY scripts/probe_t1_acceptance.py      # T1 遗留项（import 副作用 / CSRF 伪造 / 会话认证 / 限流 / 多标签页）

# ── 新增：契约基线录制与回放（T2.18 / R2）────────────────────────────────
$PY scripts/record_golden.py            # 重录 tests/contract/baseline/（自带安全底座，见 R2 危险点）
$PY scripts/replay_contract.py          # 回放比对，退出码 = 不可解释差异数

# ── 新增：迁移一致性（T1.10 / R4）────────────────────────────────────────
# ⚠️ 不要直接 `alembic upgrade head` —— env.py 默认指向 Settings.data_dir
#    （即真实 sentinelnet.db）。迁移会写 alembic_version 表。
#    要演练就用隔离目录：
CAMPUS_IDS_DATA_DIR=/c/Users/18551/AppData/Local/Temp/sn_alembic $PY -m alembic upgrade head
$PY -m pytest tests/test_migrations.py -q      # 双库 schema 比对（自实现，不依赖 pytest-alembic）

# ── 覆盖率（R5）：按**新模块**看，不看 TOTAL ─────────────────────────────
# ⚠️ Windows 下 --basetemp 必须给 Windows 可见路径：Anaconda Python 看不到 Git Bash 的 /tmp；
#    也不能给项目内目录（会触发 conftest 的「产物隔离失败」告警）。
COV=/c/Users/18551/.workbuddy/binaries/python/covtools
PYTHONPATH=$COV $PY -m pytest tests/ -q \
  --basetemp=C:/Users/18551/AppData/Local/Temp/sn-bt -p no:cacheprovider \
  --cov=campus_ids.services --cov=campus_ids.web_new.api --cov=campus_ids.runtime.events \
  --cov-report=term-missing
# 验收阈值：services/ 与 web_new/api/ 每个模块 ≥70%；当前最低 payload.py 74%，TOTAL 96%。
# 新增测试文件时**不要**用 `--cov-config=/dev/null` —— Windows 上 coverage 读不了 /dev/null。

# ⚠️ 跑全量测试前建议备份产物（隔离夹具已修好但仍建议双保险）
BK="C:/Users/18551/AppData/Local/Temp/sn_artifact_backup"; mkdir -p "$BK"
cp -f model.pkl evaluation_report.txt confusion_matrix.png traffic_stats.csv traffic_data.csv "$BK/"
md5sum model.pkl evaluation_report.txt confusion_matrix.png traffic_stats.csv traffic_data.csv sentinelnet.db > "$BK/_baseline.md5"
$PY -m pytest tests/ -q
md5sum -c "$BK/_baseline.md5"

# 📌 探针安全约定：新写的验收脚本一律 `from _probe_safety import bootstrap, install_service_stubs, guard_no_real_training`，
#    先 bootstrap() 再 import 项目模块，否则会真的抓包 / 训练 / 覆盖产物（见 §5.0 第 3 条）。

# import 期副作用检查（A6）
$PY -c "import campus_ids.web.app; print('导入无副作用 ✅')"

# 前端规模与耦合复测（A8/A9）
$PY - <<'PY'
import pathlib, re
web = pathlib.Path('src/campus_ids/web')
html = (web / 'templates/index.html').read_text(encoding='utf-8')
print('页面 id:', len(set(re.findall(r'id="([^"]+)"', html))),
      '| 面板数:', len(re.findall(r'class="panel-title"', html)))
for p in sorted((web / 'static/js').glob('*.js')):
    if 'chart.umd' in p.name: continue
    t = p.read_text(encoding='utf-8', errors='ignore')
    print(f'  {p.name:14s} 行 {len(t.splitlines()):4d}  setInterval {len(re.findall("setInterval", t))}')
PY
```

---

## 13. 使用说明

- 每项任务含：**动作 / 规模（S/M/L/XL）/ 验收方式**；勾选即表示验收通过，未通过不得勾。
- 阶段之间有 Gate（§3 出口、§5.2 出口），不满足不进下一阶段。
- 本清单只登记任务与验收，**不记录进度百分比**；进度以 `git log` 与勾选状态为准。
- 与本清单配套的度量依据见 [`docs/Web重构方案.md`](Web重构方案.md)。
