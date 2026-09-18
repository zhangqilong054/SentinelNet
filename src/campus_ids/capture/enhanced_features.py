"""增强特征提取模块 — 多维度网络流量特征。

P0-9:  流级特征（平均包长、标准差、上下行比、包数）
P0-10: TCP 行为特征（SYN/FIN/RST/PSH 比例、窗口大小均值）
P0-11: 端口特征（目标端口熵值）、时间特征（包间隔均值方差）
P0-12: 加密流量特征（JA3 哈希、TLS 版本、加密套件数量）

特征总数达 15+，统一管理。
"""
from __future__ import annotations

import csv
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from campus_ids.config import TRAFFIC_CSV, HIGH_FREQ_IP_THRESHOLD, PORT_SCAN_THRESHOLD

logger = logging.getLogger(__name__)

# ── 特征名称列表（统一管理，训练 / 检测共用） ────────────────────────
FEATURE_NAMES = [
    # 流级特征 (P0-9)
    "avg_pkt_len",          # 平均包长
    "std_pkt_len",          # 包长标准差
    "up_down_byte_ratio",   # 实为 bytes_per_packet（总字节/包数），历史命名保留以兼容已训练模型
    "pkt_count",            # 包数
    "total_bytes",          # 总字节数
    # TCP 行为特征 (P0-10)
    "syn_flag_ratio",       # SYN 标志比例
    "fin_flag_ratio",       # FIN 标志比例
    "rst_flag_ratio",       # RST 标志比例
    "psh_flag_ratio",       # PSH 标志比例
    "avg_window_size",      # 窗口大小均值
    # 端口特征 (P0-11)
    "dst_port_entropy",     # 目标端口熵值
    # 时间特征 (P0-11)
    "avg_pkt_interval",     # 包间隔均值
    "std_pkt_interval",     # 包间隔方差
    # 加密流量特征 (P0-12)
    "ja3_hash_enc",         # JA3 哈希编码（数值化）
    "tls_version_enc",      # TLS 版本编码
    "cipher_suite_count",   # 加密套件数量
    # 基础特征
    "max_pkt_len",          # 最大包长（区别于 avg_pkt_len，反映异常大包/小包）
    "duration",             # 持续时间
]

# TLS 版本编码映射 — R-02: 从 tls_analyzer.TLS_VERSION_MAP 派生，保证双向一致
# 编码值已嵌入训练模型(feature_list.json)，必须与 TLS_VERSION_MAP 的值保持对应
from campus_ids.capture.tls_analyzer import TLS_VERSION_MAP as _TLS_VERSION_MAP

_TLS_VERSION_NAMES = set(_TLS_VERSION_MAP.values())
TLS_VERSION_ENCODE: dict[str, int] = {
    "SSL 3.0": 0,
    "TLS 1.0": 1,
    "TLS 1.1": 2,
    "TLS 1.2": 3,
    "TLS 1.3": 4,
    "": -1,  # 未知版本
}
# 运行时校验：确保 TLS_VERSION_MAP 中的所有版本名都在编码映射中
_missing = _TLS_VERSION_NAMES - set(TLS_VERSION_ENCODE)
if _missing:
    logger.warning("TLS_VERSION_MAP 中有版本未在 TLS_VERSION_ENCODE 中定义: %s", _missing)


@dataclass
class PacketInfo:
    """单个包的信息。"""
    src_ip: str = ""
    dst_ip: str = ""
    src_port: int = 0
    dst_port: int = 0
    proto: str = ""          # TCP / UDP / Other
    length: int = 0
    timestamp: float = 0.0
    # TCP 标志
    is_syn: bool = False
    is_fin: bool = False
    is_rst: bool = False
    is_psh: bool = False
    window_size: int = 0
    # TLS 信息
    ja3_hash: str = ""
    tls_version: str = ""
    cipher_suite_count: int = 0


@dataclass
class FlowFeatures:
    """一条流（五元组）的聚合特征。"""
    src_ip: str = ""
    dst_ip: str = ""
    src_port: int = 0
    dst_port: int = 0
    proto: str = ""
    # 流级特征
    avg_pkt_len: float = 0.0
    std_pkt_len: float = 0.0
    up_down_byte_ratio: float = 0.0
    pkt_count: int = 0
    total_bytes: int = 0
    # TCP 行为特征
    syn_flag_ratio: float = 0.0
    fin_flag_ratio: float = 0.0
    rst_flag_ratio: float = 0.0
    psh_flag_ratio: float = 0.0
    avg_window_size: float = 0.0
    # 端口特征
    dst_port_entropy: float = 0.0
    # 时间特征
    avg_pkt_interval: float = 0.0
    std_pkt_interval: float = 0.0
    # 加密流量特征
    ja3_hash_enc: float = 0.0
    tls_version_enc: float = -1.0
    cipher_suite_count: int = 0
    # 基础特征
    max_pkt_len: float = 0.0
    duration: float = 0.0
    # 标签
    label: str = "Normal"


