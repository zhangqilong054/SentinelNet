# -*- coding: utf-8 -*-
"""T2.18 契约回放 CLI —— 退出码 = 未解释差异数。

用法：

    python scripts/replay_contract.py                     # 完整回放（含真实 GET 比对）
    python scripts/replay_contract.py --schema-only       # 只跑规格门禁（不动任何端点）
    python scripts/replay_contract.py --update-snapshot   # 刷新规格快照（契约有意外变更时）
    python scripts/replay_contract.py -v                  # 打印每条判定

**为什么必须先隔离 data_dir**：回放会真的实例化新应用（`create_app()`），
lifespan 会 `init_db()` 并构造 detector / 各 service。不隔离就会写真实
`sentinelnet.db`。这里复用录制器同一套 `bootstrap()`。

⚠️ `--update-snapshot` 只应在**确认契约变更是有意为之**后使用，
并在提交信息里说明原因 —— 否则等于把门禁关掉。
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
from tests.contract.specdiff import dump_snapshot, summarize_snapshot  # noqa: E402


def _update_snapshot() -> int:
    """刷新 `tests/contract/schema_snapshot.json`。"""
    from campus_ids.web.app import create_app

    app = create_app()
    spec = dump_snapshot(app)
    ops = summarize_snapshot(spec)
    print(f"data_dir 已隔离到 {TMP_DIR}")
    print(f"已写入规格快照：{len(spec.get('paths', {}))} 条路径 / {ops} 个操作")
    print("⚠️ 请确认这些契约变更是有意为之，并在提交信息中说明。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="契约回放与规格门禁（T2.18）")
    parser.add_argument("--schema-only", action="store_true",
                        help="只跑规格门禁，不发任何请求")
    parser.add_argument("--update-snapshot", action="store_true",
                        help="刷新规格快照（契约有意变更时使用）")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印每条判定")
    args = parser.parse_args()

    before = snapshot_products()

    if args.update_snapshot:
        code = _update_snapshot()
        assert_products_untouched(before)
        print("真实产物 6/6 md5 未变 ✅")
        return code

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
            if line.startswith(("旧规格", "  保留", "  已解释", "  未解释", "新规格",
                                "参数级", "快照门禁", "合计")):
                print(line)
    print("=" * 78)
    print(f"未解释差异：{failures}  → 退出码 {min(failures, 125)}")
    print("真实产物 6/6 md5 未变 ✅")
    return min(failures, 125)


if __name__ == "__main__":
    sys.exit(main())
