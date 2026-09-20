"""攻击模拟与回放脚本。

P0-21: 预存演示数据（SYN Flood / 端口扫描 / DDoS）
P0-22: 攻击回放脚本（基于 Scapy sendp）
P0-24: 备选方案（模拟攻击脚本直接生成攻击流量）

用法:
    python -m campus_ids.demo.attack_sim syn_flood --count 500
    python -m campus_ids.demo.attack_sim port_scan --count 200
    python -m campus_ids.demo.attack_sim udp_flood --count 1000
    python -m campus_ids.demo.attack_sim all --duration 30
    python -m campus_ids.demo.attack_sim generate_pcap --output demo_attacks.pcap
"""
from __future__ import annotations

import argparse
import logging
import queue
import random
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 攻击源 IP 池（模拟）
ATTACK_IPS = [
    "10.0.0.100", "10.0.0.101", "10.0.0.102",
    "192.168.1.100", "192.168.1.101",
    "172.16.0.50", "172.16.0.51",
]

# 目标 IP
TARGET_IP = "192.168.1.1"

# 目标端口池
COMMON_PORTS = [22, 80, 443, 3306, 5432, 8080, 8443, 25, 53, 110]
SCAN_PORTS = list(range(1, 1025))


def _send_packets(packets: list, interval: float = 0.001, iface: str | None = None) -> int:
    """发送数据包列表。返回成功发送的数量。

    Args:
        packets: 待发送的数据包列表。
        interval: 发包间隔（秒）。
        iface: 目标网卡名（None 时由 scapy 自行选择，Windows 上可能选错）。
    """
    try:
        from scapy.all import sendp
        sent = 0
        for pkt in packets:
            try:
                sendp(pkt, verbose=False, iface=iface)
                sent += 1
                if interval > 0:
                    time.sleep(interval)
            except Exception as exc:
                logger.debug("发送失败: %s", exc)
        return sent
    except ImportError:
        logger.error("Scapy 未安装，无法发送数据包")
        return 0


def generate_syn_flood(target_ip: str = TARGET_IP, target_port: int = 80,
                       count: int = 500, src_ips: list | None = None) -> list:
    """P0-21: 生成 SYN Flood 攻击数据包。"""
    from scapy.all import IP, TCP, Ether

    src_ips = src_ips or ATTACK_IPS
    packets = []
    for i in range(count):
        src_ip = random.choice(src_ips)
        src_port = random.randint(1024, 65535)
        pkt = Ether() / IP(src=src_ip, dst=target_ip) / TCP(
            sport=src_port, dport=target_port, flags="S", window=random.randint(1000, 65535)
        )
        packets.append(pkt)
    logger.info("生成 SYN Flood 数据包 %d 个（目标: %s:%d）", count, target_ip, target_port)
    return packets


def generate_port_scan(target_ip: str = TARGET_IP, ports: list | None = None,
                       src_ip: str = "10.0.0.100") -> list:
    """P0-21: 生成端口扫描数据包。"""
    from scapy.all import IP, TCP, Ether

    ports = ports or SCAN_PORTS
    packets = []
    for port in ports:
        src_port = random.randint(1024, 65535)
        pkt = Ether() / IP(src=src_ip, dst=target_ip) / TCP(
            sport=src_port, dport=port, flags="S", window=8192
        )
        packets.append(pkt)
    logger.info("生成端口扫描数据包 %d 个（目标: %s）", len(ports), target_ip)
    return packets


def generate_udp_flood(target_ip: str = TARGET_IP, target_port: int = 53,
                       count: int = 1000, src_ips: list | None = None) -> list:
    """P0-21: 生成 UDP Flood 攻击数据包。"""
    from scapy.all import IP, UDP, Ether, Raw

    src_ips = src_ips or ATTACK_IPS
    packets = []
    for i in range(count):
        src_ip = random.choice(src_ips)
        src_port = random.randint(1024, 65535)
        payload = Raw(load=b"X" * random.randint(64, 512))
        pkt = Ether() / IP(src=src_ip, dst=target_ip) / UDP(
            sport=src_port, dport=target_port
        ) / payload
        packets.append(pkt)
    logger.info("生成 UDP Flood 数据包 %d 个（目标: %s:%d）", count, target_ip, target_port)
    return packets