def _compute_entropy(values: list) -> float:
    """计算信息熵。"""
    if not values:
        return 0.0
    counts: dict = defaultdict(int)
    for v in values:
        counts[v] += 1
    total = len(values)
    entropy = 0.0
    for c in counts.values():
        p = c / total
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


def _ja3_to_numeric(ja3_hash: str) -> float:
    """将 JA3 哈希转为数值特征（取前 8 位 hex 转 int，归一化到 0~1）。"""
    if not ja3_hash:
        return 0.0
    try:
        return int(ja3_hash[:8], 16) / 0xFFFFFFFF
    except (ValueError, ZeroDivisionError):
        return 0.0


def _parse_base_fields(pkt) -> Optional[tuple]:
    """从 Scapy 包中解析基础字段。

    R-04: 统一包解析逻辑，供 extract_packet_info 和 features.process_packet 复用。

    Returns:
        (ip_layer, l4, proto, src_ip, dst_ip, src_port, dst_port, pkt_len, timestamp) 或 None
    """
    from scapy.all import IP, TCP, UDP

    if not pkt.haslayer(IP):
        return None
    if not (pkt.haslayer(TCP) or pkt.haslayer(UDP)):
        return None

    ip = pkt[IP]
    l4 = pkt[TCP] if pkt.haslayer(TCP) else pkt[UDP]
    proto = "TCP" if pkt.haslayer(TCP) else "UDP"

    return (
        ip, l4, proto,
        ip.src, ip.dst,
        int(l4.sport), int(l4.dport),
        len(pkt), float(pkt.time),
    )


def extract_packet_info(pkt) -> Optional[PacketInfo]:
    """从 Scapy 包中提取 PacketInfo。"""
    from scapy.all import TCP

    base = _parse_base_fields(pkt)
    if base is None:
        return None

    ip, l4, proto, src_ip, dst_ip, src_port, dst_port, pkt_len, timestamp = base

    info = PacketInfo(
        src_ip=src_ip,
        dst_ip=dst_ip,
        src_port=src_port,
        dst_port=dst_port,
        proto=proto,
        length=pkt_len,
        timestamp=timestamp,
    )

    # TCP 标志
    if pkt.haslayer(TCP):
        flags = pkt[TCP].flags
        info.is_syn = bool(flags & 0x02)
        info.is_fin = bool(flags & 0x01)
        info.is_rst = bool(flags & 0x04)
        info.is_psh = bool(flags & 0x08)
        info.window_size = int(pkt[TCP].window)

    return info


