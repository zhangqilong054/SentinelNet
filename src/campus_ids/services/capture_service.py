"""services/capture_service.py — 基础 + 增强抓包业务用例。

合并原 helpers.py 的 start_capture_thread / stop_capture_thread /
start_enhanced_capture / stop_enhanced_capture 为统一接口。

依赖注入：
- RuntimeState: packet_queue / capture_running / capture_thread / enhanced_capture_*
- DualDetector: add_packet
- tls_analyzer: parse_tls_from_packet
"""
from __future__ import annotations

import logging
import queue
import threading
import time as _time
from typing import Any

from campus_ids.config import DNS_PORT, TLS_PORTS
from campus_ids.capture.enhanced_features import _parse_base_fields
from campus_ids.runtime.settings import get_settings

logger = logging.getLogger(__name__)


def autodetect_iface() -> tuple[str | None, str]:
    """自动挑选活动网卡（用户未显式配置时的兜底）。

    2026-09-20：不再回退 scapy `conf.iface`（Windows 上常指向非活动适配器
    → 抓包永远 0 包）。按两级策略推导：

    1. 默认路由网卡：`conf.route.route("0.0.0.0")[0]`，即真正承载上网流量的
       适配器（本机实测 3s 169 包）；
    2. 活动网卡打分：排除环回/虚拟/隧道适配器（VMware/Hyper-V/蓝牙/Wi-Fi
       Direct 等），按「有全局 IPv4 > 有全局 IPv6」挑第一个。

    模块级函数：`/api/check` 需要在不构造 CaptureService 的情况下复用同一逻辑，
    避免诊断口径与实际选卡逻辑各写一套而漂移。

    Returns:
        (scapy 接口名 或 None, 来源标记 auto:route | auto:active | fallback)
    """
    try:
        from scapy.all import conf, get_if_list

        scapy_names = set(get_if_list())
    except Exception as exc:  # noqa: BLE001 —— scapy 不可用
        logger.debug("scapy 接口枚举失败: %s", exc)
        return None, "fallback"

    try:
        route_iface = conf.route.route("0.0.0.0")[0]
        if route_iface in scapy_names:
            return route_iface, "auto:route"
    except Exception as exc:  # noqa: BLE001 —— 无路由表时降级到打分
        logger.debug("默认路由网卡解析失败: %s", exc)

    try:
        from scapy.arch.windows import get_windows_if_list

        skip_keywords = (
            "loopback", "vmware", "hyper-v", "virtual", "wifi direct",
            "bluetooth", "wan miniport", "tap-", "tunnel", "vpn",
            "npcap packet driver", "qos packet scheduler",
        )
        best: tuple[int, str] | None = None
        for itf in get_windows_if_list():
            name = itf.get("name") or ""
            if name not in scapy_names:
                continue
            desc = f"{name} {itf.get('description') or ''}".lower()
            if any(k in desc for k in skip_keywords):
                continue
            ips = itf.get("ips") or []
            has_v4 = any(
                ip.count(".") == 3 and not ip.startswith(("127.", "169.254."))
                for ip in ips
            )
            has_v6 = any(":" in ip and not ip.lower().startswith("fe80") for ip in ips)
            score = 2 if has_v4 else (1 if has_v6 else 0)
            if score == 0:
                continue
            if best is None or score > best[0]:
                best = (score, name)
        if best is not None:
            return best[1], "auto:active"
    except Exception as exc:  # noqa: BLE001 —— 非 Windows 或枚举失败
        logger.debug("活动网卡打分失败: %s", exc)

    return None, "fallback"


def build_capture_filter() -> str:
    """构造 BPF 过滤表达式（内核态过滤，减轻用户态回调压力）。

    默认 `tcp or udp`：检测器只消费 TCP/UDP，ARP/STP/IPv6-ND 等是纯噪声。
    开启 `capture_exclude_web_port` 时追加 `and not port <web_port>`，
    避免面板自身轮询/SSE 心跳被当成真实流量污染统计与检测。

    模块级函数：`/api/check` 与会话服务共用同一算法，避免口径漂移。
    """
    settings = get_settings()
    expr = (settings.capture_filter or "").strip()
    if expr and settings.capture_exclude_web_port:
        port = int(settings.web_port)
        expr = f"({expr}) and not port {port}"
    return expr


