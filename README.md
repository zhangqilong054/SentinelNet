# SentinelNet 哨兵网络 — 校园网加密流量入侵检测系统

基于 Python 的校园网加密流量入侵检测系统，集成抓包、特征提取、机器学习模型训练与实时 Web 监控面板。

## 系统架构

```
┌─────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
│  流量采集    │───▶│  特征提取     │───▶│  双引擎检测   │───▶│  Web 监控面板     │
│  Scapy/Npcap│    │  18 维特征    │    │  规则 + ML    │    │  FastAPI 实时仪表盘 │
│  TLS 解析   │    │  JA3 指纹     │    │  告警分级     │    │  API + 认证      │
└─────────────┘    └──────────────┘    └──────────────────┘    └──────────────────┘
                          │                     │                      │
                          ▼                     ▼                      ▼
                   ┌──────────────┐    ┌──────────────┐      ┌──────────────┐
                   │  模型训练     │    │  日志持久化   │      │  Docker 部署  │
                   │  RF/XGB/LGBM │    │  JSON Lines  │      │  一键启动     │
                   │  算法对比     │    │  Rotating    │      │  健康检查     │
                   └──────────────┘    └──────────────┘      └──────────────┘
```

**数据流**：抓包 → 特征提取（流级/TCP/端口/时间/加密流量）→ 双引擎检测（规则实时 + ML 周期聚合）→ 告警分级（高危/中危/低危）→ Web 面板实时展示 + 日志持久化

## 一、环境要求

| 项 | 要求 |
|----|------|
| Python | 3.10+（推荐 Anaconda 环境） |
| 操作系统 | Windows / Linux / macOS |
| 抓包驱动 | **Npcap**（Windows 必装，https://npcap.com/） |

> Windows 抓包需安装 Npcap，安装时勾选 *Install Npcap in WinPcap API-compatible Mode*。

## 二、安装步骤

### 1. 准备 Python 环境

确保已安装 Python 3.10+（推荐使用 Anaconda 或 Miniconda）。

### 2. 安装依赖

```bash
# 安装第三方依赖
pip install -r requirements.txt

# 安装为可编辑包（src layout 结构必需，使 campus_ids 可被 import）
pip install -e .
```

