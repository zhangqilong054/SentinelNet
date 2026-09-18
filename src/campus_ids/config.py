"""config.py — Settings 的兼容委托层。

所有值委托给 Settings 单例，保持 `from campus_ids.config import X` 向后兼容。
新代码应直接使用 `from campus_ids.runtime.settings import get_settings`。
"""
from __future__ import annotations

from pathlib import Path

from campus_ids.runtime.settings import get_settings

# 项目根目录（不在 Settings 中，纯路径常量）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ── 委托 Settings 单例 ─────────────────────────────────────────
_s = get_settings()

# 项目路径
DATA_DIR = _s.data_dir
LOG_DIR = _s.log_dir

# 模型与数据文件
MODEL_PATH = _s.model_path
TRAFFIC_CSV = _s.traffic_csv
TRAFFIC_STATS_CSV = _s.traffic_stats_csv
CONFUSION_MATRIX_PATH = _s.confusion_matrix_path
EVALUATION_PATH = _s.evaluation_path

# 模型注册表
MODELS_DIR = _s.models_dir
RUNS_DIR = _s.runs_dir
LATEST_JSON = _s.latest_json
BEST_JSON = _s.best_json
REGISTRY_JSON = _s.registry_json

# Web 面板配置
WEB_PORT = _s.web_port

# 滑动窗口
WINDOW_SIZE = _s.window_size

# 规则检测阈值
DDOS_THRESHOLD = _s.ddos_threshold
PORT_SCAN_THRESHOLD = _s.port_scan_threshold
SYN_FLOOD_THRESHOLD = _s.syn_flood_threshold
UDP_FLOOD_THRESHOLD = _s.udp_flood_threshold
HIGH_FREQ_IP_THRESHOLD = _s.high_freq_ip_threshold
BRUTE_FORCE_THRESHOLD = _s.brute_force_threshold
BRUTE_FORCE_WINDOW_SEC = _s.brute_force_window
LATERAL_MOVEMENT_THRESHOLD = _s.lateral_movement_threshold

# ML 检测配置
ML_INTERVAL_SEC = _s.ml_interval_sec
ML_FLOW_BUFFER_SIZE = _s.ml_flow_buffer_size
ML_HISTORY_SIZE = _s.ml_history_size
ML_CONF_HIGH = _s.ml_conf_high
ML_CONF_LOW = _s.ml_conf_low

# TLS 分析器记录上限
TLS_RECORD_MAX = _s.tls_record_max

# 最小训练样本数
MIN_TRAIN_SAMPLES = _s.min_train_samples

# 抓包配置
DNS_PORT = _s.dns_port
TLS_PORTS = _s.tls_ports
BRUTE_FORCE_PORTS = _s.brute_force_ports

# 演示模式常量
DEMO_QPS_MIN = _s.DEMO_QPS_MIN
DEMO_QPS_MAX = _s.DEMO_QPS_MAX
DEMO_CONN_MIN = _s.DEMO_CONN_MIN
DEMO_CONN_MAX = _s.DEMO_CONN_MAX
DEMO_SYN_MIN = _s.DEMO_SYN_MIN
DEMO_SYN_MAX = _s.DEMO_SYN_MAX
DEMO_UDP_MIN = _s.DEMO_UDP_MIN
DEMO_UDP_MAX = _s.DEMO_UDP_MAX
DEMO_DNS_MIN = _s.DEMO_DNS_MIN
DEMO_DNS_MAX = _s.DEMO_DNS_MAX
DEMO_PKT_MIN = _s.DEMO_PKT_MIN
DEMO_PKT_MAX = _s.DEMO_PKT_MAX