def generate_ddos(target_ip: str = TARGET_IP, count: int = 2000) -> list:
    """P0-21: 生成 DDoS 攻击数据包（混合 TCP + UDP）。"""
    packets = []
    # TCP Flood
    packets.extend(generate_syn_flood(target_ip, 80, count // 2))
    # UDP Flood
    packets.extend(generate_udp_flood(target_ip, 53, count // 2))
    logger.info("生成 DDoS 数据包 %d 个（混合 TCP+UDP）", len(packets))
    return packets


def generate_all_attacks(target_ip: str = TARGET_IP) -> list:
    """P0-21: 生成所有类型的攻击数据包。"""
    packets = []
    packets.extend(generate_syn_flood(target_ip, 80, 200))
    packets.extend(generate_port_scan(target_ip))
    packets.extend(generate_udp_flood(target_ip, 53, 300))
    logger.info("生成全部攻击数据包 %d 个", len(packets))
    return packets


def save_pcap(packets: list, output: Path) -> None:
    """保存数据包到 pcap 文件。"""
    from scapy.all import wrpcap
    wrpcap(str(output), packets)
    logger.info("数据包已保存至 %s（%d 个包）", output, len(packets))


def load_and_replay(pcap_path: Path, count: int | None = None,
                    interval: float = 0.001, iface: str | None = None) -> int:
    """P0-22: 从 pcap 文件回放攻击流量。

    Args:
        pcap_path: pcap 文件路径。
        count: 最大回放包数（None=全部）。
        interval: 发包间隔（秒）。
        iface: 目标网卡名（None 时由 scapy 自行选择）。
    """
    try:
        from scapy.all import rdpcap
        packets = rdpcap(str(pcap_path))
        if count:
            packets = packets[:count]
        logger.info("从 %s 加载 %d 个数据包，开始回放...", pcap_path, len(packets))
        return _send_packets(list(packets), interval, iface=iface)
    except Exception as exc:
        logger.error("回放失败: %s", exc)
        return 0


# ── 模拟攻击（不依赖 Scapy 发包，直接注入 Web 面板数据） ─────────────

class AttackSimulator:
    """P0-24: 模拟攻击流量注入器（无需真实网络发包）。

    直接向 RuntimeState.packet_queue 注入模拟数据，
    适用于现场网络受限的场景。

    用法：传入 RuntimeState.packet_queue 作为注入目标。
    """

    def __init__(self, packet_queue: queue.Queue | None = None):
        """初始化攻击模拟器。

        Args:
            packet_queue: 注入目标队列（RuntimeState.packet_queue）。
        """
        self._running = False
        self._threads: list[threading.Thread] = []
        self._packet_queue = packet_queue

    def _get_queue(self) -> queue.Queue:
        """获取注入目标队列。"""
        if self._packet_queue is not None:
            return self._packet_queue
        # 回退到 RuntimeState.packet_queue
        from campus_ids.runtime.state import RuntimeState
        state = RuntimeState()
        if state.packet_queue is not None:
            return state.packet_queue
        raise RuntimeError(
            "AttackSimulator 需要显式传入 packet_queue，"
            "或确保 RuntimeState.packet_queue 已初始化"
        )

    def inject_syn_flood(self, duration: int = 10, rate: int = 50) -> None:
        """注入 SYN Flood 模拟数据。"""
        pkt_queue = self._get_queue()
        logger.info("注入 SYN Flood 模拟数据（%d 秒，%d 包/秒）", duration, rate)
        start = time.time()
        while time.time() - start < duration and self._running:
            for _ in range(rate):
                try:
                    pkt_queue.put_nowait({
                        'length': random.randint(40, 60),
                        'sport': random.randint(1024, 65535),
                        'dport': 80,
                        'src_ip': random.choice(ATTACK_IPS),
                        'dst_ip': TARGET_IP,
                        'proto': 'TCP',
                        'is_syn': True,
                        'is_dns': False,
                        'timestamp': time.time(),
                    })
                except Exception:
                    pass
            time.sleep(1)

    def inject_port_scan(self, duration: int = 10, rate: int = 30) -> None:
        """注入端口扫描模拟数据。"""
        pkt_queue = self._get_queue()
        logger.info("注入端口扫描模拟数据（%d 秒，%d 包/秒）", duration, rate)
        start = time.time()
        port = 1
        while time.time() - start < duration and self._running:
            for _ in range(rate):
                try:
                    pkt_queue.put_nowait({
                        'length': random.randint(40, 60),
                        'sport': random.randint(1024, 65535),
                        'dport': port % 1024 + 1,
                        'src_ip': '10.0.0.100',
                        'dst_ip': TARGET_IP,
                        'proto': 'TCP',
                        'is_syn': True,
                        'is_dns': False,
                        'timestamp': time.time(),
                    })
                    port += 1
                except Exception:
                    pass
            time.sleep(1)

    def inject_udp_flood(self, duration: int = 10, rate: int = 100) -> None:
        """注入 UDP Flood 模拟数据。"""
        pkt_queue = self._get_queue()
        logger.info("注入 UDP Flood 模拟数据（%d 秒，%d 包/秒）", duration, rate)
        start = time.time()
        while time.time() - start < duration and self._running:
            for _ in range(rate):
                try:
                    pkt_queue.put_nowait({
                        'length': random.randint(64, 512),
                        'sport': random.randint(1024, 65535),
                        'dport': 53,
                        'src_ip': random.choice(ATTACK_IPS),
                        'dst_ip': TARGET_IP,
                        'proto': 'UDP',
                        'is_syn': False,
                        'is_dns': False,
                        'timestamp': time.time(),
                    })
                except Exception:
                    pass
            time.sleep(1)

    def inject_brute_force(self, duration: int = 10, rate: int = 15,
                           target_port: int = 22) -> None:
        """注入暴力破解模拟数据（针对 SSH/RDP 等服务端口）。

        同一源 IP 高频连接同一目标端口，触发暴力破解检测。
        阈值: BRUTE_FORCE_THRESHOLD(10) 次 / BRUTE_FORCE_WINDOW_SEC(60s)。
        """
        pkt_queue = self._get_queue()
        from campus_ids.config import BRUTE_FORCE_PORTS
        if target_port not in BRUTE_FORCE_PORTS:
            logger.warning("端口 %d 不在 BRUTE_FORCE_PORTS %s 中，可能无法触发检测",
                           target_port, BRUTE_FORCE_PORTS)
        src_ip = "10.0.0.200"  # 固定源 IP，确保同一 tracker key 累积
        logger.info("注入暴力破解模拟数据（%d 秒，%d 包/秒，端口 %d）", duration, rate, target_port)
        start = time.time()
        while time.time() - start < duration and self._running:
            for _ in range(rate):
                try:
                    pkt_queue.put_nowait({
                        'length': random.randint(60, 200),
                        'sport': random.randint(1024, 65535),
                        'dport': target_port,
                        'src_ip': src_ip,
                        'dst_ip': TARGET_IP,
                        'proto': 'TCP',
                        'is_syn': True,
                        'is_dns': False,
                        'timestamp': time.time(),
                    })
                except Exception:
                    pass
            time.sleep(1)

    def inject_lateral_movement(self, duration: int = 10, rate: int = 10) -> None:
        """注入横向移动模拟数据。

        同一源 IP 访问多个不同内网目标 IP，触发横向移动检测。
        阈值: LATERAL_MOVEMENT_THRESHOLD(5) 个不同内网 IP。
        """
        pkt_queue = self._get_queue()
        src_ip = "10.0.0.200"  # 固定源 IP
        # 生成多个不同的内网目标 IP
        lateral_targets = [f"192.168.1.{i}" for i in range(1, 20)]
        logger.info("注入横向移动模拟数据（%d 秒，%d 包/秒，%d 个目标 IP）",
                     duration, rate, len(lateral_targets))
        start = time.time()
        idx = 0
        while time.time() - start < duration and self._running:
            for _ in range(rate):
                try:
                    pkt_queue.put_nowait({
                        'length': random.randint(60, 200),
                        'sport': random.randint(1024, 65535),
                        'dport': 445,  # SMB 端口，典型横向移动目标
                        'src_ip': src_ip,
                        'dst_ip': lateral_targets[idx % len(lateral_targets)],
                        'proto': 'TCP',
                        'is_syn': True,
                        'is_dns': False,
                        'timestamp': time.time(),
                    })
                    idx += 1
                except Exception:
                    pass
            time.sleep(1)

    def start_all(self, duration: int = 30) -> None:
        """同时启动所有攻击模拟。

        Args:
            duration: 持续秒数

        注：本方法经 inject_* 直接向 packet_queue 注入合成流量，不涉及
        Npcap/sendp，因此无需网卡参数；iface 仅 CLI 真实发包路径（
        _send_packets / load_and_replay）需要。
        """
        self._running = True
        attacks = [
            threading.Thread(target=self.inject_syn_flood, args=(duration, 80), daemon=True),
            threading.Thread(target=self.inject_port_scan, args=(duration, 40), daemon=True),
            threading.Thread(target=self.inject_udp_flood, args=(duration, 150), daemon=True),
            threading.Thread(target=self.inject_brute_force, args=(duration, 15), daemon=True),
            threading.Thread(target=self.inject_lateral_movement, args=(duration, 10), daemon=True),
        ]
        for t in attacks:
            t.start()
        self._threads.extend(attacks)
        logger.info("所有攻击模拟已启动（持续 %d 秒）", duration)

    def stop(self) -> None:
        """停止所有攻击模拟。"""
        self._running = False
        for t in self._threads:
            t.join(timeout=2)
        self._threads.clear()
        logger.info("攻击模拟已停止")



# ── CLI 入口 ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="攻击模拟与回放工具")
    sub = parser.add_subparsers(dest="command")

    # SYN Flood
    p_syn = sub.add_parser("syn_flood", help="生成 SYN Flood 攻击")
    p_syn.add_argument("--count", type=int, default=500)
    p_syn.add_argument("--target", default=TARGET_IP)
    p_syn.add_argument("--port", type=int, default=80)
    p_syn.add_argument("--iface", default=None, help="发包网卡（None=scapy 默认）")
    p_syn.add_argument("--send", action="store_true", help="直接发送（需管理员权限）")

    # 端口扫描
    p_scan = sub.add_parser("port_scan", help="生成端口扫描")
    p_scan.add_argument("--target", default=TARGET_IP)
    p_scan.add_argument("--iface", default=None, help="发包网卡（None=scapy 默认）")
    p_scan.add_argument("--send", action="store_true")

    # UDP Flood
    p_udp = sub.add_parser("udp_flood", help="生成 UDP Flood 攻击")
    p_udp.add_argument("--count", type=int, default=1000)
    p_udp.add_argument("--target", default=TARGET_IP)
    p_udp.add_argument("--port", type=int, default=53)
    p_udp.add_argument("--iface", default=None, help="发包网卡（None=scapy 默认）")
    p_udp.add_argument("--send", action="store_true")

    # DDoS
    p_ddos = sub.add_parser("ddos", help="生成 DDoS 攻击（混合 TCP+UDP）")
    p_ddos.add_argument("--count", type=int, default=2000)
    p_ddos.add_argument("--target", default=TARGET_IP)
    p_ddos.add_argument("--iface", default=None, help="发包网卡（None=scapy 默认）")
    p_ddos.add_argument("--send", action="store_true")

    # 全部攻击
    p_all = sub.add_parser("all", help="生成所有类型攻击")
    p_all.add_argument("--target", default=TARGET_IP)
    p_all.add_argument("--iface", default=None, help="发包网卡（None=scapy 默认）")
    p_all.add_argument("--send", action="store_true")

    # 保存 pcap
    p_pcap = sub.add_parser("generate_pcap", help="生成攻击 pcap 文件")
    p_pcap.add_argument("--output", default="demo_attacks.pcap")
    p_pcap.add_argument("--target", default=TARGET_IP)

    # 回放 pcap
    p_replay = sub.add_parser("replay", help="回放 pcap 文件")
    p_replay.add_argument("pcap_file")
    p_replay.add_argument("--count", type=int, default=None)
    p_replay.add_argument("--interval", type=float, default=0.001)
    p_replay.add_argument("--iface", default=None, help="发包网卡（None=scapy 默认）")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "syn_flood":
        pkts = generate_syn_flood(args.target, args.port, args.count)
        if args.send:
            _send_packets(pkts, iface=args.iface)
        else:
            save_pcap(pkts, Path("syn_flood.pcap"))

    elif args.command == "port_scan":
        pkts = generate_port_scan(args.target)
        if args.send:
            _send_packets(pkts, iface=args.iface)
        else:
            save_pcap(pkts, Path("port_scan.pcap"))

    elif args.command == "udp_flood":
        pkts = generate_udp_flood(args.target, args.port, args.count)
        if args.send:
            _send_packets(pkts, iface=args.iface)
        else:
            save_pcap(pkts, Path("udp_flood.pcap"))

    elif args.command == "ddos":
        pkts = generate_ddos(args.target, args.count)
        if args.send:
            _send_packets(pkts, iface=args.iface)
        else:
            save_pcap(pkts, Path("ddos.pcap"))

    elif args.command == "all":
        pkts = generate_all_attacks(args.target)
        if args.send:
            _send_packets(pkts, iface=args.iface)
        else:
            save_pcap(pkts, Path("all_attacks.pcap"))

    elif args.command == "generate_pcap":
        pkts = generate_all_attacks(args.target)
        save_pcap(pkts, Path(args.output))

    elif args.command == "replay":
        load_and_replay(Path(args.pcap_file), args.count, args.interval, iface=args.iface)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    main()