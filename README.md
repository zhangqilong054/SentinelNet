# SentinelNet 哨兵网络 — 校园网加密流量入侵检测系统

基于 Python 的校园网加密流量入侵检测系统，集成抓包、特征提取、机器学习模型训练与实时 Web 监控面板。

## 系统架构

```
┌─────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
│  流量采集    │───▶│  特征提取     │───▶│  双引擎检测   │───▶│  Web 监控面板     │
│  Scapy/Npcap│    │  18 维特征    │    │  规则 + ML    │    │  Flask 实时仪表盘 │
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
python My_task.py
```

若输出用法说明，说明安装成功。若报 `ModuleNotFoundError: No module named 'campus_ids'`，说明可编辑包未正确安装或 Python 解释器不一致，请重新执行 `pip install -e .` 并确认使用的是同一个 Python 环境。

## 三、快速开始

安装完成后，三条命令即可体验完整功能：

```bash
# 1. 环境自检
python My_task.py check

# 2. 一键全流程（抓包→训练→检测→面板）
python My_task.py auto

# 3. 交互式菜单（数字选择，无需记忆命令）
python My_task.py menu
```

> 首次使用建议先运行 `check` 确认环境就绪，再运行 `auto` 完成全流程。

## 四、使用方法

所有命令通过 `My_task.py` 入口执行：

```
python My_task.py <command> [args]
```

### 命令速查表

| 命令 | 说明 | 示例 |
|------|------|------|
| `check` | 环境自检 | `python My_task.py check` |
| `auto [秒]` | 一键全流程 | `python My_task.py auto 60` |
| `menu` | 交互式菜单 | `python My_task.py menu` |
| `capture [秒]` | 基础抓包（8 字段） | `python My_task.py capture 60` |
| `ecapture [秒]` | 增强抓包（18 维特征） | `python My_task.py ecapture 60` |
| `train` | 模型训练 | `python My_task.py train` |
| `train --quick` | 快速训练（仅 RF+LGB） | `python My_task.py train --quick` |
| `detect` | 入侵检测 | `python My_task.py detect` |
| `app` | 启动 Web 面板 | `python My_task.py app` |
| `demo` | 一键演示 | `python My_task.py demo` |
| `attack_sim` | 攻击模拟 | `python My_task.py attack_sim all` |

### 1. 抓包采集流量数据

基础抓包：

```
python My_task.py capture [秒数]
```

- 默认抓包 60 秒，可指定时长（如 `capture 30`）
- 输出文件：`traffic_data.csv`，包含 8 个基础字段：`Src_IP, Dst_IP, Src_Port, Dst_Port, Protocol, Length, Timestamp, Label`
- `Label` 字段基于启发式规则自动打标：同一源 IP 出现次数 > 50 或访问不同目的端口数 > 20 标记为 `Attack`，否则为 `Normal`

增强抓包（推荐用于模型训练）：

```
python My_task.py ecapture [秒数]
```

- 在抓包同时按流聚合，输出 **18 维特征**（流级 / TCP 行为 / 端口 / 时间 / 加密流量 / 基础）并同步进行 TLS/JA3 分析
- 输出格式与训练管线的 `ENHANCED_FEATURE_COLUMNS` 对齐

### 2. 训练检测模型

```
python My_task.py train [--dataset NAME] [--quick]
```

- 默认读取本地 `traffic_data.csv`；样本不足 100 条时自动回退到合成数据并给出明显警告（合成模型无实际检测能力）
- 可指定数据源：`python My_task.py train --dataset cicids2017|nsl_kdd|synthetic`，也可直接传数据集文件路径
- `--quick` 快速模式：仅训练 RF+LGB，跳过 CV/融合/跨数据集评估，CICIDS2017 DDoS 约 3 秒完成
- 支持算法对比：随机森林 / XGBoost / LightGBM / MLP 神经网络 / 逻辑回归 / 规则基线，含 3 折交叉验证与 ROC-AUC
- 类别均衡处理（SMOTE 过采样 / class_weight / undersample 三种策略）
- **严格防泄漏**：先 80/20 分层划分训练/测试集，类别均衡仅在训练集上执行，所有模型统一使用 held-out 测试集评估
- 输出评估指标：准确率、精确率、召回率、F1、Per-Class F1、误报率 FPR、检测延迟、混淆矩阵
- 训练完成后模型保存为 `model.pkl`，评估报告保存为 `evaluation_report.txt`
- 自动生成 `train_data.csv` / `test_data.csv`（真实数据集时）

**训练模式对比**：

| 模式 | 训练模型 | 典型耗时（CICIDS2017 DDoS） |
|------|----------|---------------------------|
| 完整模式（默认） | RF + LR + XGB + LGB + MLP | ~4 分钟 |
| 快速模式（`--quick`） | RF + LGB | ~3 秒 |

### 3. 入侵检测

```
python My_task.py detect
```

- 优先加载 `model.pkl` 对 `traffic_data.csv` 做预测并输出统计
- 若无可用模型，回退到规则检测演示（AnomalyDetector）

### 4. 启动 Web 监控面板

```
python My_task.py app
```

