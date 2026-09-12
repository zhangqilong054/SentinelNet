# 校赛冲刺总表（合并版）

> 合并《校赛改进清单》与《checklist.md》，按优先级排列。
> 勾选框 `[ ]` 表示待完成，`[x]` 表示已完成。
> 原则：名不副实的事先做，没有量化结果的事先做，评委一眼能看到的事先做。

---

## 待完成

- [ ] 答辩 PPT / 演示文稿
  - 问题背景：校园网安全现状 + 加密流量检测难题
  - 系统架构图：抓包 → 特征提取 → 模型检测 → 规则检测 → 告警 全链路
  - 技术创新点：JA3 指纹、多维度特征、规则 + ML 双引擎
  - 实验结果：数据集说明、评估指标、对比实验图表
  - 现场演示：实时攻击检测全链路
  - 未来展望

---

## 训练管线数据泄漏修复（2026-09-12）

> 代码审查发现训练管线存在多处数据泄漏和逻辑缺陷，已全部修复并通过 67 项单元测试 + 端到端训练验证。

- [x] **P0 数据泄漏**：`train()` 调用 `split_and_save_dataset()` 的结果被丢弃，`train_model()` 对全量数据重新划分 → 修复：使用划分结果，仅训练集做类别均衡，统一 held-out 测试集评估
- [x] **P0 外部测试集**：`train_model()` 不支持外部测试集 → 修复：新增 `X_test`/`y_test` 参数，scaler 仅 fit 训练集
- [x] **P1 融合评估泄漏**：`_evaluate_dual_fusion()` 在全量数据上训练+预测 → 修复：接收 `X_test`/`y_test` 参数，仅在测试集上评估
- [x] **P2 交叉验证冗余**：`cross_validate_models()` 先调用 `train_model()`（结果未使用）再创建新分类器 → 修复：移除冗余调用；ROC-AUC 改用交叉验证而非全量拟合
- [x] **P3 多分类安全**：XGBoost/LightGBM 的 `scale_pos_weight` 假设二分类 → 修复：多分类时使用 `sample_weight` 替代
- [x] **P4 评估不一致**：规则基线在全量数据评估，ML 模型在 20% 测试集评估 → 修复：统一使用 held-out 测试集
- [x] **P5 过度加权**：SMOTE/undersample 后仍用 `class_weight="balanced"` → 修复：`balance_classes()` 返回 `False` 表示不加权
  - 问题背景：校园网安全现状 + 加密流量检测难题
  - 系统架构图：抓包 → 特征提取 → 模型检测 → 规则检测 → 告警 全链路
  - 技术创新点：JA3 指纹、多维度特征、规则 + ML 双引擎
  - 实验结果：数据集说明、评估指标、对比实验图表
  - 现场演示：实时攻击检测全链路
  - 未来展望

---

## 已完成归档

### P0 — 必做（基础质量 + 核心竞争力 + 答辩不翻车）✅ 全部完成

#### 1. Bug 修复与基础质量

- [x] **#1 SYN 计数逻辑错误** — `app.py` 中按 TCP SYN 标志位过滤，而非所有 TCP 包
- [x] **#2 DNS 计数恒为 0** — `_capture_worker` 中检测目标端口 53 的 UDP 包
- [x] **#3 `feature_list` 全局变量累积** — 改为函数局部变量，每次抓包前清空

#### 2. 加密流量分析

- [x] TLS ClientHello 解析 + JA3 指纹提取 → `tls_analyzer.py`
- [x] SNI 提取、TLS 版本、加密套件列表
- [x] JA3 指纹异常检测（未知指纹 / 罕见加密套件 → 可疑）
- [x] Web 面板加密流量指标展示（TLS 版本分布、异常 SNI）
- [x] 加密流量 vs 明文流量检测区别可答辩

#### 3. 特征工程扩展

- [x] 流级特征：平均包长、包长标准差、上下行字节比、包数、总字节
- [x] TCP 行为特征：SYN/FIN/RST/PSH 标志比例、窗口大小均值
- [x] 端口特征：目标端口熵值
- [x] 时间特征：包间隔均值和方差
- [x] 加密流量特征：JA3 哈希编码、TLS 版本编码、加密套件数量
- [x] 特征总数 18 维，统一管理 `FEATURE_NAMES`
- [x] 替换仅用 Length+Duration 的训练流程 → `enhanced_features.py`

