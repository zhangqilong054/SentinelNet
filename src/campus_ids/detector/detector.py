from __future__ import annotations

import logging
import re
from collections import defaultdict
from time import time

import numpy as np
import pandas as pd

from campus_ids.config import (
    DDOS_THRESHOLD, PORT_SCAN_THRESHOLD, SYN_FLOOD_THRESHOLD,
    UDP_FLOOD_THRESHOLD, BRUTE_FORCE_THRESHOLD, BRUTE_FORCE_WINDOW_SEC,
    LATERAL_MOVEMENT_THRESHOLD,
)

logger = logging.getLogger(__name__)

# P1-#9: 应用层攻击载荷特征
_SQL_INJECTION_PATTERNS = [
    re.compile(r"(\bunion\b.*\bselect\b)", re.IGNORECASE),
    re.compile(r"(\bselect\b.*\bfrom\b)", re.IGNORECASE),
    re.compile(r"(\binsert\b.*\binto\b)", re.IGNORECASE),
    re.compile(r"(\bdrop\b\s+table\b)", re.IGNORECASE),
    re.compile(r"(\bor\b\s+1\s*=\s*1)", re.IGNORECASE),
    re.compile(r"(\b'\s*or\s*'\s*=\s*')", re.IGNORECASE),
    re.compile(r"(\bexec\b.*\bxp_cmdshell\b)", re.IGNORECASE),
    re.compile(r"(;\s*--)", re.IGNORECASE),
]

_XSS_PATTERNS = [
    re.compile(r"<script[^>]*>", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"on(error|load|click|mouseover)\s*=", re.IGNORECASE),
    re.compile(r"<img[^>]+on\w+\s*=", re.IGNORECASE),
    re.compile(r"<iframe[^>]*>", re.IGNORECASE),
    re.compile(r"document\.(cookie|location|write)", re.IGNORECASE),
    re.compile(r"eval\s*\(", re.IGNORECASE),
]


