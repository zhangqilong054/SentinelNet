# SentinelNet Web 层重构方案（提案）

- 出具时间：2026-09-16
- 代码基线：`af7af58`（工作区干净）
- **本文档仅为方案，未修改任何代码**（`git status` 应为空）
- 结论依据：对 `src/campus_ids/web/` 全量静态度量 + 运行时接口盘点（脚本见 §9）

---

## 1. 结论摘要

「冗杂」不是功能多，而是**同一件事被写了多次、同一份真相被存了三处**。三个根因：

| 根因 | 一句话证据 |
|------|-----------|
| **A. 没有"后台任务"抽象** | 8 组 `start/stop/status` 端点（21 个）各自重复实现，`auto` 与 `demo` 是同一种"多步编排"的两种写法 |
| **B. 没有"文档外置"** | Swagger YAML 写在函数 docstring 里，占蓝图文件 **33%–57%**（约 586 行） |
| **C. 单一全局状态模块** | `helpers.py` 657 行、12 个模块级赋值（其中 7 个是**可变状态**）、**4 处线程创建点**（抓包 / 增强抓包 / 一键流程 / 检测节拍），被 6 个模块 import —— 连 `demo/attack_sim.py` 都反向 import 它的私有队列 `_packet_queue` |

对应三个动作：**引入任务注册表 → 文档外移 → 状态收容器**。前端同步拆成「4 个区 + 模块化 controller + 单条 SSE」。

**预期收益**：Web Python 从 2822 行降到约 2000 行，编排端点 20 → 3，前端 `controls.js` 959 行绑 63 个 DOM id → 7 个模块各约 150 行，前端定时器 8 个 → 1 条 SSE + 2 个兜底。

---

## 2. 现状度量（全部为实测）

### 2.1 后端规模

| 文件 | 行数 | 其中 docstring | 路由 | `@_csrf_exempt` |
|------|-----:|---------------:|-----:|----------------:|
| `helpers.py` | **657** | 0 | 0 | 0 |
| `bp_admin.py` | 429 | 143（33%） | 8 | 5 |
| `bp_capture.py` | 374 | **215（57%）** | 15 | 9 |
| `bp_model.py` | 262 | 128（48%） | 6 | 3 |
| `bp_monitor.py` | 222 | 100（45%） | 5 | 0 |
| `database.py` | 251 | — | 0 | 0 |
| `app.py` | 227 | 0 | 1 | 1 |
| `auth.py` | 204 | — | 3 | 0 |
| `attack_sim_state.py` / `utils.py` / `limiter.py` / `sse.py` | 85/61/30/26 | — | 0 | 0 |
| **合计** | **2822** | **约 586** | **38** | **18** |

### 2.2 前端规模与耦合

| 项 | 实测 |
|----|------|
| `index.html` | 423 行、**6 个 tab、12 个面板、104 个 id**、0 个内联 `on*` 事件（这点做得好） |
| **控制页（`tab-control`）** | **10 个面板 / 229 行 = 全页面的 54%**，三个页签（总览/加密流量/告警）一个面板都没有 |
| `controls.js` | **959 行**，引用 **63 个 DOM id**（占页面 id 的 61%） |
| 其他 JS | `api.js` 172 / `charts.js` 125 / `alerts.js` 108 |
| 定时器 | `controls.js` 内 **8 个 `setInterval`** + **2 条 `EventSource`** 并存 |
| 构建 | 无（原生 `<script>` + 全局 `SN` 命名空间） |

控制页的 10 个面板：检测阈值配置、实时抓包、检测节拍、增强抓包、一键全流程、环境自检、系统健康、攻击模拟、模型管理、一键演示模式。（下划线是"这些本该分属不同分区"的直接证据。）

### 2.3 编排类端点：21 个，实为 8 组同构实现

> 口径说明：以下 8 组族端点合计 **21 个**；其中 `/api/model/list` 属该族但为只读列表，非启停/状态。
> 按「启动/停止/状态」精确口径统计为 **20 个**（启动 8 / 停止 5 / 状态 7）→ 收敛为 3 个。任务清单采用精确值。

