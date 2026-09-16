"""runtime/repositories.py — 数据访问层 Repository。

ADR-0001 §5.1: SQLAlchemy 2.0 Core 写法，不引 ORM Session。
每个 Repository 封装一个域的 CRUD，通过 Connection 执行表达式查询。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Sequence

from sqlalchemy import delete, insert, select, update, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Row

from campus_ids.runtime.db import alerts, traffic_history, config, users

logger = logging.getLogger(__name__)


# ── AlertRepository ────────────────────────────────────────────────

class AlertRepository:
    """告警数据访问。"""

    @staticmethod
    def insert(conn: Connection, *, time: str, level: str,
               attack_type: str, message: str, ml_confidence: float = 0.0) -> int:
        """插入一条告警，返回自增 ID。"""
        result = conn.execute(
            insert(alerts).values(
                time=time, level=level, attack_type=attack_type,
                message=message, ml_confidence=ml_confidence,
            )
        )
        conn.commit()
        return result.lastrowid  # type: ignore[return-value]

    @staticmethod
    def query(conn: Connection, *, level: str | None = None,
              limit: int = 20, offset: int = 0) -> Sequence[Row]:
        """查询告警列表，支持按 level 过滤。"""
        stmt = select(alerts).order_by(alerts.c.id.desc()).limit(limit).offset(offset)
        if level:
            stmt = stmt.where(alerts.c.level == level)
        return conn.execute(stmt).fetchall()

    @staticmethod
    def count(conn: Connection, *, level: str | None = None) -> int:
        """统计告警数量。"""
        stmt = select(func.count()).select_from(alerts)
        if level:
            stmt = stmt.where(alerts.c.level == level)
        return conn.execute(stmt).scalar() or 0

    @staticmethod
    def get_type_distribution(conn: Connection) -> Sequence[Row]:
        """获取告警类型分布。"""
        stmt = (
            select(alerts.c.attack_type, func.count().label("count"))
            .group_by(alerts.c.attack_type)
            .order_by(func.count().desc())
        )
        return conn.execute(stmt).fetchall()


# ── TrafficRepository ──────────────────────────────────────────────

class TrafficRepository:
    """流量数据访问。"""

    @staticmethod
    def insert(conn: Connection, *, time: str, qps: int | None = None,
               connections: int | None = None, packet_count: int | None = None,
               port_count: int | None = None, src_ip_count: int | None = None,
               alert: str | None = None) -> int:
        """插入一条流量记录，返回自增 ID。"""
        result = conn.execute(
            insert(traffic_history).values(
                time=time, qps=qps, connections=connections,
                packet_count=packet_count, port_count=port_count,
                src_ip_count=src_ip_count, alert=alert,
            )
        )
        conn.commit()
        return result.lastrowid  # type: ignore[return-value]

    @staticmethod
    def query(conn: Connection, *, limit: int = 60, offset: int = 0) -> Sequence[Row]:
        """查询流量历史。"""
        stmt = (
            select(traffic_history)
            .order_by(traffic_history.c.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return conn.execute(stmt).fetchall()

    @staticmethod
    def count(conn: Connection) -> int:
        """统计流量记录总数。"""
        stmt = select(func.count()).select_from(traffic_history)
        return conn.execute(stmt).scalar() or 0


# ── ConfigRepository ───────────────────────────────────────────────

class ConfigRepository:
    """配置数据访问。"""

    @staticmethod
    def get_all(conn: Connection) -> dict[str, str]:
        """获取所有配置项。"""
        rows = conn.execute(select(config)).fetchall()
        return {row.key: row.value for row in rows}

    @staticmethod
    def get(conn: Connection, key: str) -> str | None:
        """获取单个配置项。"""
        stmt = select(config.c.value).where(config.c.key == key)
        return conn.execute(stmt).scalar()

    @staticmethod
    def set(conn: Connection, key: str, value: str) -> None:
        """设置单个配置项（INSERT OR REPLACE）。"""
        stmt = sqlite_insert(config).values(key=key, value=value)
        stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value})
        conn.execute(stmt)
        conn.commit()

    @staticmethod
    def bulk_set(conn: Connection, items: dict[str, str]) -> None:
        """批量设置配置项。"""
        for key, value in items.items():
            stmt = sqlite_insert(config).values(key=key, value=value)
            stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value})
            conn.execute(stmt)
        conn.commit()

    @staticmethod
    def delete(conn: Connection, key: str) -> None:
        """删除单个配置项。"""
        conn.execute(delete(config).where(config.c.key == key))
        conn.commit()


# ── UserRepository ─────────────────────────────────────────────────

class UserRepository:
    """用户数据访问。"""

    @staticmethod
    def get_by_username(conn: Connection, username: str) -> Row | None:
        """按用户名查找用户。"""
        stmt = select(users).where(users.c.username == username)
        return conn.execute(stmt).fetchone()

    @staticmethod
    def get_by_id(conn: Connection, user_id: int) -> Row | None:
        """按 ID 查找用户。"""
        stmt = select(users).where(users.c.id == user_id)
        return conn.execute(stmt).fetchone()

    @staticmethod
    def create(conn: Connection, *, username: str, password_hash: str,
               is_active: int = 1) -> int:
        """创建用户，返回自增 ID。"""
        result = conn.execute(
            insert(users).values(
                username=username, password_hash=password_hash, is_active=is_active,
            )
        )
        conn.commit()
        return result.lastrowid  # type: ignore[return-value]

    @staticmethod
    def update_password(conn: Connection, *, username: str,
                        password_hash: str) -> bool:
        """更新用户密码，返回是否成功。"""
        stmt = (
            update(users)
            .where(users.c.username == username)
            .values(password_hash=password_hash)
        )
        result = conn.execute(stmt)
        conn.commit()
        return result.rowcount > 0

    @staticmethod
    def ensure_default(conn: Connection, *, username: str = "admin",
                       password_hash: str = "") -> None:
        """确保默认用户存在（不存在则创建）。"""
        existing = UserRepository.get_by_username(conn, username)
        if existing is None:
            UserRepository.create(
                conn, username=username, password_hash=password_hash
            )
            logger.info("默认用户 %s 已创建", username)

    @staticmethod
    def cleanup_old_data(conn: Connection, *, days: int = 30) -> int:
        """清理超过 days 天的旧数据（alerts + traffic_history）。

        返回删除的行数。
        """
        cutoff = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        # SQLite 字符串比较可直接用于时间戳
        r1 = conn.execute(delete(alerts).where(alerts.c.time < cutoff))
        r2 = conn.execute(delete(traffic_history).where(traffic_history.c.time < cutoff))
        conn.commit()
        return (r1.rowcount or 0) + (r2.rowcount or 0)