def aggregate_flow_features(packets: list[PacketInfo],
                            tls_records: list[dict] | None = None) -> list[FlowFeatures]:
    """将包列表按五元组聚合为流特征。

    Args:
        packets: PacketInfo 列表
        tls_records: 来自 tls_analyzer 的 TLS 记录列表（可选）

    Returns:
        FlowFeatures 列表
    """
    # 按五元组分组
    flows: dict[tuple, list[PacketInfo]] = defaultdict(list)
    for pkt in packets:
        key = (pkt.src_ip, pkt.dst_ip, pkt.src_port, pkt.dst_port, pkt.proto)
        flows[key].append(pkt)

    # 构建 TLS 信息索引：(src_ip, src_port) -> TLS 记录
    tls_map: dict[tuple, dict] = {}
    if tls_records:
        for rec in tls_records:
            tls_key = (rec.get("src_ip", ""), rec.get("src_port", 0))
            tls_map[tls_key] = rec

    results: list[FlowFeatures] = []
    label_rows: list[dict] = []  # T-23: 收集标签输入，批量化

    for key, pkts in flows.items():
        src_ip, dst_ip, src_port, dst_port, proto = key
        n = len(pkts)

        # 包长统计
        lengths = [p.length for p in pkts]
        avg_len = np.mean(lengths) if lengths else 0.0
        std_len = float(np.std(lengths)) if len(lengths) > 1 else 0.0
        total_bytes = sum(lengths)

        # bytes_per_packet（历史命名 up_down_byte_ratio，保留以兼容已训练模型）
        up_down_ratio = total_bytes / n if n > 0 else 0.0

        # TCP 行为特征
        syn_count = sum(1 for p in pkts if p.is_syn)
        fin_count = sum(1 for p in pkts if p.is_fin)
        rst_count = sum(1 for p in pkts if p.is_rst)
        psh_count = sum(1 for p in pkts if p.is_psh)
        window_sizes = [p.window_size for p in pkts if p.window_size > 0]

        # 端口熵（同一流内通常只有 1 个目标端口，熵为 0；跨流聚合时有用）
        dst_ports = [p.dst_port for p in pkts]
        port_entropy = _compute_entropy(dst_ports)

        # 时间特征
        timestamps = sorted([p.timestamp for p in pkts])
        intervals = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)] if len(timestamps) > 1 else [0.0]
        avg_interval = float(np.mean(intervals)) if intervals else 0.0
        std_interval = float(np.std(intervals)) if len(intervals) > 1 else 0.0

        # 加密流量特征
        tls_info = tls_map.get((src_ip, src_port), {})
        ja3_hash = tls_info.get("ja3_hash", "")
        tls_version = tls_info.get("tls_version", "")
        cipher_count = tls_info.get("cipher_count", 0)

        # 最大包长（区别于 avg_pkt_len，反映异常大包/小包）
        max_len = max(lengths) if lengths else 0.0

        # 持续时间
        duration = (timestamps[-1] - timestamps[0]) if len(timestamps) > 1 else 0.0

        # T-23: 收集标签输入数据，循环结束后批量计算
        label_rows.append({
            "pkt_count": float(n),
            "syn_flag_ratio": syn_count / n if n > 0 else 0.0,
            "dst_port_entropy": port_entropy,
            "duration": duration,
            "psh_flag_ratio": psh_count / n if n > 0 else 0.0,
            "avg_pkt_len": avg_len,
            "up_down_byte_ratio": up_down_ratio,  # bytes_per_packet: 总字节/包数
            "rst_flag_ratio": rst_count / n if n > 0 else 0.0,
        })

        flow = FlowFeatures(
            src_ip=src_ip, dst_ip=dst_ip, src_port=src_port, dst_port=dst_port,
            proto=proto,
            avg_pkt_len=avg_len, std_pkt_len=std_len,
            up_down_byte_ratio=up_down_ratio, pkt_count=n, total_bytes=total_bytes,
            syn_flag_ratio=syn_count / n if n > 0 else 0.0,
            fin_flag_ratio=fin_count / n if n > 0 else 0.0,
            rst_flag_ratio=rst_count / n if n > 0 else 0.0,
            psh_flag_ratio=psh_count / n if n > 0 else 0.0,
            avg_window_size=float(np.mean(window_sizes)) if window_sizes else 0.0,
            dst_port_entropy=port_entropy,
            avg_pkt_interval=avg_interval, std_pkt_interval=std_interval,
            ja3_hash_enc=_ja3_to_numeric(ja3_hash),
            tls_version_enc=TLS_VERSION_ENCODE.get(tls_version, -1),
            cipher_suite_count=cipher_count,
            max_pkt_len=max_len, duration=duration,
        )
        results.append(flow)

    # T-23: 批量启发式标签——一次 vectorized_rule_predict 代替逐流调用
    labels = _batch_heuristic_labels(label_rows)
    for flow, label in zip(results, labels):
        flow.label = label

    return results


def _batch_heuristic_labels(flow_rows: list[dict]) -> list[str]:
    """批量启发式标签：一次 vectorized_rule_predict 调用处理所有流。

    T-23: 替代逐流单行 DataFrame 的 _heuristic_label，减少重复 DataFrame 构造开销。

    Args:
        flow_rows: 每个元素为含 pkt_count/syn_flag_ratio/dst_port_entropy/
                   duration/psh_flag_ratio/avg_pkt_len/bytes_per_packet(up_down_byte_ratio)/rst_flag_ratio 的字典

    Returns:
        与 flow_rows 等长的标签列表（"Attack" / "Normal"）
    """
    import pandas as pd
    from campus_ids.detector.detector import vectorized_rule_predict

    if not flow_rows:
        return []

    # 空包流直接标 Normal
    labels = ["Normal"] * len(flow_rows)
    nonzero_indices = [i for i, r in enumerate(flow_rows) if r["pkt_count"] > 0]
    if not nonzero_indices:
        return labels

    df = pd.DataFrame([flow_rows[i] for i in nonzero_indices])
    is_attack = vectorized_rule_predict(df)
    for idx, attack in zip(nonzero_indices, is_attack):
        labels[idx] = "Attack" if bool(attack) else "Normal"
    return labels