```
POST/GET /api/capture/{start,stop,status}              ← 基础抓包
POST/GET /api/capture/{start-enhanced,stop-enhanced,enhanced-status}   ← 增强抓包（同构，仅参数不同）
POST/GET /api/detector/{start,stop,status}             ← 检测节拍
POST/GET /api/attack/{start,stop,status}               ← 攻击模拟
POST     /api/auto/{start} + GET /api/auto/status      ← 一键全流程
POST     /api/demo/start                               ← 一键演示
POST     /api/dual/{load,stop} + GET /api/dual/stats   ← ML 引擎
POST     /api/model/train + GET /api/model/train-status← 模型训练
```

`auto` 与 `demo` 的对比（源码已确认）：

| | `/api/auto/start` | `/api/demo/start` |
|---|---|---|
| 语义 | 增强抓包 → 训练 → ML 加载 | 抓包 + 攻击模拟 + ML 加载 |
| 实现 | `start_auto_thread(duration)` 自建线程 + 状态字典 + 锁 | 内联三步，**直接读 `_helpers._capture_running` 私有变量** |
| 冲突处理 | 返回 409 | 交给 `start_attack_sim` 的返回元组 |

两者都是「按顺序启动 N 个子任务、限时、可查询状态」——**同一个抽象，两套实现**。

### 2.4 配置三处真相

```
config.py 常量（7 个 *_THRESHOLD，含 HIGH_FREQ_IP_THRESHOLD）
      ↓ 导入期生成
helpers.CONFIG（9 个键，运行期可变，被 update_config 改写）
      ↓ 启动时覆盖
SQLite config 表（持久化 7 个阈值键）
```

实测差异：

- `HIGH_FREQ_IP_THRESHOLD` 存在于 `config.py`，**不在 `helpers.CONFIG`** → Web 界面无法调整它，也没有对外暴露
- `brute_force_window` 在 CONFIG 与 DB 中，但 `config.py` 里对应常量叫 `BRUTE_FORCE_WINDOW_SEC`（键名映射靠 §2.4 那段注释维持）
- `helpers.py:57` 与 `:84` 在 **import 期**就执行 `init_db()` 和读 DB 覆盖配置 → 导入即产生副作用，测试顺序敏感、`create_app()` 不存在

### 2.5 错误协议不统一

- `start_attack_sim()` 返回 `(dict, http_code)` 元组，调用方写 `err = ...; if err: return jsonify(err[0]), err[1]`
- 其他端点用 `jsonify({'status': 'error', ...}), 4xx`
- `demo/start` 在 ML 加载失败时 `logger.warning` 后**继续**返回 success
- `@_csrf_exempt` 18 处，语义是"条件豁免"（仅 Bearer 模式生效）——名字与实际行为不一致（已在检查报告 §七 F-1 记录）

### 2.6 改动风险面：Web 层测试覆盖偏低

| 模块 | 覆盖率 |
|------|-------:|
| `bp_admin.py` | **21%** |
| `helpers.py` | **23%** |
| `bp_model.py` | 32% |
| `auth.py` | 35% |
| `bp_capture.py` | 41% |
| `app.py` | 45% |
| `database.py` | 48% |
| `bp_monitor.py` | 64% |

**这些几乎全是本次重构要动的文件。** 所以阶段 0 不可跳过。

---

## 3. 目标架构

### 3.1 后端

```
src/campus_ids/
  config.py                  # 只声明默认值（dataclass Settings）
  runtime/                   # 新增：运行期基础设施
    settings.py              #   三层合并（默认 → DB → 运行期覆盖）的唯一读写入口
    events.py                #   进程内事件总线（线程安全），取代两个回调 list
    tasks.py                 #   任务注册表：注册/启动/停止/状态/超时看门狗
    state.py                 #   运行时句柄容器，取代 helpers 的模块级全局
  services/                  # 新增：业务用例，不依赖 Flask，可独立单测
    capture_service.py       #   基础 + 增强抓包合并为一套（参数化）
    detection_service.py     #   规则 + ML 双引擎 + 节拍
    model_service.py         #   训练 / 加载 / 注册表
    traffic_service.py       #   窗口、QPS、落库
    alert_service.py         #   冷却、落库、广播
    scenario_service.py      #   演练剧本（合并 attack / auto / demo）
  web/
    app.py                   # create_app() 工厂，消除 import 期副作用
    security.py              # @public / @readonly / @write 三档策略（认证+CSRF+限流一处定义）
    errors.py                # ApiError + 统一异常→JSON 处理器
    schemas.py               # 请求体校验（dataclass 或 pydantic 二选一）
    openapi.py               # apispec 自动生成文档，替代 586 行 docstring
    api/                     # 瘦蓝图：每端点 5–10 行
      system.py traffic.py alerts.py tasks.py models.py scenarios.py tls.py payload.py
```

