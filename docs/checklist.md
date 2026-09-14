# Web 应用化改造清单

> 勾选框 `[ ]` 表示待完成，`[x]` 表示已完成。
> 优先级：P0（必须）→ P1（推荐）→ P2（建议）

---

## P0 — 生产级部署

### M1 — WSGI 服务器替换

- [x] `requirements.txt` 添加 `waitress>=2.1`（Windows 原生支持）
- [x] `requirements.txt` 添加 `flask-talisman>=1.0`（HTTPS 强制）
- [x] `app.py` 的 `run_app()` 替换 `app.run()` 为 `waitress.serve()`
- [x] `Dockerfile` 添加 Gunicorn 入口（Linux 容器生产部署）
- [x] `docker-compose.yml` 添加 Gunicorn 配置

### M2 — 数据持久化（SQLite）

- [x] 新建 `src/campus_ids/web/database.py` — SQLite 数据层
  - [x] `alerts` 表：时间、级别、攻击类型、置信度、描述
  - [x] `traffic_history` 表：时间戳、QPS、连接数、包计数、端口数、源IP数、告警
  - [x] `config` 表：键值对存储检测阈值
  - [x] `models` 表：模型版本注册（替代 `registry.json`）
  - [x] 应用启动时自动建表（`init_db()` 函数）
- [x] 修改 `helpers.py`：`update_traffic_data()` 写入 SQLite 而非内存列表
- [x] 修改 `helpers.py`：告警写入改用数据库插入
- [x] 修改 `app.py`：`/api/alerts` 改为数据库查询（支持分页）
- [x] 修改 `app.py`：`/api/config` POST 持久化到数据库
- [x] 修改 `app.py`：`/api/model/list` 改为数据库查询
- [x] 添加历史数据清理策略（保留最近 7 天）

---

## P1 — 实时通信与安全加固

### M3 — Server-Sent Events 实时推送

- [x] `app.py` 新增 `/api/stream/traffic` SSE 端点（替代轮询）
- [x] `app.py` 新增 `/api/stream/alerts` SSE 端点（告警实时推送）
- [x] `dashboard.js` 改用 `EventSource` 替代 `setInterval(fetch)`
- [x] 保留 REST API 作为降级方案（SSE 连接失败时回退轮询）
- [x] SSE 连接心跳保活（每 30 秒发送 `:keepalive\n\n`）

### M4 — 安全加固

- [x] `requirements.txt` 添加 `flask-login>=0.6`、`flask-cors>=4.0`、`flask-limiter>=3.0`、`flask-wtf>=1.2`
- [x] 新建 `src/campus_ids/web/auth.py` — 用户认证模块
  - [x] User 模型（SQLite 存储，用户名/密码哈希）
  - [x] 登录/登出路由（`/login`、`/logout`）
  - [x] `@login_required` 装饰器保护 API 端点
- [x] 新建 `src/campus_ids/web/templates/login.html` — 登录页面
- [x] `app.py` 添加 Flask-CORS 配置（限制允许来源）
- [x] `app.py` 添加 Flask-Limiter 速率限制（60 次/分钟默认）
- [x] `app.py` 添加 CSRF 保护（Flask-WTF）
- [x] `app.py` 添加安全响应头（CSP、HSTS、X-Frame-Options）
- [x] `app.py` 添加 Flask-Login 会话认证（`CAMPUS_IDS_LOGIN_ENABLED=1` 启用）

---

## P2 — 前端优化与运维增强

### M5 — 前端优化

- [x] 拆分 `dashboard.js` 为模块：
  - [x] `api.js` — API 封装与错误处理（SN 命名空间、DOM 辅助、Toast、API 拦截器、连接状态）
  - [x] `charts.js` — 图表渲染逻辑（QPS 折线图、攻击饼图）
  - [x] `alerts.js` — 告警列表与筛选（告警状态、筛选按钮、SSE 处理、DOM 渲染）
  - [x] `controls.js` — 控制面板交互（SSE 管理、轮询降级、Tab 切换、阈值配置、骨架屏、入口点）
- [x] `index.html` 更新 script 标签：加载 4 个新模块 + `SN.initControls()` 调用
- [x] 添加全局 Toast 通知组件（成功/警告/错误）
- [x] 添加 API 响应拦截器（统一处理 401/403/500）
- [x] 添加加载状态骨架屏
  - [x] HTML: `class="skeleton" data-section="overview|tls|dual"` 属性
  - [x] CSS: `.skeleton` 脉冲动画 + `.skeleton-loaded` 过渡
  - [x] JS: `SN.hideSkeleton(section)` 移除骨架类
