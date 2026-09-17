"""web_new/deprecation.py — 旧端点命中追踪（T4.2）。

记录每个旧端点的调用次数与来源，作为删除依据（T4.5）。
数据驻留进程内，可通过 GET /api/admin/deprecation-stats 查询。

设计决策：
- 进程内字典，不引 Redis/SQLite（ADR §7 约束）
- 线程安全：用 threading.Lock 保护写操作
- 轻量：只记路径+次数+最近来源，不记请求体（避免内存膨胀）
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HitRecord:
    """单个旧端点的命中记录。"""
    path: str
    method: str
    count: int = 0
    last_source: str = ""       # 最近调用来源（IP 或 User-Agent 摘要）
    last_hit_at: float = 0.0    # 最近命中时间戳


class DeprecationTracker:
    """旧端点命中追踪器（T4.2 / T4.5）。

    用法：
        tracker = DeprecationTracker()
        tracker.record_hit("/api/capture/start", "POST", source="192.168.1.1")
        stats = tracker.get_stats()
    """

    def __init__(self) -> None:
        self._records: dict[str, HitRecord] = {}
        self._lock = threading.Lock()

    def record_hit(self, path: str, method: str, source: str = "") -> None:
        """记录一次旧端点命中。

        Args:
            path: 旧端点路径（如 /api/capture/start）
            method: HTTP 方法（GET/POST）
            source: 调用来源（IP 或 User-Agent 摘要）
        """
        key = f"{method} {path}"
        with self._lock:
            if key not in self._records:
                self._records[key] = HitRecord(path=path, method=method)
            rec = self._records[key]
            rec.count += 1
            rec.last_source = source
            rec.last_hit_at = time.time()

    def get_stats(self) -> list[dict[str, Any]]:
        """获取所有旧端点的命中统计。

        Returns:
            按命中次数降序排列的记录列表，每条含：
            path, method, count, last_source, last_hit_at
        """
        with self._lock:
            records = sorted(
                self._records.values(),
                key=lambda r: r.count,
                reverse=True,
            )
            return [
                {
                    "path": r.path,
                    "method": r.method,
                    "count": r.count,
                    "last_source": r.last_source,
                    "last_hit_at": r.last_hit_at,
                }
                for r in records
            ]

    def get_hit_count(self, path: str, method: str) -> int:
        """获取指定旧端点的命中次数。"""
        key = f"{method} {path}"
        with self._lock:
            return self._records.get(key, HitRecord(path=path, method=method)).count

    def get_deletable_endpoints(self, min_idle_days: float = 30.0) -> list[dict[str, Any]]:
        """返回可安全删除的旧端点列表（T4.5 删除判定）。

        判定标准：
        1. 命中数为 0，或
        2. 最近命中时间超过 min_idle_days 天

        Args:
            min_idle_days: 最近命中距今超过此天数视为可删除（默认 30 天）
        """
        now = time.time()
        idle_seconds = min_idle_days * 86400
        with self._lock:
            result = []
            for r in self._records.values():
                if r.count == 0:
                    result.append({
                        "path": r.path,
                        "method": r.method,
                        "count": r.count,
                        "reason": "zero_hits",
                        "deletable": True,
                    })
                elif r.last_hit_at > 0 and (now - r.last_hit_at) > idle_seconds:
                    result.append({
                        "path": r.path,
                        "method": r.method,
                        "count": r.count,
                        "last_hit_at": r.last_hit_at,
                        "idle_days": round((now - r.last_hit_at) / 86400, 1),
                        "reason": "idle_expired",
                        "deletable": True,
                    })
            return result

    def reset(self) -> None:
        """重置所有记录（测试用）。"""
        with self._lock:
            self._records.clear()


# 全局单例（进程内唯一）
_tracker: DeprecationTracker | None = None
_tracker_lock = threading.Lock()


def get_deprecation_tracker() -> DeprecationTracker:
    """获取全局 DeprecationTracker 单例。"""
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = DeprecationTracker()
    return _tracker


def reset_deprecation_tracker() -> None:
    """重置全局追踪器（测试用）。"""
    global _tracker
    with _tracker_lock:
        if _tracker is not None:
            _tracker.reset()
        _tracker = None


# ── Sunset 日期 ──────────────────────────────────────────────
# D2: 旧端点保留 1 个发布版本。Sunset 日期设为下一版本发布后 30 天。
# 当前版本 0.2.0 → Sunset 日期设为 2026-11-01（预留足够迁移窗口）。
SUNSET_DATE = "2026-11-01"

# Deprecation 响应头值
DEPRECATION_HEADER = "true"