### 3.2 前端

```
static/js/
  core/api.js          # fetch + CSRF + 错误归一（现有 api.js 演进）
  core/bus.js          # 单条 SSE 连接 + 事件订阅（取代 8 个 setInterval）
  core/ui.js           # $ / toast / 表格与表单渲染原语
  controllers/overview.js  tls.js  dual.js  alerts.js  payload.js
  controllers/tasks.js      # 任务中心（取代控制页那 10 个面板 / 229 行）
  controllers/settings.js   # 阈值配置
  controllers/system.js     # 自检 / 健康 / 模型管理
  main.js              # 按 tab 懒初始化 controller
```

**不加构建工具**：用原生 `<script type="module">`，内网部署不引入 npm。

### 3.3 核心抽象一：任务注册表

```python
# 语义示意，非最终实现
TASKS = {
    'capture':   Task(kind='continuous'),                 # 原 capture
    'capture_full': Task(kind='timed', default=60),        # 原 enhanced（参数化合并）
    'detection': Task(kind='continuous'),                  # 原 detector 节拍
    'ml':        Task(kind='continuous'),                  # 原 dual
    'scenario':  Task(kind='timed', script=[...]),         # 原 attack / auto / demo 合并为"剧本"
}
```

统一端点（20 → 3，族总数 21）：

| 新端点 | 替代 |
|--------|------|
| `GET  /api/tasks` | 8 个 `*/status`、`*/stats` |
| `POST /api/tasks/{name}/start` | 8 个 `*/start`、`*/load`、`/api/model/train` |
| `POST /api/tasks/{name}/stop` | 8 个 `*/stop` |

「剧本」模型把编排差异数据化：

```python
SCENARIOS = {
    'demo':    ['capture', 'ml', 'attack'],       # 原 /api/demo/start
    'full':    ['capture_full', 'train', 'ml'],   # 原 /api/auto/start
    'attack':  ['attack'],                        # 原 /api/attack/start
}
```

### 3.4 核心抽象二：安全策略三档

用显式装饰器取代 18 处语义含混的 `@_csrf_exempt`：

| 装饰器 | 语义 |
|--------|------|
| `@public` | 完全免认证/免 CSRF（仅 SSE、`/api/health`、页面） |
| `@readonly` | GET，需认证（若开启），免 CSRF |
| `@write` | 写操作：认证 + CSRF + 限流，**行为与模式无关**（Bearer / session 一致） |

### 3.5 信息架构重排（12 面板 → 4 区）

| 新分区 | 收纳现有面板 | panel 数 |
|--------|-------------|---------:|
| **观测** | 总览指标、流量图表、TLS 统计、双引擎统计、告警列表 | ~5 |
| **任务中心** | 实时抓包、检测节拍、增强抓包、ML 控制、攻击模拟、一键全流程、一键演示、模型训练 | **8 → 1 张表 + 1 个启动抽屉** |
| **配置** | 检测阈值配置、载荷检测（送检工具） | 2 |
| **系统** | 环境自检、系统健康、模型管理 | 3 |

关键动作：把控制页那 8 个"启动/停止按钮对"替换为**一张任务表**（名称 / 状态 / 运行时长 / 操作），前端原先 ~16 个按钮收敛成一个通用组件。控制页 229 行（占全页 54%）是本次信息架构重排的主要受力点。

### 3.6 明确不做（避免过度设计）

