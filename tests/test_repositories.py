"""T1.10 数据访问层（Repository）— SQLAlchemy Core CRUD 测试。

验收标准：
- AlertRepository: insert/query/count/get_type_distribution
- TrafficRepository: insert/query
- ConfigRepository: get_all/get/set/bulk_set/delete
- UserRepository: get_by_username/get_by_id/create/update_password/ensure_default/cleanup_old_data
- 使用 SQLite 内存数据库，每个测试独立事务
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from campus_ids.runtime.db import metadata
from campus_ids.runtime.repositories import (
    AlertRepository,
    TrafficRepository,
    ConfigRepository,
    UserRepository,
)


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """创建 SQLite 内存引擎并建表。"""
    eng = create_engine("sqlite:///:memory:", echo=False)
    metadata.create_all(eng)
    # 启用外键约束
    with eng.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.commit()
    return eng


@pytest.fixture
def conn(engine):
    """提供测试用数据库连接，测试后回滚。"""
    with engine.connect() as connection:
        yield connection
        connection.rollback()


# ── AlertRepository ─────────────────────────────────────────────


class TestAlertRepositoryInsert:
    """AlertRepository.insert 测试。"""

    def test_insert_returns_id(self, conn):
        """插入告警返回自增 ID。"""
        rid = AlertRepository.insert(
            conn, time="2026-01-01 00:00:00", level="high",
            attack_type="ddos", message="DDoS detected", ml_confidence=0.95,
        )
        assert rid == 1

    def test_insert_multiple(self, conn):
        """插入多条告警 ID 递增。"""
        id1 = AlertRepository.insert(
            conn, time="2026-01-01 00:00:01", level="high",
            attack_type="ddos", message="DDoS 1",
        )
        id2 = AlertRepository.insert(
            conn, time="2026-01-01 00:00:02", level="medium",
            attack_type="port_scan", message="Port scan 1",
        )
        assert id2 == id1 + 1


class TestAlertRepositoryQuery:
    """AlertRepository.query 测试。"""

    def _insert_samples(self, conn):
        AlertRepository.insert(conn, time="2026-01-01 00:00:01", level="high", attack_type="ddos", message="m1")
        AlertRepository.insert(conn, time="2026-01-01 00:00:02", level="medium", attack_type="port_scan", message="m2")
        AlertRepository.insert(conn, time="2026-01-01 00:00:03", level="low", attack_type="infiltration", message="m3")

    def test_query_all(self, conn):
        """无过滤查询返回所有记录。"""
        self._insert_samples(conn)
        rows = AlertRepository.query(conn)
        assert len(rows) == 3

    def test_query_by_level(self, conn):
        """按 level 过滤查询。"""
        self._insert_samples(conn)
        rows = AlertRepository.query(conn, level="high")
        assert len(rows) == 1
        assert rows[0].level == "high"

    def test_query_limit_offset(self, conn):
        """分页查询。"""
        self._insert_samples(conn)
        rows = AlertRepository.query(conn, limit=2, offset=0)
        assert len(rows) == 2
        rows2 = AlertRepository.query(conn, limit=2, offset=2)
        assert len(rows2) == 1

    def test_query_order_desc(self, conn):
        """查询按 ID 降序排列。"""
        self._insert_samples(conn)
        rows = AlertRepository.query(conn)
        assert rows[0].id > rows[1].id


class TestAlertRepositoryCount:
    """AlertRepository.count 测试。"""

    def test_count_empty(self, conn):
        """空表计数为 0。"""
        assert AlertRepository.count(conn) == 0

    def test_count_all(self, conn):
        """无过滤计数。"""
        AlertRepository.insert(conn, time="2026-01-01 00:00:01", level="high", attack_type="ddos", message="m1")
        AlertRepository.insert(conn, time="2026-01-01 00:00:02", level="low", attack_type="scan", message="m2")
        assert AlertRepository.count(conn) == 2

    def test_count_by_level(self, conn):
        """按 level 过滤计数。"""
        AlertRepository.insert(conn, time="2026-01-01 00:00:01", level="high", attack_type="ddos", message="m1")
        AlertRepository.insert(conn, time="2026-01-01 00:00:02", level="high", attack_type="scan", message="m2")
        AlertRepository.insert(conn, time="2026-01-01 00:00:03", level="low", attack_type="scan", message="m3")
        assert AlertRepository.count(conn, level="high") == 2


class TestAlertRepositoryTypeDistribution:
    """AlertRepository.get_type_distribution 测试。"""

    def test_type_distribution(self, conn):
        """告警类型分布统计。"""
        AlertRepository.insert(conn, time="2026-01-01 00:00:01", level="high", attack_type="ddos", message="m1")
        AlertRepository.insert(conn, time="2026-01-01 00:00:02", level="high", attack_type="ddos", message="m2")
        AlertRepository.insert(conn, time="2026-01-01 00:00:03", level="low", attack_type="port_scan", message="m3")
        rows = AlertRepository.get_type_distribution(conn)
        types = {row.attack_type: row.count for row in rows}
        assert types["ddos"] == 2
        assert types["port_scan"] == 1

    def test_type_distribution_empty(self, conn):
        """空表类型分布为空。"""
        rows = AlertRepository.get_type_distribution(conn)
        assert len(rows) == 0


# ── TrafficRepository ───────────────────────────────────────────


class TestTrafficRepositoryInsert:
    """TrafficRepository.insert 测试。"""

    def test_insert_returns_id(self, conn):
        """插入流量记录返回自增 ID。"""
        rid = TrafficRepository.insert(
            conn, time="2026-01-01 00:00:00", qps=100, connections=50,
        )
        assert rid == 1

    def test_insert_minimal(self, conn):
        """仅必填字段插入。"""
        rid = TrafficRepository.insert(conn, time="2026-01-01 00:00:00")
        assert rid == 1


class TestTrafficRepositoryQuery:
    """TrafficRepository.query 测试。"""

    def test_query_empty(self, conn):
        """空表查询返回空。"""
        rows = TrafficRepository.query(conn)
        assert len(rows) == 0

    def test_query_with_data(self, conn):
        """查询返回插入的记录。"""
        TrafficRepository.insert(conn, time="2026-01-01 00:00:01", qps=100)
        TrafficRepository.insert(conn, time="2026-01-01 00:00:02", qps=200)
        rows = TrafficRepository.query(conn)
        assert len(rows) == 2

    def test_query_limit(self, conn):
        """分页查询。"""
        for i in range(5):
            TrafficRepository.insert(conn, time=f"2026-01-01 00:00:0{i}", qps=i * 100)
        rows = TrafficRepository.query(conn, limit=3)
        assert len(rows) == 3

    def test_query_order_desc(self, conn):
        """查询按 ID 降序排列。"""
        TrafficRepository.insert(conn, time="2026-01-01 00:00:01", qps=100)
        TrafficRepository.insert(conn, time="2026-01-01 00:00:02", qps=200)
        rows = TrafficRepository.query(conn)
        assert rows[0].id > rows[1].id


# ── ConfigRepository ────────────────────────────────────────────


class TestConfigRepositoryGet:
    """ConfigRepository.get 测试。"""

    def test_get_nonexistent(self, conn):
        """获取不存在的配置返回 None。"""
        assert ConfigRepository.get(conn, "nonexistent") is None

    def test_get_existing(self, conn):
        """获取已存在的配置。"""
        ConfigRepository.set(conn, "key1", "value1")
        assert ConfigRepository.get(conn, "key1") == "value1"


class TestConfigRepositoryGetAll:
    """ConfigRepository.get_all 测试。"""

    def test_get_all_empty(self, conn):
        """空表返回空字典。"""
        assert ConfigRepository.get_all(conn) == {}

    def test_get_all_with_data(self, conn):
        """获取所有配置项。"""
        ConfigRepository.set(conn, "k1", "v1")
        ConfigRepository.set(conn, "k2", "v2")
        result = ConfigRepository.get_all(conn)
        assert result == {"k1": "v1", "k2": "v2"}


class TestConfigRepositorySet:
    """ConfigRepository.set 测试。"""

    def test_set_new(self, conn):
        """设置新配置项。"""
        ConfigRepository.set(conn, "new_key", "new_value")
        assert ConfigRepository.get(conn, "new_key") == "new_value"

    def test_set_update(self, conn):
        """更新已有配置项（INSERT OR REPLACE）。"""
        ConfigRepository.set(conn, "key1", "old")
        ConfigRepository.set(conn, "key1", "new")
        assert ConfigRepository.get(conn, "key1") == "new"


class TestConfigRepositoryBulkSet:
    """ConfigRepository.bulk_set 测试。"""

    def test_bulk_set(self, conn):
        """批量设置配置项。"""
        ConfigRepository.bulk_set(conn, {"k1": "v1", "k2": "v2", "k3": "v3"})
        result = ConfigRepository.get_all(conn)
        assert result == {"k1": "v1", "k2": "v2", "k3": "v3"}

    def test_bulk_set_upsert(self, conn):
        """批量设置覆盖已有值。"""
        ConfigRepository.set(conn, "k1", "old")
        ConfigRepository.bulk_set(conn, {"k1": "new", "k2": "v2"})
        assert ConfigRepository.get(conn, "k1") == "new"
        assert ConfigRepository.get(conn, "k2") == "v2"


class TestConfigRepositoryDelete:
    """ConfigRepository.delete 测试。"""

    def test_delete_existing(self, conn):
        """删除已有配置项。"""
        ConfigRepository.set(conn, "key1", "value1")
        ConfigRepository.delete(conn, "key1")
        assert ConfigRepository.get(conn, "key1") is None

    def test_delete_nonexistent(self, conn):
        """删除不存在的配置项不报错。"""
        ConfigRepository.delete(conn, "nonexistent")  # 不应抛异常


# ── UserRepository ──────────────────────────────────────────────


class TestUserRepositoryCreate:
    """UserRepository.create 测试。"""

    def test_create_returns_id(self, conn):
        """创建用户返回自增 ID。"""
        uid = UserRepository.create(conn, username="alice", password_hash="h1")
        assert uid == 1

    def test_create_multiple(self, conn):
        """创建多个用户 ID 递增。"""
        id1 = UserRepository.create(conn, username="alice", password_hash="h1")
        id2 = UserRepository.create(conn, username="bob", password_hash="h2")
        assert id2 == id1 + 1


class TestUserRepositoryGet:
    """UserRepository.get_by_username / get_by_id 测试。"""

    def _create_user(self, conn):
        return UserRepository.create(conn, username="alice", password_hash="hash123", is_active=1)

    def test_get_by_username_found(self, conn):
        """按用户名查找用户。"""
        self._create_user(conn)
        row = UserRepository.get_by_username(conn, "alice")
        assert row is not None
        assert row.username == "alice"
        assert row.password_hash == "hash123"

    def test_get_by_username_not_found(self, conn):
        """按用户名查找不存在的用户返回 None。"""
        assert UserRepository.get_by_username(conn, "nobody") is None

    def test_get_by_id_found(self, conn):
        """按 ID 查找用户。"""
        uid = self._create_user(conn)
        row = UserRepository.get_by_id(conn, uid)
        assert row is not None
        assert row.username == "alice"

    def test_get_by_id_not_found(self, conn):
        """按 ID 查找不存在的用户返回 None。"""
        assert UserRepository.get_by_id(conn, 9999) is None


class TestUserRepositoryUpdatePassword:
    """UserRepository.update_password 测试。"""

    def test_update_password_success(self, conn):
        """更新密码成功。"""
        UserRepository.create(conn, username="alice", password_hash="old_hash")
        result = UserRepository.update_password(conn, username="alice", password_hash="new_hash")
        assert result is True
        row = UserRepository.get_by_username(conn, "alice")
        assert row.password_hash == "new_hash"

    def test_update_password_nonexistent(self, conn):
        """更新不存在用户的密码返回 False。"""
        result = UserRepository.update_password(conn, username="nobody", password_hash="hash")
        assert result is False


class TestUserRepositoryEnsureDefault:
    """UserRepository.ensure_default 测试。"""

    def test_ensure_default_creates(self, conn):
        """默认用户不存在时创建。"""
        UserRepository.ensure_default(conn, username="admin", password_hash="default_hash")
        row = UserRepository.get_by_username(conn, "admin")
        assert row is not None
        assert row.username == "admin"

    def test_ensure_default_idempotent(self, conn):
        """默认用户已存在时不重复创建。"""
        UserRepository.create(conn, username="admin", password_hash="original")
        UserRepository.ensure_default(conn, username="admin", password_hash="should_not_overwrite")
        row = UserRepository.get_by_username(conn, "admin")
        # ensure_default 不应覆盖已有用户
        assert row.password_hash == "original"


class TestUserRepositoryCleanupOldData:
    """UserRepository.cleanup_old_data 测试。"""

    def test_cleanup_deletes_old_alerts(self, conn):
        """清理旧告警数据。"""
        # 插入"旧"数据（时间戳在未来，cleanup 使用 utcnow，所以用过去时间）
        AlertRepository.insert(
            conn, time="2020-01-01 00:00:00", level="high",
            attack_type="ddos", message="old alert",
        )
        AlertRepository.insert(
            conn, time="2099-01-01 00:00:00", level="high",
            attack_type="ddos", message="future alert",
        )
        alerts_deleted, traffic_deleted = UserRepository.cleanup_old_data(conn, days=30)
        assert alerts_deleted >= 1
        remaining = AlertRepository.count(conn)
        assert remaining == 1  # 未来记录保留