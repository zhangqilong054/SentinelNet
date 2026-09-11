"""Project entry point.

Run one of the modules explicitly instead of executing every demo at import time.

用法:
  python My_task.py capture [seconds]          基础抓包
  python My_task.py ecapture [seconds]         增强抓包（18维特征+TLS分析）
  python My_task.py train [--dataset NAME]     模型训练（支持公开数据集+多算法对比）
  python My_task.py detect                     入侵检测
  python My_task.py app                        启动 Web 面板（双引擎检测）
  python My_task.py demo [--duration N]        一键演示
  python My_task.py attack_sim <command>       攻击模拟工具

简化操作:
  python My_task.py auto [seconds]             一键全流程（抓包→训练→检测→面板）
  python My_task.py menu                       交互式菜单（数字选择）
  python My_task.py check                      环境自检（依赖/权限/模型）

P1 新增功能:
  - 双引擎检测融合（规则+ML），三级告警（高危/中危/低危）
  - 扩展检测类型：SQL注入/XSS/暴力破解/横向移动
  - 多算法对比：RandomForest/XGBoost/LightGBM/MLP + 交叉验证+ROC-AUC
  - Web面板增强：ML状态卡片/攻击类型饼图/告警颜色分级
"""

from __future__ import annotations

import logging
import sys

# P2-10.4: 使用增强日志系统（文件输出+级别过滤+检测结果持久化）
from campus_ids.logging_config import setup_logging
setup_logging()


def _usage() -> None:
    print(
        "用法:\n"
        "  python My_task.py capture [seconds]          基础抓包\n"
        "  python My_task.py ecapture [seconds]         增强抓包（18维特征+TLS分析）\n"
        "  python My_task.py train [--dataset NAME]     模型训练（多算法对比）\n"
        "  python My_task.py detect                     入侵检测\n"
        "  python My_task.py app                        启动 Web 面板（双引擎检测）\n"
        "  python My_task.py demo [--duration N]        一键演示\n"
        "  python My_task.py attack_sim <command>       攻击模拟工具\n"
        "\n"
        "简化操作:\n"
        "  python My_task.py auto [seconds]             一键全流程（抓包→训练→检测→面板）\n"
        "  python My_task.py menu                       交互式菜单（数字选择）\n"
        "  python My_task.py check                      环境自检（依赖/权限/模型）\n"
        "\n"
        "数据集选项: cicids2017, nsl_kdd, synthetic\n"
        "算法选项: RF(默认), XGBoost, LightGBM, MLP, LR\n"
    )


# ── 简化操作：环境自检 ──────────────────────────────────────────
def _cmd_check() -> int:
    """检查运行环境：Python 版本、依赖包、Npcap、模型文件等。"""
    import importlib
    import platform
    import struct

    ok = True
    print("=" * 50)
    print("  校园网 IDS — 环境自检")
    print("=" * 50)

    # 1. Python 版本
    py_ver = sys.version_info
    py_ok = py_ver >= (3, 10)
    status = "✓" if py_ok else "✗"
    print(f"  {status} Python {py_ver.major}.{py_ver.minor}.{py_ver.micro}  (需要 ≥3.10)")
    if not py_ok:
        ok = False

    # 2. 必需依赖包
    required = [
        ("scapy", "scapy"),
        ("flask", "flask"),
        ("sklearn", "scikit-learn"),
        ("joblib", "joblib"),
        ("pandas", "pandas"),
        ("numpy", "numpy"),
    ]
    optional = [
        ("xgboost", "xgboost"),
        ("lightgbm", "lightgbm"),
    ]
    print("\n  [必需依赖]")
    for mod, pkg in required:
        try:
            importlib.import_module(mod)
            print(f"    ✓ {pkg}")
        except ImportError:
            print(f"    ✗ {pkg}  (pip install {pkg})")
            ok = False

    print("  [可选依赖]")
    for mod, pkg in optional:
        try:
            importlib.import_module(mod)
            print(f"    ✓ {pkg}")
        except ImportError:
            print(f"    - {pkg}  (pip install {pkg})  — 不影响基本功能")

    # 3. Npcap / libpcap（抓包所需）
    print("\n  [抓包权限]")
    try:
        from scapy.arch import get_if_addr  # noqa: F401
        print("    ✓ Npcap/libpcap 可用")
    except Exception:
        print("    ✗ Npcap/libpcap 不可用 — 抓包功能受限，仍可使用模拟数据")
        # 不标记 ok=False，因为模拟数据仍可工作

    # 4. 模型文件
    print("\n  [模型文件]")
    from campus_ids.config import MODEL_PATH, EVALUATION_PATH
    if MODEL_PATH.exists():
        size_kb = MODEL_PATH.stat().st_size / 1024
        print(f"    ✓ {MODEL_PATH.name}  ({size_kb:.1f} KB)")
    else:
        print(f"    - {MODEL_PATH.name}  不存在 — 需先运行 train 命令")
    if EVALUATION_PATH.exists():
        print(f"    ✓ {EVALUATION_PATH.name}")
    else:
        print(f"    - {EVALUATION_PATH.name}  不存在")

    # 5. 数据文件
    print("\n  [数据文件]")
    from campus_ids.config import TRAFFIC_CSV, TRAFFIC_STATS_CSV
    for f in [TRAFFIC_CSV, TRAFFIC_STATS_CSV]:
        if f.exists():
            print(f"    ✓ {f.name}")
        else:
            print(f"    - {f.name}  不存在 — 需先运行 capture/ecapture")

    print("\n" + "=" * 50)
    if ok:
        print("  环境检查通过 ✓  可以正常运行")
    else:
        print("  环境存在问题 ✗  请根据提示安装缺失依赖")
    print("=" * 50)
    return 0 if ok else 1


