# ADR-0001: Web 层重写技术栈选型

- 状态：**已定稿**
- 决策时间：2026-09-16
- 最近修订：2026-09-16（并入技术路线评估结论：环境核验、单 worker 不变量、各层落地约定、待补职责）
- 二次修订：2026-09-16（并入 GitHub 成熟项目调研结论：**安全头改采纳现成库、CSRF 补签名、限流为"已接线未生效"**，新增 §1.1 补充采纳与 §6.1/§6.2，§9 待决项核实；依据 [`docs/GitHub成熟项目调研-2026-09-16.md`](../GitHub成熟项目调研-2026-09-16.md)）
- 上下文：[`docs/Web重构方案.md`](../Web重构方案.md)、[`docs/Web重写任务清单.md`](../Web重写任务清单.md)

## 1. 决策

Web 层采用**重写**路径（D1），技术栈如下：

| 层 | 选型 | 关键理由 |
|----|------|---------|
| 后端框架 | **FastAPI + Uvicorn** | 自动生成 OpenAPI，直接消灭 586 行 docstring（根因 B）；依赖注入天然表达认证/CSRF/限流三档；同步 `def` 端点自动走线程池，现有阻塞式抓包/检测逻辑无需改写 |
| 数据校验 | **Pydantic v2** | 请求体校验、配置模型、响应序列化同一套；FastAPI 原生集成（详见 §5.3） |
| 数据访问 | **SQLAlchemy 2.0（Core 写法）+ Alembic** | 用 Core 保留"显式 SQL"的心智模型，Alembic 提供重写期 schema 迁移；**不引入 ORM `Session`**（详见 §5.1） |
| 前端 | **Vue 3 + TypeScript + Vite** | 组件库自带表格/抽屉/表单/对话框，直接消掉 63 个 DOM id 手写绑定；中文生态文档最全 |
| UI 组件库 | **Element Plus** | 任务表、抽屉、表单校验开箱即用 |
| 图表 | **ECharts 5** | 替换 chart.umd；流式追加更顺；中文文档 |
| 实时通道 | **单条 SSE（`sse-starlette` + 线程安全桥接）** | 后端已有 SSE 实现；浏览器自带重连；`async` 端点不再占用工作线程，但生产者是普通线程，须桥接（详见 §5.2） |
| 前端测试 | **Vitest + Playwright** | 用自动化补上浏览器验证空白 |
| Python | 统一到 **3.13.x** | 与开发环境一致；需同时修正 Dockerfile 与 `pyproject`（详见 §3） |

### 1.1 补充采纳（2026-09-16 GitHub 调研结论）

对方案缺口做 GitHub 检索（3 批共 84 次查询，见 [`docs/GitHub成熟项目调研-2026-09-16.md`](../GitHub成熟项目调研-2026-09-16.md)）后，在 §1 主表之外**补充采纳**以下小件。三处大件（任务注册表、进程内事件总线、Flask→FastAPI 迁移工具）**逐条确认无成熟替代**，维持自写。

