"""TLS 加密流量分析模块 — JA3 指纹提取与异常检测。

P0-4: 用 Scapy 解析 TLS ClientHello，提取 JA3 指纹
P0-5: 提取 SNI、TLS 版本、加密套件列表
P0-6: 基于 JA3 指纹做异常检测
"""
from __future__ import annotations

import hashlib
import logging
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Optional

from campus_ids.config import TLS_RECORD_MAX

logger = logging.getLogger(__name__)

# ── 已知良性 JA3 指纹库（常见浏览器 / 工具） ──────────────────────────
# 来源：ja3er.com 等公开库中高频出现的指纹
KNOWN_BENIGN_JA3: set[str] = {
    # Chrome (各版本)
    "771,4865-4866-4867-49195-49199-49196-49200-49162-49161-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13-18-51-45-43-27-17513,29-23-24,0",
    "771,4865-4866-4867-49195-49199-49196-49200-49162-49161-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13-18-51-45-43-27-21,29-23-24,0",
    "771,4866-4867-49195-49199-52393-52392-49196-49200-49162-49161-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13-18-51-45-43-27-21,29-23-24,0",
    # Firefox
    "771,4865-4866-4867-49195-49199-49196-49200-49162-49161-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13-18-51-45-43-27,29-23-24,0",
    "771,4866-4867-49195-49199-52393-52392-49196-49200-49162-49161-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13-18-51-45-43-27,29-23-24,0",
    # curl / wget
    "771,49195-49196-49199-49200-49162-49161-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13,29-23-24,0",
    "771,49195-49200-49196-49199-52393-49162-49161-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13,29-23-24,0",
    # Python requests / urllib3
    "771,49199-49195-49196-49200-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13,29-23-24,0",
    # Java
    "771,49200-49196-49192-49188-49172-49162-49202-49198-49194-49190-49168-49158-49206-49204-157-156-61-60-53-47-255,0-23-65281-10-11-35-16-5-13,29-23-24,0",
}

# 已知恶意 / 可疑 JA3 指纹（Metasploit, Cobalt Strike 等工具）
KNOWN_MALICIOUS_JA3: set[str] = {
    # Metasploit default
    "771,49195-49199-49196-49200-49162-49161-5-4-49170-49171-50-156-157-47-53,0-23-65281-10-11-35-16-5-13,29-23-24,0",
    # Cobalt Strike
    "771,4865-4866-4867-49195-49199-49196-49200-49162-49161-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13-18-51-45-43-27-17513,29-23-24,0",
}

# TLS 版本映射
TLS_VERSION_MAP: dict[int, str] = {
    0x0300: "SSL 3.0",
    0x0301: "TLS 1.0",
    0x0302: "TLS 1.1",
    0x0303: "TLS 1.2",
    0x0304: "TLS 1.3",
}


@dataclass
class TLSInfo:
    """从单个 TLS ClientHello 中提取的信息。"""
    src_ip: str = ""
    dst_ip: str = ""
    src_port: int = 0
    dst_port: int = 0
    tls_version: str = ""
    ja3_raw: str = ""          # JA3 原始字符串
    ja3_hash: str = ""         # JA3 MD5 哈希
    sni: str = ""              # Server Name Indication
    cipher_suites: list[int] = field(default_factory=list)
    cipher_count: int = 0
    extensions: list[int] = field(default_factory=list)
    elliptic_curves: list[int] = field(default_factory=list)
    ec_point_formats: list[int] = field(default_factory=list)
    is_suspicious: bool = False
    suspicion_reason: str = ""


