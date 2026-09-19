"""Project entry point.

用法:
  python main.py app     启动 Web 面板（双引擎检测）

所有功能均通过 Web 面板操作:
  - 基础抓包:     控制面板 → 实时抓包控制
  - 增强抓包:     控制面板 → 增强抓包（18维流特征 + TLS 分析）
  - 模型训练:     控制面板 → 模型管理
  - 入侵检测:     控制面板 → 双引擎检测
  - 一键演示:     控制面板 → 一键演示模式
  - 攻击模拟:     控制面板 → 攻击模拟控制
  - 一键全流程:   控制面板 → 一键全流程（抓包 → 训练 → 检测）
  - 环境自检:     控制面板 → 环境自检
"""

from __future__ import annotations

import sys

# 使用增强日志系统（文件输出+级别过滤+检测结果持久化）
from campus_ids.logging_config import setup_logging
setup_logging()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0].lower() in ("app", "run", "start", ""):
        from campus_ids.web_new.app import run_app
        run_app()
        return 0

    if args[0].lower() == "reset-password":
        from campus_ids.web_new.cli import reset_password
        return reset_password(args[1:])

    print(
        "用法:\n"
        "  python main.py app               启动 Web 面板\n"
        "  python main.py reset-password    重置/创建管理员密码（--user admin）\n\n"
        "其余功能均通过 Web 面板操作，无需额外命令行参数。\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())