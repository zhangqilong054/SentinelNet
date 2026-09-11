from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path

from campus_ids.config import (
    HIGH_FREQ_IP_THRESHOLD,
    PORT_SCAN_THRESHOLD,
    TRAFFIC_CSV,
)

logger = logging.getLogger(__name__)

# 保留模块级别名，供 label_packets() 和 start_capture() 使用
OUTPUT_CSV = TRAFFIC_CSV


def _protocol_name(proto_num: int) -> str:
    if proto_num == 6:
        return "TCP"
    if proto_num == 17:
        return "UDP"
    return "Other"


def process_packet(pkt, feature_list: list):
    """Process one captured packet, append to the given feature_list."""
    from scapy.all import IP, TCP, UDP

    if pkt.haslayer(IP) and (pkt.haslayer(TCP) or pkt.haslayer(UDP)):
        src_ip = pkt[IP].src
        dst_ip = pkt[IP].dst
        l4 = pkt[TCP] if pkt.haslayer(TCP) else pkt[UDP]
        src_port = l4.sport
        dst_port = l4.dport
        proto = _protocol_name(pkt[IP].proto)
        pkt_len = len(pkt)
        timestamp = float(pkt.time)

        row = [src_ip, dst_ip, src_port, dst_port, proto, pkt_len, timestamp]
        feature_list.append(row)
        logger.info("捕获流量: %s -> %s, 长度: %s", src_ip, dst_ip, pkt_len)


def label_packets(rows: list[list]) -> list[list]:
    """基于本次 session 的统计给每行打 Normal/Attack Label。"""
    src_count: dict[str, int] = defaultdict(int)
    src_ports: dict[str, set] = defaultdict(set)
    for r in rows:
        src_count[r[0]] += 1
        src_ports[r[0]].add(r[3])
    attack_ips = set()
    for ip, cnt in src_count.items():
        if cnt > HIGH_FREQ_IP_THRESHOLD or len(src_ports[ip]) > PORT_SCAN_THRESHOLD:
            attack_ips.add(ip)
    return [r + (["Attack"] if r[0] in attack_ips else ["Normal"]) for r in rows]


def start_capture(duration=60):
    from scapy.all import sniff

    logger.info("开始抓包，持续 %s 秒...", duration)
    # P0-3 fix: 使用局部变量，每次调用独立，避免全局累积
    local_feature_list: list = []
    try:
        sniff(prn=lambda pkt: process_packet(pkt, local_feature_list), store=False, timeout=duration)
    except RuntimeError as exc:
        logger.error("抓包失败: 当前环境不支持 WinPcap/Npcap 方式的二层抓包。")
        logger.error("请安装 Npcap，或者以管理员权限运行，并确保 Scapy 可访问抓包驱动。")
        logger.error("原始错误: %s", exc)
        return False

    labeled_rows = label_packets(local_feature_list)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Src_IP", "Dst_IP", "Src_Port", "Dst_Port", "Protocol", "Length", "Timestamp", "Label"])
        writer.writerows(labeled_rows)

    logger.info("特征数据已保存至 %s", OUTPUT_CSV)
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(0 if start_capture() else 1)
