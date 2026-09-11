"""一键演示脚本。

P0-23: 一键演示流程
    1. 启动 Web 面板
    2. 自动注入模拟攻击流量
    3. 实时展示检测效果

用法:
    python -m campus_ids.demo.run_demo
    python -m campus_ids.demo.run_demo --duration 60
    python -m campus_ids.demo.run_demo --no-attack  # 仅启动面板
    python -m campus_ids.demo.run_demo --pcap demo_attacks.pcap  # 回放 pcap
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
import webbrowser

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000
DEFAULT_DURATION = 60  # 演示持续秒数


def _start_web_panel(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                     open_browser: bool = True) -> threading.Thread:
    """启动 Web 面板并返回线程对象。"""
    from campus_ids.web.app import app

    def _run():
        app.run(host=host, port=port, debug=False, use_reloader=False)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    logger.info("Web 面板已启动: http://%s:%d", host, port)

    if open_browser:
        time.sleep(1.5)
        webbrowser.open(f"http://{host}:{port}")

    return t


def _start_attack_sim(duration: int = DEFAULT_DURATION) -> "AttackSimulator":
    """启动攻击模拟注入器。"""
    from campus_ids.demo.attack_sim import AttackSimulator

    sim = AttackSimulator()
    sim.start_all(duration=duration)
    logger.info("攻击模拟已启动（持续 %d 秒）", duration)
    return sim


def _start_pcap_replay(pcap_path: str, duration: int = DEFAULT_DURATION) -> None:
    """回放 pcap 文件中的攻击流量。"""
    from campus_ids.demo.attack_sim import load_and_replay
    from pathlib import Path

    path = Path(pcap_path)
    if not path.exists():
        logger.error("pcap 文件不存在: %s", path)
        return

    def _replay():
        load_and_replay(path, interval=0.01)
        logger.info("pcap 回放完成")

    t = threading.Thread(target=_replay, daemon=True)
    t.start()
    logger.info("pcap 回放已启动: %s", path)


def run_demo(duration: int = DEFAULT_DURATION,
             host: str = DEFAULT_HOST,
             port: int = DEFAULT_PORT,
             no_attack: bool = False,
             pcap: str | None = None,
             open_browser: bool = True) -> None:
    """一键演示主流程。"""
    print("=" * 60)
    print("  校园网入侵检测系统 — 一键演示")
    print("=" * 60)
    print(f"  演示时长: {duration} 秒")
    print(f"  Web 面板: http://{host}:{port}")
    print(f"  攻击模拟: {'关闭' if no_attack else '开启'}")
    if pcap:
        print(f"  pcap 回放: {pcap}")
    print("=" * 60)
    print()

    # 1. 启动 Web 面板
    web_thread = _start_web_panel(host, port, open_browser)

    # 2. 启动攻击模拟
    sim = None
    if not no_attack:
        time.sleep(2)  # 等待面板就绪
        if pcap:
            _start_pcap_replay(pcap, duration)
        else:
            sim = _start_attack_sim(duration)

    # 3. 等待演示完成
    print("\n[演示进行中] 按 Ctrl+C 提前结束\n")
    try:
        end_time = time.time() + duration + 5
        while time.time() < end_time:
            time.sleep(1)
            remaining = int(end_time - time.time())
            if remaining > 0 and remaining % 10 == 0:
                logger.info("演示剩余 %d 秒...", remaining)
    except KeyboardInterrupt:
        print("\n用户中断演示")

    # 4. 清理
    if sim:
        sim.stop()
    logger.info("演示结束")


def main():
    parser = argparse.ArgumentParser(description="校园网入侵检测系统 — 一键演示")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION,
                        help=f"演示时长（秒），默认 {DEFAULT_DURATION}")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"Web 面板地址，默认 {DEFAULT_HOST}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Web 面板端口，默认 {DEFAULT_PORT}")
    parser.add_argument("--no-attack", action="store_true",
                        help="不启动攻击模拟（仅展示面板）")
    parser.add_argument("--pcap", default=None,
                        help="回放指定 pcap 文件（替代内置攻击模拟）")
    parser.add_argument("--no-browser", action="store_true",
                        help="不自动打开浏览器")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    run_demo(
        duration=args.duration,
        host=args.host,
        port=args.port,
        no_attack=args.no_attack,
        pcap=args.pcap,
        open_browser=not args.no_browser,
    )


if __name__ == "__main__":
    main()