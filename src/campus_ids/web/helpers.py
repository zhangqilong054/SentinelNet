"""Web 监控辅助模块 — 全局状态、抓包线程、流量更新与保存。"""
from __future__ import annotations

import csv
import logging
import queue
import random
import threading
import time as _time
from collections import deque
from datetime import datetime
from pathlib import Path

from campus_ids.detector.detector import create_rule_detector
from campus_ids.capture.tls_analyzer import tls_analyzer
from campus_ids.capture.enhanced_features import _parse_base_fields
from campus_ids.detector.dual_detector import DualDetector
from campus_ids.config import (
    DDOS_THRESHOLD, PORT_SCAN_THRESHOLD,
    SYN_FLOOD_THRESHOLD, UDP_FLOOD_THRESHOLD, BRUTE_FORCE_THRESHOLD,
    BRUTE_FORCE_WINDOW_SEC, LATERAL_MOVEMENT_THRESHOLD, BRUTE_FORCE_PORTS,
    DNS_PORT, TLS_PORTS, WINDOW_SIZE,
    MAX_ALERT_API_RETURN, MAX_CHART_LABELS, WEB_PORT, WEB_REFRESH_INTERVAL_MS,
    MODEL_PATH, TRAFFIC_STATS_CSV, ML_INTERVAL_SEC, DEMO_QPS_MIN, DEMO_QPS_MAX,
    DEMO_CONN_MIN, DEMO_CONN_MAX, DEMO_SYN_MIN, DEMO_SYN_MAX,
    DEMO_UDP_MIN, DEMO_UDP_MAX, DEMO_DNS_MIN, DEMO_DNS_MAX,
    DEMO_PKT_MIN, DEMO_PKT_MAX,
)
from campus_ids.web.database import (
    init_db, insert_alert, insert_traffic, get_all_config,
    bulk_set_config, cleanup_old_data,
)

logger = logging.getLogger(__name__)

# ── 全局配置字典 ────────────────────────────────────────────────────
# R-11: CONFIG 字典从 config.py 常量生成，键名与常量名对应关系如下：
#   port ← WEB_PORT, refresh_interval ← WEB_REFRESH_INTERVAL_MS,
#   ddos_threshold ← DDOS_THRESHOLD, port_scan_threshold ← PORT_SCAN_THRESHOLD,
#   syn_flood_threshold ← SYN_FLOOD_THRESHOLD, udp_flood_threshold ← UDP_FLOOD_THRESHOLD,
#   brute_force_threshold ← BRUTE_FORCE_THRESHOLD,
#   brute_force_window ← BRUTE_FORCE_WINDOW_SEC,
#   lateral_movement_threshold ← LATERAL_MOVEMENT_THRESHOLD
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

# ── 数据库初始化 ──────────────────────────────────────────────────
init_db()

# 需要持久化到 DB 的配置键集合
_THRESHOLD_KEYS = frozenset({
    'ddos_threshold', 'port_scan_threshold', 'syn_flood_threshold',
    'udp_flood_threshold', 'brute_force_threshold',
    'brute_force_window', 'lateral_movement_threshold',
})


def update_config(key: str, value, *, persist: bool = False) -> None:
    """更新配置项，自动尝试 int 转换。

    Args:
        key: CONFIG 字典中的键名。
        value: 新值（自动尝试 int 转换，失败则保留原类型）。
        persist: 是否同步持久化到 SQLite。
    """
    try:
        CONFIG[key] = int(value)
    except (ValueError, TypeError):
        CONFIG[key] = value
    if persist and key in _THRESHOLD_KEYS:
        bulk_set_config({key: str(CONFIG[key])})


# 从数据库恢复已保存的配置（覆盖默认值）
_saved_config = get_all_config()
for _key, _val in _saved_config.items():
    if _key in CONFIG:
        update_config(_key, _val)

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

# M3: SSE 广播回调列表 — 当告警/流量更新时通知订阅者
_on_alert_callbacks: list = []
_on_traffic_callbacks: list = []

_rule_detector = create_rule_detector()

dual_detector = DualDetector(
    rule_detector=_rule_detector,
    model_path=MODEL_PATH,
    ml_interval=ML_INTERVAL_SEC,
)

_recent_packets: list[dict] = []

# 上次流量更新时间（用于计算真实 QPS）
_last_update_time: float = 0.0


# ── 抓包线程 ────────────────────────────────────────────────────────

def _capture_worker():
    """后台抓包线程：持续抓包并把关键信息放入队列。"""
    from scapy.all import IP, TCP, UDP, sniff

    def _on_pkt(pkt):
        if not _capture_running:
            return
        base = _parse_base_fields(pkt)
        if base is not None:
            _, l4, proto, src_ip, _, _, dst_port, pkt_len, _ = base
            is_syn = bool(pkt.haslayer(TCP) and pkt[TCP].flags & 0x02)
            is_dns = bool(pkt.haslayer(UDP) and dst_port == DNS_PORT)
            try:
                _packet_queue.put_nowait({
                    'length': pkt_len,
                    'sport': int(l4.sport),
                    'dport': dst_port,
                    'src_ip': src_ip,
                    'proto': proto,
                    'is_syn': is_syn,
                    'is_dns': is_dns,
                })
            except queue.Full:
                pass

            if pkt.haslayer(TCP) and dst_port in TLS_PORTS:
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


