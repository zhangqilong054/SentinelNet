"""Web 监控辅助模块 — 全局状态、抓包线程、流量更新与保存。"""
from __future__ import annotations

import csv
import logging
import queue
import random
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

from campus_ids.detector.detector import AnomalyDetector
from campus_ids.capture.tls_analyzer import tls_analyzer
from campus_ids.detector.dual_detector import DualDetector
from campus_ids.config import (
    DDOS_THRESHOLD, PORT_SCAN_THRESHOLD,
    SYN_FLOOD_THRESHOLD, UDP_FLOOD_THRESHOLD, BRUTE_FORCE_THRESHOLD,
    BRUTE_FORCE_WINDOW_SEC, LATERAL_MOVEMENT_THRESHOLD, BRUTE_FORCE_PORTS,
    DNS_PORT, TLS_PORTS, WINDOW_SIZE, MAX_ALERT_HISTORY, MAX_TRAFFIC_HISTORY,
    MAX_ALERT_API_RETURN, MAX_CHART_LABELS, WEB_PORT, WEB_REFRESH_INTERVAL_MS,
    MODEL_PATH, TRAFFIC_STATS_CSV, ML_INTERVAL_SEC, DEMO_MODE, DEMO_QPS_MIN, DEMO_QPS_MAX,
    DEMO_CONN_MIN, DEMO_CONN_MAX, DEMO_SYN_MIN, DEMO_SYN_MAX,
    DEMO_UDP_MIN, DEMO_UDP_MAX, DEMO_DNS_MIN, DEMO_DNS_MAX,
    DEMO_PKT_MIN, DEMO_PKT_MAX,
)

logger = logging.getLogger(__name__)

# ── 全局配置字典 ────────────────────────────────────────────────────
CONFIG = {
    'port': WEB_PORT,
    'refresh_interval': WEB_REFRESH_INTERVAL_MS,
    'ddos_threshold': DDOS_THRESHOLD,
    'port_scan_threshold': PORT_SCAN_THRESHOLD,
    'syn_flood_threshold': SYN_FLOOD_THRESHOLD,
    'udp_flood_threshold': UDP_FLOOD_THRESHOLD,
    'brute_force_threshold': BRUTE_FORCE_THRESHOLD,
    'brute_force_window': BRUTE_FORCE_WINDOW_SEC,
    'lateral_movement_threshold': LATERAL_MOVEMENT_THRESHOLD,
}

# ── 全局状态 ────────────────────────────────────────────────────────
WINDOW_SIZE_CFG = WINDOW_SIZE

_state_lock = threading.Lock()

_packet_queue: "queue.Queue[dict]" = queue.Queue()
_capture_running = False
_capture_thread: threading.Thread | None = None

traffic_data = {
    'qps': 200,
    'connections': 80,
    'alert': None,
    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    'packet_count': 0,
    'unique_ports': deque(maxlen=WINDOW_SIZE_CFG),
    'src_ips': deque(maxlen=WINDOW_SIZE_CFG),
    'syn_packets': 0,
    'udp_packets': 0,
    'dns_packets': 0,
}

alert_history: list[dict] = []
traffic_history: list[dict] = []

OUTPUT_CSV = TRAFFIC_STATS_CSV

_rule_detector = AnomalyDetector(
    ddos_threshold=DDOS_THRESHOLD,
    port_scan_threshold=PORT_SCAN_THRESHOLD,
    syn_flood_threshold=SYN_FLOOD_THRESHOLD,
    udp_flood_threshold=UDP_FLOOD_THRESHOLD,
    brute_force_threshold=BRUTE_FORCE_THRESHOLD,
    brute_force_window=BRUTE_FORCE_WINDOW_SEC,
    lateral_movement_threshold=LATERAL_MOVEMENT_THRESHOLD,
)

dual_detector = DualDetector(
    rule_detector=_rule_detector,
    model_path=MODEL_PATH,
    ml_interval=ML_INTERVAL_SEC,
)

_recent_packets: list[dict] = []


# ── 抓包线程 ────────────────────────────────────────────────────────

def _capture_worker():
    """后台抓包线程：持续抓包并把关键信息放入队列。"""
    from scapy.all import IP, TCP, UDP, sniff

    def _on_pkt(pkt):
        if not _capture_running:
            return
        if pkt.haslayer(IP) and (pkt.haslayer(TCP) or pkt.haslayer(UDP)):
            l4 = pkt[TCP] if pkt.haslayer(TCP) else pkt[UDP]
            proto = "TCP" if pkt.haslayer(TCP) else "UDP"
            is_syn = bool(pkt.haslayer(TCP) and pkt[TCP].flags & 0x02)
            is_dns = bool(pkt.haslayer(UDP) and int(l4.dport) == DNS_PORT)
            try:
                _packet_queue.put_nowait({
                    'length': len(pkt),
                    'sport': int(l4.sport),
                    'dport': int(l4.dport),
                    'src_ip': pkt[IP].src,
                    'proto': proto,
                    'is_syn': is_syn,
                    'is_dns': is_dns,
                })
            except queue.Full:
                pass

            if pkt.haslayer(TCP) and int(l4.dport) in TLS_PORTS:
                tls_analyzer.parse_tls_from_packet(pkt)

    try:
        sniff(prn=_on_pkt, store=False, stop_filter=lambda _: not _capture_running)
    except Exception as exc:
        logger.error("后台抓包线程异常退出: %s", exc)


