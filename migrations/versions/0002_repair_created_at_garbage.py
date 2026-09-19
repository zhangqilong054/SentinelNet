"""repair created_at literal garbage rows

数据修复迁移（2026-09-19）：修复 2026-09-17 之前由裸字符串
`server_default="CURRENT_TIMESTAMP"` 写入的历史废值行 —— 该列存的是
**文本** `'CURRENT_TIMESTAMP'` 而不是时间戳（缺陷本身已于 2026-09-17 修复，
见 `runtime/db.py` 头注与 `0001_initial_schema.py`）。

修复策略（尽力而为，原始时间已不可考）：
- `alerts` / `traffic_history`：有业务时间列 `time`，废值行改为复用
  `time` 的值（同为 Text、格式同族，可保住排序信息）；`time` 为空则
  回落到 CURRENT_TIMESTAMP（迁移执行时刻）。
- `users`：无业务时间列，改为 CURRENT_TIMESTAMP。

幂等：只命中 `created_at = 'CURRENT_TIMESTAMP'` 的行，重复执行无副作用。
真实库（2026-09-19 实测）三表均为 0 废值 —— 本迁移是为**任何运行过旧代码
的既有库**兜底，属于防御性收敛，不是修复本机数据。

downgrade 为 no-op：数据修复不可逆（无从得知废值行的"真实"时间）。

Revision ID: 0002_repair_created_at_garbage
Revises: 0001_initial_schema
Create Date: 2026-09-19

⚠️ alembic.ini 必须纯 ASCII（cp936 locale），中文说明只能写在本文件里。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_repair_created_at_garbage"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GARBAGE = "'CURRENT_TIMESTAMP'"  # 字面量文本，非时间戳


def upgrade() -> None:
    conn = op.get_bind()
    # alerts / traffic_history：优先复用业务时间列 time
    for table in ("alerts", "traffic_history"):
        conn.execute(
            sa.text(
                f"UPDATE {table} SET created_at = "
                f"COALESCE(NULLIF(time, ''), CURRENT_TIMESTAMP) "
                f"WHERE created_at = {GARBAGE}"
            )
        )
    # users：无业务时间列 → 迁移执行时刻
    conn.execute(
        sa.text(f"UPDATE users SET created_at = CURRENT_TIMESTAMP WHERE created_at = {GARBAGE}")
    )


def downgrade() -> None:
    # 数据修复不可逆：废值行的原始时间无从得知，降级不做任何事。
    pass