- [x] 完善移动端响应式适配
  - [x] 768px 断点：顶栏纵向排列、Tab 横向滚动、卡片 2 列、配置单列
  - [x] 480px 断点：卡片单列、按钮全宽、字号调整

### M6 — 运维增强

- [x] `requirements.txt` 添加 `python-dotenv>=1.0`、`flasgger>=0.9`、`psutil>=5.9`
- [x] 新建 `.env.example` — 列出所有可配置环境变量及默认值
- [x] `app.py` 添加信号处理（SIGTERM 优雅关闭：停止抓包+ML循环）
- [x] `app.py` 添加 `/api/health` 详细健康检查端点
  - [x] 数据库连接状态
  - [x] 抓包线程状态
  - [x] ML 模型加载状态
  - [x] 内存使用量
  - [x] 运行时长
  - [x] SSE 订阅者数量
- [x] `app.py` 添加 `/api/check` 环境自检端点（Python 版本/依赖/Npcap/模型/数据）
- [x] `app.py` 添加增强抓包端点（`/api/capture/start-enhanced`、`/api/capture/stop-enhanced`、`/api/capture/enhanced-status`）
- [x] `app.py` 添加一键全流程端点（`/api/auto/start`、`/api/auto/status`）
- [x] 各 API 端点添加 Flasgger 注解（自动生成 Swagger 文档）
  - [x] Flasgger 初始化 + Swagger UI 入口 `/apidocs/`
  - [x] 25 个 API 端点全部添加 YAML docstring 注解
- [x] `config.py` 支持 `.env` 文件加载（`python-dotenv`）

---

## 需修改的文件清单

| 文件 | 改动 | 模块 | 状态 |
|------|------|------|------|
| `requirements.txt` | 添加 waitress, flask-talisman, flask-login, flask-cors, flask-limiter, flask-wtf, python-dotenv, flasgger, psutil | M1/M4/M6 | ✅ |
| `pyproject.toml` | 同步 requirements.txt 依赖 | M1/M4/M6 | ✅ |
| `src/campus_ids/web/app.py` | WSGI 替换、SSE 端点、安全中间件、信号处理、健康检查、Flasgger 注解 | M1/M3/M4/M6 | ✅ |
| `src/campus_ids/web/helpers.py` | 数据库写入替代内存列表 | M2 | ✅ |
| **新建** `src/campus_ids/web/database.py` | SQLite 数据层（建表/CRUD/清理/用户表） | M2/M4 | ✅ |
| **新建** `src/campus_ids/web/auth.py` | 用户认证模块 | M4 | ✅ |
| **新建** `src/campus_ids/web/templates/login.html` | 登录页面 | M4 | ✅ |
| **新建** `src/campus_ids/web/static/js/api.js` | SN 命名空间、DOM 辅助、Toast、API 拦截器 | M5 | ✅ |
| **新建** `src/campus_ids/web/static/js/charts.js` | Chart.js 图表模块 | M5 | ✅ |
| **新建** `src/campus_ids/web/static/js/alerts.js` | 告警列表与筛选模块 | M5 | ✅ |
| **新建** `src/campus_ids/web/static/js/controls.js` | 控制面板交互 + 启动入口 | M5 | ✅ |
| `src/campus_ids/web/templates/index.html` | 骨架屏属性、script 标签更新 | M5 | ✅ |
| `src/campus_ids/web/static/css/dashboard.css` | Toast 样式、骨架屏样式、移动端响应式 | M5 | ✅ |
| `src/campus_ids/web/static/js/dashboard.js` | 旧单体文件（已被 4 个模块替代） | M5 | ✅ |
| `src/campus_ids/config.py` | python-dotenv .env 文件加载 | M6 | ✅ |
| **新建** `.env.example` | 环境变量模板 | M6 | ✅ |
| `Dockerfile` | Gunicorn 入口 | M1 | ✅ |
| `docker-compose.yml` | Gunicorn 配置 | M1 | ✅ |

---

## 完成统计

| 模块 | 总任务 | 已完成 | 进度 |
|------|--------|--------|------|
| M1 — WSGI 服务器替换 | 5 | 5 | 100% |
| M2 — 数据持久化 | 11 | 11 | 100% |
| M3 — SSE 实时推送 | 5 | 5 | 100% |
| M4 — 安全加固 | 9 | 9 | 100% |
| M5 — 前端优化 | 7 | 7 | 100% |
| M6 — 运维增强 | 10 | 10 | 100% |
| **合计** | **47** | **47** | **100%** |