#### 4. 数据集与模型评估

- [x] 公开数据集加载：CICIDS2017 / NSL-KDD → `load_cicids2017()` / `load_nsl_kdd()`
- [x] 数据集加载与预处理模块 → `load_dataset()` 统一入口
- [x] CICIDS2017 真实数据集训练完成（8 文件 2.83M 条，采样 800K，13 特征映射）
  - Attack F1: **0.9828**（合成数据 0.00 → 真实数据 0.9828）
  - Normal F1: 1.00 | 整体准确率: 0.99 | Attack Recall: 1.00
  - 训练集 640K / 测试集 160K（分层 80/20 划分）
  - 模型: RandomForest（200 棵树, class_weight=balanced, max_depth=20）
- [x] 评估指标输出：准确率、精确率、召回率、F1 → `evaluate_model()`
- [x] 混淆矩阵保存为图片 → `confusion_matrix.png`
- [x] `_synthetic_data()` 标注为演示回退，答辩提示使用真实数据
- [x] 类别均衡：SMOTE / class_weight / undersample 三种方式
- [x] 算法对比：随机森林 vs 规则检测基线

#### 5. 演示准备

- [x] 攻击模拟脚本 → `demo/attack_sim.py`（SYN Flood / 端口扫描 / UDP Flood）
- [x] 攻击回放脚本（Scapy sendp）
- [x] 一键演示流程 → `demo/run_demo.py`
- [x] 备选方案：模拟攻击脚本直接生成流量
- [x] `python My_task.py app` 面板功能完整可演示
- [x] 实时抓包、阈值配置、QPS 趋势图、警报日志正常工作

---

### P1 — 加分项（功能完整度 + 融合 + 可视化）✅ 全部完成

#### 6. 检测能力融合与扩展

- [x] **#8 ML + 规则双引擎融合到 Web 面板** → `dual_detector.py`
  - 规则实时检测 + ML 周期聚合预测
  - 融合策略：双引擎触发=高危，仅ML=中危，仅规则=低危
- [x] **#9 扩展检测类型**
  - SQL 注入 / XSS 载荷模式匹配 → `detector.py` 8 类正则
  - 暴力破解检测（同 IP 短时间多次连接敏感端口）
  - 横向移动检测（内网 IP 间异常连接模式）

#### 7. 算法对比实验

- [x] 随机森林基线
- [x] XGBoost / LightGBM 对比
- [x] MLP 深度学习模型
- [x] 5 折交叉验证 + ROC-AUC 对比 → `cross_validate_models()`
- [x] 算法对比表格输出 → `_print_comparison_table()`
- [x] 规则检测基线 → `_evaluate_rule_baseline()`
- [x] 随机森林基线
- [x] 双引擎融合方案 → `_evaluate_dual_fusion()`
- [x] 三种方案 F1 对比表
- [x] ROC-AUC 对比（二分类时计算）

#### 8. Web 面板可视化增强

- [x] 攻击类型分布饼图
- [x] 告警条目颜色分级（高危红 / 中危橙 / 低危黄）
- [x] 检测准确率指标卡（模型置信度）

#### 9. 演示打磨

- [x] 演示脚本排练，全链路流畅
- [x] 备选方案：离线 pcap / 模拟攻击 → `attack_sim.py generate_pcap`

---

### P2 — 锦上添花（工程化 + 文档 + 答辩）✅ 11/12 完成

#### 10. 工程化 ✅ 全部完成

- [x] `.gitignore` — 排除 `__pycache__/`、`*.pkl`、`*.csv`、`.trae/`、`*.egg-info/` 等 37 条规则
- [x] 单元测试 `tests/` — 67 个测试全部通过
  - `test_detector.py` 29 个：DDoS/PortScan/SYNFlood/UDPFlood/SQLi/XSS/BruteForce/LateralMovement/Payload
  - `test_enhanced_features.py` 20 个：流聚合/18 维特征提取/TLS 特征
  - `test_features.py` 7 个：label_packets 各场景
  - `test_train.py` 11 个：CSV 格式检测/数据加载/模型训练
- [x] Docker 化部署 — `Dockerfile`(python:3.11-slim+libpcap) + `docker-compose.yml`(健康检查+数据卷)
- [x] 日志系统增强 — `logging_config.py`
  - 三级处理器：控制台(INFO) + 全量文件(DEBUG, 10MB×5轮转) + 检测专用(WARNING, JSON Lines)
  - `DetectionLogHandler` 告警持久化