def validate_bpf(bpf: str) -> str | None:
    """预校验 BPF 过滤表达式合法性。

    F1 修复：在启动抓包线程前做一次 BPF 编译校验，
    非法表达式直接返回错误信息，不启动线程（避免永久锁死）。

    Returns:
        None 表示合法，str 为错误描述。
    """
    if not bpf:
        return None
    try:
        from scapy.arch.common import compile_filter
        compile_filter(bpf, linktype=1)
    except ImportError:
        # scapy.arch.common 不可用时，尝试 scapy.all（旧版兼容）
        try:
            from scapy.all import compile_filter as _cf
            _cf(bpf)
        except ImportError:
            # compile_filter 完全不可用 → 跳过校验，交由 sniff 阶段报错
            return None
    except Exception as exc:
        return f"BPF 过滤表达式非法: {exc}"
    return None


class CaptureService:
    """抓包服务 — 管理基础抓包和增强抓包的生命周期。

    通过 RuntimeState 管理运行状态，通过注入的依赖访问共享资源。
    """

    def __init__(
        self,
        state: Any,  # RuntimeState
        dual_detector: Any,  # DualDetector
        tls_analyzer: Any,  # tls_analyzer 单例
        alert_service: Any | None = None,  # AlertService
        traffic_service: Any | None = None,  # TrafficService
    ) -> None:
        self._state = state
        self._dual_detector = dual_detector
        self._tls_analyzer = tls_analyzer
        self._alert_service = alert_service
        self._traffic_service = traffic_service
        self._capture_iface: str | None = None  # start_capture() 时解析，_capture_worker 消费
        self._capture_iface_source: str = "unset"  # config | auto:route | auto:active | fallback
        self._capture_filter: str = ""          # start_capture() 时构造，_capture_worker 消费
        self._enhanced_iface: str | None = None
        self._enhanced_filter: str = ""

    # ── 网卡解析 ──────────────────────────────────────────────────

    def _autodetect_iface(self) -> tuple[str | None, str]:
        """自动挑选活动网卡 —— 委托模块级 `autodetect_iface()`（供 /api/check 复用）。"""
        return autodetect_iface()

    def _resolve_iface(self, requested: str | None) -> str | None:
        """把用户可读的网卡名解析为 scapy 可用的接口名。

        2026-09-19 真机发现：`sniff()` 不传 iface 时用 scapy `conf.iface`，
        Windows 上常指向非活动适配器 → 抓包永远 0 包（原生 pcap 同设备可抓到）。
        接受：scapy 完整接口名（\\Device\\NPF_*）或系统友好名（如 WLAN / 以太网）。
        显式配置解析失败仍回退 None（scapy 默认）并告警，不阻断启动。
        未显式配置时改为自动识别（默认路由 → 活动网卡打分），不再用 conf.iface。

        Returns:
            scapy 接口名，或 None（调用方应视为「无法确认网卡」）。
        """
        name = (
            requested
            or getattr(self, "_requested_iface", "")
            or get_settings().capture_iface
        )
        if not name:
            iface, source = self._autodetect_iface()
            self._capture_iface_source = source
            if iface:
                logger.info("抓包网卡未配置，自动识别为 %s（%s）", iface, source)
            else:
                logger.warning("抓包网卡未配置且自动识别失败，回退 scapy 默认网卡（可能 0 包）")
            return iface
        self._capture_iface_source = "config"

        from scapy.all import get_if_list

        scapy_names = set(get_if_list())
        if name in scapy_names:
            return name
        try:
            from scapy.arch.windows import get_windows_if_list

            for itf in get_windows_if_list():
                if itf.get("name") == name or itf.get("description") == name:
                    if itf.get("name") in scapy_names:
                        return itf["name"]
        except Exception as exc:  # noqa: BLE001 —— 非平台或枚举失败仅降级
            logger.debug("网卡枚举失败: %s", exc)
        logger.warning("抓包网卡 %r 无法解析为 scapy 接口，回退 scapy 默认网卡", name)
        return None

    # ── BPF 过滤 ──────────────────────────────────────────────────

    def _build_filter(self) -> str:
        """构造 BPF 过滤表达式 —— 委托模块级 `build_capture_filter()`。"""
        return build_capture_filter()

    # ── 基础抓包 ──────────────────────────────────────────────────

    def start_capture(self, interface: str | None = None) -> dict:
        """启动基础抓包（幂等）。

        Args:
            interface: 网卡名（scapy 接口名或系统友好名如 WLAN）。
                None 时使用 Settings.capture_iface，仍为空则自动识别活动网卡。

        对应旧实现: helpers.start_capture_thread()

        F1 修复：启动前预校验 BPF，非法直接返回错误，不启动线程。
        """
        with self._state._state_lock:
            if self._state.capture_running:
                return {"status": "already_running"}

        # F1 修复：先解析网卡和过滤表达式，校验 BPF 合法性后再置标志位
        self._capture_iface = self._resolve_iface(interface)
        self._capture_filter = self._build_filter()

        bpf_error = validate_bpf(self._capture_filter)
        if bpf_error:
            logger.error("基础抓包启动被拒绝: %s", bpf_error)
            return {"status": "error", "message": bpf_error}

        with self._state._state_lock:
            if self._state.capture_running:
                # 并发启动竞争，另一个已成功
                return {"status": "already_running"}
            self._state.capture_running = True
        if self._capture_iface:
            logger.info(
                "基础抓包已启动（网卡: %s, 来源: %s, 过滤: %s）",
                self._capture_iface, self._capture_iface_source,
                self._capture_filter or "<无>",
            )
        else:
            logger.info("基础抓包已启动（scapy 默认网卡，未确认网卡可能 0 包）")
        thread = threading.Thread(target=self._capture_worker, daemon=True)
        self._state.capture_thread = thread
        thread.start()
        return {"status": "started"}

    def stop_capture(self) -> dict:
        """停止基础抓包。

        对应旧实现: helpers.stop_capture_thread()
        """
        with self._state._state_lock:
            self._state.capture_running = False
        logger.info("基础抓包已停止")
        return {"status": "stopped"}

    def capture_status(self) -> dict:
        """查询基础抓包状态 — 含生效网卡与过滤表达式，便于排障自证。"""
        return {
            "running": self._state.capture_running,
            "dropped_packets": self._state.dropped_packets,
            "iface": self._capture_iface,
            "iface_source": self._capture_iface_source,
            "filter": self._capture_filter,
            "queue_size": self._state.packet_queue.qsize(),
        }

    # ── 增强抓包 ──────────────────────────────────────────────────

    def start_enhanced(self, duration: int = 60) -> dict:
        """启动增强抓包（限时）。

        对应旧实现: helpers.start_enhanced_capture_thread()

        F1 修复：启动前预校验 BPF，非法直接返回错误，不启动线程。
        """
        with self._state._state_lock:
            if self._state.enhanced_capture_running:
                return {"status": "already_running"}

        # F1 修复：先解析网卡和过滤表达式，校验 BPF 合法性后再置标志位
        enhanced_iface = self._resolve_iface(None)
        enhanced_filter = self._build_filter()

        bpf_error = validate_bpf(enhanced_filter)
        if bpf_error:
            logger.error("增强抓包启动被拒绝: %s", bpf_error)
            return {"status": "error", "message": bpf_error}

        with self._state._state_lock:
            if self._state.enhanced_capture_running:
                return {"status": "already_running"}
            self._state.enhanced_capture_running = True
        if enhanced_iface:
            logger.info(
                "增强抓包已启动 (duration=%ds, 网卡: %s, 过滤: %s)",
                duration, enhanced_iface, enhanced_filter or "<无>",
            )
        else:
            logger.info("增强抓包已启动 (duration=%ds, scapy 默认网卡)", duration)

        self._enhanced_iface = enhanced_iface
        self._enhanced_filter = enhanced_filter
        thread = threading.Thread(
            target=self._enhanced_capture_worker,
            args=(duration, enhanced_iface, enhanced_filter),
            daemon=True,
        )
        self._state.enhanced_capture_thread = thread
        thread.start()
        return {"status": "started"}

    def stop_enhanced(self) -> dict:
        """停止增强抓包。

        对应旧实现: helpers.stop_enhanced_capture_thread()
        """
        with self._state._state_lock:
            self._state.enhanced_capture_running = False
        logger.info("增强抓包已停止")
        return {"status": "stopped"}

    def enhanced_status(self) -> dict:
        """查询增强抓包状态。

        对应旧实现: helpers.get_enhanced_capture_status()
        """
        running = self._state.enhanced_capture_running
        result = dict(self._state.enhanced_capture_result) if self._state.enhanced_capture_result else {}
        result["running"] = running
        result["iface"] = self._enhanced_iface
        result["filter"] = self._enhanced_filter
        return result

    # ── 内部工作函数 ──────────────────────────────────────────────

    def _capture_worker(self) -> None:
        """后台抓包线程：持续抓包并把关键信息放入队列。

        对应旧实现: helpers._capture_worker()

        F1 修复：finally 块确保 capture_running 复位，避免异常/非法 BPF
        导致线程死亡后标志位仍为 True → 永久锁死。
        """
        from scapy.all import TCP, UDP, sniff  # type: ignore[attr-defined]  # scapy 动态导出，无静态属性

        def _on_pkt(pkt):
            if not self._state.capture_running:
                return
            base = _parse_base_fields(pkt)
            if base is not None:
                _, l4, proto, src_ip, dst_ip, _, dst_port, pkt_len, timestamp = base
                is_syn = bool(pkt.haslayer(TCP) and pkt[TCP].flags & 0x02)
                is_dns = bool(pkt.haslayer(UDP) and dst_port == DNS_PORT)
                try:
                    self._state.packet_queue.put_nowait({
                        "length": pkt_len,
                        "sport": int(l4.sport),
                        "dport": dst_port,
                        "src_ip": src_ip,
                        "dst_ip": dst_ip,
                        "proto": proto,
                        "is_syn": is_syn,
                        "is_dns": is_dns,
                        "timestamp": timestamp,
                    })
                except queue.Full:
                    # R3.3: 单写者（capture 线程）+=1，CPython GIL 保证原子性；
                    # 若未来多线程写入需改用 Lock 或 atomic 原语。
                    self._state.dropped_packets += 1

                # TLS 解析
                if pkt.haslayer(TCP) and dst_port in TLS_PORTS:
                    try:
                        self._tls_analyzer.parse_tls_from_packet(pkt)
                    except Exception as exc:
                        logger.warning("TLS 解析失败: %s", exc)

        try:
            # iface 必须显式传递：不传时 scapy 用 conf.iface，Windows 上常是
            # 非活动适配器（2026-09-19 实测 8s 0 包，原生 pcap 同机 548 包）。
            # filter 走 BPF 内核态过滤，减少用户态回调压力（2026-09-20）。
            sniff(prn=_on_pkt, store=False, iface=self._capture_iface,
                  filter=self._capture_filter or None,
                  stop_filter=lambda _: not self._state.capture_running)
        except BaseException as exc:
            # F1 修复：捕获 BaseException（含 scapy.error.Scapy_Exception 等非 RuntimeError）
            # 而非仅 Exception，确保 KeyboardInterrupt 等也能复位。
            logger.error("后台抓包线程异常退出: %s", exc)
        finally:
            # F1 修复：无论正常退出还是异常，都必须复位 capture_running，
            # 否则后续 start_capture() 永远返回 already_running。
            self._state.capture_running = False

    def _enhanced_capture_worker(
        self, duration: int, iface: str | None = None, bpf: str = ""
    ) -> None:
        """后台增强抓包线程：提取 18 维流特征 + TLS 分析，保存 CSV。

        对应旧实现: helpers._enhanced_capture_worker()

        F1 修复：try/except BaseException 包住 run_enhanced_capture，
        确保 Scapy_Exception 等非 RuntimeError 异常也能被捕获并复位标志位。
        """
        from campus_ids.capture.enhanced_features import run_enhanced_capture

        logger.info("增强抓包线程启动，持续 %d 秒", duration)
        self._state.enhanced_capture_result = {
            "status": "running",
            "duration": duration,
            "packets": 0,
            "flows": 0,
            "error": None,
        }

        stop_time = _time.time() + duration

        def stop_filter(_pkt) -> bool:
            return not self._state.enhanced_capture_running or _time.time() >= stop_time

        try:
            result = run_enhanced_capture(
                duration, stop_filter=stop_filter, iface=iface, bpf=bpf
            )

            if result is None:
                self._state.enhanced_capture_result = {
                    "status": "error",
                    "duration": duration,
                    "packets": 0,
                    "flows": 0,
                    "error": "抓包失败",
                }
            else:
                packets, flows = result
                self._state.enhanced_capture_result = {
                    "status": "completed",
                    "duration": duration,
                    "packets": packets,
                    "flows": flows,
                    "error": None,
                }
        except BaseException as exc:
            # F1 修复：捕获 BaseException（含 scapy.error.Scapy_Exception 等），
            # 写入错误信息到 result，避免状态停留在 "running"。
            logger.error("增强抓包线程异常退出: %s", exc)
            self._state.enhanced_capture_result = {
                "status": "error",
                "duration": duration,
                "packets": 0,
                "flows": 0,
                "error": str(exc),
            }
        finally:
            # F1 修复：无论正常退出还是异常，都必须复位 enhanced_capture_running。
            self._state.enhanced_capture_running = False