| 不做 | 理由 |
|------|------|
| 引入 React / Vue / 打包器 | 内网单机部署，原生 ESM 足够；引入构建链会让"改一行要 npm build" |
| 换 FastAPI / 拆微服务 | 单进程 + SQLite 是当前部署优势，Flask 不是瓶颈 |
| 引入 Celery / Redis | 现有线程 + 内存队列满足需求，加中间件徒增运维 |
| 换数据库 | SQLite + WAL 内网够用；并发写是"串行化"问题，不是"换库"问题 |
| 把蓝图层自动注册做成元编程 | 显式注册更易读、易调试 |

---

## 4. 分阶段路线

> 原则：**每阶段可独立发布、可回滚、行为可验证。**

### 阶段 0｜特征测试与契约冻结（前置，必做）

| 项 | 内容 |
|----|------|
| 目标 | 把 Web 层覆盖率从 21%–64% 提到 **≥70%**，先固化"当前行为"（含怪但有意的地方） |
| 动作 | ① 为 20 个编排端点补状态机测试（启动/重复启动 409/停止/超时）<br>② 为 `helpers.py` 的 `update_traffic_data`、`should_emit_alert`、`_drain_packets` 补测试<br>③ 导出 `apispec_1.json` 存为快照基准<br>④ 把 `test_endpoints.bat` 扩成可复跑的手工冒烟清单 |
| 验收 | 144 测试 + 新增测试全绿；apispec 快照生成成功 |
| 回滚 | 纯新增测试，无回滚风险 |

### 阶段 1｜结构搬迁（行为零变化）

| 项 | 内容 |
|----|------|
| 动作 | ① `helpers.py` 按职责拆为 `runtime/{settings,events,tasks,state}.py` + `services/*`，**原模块保留 re-export 兼容**<br>② 引入 `create_app()` 工厂，消除 import 期副作用（`init_db`、DB 配置覆盖移入工厂）<br>③ Swagger docstring 外移到 `openapi.py`（预计 -586 行）<br>④ 配置收敛为单一读写入口，`HIGH_FREQ_IP_THRESHOLD` 补进设置项 |
| 验收 | 测试全绿；`apispec_1.json` 快照**逐字节一致**；所有 API 路径/方法不变 |
| 回滚 | 保留兼容 re-export，单提交可 revert |

### 阶段 2｜API 收敛（带兼容层）

| 项 | 内容 |
|----|------|
| 动作 | ① 实现任务注册表 + 剧本模型<br>② 新增 `/api/tasks*`<br>③ 旧 21 个端点改为 shim，返回 `Deprecation: true` + `Sunset` 头，内部转发到任务层<br>④ 统一错误协议（`ApiError` + 全局 handler），消灭 `(dict, code)` 元组<br>⑤ 安全装饰器三档化，取代 18 处 `@_csrf_exempt` |
| 验收 | 新旧端点行为一致性测试通过（同一场景两种调用返回等价结果）；测试全绿 |
| 回滚 | 旧端点仍在（shim），可当次退回 |
| 风险提示 | **不要在演示/评审前做本阶段** |

### 阶段 3｜前端重构

| 项 | 内容 |
|----|------|
| 动作 | ① `controls.js` 拆为 `core/` + `controllers/`（原生 ESM，按 tab 懒初始化）<br>② 8 个轮询 → 1 条 SSE + 2 个 30s 兜底校验<br>③ 信息架构重排为 4 区，任务表取代 8 组按钮<br>④ 保留 `SN.*` 兼容别名一个版本，便于灰度 |
| 验收 | 手工冒烟清单逐项通过（此处必须真在浏览器里点，现有检查报告已把"未做浏览器验证"列为局限） |
| 回滚 | 保留旧 `controls.js` 一个版本，路由可切换 |

### 阶段 4｜清理与收口

| 项 | 内容 |
|----|------|
| 动作 | 删除兼容 shim、旧端点、`SN.*` 别名；更新 README / 操作手册 API 表与结构树；同步 `docs/checklist.md` |
| 验收 | 文档与代码一致（可用检查报告里的"文档漂移"核对脚本复验）；覆盖率不下降 |

---

## 5. 预期收益（量化）