class AnomalyDetector:
    def __init__(self, ddos_threshold=None, port_scan_threshold=None,
                 syn_flood_threshold=None, udp_flood_threshold=None,
                 brute_force_threshold=None, brute_force_window=None,
                 lateral_movement_threshold=None):
        # T-21: None 哨兵 → 取 config 常量，确保单一真值源
        self.ddos_threshold = ddos_threshold if ddos_threshold is not None else DDOS_THRESHOLD
        self.port_scan_threshold = port_scan_threshold if port_scan_threshold is not None else PORT_SCAN_THRESHOLD
        self.syn_flood_threshold = syn_flood_threshold if syn_flood_threshold is not None else SYN_FLOOD_THRESHOLD
        self.udp_flood_threshold = udp_flood_threshold if udp_flood_threshold is not None else UDP_FLOOD_THRESHOLD

        # P1-#9: 暴力破解检测参数
        self.brute_force_threshold = brute_force_threshold if brute_force_threshold is not None else BRUTE_FORCE_THRESHOLD  # 同一IP同一端口最大连接数
        self.brute_force_window = brute_force_window if brute_force_window is not None else BRUTE_FORCE_WINDOW_SEC  # 时间窗口（秒）

        # P1-#9: 横向移动检测参数
        self.lateral_movement_threshold = lateral_movement_threshold if lateral_movement_threshold is not None else LATERAL_MOVEMENT_THRESHOLD  # 同一IP访问不同内网IP数阈值

        # 暴力破解追踪: {(src_ip, dport): [timestamp, ...]}
        self._bf_tracker: dict[tuple[str, int], list[float]] = defaultdict(list)
        # 横向移动追踪: {src_ip: set(dst_ip)}
        self._lateral_tracker: dict[str, set[str]] = defaultdict(set)

    def check_ddos(self, packet_count_per_second):
        if packet_count_per_second > self.ddos_threshold:
            return True, f"检测到疑似 DDoS 攻击 (QPS: {packet_count_per_second})"
        return False, ""

    def check_port_scan(self, unique_ports_accessed):
        if unique_ports_accessed > self.port_scan_threshold:
            return True, f"检测到疑似端口扫描行为 (访问端口数: {unique_ports_accessed})"
        return False, ""

    def check_syn_flood(self, syn_count):
        if syn_count > self.syn_flood_threshold:
            return True, f"检测到疑似 TCP SYN 洪水攻击 (SYN包: {syn_count})"
        return False, ""

    def check_udp_flood(self, udp_count):
        if udp_count > self.udp_flood_threshold:
            return True, f"检测到疑似 UDP 洪水攻击 (UDP包: {udp_count})"
        return False, ""

    # ---- P1-#9: 扩展检测类型 ----

    def check_sql_injection(self, payload: str) -> tuple[bool, str]:
        """检测 SQL 注入攻击载荷。基于正则匹配常见 SQL 注入模式。

        Args:
            payload: HTTP 请求载荷字符串（URL、body 等）
        """
        for pattern in _SQL_INJECTION_PATTERNS:
            match = pattern.search(payload)
            if match:
                return True, f"检测到疑似 SQL 注入攻击 (匹配: {match.group()[:50]})"
        return False, ""

    def check_xss(self, payload: str) -> tuple[bool, str]:
        """检测 XSS 跨站脚本攻击载荷。基于正则匹配常见 XSS 模式。

        Args:
            payload: HTTP 请求载荷字符串
        """
        for pattern in _XSS_PATTERNS:
            match = pattern.search(payload)
            if match:
                return True, f"检测到疑似 XSS 攻击 (匹配: {match.group()[:50]})"
        return False, ""

    def check_brute_force(self, src_ip: str, dport: int) -> tuple[bool, str]:
        """检测暴力破解行为。追踪同一源IP对同一目标端口的连接频率。

        Args:
            src_ip: 源 IP 地址
            dport: 目标端口
        """
        now = time()
        key = (src_ip, dport)
        # 清理过期记录
        self._bf_tracker[key] = [t for t in self._bf_tracker[key]
                                  if now - t < self.brute_force_window]
        self._bf_tracker[key].append(now)

        count = len(self._bf_tracker[key])
        if count >= self.brute_force_threshold:
            return True, (f"检测到疑似暴力破解行为 "
                          f"(IP: {src_ip}, 端口: {dport}, "
                          f"{self.brute_force_window}s内{count}次连接)")
        return False, ""

    def check_lateral_movement(self, src_ip: str, dst_ip: str) -> tuple[bool, str]:
        """检测横向移动行为。追踪同一源IP访问不同内网目标IP的数量。

        Args:
            src_ip: 源 IP 地址
            dst_ip: 目标内网 IP 地址
        """
        self._lateral_tracker[src_ip].add(dst_ip)
        count = len(self._lateral_tracker[src_ip])
        if count >= self.lateral_movement_threshold:
            return True, (f"检测到疑似横向移动行为 "
                          f"(IP: {src_ip}, 访问{count}个不同内网主机)")
        return False, ""

    def check_payload(self, payload: str) -> list[str]:
        """对 HTTP 载荷执行应用层检测（SQL 注入 + XSS）。

        Args:
            payload: HTTP 请求载荷字符串

        Returns:
            告警消息列表
        """
        alerts = []
        is_sqli, sqli_msg = self.check_sql_injection(payload)
        if is_sqli:
            alerts.append(sqli_msg)
        is_xss, xss_msg = self.check_xss(payload)
        if is_xss:
            alerts.append(xss_msg)
        return alerts


def create_rule_detector(
    ddos_threshold: int = DDOS_THRESHOLD,
    port_scan_threshold: int = PORT_SCAN_THRESHOLD,
    syn_flood_threshold: int = SYN_FLOOD_THRESHOLD,
    udp_flood_threshold: int = UDP_FLOOD_THRESHOLD,
    brute_force_threshold: int = BRUTE_FORCE_THRESHOLD,
    brute_force_window: int = BRUTE_FORCE_WINDOW_SEC,
    lateral_movement_threshold: int = LATERAL_MOVEMENT_THRESHOLD,
) -> AnomalyDetector:
    """工厂函数：使用 config 常量作为默认值创建 AnomalyDetector 实例。

    R-07: 统一实例化入口，避免阈值分散在多处。
    """
    return AnomalyDetector(
        ddos_threshold=ddos_threshold,
        port_scan_threshold=port_scan_threshold,
        syn_flood_threshold=syn_flood_threshold,
        udp_flood_threshold=udp_flood_threshold,
        brute_force_threshold=brute_force_threshold,
        brute_force_window=brute_force_window,
        lateral_movement_threshold=lateral_movement_threshold,
    )


