"""数据加载与预处理模块 — CSV 格式检测、公开数据集加载、类别均衡。"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.utils.class_weight import compute_class_weight

from campus_ids.capture.enhanced_features import FEATURE_NAMES
from campus_ids.config import TRAFFIC_CSV, DATA_DIR

logger = logging.getLogger(__name__)

# 增强特征列名 — 统一从 enhanced_features.FEATURE_NAMES 导入，作为唯一真值源
ENHANCED_FEATURE_COLUMNS = list(FEATURE_NAMES)

# 兼容旧格式（仅 Length + Duration）
LEGACY_FEATURE_COLUMNS = ["Length", "Duration"]

MIN_TRAIN_SAMPLES = 100


# ── CSV 格式检测 ────────────────────────────────────────────────────

def _detect_csv_format(csv_path: Path) -> str:
    """检测 CSV 格式：enhanced / legacy / unknown。"""
    try:
        df = pd.read_csv(csv_path, nrows=1)
        cols = set(df.columns)
        if "avg_pkt_len" in cols:
            return "enhanced"
        if "Length" in cols and "Duration" in cols:
            return "legacy"
    except Exception:
        pass
    return "unknown"


# ── 本地数据加载 ────────────────────────────────────────────────────

def _load_local_data(csv_path: Path | None = None) -> tuple[pd.DataFrame, pd.Series] | None:
    """加载本地抓包 CSV 数据，返回 (X, y) 或 None。"""
    path = csv_path or TRAFFIC_CSV
    if not path.exists() or path.stat().st_size == 0:
        return None

    fmt = _detect_csv_format(path)
    df = pd.read_csv(path)
    if df.empty or "Label" not in df.columns:
        return None

    if fmt == "enhanced":
        feature_cols = [c for c in ENHANCED_FEATURE_COLUMNS if c in df.columns]
    elif fmt == "legacy":
        df = df.sort_values("Timestamp")
        df["Duration"] = df["Timestamp"].diff().fillna(0)
        feature_cols = LEGACY_FEATURE_COLUMNS
    else:
        logger.warning("无法识别 CSV 格式: %s", path)
        return None

    X = df[feature_cols].fillna(0)
    y = df["Label"]
    logger.info("加载本地数据 %d 条，特征 %d 个（格式: %s）", len(df), len(feature_cols), fmt)
    return X, y


# ── 公开数据集加载 ──────────────────────────────────────────────────

def load_cicids2017(csv_path: Path) -> tuple[pd.DataFrame, pd.Series] | None:
    """P0-14/15: 加载 CICIDS2017 数据集。

    CICIDS2017 CSV 通常包含以下特征列（部分）：
    - Flow Duration, Tot Fwd Pkts, Tot Bwd Pkts, Fwd Pkt Len Mean, ...
    - Label 列标记流量类别

    下载地址: https://www.unb.ca/cic/datasets/ids-2017.html

    支持传入单个 CSV 文件或包含多个 CSV 的目录路径。
    """
    # 如果传入目录，合并目录下所有 CSV
    if csv_path.is_dir():
        return _load_cicids2017_dir(csv_path)

    if not csv_path.exists():
        logger.warning("CICIDS2017 数据集文件不存在: %s", csv_path)
        return None

    return _load_cicids2017_file(csv_path)


def _load_cicids2017_file(csv_path: Path, max_rows: int | None = None) -> tuple[pd.DataFrame, pd.Series] | None:
    """加载单个 CICIDS2017 CSV 文件。

    Args:
        csv_path: CSV 文件路径
        max_rows: 每个文件最大加载行数（None 表示全量加载）
    """
    try:
        if max_rows:
            df = pd.read_csv(csv_path, encoding="utf-8", low_memory=False, nrows=max_rows)
        else:
            df = pd.read_csv(csv_path, encoding="utf-8", low_memory=False)
    except UnicodeDecodeError:
        df = pd.read_csv(csv_path, encoding="latin-1", low_memory=False, nrows=max_rows)

    df.columns = df.columns.str.strip()

    if "Label" not in df.columns:
        logger.warning("CICIDS2017 数据集缺少 Label 列: %s", csv_path.name)
        return None

    X, y = _map_cicids2017_features(df)
    if X is None:
        return None

    logger.info("加载 CICIDS2017 文件 %s: %d 条，映射特征 %d 个", csv_path.name, len(df), len(X.columns))
    return X, y


def _load_cicids2017_dir(dir_path: Path, max_rows_per_file: int = 100000) -> tuple[pd.DataFrame, pd.Series] | None:
    """合并目录下所有 CICIDS2017 CSV 文件。

    Args:
        dir_path: 包含 CSV 文件的目录
        max_rows_per_file: 每个文件最大加载行数，避免内存溢出
    """
    csv_files = sorted(dir_path.glob("*.csv"))
    if not csv_files:
        logger.warning("目录中无 CSV 文件: %s", dir_path)
        return None

    all_X, all_y = [], []
    for f in csv_files:
        result = _load_cicids2017_file(f, max_rows=max_rows_per_file)
        if result:
            X, y = result
            all_X.append(X)
            all_y.append(y)

    if not all_X:
        logger.warning("目录中无有效 CICIDS2017 文件: %s", dir_path)
        return None

    X_merged = pd.concat(all_X, ignore_index=True)
    y_merged = pd.concat(all_y, ignore_index=True)
    logger.info("合并 CICIDS2017 目录 %s: 共 %d 条记录（%d 个文件）", dir_path.name, len(X_merged), len(all_X))
    return X_merged, y_merged


def _map_cicids2017_features(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series] | None:
    """将 CICIDS2017 原始列映射到系统特征空间。

    注意: CICIDS2017 每行是一条流记录，无法计算真正的端口熵。
    dst_port_entropy 使用端口类别的信息量作为代理：
    - 知名端口 (0-1023): 低熵代理值 0.5
    - 注册端口 (1024-49151): 中熵代理值 1.5
    - 动态端口 (49152-65535): 高熵代理值 2.5

    增强映射 (v2):
    - pkt_count: Total Fwd Packets + Total Backward Packets（总包数）
    - total_bytes: Total Length of Fwd Packets + Total Length of Bwd Packets（总字节数）
    - up_down_byte_ratio: Down/Up Ratio（CICIDS2017 直接提供）
    - avg_pkt_len / std_pkt_len: 使用全局 Packet Length Mean/Std（更准确）
    - avg_window_size: 前向+后向窗口均值
    """
    # ── 基础映射：直接列名对应 ──
    feature_map = {
        "Flow Duration": "duration",
        "Fwd Packet Length Max": "max_pkt_len",
        "Flow IAT Mean": "avg_pkt_interval",
        "Flow IAT Std": "std_pkt_interval",
        "SYN Flag Count": "syn_flag_ratio",
        "FIN Flag Count": "fin_flag_ratio",
        "RST Flag Count": "rst_flag_ratio",
        "PSH Flag Count": "psh_flag_ratio",
    }

    mapped_cols = {}
    for src_col, dst_col in feature_map.items():
        if src_col in df.columns:
            mapped_cols[dst_col] = df[src_col]

    # ── 组合映射：需要多列计算 ──

    # pkt_count = Total Fwd Packets + Total Backward Packets
    fwd_pkts = df.get("Total Fwd Packets", pd.Series(0, index=df.index)).fillna(0)
    bwd_pkts = df.get("Total Backward Packets", pd.Series(0, index=df.index)).fillna(0)
    mapped_cols["pkt_count"] = fwd_pkts + bwd_pkts

    # total_bytes = Total Length of Fwd Packets + Total Length of Bwd Packets
    fwd_bytes = df.get("Total Length of Fwd Packets", pd.Series(0, index=df.index)).fillna(0)
    bwd_bytes = df.get("Total Length of Bwd Packets", pd.Series(0, index=df.index)).fillna(0)
    mapped_cols["total_bytes"] = fwd_bytes + bwd_bytes

    # avg_pkt_len: 优先使用全局 Packet Length Mean，回退到 Fwd Packet Length Mean
    if " Packet Length Mean" in df.columns:
        mapped_cols["avg_pkt_len"] = df[" Packet Length Mean"].fillna(0)
    elif "Fwd Packet Length Mean" in df.columns:
        mapped_cols["avg_pkt_len"] = df["Fwd Packet Length Mean"].fillna(0)

    # std_pkt_len: 优先使用全局 Packet Length Std，回退到 Fwd Packet Length Std
    if " Packet Length Std" in df.columns:
        mapped_cols["std_pkt_len"] = df[" Packet Length Std"].fillna(0)
    elif "Fwd Packet Length Std" in df.columns:
        mapped_cols["std_pkt_len"] = df["Fwd Packet Length Std"].fillna(0)

    # up_down_byte_ratio: CICIDS2017 直接提供 Down/Up Ratio
    if " Down/Up Ratio" in df.columns:
        mapped_cols["up_down_byte_ratio"] = df[" Down/Up Ratio"].fillna(0).astype(float)
    elif "Total Length of Bwd Packets" in df.columns and "Total Length of Fwd Packets" in df.columns:
        # 回退：下行/上行 = bwd_bytes / fwd_bytes
        ratio = bwd_bytes / fwd_bytes.replace(0, 1)
        mapped_cols["up_down_byte_ratio"] = ratio.replace([np.inf, -np.inf], 0).fillna(0)

    # avg_window_size: 前向+后向窗口均值
    fwd_win = df.get("Init_Win_bytes_forward", pd.Series(0, index=df.index)).fillna(0)
    bwd_win = df.get(" Init_Win_bytes_backward", pd.Series(0, index=df.index)).fillna(0)
    mapped_cols["avg_window_size"] = (fwd_win + bwd_win) / 2

    # dst_port_entropy: 从 Destination Port 计算端口类别信息量代理
    if "Destination Port" in df.columns or " Destination Port" in df.columns:
        port_col = "Destination Port" if "Destination Port" in df.columns else " Destination Port"
        ports = df[port_col].fillna(0).astype(float)
        dst_port_entropy = np.where(
            ports <= 1023, 0.5,       # 知名端口：低熵
            np.where(ports <= 49151, 1.5, 2.5)  # 注册/动态端口：中/高熵
        )
        mapped_cols["dst_port_entropy"] = dst_port_entropy

    if not mapped_cols:
        logger.warning("CICIDS2017 数据集特征列不匹配")
        return None, None

    X = pd.DataFrame(mapped_cols).fillna(0)

    # 替换无穷大值（Flow Bytes/s 等列在流时长为0时会产生 inf）
    X = X.replace([np.inf, -np.inf], 0)

    # 归一化 SYN/FIN/RST/PSH 为比例（除以总包数）
    if "pkt_count" in X.columns:
        pkt_count = X["pkt_count"].replace(0, 1)
        for flag_col in ["syn_flag_ratio", "fin_flag_ratio", "rst_flag_ratio", "psh_flag_ratio"]:
            if flag_col in X.columns:
                X[flag_col] = X[flag_col] / pkt_count

    # 二分类标签：BENIGN → Normal, 其余 → Attack
    y = df["Label"].apply(lambda x: "Normal" if str(x).strip().upper() == "BENIGN" else "Attack")

    return X, y


def load_nsl_kdd(csv_path: Path) -> tuple[pd.DataFrame, pd.Series] | None:
    """P0-14/15: 加载 NSL-KDD 数据集。

    列: duration, protocol_type, service, flag, src_bytes, dst_bytes, ...
    最后两列: label, difficulty_level
    """
    if not csv_path.exists():
        logger.warning("NSL-KDD 数据集文件不存在: %s", csv_path)
        return None

    try:
        # NSL-KDD 无表头
        col_names = [
            "duration", "protocol_type", "service", "flag", "src_bytes", "dst_bytes",
            "land", "wrong_fragment", "urgent", "hot", "num_failed_logins", "logged_in",
            "num_compromised", "root_shell", "su_attempted", "num_root", "num_file_creations",
            "num_shells", "num_access_files", "num_outbound_cmds", "is_host_login",
            "is_guest_login", "count", "srv_count", "serror_rate", "srv_serror_rate",
            "rerror_rate", "srv_rerror_rate", "same_srv_rate", "diff_srv_rate",
            "srv_diff_host_rate", "dst_host_count", "dst_host_srv_count",
            "dst_host_same_srv_rate", "dst_host_diff_srv_rate",
            "dst_host_same_src_port_rate", "dst_host_srv_diff_host_rate",
            "dst_host_serror_rate", "dst_host_srv_serror_rate",
            "dst_host_rerror_rate", "dst_host_srv_rerror_rate", "label", "difficulty_level"
        ]
        df = pd.read_csv(csv_path, names=col_names, encoding="utf-8")
    except Exception as exc:
        logger.warning("加载 NSL-KDD 失败: %s", exc)
        return None

    # 映射特征
    numeric_cols = ["duration", "src_bytes", "dst_bytes", "count", "srv_count",
                    "serror_rate", "rerror_rate", "same_srv_rate", "diff_srv_rate",
                    "dst_host_count", "dst_host_srv_count", "dst_host_same_srv_rate",
                    "dst_host_diff_srv_rate", "dst_host_serror_rate", "dst_host_rerror_rate"]

    available_cols = [c for c in numeric_cols if c in df.columns]
    X = df[available_cols].apply(pd.to_numeric, errors="coerce").fillna(0)

    # 二分类标签
    y = df["label"].apply(lambda x: "Normal" if str(x).strip().endswith("normal") else "Attack")

    logger.info("加载 NSL-KDD 数据集 %d 条，特征 %d 个", len(df), len(X.columns))
    return X, y


def load_dataset(path: Path, dataset_type: str = "auto") -> tuple[pd.DataFrame, pd.Series] | None:
    """统一数据集加载入口。

    Args:
        path: 数据集文件路径或目录路径
        dataset_type: auto / local / cicids2017 / nsl_kdd
    """
    if dataset_type == "auto":
        # 目录路径：若含 cicids 文件则识别为 cicids2017
        if path.is_dir():
            csv_files = list(path.glob("*.csv"))
            if any("cicids" in f.name.lower() or "pcap" in f.name.lower() for f in csv_files):
                dataset_type = "cicids2017"
            else:
                dataset_type = "local"
        else:
            name = path.name.lower()
            if "cicids" in name or "cic-ids" in name or "pcap" in name:
                dataset_type = "cicids2017"
            elif "nsl" in name or "kdd" in name:
                dataset_type = "nsl_kdd"
            else:
                dataset_type = "local"

    if dataset_type == "cicids2017":
        return load_cicids2017(path)
    elif dataset_type == "nsl_kdd":
        return load_nsl_kdd(path)
    else:
        return _load_local_data(path)


# ── 类别均衡处理 (P0-19) ────────────────────────────────────────────

def balance_classes(X: pd.DataFrame, y: pd.Series, method: str = "class_weight") -> tuple[pd.DataFrame, pd.Series, dict | None]:
    """类别均衡处理。

    Args:
        method: class_weight / smote / undersample

    Returns:
        (X_resampled, y_resampled, class_weight_dict_or_None)
    """
    class_weight_dict = None

    if method == "class_weight":
        # 使用 class_weight='balanced' 代替实际重采样
        classes = np.unique(y)
        weights = compute_class_weight("balanced", classes=classes, y=y)
        class_weight_dict = dict(zip(classes, weights))
        logger.info("使用 class_weight 均衡: %s", class_weight_dict)
        return X, y, class_weight_dict

    elif method == "smote":
        try:
            from imblearn.over_sampling import SMOTE
            smote = SMOTE(random_state=42)
            X_res, y_res = smote.fit_resample(X, y)
            logger.info("SMOTE 过采样: %d → %d 条", len(y), len(y_res))
            # 数据已均衡，返回 False 表示不需要 class_weight 加权
            return X_res, y_res, False
        except ImportError:
            logger.warning("imbalanced-learn 未安装，回退到 class_weight")
            return balance_classes(X, y, "class_weight")

    elif method == "undersample":
        from collections import Counter
        min_count = min(Counter(y).values())
        dfs = []
        for label in np.unique(y):
            dfs.append(X[y == label].sample(n=min_count, random_state=42))
        X_res = pd.concat(dfs)
        y_res = pd.Series([y[idx] for idx in X_res.index], index=X_res.index)
        logger.info("欠采样: %d → %d 条", len(y), len(y_res))
        # 数据已均衡，返回 False 表示不需要 class_weight 加权
        return X_res, y_res, False

    return X, y, None


# ── 兼容旧接口 ──────────────────────────────────────────────────────

def _synthetic_data():
    """生成模拟流量数据用于流程演示（无真实抓包数据时回退）。

    Normal 样本模拟正常浏览、视频、文件下载等校园网流量；
    Attack 样本模拟 SYN 洪泛、端口扫描、DDoS 等攻击行为，
    两类在多个关键特征上有显著统计差异，使模型可学习决策边界。

    注意：仍为合成数据，生产环境应使用真实数据集（CICIDS2017/NSL-KDD）。
    """
    rng = np.random.default_rng(42)
    n_normal, n_attack = 800, 200

    def _normal(n):
        """正常流量：中等包长、低 SYN 比例、正常包数、较长持续时间。"""
        return {
            "avg_pkt_len":      rng.normal(600, 200, n).clip(64, 1500),
            "std_pkt_len":      rng.normal(150, 50, n).clip(0, 400),
            "up_down_byte_ratio": rng.normal(2.5, 1.0, n).clip(0.1, 10),
            "pkt_count":        rng.integers(5, 80, n).astype(float),
            "total_bytes":      rng.integers(5000, 200000, n).astype(float),
            "syn_flag_ratio":   rng.normal(0.15, 0.08, n).clip(0, 0.4),
            "fin_flag_ratio":   rng.normal(0.12, 0.06, n).clip(0, 0.35),
            "rst_flag_ratio":   rng.normal(0.03, 0.02, n).clip(0, 0.15),
            "psh_flag_ratio":   rng.normal(0.25, 0.10, n).clip(0, 0.6),
            "avg_window_size":  rng.normal(32000, 10000, n).clip(1000, 65535),
            "dst_port_entropy": rng.normal(0.5, 0.3, n).clip(0, 2),
            "avg_pkt_interval": rng.normal(0.05, 0.02, n).clip(0.001, 0.2),
            "std_pkt_interval": rng.normal(0.02, 0.01, n).clip(0, 0.08),
            "ja3_hash_enc":     rng.random(n),
            "tls_version_enc":  rng.choice([3.0, 4.0], n),
            "cipher_suite_count": rng.integers(5, 20, n).astype(float),
            "duration":         rng.normal(5.0, 2.5, n).clip(0.1, 30),
        }

    def _attack(n):
        """攻击流量：高 SYN 比例、高包数、短持续时间、高端口熵、小包。"""
        return {
            "avg_pkt_len":      rng.normal(120, 40, n).clip(40, 300),
            "std_pkt_len":      rng.normal(30, 15, n).clip(0, 100),
            "up_down_byte_ratio": rng.normal(0.5, 0.3, n).clip(0.01, 2),
            "pkt_count":        rng.integers(100, 500, n).astype(float),
            "total_bytes":      rng.integers(100, 50000, n).astype(float),
            "syn_flag_ratio":   rng.normal(0.85, 0.10, n).clip(0.5, 1.0),
            "fin_flag_ratio":   rng.normal(0.02, 0.02, n).clip(0, 0.1),
            "rst_flag_ratio":   rng.normal(0.15, 0.08, n).clip(0, 0.5),
            "psh_flag_ratio":   rng.normal(0.05, 0.03, n).clip(0, 0.15),
            "avg_window_size":  rng.normal(5000, 3000, n).clip(100, 20000),
            "dst_port_entropy": rng.normal(3.5, 1.0, n).clip(1.5, 6),
            "avg_pkt_interval": rng.normal(0.005, 0.003, n).clip(0.0001, 0.02),
            "std_pkt_interval": rng.normal(0.003, 0.002, n).clip(0, 0.01),
            "ja3_hash_enc":     rng.random(n),
            "tls_version_enc":  rng.choice([3.0, 4.0, -1.0], n),
            "cipher_suite_count": rng.integers(0, 5, n).astype(float),
            "duration":         rng.normal(0.5, 0.3, n).clip(0.01, 2),
        }

    normal_data = _normal(n_normal)
    attack_data = _attack(n_attack)

    # 合并两类数据
    combined = {}
    for key in normal_data:
        combined[key] = np.concatenate([normal_data[key], attack_data[key]])
    combined["Label"] = np.array(["Normal"] * n_normal + ["Attack"] * n_attack)

    df = pd.DataFrame(combined)
    # 打乱顺序
    df = df.sample(frac=1, random_state=42).reset_index(drop=True)

    feature_cols = [c for c in ENHANCED_FEATURE_COLUMNS if c in df.columns]
    return df[feature_cols], df["Label"]


# ── 训练/测试集划分与保存 ──────────────────────────────────────────

def split_and_save_dataset(
    X: pd.DataFrame,
    y: pd.Series,
    output_dir: Path | None = None,
    test_size: float = 0.2,
    random_state: int = 42,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    """将数据集划分为训练集和测试集，并保存为 CSV 文件。

    Args:
        X: 特征 DataFrame
        y: 标签 Series
        output_dir: 输出目录（默认为项目根目录）
        test_size: 测试集比例（默认 0.2）
        random_state: 随机种子

    Returns:
        (X_train, y_train, X_test, y_test)
    """
    from sklearn.model_selection import train_test_split

    out_dir = output_dir or DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # 分层划分，保证训练/测试集中类别比例一致
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state, stratify=y
    )

    # 保存训练集
    train_df = X_train.copy()
    train_df["Label"] = y_train.values
    train_path = out_dir / "train_data.csv"
    train_df.to_csv(train_path, index=False)
    logger.info("训练集已保存: %s (%d 条)", train_path, len(train_df))

    # 保存测试集
    test_df = X_test.copy()
    test_df["Label"] = y_test.values
    test_path = out_dir / "test_data.csv"
    test_df.to_csv(test_path, index=False)
    logger.info("测试集已保存: %s (%d 条)", test_path, len(test_df))

    # 打印类别分布
    logger.info("训练集类别分布: %s", dict(y_train.value_counts()))
    logger.info("测试集类别分布: %s", dict(y_test.value_counts()))

    return X_train, y_train, X_test, y_test


# ── 特征对齐 ──────────────────────────────────────────────────────

def align_features(X: pd.DataFrame, expected_features: list[str]) -> pd.DataFrame:
    """对齐特征列：补缺失、删多余、排序一致。

    训练时模型使用的特征列表可能与推理时数据列不完全匹配：
    - CICIDS2017 映射后仅 14 个特征，而 ENHANCED_FEATURE_COLUMNS 有 18 个
    - 合成数据有全部 18 个特征，CICIDS2017 缺少 TLS 相关 4 个
    - scaler.transform() 要求特征名和顺序完全一致

    Args:
        X: 待对齐的特征 DataFrame
        expected_features: 模型训练时的特征列表（从 feature_list.json 加载）

    Returns:
        对齐后的 DataFrame，列与 expected_features 完全一致，缺失特征填 0
    """
    X_aligned = X.copy()

    # 补缺失特征（填 0）
    for col in expected_features:
        if col not in X_aligned.columns:
            X_aligned[col] = 0

    # 删除多余特征
    extra_cols = [c for c in X_aligned.columns if c not in expected_features]
    if extra_cols:
        X_aligned = X_aligned.drop(columns=extra_cols)

    # 按 expected_features 顺序排列
    return X_aligned[expected_features]