# ── 简化操作：一键全流程 ────────────────────────────────────────
def _cmd_auto(duration: int = 30) -> int:
    """一键全流程：增强抓包 → 训练 → 检测 → 启动 Web 面板。"""
    from pathlib import Path
    from campus_ids.config import MODEL_PATH, TRAFFIC_CSV

    print("=" * 60)
    print("  校园网 IDS — 一键全流程 (auto)")
    print("=" * 60)
    print(f"  抓包时长: {duration} 秒")
    print("=" * 60)

    # Step 1: 增强抓包
    print("\n[1/4] 增强抓包中...")
    from campus_ids.capture.enhanced_features import start_enhanced_capture
    if not start_enhanced_capture(duration):
        print("  ✗ 抓包失败，尝试使用已有数据继续...")
        if not TRAFFIC_CSV.exists():
            print("  ✗ 无可用数据，退出")
            return 1
        print("  ✓ 发现已有数据文件，继续流程")

    # Step 2: 训练
    print("\n[2/4] 模型训练中...")
    from campus_ids.model.train import train
    try:
        train()
    except Exception as e:
        print(f"  ✗ 训练失败: {e}")
        return 1

    if not MODEL_PATH.exists():
        print("  ✗ 模型文件未生成，退出")
        return 1
    print("  ✓ 模型训练完成")

    # Step 3: 检测
    print("\n[3/4] 入侵检测中...")
    from campus_ids.model.train import predict
    result = predict()
    if result is not None:
        df, preds = result
        from collections import Counter
        counts = Counter(preds)
        print(f"  ✓ 检测完成，共 {len(preds)} 条流量：")
        for label, cnt in counts.items():
            print(f"      {label}: {cnt}")
    else:
        print("  - 无可用模型进行 ML 检测，跳过")

    # Step 4: 启动 Web 面板
    print("\n[4/4] 启动 Web 面板...")
    print("  浏览器将自动打开，按 Ctrl+C 退出\n")
    from campus_ids.web.app import run_app
    run_app()
    return 0