def vectorized_rule_predict(X: pd.DataFrame) -> np.ndarray:
    """向量化规则判定：对 DataFrame 批量应用规则阈值。

    统一规则判定逻辑，避免 detector / evaluation / enhanced_features 中重复实现。
    阈值与 AnomalyDetector 默认值保持一致。

    Args:
        X: 包含流量特征的 DataFrame，需包含 pkt_count, syn_flag_ratio 等列。

    Returns:
        布尔 ndarray，True 表示判定为攻击。
    """
    _get = lambda col: X[col].values if col in X.columns else np.zeros(len(X))

    pkt_count = _get("pkt_count")
    syn_ratio = _get("syn_flag_ratio")
    port_entropy = _get("dst_port_entropy")
    duration = _get("duration")
    psh_ratio = _get("psh_flag_ratio")
    avg_pkt_len = _get("avg_pkt_len")
    up_down_ratio = _get("up_down_byte_ratio")
    rst_ratio = _get("rst_flag_ratio")
    fwd_pkt_len_mean = _get("fwd_pkt_len_mean")
    fwd_pkt_count = _get("fwd_pkt_count")
    bwd_pkt_count = _get("bwd_pkt_count")
    flow_bytes_per_sec = _get("flow_bytes_per_sec")
    flow_pkt_per_sec = _get("flow_pkt_per_sec")
    init_win_fwd = _get("init_win_bytes_fwd")
    init_win_bwd = _get("init_win_bytes_bwd")

    # ── DoS/DDoS 规则（收紧：减少 Normal 误判）──
    # 原版 pkt_count>500 太宽松，大量 Normal 高包数流被误判
    # 收紧为：高包数 + 短时间 + 高 SYN 比例 三条件联合
    is_attack = (
        (pkt_count > 800) |                                          # 极高包数（DDoS 大流量）
        ((syn_ratio > 0.9) & (pkt_count > 100)) |                   # SYN Flood 特征更严格
        ((duration < 1) & (pkt_count > 500) & (flow_pkt_per_sec > 1000))  # 突发攻击 + 高速率
    )

    # ── PortScan 规则（收紧：减少 Normal 短连接误判）──
    is_attack = is_attack | (
        (port_entropy >= 3.0) |                                      # 收紧熵阈值 2.5→3.0
        ((pkt_count > 20) & (duration > 0) & (duration < 0.01) & (port_entropy >= 2.0))  # 极快速扫描
    )

    # ── Web Attack / BruteForce 规则（保留 + 增强条件）──
    is_attack = is_attack | ((psh_ratio > 0.6) & (duration > 10) & (pkt_count > 50))
    is_attack = is_attack | ((avg_pkt_len < 100) & (pkt_count > 50) & (duration < 5))  # 收紧：加时间限制
    is_attack = is_attack | (up_down_ratio > 10)  # 收紧 5→10

    # ── RST 异常规则（收紧）──
    is_attack = is_attack | ((rst_ratio > 0.7) & (pkt_count > 30))  # 0.5→0.7, 10→30

    # ── 新增：Infiltration / Botnet 检测规则 ──
    # 小包长 + 高前向包数 + 非对称窗口（CICIDS2017 Infiltration 特征）
    is_attack = is_attack | (
        (fwd_pkt_len_mean > 0) & (fwd_pkt_len_mean < 200) &
        (fwd_pkt_count > bwd_pkt_count * 3) &
        (init_win_fwd > 0) & (init_win_bwd == 0)
    )
    # 极低速率长连接（Botnet 心跳特征）
    is_attack = is_attack | (
        (duration > 100) & (flow_pkt_per_sec > 0) & (flow_pkt_per_sec < 1) &
        (pkt_count > 5)
    )

    return is_attack



