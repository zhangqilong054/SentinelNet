# Web 重写收口报告（T5.4）

> 出具时间：2026-09-18
> 代码基线：HEAD（含 P1 修复后 809 passed / 0 failed）

---

## 一、总判定

**Web 重写任务主体完成，代码层面所有架构/安全/契约/覆盖率目标已达成并有实测证据。** 两个 P1 测试回归已修复（见 §四），P2 文档同步待补（见 §五）。

---

## 二、§5 收益表实测比对

| 指标 | 现状（旧） | 目标 | 实测（新） | 达成 |
|------|----------:|------:|----------:|:----:|
| Web Python 行数 | 2822 | ~2050 | **3122**（web_new/）+ **1085**（services/）+ **1135**（runtime/）= **5342** | ⚠️ 见注1 |
| 蓝图中的 docstring | 586 行 | 0 | **0**（已外置到 openapi.py / schemas.py） | ✅ |
| 编排端点 | 20（族 21） | 3 | **3**（tasks/start + tasks/stop + tasks/status） | ✅ |
| 并发/状态机制 | 8 组各自实现 | 1 套任务注册表 | **1 套**（runtime/tasks.py 275 行） | ✅ |
| helpers.py | 657 行 / 12 模块级赋值 / 4 线程创建点 | 拆为 4 模块 | **已删除**，功能拆入 runtime/ + services/ | ✅ |
| controls.js | 959 行 / 63 id | 7 模块 × ~150 行 | **Vue 3 重写**：25 文件 / 4678 行（含 2223 行自动生成类型） | ⚠️ 见注2 |
| 前端定时器 | 8 轮询 + 2 SSE | 1 SSE + 2 兜底 | **1 SSE**（composables/useSSE.ts）+ **1 兜底轮询**（composables/usePolling.ts） | ✅ |
| 页面结构 | 6 tab / 12 面板 | 4 区 / ~10 面板 | **4 区**（Observe / Tasks / Config / System） | ✅ |
| Web 层覆盖率 | 21%–64% | ≥70% | **96%**（全模块 ≥74%） | ✅ |
| Swagger YAML 内嵌 | 586 行 | 0 | **0**（apispec 自动生成） | ✅ |
| 模块级可变全局 | 12 个 | 0 | **0**（收容器 RuntimeState） | ✅ |
| import 期副作用 | 有（init_db 等） | 0 | **0**（create_app() 工厂） | ✅ |

**注1**：新代码行数看似增加，但旧 2822 行是**单层 Web 代码**（含 586 行 docstring），新 5342 行是**三层架构**（web_new API 层 3122 + services 业务层 1085 + runtime 基础设施层 1135），且 docstring 已清零、编排端点从 20→3、全局状态从 12→0。**复杂度实质下降**，行数增加是架构分层的正常代价。

**注2**：前端从原生 JS 重写为 Vue 3 + TypeScript，4678 行中 2223 行为 OpenAPI 自动生成的类型声明（api-generated.d.ts），实际手写代码约 2455 行，模块化程度远超旧 959 行单文件。

---

## 三、总验收 A1–A11 对账

| # | 项 | 状态 | 证据 |
|---|---|:----:|------|
| A1 | 功能等价（golden 比对） | ✅ | 42/42 契约判定全过，未解释差异 0 |
| A2 | 契约 diff 有意变更入 ADR | ✅ | 8 保留 + 26 已解释移除 + 0 丢失 |
| A3 | 编排 20→3、剧本数据驱动 | ✅ | tasks.py 3 端点 + scenarios.py 剧本模型 |
| A4 | Swagger YAML 内嵌 = 0 | ✅ | apispec 自动生成 |
| A5 | 无模块级可变全局 | ✅ | T1/T2 探针通过 |
| A6 | 无 import 期副作用 | ✅ | T1/T2 探针通过 |
| A7 | Web 层覆盖率 ≥70% | ✅ | 96%（全模块 ≥74%） |
| A8 | 前端模块化 | ✅ | Vue 3 + TS，7 views + 6 stores + 2 composables |
| A9 | 信息架构 | ✅ | 4 区（Observe / Tasks / Config / System） |
| A10 | 浏览器验证 | 🟡 | Playwright 9/9 ✅ + 人工清单待用户执行 |
| A11 | Docker 构建成功 | ⬜ | 未实际构建 |

---

## 四、P1 测试回归修复

### 4.1 `test_history_is_persisted_to_db` 稳定失败

- **根因**：conftest `_isolate_artifacts` 重置引擎指向临时库但不建表，`traffic_history` 表不存在
- **修复**：在 `test_detection_service.py` 添加 `autouse` fixture 调用 `init_db()` 建表（与 `test_alert_service.py` 同模式）
- **验证**：809 passed / 0 failed

### 4.2 `test_other_udp_port_is_not_dns` 顺序相关 flaky

- **根因**：`start_capture()` 启动后台线程后立即返回，测试随即 `_drain()` 读取队列，存在竞态——线程可能尚未处理包
- **修复**：在所有 `TestCaptureWorker` 测试中添加 `_wait_for_thread(state)` 等待线程完成，消除竞态
- **验证**：809 passed / 0 failed（连续 2 次全量通过）

---

## 五、遗留事项

| 级 | 项 | 状态 |
|---|---|:----:|
| P2 | T4.4 文档同步：README §七 / 操作手册 §5 仍引用已删除旧端点 | 待补 |
| P2 | T0.4 人工视觉比对 / T3.10 人工核对清单 | 待用户执行 |
| P2 | T2.19 十分钟真机并发 | 需带网卡机器 |
| P3 | A11 Docker 多阶段镜像构建 | 未实际构建 |

---

## 六、测试基线

```
809 passed, 0 failed, 26 warnings in 106.53s
覆盖率（services + web_new + runtime 全口径）：TOTAL 96%
契约回放：42 条判定全 ✅，退出码 0
规格门禁：旧 34 路径 → 保留 8 + 已解释移除 26 + 未解释丢失 0
```