class TLSAnalyzer:
    """TLS 加密流量分析器：提取 JA3 指纹并进行异常检测。"""

    def __init__(self, known_benign: set[str] | None = None,
                 known_malicious: set[str] | None = None):
        self.known_benign = known_benign or KNOWN_BENIGN_JA3
        self.known_malicious = known_malicious or KNOWN_MALICIOUS_JA3
        # 统计 JA3 指纹出现频次
        self._ja3_counter: Counter[str] = Counter()
        # 所有观测到的 TLS 信息（有界，防止内存泄漏）
        self._tls_records: deque[TLSInfo] = deque(maxlen=TLS_RECORD_MAX)
        # TLS 版本分布
        self._tls_version_counter: Counter[str] = Counter()

    # ── JA3 指纹计算 ────────────────────────────────────────────────

    @staticmethod
    def compute_ja3(tls_version: int, cipher_suites: list[int],
                    extensions: list[int], elliptic_curves: list[int],
                    ec_point_formats: list[int]) -> tuple[str, str]:
        """计算 JA3 指纹。

        返回 (ja3_raw, ja3_hash)。
        JA3 格式: TLSVersion,Ciphers,Extensions,EllipticCurves,EllipticCurvePointFormats
        """
        ja3_raw = ",".join([
            str(tls_version),
            "-".join(str(c) for c in cipher_suites),
            "-".join(str(e) for e in extensions),
            "-".join(str(c) for c in elliptic_curves),
            "-".join(str(f) for f in ec_point_formats),
        ])
        ja3_hash = hashlib.md5(ja3_raw.encode()).hexdigest()
        return ja3_raw, ja3_hash

    # ── 从 Scapy 包中提取 TLS 信息 ──────────────────────────────────

    def parse_tls_from_packet(self, pkt) -> Optional[TLSInfo]:
        """从 Scapy 抓包中解析 TLS ClientHello，返回 TLSInfo 或 None。"""
        try:
            from scapy.all import IP, TCP, Raw

            if not pkt.haslayer(IP) or not pkt.haslayer(TCP):
                return None

            ip_layer = pkt[IP]
            tcp_layer = pkt[TCP]

            # TLS ClientHello 起始于 TCP payload
            raw = bytes(tcp_layer.payload) if tcp_layer.payload else b""
            if len(raw) < 44:
                return None

            # TLS Record: ContentType=0x16 (Handshake), Version, Length
            if raw[0] != 0x16:
                return None

            # HandshakeType=0x01 (ClientHello)
            if len(raw) < 6 or raw[5] != 0x01:
                return None

            tls_version = int.from_bytes(raw[1:3], "big")
            handshake_version = int.from_bytes(raw[5 + 4:5 + 6], "big") if len(raw) > 10 else tls_version

            # ClientHello 结构解析
            # SessionID length at offset 5+6+32 = 43
            offset = 5 + 6 + 32
            if offset >= len(raw):
                return None

            session_id_len = raw[offset]
            offset += 1 + session_id_len

            # Cipher Suites
            if offset + 2 > len(raw):
                return None
            cipher_len = int.from_bytes(raw[offset:offset + 2], "big")
            offset += 2
            cipher_suites = []
            if offset + cipher_len <= len(raw):
                for i in range(0, cipher_len, 2):
                    if offset + i + 2 <= len(raw):
                        cipher_suites.append(int.from_bytes(raw[offset + i:offset + i + 2], "big"))
            offset += cipher_len

            # Compression Methods
            if offset + 1 > len(raw):
                return None
            comp_len = raw[offset]
            offset += 1 + comp_len

            # Extensions
            extensions = []
            elliptic_curves = []
            ec_point_formats = []
            sni = ""

            if offset + 2 <= len(raw):
                ext_total_len = int.from_bytes(raw[offset:offset + 2], "big")
                offset += 2
                ext_end = offset + ext_total_len

                while offset + 4 <= min(ext_end, len(raw)):
                    ext_type = int.from_bytes(raw[offset:offset + 2], "big")
                    ext_len = int.from_bytes(raw[offset + 2:offset + 4], "big")
                    ext_data_start = offset + 4

                    extensions.append(ext_type)

                    # SNI (extension type 0x0000)
                    if ext_type == 0x0000 and ext_data_start + 5 <= len(raw):
                        sni_type = raw[ext_data_start + 2]
                        if sni_type == 0 and ext_data_start + 5 <= len(raw):
                            sni_len = int.from_bytes(raw[ext_data_start + 3:ext_data_start + 5], "big")
                            if ext_data_start + 5 + sni_len <= len(raw):
                                sni = raw[ext_data_start + 5:ext_data_start + 5 + sni_len].decode("utf-8", errors="ignore")

                    # Supported Groups / Elliptic Curves (extension type 0x000a)
                    elif ext_type == 0x000a and ext_data_start + 2 <= len(raw):
                        curves_len = int.from_bytes(raw[ext_data_start:ext_data_start + 2], "big")
                        for i in range(0, curves_len, 2):
                            if ext_data_start + 2 + i + 2 <= len(raw):
                                elliptic_curves.append(
                                    int.from_bytes(raw[ext_data_start + 2 + i:ext_data_start + 2 + i + 2], "big"))

                    # EC Point Formats (extension type 0x000b)
                    elif ext_type == 0x000b and ext_data_start + 1 <= len(raw):
                        fmt_len = raw[ext_data_start]
                        for i in range(fmt_len):
                            if ext_data_start + 1 + i < len(raw):
                                ec_point_formats.append(raw[ext_data_start + 1 + i])

                    offset = ext_data_start + ext_len

            # 计算 JA3 指纹
            ja3_raw, ja3_hash = self.compute_ja3(
                handshake_version, cipher_suites, extensions, elliptic_curves, ec_point_formats
            )

            tls_info = TLSInfo(
                src_ip=ip_layer.src,
                dst_ip=ip_layer.dst,
                src_port=int(tcp_layer.sport),
                dst_port=int(tcp_layer.dport),
                tls_version=TLS_VERSION_MAP.get(handshake_version, f"0x{handshake_version:04x}"),
                ja3_raw=ja3_raw,
                ja3_hash=ja3_hash,
                sni=sni,
                cipher_suites=cipher_suites,
                cipher_count=len(cipher_suites),
                extensions=extensions,
                elliptic_curves=elliptic_curves,
                ec_point_formats=ec_point_formats,
            )

            # 异常检测
            self._classify_tls(tls_info)

            # 更新统计
            self._ja3_counter[ja3_hash] += 1
            self._tls_version_counter[tls_info.tls_version] += 1
            self._tls_records.append(tls_info)

            return tls_info

        except Exception as exc:
            logger.debug("TLS 解析失败: %s", exc)
            return None

    def _classify_tls(self, info: TLSInfo) -> None:
        """基于 JA3 指纹进行异常分类。"""
        # 已知恶意指纹
        if info.ja3_hash in self.known_malicious:
            info.is_suspicious = True
            info.suspicion_reason = "已知恶意 JA3 指纹"
            return

        # 未知指纹（不在良性库中）
        if info.ja3_hash not in self.known_benign:
            info.is_suspicious = True
            info.suspicion_reason = "未知 JA3 指纹（不在已知良性库中）"
            return

        # 过旧 TLS 版本
        if info.tls_version in ("SSL 3.0", "TLS 1.0", "TLS 1.1"):
            info.is_suspicious = True
            info.suspicion_reason = f"使用过旧 TLS 版本: {info.tls_version}"
            return

        # 异常少量加密套件（正常客户端通常 5+ 个）
        if 0 < info.cipher_count < 5:
            info.is_suspicious = True
            info.suspicion_reason = f"加密套件数量异常少: {info.cipher_count}"

    # ── 统计查询接口 ────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """返回加密流量统计摘要，供 Web 面板展示。"""
        total = len(self._tls_records)
        suspicious = sum(1 for r in self._tls_records if r.is_suspicious)
        unique_ja3 = len(self._ja3_counter)

        # 异常 SNI 列表（关联可疑 JA3 的 SNI）
        suspicious_snis: list[str] = []
        for rec in self._tls_records:
            if rec.is_suspicious and rec.sni:
                suspicious_snis.append(rec.sni)

        # JA3 指纹 Top 10
        ja3_top = self._ja3_counter.most_common(10)

        # TLS 版本分布
        tls_version_dist = dict(self._tls_version_counter)

        return {
            "total_tls_flows": total,
            "suspicious_tls_flows": suspicious,
            "unique_ja3_fingerprints": unique_ja3,
            "tls_version_distribution": tls_version_dist,
            "suspicious_sni_list": list(set(suspicious_snis))[:20],
            "ja3_top_fingerprints": [
                {"hash": h, "count": c, "is_benign": h in self.known_benign}
                for h, c in ja3_top
            ],
        }

    def get_suspicious_records(self, limit: int = 50) -> list[dict]:
        """返回可疑 TLS 记录列表。"""
        results = []
        for rec in self._tls_records:
            if rec.is_suspicious:
                results.append({
                    "src_ip": rec.src_ip,
                    "dst_ip": rec.dst_ip,
                    "dst_port": rec.dst_port,
                    "tls_version": rec.tls_version,
                    "ja3_hash": rec.ja3_hash,
                    "sni": rec.sni,
                    "cipher_count": rec.cipher_count,
                    "reason": rec.suspicion_reason,
                })
                if len(results) >= limit:
                    break
        return results

    def get_all_records(self) -> list[TLSInfo]:
        """返回所有 TLS 记录的列表副本（供外部模块安全访问）。"""
        return list(self._tls_records)

    def reset(self) -> None:
        """重置所有统计数据。"""
        self._ja3_counter.clear()
        self._tls_records.clear()
        self._tls_version_counter.clear()


# 全局 TLS 分析器实例
tls_analyzer = TLSAnalyzer()