def flow_to_feature_vector(flow: FlowFeatures) -> list[float]:
    """将 FlowFeatures 转为特征向量（与 FEATURE_NAMES 对应）。"""
    return [
        flow.avg_pkt_len, flow.std_pkt_len, flow.up_down_byte_ratio,
        flow.pkt_count, flow.total_bytes,
        flow.syn_flag_ratio, flow.fin_flag_ratio, flow.rst_flag_ratio,
        flow.psh_flag_ratio, flow.avg_window_size,
        flow.dst_port_entropy,
        flow.avg_pkt_interval, flow.std_pkt_interval,
        flow.ja3_hash_enc, flow.tls_version_enc, flow.cipher_suite_count,
        flow.max_pkt_len, flow.duration,
    ]


def flow_to_csv_row(flow: FlowFeatures) -> list:
    """将 FlowFeatures 转为 CSV 行（含元信息 + 特征 + 标签）。"""
    return [
        flow.src_ip, flow.dst_ip, flow.src_port, flow.dst_port, flow.proto,
        flow.avg_pkt_len, flow.std_pkt_len, flow.up_down_byte_ratio,
        flow.pkt_count, flow.total_bytes,
        flow.syn_flag_ratio, flow.fin_flag_ratio, flow.rst_flag_ratio,
        flow.psh_flag_ratio, flow.avg_window_size,
        flow.dst_port_entropy,
        flow.avg_pkt_interval, flow.std_pkt_interval,
        flow.ja3_hash_enc, flow.tls_version_enc, flow.cipher_suite_count,
        flow.max_pkt_len, flow.duration,
        flow.label,
    ]


CSV_HEADER = [
    "Src_IP", "Dst_IP", "Src_Port", "Dst_Port", "Protocol",
    *FEATURE_NAMES,
    "Label",
]


def save_flows_to_csv(flows: list[FlowFeatures], path: Path | None = None) -> Path:
    """将流特征保存到 CSV 文件。"""
    out = path or TRAFFIC_CSV
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for flow in flows:
            writer.writerow(flow_to_csv_row(flow))
    logger.info("流特征数据已保存至 %s（%d 条流）", out, len(flows))
    return out


def run_enhanced_capture(duration: int, stop_filter=None) -> tuple[int, int] | None:
    """公共增强抓包逻辑：提取多维度流特征 + TLS 分析，保存 CSV。

    T-22: 合并阻塞版与线程版的公共逻辑，消除重复。

    Args:
        duration: 抓包时长（秒）
        stop_filter: 可选 scapy stop_filter 回调，返回 True 时提前终止

    Returns:
        (packet_count, flow_count) 成功时，None 失败时
    """
    from scapy.all import sniff, IP, TCP
    from campus_ids.capture.tls_analyzer import tls_analyzer

    logger.info("开始增强抓包，持续 %s 秒...", duration)
    packets_info: list[PacketInfo] = []

    def _on_pkt(pkt):
        info = extract_packet_info(pkt)
        if info:
            packets_info.append(info)
        # 同时解析 TLS
        if pkt.haslayer(IP) and pkt.haslayer(TCP):
            tls_analyzer.parse_tls_from_packet(pkt)

    try:
        sniff(prn=_on_pkt, store=False, timeout=duration, stop_filter=stop_filter)
    except RuntimeError as exc:
        logger.error("抓包失败: %s", exc)
        return None

    # 获取 TLS 记录
    all_tls = [
        {"src_ip": r.src_ip, "src_port": r.src_port, "ja3_hash": r.ja3_hash,
         "tls_version": r.tls_version, "cipher_count": r.cipher_count}
        for r in tls_analyzer.get_all_records()
    ]

    # 聚合流特征
    flows = aggregate_flow_features(packets_info, all_tls)
    save_flows_to_csv(flows)

    logger.info("增强抓包完成：捕获 %d 个包，聚合为 %d 条流", len(packets_info), len(flows))
    return len(packets_info), len(flows)