| 包 | 用途 | 对应任务 | 性质 |
|----|------|---------|------|
| [`secure`](https://github.com/TypeError/secure)（1055★ / MIT / **零依赖** / ASGI 原生） | 安全响应头，替掉自写中间件（见 §6 #3） | T1.11 ③ | 运行期依赖 |
| [`oasdiff`](https://github.com/oasdiff/oasdiff)（1364★ / Apache-2.0 / Go 单二进制） | OpenAPI 契约 diff + **破坏性变更分级** | T0.2 / T1.9 / T2.18 / A2 | **开发期工具**，非运行期依赖 |
| [`pytest-regressions`](https://github.com/ESSS/pytest-regressions)（220★ / MIT） | golden file 录制与比对（`data_regression` / `file_regression`） | T0.2 / T0.3 | 开发期依赖 |
| [`pytest-alembic`](https://github.com/schireson/pytest-alembic)（253★ / MIT） | 迁移链可 upgrade / 可 downgrade 自动测试 | T1.10 | 开发期依赖 |
| [`@vueuse/core`](https://github.com/vueuse/vueuse)（22356★ / MIT） | `useEventSource`（内置断线重连）/ `useIntervalFn` | T3.4 | 前端依赖 |
| [`vue-echarts`](https://github.com/ecomfe/vue-echarts)（10756★ / MIT，ECharts 官方团队出品） | 图表组件，免手管实例与 resize | T3.6 | 前端依赖 |

**约束不变**：以上均为小依赖，不触碰 §7 边界（不引中间件 / 不换库 / 不拆服务）。`slowapi` 保持**内存存储，不引 Redis**。新增/变更的依赖须按项目约定在 `pyproject.toml` 与 `requirements.txt` **双份同步**（对应新增任务 T0.8）。

## 2. 被否决的备选

| 备选 | 否决理由 |
|------|---------|
| Flask + apispec | 迁移量小，但文档仍需手工维护，根因 B 解不干净 |
| React 18 + Ant Design | 团队更熟 Vue，中文文档不如 Element Plus 全 |
| dataclass + 手写校验 | 与 FastAPI 生态脱节，Pydantic 零成本集成 |
| 沿用 sqlite3 手写 SQL（连 Alembic 也不引） | 重写期 schema 演进无迁移脚本，回滚靠手写 |
| Chart.js（沿用） | 流式追加体验差，ECharts 中文文档更全 |
| WebSocket | SSE 已满足需求，引入 WebSocket 增复杂度无收益 |
| SQLAlchemy **ORM `Session`** | 现有 DB 层仅 4 表 / 16 函数 / 21 条 SQL，且"线程局部连接 + WAL + 显式 SQL"已解掉 SQLite 跨线程问题；ORM session 会把复杂度请回来，高频写路径（`insert_traffic`）额外吃开销（详见 §5.1） |
| 继续用 Waitress 托管 FastAPI | 能用但非目标形态；Waitress 的线程模型正是 SSE 占线程的成因。Uvicorn 为单进程 asyncio，配合 §4.1 单 worker 约束更自洽 |

## 3. 环境核验（2026-09-16 实测）

**目标栈已全部就位**（`C:\Users\18551\anaconda3\python.exe`，Python 3.13.9），不存在依赖解析风险：

| 包 | 版本 | 包 | 版本 |
|----|------|----|------|
| fastapi | 0.141.1 | starlette | 1.6.0 |
| uvicorn | 0.52.3 | httpx | 0.28.1 |
| pydantic | 2.12.5 | itsdangerous | 2.2.0 |
| sqlalchemy | 2.0.48 | werkzeug | 3.1.6 |
| alembic | 1.18.4 | waitress | 3.0.2 |

**移植面（实测）**：

- 业务端点 **34 个**，分布在 **4 个蓝图**
- Jinja 模板 **3 个**
- Flask 特有调用点（`jsonify` / `request.` / `session[` / `current_user` / `render_template`）**106 处**

**需一并统一的版本不一致**（重写期顺手修掉）：

| 位置 | 现状 | 目标 |
|------|------|------|
| `Dockerfile:6` / `Dockerfile:15` | `FROM python:3.11-slim` | 3.13-slim |
| `pyproject.toml:9` | `requires-python = ">=3.10"` | `>=3.13` |
| 开发环境 | 3.13.9 | 已达标 |

## 4. 架构不变量（硬约束）

### 4.1 单 worker —— 违反即故障

**运行时状态全部驻留进程内**，实测分布：

| 位置 | 进程内状态 |
|------|-----------|
| `web/helpers.py` | **4 处线程创建点**（capture / enhanced capture / auto worker / detector tick）＋模块级可变全局（`_capture_running`、`_packet_queue`、`_saved_config`、`_state_lock`、`CONFIG`） |
| `web/attack_sim_state.py` | 2 处线程创建点（攻击线程 + watchdog） |
| `web/bp_model.py` | 1 处线程创建点（train worker） |
| `detector/dual_detector.py` | `_ml_thread` |
| `web/sse.py` | `_sse_subscribers` 订阅者表 + `_sse_lock` |
| `web/database.py` | `_local` 线程局部连接 |

多 worker 下的具体后果：

- `POST /api/capture/start` 落在 worker 1，随后 `GET /api/capture/status` 被负载均衡到 worker 2 → **返回"未运行"**
- SSE 订阅者注册在 worker A，事件在 worker B 产生 → **实时数据随机丢失**
- `init_db()` 与 DB 配置覆盖在 import 期执行，会各跑 N 遍
- 攻击/训练线程同理：启停分落不同 worker 后互相看不见

**⚠️ 现状缺陷**：`Dockerfile:59` 为 `gunicorn --workers ${GUNICORN_WORKERS:-4}`，`docker-compose.yml:19` 显式设 `GUNICORN_WORKERS=4` —— **当前容器部署本就是坏的**。本机走 `prod.py` + Waitress（单进程）所以一直未暴露。

**约束**：

1. Uvicorn **固定单 worker**（不加 `--workers`，不设 `WEB_CONCURRENCY>1`）
2. 启动时**断言**：检测到 `WEB_CONCURRENCY>1` 或 CLI `--workers>1` 时**直接拒绝启动**并输出原因，避免"看似部署成功、实际状态分裂"
3. 这条是 T1.x 新骨架的验收项，必须进测试

> 若将来确需横向扩展，前提是把运行时状态外置（Redis / 消息中间件）——与本 ADR §7 边界冲突，届时应**新开 ADR**，不要在本条上打补丁。

### 4.2 状态归属

- 运行时可变状态只允许存在于 `runtime/` 容器内（对应根因 C），**不允许**模块级裸全局
- Web 层可 import `services/`，`services/` **不得**反向 import Web 层（现有 `demo/attack_sim.py` 反向 import `helpers._packet_queue` 私有队列的做法须一并纠正）
- 跨线程通信统一走显式队列/事件总线，禁止跨模块直接读写对方私有变量

### 4.3 分层方向

```
web/ (FastAPI 路由 + 依赖)  →  services/ (用例编排，无框架依赖)  →  runtime/ (状态容器)  /  detector·model·capture (既有业务)
```

## 5. 各层落地约定

### 5.1 SQLAlchemy 2.0：用 Core，不用 ORM `Session`

实测 DB 层规模：**4 张表**（alerts / traffic_history / config / users）、**16 个函数**、**21 条 SQL**、全项目仅 **4 处** `sqlite3` 调用点。

引入 SQLAlchemy 的目的是拿 **Alembic 迁移能力**，不是换数据访问范式。因此：

- ✅ 用 `Table` / `select` / `insert` / `update` / `delete`（Core 表达式），保住现有显式 SQL 的可读性
- ❌ 不定义 `declarative_base()` 模型类，不使用 `Session` / `scoped_session`
- 连接仍走"线程局部 + WAL"，与现有 `database.py` 心智模型一致
- Alembic 只负责 schema 版本演进，不改写业务查询

> 若后续表数量显著增长、确实要上 ORM，则须满足：一请求一 session、每个工作线程各自 scoped session、**绝不跨任务线程共享**，并新开 ADR 记录该变更。

### 5.2 SSE：`async` 端点 + 线程安全桥接

现状：`sse.py` 用阻塞 `queue.Queue`，注释明确「每个 SSE 连接占 1 个 Waitress 线程」，上限 `MAX_SSE_SUBSCRIBERS = 20`。

改 async 后不再占工作线程，但**生产者是普通线程**（抓包、检测、演练），不能从别的线程直接写入 `asyncio.Queue`：

- ✅ 每订阅者一个 `asyncio.Queue`，生产者通过 `loop.call_soon_threadsafe(q.put_nowait, msg)` 投递，并持有 loop 引用
- ❌ 不要试图直接 `put_nowait` 到 `asyncio.Queue`（轻则静默丢事件，重则压力下抛 "attached to a different loop"）
- ❌ **端点必须是 `async def`**。写成同步 `def` 生成器会被 Starlette 丢进线程池，等于白改 —— 20 条连接会吃掉默认 40 个线程池槽位的一半
- 保留 20 订阅者上限；前端 2 条 SSE 收敛为 1 条（同时省线程与线程池槽）

### 5.3 Pydantic v2

- 请求体 / 响应模型 / 配置模型统一用 Pydantic v2
- 配置用 `pydantic-settings`，替掉 `config.py` 的 `os.getenv` 散读 + `helpers.CONFIG` 双写（配置三处真相问题随之收口）
- 响应模型必须显式声明，避免 FastAPI 自动推断导致契约漂移（阶段 1 的快照 diff 会检出这类问题）

## 6. 换栈后需自行补齐的职责（8 项）

Flask 生态的这些能力在 Starlette/FastAPI 侧**没有等价物或需要替代方案**，重写时必须逐项落地：

| # | 职责 | 原实现 | 替代方案 |
|---|------|--------|---------|
| 1 | 会话认证 | Flask-Login 0.6.3 | `SessionMiddleware`（itsdangerous 2.2.0 已装）+ 自写 `current_user` 依赖 |
| 2 | CSRF | Flask-WTF `CSRFProtect` | **有 3 个候选但无事实标准**（最高 108★），自写仍合理；但 token **必须 HMAC 签名**（现实现是未签名的朴素双提交）。现有「取 `<meta name="csrf-token">` → 带 `X-CSRFToken`」契约**可不重做** —— 若走库，`fastapi-csrf-protect` 用的正是**同名头**（详见 §6.1） |
| 3 | 安全响应头 | flask-talisman 1.1.0（含 CSP） | **采纳 [`TypeError/secure`](https://github.com/TypeError/secure)**（1055★ / MIT / **零依赖** / ASGI 原生 + BALANCED 预设）；"避免额外依赖"不再是自写的理由。⚠️ 前任 `flask-talisman` **已被上游 archived**（详见 §6.1） |
| 4 | 限流 | Flask-Limiter 4.1.1（写端点 30/min） | `slowapi`（2058★ / MIT，**内存存储、不引 Redis**）。⚠️ **已接线但实测未生效**：须逐路由 `@limiter.limit(...)`，并把"超限返回 429"写成断言（详见 §6.1） |
| 5 | CORS | flask-cors 6.0.5 | `CORSMiddleware`，直接替换 |
| 6 | 密码哈希 | `werkzeug.security` | werkzeug 3.1.6 可独立安装继续用；`auth.py` 的 scrypt 兼容判定不用改 |
| 7 | 测试客户端 | Flask `test_client` | httpx / `TestClient` —— **阶段 0 新写的 321 个测试与 `scripts/record_golden.py` 都要改调用方式** |
| 8 | 前后端类型 | 无 | `openapi-typescript`（8366★ / MIT，调研核实成立）从 OpenAPI 生成 TS 类型（对应 T3.2），**不手写 `api.js`** |

### 6.1 调研补充：三项的实测证据与落地方式（2026-09-16）

**#3 安全响应头（改动最大）** —— 实测新骨架相对旧版出现**功能回退**：

| 响应头 | 旧版（Flask-Talisman） | 新骨架（自实现，实测） | `secure` BALANCED 预设 |
|--------|:---------------------:|:--------------------:|:----------------------:|
| `Content-Security-Policy` | ✅ | ✅ | ✅（带 builder） |
| `X-Content-Type-Options` | ✅ | ✅ | ✅ |
| `X-Frame-Options` | ✅ `DENY` | ✅ `DENY` | ✅ `SAMEORIGIN` |
| `Strict-Transport-Security` | ✅（`strict_transport_security=True`） | ❌ **缺失** | ✅ `max-age=31536000; includeSubDomains` |
| `Permissions-Policy` | ❌ | ❌ 缺失 | ✅ |
| `Cross-Origin-Opener-Policy` / `Resource-Policy` | ❌ | ❌ 缺失 | ✅ |
| `Referrer-Policy` | ❌ | ✅ | ✅ |

落地方式：`app.add_middleware(SecureASGIMiddleware, secure=Secure.from_preset(Preset.BALANCED))`。
若坚持自实现，最低要求是补齐上表 ❌ 三项，并以 BALANCED 的 CSP 为基线 —— 手写 CSP 最容易漏 `base-uri` / `form-action` / `object-src` / `frame-ancestors`。
另注：重写后前端由 Vite 打包、无内联脚本，`script-src` 可从旧的 `'self' 'unsafe-inline'` **收紧到 `'self'`**（新实现已如此，须保持）。

**#2 CSRF** —— 问题不在"自写"，而在**没签名**：`secrets.token_hex(32)` + `hmac.compare_digest(cookie, header)` 属朴素双提交，只要攻击者能让受害者浏览器**写入**同域 cookie（同域子站 / 中间人 / HTTP 降级），即可自造一对匹配的 cookie + header，防线失效。两条路线：

- **路线 A（少写代码）**：采纳 [`fastapi-csrf-protect`](https://github.com/aekasitt/fastapi-csrf-protect)（108★ / MIT / 2026-08-11 活跃），接在写策略内部；其**请求头名就是 `X-CSRFToken`**，前端「取 token → 塞 header」的逻辑不用重写（只需改 token 来源 URL）。
- **路线 B（少引依赖）**：保留自实现，改为 `HMAC(secret, random)` + 服务端验签，cookie 在生产环境补 `Secure`。

无论哪条，**T1.7 的三档矩阵测试不可省** —— 三候选仅 108 / 83 / 67★，与 Flask-WTF 在 Flask 生态的地位不是一个量级，矩阵测试是"将来还能换库"的唯一保险。另有两处必须写进 README / 操作手册：

1. CSRF cookie 为 `HttpOnly`，前端**只能**从 `/api/csrf-token` 的 JSON 响应体取 token（按老习惯读 `document.cookie` 会拿到空值）；
2. 该接口每次调用都换新 cookie，**多标签页并发时后调用者会覆盖前者的 token** → 另一标签页随后 403（须在 T3.10 覆盖）。

**#4 限流** —— 实测 `80 × GET /api/health` **全部 200、无一 429**。`app.state.limiter` 与 `RateLimitExceeded` 处理器都挂了，但 slowapi 的 `default_limits` 需经装饰器显式施加到路由，且被装饰函数签名里要有 `request: Request`。这正是 RS12"安全能力悄悄消失"的实例：**接线看起来完整，功能为空**。

### 6.2 已检索但明确不采纳（登记备查）

记录在此，避免后续反复寻找：

| 候选 | 不采纳理由（对照本 ADR 边界） |
|------|---------------------------|
| `long2ice/fastapi-limiter`（790★） | **强依赖 Redis**，违反 §4.1 → 已选 slowapi（内存） |
| `fastapi-users` 类 | 要求 ORM 模型 + DB adapter，**直接违反 §5.1「不引 ORM `Session`」** |
| `celery` / `prefect` / `dagster` / `taskiq` / `arq` / `huey` / `dramatiq` / `Supervisor` | 消息中间件或进程外编排，违反 §4.1 单进程与 §7 边界；`dramatiq` 另为 LGPL-3.0 |
| 整站后台模板（`vue-vben-admin` 33459★ / `art-design-pro` 5874★ 等） | 星标最高者用 Shadcn UI，与 Element Plus 选型冲突；整站引入自带数十路由与独立 store 约定，会把根因 C 以另一种形式带回来。**只取版式参考** |
| `nfstream`（LGPL-3.0）/ `Kitsune-py` / `river` | 属 D4 范围外，且会**改变 17/18 维特征契约**（与阶段 0「冻结行为」及已训练模型的 `align_features()` 冲突） |
| `StratosphereLinuxIPS`（GPL-2.0）/ `suricata`（GPL-2.0）/ `zeek` | 不可链接进本项目；且属签名/流式范式（C 实现），与本项目"加密流量 + ML"定位不同。**只宜作答辩对照** |
| `encode/broadcaster`（1267★） | 事件总线的最接近候选，但**已 archived**（2025-04-09），不引 |

## 7. 仍然有效的边界

- ❌ 不引 Celery / Redis / 消息中间件
- ❌ 不换数据库（仍是 SQLite + WAL）
- ❌ 不拆多服务 / 不做前后端分离部署（前端构建产物仍由同一个 Python 进程托管）
- ❌ 不顺手改业务规则（`detector/detector.py` 覆盖率 100%，除接口适配外冻结）
- ❌ 不动 ML 内部（D4）

## 8. 验收以根因为准

若重写只做到"换个框架、照抄旧结构"，根因 A/B/C 一个都没解决，收益为负：

| 根因 | 目标 |
|------|------|
| A. 无后台任务抽象（**20 个编排端点** = 8 组同构实现） | 任务注册表 + 剧本模型 → 3 个端点 |
| B. 无文档外置（586 行 Swagger YAML 在 docstring 里） | FastAPI 自动生成 OpenAPI → docstring 归零 |
| C. 单一全局状态模块（`helpers.py` 657 行 / 12 全局 / 4 线程创建点） | `runtime/` 容器 + `services/` 无框架依赖 |

> **根因 A 计数口径**：编排族（`/api/{capture,detector,attack,auto,demo,dual,model}/...`）共 **21** 个端点，其中 `/api/model/list` 是只读列表、不属启停/状态，故编排端点为 **20** 个（启动 8 / 停止 5 / 状态 7）。编写迁移映射时须按**前缀族**分组再分类 —— 按后缀 `/start`、`/status` 匹配会漏掉 `/start-enhanced`、`/enhanced-status` 这类端点。

## 9. 后续 ADR 与待决事项

**候选 ADR（触发条件明确后再开，不提前写）**：

| 候选 | 触发条件 |
|------|---------|
| ADR-0002 横向扩展与运行时状态外置 | 单 worker 成为实际瓶颈，需要多实例部署 |
| ADR-0003 ORM 引入边界 | 表数量显著增长、Core 表达式维护成本明显上升 |

**待决事项**：

- [x] **`ruff` 统一 lint / format**，替代 flake8 + isort + black（重写期一并切换）—— 2026-09-16 调研**核实成立**：49645★ / MIT / 持续活跃，可勾选
- [ ] 前端状态管理引入 `Pinia`（任务表 / 全局状态需要跨组件共享时）
- [ ] 演示/评审时间点（D5：时间充裕，但阶段 2/3 仍应避开任何演示窗口）

**关联的既有缺陷**（本 ADR 记录，修复动作在任务清单中跟踪）：

- §4.1 的 `GUNICORN_WORKERS=4` 配置
- §3 的 Dockerfile / pyproject Python 版本不一致
