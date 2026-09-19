"""runtime/db.py — SQLAlchemy Core 数据库层。

ADR-0001 §5.1: 使用 SQLAlchemy 2.0 Core 写法，不引 ORM Session。
表定义用 Table + metadata，查询用 Core 表达式，连接用 Connection。

本模块 import 不产生 I/O 副作用（不建库、不连库）。
实际连接在 create_app() 中通过 init_db() 显式建立。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import (
    Column, Integer, REAL, Text, MetaData, Table, create_engine, Index, text,
)
from sqlalchemy.engine import Connection, Engine

from campus_ids.runtime.settings import get_settings

logger = logging.getLogger(__name__)

# ── 元数据 ────────────────────────────────────────────────────────
#
# ⚠️ `created_at` 的默认值必须写成 `text("CURRENT_TIMESTAMP")`，**不能**写裸字符串
# `"CURRENT_TIMESTAMP"` —— 裸字符串会被 SQLAlchemy 当作**字面量**并在 DDL 里加引号，
# 生成 `DEFAULT 'CURRENT_TIMESTAMP'`，于是不传该字段时存进去的是**文本**
# `'CURRENT_TIMESTAMP'` 而不是时间戳。
# 本项目 2026-09-17 由 `tests/test_migrations.py` 的
# create_all × alembic 双库比对发现并修正（此前 `alerts` / `traffic_history` /
# `users` 三张表的该列全是废值）。
# ⚠️ **既有数据库的历史行仍是那串文本**，修正只对新库与新增行生效；
# 历史废值已由迁移 `0002_repair_created_at_garbage`（2026-09-19）收敛：
# alerts/traffic_history 复用业务时间列 `time`，users 用迁移执行时刻。
metadata = MetaData()

# ── 表定义 ────────────────────────────────────────────────────────

alerts = Table(
    "alerts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("time", Text, nullable=False),
    Column("level", Text, nullable=False),
    Column("attack_type", Text, nullable=False),
    Column("message", Text, nullable=False),
    Column("ml_confidence", REAL, default=0.0),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
)

traffic_history = Table(
    "traffic_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("time", Text, nullable=False),
    Column("qps", Integer),
    Column("connections", Integer),
    Column("packet_count", Integer),
    Column("port_count", Integer),
    Column("src_ip_count", Integer),
    Column("alert", Text),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
)

config = Table(
    "config",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
)

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", Text, unique=True, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("is_active", Integer, default=1),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
)

# ── 索引 ──────────────────────────────────────────────────────────
Index("idx_alerts_time", alerts.c.time)
Index("idx_alerts_level", alerts.c.level)
Index("idx_traffic_time", traffic_history.c.time)
Index("idx_users_username", users.c.username)

# ── 引擎管理 ──────────────────────────────────────────────────────

_engine: Engine | None = None


def get_engine() -> Engine:
    """获取全局引擎单例。

    首次调用时从 Settings 构建，后续调用返回同一实例。
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        db_path = settings.data_dir / "sentinelnet.db"
        db_url = f"sqlite:///{db_path}"
        _engine = create_engine(
            db_url,
            echo=False,
            connect_args={"check_same_thread": False},  # SQLite 多线程
        )
        logger.info("数据库引擎创建: %s", db_url)
    return _engine


def reset_engine() -> None:
    """重置引擎（仅用于测试）。"""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def init_db() -> None:
    """初始化数据库（创建表和索引）。

    应在 create_app() lifespan 中调用，不在 import 时执行。
    """
    engine = get_engine()
    db_path = engine.url.database
    if db_path:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # 使用 SQLAlchemy 的 create_all 创建所有表
    metadata.create_all(engine)
    # 启用 WAL 模式和外键约束
    import sqlalchemy
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("PRAGMA journal_mode=WAL"))
        conn.execute(sqlalchemy.text("PRAGMA foreign_keys=ON"))
        conn.commit()
    logger.info("数据库初始化完成: %s", db_path)


@contextmanager
def get_connection() -> Generator[Connection, None, None]:
    """获取数据库连接上下文管理器。

    用法:
        with get_connection() as conn:
            result = conn.execute(...)
            conn.commit()
    """
    engine = get_engine()
    with engine.connect() as conn:
        yield conn