- [x] Web API 基础认证 — Bearer Header + Query token 双通道，`AUTH_ENABLED` 开关
- [x] 配置常量集中化 — `config.py` 25+ 常量，`CAMPUS_IDS_*` 环境变量覆盖

#### 11. 文档与答辩

- [x] README 精修
  - ASCII 架构图（数据流：抓包→特征提取→双引擎检测→Web面板）
  - 项目结构说明（反映全部新增模块）
  - 8 类规则检测表 + 双引擎融合策略 + 加密流量检测 + 18 维特征维度
  - 模型评估对比表（RF/XGB/LGB/MLP/LR/双引擎融合/RuleBaseline）
  - Docker 部署说明 + 配置常量表 + 日志系统 + API 认证 + 测试说明 + 技术栈
- [x] 评估指标补充
  - Per-Class F1（`evaluate_model()` 按类别分别计算）
  - 误报率 FPR（二分类 FP/(FP+TN)，多分类宏平均）
  - 检测延迟指标（`dual_detector.py` perf_counter 计时 + `_benchmark_detection_latency()` 基准测试）
  - 评估报告新增 Per-Class F1 对比表 + FPR 列 + 延迟统计

---

## 进度跟踪

| 优先级 | 项目数 | 已完成 | 完成率 |
|--------|--------|--------|--------|
| P0 必做 | 约 22 项 | 22 | 100% |
| P1 加分 | 约 14 项 | 14 | 100% |
| P2 锦上添花 | 约 12 项 | 11 | 92% |
| 数据泄漏修复 | 7 项 | 7 | 100% |
| **合计** | **约 55 项** | **54** | **98%** |

> 仅剩「答辩 PPT / 演示文稿」1 项未完成。

---

## 交付物清单

| 类别 | 文件 | 说明 |
|------|------|------|
| CLI 入口 | `My_task.py` | capture/ecapture/train/detect/app/demo/attack_sim 七命令 |
| 配置 | `src/campus_ids/config.py` | 25+ 常量，环境变量覆盖 |
| 日志 | `src/campus_ids/logging_config.py` | 三级处理器 + JSON Lines |
| 抓包 | `src/campus_ids/capture/features.py` | 基础抓包 + 启发式打标 |
| 特征 | `src/campus_ids/capture/enhanced_features.py` | 18 维增强特征 |
| TLS | `src/campus_ids/capture/tls_analyzer.py` | JA3 指纹 + SNI + 异常检测 |
| 训练 | `src/campus_ids/model/train.py` | RF/XGB/LGBM/LR/MLP + 算法对比 + Per-Class F1 + FPR + 延迟基准 + 防泄漏 |
| 规则检测 | `src/campus_ids/detector/detector.py` | 8 类规则（DDoS/Scan/SYN/UDP/SQLi/XSS/BruteForce/LateralMove） |
| 双引擎 | `src/campus_ids/detector/dual_detector.py` | 规则+ML 融合 + 告警分级 + 检测延迟测量 |
| Web 面板 | `src/campus_ids/web/app.py` | Flask 实时仪表盘 + API 认证 + 配置化 |
| 演示 | `src/campus_ids/demo/attack_sim.py` | 攻击模拟 + pcap 生成 |
| 演示 | `src/campus_ids/demo/run_demo.py` | 一键演示流程 |
| 测试 | `tests/` | 67 个 pytest 测试 |
| 容器 | `Dockerfile` + `docker-compose.yml` | 一键部署 + 健康检查 |
| 文档 | `README.md` | 架构图 + 项目结构 + 检测能力 + API + Docker + 配置 + 日志 + 测试 |
| 评估 | `evaluation_report.txt` + `confusion_matrix.png` | 算法对比 + Per-Class F1 + FPR + 防泄漏评估 |
| 数据 | `train_data.csv` + `test_data.csv` | CICIDS2017 训练/测试集（640K/160K） |
| 模型 | `model.pkl` | RandomForest, 13 特征, Attack F1=0.9828 |

---

## 备注

- P0/P1/P2-10 全部完成，仅剩答辩 PPT
- 加密流量分析是本项目名称的核心诉求，已实现 JA3 指纹 + TLS 特征 + 异常检测
- 67 个单元测试全部通过，Docker 一键部署可用
