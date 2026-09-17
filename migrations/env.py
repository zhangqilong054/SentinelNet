"""Alembic 迁移环境（T1.10）。

三点设计说明：

1. **target_metadata 直接复用 `runtime/db.py` 的 `metadata`** ——
   迁移与 `init_db()` 共用一个表定义来源，两者不可能漂移。
   （`tests/test_migrations.py` 会实际建两个库逐列比对来守着这一点。）

2. **URL 默认从 `Settings.data_dir` 推导**，而不是写在 `alembic.ini` 里 ——
   避免"配置里的库"和"应用实际用的库"两套真相。
   优先级：`-x db_url=...` > `sqlalchemy.url`（调用方 set_main_option）> Settings。

3. **不调用 `logging.config.fileConfig`** —— Alembic 模板默认会调，但它会重置 root
   logger 的 handler。本项目的测试会在**进程内**调 `alembic upgrade`，
   一旦重置就会破坏其他测试与应用的 JSON 日志输出。
   迁移脚本自身用 `logging.getLogger(...)` 输出即可。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

logger = logging.getLogger("alembic.env")

# 保证 `src/` 在 import 路径上（prepend_sys_path 对程序化调用不一定生效）
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from campus_ids.runtime.db import metadata  # noqa: E402  （必须在 sys.path 处理之后）

config = context.config
target_metadata = metadata


def _resolve_url() -> str:
    """按优先级解析数据库 URL。"""
    # 1) -x db_url=...
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("db_url"):
        return x_args["db_url"]

    # 2) 调用方通过 config.set_main_option 显式设置
    configured = config.get_main_option("sqlalchemy.url")
    if configured and configured.strip():
        return configured.strip()

    # 3) 从应用 Settings 推导（与运行时同一个目录来源）
    from campus_ids.runtime.settings import get_settings

    db_path = get_settings().data_dir / "sentinelnet.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库。"""
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),  # SQLite 改列需 batch 模式
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连库执行迁移。"""
    url = _resolve_url()
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = url

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=url.startswith("sqlite"),
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