# ── 简化操作：交互式菜单 ────────────────────────────────────────
def _cmd_menu() -> int:
    """交互式菜单：数字选择操作，无需记忆命令。"""
    menu_items = [
        ("环境自检", "check"),
        ("基础抓包 (capture)", "capture"),
        ("增强抓包 (ecapture)", "ecapture"),
        ("模型训练 (train)", "train"),
        ("入侵检测 (detect)", "detect"),
        ("启动 Web 面板 (app)", "app"),
        ("一键演示 (demo)", "demo"),
        ("一键全流程 (auto)", "auto"),
        ("退出", None),
    ]

    while True:
        print("\n" + "=" * 50)
        print("  校园网入侵检测系统 — 交互菜单")
        print("=" * 50)
        for i, (label, _) in enumerate(menu_items, 1):
            print(f"  {i}. {label}")
        print("=" * 50)

        try:
            choice = input("  请选择 [1-9]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  退出")
            return 0

        if not choice.isdigit():
            print("  ✗ 请输入数字")
            continue

        idx = int(choice) - 1
        if idx < 0 or idx >= len(menu_items):
            print("  ✗ 无效选择")
            continue

        _, cmd = menu_items[idx]
        if cmd is None:
            print("  退出")
            return 0

        print()
        if cmd == "check":
            _cmd_check()
        elif cmd == "auto":
            try:
                dur = input("  抓包时长(秒, 回车默认30): ").strip()
                duration = int(dur) if dur else 30
            except (ValueError, EOFError):
                duration = 30
            _cmd_auto(duration)
        elif cmd == "capture":
            try:
                dur = input("  抓包时长(秒, 回车默认60): ").strip()
                duration = int(dur) if dur else 60
            except (ValueError, EOFError):
                duration = 60
            from campus_ids.capture.features import start_capture
            start_capture(duration)
        elif cmd == "ecapture":
            try:
                dur = input("  抓包时长(秒, 回车默认60): ").strip()
                duration = int(dur) if dur else 60
            except (ValueError, EOFError):
                duration = 60
            from campus_ids.capture.enhanced_features import start_enhanced_capture
            start_enhanced_capture(duration)
        elif cmd == "train":
            try:
                ds = input("  数据集(cicids2017/nsl_kdd/synthetic, 回车默认): ").strip().lower()
            except EOFError:
                ds = ""
            from campus_ids.model.train import train
            known = {"cicids2017", "nsl_kdd", "synthetic"}
            if ds in known:
                train(dataset_type=ds)
            else:
                train()
        elif cmd == "detect":
            from campus_ids.model.train import predict
            result = predict()
            if result is not None:
                df, preds = result
                from collections import Counter
                counts = Counter(preds)
                print(f"  检测完成，共 {len(preds)} 条流量：")
                for label, cnt in counts.items():
                    print(f"      {label}: {cnt}")
            else:
                from campus_ids.detector.detector import demo_detection
                print("  无可用模型，回退到规则检测演示。")
                demo_detection()
        elif cmd == "app":
            from campus_ids.web.app import run_app
            run_app()
        elif cmd == "demo":
            from campus_ids.demo.run_demo import run_demo
            run_demo()

        print("\n  --- 操作完成，返回菜单 ---")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        _usage()
        return 0

    command = args[0].lower()

    if command == "capture":
        from campus_ids.capture.features import start_capture

        duration = int(args[1]) if len(args) > 1 else 60
        return 0 if start_capture(duration) else 1

    if command == "ecapture":
        from campus_ids.capture.enhanced_features import start_enhanced_capture

        duration = int(args[1]) if len(args) > 1 else 60
        return 0 if start_enhanced_capture(duration) else 1

    if command == "train":
        from campus_ids.model.train import train
        from pathlib import Path

        # 解析 --dataset 参数
        dataset = None
        remaining = args[1:]
        i = 0
        while i < len(remaining):
            if remaining[i] == "--dataset" and i + 1 < len(remaining):
                dataset = remaining[i + 1]
                i += 2
            else:
                i += 1

        # 映射到 train() 参数：已知数据集名 → dataset_type，路径 → dataset_path
        known_datasets = {"cicids2017", "nsl_kdd", "synthetic"}
        if dataset and dataset.lower() in known_datasets:
            train(dataset_type=dataset.lower())
        elif dataset:
            train(dataset_path=Path(dataset))
        else:
            train()
        return 0

    if command == "detect":
        from campus_ids.model.train import predict

        result = predict()
        if result is not None:
            df, preds = result
            from collections import Counter
            counts = Counter(preds)
            print(f"[*] 模型预测完成，共 {len(preds)} 条流量：")
            for label, cnt in counts.items():
                print(f"    {label}: {cnt}")
            return 0

        from campus_ids.detector.detector import demo_detection
        print("[*] 无可用模型，回退到规则检测演示。")
        demo_detection()
        return 0

    if command == "app":
        from campus_ids.web.app import run_app

        run_app()
        return 0

    if command == "demo":
        from campus_ids.demo.run_demo import run_demo

        duration = 60
        no_attack = False
        no_browser = False
        remaining = args[1:]
        i = 0
        while i < len(remaining):
            if remaining[i] == "--duration" and i + 1 < len(remaining):
                duration = int(remaining[i + 1])
                i += 2
            elif remaining[i] == "--no-attack":
                no_attack = True
                i += 1
            elif remaining[i] == "--no-browser":
                no_browser = True
                i += 1
            else:
                i += 1
        run_demo(duration=duration, no_attack=no_attack, open_browser=not no_browser)
        return 0

    if command == "attack_sim":
        from campus_ids.demo.attack_sim import main as attack_main

        # 将剩余参数传递给 attack_sim
        sys.argv = [sys.argv[0]] + args[1:]
        attack_main()
        return 0

    # ── 简化操作命令 ────────────────────────────────────────────
    if command == "auto":
        duration = int(args[1]) if len(args) > 1 else 30
        return _cmd_auto(duration)

    if command == "menu":
        return _cmd_menu()

    if command == "check":
        return _cmd_check()

    _usage()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())