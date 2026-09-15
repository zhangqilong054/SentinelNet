"""M2: SQLite 数据持久化层 — 告警、流量历史、配置、模型注册。

替代内存列表（alert_history / traffic_history），重启后数据不丢失。
使用 Python 标准库 sqlite3，零额外依赖。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Optional

from campus_ids.config import DATA_DIR

logger = logging.getLogger(__name__)

# 数据库文件路径
DB_PATH = DATA_DIR / "sentinelnet.db"

# 数据保留天数
RETENTION_DAYS = 7

# 线程局部连接（SQLite 不支持跨线程共享连接）
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """获取当前线程的数据库连接（懒创建）。"""
    if not hasattr(_local, 'conn') or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH), timeout=10)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


# ── 建表 ────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,
    level TEXT NOT NULL,
    attack_type TEXT NOT NULL,
    message TEXT NOT NULL,
    ml_confidence REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS traffic_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,
    qps INTEGER,
    connections INTEGER,
    packet_count INTEGER,
    port_count INTEGER,
    src_ip_count INTEGER,
    alert TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts(time);
CREATE INDEX IF NOT EXISTS idx_alerts_level ON alerts(level);
CREATE INDEX IF NOT EXISTS idx_traffic_time ON traffic_history(time);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
"""


def init_db() -> None:
    """初始化数据库（创建表和索引）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _get_conn()
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    logger.info("数据库初始化完成: %s", DB_PATH)


# ── 告警 CRUD ───────────────────────────────────────────────────

def insert_alert(time: str, level: str, attack_type: str,
                 message: str, ml_confidence: float = 0.0) -> int:
    """插入一条告警记录，返回行 ID。"""
    conn = _get_conn()
    cursor = conn.execute(
        "INSERT INTO alerts (time, level, attack_type, message, ml_confidence) VALUES (?, ?, ?, ?, ?)",
        (time, level, attack_type, message, ml_confidence),
    )
    conn.commit()
    return cursor.lastrowid


def query_alerts(limit: int = 50, level: Optional[str] = None,
                 offset: int = 0) -> list[dict]:
    """查询告警记录，支持按级别筛选和分页。"""
    conn = _get_conn()
    if level and level != "all":
        rows = conn.execute(
            "SELECT * FROM alerts WHERE level = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (level, limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


def count_alerts(level: Optional[str] = None) -> int:
    """统计告警数量。"""
    conn = _get_conn()
    if level and level != "all":
        row = conn.execute("SELECT COUNT(*) FROM alerts WHERE level = ?", (level,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()
    return row[0] if row else 0


def get_alert_type_distribution() -> dict[str, int]:
    """获取告警的攻击类型分布（替代内存列表遍历）。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT attack_type, COUNT(*) as cnt FROM alerts GROUP BY attack_type"
    ).fetchall()
    return {row["attack_type"]: row["cnt"] for row in rows}


# ── 流量历史 CRUD ───────────────────────────────────────────────

def insert_traffic(time: str, qps: int, connections: int,
                   packet_count: int, port_count: int,
                   src_ip_count: int, alert: Optional[str] = None) -> int:
    """插入一条流量历史记录。"""
    conn = _get_conn()
    cursor = conn.execute(
        "INSERT INTO traffic_history (time, qps, connections, packet_count, port_count, src_ip_count, alert) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (time, qps, connections, packet_count, port_count, src_ip_count, alert),
    )
    conn.commit()
    return cursor.lastrowid


def query_traffic(limit: int = 60, offset: int = 0) -> list[dict]:
    """查询流量历史记录。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM traffic_history ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


# ── 配置 CRUD ───────────────────────────────────────────────────

def get_all_config() -> dict[str, str]:
    """获取所有配置。"""
    conn = _get_conn()
    rows = conn.execute("SELECT key, value FROM config").fetchall()
    return {row["key"]: row["value"] for row in rows}


def bulk_set_config(pairs: dict[str, str]) -> None:
    """批量设置配置。"""
    conn = _get_conn()
    conn.executemany(
        "INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
        [(k, v, v) for k, v in pairs.items()],
    )
    conn.commit()


# ── 数据清理 ────────────────────────────────────────────────────

def cleanup_old_data(days: int = RETENTION_DAYS) -> dict[str, int]:
    """清理超过指定天数的历史数据，返回各表删除行数。"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    alerts_deleted = conn.execute("DELETE FROM alerts WHERE time < ?", (cutoff,)).rowcount
    traffic_deleted = conn.execute("DELETE FROM traffic_history WHERE time < ?", (cutoff,)).rowcount
    conn.commit()
    logger.info("数据清理完成: 告警删除 %d 条, 流量删除 %d 条 (保留 %d 天)",
                alerts_deleted, traffic_deleted, days)
    return {"alerts_deleted": alerts_deleted, "traffic_deleted": traffic_deleted}


# ── 用户 CRUD（M4: Flask-Login 认证） ─────────────────────────────

def get_user_by_username(username: str) -> Optional[dict]:
    """根据用户名查询用户，返回 dict 或 None。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, username, password_hash, is_active, created_at FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[dict]:
    """根据 ID 查询用户，返回 dict 或 None。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, username, password_hash, is_active, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def create_user(username: str, password_hash: str) -> int:
    """创建用户，返回行 ID。"""
    conn = _get_conn()
    cursor = conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, password_hash),
    )
    conn.commit()
    return cursor.lastrowid


def ensure_default_user() -> None:
    """确保默认管理员用户存在（首次启动时自动创建）。"""
    existing = get_user_by_username("admin")
    if existing is None:
        # 默认密码: admin（应在首次登录后修改）
        import hashlib
        default_hash = hashlib.sha256("admin".encode()).hexdigest()
        create_user("admin", default_hash)
        logger.info("已创建默认管理员用户 admin/admin，请尽快修改密码")