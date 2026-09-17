"""T1.10 Alembic 迁移链一致性测试。

## 为什么不用 `pytest-alembic`

`pyproject.toml` 声明了 `pytest-alembic`，但该包当前**未安装**（`requirements.txt`
里那行还被注释掉了 —— 这就是报告里说的"双份漂移"）。与其为门禁加一个运行时依赖，
不如把最关键的断言自己写扎实：

- `pytest-alembic` 的 `test_model_definitions_match_ddl` 比的是
  "metadata 与迁移链是否一致"；
- 本文件的 `test_upgrade_schema_matches_create_all` 直接**各建一个真库**，
  用 SQLAlchemy Inspector 逐表、逐列、逐索引比对 —— 判据更强，
  且零额外依赖。

两份依赖声明已对齐（requirements.txt / pyproject.toml 均声明 `pytest-alembic`），
装了它也能用，但本门禁不依赖它。

## 这些测试在防什么

`runtime/db.py` 的 `metadata` 是表定义的**唯一来源**，迁移脚本必须与之同步。
一旦有人只改了 `db.py` 没写迁移（本项目最常见的漂移方式），
`init_db()` 建出的库与 `alembic upgrade head` 建出的库就会分叉 ——
生产用迁移、测试用 create_all，两边行为不一致且很难发现。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from campus_ids.runtime.db import metadata
from tests.conftest import PROJECT_ROOT

ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

# alembic 自己维护的表，不参与业务 schema 比对
ALEMBIC_INTERNAL_TABLES = {"alembic_version"}


def _alembic_config(db_path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _normalize_default(raw) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    # SQLite 会把 DDL 里的默认值原样存下来，两侧可能差一层括号
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    return text.upper()


def schema_snapshot(engine) -> dict:
    """抽取可比较的 schema 快照（表 / 列 / 索引 / 唯一约束 / 主键）。"""
    inspector = sa.inspect(engine)
    snapshot: dict = {}
    for table in sorted(inspector.get_table_names()):
        if table in ALEMBIC_INTERNAL_TABLES:
            continue
        columns = {
            col["name"]: {
                "type": str(col["type"]),
                "nullable": bool(col["nullable"]),
                "default": _normalize_default(col.get("default")),
                "primary_key": bool(col.get("primary_key")),
            }
            for col in inspector.get_columns(table)
        }
        indexes = sorted(
            (idx["name"], tuple(idx.get("column_names") or []), bool(idx.get("unique")))
            for idx in inspector.get_indexes(table)
        )
        unique = sorted(
            (uq["name"], tuple(uq.get("column_names") or []))
            for uq in inspector.get_unique_constraints(table)
        )
        snapshot[table] = {
            "columns": columns,
            "indexes": indexes,
            "unique_constraints": unique,
            "primary_key": tuple(inspector.get_pk_constraint(table).get("constrained_columns") or []),
        }
    return snapshot


class TestMigratedSchemaMatchesMetadata:
    def test_upgrade_schema_matches_create_all(self, tmp_path):
        """核心判据：迁移建库 == create_all 建库（表/列/索引/唯一约束/主键）。"""
        # A) init_db() 走的路径
        create_all_db = tmp_path / "create_all.db"
        engine_create_all = sa.create_engine(f"sqlite:///{create_all_db}")
        metadata.create_all(engine_create_all)
        snapshot_create_all = schema_snapshot(engine_create_all)
        engine_create_all.dispose()

        # B) alembic upgrade head 走的路径
        alembic_db = tmp_path / "alembic.db"
        command.upgrade(_alembic_config(alembic_db), "head")
        engine_alembic = sa.create_engine(f"sqlite:///{alembic_db}")
        snapshot_alembic = schema_snapshot(engine_alembic)
        engine_alembic.dispose()

        assert snapshot_alembic, "alembic 迁移未建出任何表"
        assert set(snapshot_alembic) == set(snapshot_create_all), (
            "迁移与 metadata 的表集合不一致："
            f"仅迁移有 {sorted(set(snapshot_alembic) - set(snapshot_create_all))}，"
            f"仅 metadata 有 {sorted(set(snapshot_create_all) - set(snapshot_alembic))}"
        )
        for table in snapshot_create_all:
            assert snapshot_alembic[table] == snapshot_create_all[table], (
                f"表 {table} 的 schema 不一致：\n"
                f"  create_all: {snapshot_create_all[table]}\n"
                f"  alembic   : {snapshot_alembic[table]}"
            )

    def test_upgrade_then_downgrade_is_clean(self, tmp_path):
        """downgrade base 应把业务表全部清掉（保证 downgrade 不是空实现）。"""
        db_path = tmp_path / "roundtrip.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, "head")

        engine = sa.create_engine(f"sqlite:///{db_path}")
        assert set(schema_snapshot(engine)), "upgrade 后没有表"
        engine.dispose()

        command.downgrade(cfg, "base")
        engine = sa.create_engine(f"sqlite:///{db_path}")
        remaining = {t for t in sa.inspect(engine).get_table_names()
                     if t not in ALEMBIC_INTERNAL_TABLES}
        engine.dispose()
        assert remaining == set(), f"downgrade base 后仍残留表：{remaining}"

    def test_upgrade_head_is_idempotent(self, tmp_path):
        """重复 upgrade head 不应报错，也不应改变 schema。"""
        db_path = tmp_path / "idempotent.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, "head")

        engine = sa.create_engine(f"sqlite:///{db_path}")
        first = schema_snapshot(engine)
        engine.dispose()

        command.upgrade(cfg, "head")

        engine = sa.create_engine(f"sqlite:///{db_path}")
        second = schema_snapshot(engine)
        engine.dispose()
        assert first == second


class TestRevisionGraph:
    def test_single_head(self):
        """迁移链只能有一个 head —— 多 head 会让 upgrade head 语义不确定。"""
        script = ScriptDirectory(str(MIGRATIONS_DIR))
        heads = script.get_heads()
        assert len(heads) == 1, f"存在多个 head：{heads}"

    def test_initial_revision_has_no_parent(self):
        script = ScriptDirectory(str(MIGRATIONS_DIR))
        head = script.get_heads()[0]
        revision = script.get_revision(head)
        assert revision.down_revision is None, "基线迁移不应有父版本"

    def test_all_revisions_are_importable(self):
        """每个版本文件都要能加载（防止语法错误等到生产迁移时才炸）。"""
        script = ScriptDirectory(str(MIGRATIONS_DIR))
        revisions = list(script.walk_revisions())
        assert revisions, "没有找到任何迁移版本"
        for rev in revisions:
            assert rev.module is not None