# ── 增强抓包线程 ──────────────────────────────────────────────────

_enhanced_capture_running = False
_enhanced_capture_thread: threading.Thread | None = None
_enhanced_capture_result: dict = {}


def _enhanced_capture_worker(duration: int):
    """后台增强抓包线程：提取 18 维流特征 + TLS 分析，保存 CSV。"""
    global _enhanced_capture_running, _enhanced_capture_result
    from scapy.all import sniff as scapy_sniff
    from campus_ids.capture.enhanced_features import (
        extract_packet_info, aggregate_flow_features, save_flows_to_csv,
    )
    from campus_ids.capture.tls_analyzer import tls_analyzer
    from scapy.all import IP, TCP

    logger.info("增强抓包线程启动，持续 %d 秒", duration)
    _enhanced_capture_result = {
        'status': 'running', 'duration': duration,
        'packets': 0, 'flows': 0, 'error': None,
    }

    packets_info: list = []
    stop_time = _time.time() + duration

    def _on_pkt(pkt):
        if not _enhanced_capture_running:
            return
        info = extract_packet_info(pkt)
        if info:
            packets_info.append(info)
        if pkt.haslayer(IP) and pkt.haslayer(TCP):
            tls_analyzer.parse_tls_from_packet(pkt)

    try:
        scapy_sniff(
            prn=_on_pkt, store=False,
            stop_filter=lambda _: not _enhanced_capture_running or _time.time() >= stop_time,
            timeout=duration + 5,
        )
    except Exception as exc:
        logger.error("增强抓包异常: %s", exc)
        _enhanced_capture_result = {
            'status': 'error', 'duration': duration,
            'packets': 0, 'flows': 0, 'error': str(exc),
        }
        _enhanced_capture_running = False
        return

    # 聚合流特征
    all_tls = [
        {"src_ip": r.src_ip, "src_port": r.src_port, "ja3_hash": r.ja3_hash,
         "tls_version": r.tls_version, "cipher_count": r.cipher_count}
        for r in tls_analyzer.get_all_records()
    ]
    flows = aggregate_flow_features(packets_info, all_tls)
    save_flows_to_csv(flows)

    logger.info("增强抓包完成：捕获 %d 个包，聚合为 %d 条流", len(packets_info), len(flows))
    _enhanced_capture_result = {
        'status': 'completed', 'duration': duration,
        'packets': len(packets_info), 'flows': len(flows), 'error': None,
    }
    _enhanced_capture_running = False


def start_enhanced_capture_thread(duration: int = 60) -> bool:
    """启动后台增强抓包线程（幂等）。"""
    global _enhanced_capture_running, _enhanced_capture_thread
    with _state_lock:
        if _enhanced_capture_running:
            return False
        _enhanced_capture_running = True
    _enhanced_capture_thread = threading.Thread(
        target=_enhanced_capture_worker, args=(duration,), daemon=True,
    )
    _enhanced_capture_thread.start()
    logger.info("增强抓包线程已启动 (duration=%ds)", duration)
    return True


def stop_enhanced_capture_thread() -> bool:
    """停止增强抓包线程。"""
    global _enhanced_capture_running
    with _state_lock:
        _enhanced_capture_running = False
    logger.info("增强抓包线程已停止")
    return True


def get_enhanced_capture_status() -> dict:
    """获取增强抓包状态。"""
    with _state_lock:
        running = _enhanced_capture_running
    result = dict(_enhanced_capture_result) if _enhanced_capture_result else {}
    result['running'] = running
    return result


# ── 一键全流程状态 ────────────────────────────────────────────────

_auto_status: dict = {
    'running': False, 'step': 0, 'total_steps': 3,
    'step_name': '', 'message': '', 'error': None, 'result': None,
}
_auto_lock = threading.Lock()


def get_auto_status() -> dict:
    """获取一键全流程状态。"""
    with _auto_lock:
        return dict(_auto_status)


