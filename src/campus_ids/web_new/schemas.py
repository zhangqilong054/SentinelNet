"""web_new/schemas.py — 请求/响应 Pydantic 模型。

取代原 Flask 版手写 JSON 序列化（根因 B），利用 Pydantic v2 自动生成 OpenAPI。
所有 API 端点的请求体和响应体均在此定义，确保类型安全和文档自动生成。

ADR-0001 §3.2: Pydantic v2 模型，OpenAPI 自动生成。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ── 通用 ──────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    """通用消息响应。"""
    message: str
    status: str = "ok"

    model_config = {
        "json_schema_extra": {
            "examples": [{"message": "操作成功", "status": "ok"}]
        }
    }


class ErrorResponse(BaseModel):
    """统一错误响应。"""
    error: str
    detail: str
    status: int


class HealthResponse(BaseModel):
    """健康检查响应 — 对齐旧 Flask /api/health 返回格式。"""
    status: str = "healthy"
    timestamp: str = ""
    uptime_seconds: float = 0.0
    components: dict[str, Any] = Field(default_factory=dict)

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "status": "healthy",
                "timestamp": "2026-01-01 12:00:00",
                "uptime_seconds": 3600.0,
                "components": {
                    "database": {"status": "ok"},
                    "capture": {"status": "stopped"},
                    "ml_model": {"model_loaded": False},
                },
            }]
        }
    }


# ── 任务 (Tasks) ──────────────────────────────────────────────────

class TaskActionRequest(BaseModel):
    """任务启动/停止请求。"""
    duration: int | None = Field(
        default=None,
        ge=1,
        description="限时任务运行秒数（仅 TIMED 类型）",
    )


class TaskStatusResponse(BaseModel):
    """单个任务状态响应。

    F5 修复：新增 duration 字段，暴露启动时实际使用的窗口时长。
    前端进度条应使用 `duration ?? default_duration` 计算进度。
    """
    name: str
    kind: str = Field(description="continuous | timed")
    status: str = Field(description="idle | running | stopping | finished | failed")
    elapsed: float = 0.0
    error: str | None = None
    description: str = ""
    default_duration: int | None = Field(default=None, description="限时任务默认时长(秒)")
    duration: int | None = Field(default=None, description="限时任务实际启动时长(秒)，前端进度条应用此值")


class TaskListResponse(BaseModel):
    """任务列表响应。"""
    tasks: list[TaskStatusResponse]


# ── 流量 (Traffic) ────────────────────────────────────────────────

class TrafficStatsResponse(BaseModel):
    """流量统计响应。

    F2 修复：新增 data_source 字段，标记数据来源：
    - "capture": 真实抓包数据
    - "demo": 模拟兜底数据（无抓包时随机生成，不触发告警）
    """
    qps: float = 0.0
    connections: int = 0
    alert: str = ""
    timestamp: str = ""
    packet_count: int = 0
    port_count: int = 0
    src_ip_count: int = 0
    syn_packets: int = 0
    udp_packets: int = 0
    dns_packets: int = 0
    data_source: str = "demo"

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "qps": 150.5, "connections": 42, "alert": "",
                "timestamp": "2026-01-01T00:00:00", "packet_count": 10000,
                "port_count": 15, "src_ip_count": 8,
                "syn_packets": 500, "udp_packets": 200, "dns_packets": 50,
            }]
        }
    }


class TrafficHistoryRecord(BaseModel):
    """单条流量历史记录。"""
    id: int
    time: str
    qps: int | None = None
    connections: int | None = None
    packet_count: int | None = None
    port_count: int | None = None
    src_ip_count: int | None = None
    alert: str | None = None


class TrafficHistoryResponse(BaseModel):
    """流量历史响应。"""
    history: list[TrafficHistoryRecord] = Field(default_factory=list)
    total: int = 0
    limit: int = 60
    offset: int = 0


# ── 告警 (Alerts) ─────────────────────────────────────────────────

class AlertResponse(BaseModel):
    """单条告警响应。"""
    id: int
    time: str
    level: str
    attack_type: str
    message: str
    ml_confidence: float = 0.0

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "id": 1, "time": "2026-01-01 00:00:00",
                "level": "high", "attack_type": "ddos",
                "message": "DDoS attack detected", "ml_confidence": 0.95,
            }]
        }
    }


class AlertListResponse(BaseModel):
    """告警列表响应。"""
    alerts: list[AlertResponse]
    total: int
    limit: int = 50
    offset: int = 0

    model_config = {
        "json_schema_extra": {
            "examples": [{"alerts": [], "total": 0, "limit": 50, "offset": 0}]
        }
    }


class AlertStatsResponse(BaseModel):
    """告警统计响应。"""
    total_alerts: int = 0
    alerts_by_type: dict[str, int] = Field(default_factory=dict)
    alerts_by_severity: dict[str, int] = Field(default_factory=dict)


# ── 模型 (Models) ─────────────────────────────────────────────────

class TrainRequest(BaseModel):
    """训练请求（T2.13 起带破坏性操作保护）。"""
    dataset: str = Field(default="auto", description="训练数据集标识（透传给训练链路 dataset_type）")
    epochs: int = Field(default=10, ge=1, description="训练轮数；≤3 视为 quick 模式")
    confirm: bool = Field(
        default=False,
        description=(
            "**必填确认位**。训练会覆盖 model.pkl / evaluation_report.txt / "
            "confusion_matrix.png，未传 `confirm=true` 时请求被拒绝（400），"
            "不会启动训练。启动前会自动备份这三个产物到 "
            "`<data_dir>/training_backup/<时间戳>/`。"
        ),
    )


class TrainStatusResponse(BaseModel):
    """训练状态响应。

    `status` 的真相源是 `TaskRegistry` 里 `train` 任务的状态
    （T2.13 收敛；此前是 `api/models.py` 的模块级状态机，与任务编排互不感知）。
    """
    status: str = Field(description="idle | training | completed | failed")
    progress: float = Field(
        default=0.0,
        description="进度 0.0–1.0，由任务窗口已耗时/总时长算出（非硬编码）",
    )
    epoch: int = 0
    total_epochs: int = 0
    error: str | None = None
    elapsed_seconds: float | None = Field(
        default=None, description="任务已运行秒数（来自 TaskRegistry）"
    )
    duration_seconds: int | None = Field(
        default=None, description="任务窗口时长（来自 TaskRegistry）"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"status": "training", "progress": 0.5, "epoch": 5, "total_epochs": 10, "error": None}]
        }
    }


class ModelInfoResponse(BaseModel):
    """模型信息响应。"""
    name: str
    version: str
    created_at: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    # registry 的 is_best 透出（2026-09-19）：前端据此显示「当前最佳」徽标
    is_best: bool = False


class ModelListResponse(BaseModel):
    """模型列表响应。"""
    models: list[ModelInfoResponse]


# ── 剧本 (Scenarios) ──────────────────────────────────────────────

class ScenarioInfoResponse(BaseModel):
    """剧本信息响应。"""
    name: str
    description: str
    steps: list[str] = Field(description="剧本子任务序列")


class ScenarioListResponse(BaseModel):
    """剧本列表响应。"""
    scenarios: list[ScenarioInfoResponse]


class ScenarioStartRequest(BaseModel):
    """剧本启动请求。"""
    scenario: str = Field(description="剧本名称: demo | full | attack")
    duration: int | None = Field(
        default=None,
        ge=1,
        description="限时子任务运行秒数（仅影响 TIMED 类型子任务）",
    )


class ScenarioStopRequest(BaseModel):
    """剧本停止请求。"""
    scenario: str = Field(description="剧本名称: demo | full | attack")


# ── 配置 (Settings) ───────────────────────────────────────────────

class ThresholdUpdateRequest(BaseModel):
    """阈值更新请求。"""
    key: str = Field(description="阈值键名")
    value: float = Field(description="阈值新值")


class SettingsResponse(BaseModel):
    """配置响应 — 对齐 Settings 类字段。"""
    thresholds: dict[str, Any] = Field(default_factory=dict, description="规则检测阈值")
    ml_config: dict[str, Any] = Field(default_factory=dict, description="ML 检测配置")
    web_config: dict[str, Any] = Field(default_factory=dict, description="Web 面板配置")
    auth_enabled: bool = False
    alert_config: dict[str, Any] = Field(default_factory=dict, description="告警配置")

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "thresholds": {"ddos_threshold": 500, "port_scan_threshold": 50},
                "ml_config": {"ml_interval_sec": 5.0, "ml_conf_high": 0.7},
                "web_config": {"web_port": 5000},
                "auth_enabled": False,
                "alert_config": {"max_alert_api_return": 20},
            }]
        }
    }


# ── TLS / Payload 分析 ────────────────────────────────────────────

class TlsAnalysisResponse(BaseModel):
    """TLS 分析响应。"""
    anomalies: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class PayloadAnalysisRequest(BaseModel):
    """载荷分析请求。"""
    payload: str = Field(description="待分析的 HTTP 载荷字符串")


class PayloadAnalysisResponse(BaseModel):
    """载荷分析响应。

    ⚠️ 字段名必须与**旧实现**一致：`web/bp_capture.py:368-373` 返回
    `{'alerts': [...], 'is_anomaly': bool, 'payload_length': len(payload)}`。

    2026-09-17 参数级契约门禁（`tests/contract/specdiff.py`）抓到本类此前用了
    `threats` / `is_malicious` 两个**新名字**，并把 `payload_length` 整个丢掉 ——
    旧客户端的 `addEventListener` 式取值会全部拿到 `undefined`。这正是
    "路径保住了、响应契约没保住"的典型：路径级门禁完全看不见。
    """
    alerts: list[str] = Field(default_factory=list, description="告警列表")
    is_anomaly: bool = Field(default=False, description="是否异常")
    payload_length: int = Field(default=0, description="载荷长度")


# ── 环境自检 ──────────────────────────────────────────────────────

class CheckResponse(BaseModel):
    """环境自检响应 — 对齐旧 Flask /api/check 返回格式。"""
    ok: bool = True
    python: dict[str, Any] = Field(default_factory=dict)
    dependencies: dict[str, Any] = Field(default_factory=dict)
    capture: dict[str, Any] = Field(default_factory=dict)
    model_files: list[dict[str, Any]] = Field(default_factory=list)
    data_files: list[dict[str, Any]] = Field(default_factory=list)