def start_capture_thread():
    """启动后台抓包线程（幂等）。"""
    global _capture_running, _capture_thread
    with _state_lock:
        if _capture_running:
            return True
        _capture_running = True
    _capture_thread = threading.Thread(target=_capture_worker, daemon=True)
    _capture_thread.start()
    logger.info("后台抓包线程已启动")
    return True


def stop_capture_thread():
    """停止后台抓包线程。"""
    global _capture_running
    with _state_lock:
        _capture_running = False
    logger.info("后台抓包线程已停止")
    return True


# ── 数据处理 ────────────────────────────────────────────────────────

def _drain_packets():
    """从队列取出所有包并返回统计信息，无包时返回 None。同时保存原始包到 _recent_packets。"""
    global _recent_packets
    packets = []
    while True:
        try:
            packets.append(_packet_queue.get_nowait())
        except queue.Empty:
            break
    if not packets:
        return None
    _recent_packets = packets
    for p in packets:
        dual_detector.add_packet(p)
    return {
        'count': len(packets),
        'syn_count': sum(1 for p in packets if p.get('is_syn', False)),
        'udp_count': sum(1 for p in packets if p['proto'] == 'UDP'),
        'dns_count': sum(1 for p in packets if p.get('is_dns', False)),
        'dports': set(p['dport'] for p in packets),
        'src_ips': set(p['src_ip'] for p in packets),
        'avg_len': sum(p['length'] for p in packets) // len(packets),
    }


def update_traffic_data():
    """更新流量数据（优先使用真实抓包数据，无包时回退到随机模拟）。"""
    global traffic_data

    stats = _drain_packets()

    with _state_lock:
        if stats is not None:
            qps = stats['count']
            connections = len(stats['src_ips'])
            syn_count = stats['syn_count']
            udp_count = stats['udp_count']
            dns_count = stats.get('dns_count', 0)
            traffic_data['packet_count'] += stats['count']
            for p in stats['dports']:
                traffic_data['unique_ports'].append(p)
            for ip in stats['src_ips']:
                traffic_data['src_ips'].append(ip)
        else:
            qps = random.randint(DEMO_QPS_MIN, DEMO_QPS_MAX)
            connections = random.randint(DEMO_CONN_MIN, DEMO_CONN_MAX)
            syn_count = random.randint(DEMO_SYN_MIN, DEMO_SYN_MAX)
            udp_count = random.randint(DEMO_UDP_MIN, DEMO_UDP_MAX)
            dns_count = random.randint(DEMO_DNS_MIN, DEMO_DNS_MAX)
            traffic_data['packet_count'] += random.randint(DEMO_PKT_MIN, DEMO_PKT_MAX)
            traffic_data['unique_ports'].append(random.randint(1, 65535))
            traffic_data['unique_ports'].append(random.randint(1, 65535))
            traffic_data['src_ips'].append(f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}")

        traffic_data['qps'] = qps
        traffic_data['connections'] = connections
        traffic_data['syn_packets'] = syn_count
        traffic_data['udp_packets'] = udp_count
        traffic_data['dns_packets'] = dns_count
        traffic_data['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        packets_for_ml = []
        if stats is not None:
            packets_for_ml = _recent_packets

        result = dual_detector.detect(
            qps=qps,
            port_count=len(set(traffic_data['unique_ports'])),
            syn_count=syn_count,
            udp_count=udp_count,
            packets=packets_for_ml,
        )

        if result.is_anomaly:
            level_tag = {"high": "🔴高危", "medium": "🟠中危", "low": "🟡低危"}.get(result.level, "⚠️异常")
            alert_msg = f"{level_tag} {result.description}"
            traffic_data['alert'] = alert_msg
            alert_entry = {
                'time': traffic_data['timestamp'],
                'message': alert_msg,
                'level': result.level,
                'attack_type': result.attack_type,
                'ml_confidence': result.ml_confidence,
            }
            alert_history.insert(0, alert_entry)
            if len(alert_history) > MAX_ALERT_HISTORY:
                alert_history.pop()
        else:
            traffic_data['alert'] = None

        history_entry = {
            'time': traffic_data['timestamp'],
            'qps': qps,
            'connections': connections,
            'packet_count': traffic_data['packet_count'],
            'port_count': len(set(traffic_data['unique_ports'])),
            'src_ip_count': len(set(traffic_data['src_ips'])),
            'alert': traffic_data['alert']
        }
        traffic_history.append(history_entry)
        if len(traffic_history) > MAX_TRAFFIC_HISTORY:
            traffic_history.pop(0)


def save_traffic_data():
    """将流量历史保存到 CSV 文件。"""
    with _state_lock:
        with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Time", "QPS", "Connections", "PacketCount", "PortCount", "SrcIPCount", "Alert"])
            for entry in traffic_history:
                writer.writerow([
                    entry['time'],
                    entry['qps'],
                    entry['connections'],
                    entry['packet_count'],
                    entry['port_count'],
                    entry['src_ip_count'],
                    entry['alert'] if entry['alert'] else "Normal"
                ])