def _auto_worker(duration: int):
    """一键全流程后台线程：增强抓包 → 训练 → ML 加载。"""
    global _auto_status
    from campus_ids.capture.enhanced_features import start_enhanced_capture
    from campus_ids.model.train import train
    from campus_ids.config import MODEL_PATH, TRAFFIC_CSV

    with _auto_lock:
        _auto_status['running'] = True
        _auto_status['error'] = None
        _auto_status['result'] = None

    try:
        # Step 1: 增强抓包
        with _auto_lock:
            _auto_status['step'] = 1
            _auto_status['step_name'] = '增强抓包'
            _auto_status['message'] = f'正在抓包 ({duration}s)...'
        success = start_enhanced_capture(duration)
        if not success:
            if not TRAFFIC_CSV.exists():
                with _auto_lock:
                    _auto_status['running'] = False
                    _auto_status['error'] = '抓包失败且无已有数据'
                return
            with _auto_lock:
                _auto_status['message'] = '抓包失败，使用已有数据继续'

        # Step 2: 模型训练
        with _auto_lock:
            _auto_status['step'] = 2
            _auto_status['step_name'] = '模型训练'
            _auto_status['message'] = '正在训练模型...'
        train_result = train()
        if not MODEL_PATH.exists():
            with _auto_lock:
                _auto_status['running'] = False
                _auto_status['error'] = '训练完成但模型文件未生成'
            return

        # Step 3: 加载 ML 模型
        with _auto_lock:
            _auto_status['step'] = 3
            _auto_status['step_name'] = '加载 ML 模型'
            _auto_status['message'] = '正在加载 ML 模型...'
        try:
            dual_detector.load_model(which='best')
            dual_detector.start_ml_loop()
            ml_loaded = True
        except Exception as exc:
            logger.warning("一键全流程: ML 加载失败: %s", exc)
            ml_loaded = False

        with _auto_lock:
            _auto_status['running'] = False
            _auto_status['message'] = '全流程完成'
            _auto_status['result'] = {
                'capture': True,
                'trained': True,
                'ml_loaded': ml_loaded,
            }

    except Exception as exc:
        logger.error("一键全流程异常: %s", exc)
        with _auto_lock:
            _auto_status['running'] = False
            _auto_status['error'] = str(exc)


def start_auto_thread(duration: int = 30) -> bool:
    """启动一键全流程后台线程。"""
    with _auto_lock:
        if _auto_status['running']:
            return False
    t = threading.Thread(target=_auto_worker, args=(duration,), daemon=True)
    t.start()
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
    """更新流量数据（优先使用真实抓包数据，无包时回退到随机模拟）。

    R-14: QPS/SYN/UDP 计数归一化为每秒值，与阈值语义对齐。
    """
    global traffic_data, _last_update_time

    stats = _drain_packets()
    now = _time.time()
    interval = now - _last_update_time if _last_update_time > 0 else 1.0
    _last_update_time = now
    # 防止除零或极短间隔导致数值爆炸
    if interval < 0.1:
        interval = 0.1

    with _state_lock:
        current_port_count = 0  # 当前周期唯一端口数（用于检测，避免累积窗口误报）
        if stats is not None:
            # 归一化为每秒计数，与检测阈值（QPS/SYN/UDP per second）对齐
            qps = int(stats['count'] / interval)
            connections = len(stats['src_ips'])
            syn_count = int(stats['syn_count'] / interval)
            udp_count = int(stats['udp_count'] / interval)
            dns_count = int(stats.get('dns_count', 0) / interval)
            current_port_count = len(stats['dports'])
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
            current_port_count = 2  # 演示模式每周期添加 2 个随机端口
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
            port_count=current_port_count,
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
            # M2: 持久化告警到 SQLite
            try:
                insert_alert(
                    time=traffic_data['timestamp'],
                    level=result.level,
                    attack_type=result.attack_type,
                    message=alert_msg,
                    ml_confidence=result.ml_confidence,
                )
            except Exception as exc:
                logger.warning("告警写入数据库失败: %s", exc)
            # M3: 通知 SSE 订阅者
            for cb in _on_alert_callbacks:
                try:
                    cb(alert_entry)
                except Exception as exc:
                    logger.warning("SSE 告警回调失败: %s", exc)
        else:
            traffic_data['alert'] = None

        history_entry = {
            'time': traffic_data['timestamp'],
            'qps': qps,
            'connections': connections,
            'packet_count': traffic_data['packet_count'],
            'port_count': current_port_count,
            'src_ip_count': len(set(traffic_data['src_ips'])),
            'alert': traffic_data['alert']
        }
        # M2: 持久化流量历史到 SQLite
        try:
            insert_traffic(
                time=traffic_data['timestamp'],
                qps=qps,
                connections=connections,
                packet_count=traffic_data['packet_count'],
                port_count=current_port_count,
                src_ip_count=len(set(traffic_data['src_ips'])),
                alert=traffic_data['alert'],
            )
        except Exception as exc:
            logger.warning("流量历史写入数据库失败: %s", exc)
        # M3: 通知 SSE 订阅者
        for cb in _on_traffic_callbacks:
            try:
                cb(history_entry)
            except Exception as exc:
                logger.warning("SSE 流量回调失败: %s", exc)


def save_traffic_data():
    """将流量历史保存到 CSV 文件（从 SQLite 查询，不再依赖内存列表）。"""
    from campus_ids.web.database import query_traffic
    rows = query_traffic(limit=10000)
    with TRAFFIC_STATS_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Time", "QPS", "Connections", "PacketCount", "PortCount", "SrcIPCount", "Alert"])
        for entry in rows:
            writer.writerow([
                entry.get('time', ''),
                entry.get('qps', ''),
                entry.get('connections', ''),
                entry.get('packet_count', ''),
                entry.get('port_count', ''),
                entry.get('src_ip_count', ''),
                entry.get('alert') or "Normal",
            ])