"""database.py CRUD 测试 — 告警/流量/配置/用户操作。"""
import os
import tempfile
import pytest

from campus_ids.web import database as db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """每个测试使用独立的临时数据库。"""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    # 重置线程局部连接
    if hasattr(db._local, "conn"):
        db._local.conn = None
    db.init_db()
    yield
    if hasattr(db._local, "conn") and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


# ── 告警 CRUD ─────────────────────────────────────────────────────

class TestAlertCRUD:
    def test_insert_and_query(self):
        rid = db.insert_alert("2026-01-01 00:00:00", "high", "ddos", "DDoS detected")
        assert rid > 0
        alerts = db.query_alerts()
        assert len(alerts) >= 1
        assert alerts[0]["attack_type"] == "ddos"

    def test_query_with_level_filter(self):
        db.insert_alert("2026-01-01 00:00:00", "high", "ddos", "A1")
        db.insert_alert("2026-01-01 00:00:00", "low", "scan", "A2")
        high = db.query_alerts(level="high")
        assert all(a["level"] == "high" for a in high)

    def test_query_with_limit(self):
        for i in range(10):
            db.insert_alert("2026-01-01 00:00:00", "high", "ddos", f"A{i}")
        limited = db.query_alerts(limit=5)
        assert len(limited) == 5

    def test_count_alerts(self):
        db.insert_alert("2026-01-01 00:00:00", "high", "ddos", "A1")
        db.insert_alert("2026-01-01 00:00:00", "low", "scan", "A2")
        assert db.count_alerts() == 2
        assert db.count_alerts(level="high") == 1

    def test_alert_type_distribution(self):
        db.insert_alert("2026-01-01 00:00:00", "high", "ddos", "A1")
        db.insert_alert("2026-01-01 00:00:00", "high", "ddos", "A2")
        db.insert_alert("2026-01-01 00:00:00", "low", "scan", "A3")
        dist = db.get_alert_type_distribution()
        assert dist["ddos"] == 2
        assert dist["scan"] == 1


# ── 流量历史 CRUD ─────────────────────────────────────────────────

class TestTrafficCRUD:
    def test_insert_and_query(self):
        rid = db.insert_traffic("2026-01-01 00:00:00", 100, 50, 200, 10, 5)
        assert rid > 0
        rows = db.query_traffic()
        assert len(rows) >= 1
        assert rows[0]["qps"] == 100

    def test_query_with_limit(self):
        for i in range(10):
            db.insert_traffic("2026-01-01 00:00:00", i, 0, 0, 0, 0)
        limited = db.query_traffic(limit=3)
        assert len(limited) == 3


# ── 配置 CRUD ─────────────────────────────────────────────────────

class TestConfigCRUD:
    def test_get_all_config_empty(self):
        config = db.get_all_config()
        assert isinstance(config, dict)

    def test_bulk_set_and_get(self):
        db.bulk_set_config({"key1": "val1", "key2": "val2"})
        config = db.get_all_config()
        assert config["key1"] == "val1"
        assert config["key2"] == "val2"

    def test_bulk_set_upsert(self):
        db.bulk_set_config({"key1": "old"})
        db.bulk_set_config({"key1": "new"})
        config = db.get_all_config()
        assert config["key1"] == "new"


# ── 数据清理 ──────────────────────────────────────────────────────

class TestCleanup:
    def test_cleanup_removes_old_data(self):
        db.insert_alert("2020-01-01 00:00:00", "high", "ddos", "old alert")
        db.insert_traffic("2020-01-01 00:00:00", 0, 0, 0, 0, 0)
        result = db.cleanup_old_data(days=1)
        assert result["alerts_deleted"] >= 1
        assert result["traffic_deleted"] >= 1

    def test_cleanup_keeps_recent_data(self):
        db.insert_alert("2099-01-01 00:00:00", "high", "ddos", "future alert")
        result = db.cleanup_old_data(days=1)
        assert result["alerts_deleted"] == 0


# ── 用户 CRUD ─────────────────────────────────────────────────────

class TestUserCRUD:
    def test_create_and_get_by_username(self):
        from werkzeug.security import generate_password_hash
        uid = db.create_user("testuser", generate_password_hash("pass123"))
        assert uid > 0
        user = db.get_user_by_username("testuser")
        assert user is not None
        assert user["username"] == "testuser"

    def test_get_by_id(self):
        from werkzeug.security import generate_password_hash
        uid = db.create_user("testuser2", generate_password_hash("pass123"))
        user = db.get_user_by_id(uid)
        assert user is not None
        assert user["username"] == "testuser2"

    def test_get_nonexistent_user(self):
        assert db.get_user_by_username("nonexistent") is None
        assert db.get_user_by_id(99999) is None

    def test_update_password(self):
        from werkzeug.security import generate_password_hash, check_password_hash
        uid = db.create_user("testuser3", generate_password_hash("old"))
        db.update_user_password(uid, generate_password_hash("new"))
        user = db.get_user_by_id(uid)
        assert check_password_hash(user["password_hash"], "new")

    def test_ensure_default_user(self):
        db.ensure_default_user()
        user = db.get_user_by_username("admin")
        assert user is not None
        # 重复调用不报错
        db.ensure_default_user()