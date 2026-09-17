# -*- coding: utf-8 -*-
"""`services/alert_service.py` 行为测试（R5 覆盖率补齐）。

改动前覆盖率 **44%**。本文件覆盖冷却边界、落库、广播 topic、查询过滤与统计。
"""
from __future__ import annotations

import logging

import pytest

from campus_ids.runtime.db import get_connection, init_db
from campus_ids.runtime.repositories import AlertRepository
from campus_ids.services.alert_service import ALERT_COOLDOWN_SECONDS, AlertService


class RecordingBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    def publish(self, topic: str, data: object = None) -> None:
        self.published.append((topic, data))


@pytest.fixture(autouse=True)
def _schema():
    """新库需要建表才能落库（conftest 只建了旧库的表）。"""
    init_db()


@pytest.fixture
def bus() -> RecordingBus:
    return RecordingBus()


@pytest.fixture
def svc(bus) -> AlertService:
    return AlertService(event_bus=bus)


def _count() -> int:
    with get_connection() as conn:
        return AlertRepository.count(conn)


def _emit(svc: AlertService, attack_type: str, level: str, message: str) -> dict:
    """发射一条告警，**先清掉该类型的冷却记录**。

    冷却窗口是 60s 且按 attack_type 隔离，而测试不可能真等 60s。
    凡是要对同一类型连发多条的场景都必须走这里，否则只有第一条会落库，
    断言会以"少了一条"的形式失败（看起来像业务 bug，其实是测试自己被冷却压住了）。
    """
    svc._last_alert_time.pop(attack_type, None)
    result = svc.emit_alert(attack_type, level, message)
    assert result is not None, f"{attack_type} 在清冷却后仍被压制，冷却实现有误"
    return result


# ── emit_alert ────────────────────────────────────────────────────

class TestEmitAlert:
    def test_persists_and_broadcasts(self, svc, bus):
        data = svc.emit_alert("SYN_FLOOD", "high", "检测到 SYN 洪泛", ml_confidence=0.88)

        assert data is not None
        assert data["attack_type"] == "SYN_FLOOD"
        assert data["level"] == "high"
        assert data["message"] == "检测到 SYN 洪泛"
        assert data["ml_confidence"] == 0.88
        assert isinstance(data["id"], int) and data["id"] > 0, "必须回传落库后的自增 id"

        with get_connection() as conn:
            rows = AlertRepository.query(conn, limit=10)
        assert len(rows) == 1 and rows[0].attack_type == "SYN_FLOOD"

    def test_broadcasts_on_alert_topic(self, svc, bus):
        """广播 topic 必须是 `alert`（单数）—— 曾写死 `alerts` 导致前端收不到。"""
        svc.emit_alert("XSS", "medium", "x")
        assert [t for t, _ in bus.published] == ["alert"]

    def test_works_without_event_bus(self):
        """没接事件总线时仍必须落库（只是没有实时推送）。"""
        svc = AlertService(event_bus=None)
        assert svc.emit_alert("NO_BUS", "low", "x") is not None
        assert _count() == 1


class TestCooldown:
    def test_second_alert_same_type_is_suppressed(self, svc, bus):
        assert svc.emit_alert("SYN_FLOOD", "high", "第一次") is not None
        assert svc.emit_alert("SYN_FLOOD", "high", "第二次") is None

        assert _count() == 1, "被冷却的告警不得落库"
        assert len(bus.published) == 1, "被冷却的告警不得广播"

    def test_cooldown_is_per_attack_type(self, svc):
        """冷却按 attack_type 隔离 —— 一种攻击不得压住另一种。"""
        assert svc.emit_alert("SYN_FLOOD", "high", "a") is not None
        assert svc.emit_alert("PORT_SCAN", "low", "b") is not None
        assert svc.emit_alert("SQL_INJECTION", "high", "c") is not None
        assert _count() == 3

    def test_emits_again_after_window_elapses(self, svc):
        svc.emit_alert("SYN_FLOOD", "high", "第一次")
        # 把上次时间倒推，模拟冷却窗口已过（不 sleep 60s）
        svc._last_alert_time["SYN_FLOOD"] -= ALERT_COOLDOWN_SECONDS + 1
        assert svc.emit_alert("SYN_FLOOD", "high", "第二次") is not None
        assert _count() == 2

    def test_boundary_exactly_at_window_still_suppressed(self, svc):
        svc.emit_alert("X", "low", "a")
        svc._last_alert_time["X"] -= ALERT_COOLDOWN_SECONDS - 1  # 窗口内
        assert svc.emit_alert("X", "low", "b") is None

    def test_suppressed_alert_is_logged_at_debug(self, svc, caplog):
        svc.emit_alert("X", "low", "a")
        with caplog.at_level(logging.DEBUG, logger="campus_ids.services.alert_service"):
            svc.emit_alert("X", "low", "b")
        assert any("冷却" in r.getMessage() for r in caplog.records)