- 启动 Flask 服务，访问 http://localhost:5000
- 面板功能：
  - 实时流量指标（QPS、连接数、端口数、来源 IP 数、SYN/UDP/DNS 包数）
  - QPS 趋势图
  - 安全警报日志（颜色分级：高危红 / 中危橙 / 低危黄）
  - 攻击类型分布饼图
  - 检测准确率指标卡
  - **检测阈值配置**（DDoS / 端口扫描 / SYN 洪水 / UDP 洪水）
  - **实时抓包控制**（开始/停止，启用后展示真实流量数据）

### 5. 攻击模拟与演示

```bash
# SYN Flood 模拟
python -m campus_ids.demo.attack_sim syn_flood --count 500

# 端口扫描模拟
python -m campus_ids.demo.attack_sim port_scan --count 200

# UDP Flood 模拟
python -m campus_ids.demo.attack_sim udp_flood --count 1000

# 全类型攻击模拟（30 秒）
python -m campus_ids.demo.attack_sim all --duration 30

# 生成演示 pcap 文件
python -m campus_ids.demo.attack_sim generate_pcap --output demo_attacks.pcap
```

一键演示（自动启动 Web 面板并触发模拟攻击）：

```bash
python My_task.py demo --duration 60          # 60 秒全流程演示
python My_task.py demo --no-browser           # 不自动打开浏览器
python My_task.py demo --no-attack            # 只启动面板，不模拟攻击
```

### 6. 简化操作

为降低使用门槛，提供三个简化命令，无需记忆子命令即可完成核心操作：

#### 环境自检

一键检查运行环境是否就绪：

```bash
python My_task.py check
```

检查项包括：
- Python 版本（需 ≥3.10）
- 必需依赖包（scapy / flask / scikit-learn / joblib / pandas / numpy）
- 可选依赖包（xgboost / lightgbm — 缺失不影响基本功能）
- Npcap/libpcap 抓包权限
- 模型文件（model.pkl / evaluation_report.txt）
- 数据文件（traffic_data.csv / traffic_stats.csv）

#### 一键全流程

自动执行「增强抓包 → 模型训练 → 入侵检测 → 启动 Web 面板」完整流水线：

```bash
python My_task.py auto           # 默认抓包 30 秒
python My_task.py auto 60        # 指定抓包 60 秒
```

- 每步显示进度提示，出错时自动回退（如抓包失败则尝试使用已有数据）
- 适合首次使用或快速验证全流程

#### 交互式菜单

数字选择操作，无需记忆命令参数：

```bash
python My_task.py menu
```

菜单选项：

```
  1. 环境自检          6. 启动 Web 面板 (app)
  2. 基础抓包 (capture)  7. 一键演示 (demo)
  3. 增强抓包 (ecapture) 8. 一键全流程 (auto)
  4. 模型训练 (train)    9. 退出
  5. 入侵检测 (detect)
```

- 选择后交互输入参数（如抓包时长、数据集名称），回车使用默认值
- 操作完成后自动返回菜单

## 五、项目结构

```
Task-main/
├── My_task.py                     # CLI 入口（capture/ecapture/train/detect/app/demo/attack_sim/auto/menu/check）
├── pyproject.toml                 # 包声明（src layout）
├── requirements.txt               # 依赖清单
├── Dockerfile                     # Docker 容器构建
├── docker-compose.yml             # Docker Compose 一键部署
├── traffic_data.csv               # 抓包数据（运行时生成）
├── model.pkl                      # 训练模型（CICIDS2017, RF, Attack F1=0.9998）
├── confusion_matrix.png           # 混淆矩阵图（训练时生成）
├── evaluation_report.txt          # 评估报告（训练时生成）
├── tests/                         # 单元测试（67 个）
│   ├── test_detector.py           #   规则检测测试（29 个）
│   ├── test_enhanced_features.py  #   增强特征测试（20 个）
│   ├── test_features.py           #   基础特征提取测试（7 个）
│   └── test_train.py              #   模型训练测试（11 个）
├── docs/                          # 文档（checklist.md、操作手册.md）
└── src/campus_ids/
    ├── __init__.py
    ├── config.py                  # 集中配置常量（环境变量覆盖）
    ├── logging_config.py          # 日志系统（三级处理器 + JSON Lines）
    ├── capture/
    │   ├── features.py            # 基础抓包 + 8 字段输出 + 启发式打标
    │   ├── enhanced_features.py   # 增强特征提取（18 维流特征）
    │   └── tls_analyzer.py        # TLS 加密流量分析（JA3 指纹）
    ├── model/
    │   ├── train.py               # 模型训练 + 多算法对比主流程（防泄漏 + --quick 快速模式）
    │   ├── data_loader.py         # 数据集加载（本地/CICIDS2017/NSL-KDD/合成）+ 类别均衡
    │   └── evaluation.py          # 指标评估 + 交叉验证 + 混淆矩阵 + 延迟基准（向量化优化）
    ├── detector/
    │   ├── detector.py            # 规则异常检测（8 类规则）
    │   └── dual_detector.py       # 双引擎检测器（规则 + ML 融合）
    ├── demo/
    │   ├── attack_sim.py          # 攻击模拟脚本
    │   └── run_demo.py            # 一键演示流程
    └── web/
        ├── app.py                 # Flask 路由与 API 端点
        ├── helpers.py             # 全局状态、抓包线程、流量更新与保存
        ├── templates/
        │   └── index.html         # 仪表盘 Jinja 模板（6 页签）
        └── static/
            ├── css/dashboard.css  # 设计 token + 组件样式
            └── js/
                ├── dashboard.js   # 轮询/图表/告警/控制/载荷检测
                └── chart.umd.min.js  # Chart.js 4.4.4（本地托管，断网可用）
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

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/traffic` | 获取实时流量指标 |
| GET | `/api/alerts` | 获取最近 20 条警报 |
| GET | `/api/config` | 获取当前检测阈值配置 |
| POST | `/api/config` | 更新检测阈值 |
| GET | `/api/save` | 保存流量统计到 CSV |
| POST | `/api/capture/start` | 启动后台抓包线程 |
| POST | `/api/capture/stop` | 停止后台抓包线程 |
| GET | `/api/capture/status` | 查询抓包状态 |
| GET | `/api/tls/stats` | TLS 加密流量统计 |
| GET | `/api/tls/suspicious` | 可疑 TLS 记录 |
| GET | `/api/dual/stats` | 双引擎检测统计 |
| POST | `/api/dual/load` | 加载 ML 模型 |
| POST | `/api/dual/stop` | 停止 ML 预测 |
| POST | `/api/payload/check` | 载荷检测（SQL 注入 / XSS） |