| 指标 | 现状 | 目标 | 幅度 |
|------|-----:|-----:|-----:|
| `web/` Python 行数 | 2822 | ~2050 | −27% |
| 蓝图中的 docstring | 586 行 | 0（外置） | −586 |
| 编排端点 | 20（族总数 21，见 §2.3 口径说明） | 3（+ 兼容 shim） | −85% |
| 并发/状态机制 | 8 组各自实现 | 1 套任务注册表 | — |
| `helpers.py` | 657 行 / 12 个模块级赋值 / 4 处线程创建点 | 拆为 4 模块，可变状态收进 `RuntimeState` | — |
| `controls.js` | 959 行 / 63 id | 7 模块 × ~150 行 | −45% |
| 前端定时器 | 8 轮询 + 2 SSE | 1 SSE + 2 兜底 | −70% |
| 页面结构 | 6 tab / 12 面板（控制页独占 10 个、229 行） | 4 区 / ~10 面板，控制页 229 行 → ~40 行 | 控制页 −80% |
| Web 层覆盖率 | 21%–64% | ≥70%（阶段 0 达成） | — |

---

## 6. 风险登记册

| # | 风险 | 影响 | 对策 |
|---|------|------|------|
| R1 | 全局状态搬迁引入线程竞态（抓包线程 + 任务线程 + SQLite 写） | 数据丢失/重复 | 阶段 1 只搬迁不重写逻辑；写操作集中到 service 层串行化；搬完即跑并发冒烟 |
| R2 | SQLite 并发写（WAL 已开） | `database is locked` | 保持单写者；必要时引入写入队列并把超时调大 |
| R3 | API 收敛破坏外部脚本 / Swagger 使用者 | 调用方 400/404 | 兼容 shim + `Sunset` 头 + 文档同步（阶段 2 不删旧端点） |
| R4 | 前端重构后浏览器行为差异（图表、SSE 重连、Toast） | UI 回归 | 阶段 3 必须真机浏览器逐项冒烟；保留旧 JS 可切换 |
| R5 | SSE 订阅上限 20 | 多标签页时被拒 | 前端统一单连接天然降连接数；必要时按页签名复用 |
| R6 | 演示/评审临近 | 现场故障 | 阶段 2/3 避开演示窗口；阶段 1 随时可做（行为不变） |
| R7 | 重构期间上游继续提交 | 冲突 | 阶段 0 的测试即安全网；每阶段结束即合并，不长期开分支 |

---

## 7. 决策点（已拍板）

> **决策时间：2026-09-16。** 详细任务拆解见 [`docs/Web重写任务清单.md`](Web重写任务清单.md)。

| # | 问题 | 结论 | 与原建议 |
|---|------|------|---------|
| D1 | 目标形态：轻量重构还是重写？ | **重写**（换框架 + 前端工程化） | ⚠️ 与原建议不同（原建议轻量重构） |
| D2 | 兼容策略：旧端点保留多久？ | 保留 **1 个发布版本**，shim 带 `Sunset` 头后删除 | ✅ 一致 |
| D3 | 演练功能：`attack` / `auto` / `demo` 处理方式？ | **合并为「剧本」** | ✅ 一致 |
| D4 | 范围：是否包含模型/训练链路内部？ | **不包含**，只做 Web 层与它对接的边界 | ✅ 一致 |
| D5 | 演示/评审时间点约束？ | **时间充裕**，无窗口限制 | — |

**因 D1 变更而失效的条款**（本文档 §3.6「明确不做」，详见任务清单 §0.1）：

- 「不引入 React / Vue / 打包器」→ 作废
- 「不换 FastAPI」→ 作废（仍不拆微服务，保持单进程）
- 「蓝图注册不做元编程」→ 作废

**仍然有效的边界**：不引 Celery/Redis、不换数据库、不拆多服务、不动 `detector.py` 业务规则、不动 ML 内部。
**§4 的分阶段路线与 §6 风险登记册继续有效**，其"行为等价"的验收方式在重写路径下同样适用（见任务清单 §9）。

---

## 8. 最小可行版（如果暂时不想大改）

按投入产出比，只做这三件事也能拿到大部分收益：

