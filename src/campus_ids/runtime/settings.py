"""runtime/settings.py — 三层配置唯一读写入口。

三层优先级：默认值 → DB 持久化 → 运行期覆盖。
取代 config.py 常量散读 + helpers.CONFIG 双写。

本模块 import 不产生任何 I/O 副作用。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置 — 三层合并后的最终值。

    优先级（高→低）：运行期覆盖 → 环境变量/.env → DB 持久化 → 默认值。
    """

    model_config = SettingsConfigDict(
        env_prefix="CAMPUS_IDS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 项目路径 ──────────────────────────────────────────────────
    data_dir: Path = Field(
        default=Path(__file__).resolve().parent.parent.parent.parent,
        description="数据目录根路径",
    )
    log_dir: Path = Field(
        default=Path(__file__).resolve().parent.parent.parent.parent / "logs",
        description="日志目录",
    )

    # ── Web 面板 ──────────────────────────────────────────────────
    web_port: int = Field(default=5000, description="Web 面板端口")
    web_refresh_interval_ms: int = Field(default=2000, description="前端刷新间隔(ms)")

    # ── 滑动窗口 ──────────────────────────────────────────────────
    window_size: int = Field(default=60, description="滑动窗口大小")

    # ── 规则检测阈值 ──────────────────────────────────────────────
    ddos_threshold: int = Field(default=500, description="DDoS 检测阈值")
    port_scan_threshold: int = Field(default=50, description="端口扫描检测阈值")
    syn_flood_threshold: int = Field(default=100, description="SYN Flood 检测阈值")
    udp_flood_threshold: int = Field(default=200, description="UDP Flood 检测阈值")
    high_freq_ip_threshold: int = Field(default=50, description="高频 IP 阈值")
    brute_force_threshold: int = Field(default=10, description="暴力破解检测阈值")
    brute_force_window: int = Field(default=60, description="暴力破解时间窗口(秒)")
    lateral_movement_threshold: int = Field(default=5, description="横向移动检测阈值")

    # ── ML 检测配置 ──────────────────────────────────────────────
    ml_interval_sec: float = Field(default=5.0, description="ML 检测间隔(秒)")
    ml_flow_buffer_size: int = Field(default=10000, description="ML 流缓冲大小")
    ml_history_size: int = Field(default=60, description="ML 历史窗口大小")
    ml_conf_high: float = Field(default=0.7, description="ML 高置信阈值")
    ml_conf_low: float = Field(default=0.3, description="ML 低置信阈值")

    # ── TLS / 抓包 ──────────────────────────────────────────────
    tls_record_max: int = Field(default=10000, description="TLS 记录上限")
    min_train_samples: int = Field(default=100, description="最小训练样本数")
    dns_port: int = Field(default=53, description="DNS 检测端口")
    tls_ports: tuple[int, ...] = Field(default=(443, 8443), description="TLS 检测端口")
    brute_force_ports: tuple[int, ...] = Field(
        default=(22, 21, 3389, 25, 3306, 5432),
        description="暴力破解敏感端口",
    )

    # ── 告警 ──────────────────────────────────────────────────────
    max_alert_api_return: int = Field(default=20, description="告警 API 返回上限")

    # ── 认证 ──────────────────────────────────────────────────────
    api_token: str = Field(default="", description="API Token（空则禁用认证）")
    auth_enabled: bool = Field(default=True, description="是否启用 API Token 认证")
    login_enabled: bool = Field(default=False, description="是否启用 Flask-Login 会话认证")
    secret_key: str = Field(default="change-me-in-production", description="会话密钥")

    # ── CORS ──────────────────────────────────────────────────────
    cors_origins: list[str] = Field(
        default=["*"],
        description="CORS 允许的来源列表",
    )

    # ── 运行期覆盖层（不持久化，不来自环境变量）──────────────────
    _overrides: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        """读取配置值：运行期覆盖 → 环境变量 → 默认值。"""
        if key in self._overrides:
            return self._overrides[key]
        return getattr(self, key, default)

    def set_override(self, key: str, value: Any) -> None:
        """设置运行期覆盖（内存中，不持久化）。"""
        self._overrides[key] = value

    def clear_overrides(self) -> None:
        """清除所有运行期覆盖。"""
        self._overrides.clear()

    @property
    def threshold_keys(self) -> frozenset[str]:
        """需要持久化到 DB 的配置键集合。"""
        return frozenset({
            "ddos_threshold", "port_scan_threshold", "syn_flood_threshold",
            "udp_flood_threshold", "brute_force_threshold",
            "brute_force_window", "lateral_movement_threshold",
            "high_freq_ip_threshold",
        })


# ── 全局单例（惰性初始化）──────────────────────────────────────────
_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """获取全局 Settings 单例。

    首次调用时从环境变量/.env 初始化，后续调用返回同一实例。
    不触发 DB 读取（DB 覆盖在 create_app() 中显式执行）。
    """
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


def reset_settings() -> None:
    """重置全局单例（仅用于测试）。"""
    global _settings_instance
    _settings_instance = None