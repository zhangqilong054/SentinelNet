"""web_new/cli.py — 命令行辅助子命令（2026-09-19，可用性 P0-U2）。

当前仅提供 `reset-password`：管理员密码找回的唯一入口。
背景：users 表中 admin 的历史密码一旦遗失（操作手册承诺的默认密码
admin/admin 与真实库不符），应用没有任何自助重置手段 → 完全锁死。
"""
from __future__ import annotations

import argparse
import secrets
import sys

from campus_ids.logging_config import setup_logging

setup_logging()

import logging  # noqa: E402  （须在 setup_logging 之后）

logger = logging.getLogger(__name__)


def reset_password(argv: list[str] | None = None) -> int:
    """`python main.py reset-password [--user admin] [--password X]` 入口。

    - 未指定 --password 时生成随机密码（token_urlsafe(12)，仅 stdout 打印一次）；
    - 用户不存在时直接创建（等价于首次建管理员）；
    - 明文密码**只打印到 stdout 一次**，不落日志、不落库明文。

    Returns:
        0 成功；2 参数或执行失败。
    """
    parser = argparse.ArgumentParser(
        prog="python main.py reset-password",
        description="重置（或创建）Web 面板管理员密码。",
    )
    parser.add_argument(
        "--user", default="admin", help="用户名（默认 admin）",
    )
    parser.add_argument(
        "--password", default=None,
        help="新密码；省略则自动生成随机密码（推荐）",
    )
    ns = parser.parse_args(list(argv) if argv is not None else None)

    username = ns.user.strip()
    if not username:
        print("错误：用户名不能为空", file=sys.stderr)
        return 2

    password: str = ns.password if ns.password else secrets.token_urlsafe(12)
    if not password.strip():
        print("错误：密码不能为空", file=sys.stderr)
        return 2
    if len(password) < 6:
        print("错误：密码长度至少 6 位", file=sys.stderr)
        return 2

    from campus_ids.runtime.db import get_connection, init_db
    from campus_ids.runtime.repositories import UserRepository
    from campus_ids.web_new.auth import hash_password

    try:
        # 全新数据目录（目录不存在 / 空库）也要能用：先建目录与表，
        # 否则首次部署 + 密码找回会在 sqlite 连接期报 unable to open database file。
        init_db()
        with get_connection() as conn:
            existed = UserRepository.get_by_username(conn, username) is not None
            if existed:
                ok = UserRepository.update_password(
                    conn, username=username,
                    password_hash=hash_password(password),
                )
            else:
                UserRepository.create(
                    conn, username=username,
                    password_hash=hash_password(password),
                )
                ok = True
    except Exception as exc:  # noqa: BLE001 —— CLI 层兜底，给出可读错误
        print(f"错误：写入数据库失败：{exc}", file=sys.stderr)
        return 2

    if not ok:
        print(f"错误：用户 {username} 密码更新失败", file=sys.stderr)
        return 2

    action = "已重置" if existed else "已创建"
    print(f"用户 {username} 密码{action}。")
    print(f"新密码: {password}")
    print("（明文仅此一次显示，请妥善保存；也可再次运行本命令重新生成）")
    logger.info("用户 %s 的密码已通过 CLI 重置", username)
    return 0
