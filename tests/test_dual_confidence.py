"""O-06: ML 置信度三场景单元测试 — 高置信/低置信/规则辅助融合判定。

验证 DualDetector.detect() 的融合策略：
- ML + 规则同时触发 → LEVEL_HIGH
- ML 单独触发 → LEVEL_MEDIUM
- 仅规则触发 → LEVEL_LOW
- ML 高/低置信度正确传播
"""
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from campus_ids.detector.dual_detector import (
    DualDetector, DualDetectionResult,
    LEVEL_HIGH, LEVEL_MEDIUM, LEVEL_LOW, LEVEL_SAFE,
)
from campus_ids.detector.detector import AnomalyDetector


@pytest.fixture
def dual():
    """创建 DualDetector 实例（不加载模型）。"""
    rule_det = AnomalyDetector(ddos_threshold=500, port_scan_threshold=50)
    det = DualDetector(rule_det, model_path="/nonexistent/model.pkl")
    return det


class TestMLHighConfidence:
    """ML 高置信度单独触发 → 中危。"""

    def test_ml_only_high_confidence_is_medium(self, dual):
        """ML 单独触发（高置信度）应标记为中危。"""
        ml_result = DualDetectionResult(
            is_anomaly=True,
            level=LEVEL_MEDIUM,
            ml_prediction="DDoS",
            ml_confidence=0.95,
            attack_type="DDoS",
            description="ML预测: DDoS (置信度: 95.00%)",
        )
        dual._last_ml_result = ml_result

        # 规则不触发（低 QPS）
        result = dual.detect(qps=100, port_count=10, syn_count=5, udp_count=5)
        assert result.is_anomaly is True
        assert result.level == LEVEL_MEDIUM
        assert result.ml_confidence == 0.95
        assert result.attack_type == "DDoS"


class TestMLLowConfidence:
    """ML 低置信度单独触发 → 中危（但置信度低）。"""

    def test_ml_only_low_confidence_still_medium(self, dual):
        """ML 单独触发（低置信度）仍为中危，但置信度值低。"""
        ml_result = DualDetectionResult(
            is_anomaly=True,
            level=LEVEL_MEDIUM,
            ml_prediction="PortScan",
            ml_confidence=0.52,
            attack_type="PortScan",
            description="ML预测: PortScan (置信度: 52.00%)",
        )
        dual._last_ml_result = ml_result

        result = dual.detect(qps=100, port_count=10, syn_count=5, udp_count=5)
        assert result.is_anomaly is True
        assert result.level == LEVEL_MEDIUM
        assert result.ml_confidence == 0.52


class TestRuleAssisted:
    """规则辅助（双引擎触发）→ 高危。"""

    def test_rule_plus_ml_is_high(self, dual):
        """规则 + ML 同时触发应标记为高危。"""
        ml_result = DualDetectionResult(
            is_anomaly=True,
            level=LEVEL_MEDIUM,
            ml_prediction="DDoS",
            ml_confidence=0.88,
            attack_type="DDoS",
            description="ML预测: DDoS",
        )
        dual._last_ml_result = ml_result

        # 规则触发（高 QPS 超过阈值）
        result = dual.detect(qps=600, port_count=10, syn_count=5, udp_count=5)
        assert result.is_anomaly is True
        assert result.level == LEVEL_HIGH
        assert result.attack_type == "DDoS"

    def test_rule_only_is_low(self, dual):
        """仅规则触发（ML 未触发）应为低危。"""
        ml_result = DualDetectionResult(
            is_anomaly=False,
            level=LEVEL_SAFE,
            ml_prediction="Normal",
            ml_confidence=0.0,
            attack_type="Normal",
            description="",
        )
        dual._last_ml_result = ml_result

        result = dual.detect(qps=600, port_count=10, syn_count=5, udp_count=5)
        assert result.is_anomaly is True
        assert result.level == LEVEL_LOW
        assert result.attack_type == "RuleAlert"


class TestSafeScenario:
    """双引擎均未触发 → 安全。"""

    def test_no_alert_when_safe(self, dual):
        """规则和 ML 均未触发应为安全。"""
        ml_result = DualDetectionResult(
            is_anomaly=False,
            level=LEVEL_SAFE,
            ml_prediction="Normal",
            ml_confidence=0.0,
            attack_type="Normal",
            description="",
        )
        dual._last_ml_result = ml_result

        result = dual.detect(qps=100, port_count=10, syn_count=5, udp_count=5)
        assert result.is_anomaly is False
        assert result.level == LEVEL_SAFE


class TestConfidencePropagation:
    """验证 ML 置信度正确传播到最终结果。"""

    def test_confidence_carried_to_result(self, dual):
        """ML 置信度应正确传播到 DualDetectionResult。"""
        for conf in [0.55, 0.75, 0.99]:
            ml_result = DualDetectionResult(
                is_anomaly=True,
                level=LEVEL_MEDIUM,
                ml_prediction="Attack",
                ml_confidence=conf,
                attack_type="Attack",
                description="ML预测: Attack",
            )
            dual._last_ml_result = ml_result

            result = dual.detect(qps=100, port_count=10, syn_count=5, udp_count=5)
            assert result.ml_confidence == conf, f"置信度 {conf} 未正确传播"

    def test_detection_latency_recorded(self, dual):
        """检测延迟应被记录。"""
        ml_result = DualDetectionResult()
        dual._last_ml_result = ml_result

        result = dual.detect(qps=100, port_count=10, syn_count=5, udp_count=5)
        assert result.detection_latency_ms >= 0