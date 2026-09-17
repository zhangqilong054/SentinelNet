# -*- coding: utf-8 -*-
"""管理端点行为回归 —— `POST /api/admin/export`（T2.15）与 `POST /api/admin/cleanup`（T2.14）。

## 为什么需要这个文件

改动前这两个端点**只出现在 `tests/test_openapi.py` 的路径清单里** —— 即只验证了
"OpenAPI schema 里有这条路径"，**没有任何测试真的调用过它们**。这正是本项目的
老毛病：schema 层面绿，行为层面空白。

因此本文件全部断言**对外可观测结果**：
- export：真读落盘 CSV 的表头/行数/内容，而不是只看 200；
- cleanup：真查库看哪些行被删，而不是只看返回的计数（返回的计数本身也可能是假的）。

⚠️ export 会**覆盖** `traffic_stats.csv`、cleanup 会**真删数据** —— 安全性来自
`tests/conftest.py` 的会话级 + per-test 双重 data_dir 重定向，不来自"这些端点是只读的"。
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from campus_ids.runtime.db import alerts, get_connection, traffic_history
from campus_ids.runtime.repositories import TrafficRepository
from campus_ids.runtime.settings import get_settings, reset_settings
from campus_ids.web_new.app import create_app

CSV_HEADER = ["Time", "QPS", "Connections", "PacketCount", "PortCount", "SrcIPCount", "Alert"]


@pytest.fixture(autouse=True)
def _clean_env():
    """隔离环境 + 重置限流器（export/cleanup 各带 10/minute 限流）。"""

    def _clear() -> None:
        os.environ.pop("CAMPUS_IDS_API_TOKEN", None)
        os.environ.pop("CAMPUS_IDS_AUTH_ENABLED", None)
        reset_settings()
        from campus_ids.web_new.security import limiter

        limiter.reset()

    _clear()
    yield
    _clear()


@pytest.fixture()
def client():
    """带 lifespan 的客户端（lifespan 里才会 init_db 建表）。"""
    with TestClient(create_app()) as c:
        yield c


def _csrf(client: TestClient) -> dict[str, str]:
    token = client.get("/api/csrf-token").json()["csrf_token"]
    client.cookies.set("csrf_token", token)
    return {"X-CSRFToken": token}


def _seed_traffic(rows: list[dict]) -> None:
    with get_connection() as conn:
        for row in rows:
            TrafficRepository.insert(conn, **row)


def _seed_alerts(rows: list[dict]) -> None:
    with get_connection() as conn:
        conn.execute(insert(alerts), rows)
        conn.commit()


def _read_csv() -> list[list[str]]:
    path = get_settings().data_dir / "traffic_stats.csv"
    assert path.exists(), f"导出文件未落盘: {path}"
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def _ts(days_ago: float = 0) -> str:
    return (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


# ══ T2.15 导出 ═══════════════════════════════════════════════════

class TestExport:
    """`POST /api/admin/export` 取代旧 `POST /api/save`。"""

    def test_export_writes_seeded_rows_to_csv(self, client):
        """真预置 2 行 → 真调用 → 真读 CSV 校验表头与内容。"""
        _seed_traffic([
            {"time": "2026-09-17 10:00:00", "qps": 11, "connections": 22,
             "packet_count": 33, "port_count": 44, "src_ip_count": 55, "alert": "Normal"},
            {"time": "2026-09-17 10:00:01", "qps": 66, "connections": 77,
             "packet_count": 88, "port_count": 99, "src_ip_count": 111, "alert": "SYN_FLOOD"},
        ])

        resp = client.post("/api/admin/export", headers=_csrf(client))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "success"
        assert body["rows"] == 2, f"上报行数应等于真实导出行数，实际 {body}"

        table = _read_csv()
        assert table[0] == CSV_HEADER, f"表头不符: {table[0]}"
        assert len(table) == 3, f"应为表头 + 2 行，实际 {len(table)} 行"

        # Repository.query 按 id 倒序 → 最后插入的排在最前
        assert table[1][0] == "2026-09-17 10:00:01"
        assert table[1][1:6] == ["66", "77", "88", "99", "111"]
        assert table[1][6] == "SYN_FLOOD"
        assert table[2][6] == "Normal"

    def test_export_message_mentions_filename(self, client):
        resp = client.post("/api/admin/export", headers=_csrf(client))
        assert resp.status_code == 200
        assert "traffic_stats.csv" in resp.json()["message"]

    def test_export_on_empty_db_writes_header_only(self, client):
        """空库不得报错，应产出只有表头的 CSV 且 rows=0。"""
        resp = client.post("/api/admin/export", headers=_csrf(client))
        assert resp.status_code == 200, resp.text
        assert resp.json()["rows"] == 0
        table = _read_csv()
        assert table == [CSV_HEADER], f"空库应只有表头，实际 {table}"

    def test_export_overwrites_previous_file(self, client):
        """重复导出是**覆盖**而非追加 —— 否则 CSV 会无限增长。"""
        _seed_traffic([{"time": "2026-09-17 11:00:00", "qps": 1}])
        assert client.post("/api/admin/export", headers=_csrf(client)).json()["rows"] == 1

        with get_connection() as conn:
            conn.execute(traffic_history.delete())
            conn.commit()

        assert client.post("/api/admin/export", headers=_csrf(client)).json()["rows"] == 0
        assert len(_read_csv()) == 1, "第二次导出未覆盖第一次的内容"

    def test_export_requires_csrf(self, client):
        """写端点缺 CSRF 必须被拒绝（旧应用曾是 GET，可被跨站触发）。

        状态码是 **403**（`security.verify_csrf_pair` 对"缺失/不匹配/格式非法/
        签名无效"四种情形统一用 `HTTP_403_FORBIDDEN`），不是 400。
        """
        resp = client.post("/api/admin/export")
        assert resp.status_code == 403, f"缺 CSRF 应 403，实际 {resp.status_code}"

    def test_export_rejects_get(self, client):
        """必须是 POST —— GET 会被浏览器/预取跨站触发。"""
        assert client.get("/api/admin/export").status_code == 405


# ══ T2.14 清理 ═══════════════════════════════════════════════════

class TestCleanup:
    """`POST /api/admin/cleanup` —— 修复前 `days` 参数被**完全忽略**。"""

    def test_days_parameter_actually_takes_effect(self, client):
        """`days=7` 删掉 30 天前的数据、保留今天的；`days=9999` 一条不删。

        修复前的实现用 `cutoff = utcnow()`（无视 days），三种 days 删除数完全相同。
        """
        _seed_traffic([{"time": _ts(30), "qps": 1}, {"time": _ts(0), "qps": 2}])
        _seed_alerts([
            {"time": _ts(30), "level": "low", "attack_type": "old", "message": "30天前"},
            {"time": _ts(0), "level": "low", "attack_type": "new", "message": "刚刚"},
        ])

        resp = client.post("/api/admin/cleanup", json={"days": 7}, headers=_csrf(client))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["alerts_deleted"] == 1, f"应删 1 条旧告警，实际 {body}"
        assert body["traffic_deleted"] == 1, f"应删 1 条旧流量，实际 {body}"

        with get_connection() as conn:
            left_alerts = [r[0] for r in conn.execute(select(alerts.c.message)).all()]
            left_traffic = [r[0] for r in conn.execute(select(traffic_history.c.qps)).all()]
        assert left_alerts == ["刚刚"], f"旧告警未删净: {left_alerts}"
        assert left_traffic == [2], f"旧流量未删净: {left_traffic}"

    def test_large_days_deletes_nothing(self, client):
        """`days=9999` 是"安全值"，不得删除任何数据。"""
        _seed_traffic([{"time": _ts(30), "qps": 1}, {"time": _ts(0), "qps": 2}])

        resp = client.post("/api/admin/cleanup", json={"days": 9999}, headers=_csrf(client))
        assert resp.status_code == 200
        assert resp.json()["traffic_deleted"] == 0, "days=9999 不应删任何数据"

        with get_connection() as conn:
            assert len(conn.execute(select(traffic_history.c.id)).all()) == 2

    def test_deleted_counts_are_per_table_not_merged(self, client):
        """两个计数必须**分别对应两张表** —— 修复前 `traffic_deleted` 填的是两表合计。"""
        _seed_traffic([{"time": _ts(30), "qps": 1}, {"time": _ts(30), "qps": 3}])
        _seed_alerts([
            {"time": _ts(30), "level": "low", "attack_type": "a", "message": "x"},
        ])

        body = client.post(
            "/api/admin/cleanup", json={"days": 7}, headers=_csrf(client)
        ).json()
        assert body["alerts_deleted"] == 1
        assert body["traffic_deleted"] == 2, (
            f"traffic_deleted 应为 2（不是两表合计 3），实际 {body}"
        )

    def test_default_days_is_seven_and_keeps_recent(self, client):
        """不传 days → 默认 7 天；近期数据必须保留。"""
        _seed_traffic([{"time": _ts(0), "qps": 7}])
        resp = client.post("/api/admin/cleanup", json={}, headers=_csrf(client))
        assert resp.status_code == 200
        assert resp.json()["traffic_deleted"] == 0

    def test_days_must_be_positive(self, client):
        """`days=0` / 负数应被 schema 拒绝（ge=1）。"""
        for bad in (0, -1):
            resp = client.post("/api/admin/cleanup", json={"days": bad}, headers=_csrf(client))
            assert resp.status_code in (400, 422), f"days={bad} 应被拒绝，实际 {resp.status_code}"

    def test_cleanup_requires_csrf(self, client):
        """缺 CSRF → 403（见 `test_export_requires_csrf` 的说明）。"""
        resp = client.post("/api/admin/cleanup", json={"days": 7})
        assert resp.status_code == 403
