"""web_new/schemas.py — 请求/响应 Pydantic 模型。

取代原 Flask 版手写 JSON 序列化（根因 B），利用 Pydantic v2 自动生成 OpenAPI。
所有 API 端点的请求体和响应体均在此定义，确保类型安全和文档自动生成。

ADR-0001 §3.2: Pydantic v2 模型，OpenAPI 自动生成。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
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
    """健康检查响应。"""
    status: str = "ok"
    version: str = "0.2.0"
    uptime_seconds: float = 0.0

    model_config = {
        "json_schema_extra": {
            "examples": [{"status": "ok", "version": "0.2.0", "uptime_seconds": 3600.0}]
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
    """单个任务状态响应。"""
    name: str
    kind: str = Field(description="continuous | timed")
    status: str = Field(description="idle | running | stopping | finished | failed")
    elapsed: float = 0.0
    error: str | None = None


class TaskListResponse(BaseModel):
    """任务列表响应。"""
    tasks: list[TaskStatusResponse]


# ── 流量 (Traffic) ────────────────────────────────────────────────

class TrafficStatsResponse(BaseModel):
    """流量统计响应。"""
    total_packets: int = 0
    packets_per_second: float = 0.0
    avg_packet_size: float = 0.0
    protocol_distribution: dict[str, int] = Field(default_factory=dict)
    top_sources: list[dict[str, Any]] = Field(default_factory=list)
    top_destinations: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "total_packets": 10000, "packets_per_second": 150.5,
                "avg_packet_size": 512.0,
                "protocol_distribution": {"TCP": 8000, "UDP": 1500, "ICMP": 500},
                "top_sources": [], "top_destinations": [],
            }]
        }
    }


class TrafficHistoryResponse(BaseModel):
    """流量历史响应。"""
    timestamps: list[str] = Field(default_factory=list)
    packets_per_second: list[float] = Field(default_factory=list)


# ── 告警 (Alerts) ─────────────────────────────────────────────────

class AlertResponse(BaseModel):
    """单条告警响应。"""
    id: str
    timestamp: str
    src_ip: str
    dst_ip: str
    attack_type: str
    confidence: float
    severity: str = "medium"
    detail: dict[str, Any] = Field(default_factory=dict)

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "id": "1", "timestamp": "2026-01-01T00:00:00",
                "src_ip": "192.168.1.100", "dst_ip": "10.0.0.1",
                "attack_type": "ddos", "confidence": 0.95,
                "severity": "high", "detail": {},
            }]
        }
    }


class AlertListResponse(BaseModel):
    """告警列表响应。"""
    alerts: list[AlertResponse]
    total: int

    model_config = {
        "json_schema_extra": {
            "examples": [{"alerts": [], "total": 0}]
        }
    }


class AlertStatsResponse(BaseModel):
    """告警统计响应。"""
    total_alerts: int = 0
    alerts_by_type: dict[str, int] = Field(default_factory=dict)
    alerts_by_severity: dict[str, int] = Field(default_factory=dict)
    recent_alerts: list[AlertResponse] = Field(default_factory=list)


# ── 模型 (Models) ─────────────────────────────────────────────────

class TrainRequest(BaseModel):
    """训练请求。"""
    dataset: str = Field(description="训练数据集路径")
    epochs: int = Field(default=10, ge=1, description="训练轮数")


class TrainStatusResponse(BaseModel):
    """训练状态响应。"""
    status: str = Field(description="idle | training | completed | failed")
    progress: float = 0.0
    epoch: int = 0
    total_epochs: int = 0
    error: str | None = None

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


class ModelListResponse(BaseModel):
    """模型列表响应。"""
    models: list[ModelInfoResponse]


# ── 剧本 (Scenarios) ──────────────────────────────────────────────

class ScenarioInfoResponse(BaseModel):
    """剧本信息响应。"""
    name: str
    description: str
    tasks: list[str]


class ScenarioListResponse(BaseModel):
    """剧本列表响应。"""
    scenarios: list[ScenarioInfoResponse]


class ScenarioStartRequest(BaseModel):
    """剧本启动请求。"""
    scenario: str = Field(description="剧本名称: demo | full | attack")


# ── 配置 (Settings) ───────────────────────────────────────────────

class ThresholdUpdateRequest(BaseModel):
    """阈值更新请求。"""
    key: str = Field(description="阈值键名")
    value: float = Field(description="阈值新值")


class SettingsResponse(BaseModel):
    """配置响应。"""
    thresholds: dict[str, float] = Field(default_factory=dict)
    ml_config: dict[str, Any] = Field(default_factory=dict)
    auth_enabled: bool = False

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "thresholds": {"ddos_threshold": 500, "port_scan_threshold": 100},
                "ml_config": {"model_type": "xgboost"},
                "auth_enabled": False,
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
    data: str = Field(description="待分析载荷数据（base64 或 hex）")
    format: str = Field(default="hex", description="数据格式: base64 | hex")


class PayloadAnalysisResponse(BaseModel):
    """载荷分析响应。"""
    findings: list[dict[str, Any]] = Field(default_factory=list)
    risk_score: float = 0.0
    summary: str = ""