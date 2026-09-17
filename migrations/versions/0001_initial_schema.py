"""initial schema

基线迁移：与 `campus_ids/runtime/db.py` 的 `metadata` 完全一致
（alerts / traffic_history / config / users + 4 个显式索引）。

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-17

⚠️ **本文件必须与 `runtime/db.py` 保持同步**。
两边漂移会被 `tests/test_migrations.py::test_upgrade_schema_matches_create_all`
抓到 —— 它分别用 `create_all` 和 `alembic upgrade head` 建两个库，
再逐表逐列逐索引比对。

类型注意：`ml_confidence` 用 `sa.REAL()` 而不是 `sa.Float()` ——
`runtime/db.py` 用的是 `sqlalchemy.REAL`，SQLite 会把声明类型原样写进
`PRAGMA table_info`，用 Float 会渲染成 `FLOAT` 从而比对失败（不是等价类型）。

`created_at` 的默认值用 `sa.text("CURRENT_TIMESTAMP")` 而非裸字符串 ——
裸字符串在 SQLAlchemy 里是**字面量**，会渲染成 `DEFAULT 'CURRENT_TIMESTAMP'`。
`runtime/db.py` 原本就是写成了裸字符串（三张表），
由 `tests/test_migrations.py` 的双库比对发现，已于 2026-09-17 一并修正。
⚠️ 既有库中 `alerts` / `traffic_history` / `users` 的历史行该列仍是文本
`'CURRENT_TIMESTAMP'`，修复历史数据需要单独的数据迁移（未擅自执行）。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("time", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("attack_type", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("ml_confidence", sa.REAL(), server_default=None),
        sa.Column("created_at", sa.Text(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_alerts_time", "alerts", ["time"])
    op.create_index("idx_alerts_level", "alerts", ["level"])

    op.create_table(
        "traffic_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("time", sa.Text(), nullable=False),
        sa.Column("qps", sa.Integer()),
        sa.Column("connections", sa.Integer()),
        sa.Column("packet_count", sa.Integer()),
        sa.Column("port_count", sa.Integer()),
        sa.Column("src_ip_count", sa.Integer()),
        sa.Column("alert", sa.Text()),
        sa.Column("created_at", sa.Text(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_traffic_time", "traffic_history", ["time"])

    op.create_table(
        "config",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Integer()),
        sa.Column("created_at", sa.Text(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_users_username", "users", ["username"])


def downgrade() -> None:
    op.drop_index("idx_users_username", table_name="users")
    op.drop_table("users")
    op.drop_table("config")
    op.drop_index("idx_traffic_time", table_name="traffic_history")
    op.drop_table("traffic_history")
    op.drop_index("idx_alerts_level", table_name="alerts")
    op.drop_index("idx_alerts_time", table_name="alerts")
    op.drop_table("alerts")
