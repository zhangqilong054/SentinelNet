"""P1-#8: 双引擎检测器 — ML 模型 + 规则检测融合。

将 ML 模型预测与规则检测结合，提供双重判定：
- 规则检测：实时、低延迟，基于阈值（QPS/端口数/SYN/UDP）
- ML 检测：周期性聚合流特征后预测，基于训练模型

融合策略：
- 任一引擎触发 → 标记为异常
- 双引擎同时触发 → 高危
- 仅 ML 触发 → 中危（可能是未知攻击模式）
- 仅规则触发 → 低危（可能是已知攻击模式）
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from campus_ids.config import (
    BRUTE_FORCE_PORTS, ML_INTERVAL_SEC, ML_FLOW_BUFFER_SIZE,
    ML_HISTORY_SIZE, MODEL_PATH as DEFAULT_MODEL_PATH,
    ML_CONF_HIGH,
)

logger = logging.getLogger(__name__)

# 检测结果等级
LEVEL_SAFE = "safe"
LEVEL_LOW = "low"       # 仅规则触发
LEVEL_MEDIUM = "medium"  # 仅ML触发
LEVEL_HIGH = "high"      # 双引擎触发


@dataclass
class DualDetectionResult:
    """双引擎检测结果。"""
    is_anomaly: bool = False
    level: str = LEVEL_SAFE
    rule_alerts: list[str] = field(default_factory=list)
    ml_prediction: str = "Normal"
    ml_confidence: float = 0.0
    attack_type: str = "Normal"
    description: str = ""
    detection_latency_ms: float = 0.0  # P2-11: 检测延迟（毫秒）


class DualDetector:
    """双引擎检测器：规则 + ML 融合。"""

    def __init__(self, rule_detector, model_path: Path | None = None,
                 ml_interval: float = ML_INTERVAL_SEC):
        """
        Args:
            rule_detector: AnomalyDetector 实例（规则检测）
            model_path: ML 模型路径（model.pkl）
            ml_interval: ML 预测间隔（秒）
        """
        self.rule_detector = rule_detector
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self.ml_interval = ml_interval

        # ML 模型 artifact
        self._artifact: dict | None = None
        self._model_loaded = False

        # 最近检测结果缓存
        self._last_ml_result: DualDetectionResult = DualDetectionResult()
        self._ml_history: deque[DualDetectionResult] = deque(maxlen=ML_HISTORY_SIZE)

        # 后台 ML 预测线程
        self._ml_thread: threading.Thread | None = None
        self._ml_running = False

        # 流量特征缓冲（从 packet_queue 收集）
        self._flow_buffer: deque[dict] = deque(maxlen=ML_FLOW_BUFFER_SIZE)
        self._flow_lock = threading.Lock()

        # 统计
        self._ml_predict_count = 0
        self._ml_attack_count = 0
        self._rule_alert_count = 0

        # P2-11: 检测延迟统计
        self._latency_samples: deque[float] = deque(maxlen=1000)

    def load_model(self, model_path: Path | None = None, run_id: str | None = None,
                    which: str = "best") -> bool:
        """加载 ML 模型。

        Args:
            model_path: 传统 pkl 路径（向后兼容）
            run_id: 指定 run_id 加载（优先级最高）
            which: 注册表指针 "best" 或 "latest"
        """
        try:
            from campus_ids.model.train import load_model
            # 优先级: run_id > 指针(which) > 传统路径
            if run_id is not None:
                from campus_ids.model.train import load_run
                self._artifact = load_run(run_id=run_id)
                source = f"run:{run_id}"
            elif model_path is not None:
                self.model_path = model_path
                self._artifact = load_model(path=model_path)
                source = str(model_path)
            else:
                self._artifact = load_model(which=which)
                source = f"registry:{which}"
            if self._artifact is not None:
                self._model_loaded = True
                rid = self._artifact.get("run_id", "")
                logger.info("ML 模型加载成功: %s%s", source, f" (run_id={rid})" if rid else "")
                return True
            else:
                logger.warning("ML 模型文件不存在或加载失败: %s", source)
                return False
        except Exception as exc:
            logger.warning("ML 模型加载异常: %s", exc)
            return False

    @property
    def model_loaded(self) -> bool:
        return self._model_loaded

    def add_packet(self, pkt_info: dict) -> None:
        """添加包信息到流缓冲区。"""
        with self._flow_lock:
            self._flow_buffer.append(pkt_info)

    def _drain_flow_buffer(self) -> list[dict]:
        """取出流缓冲区中的所有包。"""
        with self._flow_lock:
            pkts = list(self._flow_buffer)
            self._flow_buffer.clear()
        return pkts

    def rule_detect(self, qps: int, port_count: int,
                    syn_count: int, udp_count: int,
                    packets: list[dict] | None = None) -> list[str]:
        """执行规则检测，返回告警列表。

        P1-#9: 扩展支持暴力破解、横向移动、载荷检测。
        """
        alerts = []
        is_ddos, ddos_msg = self.rule_detector.check_ddos(qps)
        is_scan, scan_msg = self.rule_detector.check_port_scan(port_count)
        is_syn, syn_msg = self.rule_detector.check_syn_flood(syn_count)
        is_udp, udp_msg = self.rule_detector.check_udp_flood(udp_count)

        if is_ddos:
            alerts.append(ddos_msg)
        if is_scan:
            alerts.append(scan_msg)
        if is_syn:
            alerts.append(syn_msg)
        if is_udp:
            alerts.append(udp_msg)

        # P1-#9: 暴力破解 & 横向移动检测（基于包信息）
        if packets:
            bf_ips = set()
            lateral_ips = set()
            for p in packets:
                src_ip = p.get('src_ip', '')
                dport = p.get('dport', 0)
                dst_ip = p.get('dst_ip', '')

                # 暴力破解检测（常见服务端口）
                if dport in BRUTE_FORCE_PORTS:
                    is_bf, bf_msg = self.rule_detector.check_brute_force(src_ip, dport)
                    if is_bf and src_ip not in bf_ips:
                        alerts.append(bf_msg)
                        bf_ips.add(src_ip)

                # 横向移动检测（内网IP访问内网IP）
                if dst_ip and not dst_ip.startswith(('0.', '127.')) and src_ip:
                    is_lat, lat_msg = self.rule_detector.check_lateral_movement(src_ip, dst_ip)
                    if is_lat and src_ip not in lateral_ips:
                        alerts.append(lat_msg)
                        lateral_ips.add(src_ip)

                # 载荷检测（SQL注入/XSS）— 仅检测有 payload 字段的包
                payload = p.get('payload', '')
                if payload:
                    payload_alerts = self.rule_detector.check_payload(payload)
                    alerts.extend(payload_alerts)

        if alerts:
            self._rule_alert_count += 1
        return alerts

    def ml_detect(self, packets: list[dict]) -> DualDetectionResult:
        """对聚合的包数据执行 ML 预测。"""
        if not self._model_loaded or not packets:
            return DualDetectionResult()

        try:
            import pandas as pd
            from campus_ids.capture.enhanced_features import (
                PacketInfo, aggregate_flow_features, flow_to_feature_vector,
                FEATURE_NAMES
            )
            from campus_ids.model.data_loader import align_features
            from campus_ids.model.utils import clean_features

            # 将包信息转为 PacketInfo
            pkt_infos = []
            for p in packets:
                try:
                    info = PacketInfo(
                        src_ip=p.get('src_ip', '0.0.0.0'),
                        dst_ip=p.get('dst_ip', ''),
                        src_port=int(p.get('sport', 0)),
                        dst_port=int(p.get('dport', 0)),
                        proto=p.get('proto', 'TCP'),
                        length=int(p.get('length', 0)),
                        timestamp=p.get('timestamp', time.time()),
                        is_syn=bool(p.get('is_syn', False)),
                    )
                    pkt_infos.append(info)
                except Exception:
                    continue

            if not pkt_infos:
                return DualDetectionResult()

            # 聚合为流特征
            flows = aggregate_flow_features(pkt_infos, [])
            if not flows:
                return DualDetectionResult()

            # 取模型 artifact 与训练时特征列表
            clf = self._artifact["model"]
            scaler = self._artifact.get("scaler")
            le = self._artifact.get("label_encoder")
            # 实时聚合产出 18 维向量（含 3 个 TLS 特征），而注册表中的模型可能按
            # 15 维训练（无 TLS 特征）。必须按模型自身 feature_columns 对齐（裁剪
            # 多余列/补齐缺失列），否则 scaler.transform 会因维度不符抛错。
            # 与离线 predict() 路径保持一致。
            model_cols = self._artifact.get("feature_columns") or list(FEATURE_NAMES)

            # 所有流批量组装为带列名 DataFrame（向量顺序与 FEATURE_NAMES 一致）
            rows = [flow_to_feature_vector(flow) for flow in flows]
            X_df = pd.DataFrame(rows, columns=FEATURE_NAMES)
            X = clean_features(align_features(X_df, model_cols))
            X_scaled = scaler.transform(X) if scaler else X.values

            preds_encoded = clf.predict(X_scaled)
            if le:
                labels = le.inverse_transform(preds_encoded)
            else:
                labels = preds_encoded
            predictions = [str(x) for x in labels]

            # 获取置信度（概率）
            if hasattr(clf, 'predict_proba'):
                confidences = [float(max(p)) for p in clf.predict_proba(X_scaled)]
            else:
                confidences = [1.0] * len(predictions)

            if not predictions:
                return DualDetectionResult()

            self._ml_predict_count += 1

            # 统计预测结果
            from collections import Counter
            pred_counts = Counter(predictions)
            most_common = pred_counts.most_common(1)[0]
            majority_label = most_common[0]
            attack_count = sum(c for l, c in pred_counts.items() if l != "Normal")
            avg_confidence = sum(confidences) / len(confidences)

            result = DualDetectionResult(
                is_anomaly=majority_label != "Normal",
                ml_prediction=majority_label,
                ml_confidence=round(avg_confidence, 4),
                attack_type=majority_label if majority_label != "Normal" else "Normal",
                description=f"ML预测: {majority_label} (置信度: {avg_confidence:.2%}, "
                           f"攻击流: {attack_count}/{len(predictions)})",
            )

            if result.is_anomaly:
                self._ml_attack_count += 1

            self._last_ml_result = result
            self._ml_history.append(result)
            return result

        except Exception as exc:
            logger.debug("ML 预测异常: %s", exc)
            return DualDetectionResult()

    def detect(self, qps: int, port_count: int,
               syn_count: int, udp_count: int,
               packets: list[dict] | None = None) -> DualDetectionResult:
        """P2-11: 双引擎融合检测（含检测延迟测量）。

        规则快速预筛 → 仅规则触发或不确定时才调 ML。
        """
        import time as _time
        t_start = _time.perf_counter()

        # 规则检测（始终执行，0.015ms 级延迟）
        rule_alerts = self.rule_detect(qps, port_count, syn_count, udp_count, packets=packets)
        rule_triggered = len(rule_alerts) > 0

        ml_result = self._last_ml_result
        # 当后台 ML 循环运行时，使用其缓存结果，避免与 add_packet→_drain_flow_buffer→ml_detect 重复处理；
        # 仅在后台循环未运行时，才直接对传入的 packets 调用 ml_detect（回退路径）。
        if packets and not self._ml_running:
            ml_result = self.ml_detect(packets)
        ml_triggered = ml_result.is_anomaly

        # ── 融合判定（v2：自适应 OR 互补）──
        # ML 高置信(>ML_CONF_HIGH)：直接采用 ML 判定
        # ML 低置信(ML_CONF_LOW~ML_CONF_HIGH) + 规则触发：提升为攻击（互补提升召回）
        # 规则独有触发：低危告警
        #
        # 注：向量化批处理版见 evaluation._evaluate_dual_fusion()，策略语义保持一致。

        if rule_triggered and ml_triggered:
            level = LEVEL_HIGH
            attack_type = ml_result.attack_type if ml_result.attack_type != "Normal" else "Attack"
        elif ml_triggered and ml_result.ml_confidence >= ML_CONF_HIGH:
            # ML 高置信单独触发：中危（ML 可信度高）
            level = LEVEL_MEDIUM
            attack_type = ml_result.attack_type
        elif ml_triggered:
            # ML 低置信触发：中危但标记不确定
            level = LEVEL_MEDIUM
            attack_type = ml_result.attack_type
        elif rule_triggered:
            # 仅规则触发：低危
            level = LEVEL_LOW
            attack_type = "RuleAlert"
        else:
            level = LEVEL_SAFE
            attack_type = "Normal"

        # 构建描述
        desc_parts = []
        if rule_alerts:
            desc_parts.append("规则: " + "; ".join(rule_alerts))
        if ml_triggered:
            desc_parts.append(ml_result.description)

        # P2-11: 计算检测延迟
        latency_ms = (_time.perf_counter() - t_start) * 1000
        self._latency_samples.append(latency_ms)

        return DualDetectionResult(
            is_anomaly=rule_triggered or ml_triggered,
            level=level,
            rule_alerts=rule_alerts,
            ml_prediction=ml_result.ml_prediction,
            ml_confidence=ml_result.ml_confidence,
            attack_type=attack_type,
            description=" | ".join(desc_parts) if desc_parts else "正常",
            detection_latency_ms=round(latency_ms, 2),
        )

    def get_stats(self) -> dict:
        """P2-11: 获取双引擎检测统计（含检测延迟指标）。"""
        import numpy as _np
        stats = {
            "model_loaded": self._model_loaded,
            "ml_predict_count": self._ml_predict_count,
            "ml_attack_count": self._ml_attack_count,
            "rule_alert_count": self._rule_alert_count,
            "last_ml_prediction": self._last_ml_result.ml_prediction,
            "last_ml_confidence": self._last_ml_result.ml_confidence,
            "last_ml_attack_type": self._last_ml_result.attack_type,
        }
        # P2-11: 检测延迟统计
        if self._latency_samples:
            latencies = list(self._latency_samples)
            stats["detection_latency_ms"] = {
                "mean": round(_np.mean(latencies), 2),
                "p50": round(_np.percentile(latencies, 50), 2),
                "p95": round(_np.percentile(latencies, 95), 2),
                "p99": round(_np.percentile(latencies, 99), 2),
                "max": round(max(latencies), 2),
                "samples": len(latencies),
            }
        return stats

    def start_ml_loop(self) -> None:
        """启动后台 ML 预测循环。"""
        if not self._model_loaded:
            logger.warning("ML 模型未加载，无法启动预测循环")
            return

        self._ml_running = True

        def _loop():
            while self._ml_running:
                try:
                    pkts = self._drain_flow_buffer()
                    if pkts:
                        self.ml_detect(pkts)
                    time.sleep(self.ml_interval)
                except Exception as exc:
                    logger.debug("ML 预测循环异常: %s", exc)
                    time.sleep(self.ml_interval)

        self._ml_thread = threading.Thread(target=_loop, daemon=True)
        self._ml_thread.start()
        logger.info("ML 预测循环已启动（间隔 %.1f 秒）", self.ml_interval)

    def stop_ml_loop(self) -> None:
        """停止后台 ML 预测循环。"""
        self._ml_running = False
        if self._ml_thread:
            self._ml_thread.join(timeout=3)
        logger.info("ML 预测循环已停止")