# -*- coding: utf-8 -*-
"""T2.18 契约回放 CLI —— 退出码 = 未解释差异数。

用法：

    python scripts/replay_contract.py                # 完整回放（含真实 GET 比对）
    python scripts/replay_contract.py --schema-only  # 只跑规格门禁（不动任何端点）
    python scripts/replay_contract.py -v             # 打印每条判定

**为什么必须先隔离 data_dir**：回放会真的实例化新应用（`create_app()`），
lifespan 会 `init_db()` 并构造 detector / 各 service。不隔离就会写真实
`sentinelnet.db`。这里复用录制器同一套 `bootstrap()`。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# ① 隔离必须在 import campus_ids 之前
from _recorder_safety import assert_products_untouched, bootstrap, snapshot_products  # noqa: E402

TMP_DIR = bootstrap()

from tests.contract.replay import run_replay  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="契约回放与规格门禁（T2.18）")
    parser.add_argument("--schema-only", action="store_true",
                        help="只跑规格门禁，不发任何请求")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印每条判定")
    args = parser.parse_args()

    before = snapshot_products()
    failures, lines = run_replay(schema_only=args.schema_only)
    assert_products_untouched(before)

    print(f"data_dir 已隔离到 {TMP_DIR}")
    print("=" * 78)
    if args.verbose or failures:
        for line in lines:
            print(line)
    else:
        # 全绿时只打印汇总与规格账，避免刷屏
        for line in lines:
            if line.startswith(("旧规格", "  保留", "  已解释", "  未解释", "新规格", "合计")):
                print(line)
    print("=" * 78)
    print(f"未解释差异：{failures}  → 退出码 {min(failures, 125)}")
    print("真实产物 6/6 md5 未变 ✅")
    return min(failures, 125)


if __name__ == "__main__":
    sys.exit(main())