# ── get_alerts ────────────────────────────────────────────────────

class TestGetAlerts:
    @pytest.fixture(autouse=True)
    def _seed(self, svc):
        # 三个不同类型 —— 冷却按 attack_type 隔离，所以三条都能落库。
        # 不要改成同一类型，否则会被冷却压掉两条（需改用模块级 _emit）。
        svc.emit_alert("A1", "high", "m1")
        svc.emit_alert("A2", "low", "m2")
        svc.emit_alert("A3", "high", "m3")

    def test_returns_pagination_envelope(self, svc):
        result = svc.get_alerts()
        assert set(result) == {"alerts", "total", "limit", "offset"}
        assert result["total"] == 3 and result["limit"] == 50 and result["offset"] == 0

    def test_filters_by_level(self, svc):
        assert svc.get_alerts(level="high")["total"] == 2
        assert svc.get_alerts(level="low")["total"] == 1

    def test_level_all_means_no_filter(self, svc):
        assert svc.get_alerts(level="all")["total"] == 3

    def test_level_none_means_no_filter(self, svc):
        assert svc.get_alerts(level=None)["total"] == 3

    def test_limit_is_capped(self, svc):
        assert svc.get_alerts(limit=99999)["limit"] == 500

    def test_offset_and_limit_slice_rows_but_keep_total(self, svc):
        result = svc.get_alerts(limit=2, offset=1)
        assert len(result["alerts"]) == 2
        assert result["total"] == 3, "total 必须是过滤后的总数，不受分页影响"

    def test_alert_fields_are_exposed(self, svc):
        alert = svc.get_alerts(level="low")["alerts"][0]
        assert set(alert) == {"id", "time", "level", "attack_type",
                              "message", "ml_confidence"}


class TestGetAlertStats:
    def test_empty_db(self, svc):
        stats = svc.get_alert_stats()
        assert stats["total_alerts"] == 0
        assert stats["alerts_by_type"] == {}
        assert stats["alerts_by_severity"] == {"high": 0, "medium": 0, "low": 0}

    def test_counts_by_type_and_severity(self, svc):
        # 两条 SYN_FLOOD 必须显式清冷却，否则第二条被 60s 窗口压掉（见 _emit）
        _emit(svc, "SYN_FLOOD", "high", "a")
        _emit(svc, "SYN_FLOOD", "high", "b")
        _emit(svc, "XSS", "medium", "c")
        _emit(svc, "PORT_SCAN", "low", "d")

        stats = svc.get_alert_stats()
        assert stats["total_alerts"] == 4
        assert stats["alerts_by_type"] == {"SYN_FLOOD": 2, "XSS": 1, "PORT_SCAN": 1}
        assert stats["alerts_by_severity"] == {"high": 2, "medium": 1, "low": 1}

    def test_empty_level_bucket_is_zero_not_missing(self, svc):
        """没有任何 low 告警时，`low` 键必须仍存在且为 0（前端直接读该键）。"""
        _emit(svc, "ONLY_HIGH", "high", "a")
        assert svc.get_alert_stats()["alerts_by_severity"] == {
            "high": 1, "medium": 0, "low": 0,
        }