> **Windows 用户**：若 `pip` 或 `python` 指向了错误的解释器（例如系统自带 Python），请用目标 Python 的完整路径调用，例如 `C:\path\to\anaconda3\python.exe -m pip install -r requirements.txt`。
>
> **抓包依赖**：Windows 平台需额外安装 [Npcap](https://npcap.com/)，安装时勾选 *Install Npcap in WinPcap API-compatible Mode*。Linux/macOS 通常无需额外驱动。

### 3. 验证安装

```bash
python main.py
```

若输出用法说明，说明安装成功。若报 `ModuleNotFoundError: No module named 'campus_ids'`，说明可编辑包未正确安装或 Python 解释器不一致，请重新执行 `pip install -e .` 并确认使用的是同一个 Python 环境。

## 三、快速开始

安装完成后，启动 Web 面板即可体验全部功能：

```bash
python main.py
```

> 启动后访问 http://localhost:8000 ，所有功能（环境自检、抓包、训练、检测、攻击模拟）均通过 Web 面板操作。

> **前端模式**（`CAMPUS_IDS_FRONTEND`，2026-09-19 起默认 `new`）：
> `new` = Vue3 SPA（`frontend/dist`，完整交互界面，推荐）；`legacy` = Jinja2 只读壳（无操作按钮，仅作 API 冒烟与安全兜底）。
> 默认模式要求 `frontend/dist` 已构建（Docker 镜像与仓库内已含）；裸环境若未构建会启动报错并提示先执行 `cd frontend && npm install --legacy-peer-deps && npm run build`。

## 四、使用方法

启动 Web 面板：

```
python main.py app
```

启动后访问 http://localhost:8000，所有功能通过 Web 面板操作：

| 功能 | 面板入口 |
|------|----------|
| 环境自检 | 控制面板 → 环境自检 |
| 基础抓包（8 字段） | 控制面板 → 实时抓包控制 |
| 增强抓包（18 维特征） | 控制面板 → 增强抓包 |
| 模型训练 | 控制面板 → 模型管理 |
| 入侵检测 | 控制面板 → 双引擎检测 |
| 一键全流程 | 控制面板 → 一键全流程 |
| 一键演示 | 控制面板 → 一键演示模式 |
| 攻击模拟 | 控制面板 → 攻击模拟控制 |

> 旧版 CLI 子命令（check/auto/menu/capture/ecapture/train/detect/demo/attack_sim）已整合到 Web 面板。`main.py` 现仅接受 `app` 或无参数启动 Web 面板。

### 1. 抓包采集流量数据

通过 Web 面板操作：

- **基础抓包**：控制面板 → 实时抓包控制 → 开始抓包
  - 默认抓包 60 秒，可指定时长
  - 抓取的包直接入内存队列供实时检测使用，不输出 CSV 文件

- **增强抓包**（推荐用于模型训练）：控制面板 → 增强抓包 → 开始增强抓包
  - 在抓包同时按流聚合，输出 **18 维特征**（流级 / TCP 行为 / 端口 / 时间 / 加密流量 / 基础）并同步进行 TLS/JA3 分析
  - 输出格式与训练管线的 `ENHANCED_FEATURE_COLUMNS` 对齐

### 2. 训练检测模型

通过 Web 面板操作：控制面板 → 模型管理 → 训练模型

- 默认读取本地 `traffic_data.csv`；样本不足 100 条时自动回退到合成数据并给出明显警告（合成模型无实际检测能力）
- 可选数据源：`auto`（自动选择）、`local`（本地 CSV）、`cicids2017`、`nsl_kdd`
- 支持算法对比：随机森林 / XGBoost / LightGBM / MLP 神经网络，含 3 折交叉验证与 ROC-AUC
- 类别均衡处理（SMOTE 过采样 / class_weight / undersample 三种策略）
- **严格防泄漏**：先 80/20 分层划分训练/测试集，类别均衡仅在训练集上执行，所有模型统一使用 held-out 测试集评估
- 输出评估指标：准确率、精确率、召回率、F1、Per-Class F1、误报率 FPR、检测延迟、混淆矩阵
- 训练完成后模型保存为 `model.pkl`，评估报告保存为 `evaluation_report.txt`
- 自动生成 `train_data.csv` / `test_data.csv`（真实数据集时）

### 3. 入侵检测

通过 Web 面板操作：控制面板 → 双引擎检测

- 优先加载 `model.pkl` 对 `traffic_data.csv` 做预测并输出统计
- 若无可用模型，回退到规则检测演示（AnomalyDetector）

### 4. 启动 Web 监控面板

```
python main.py app
```

- 启动 FastAPI 服务，访问 http://localhost:8000
- 面板功能：
  - 实时流量指标（QPS、连接数、端口数、来源 IP 数、SYN/UDP/DNS 包数）
  - QPS 趋势图
  - 安全警报日志（颜色分级：高危红 / 中危橙 / 低危黄）
  - 攻击类型分布饼图
  - 检测准确率指标卡
  - **检测阈值配置**（DDoS / 端口扫描 / SYN 洪水 / UDP 洪水）
  - **实时抓包控制**（开始/停止，启用后展示真实流量数据）

### 5. 攻击模拟与演示

通过 Web 面板操作：

- **攻击模拟**：控制面板 → 攻击模拟控制
  - 支持 SYN Flood、端口扫描、UDP Flood、暴力破解、横向移动、全类型攻击
  - 可指定持续时长（暴力破解与横向移动仅 Web 面板可用，CLI 不支持）

- **一键演示**：控制面板 → 一键演示模式
  - 自动启动抓包并触发模拟攻击

也可通过命令行直接运行攻击模拟脚本：

```bash
# SYN Flood 模拟（默认 500 包）
python -m campus_ids.demo.attack_sim syn_flood --count 500

# 端口扫描模拟（默认 200 包）
python -m campus_ids.demo.attack_sim port_scan --count 200

# UDP Flood 模拟（默认 1000 包）
python -m campus_ids.demo.attack_sim udp_flood --count 1000

# DDoS 混合攻击模拟（默认 2000 包）
python -m campus_ids.demo.attack_sim ddos --count 2000

# 全类型攻击模拟（秒）
python -m campus_ids.demo.attack_sim all --duration 30

# 生成演示 pcap 文件
python -m campus_ids.demo.attack_sim generate_pcap --output demo_attacks.pcap
```

> 注：单类型攻击（syn_flood/port_scan/udp_flood/ddos/replay）使用 `--count` 参数（包数），
> `all` 使用 `--duration` 参数（秒），`generate_pcap` 使用 `--output` 参数。

## 五、项目结构

```
Task-main/
├── main.py                     # CLI 入口（仅 app 命令，所有功能通过 Web 面板操作）
├── pyproject.toml                 # 包声明（src layout）
├── requirements.txt               # 依赖清单
├── Dockerfile                     # Docker 容器构建
├── docker-compose.yml             # Docker Compose 一键部署
├── traffic_data.csv               # 增强抓包数据（运行时生成）
├── model.pkl                      # 当前模型（2026-09-19 起为 CICIDS2017 真实数据训练产物）
├── confusion_matrix.png           # 混淆矩阵图（训练时生成）
├── evaluation_report.txt          # 评估报告（训练时生成）
├── tests/                         # 单元测试（144 个，12 个文件）
│   ├── test_alert_cooldown.py     #   告警冷却测试（9 个）
│   ├── test_attack_sim.py         #   攻击模拟测试（17 个）
│   ├── test_detector.py           #   规则检测 + 双引擎测试（33 个）
│   ├── test_dual_confidence.py    #   双引擎置信度测试（7 个）
│   ├── test_enhanced_features.py  #   增强特征测试（20 个）
│   ├── test_grease_filter.py      #   GREASE 过滤测试（10 个）
│   ├── test_idle_flow.py          #   空闲流检测测试（8 个）
│   ├── test_queue_overflow.py     #   队列溢出测试（5 个）
│   ├── test_sse_endpoints.py      #   SSE 端点测试（6 个）
│   ├── test_tls_analyzer.py       #   TLS 分析测试（10 个）
│   ├── test_train.py              #   模型训练测试（11 个）
│   └── test_verification.py       #   验证测试（8 个）
├── docs/                          # 文档（checklist.md、操作手册.md）
└── src/campus_ids/
    ├── __init__.py
    ├── config.py                  # 集中配置常量（环境变量覆盖）
    ├── logging_config.py          # 日志系统（三级处理器 + JSON Lines）
    ├── capture/
    │   ├── enhanced_features.py   #   基础抓包 + 18 维流特征提取
    │   └── tls_analyzer.py        # TLS 加密流量分析（JA3 指纹）
    ├── model/
    │   ├── train.py               # 模型训练 + 多算法对比主流程（防泄漏 + --quick 快速模式）
    │   ├── data_loader.py         # 数据集加载（本地/CICIDS2017/NSL-KDD/合成）+ 类别均衡
    │   ├── evaluation.py          # 指标评估 + 交叉验证 + 混淆矩阵 + 延迟基准（向量化优化）
    │   └── utils.py               # 模型工具函数（class_weight 编码 + inf/nan 清理）
    ├── detector/
    │   ├── detector.py            # 规则异常检测（8 类规则）
    │   └── dual_detector.py       # 双引擎检测器（规则 + ML 融合）
    ├── demo/
    │   └── attack_sim.py          # 攻击模拟脚本
    └── web_new/
        ├── app.py                 # FastAPI 应用工厂 + lifespan + 路由注册
        ├── auth.py                # 认证（登录/登出/密码修改）
        ├── security.py            # 安全策略（@public/@readonly/@write + CSRF + 限流）
        ├── schemas.py             # 请求体校验
        ├── errors.py              # 统一异常处理
        ├── openapi.py             # OpenAPI 自动生成
        ├── pages.py               # 页面路由
        ├── api/                   # API 路由模块
        │   ├── admin.py           # 清理/导出
        │   ├── alerts.py          # 告警查询/统计
        │   ├── auth_routes.py     # 登录/登出/改密 API
        │   ├── models.py          # 模型管理
        │   ├── payload.py         # 载荷检测
        │   ├── scenarios.py       # 剧本编排
        │   ├── stream.py          # SSE 统一事件流
        │   ├── system.py          # 健康/自检/配置/CSRF
        │   ├── tasks.py           # 任务统一端点
        │   ├── tls.py             # TLS 分析
        │   └── traffic.py         # 流量查询
        ├── templates/             # Jinja 模板（legacy 只读壳；默认前端为 frontend/dist 的 Vue3 SPA）
        └── static/                # 静态资源（legacy 模式）
            ├── css/dashboard.css  # 设计 token + 组件样式
            └── js/
                ├── alerts.js      # 告警面板逻辑
                ├── api.js         # API 请求封装
                ├── charts.js      # 图表渲染
                ├── controls.js    # 控制面板逻辑
                └── chart.umd.min.js  # Chart.js 4.4.4（本地托管，断网可用）
```

## 五点五、前端（Vue3 SPA）

```
frontend/
├── src/views/                # 观测 / 任务中心 / 剧本 / 配置 / 系统 / 登录
├── src/api/                  # client.ts（CSRF 双提交 + 403 重试）+ endpoints.ts
├── src/composables/useSSE.ts # SSE：/api/stream 命名帧（alert/traffic）
├── dist/                     # 构建产物（npm run build；后端默认从这里托管）
└── e2e/                      # Playwright 真机冒烟（npm run e2e，9 用例）
```

## 六、检测能力

### 双引擎融合策略

| 检测引擎 | 触发条件 | 告警等级 |
|----------|----------|----------|
| 规则 + ML 同时触发 | 双引擎确认异常 | 高危 |
| 仅 ML 触发 | 可能是未知攻击模式 | 中危 |
| 仅规则触发 | 可能是已知攻击模式 | 低危 |

### 规则检测（8 类）

| 检测项 | 默认阈值 | 触发条件 | 环境变量 |
|--------|----------|----------|----------|
| DDoS | 500 QPS | 每秒请求数 > 阈值 | `CAMPUS_IDS_DDoS_THRESHOLD` |
| 端口扫描 | 50 端口 | 滑动窗口内访问不同目的端口数 > 阈值 | `CAMPUS_IDS_PORT_SCAN_THRESHOLD` |
| SYN 洪水 | 100 | TCP SYN 包数 > 阈值 | `CAMPUS_IDS_SYN_FLOOD_THRESHOLD` |
| UDP 洪水 | 200 | UDP 包数 > 阈值 | `CAMPUS_IDS_UDP_FLOOD_THRESHOLD` |
| SQL 注入 | 模式匹配 | HTTP 载荷匹配 SQL 注入模式 | — |
| XSS 攻击 | 模式匹配 | HTTP 载荷匹配 XSS 模式 | — |
| 暴力破解 | 10 次/60秒 | 同 IP 短时间多次连接敏感端口 | `CAMPUS_IDS_BF_THRESHOLD` |
| 横向移动 | 5 连接 | 内网 IP 间异常连接模式 | `CAMPUS_IDS_LATERAL_THRESHOLD` |

### 两套规则系统说明

系统存在两套规则判定路径，分工不同：

| 维度 | AnomalyDetector（实时路径） | vectorized_rule_predict（离线路径） |
|------|----------------------------|-------------------------------------|
| 调用方 | `dual_detector.py` 实时检测循环 | `enhanced_features.py` 训练打标、`evaluation.py` 离线评估 |
| 状态 | 有状态（暴力破解滑窗、横向移动追踪） | 无状态纯函数 |
| 粒度 | 逐 tick 聚合统计 | 批量 DataFrame 向量化 |
| DDoS 检测 | 基于 QPS（每秒包数） | 基于 pkt_count + duration + flow_pkt_per_sec 复合条件 |
| 端口扫描 | 基于唯一端口数 | 基于 dst_port_entropy |
| 暴力破解/横向移动 | ✅ 有状态追踪 | ❌ 不含（依赖时序窗口/内网拓扑） |
| 载荷检测 | ✅ SQL/XSS 正则 | ❌ 不含（依赖原始载荷） |
| Infiltration/Botnet | ❌ 不含 | ✅ 基于前向/后向包数比+窗口大小 |
| 输出 | `(bool, str)` 含告警消息 | `ndarray[bool]` 批量标签 |

> 阈值来源均为 `config.py`，但判定语义因路径不同而有差异。中期目标：抽为同一套可配置规则定义，实时与离线共用。

### 加密流量检测

- **JA3 指纹**：提取 TLS ClientHello 中的加密套件组合，生成哈希指纹
- **异常检测**：未知 JA3 指纹 / 罕见加密套件组合 → 标记可疑
- **SNI 提取**：解析 TLS 握手中的 Server Name Indication
- **TLS 版本检测**：识别过时 TLS 版本（TLS 1.0/1.1）

### 特征维度（18 维）

| 类别 | 特征 |
|------|------|
| 流级 | 平均包长、包长标准差、上下行字节比、包数、总字节 |
| TCP 行为 | SYN/FIN/RST/PSH 标志比例、窗口大小均值 |
| 端口 | 目标端口熵值 |
| 时间 | 包间隔均值、包间隔标准差 |
| 加密流量 | JA3 哈希编码、TLS 版本编码、加密套件数量 |
| 基础 | 包长度、持续时间 |

## 七、Web API 接口

> **迁移说明（v0.3.0）**：旧 Flask 层端点已删除，以下为当前 FastAPI 层端点。旧端点迁移映射见操作手册 §5。

### 任务控制（统一端点）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/tasks` | 获取所有任务状态 |
| POST | `/api/tasks/{name}/start` | 启动指定任务（name: capture / capture_full / detection / ml / scenario） |
| POST | `/api/tasks/{name}/stop` | 停止指定任务 |

### 剧本编排

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/scenarios` | 获取可用剧本列表 |
| POST | `/api/scenarios/start` | 启动指定剧本（name: demo / full / attack） |
| POST | `/api/scenarios/stop` | 停止当前剧本 |

### 流量与告警

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/traffic` | 获取实时流量指标 |
| GET | `/api/traffic/history` | 获取流量历史数据 |
| GET | `/api/alerts` | 获取最近告警（支持 level 筛选与分页） |
| GET | `/api/alerts/stats` | 获取告警统计 |
| GET | `/api/stream` | SSE 实时事件流（统一，取代旧 /api/stream/traffic + /api/stream/alerts） |

### 配置与运维

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/settings` | 获取当前检测阈值配置 |
| PUT | `/api/settings` | 更新检测阈值配置 |
| POST | `/api/cleanup` | 清理旧数据 |
| POST | `/api/export` | 导出流量数据到 CSV |
| GET | `/api/check` | 环境自检 |
| GET | `/api/health` | 系统健康检查（免认证） |
| GET | `/api/csrf-token` | 获取 CSRF 令牌 |

### TLS 与载荷

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/tls/analyze` | TLS 异常分析 |
| GET | `/api/tls/stats` | TLS 加密流量统计 |
| GET | `/api/tls/suspicious` | 可疑 TLS 记录 |
| POST | `/api/payload/check` | 载荷检测（旧路径，兼容） |
| POST | `/api/payload/analyze` | 载荷分析 |

### 模型管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/models` | 获取可用模型列表 |
| POST | `/api/models/train` | 启动模型训练 |
| GET | `/api/models/train/status` | 获取训练状态 |
| DELETE | `/api/models/{name}` | 删除指定模型 |

### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/login` | 会话登录 |
| POST | `/api/logout` | 会话登出 |
| POST | `/api/change-password` | 修改密码 |

### 页面

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 控制台首页 |
| GET | `/login` | 登录页 |
| POST | `/login` | 登录提交 |
| GET+POST | `/logout` | 登出 |
| GET+POST | `/change-password` | 修改密码页 |

> API 认证默认关闭，通过 `CAMPUS_IDS_AUTH_ENABLED=1` 启用。详见 [操作手册 §5](docs/操作手册.md)。

### 写操作认证方式

所有 `POST`/`PUT`/`DELETE` 端点受 CSRF 保护。会话模式下需先获取 CSRF 令牌：

```bash
# 1) 取 CSRF 令牌
TOKEN=$(curl -s http://localhost:8000/api/csrf-token | python -c "import sys,json;print(json.load(sys.stdin)['csrf_token'])")

# 2) 带 token 调用写操作
curl -X POST http://localhost:8000/api/cleanup \
  -H "X-CSRFToken: $TOKEN" \
  -H "Content-Type: application/json" -d '{"days": 30}'
```

设置了 `CAMPUS_IDS_API_TOKEN` 后转为 Bearer 模式，写操作豁免 CSRF：

```bash
curl -X POST http://localhost:8000/api/cleanup \
  -H "Authorization: Bearer $CAMPUS_IDS_API_TOKEN" \
  -H "Content-Type: application/json" -d '{"days": 30}'
```

## 八、模型评估

### 算法对比（CICIDS2017 DDoS 数据集，2026-09-12 实测）

> **注意**：~~当前交付的 `model.pkl` 来自 `synthetic_demo` 合成数据~~ **已于 2026-09-19 更新**：根目录 `model.pkl` 为 CICIDS2017 完整数据集（16 万样本）真实训练产物（RF，f1≈0.998，run `20260919_133750_xosxdw`）；`models/best.json` 仍指向 2026-09-12 的 f1=0.9998 run（按分数保留）。重新训练请通过 Web 面板 → 任务中心/系统 → 模型训练（入口需 `confirm=true` 且自动备份产物）。以下 CICIDS2017 指标为历史训练记录。

| 模型 | F1 | Attack F1 | FPR | 备注 |
|------|-----|-----------|-----|------|
| RandomForest | 0.9998 | 0.9998 | 0.0003 | |
| LogisticRegression | 0.9795 | 0.9823 | 0.0005 | |
| LightGBM | 0.9998 | 0.9998 | 0.0004 | |
| MLP | 0.9986 | 0.9988 | 0.0012 | |
| RF_CV | 0.9998±0.0001 | — | — | ROC-AUC=1.0000 |
| LGB_CV | 0.9998±0.0000 | — | — | ROC-AUC=1.0000 |
| XGB_CV | 0.9997±0.0000 | — | — | ROC-AUC=1.0000 |
| DualFusion（规则+RF） | 0.9998 | — | — | |
| RuleBaseline | 0.6139 | — | — | |

**跨数据集泛化**（以 DDoS 训练，在其他 CICIDS2017 子集上评估）：

| 测试数据集 | F1 | 备注 |
|------------|-----|------|
| PortScan | 0.9845 | |
| Friday-Morning | 0.9986 | |
| Thursday-Infilteration | 0.9996 | |
| Thursday-WebAttacks | 0.9807 | |
| Tuesday | 0.9535 | |
| Wednesday | 0.4964 | 攻击类型差异大 |

**训练管线性能优化**：

| 优化项 | 优化前 | 优化后 | 加速比 |
|--------|--------|--------|--------|
| 规则基线/融合评估 | iterrows 逐行判定 | numpy 布尔向量运算 | 10x+ |
| MLP 训练 | max_iter=300 | max_iter=150 + 更激进早停 | ~5x |
| 交叉验证 | 5 模型 × 5 折 | 3 模型 × 3 折 | ~2.8x |
| CICIDS2017 DDoS 端到端 | ~81 分钟 | ~4 分钟 | ~20x |
| 快速模式端到端 | — | ~3 秒 | ~1600x |

> **防泄漏说明**：所有模型统一使用 held-out 测试集评估（80/20 分层划分），类别均衡仅在训练集上执行，scaler 仅在训练集上 fit，交叉验证 ROC-AUC 使用 `cross_val_score` 而非全量数据拟合。完整输出见训练时生成的 `evaluation_report.txt`。

## 九、Docker 部署

```bash
# 构建并启动（模拟模式，无需 Npcap）
docker compose up --build

# 访问面板：http://localhost:8000
```

关键环境变量：`CAMPUS_IDS_AUTH_ENABLED`、`CAMPUS_IDS_API_TOKEN`、`CAMPUS_IDS_WEB_PORT`。

数据持久化：`model-data`（模型文件）、`log-data`（日志文件）。

> 完整 Docker 部署指南详见 [操作手册 §7](docs/操作手册.md)。

## 十、配置常量

所有配置集中在 `src/campus_ids/config.py`，支持环境变量覆盖（前缀 `CAMPUS_IDS_`）。

| 类别 | 关键配置项 | 环境变量示例 |
|------|-----------|-------------|
| Web 面板 | 端口 / 刷新间隔 | `CAMPUS_IDS_WEB_PORT` / `CAMPUS_IDS_REFRESH_MS` |
| 检测阈值 | DDoS / 端口扫描 / SYN 洪水 / UDP 洪水 | `CAMPUS_IDS_DDoS_THRESHOLD` 等 |
| ML 检测 | 检测间隔 / 缓冲区大小 | `CAMPUS_IDS_ML_INTERVAL` |
| 其他 | API 认证 | `CAMPUS_IDS_AUTH_ENABLED` / `CAMPUS_IDS_API_TOKEN` |

> 完整配置参数说明详见 [操作手册 §6](docs/操作手册.md)。

## 十一、日志系统

| 处理器 | 级别 | 输出 | 说明 |
|--------|------|------|------|
| 控制台 | INFO | stdout | 实时查看运行状态 |
| 全量文件 | DEBUG | `logs/app.log` | 轮转 10MB × 5 份 |
| 检测专用 | WARNING | `logs/detections.jsonl` | JSON Lines 格式，告警持久化 |

## 十二、测试

> **覆盖率**：当前 branch 模式覆盖率 **34%**（3501 语句 / 2211 未覆盖）。核心规则检测 `detector.py` 达 100%，但模型训练→评估链路（5%–17%）、Web 蓝图层（21%–32%）覆盖偏低，后续迭代建议优先补测试。

```bash
# 运行全部测试（144 个）
python -m pytest tests/ -v

# 运行覆盖率（branch 模式）
python -m pytest tests/ --cov=campus_ids --cov-report=term-missing --cov-branch

# 运行指定模块测试
python -m pytest tests/test_alert_cooldown.py -v     # 告警冷却（9 个）
python -m pytest tests/test_attack_sim.py -v          # 攻击模拟（17 个）
python -m pytest tests/test_detector.py -v            # 规则检测 + 双引擎（33 个）
python -m pytest tests/test_dual_confidence.py -v     # 双引擎置信度（7 个）
python -m pytest tests/test_enhanced_features.py -v   # 增强特征（20 个）
python -m pytest tests/test_grease_filter.py -v       # GREASE 过滤（10 个）
python -m pytest tests/test_idle_flow.py -v           # 空闲流检测（8 个）
python -m pytest tests/test_queue_overflow.py -v      # 队列溢出（5 个）
python -m pytest tests/test_sse_endpoints.py -v       # SSE 端点（6 个）
python -m pytest tests/test_tls_analyzer.py -v        # TLS 分析（10 个）
python -m pytest tests/test_train.py -v               # 模型训练（11 个）
python -m pytest tests/test_verification.py -v        # 验证测试（8 个）
```

> **注意**：若系统 Python 缺少 pytest，请使用 Anaconda Python 完整路径：
> `C:\path\to\anaconda3\python.exe -m pytest tests/ -v`

## 十三、典型工作流

```bash
# 启动 Web 面板（所有功能均通过面板操作）
python main.py

# 面板内操作流程：
# 1. 环境自检 → 确认环境就绪
# 2. 一键全流程 / 逐步操作：
#    - 增强抓包（60s）→ 模型训练 → ML 检测启动
#    - 或使用「一键全流程」自动完成
# 3. 攻击模拟 → 验证检测效果
```

# Docker 一键部署
docker compose up --build
```

## 十四、技术栈

| 组件 | 技术 |
|------|------|
| 抓包 | Scapy + Npcap |
| 特征提取 | NumPy + Pandas |
| 模型训练 | scikit-learn / XGBoost / LightGBM |
| 类别均衡 | imbalanced-learn (SMOTE) |
| Web 面板 | FastAPI + Vue 3 |
| 日志 | Python logging + RotatingFileHandler |
| 容器化 | Docker + Docker Compose |
| 测试 | pytest |
