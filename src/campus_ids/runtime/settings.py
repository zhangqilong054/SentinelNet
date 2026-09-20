"""runtime/settings.py — 三层配置唯一读写入口。

三层优先级：默认值 → DB 持久化 → 运行期覆盖。
取代 config.py 常量散读 + helpers.CONFIG 双写。

本模块 import 不产生任何 I/O 副作用。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, ClassVar

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置 — 三层合并后的最终值。

    优先级（高→低）：运行期覆盖 → 环境变量/.env → DB 持久化 → 默认值。
    """

    model_config = SettingsConfigDict(
        env_prefix="CAMPUS_IDS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
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
    capture_iface: str = Field(
        default="",
        description="基础抓包网卡（scapy 接口名或系统友好名如 WLAN；空=自动挑选活动网卡）。"
                    "2026-09-19 真机实测：Windows 下 scapy conf.iface 常指向非活动适配器，"
                    "导致抓包永远 0 包，故提供显式配置；"
                    "2026-09-20 起留空不再回退 conf.iface，而是走默认路由/活动网卡自动识别。",
    )
    capture_filter: str = Field(
        default="tcp or udp",
        description="抓包 BPF 过滤表达式（内核态过滤，减少用户态开销）。"
                    "默认只抓 TCP/UDP（检测器只用到这两类，ARP/STP 等是纯噪声）；"
                    "置空字符串表示不过滤、抓全部。",
    )
    capture_exclude_web_port: bool = Field(
        default=True,
        description="是否把面板自身流量（web_port 的 TCP 会话）排除出抓包。"
                    "开启可避免前端轮询/SSE 心跳被当成真实流量污染统计与检测；"
                    "如需检测针对面板端口的攻击，请关闭。",
    )
    frontend: str = Field(
        default="new",
        description="前端模式：new=Vue3 SPA（frontend/dist，完整交互，默认）/ legacy=Jinja2 只读壳（API 冒烟与安全兜底）。"
        "2026-09-19 默认值翻转为 new（Web 交互化）：dist 缺失时启动报错并提示 npm run build，"
        "legacy 改为显式 opt-in。同日已收编进 Settings（此前 app.py 直接 os.environ.get 读取，"
        "写进 .env 会被静默忽略）。",
    )
    web_refresh_interval_ms: int = Field(
        default=2000,
        validation_alias=AliasChoices(
            "CAMPUS_IDS_WEB_REFRESH_INTERVAL_MS", "CAMPUS_IDS_REFRESH_MS"
        ),
        description="前端刷新间隔(ms)",
    )

    # ── 滑动窗口 ──────────────────────────────────────────────────
    window_size: int = Field(default=60, description="滑动窗口大小")

    # ── 规则检测阈值 ──────────────────────────────────────────────
    ddos_threshold: int = Field(default=500, description="DDoS 检测阈值")
    port_scan_threshold: int = Field(default=50, description="端口扫描检测阈值")
    syn_flood_threshold: int = Field(default=100, description="SYN Flood 检测阈值")
    udp_flood_threshold: int = Field(default=200, description="UDP Flood 检测阈值")
    high_freq_ip_threshold: int = Field(default=50, description="高频 IP 阈值")
    # 注：以下三项旧 config.py 用的键名是缩写版（BF_* / LATERAL_*），
    # 与 Settings 字段派生的 CAMPUS_IDS_BRUTE_FORCE_* 不一致 → 用 AliasChoices 兼容两者，
    # 否则沿用旧 .env 的部署会静默取默认值。
    brute_force_threshold: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "CAMPUS_IDS_BRUTE_FORCE_THRESHOLD", "CAMPUS_IDS_BF_THRESHOLD"
        ),
        description="暴力破解检测阈值",
    )
    brute_force_window: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "CAMPUS_IDS_BRUTE_FORCE_WINDOW", "CAMPUS_IDS_BF_WINDOW"
        ),
        description="暴力破解时间窗口(秒)",
    )
    lateral_movement_threshold: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "CAMPUS_IDS_LATERAL_MOVEMENT_THRESHOLD", "CAMPUS_IDS_LATERAL_THRESHOLD"
        ),
        description="横向移动检测阈值",
    )

    # ── ML 检测配置 ──────────────────────────────────────────────
    # 旧键名同样为缩写版（ML_INTERVAL / ML_FLOW_BUFFER / ML_HISTORY）
    ml_interval_sec: float = Field(
        default=5.0,
        validation_alias=AliasChoices(
            "CAMPUS_IDS_ML_INTERVAL_SEC", "CAMPUS_IDS_ML_INTERVAL"
        ),
        description="ML 检测间隔(秒)",
    )
    ml_flow_buffer_size: int = Field(
        default=10000,
        validation_alias=AliasChoices(
            "CAMPUS_IDS_ML_FLOW_BUFFER_SIZE", "CAMPUS_IDS_ML_FLOW_BUFFER"
        ),
        description="ML 流缓冲大小",
    )
    ml_history_size: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "CAMPUS_IDS_ML_HISTORY_SIZE", "CAMPUS_IDS_ML_HISTORY"
        ),
        description="ML 历史窗口大小",
    )
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
    max_alert_api_return: int = Field(
        default=20,
        validation_alias=AliasChoices(
            "CAMPUS_IDS_MAX_ALERT_API_RETURN", "CAMPUS_IDS_MAX_ALERT_API"
        ),
        description="告警 API 返回上限",
    )
    cleanup_days: int = Field(
        default=30,
        description="数据自动清理保留天数（告警与流量记录）",
    )

    # ── 认证 ──────────────────────────────────────────────────────
    api_token: str = Field(default="", description="API Token（空则禁用认证）")
    auth_enabled: bool = Field(default=True, description="是否启用 API Token 认证")
    secret_key: str = Field(default="change-me-in-production", description="会话密钥")
    debug: bool = Field(default=False, description="开发模式（允许不安全默认值）")

    # ── CORS ──────────────────────────────────────────────────────
    # NoDecode：禁用 pydantic-settings 对 list 类型的强制 JSON 解码，
    # 否则 `CAMPUS_IDS_CORS_ORIGINS=`（空串）或逗号分隔写法会让 Settings()
    # 直接抛 SettingsError（.env.example 文档化的是逗号分隔 → 复制即启动失败）。
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default=["*"],
        description="CORS 允许的来源列表（空串=[] 即仅同源；逗号分隔或 JSON 数组均可）",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: Any) -> Any:
        """兼容三种写法：空串→[]（仅同源）；逗号分隔→拆分；JSON 数组→原样。"""
        if v is None:
            return []
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            if s.startswith("["):
                return json.loads(s)  # JSON 数组写法；格式错则让 pydantic 报出可读错误
            return [item.strip() for item in s.split(",") if item.strip()]
        return v

    # ── 演示模式常量（不可配置，仅用于 demo 数据生成）──────────────
    DEMO_QPS_MIN: ClassVar[int] = 100
    DEMO_QPS_MAX: ClassVar[int] = 800
    DEMO_CONN_MIN: ClassVar[int] = 50
    DEMO_CONN_MAX: ClassVar[int] = 200
    DEMO_SYN_MIN: ClassVar[int] = 0
    DEMO_SYN_MAX: ClassVar[int] = 150
    DEMO_UDP_MIN: ClassVar[int] = 0
    DEMO_UDP_MAX: ClassVar[int] = 250
    DEMO_DNS_MIN: ClassVar[int] = 0
    DEMO_DNS_MAX: ClassVar[int] = 50
    DEMO_PKT_MIN: ClassVar[int] = 10
    DEMO_PKT_MAX: ClassVar[int] = 50

    # ── 计算路径属性（从 data_dir 派生，不独立存储）──────────────────
    @property
    def model_path(self) -> Path:
        return self.data_dir / "model.pkl"

    @property
    def traffic_csv(self) -> Path:
        return self.data_dir / "traffic_data.csv"

    @property
    def traffic_stats_csv(self) -> Path:
        return self.data_dir / "traffic_stats.csv"

    @property
    def confusion_matrix_path(self) -> Path:
        return self.data_dir / "confusion_matrix.png"

    @property
    def evaluation_path(self) -> Path:
        return self.data_dir / "evaluation_report.txt"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def runs_dir(self) -> Path:
        return self.models_dir / "runs"

    @property
    def latest_json(self) -> Path:
        return self.models_dir / "latest.json"

    @property
    def best_json(self) -> Path:
        return self.models_dir / "best.json"

    @property
    def registry_json(self) -> Path:
        return self.models_dir / "registry.json"

    # ── 运行期覆盖层（不持久化，不来自环境变量）──────────────────
    _override_originals: dict[str, Any] = {}

    def set_override(self, key: str, value: Any) -> None:
        """设置运行期覆盖 —— **直接写入实例字段**，所有读取点立即生效。

        修复说明（2026-09-17)：早期实现只把值塞进 `_overrides`，而全项目仅
        `get()` 读该字典，其余读取点（端点序列化、`_rebuild_rule_detector`、
        服务层）一律走直接属性访问 → `PUT /api/settings` 返回 200 声称成功，
        值却取不到，`_overrides` 实为死存储。现在直接赋值，并记录覆盖前的
        原值，使 `clear_overrides()` 能真正还原。
        """
        if not hasattr(self, key):
            raise AttributeError(f"Settings 无此配置项: {key}")
        if key not in self._override_originals:
            self._override_originals[key] = getattr(self, key)
        setattr(self, key, value)

    def clear_overrides(self) -> None:
        """清除所有运行期覆盖，还原为覆盖前的值。"""
        for key, original in self._override_originals.items():
            setattr(self, key, original)
        self._override_originals.clear()

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