> API 认证默认关闭，通过 `CAMPUS_IDS_AUTH_ENABLED=1` 启用。详见 [操作手册 §5](docs/操作手册.md)。

## 八、模型评估

### 算法对比（CICIDS2017 DDoS 数据集，2026-09-12 实测）

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

# 访问面板：http://localhost:5000
```

关键环境变量：`CAMPUS_IDS_DEMO_MODE=1`（Docker 中必须启用）、`CAMPUS_IDS_AUTH_ENABLED`、`CAMPUS_IDS_API_TOKEN`、`CAMPUS_IDS_WEB_PORT`。

数据持久化：`model-data`（模型文件）、`log-data`（日志文件）。

> 完整 Docker 部署指南详见 [操作手册 §7](docs/操作手册.md)。

## 十、配置常量

所有配置集中在 `src/campus_ids/config.py`，支持环境变量覆盖（前缀 `CAMPUS_IDS_`）。

| 类别 | 关键配置项 | 环境变量示例 |
|------|-----------|-------------|
| Web 面板 | 端口 / 刷新间隔 | `CAMPUS_IDS_WEB_PORT` / `CAMPUS_IDS_REFRESH_MS` |
| 检测阈值 | DDoS / 端口扫描 / SYN 洪水 / UDP 洪水 | `CAMPUS_IDS_DDoS_THRESHOLD` 等 |
| ML 检测 | 检测间隔 / 缓冲区大小 | `CAMPUS_IDS_ML_INTERVAL` |
| 其他 | 模拟模式 / API 认证 | `CAMPUS_IDS_DEMO_MODE` / `CAMPUS_IDS_AUTH_ENABLED` |

> 完整配置参数说明详见 [操作手册 §6](docs/操作手册.md)。

## 十一、日志系统

| 处理器 | 级别 | 输出 | 说明 |
|--------|------|------|------|
| 控制台 | INFO | stdout | 实时查看运行状态 |
| 全量文件 | DEBUG | `logs/app.log` | 轮转 10MB × 5 份 |
| 检测专用 | WARNING | `logs/detections.jsonl` | JSON Lines 格式，告警持久化 |

## 十二、测试

```bash
# 运行全部测试（67 个）
python -m pytest tests/ -v

# 运行指定模块测试
python -m pytest tests/test_detector.py -v            # 规则检测（29 个）
python -m pytest tests/test_enhanced_features.py -v   # 增强特征（20 个）
python -m pytest tests/test_features.py -v            # 基础特征提取（7 个）
python -m pytest tests/test_train.py -v               # 模型训练（11 个）
```

> **注意**：若系统 Python 缺少 pytest，请使用 Anaconda Python 完整路径：
> `C:\path\to\anaconda3\python.exe -m pytest tests/ -v`

## 十三、典型工作流

```bash
# 方式一：一键全流程（推荐首次使用）
python My_task.py check          # 环境自检
python My_task.py auto 60        # 抓包60秒→训练→检测→面板

# 方式二：交互式菜单
python My_task.py menu

# 方式三：逐步执行（精细控制）
python My_task.py ecapture 60    # 增强抓包
python My_task.py train          # 模型训练（完整模式，约4分钟）
python My_task.py train --quick  # 模型训练（快速模式，约3秒）
python My_task.py detect         # 入侵检测
python My_task.py app            # 启动面板

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
| Web 面板 | Flask + Chart.js |
| 日志 | Python logging + RotatingFileHandler |
| 容器化 | Docker + Docker Compose |
| 测试 | pytest |
