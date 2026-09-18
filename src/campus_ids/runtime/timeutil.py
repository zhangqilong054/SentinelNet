"""runtime/timeutil.py — 统一时间戳工具。

R-13: 消除 5 处独立书写的时间格式化字面量，统一时区语义。
所有入库/展示时间统一使用本地时间（与既有数据保持一致），
修复 repositories.py cutoff 因 utcnow() 与入库时间时区不一致
导致的潜在多删 bug。
"""
from __future__ import annotations

from datetime import datetime, timedelta

_DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


def now_str() -> str:
    """返回当前本地时间的标准格式字符串（与既有入库数据保持同一时区语义）。"""
    return datetime.now().strftime(_DATETIME_FMT)


def fmt_datetime(dt: datetime) -> str:
    """将 datetime 对象格式化为标准字符串。"""
    return dt.strftime(_DATETIME_FMT)


def cutoff_str(days: int) -> str:
    """返回 days 天前本地时间的标准格式字符串（用于数据清理 cutoff）。"""
    return (datetime.now() - timedelta(days=days)).strftime(_DATETIME_FMT)