1. **Swagger docstring 外移**（−586 行，改动机械、风险极低，`apispec` 快照可验证一致性）
2. **引入任务注册表**（20 → 3 端点，消除 8 组重复实现）
3. **`helpers.py` 拆分**（657 行 → 4 个 <200 行模块，全局状态收容器）

好处：不动前端、不动信息架构也能显著降复杂度；坏处：前端 8 个轮询与 63 个 id 的耦合仍在（阶段 3 的问题）。

---

## 9. 附录

### 9.1 复现本方案所有度量

```bash
cd /d/18551/AiC/SentinelNet
PY=/c/Users/18551/anaconda3/python.exe

# 文件规模 + docstring 占比 + 路由数 + @_csrf_exempt 数
$PY - <<'PY'
import pathlib, re, ast
web = pathlib.Path('src/campus_ids/web')
for p in sorted(web.glob('*.py')) + sorted(web.glob('bp_*.py')):
    src = p.read_text(encoding='utf-8'); tree = ast.parse(src)
    doc = sum(len(ast.get_docstring(n, clean=False).splitlines()) + 2
              for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module, ast.ClassDef))
              and ast.get_docstring(n, clean=False))
    print(f'{p.name:20s} {len(src.splitlines()):4d} 行  docstring {doc:4d}  '
          f'路由 {len(re.findall(r"^@[a-z_]+.route", src, re.M)):2d}  '
          f'csrf_exempt {len(re.findall("@_csrf_exempt", src))}')
PY

# 前端耦合：JS 引用 id 数 vs 页面 id 数（panel 用 panel-title 计数，避免匹配 panel-body 等）
$PY - <<'PY'
import pathlib, re
web = pathlib.Path('src/campus_ids/web')
html = (web / 'templates/index.html').read_text(encoding='utf-8')
print('页面 id:', len(set(re.findall(r'id="([^"]+)"', html))),
      '| 面板数:', len(re.findall(r'class="panel-title"', html)))
parts = re.split(r'<section class="tab-pane[^>]*id="([^"]+)"', html)
for i in range(1, len(parts), 2):
    seg = parts[i + 1]
    print(f'  {parts[i]:14s} 面板 {len(re.findall("panel-title", seg)):2d}  行数 {len(seg.splitlines()):4d}')
for p in sorted((web / 'static/js').glob('*.js')):
    if 'chart.umd' in p.name: continue
    t = p.read_text(encoding='utf-8', errors='ignore')
    print(f'  {p.name:14s} 引用 id {len(set(re.findall(chr(83) + chr(78) + r"\.\$\('([^']+)'\)", t))):3d}'
          f'  setInterval {len(re.findall("setInterval", t))}')
PY

# 现状覆盖率（本方案 §2.6 数据来源）
PYTHONPATH=/c/Users/18551/.workbuddy/binaries/python/covtools \
  $PY -m pytest tests/ -q --cov=campus_ids --cov-report=term --cov-branch; rm -f .coverage
```

### 9.2 阶段 1 的一致性验证方式（已实测可用）

Windows 下**不要**用 `/tmp`（Anaconda Python 看不到 Git Bash 的 `/tmp`），用项目内临时文件并在末尾清理：

```bash
cd /d/18551/AiC/SentinelNet
PY=/c/Users/18551/anaconda3/python.exe

# ① 搬迁前：导出 OpenAPI 契约快照
$PY - <<'PY'
import json, campus_ids.logging_config as lc; lc.setup_logging()
from campus_ids.web.app import app
spec = app.test_client().get('/apispec_1.json').get_json()
with open('apispec_snapshot_before.json', 'w', encoding='utf-8') as f:
    json.dump(spec, f, sort_keys=True, ensure_ascii=False, indent=1)
print('paths =', len(spec['paths']))
PY

# ② 搬迁后：同样导出为 apispec_snapshot_after.json，然后
diff apispec_snapshot_before.json apispec_snapshot_after.json && echo "契约未变 ✅"

# ③ 清理临时文件（避免污染工作区）
rm -f apispec_snapshot_before.json apispec_snapshot_after.json
```

实测基线：当前 `paths = 34`；同源快照 `diff` 为空，说明该手法可稳定检出契约漂移。
