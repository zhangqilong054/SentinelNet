"""P2-10.6: 集中配置常量 — 消除硬编码魔法数字。

所有模块从此处导入配置常量，便于统一管理和调优。
支持环境变量覆盖，支持 .env 文件加载（python-dotenv）。
"""
from __future__ import annotations

import os
from pathlib import Path

# M6: 加载 .env 文件（python-dotenv）
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv 未安装，仅使用环境变量


# ── 项目路径 ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("CAMPUS_IDS_DATA_DIR", str(PROJECT_ROOT)))
LOG_DIR = Path(os.environ.get("CAMPUS_IDS_LOG_DIR", str(PROJECT_ROOT / "logs")))

# 模型与数据文件
MODEL_PATH = DATA_DIR / "model.pkl"
TRAFFIC_CSV = DATA_DIR / "traffic_data.csv"
TRAFFIC_STATS_CSV = DATA_DIR / "traffic_stats.csv"
CONFUSION_MATRIX_PATH = DATA_DIR / "confusion_matrix.png"
EVALUATION_PATH = DATA_DIR / "evaluation_report.txt"

# 模型注册表（版本化保存）
MODELS_DIR = DATA_DIR / "models"
RUNS_DIR = MODELS_DIR / "runs"
LATEST_JSON = MODELS_DIR / "latest.json"
BEST_JSON = MODELS_DIR / "best.json"
REGISTRY_JSON = MODELS_DIR / "registry.json"


# ── Web 面板配置 ──────────────────────────────────────────────
WEB_PORT = int(os.environ.get("CAMPUS_IDS_WEB_PORT", "5000"))
WEB_REFRESH_INTERVAL_MS = int(os.environ.get("CAMPUS_IDS_REFRESH_MS", "2000"))

# 滑动窗口
WINDOW_SIZE = int(os.environ.get("CAMPUS_IDS_WINDOW_SIZE", "60"))

# 告警 API 返回上限
MAX_ALERT_API_RETURN = int(os.environ.get("CAMPUS_IDS_MAX_ALERT_API", "20"))

# QPS 趋势图最大数据点
MAX_CHART_LABELS = int(os.environ.get("CAMPUS_IDS_MAX_CHART_LABELS", "30"))


# ── 规则检测阈值 ──────────────────────────────────────────────
DDOS_THRESHOLD = int(os.environ.get("CAMPUS_IDS_DDoS_THRESHOLD", "500"))
PORT_SCAN_THRESHOLD = int(os.environ.get("CAMPUS_IDS_PORT_SCAN_THRESHOLD", "50"))
SYN_FLOOD_THRESHOLD = int(os.environ.get("CAMPUS_IDS_SYN_FLOOD_THRESHOLD", "100"))
UDP_FLOOD_THRESHOLD = int(os.environ.get("CAMPUS_IDS_UDP_FLOOD_THRESHOLD", "200"))

# 基础抓包标签阈值
HIGH_FREQ_IP_THRESHOLD = int(os.environ.get("CAMPUS_IDS_HIGH_FREQ_IP_THRESHOLD", "50"))

# P1-#9: 应用层检测阈值
BRUTE_FORCE_THRESHOLD = int(os.environ.get("CAMPUS_IDS_BF_THRESHOLD", "10"))
BRUTE_FORCE_WINDOW_SEC = int(os.environ.get("CAMPUS_IDS_BF_WINDOW", "60"))
LATERAL_MOVEMENT_THRESHOLD = int(os.environ.get("CAMPUS_IDS_LATERAL_THRESHOLD", "5"))


# ── ML 检测配置 ──────────────────────────────────────────────
ML_INTERVAL_SEC = float(os.environ.get("CAMPUS_IDS_ML_INTERVAL", "5.0"))
ML_FLOW_BUFFER_SIZE = int(os.environ.get("CAMPUS_IDS_ML_FLOW_BUFFER", "10000"))
ML_HISTORY_SIZE = int(os.environ.get("CAMPUS_IDS_ML_HISTORY", "60"))

# ML 置信阈值（双引擎融合策略使用）
ML_CONF_HIGH = float(os.environ.get("CAMPUS_IDS_ML_CONF_HIGH", "0.7"))
ML_CONF_LOW = float(os.environ.get("CAMPUS_IDS_ML_CONF_LOW", "0.3"))

# TLS 分析器记录上限
TLS_RECORD_MAX = int(os.environ.get("CAMPUS_IDS_TLS_RECORD_MAX", "10000"))

# 最小训练样本数
MIN_TRAIN_SAMPLES = int(os.environ.get("CAMPUS_IDS_MIN_TRAIN_SAMPLES", "100"))


# ── 抓包配置 ──────────────────────────────────────────────────
# P0-2: DNS 检测端口
DNS_PORT = 53
# P0-4/5/6: TLS 检测端口
TLS_PORTS = (443, 8443)
# P1-#9: 暴力破解敏感端口
BRUTE_FORCE_PORTS = (22, 21, 3389, 25, 3306, 5432)


# ── 模拟模式（Docker/演示） ──────────────────────────────────
DEMO_MODE = os.environ.get("CAMPUS_IDS_DEMO_MODE", "0") == "1"

# 模拟数据范围
DEMO_QPS_MIN, DEMO_QPS_MAX = 100, 800
DEMO_CONN_MIN, DEMO_CONN_MAX = 50, 200
DEMO_SYN_MIN, DEMO_SYN_MAX = 0, 150
DEMO_UDP_MIN, DEMO_UDP_MAX = 0, 250
DEMO_DNS_MIN, DEMO_DNS_MAX = 0, 50
DEMO_PKT_MIN, DEMO_PKT_MAX = 10, 50


# ── API 认证 ──────────────────────────────────────────────────
_api_token_env = os.environ.get("CAMPUS_IDS_API_TOKEN", "")
if not _api_token_env:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "CAMPUS_IDS_API_TOKEN 未设置，API 认证已禁用。生产环境请设置此环境变量。"
    )
API_TOKEN = _api_token_env
AUTH_ENABLED = bool(_api_token_env) and os.environ.get("CAMPUS_IDS_AUTH_ENABLED", "1") == "1"