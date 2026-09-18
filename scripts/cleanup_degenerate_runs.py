"""清理 models/runs 中的「退化 run」并同步收敛注册表。

背景
----
`train()` 在找不到足够真实数据时，会退化为随机合成数据训练，并在
`metrics.json` 里写 `data_source = "synthetic_demo"`，同时在日志里明确警告：

    ⚠️  此模型无实际检测能力，仅用于流程验证！
    ⚠️  答辩时请使用真实数据集训练。

这类 run 的 `model.pkl` 只有 ~69KB、指标恒为 1.0，属于流程验证产物。
历史上它们因测试隔离缺陷落进了真实 `models/` 目录，并把 `best.json`
（而 `best.json` 直接驱动前端「最佳模型」展示与 `load_run(which="best")`）
指向了一个假模型。

判据（全部来自 train.py 自己写下的字段，不从被测代码推期望）
----------------------------------------------------------
- `data_source == "synthetic_demo"`               → 退化
- 单类别（`per_class_f1` 只有 1 个键）且 pkl < 1MB → 退化
- `data_source` 以 `dataset:` 开头                  → 真实数据，保留

安全约束
--------
- 默认 dry-run；必须显式 `--apply` 才动手。
- `--apply` 必须传 `--backup <dir>`，且该目录须存在、位于项目外、
  且含 `models/registry.json`。备份缺失直接拒绝执行。
- 删除统一走 `ModelService.delete_model()`（生产代码路径），
  保证 registry.json 与磁盘一致，不手工改写 JSON。
- 收尾按 train.py 自身规则重算 `best.json` / `latest.json` / `is_best`：
  best = 剩余 run 中 f1_score 最大者；latest = created_at 最大者。

用法
----
    python scripts/cleanup_degenerate_runs.py                    # 预览
    python scripts/cleanup_degenerate_runs.py --apply \
        --backup D:/18551/AiC/_SentinelNet_cleanup_backup_20260918
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# 真实数据源前缀
REAL_PREFIX = "dataset:"
# best.json 保留的指标键（与 train.update_best 保持一致）
BEST_METRIC_KEYS = ("accuracy", "precision", "recall", "f1_score", "cv_f1_mean")
SMALL_PKL = 1_000_000


def _load(registry_path: Path) -> list[dict]:
    return json.loads(registry_path.read_text(encoding="utf-8"))


def _metrics_of(run_dir: Path) -> dict:
    p = run_dir / "metrics.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def classify(run_dir: Path, entry: dict) -> tuple[str, str]:
    """返回 (verdict, reason)。verdict ∈ {keep, drop}。"""
    met = _metrics_of(run_dir)
    src = str(met.get("data_source") or "")
    pkl = run_dir / "model.pkl"
    pkl_size = pkl.stat().st_size if pkl.exists() else 0
    per_class = met.get("per_class_f1") or {}

    if not run_dir.exists():
        return "drop", "run 目录不存在（registry 悬挂）"
    if src.startswith(REAL_PREFIX):
        return "keep", f"真实数据源 {src}"
    if src == "synthetic_demo":
        return "drop", "synthetic_demo（train.py 明示无检测能力）"
    if len(per_class) < 2 and pkl_size < SMALL_PKL:
        return "drop", f"单类别 {list(per_class)} 且 pkl 仅 {pkl_size/1024:.0f}KB"
    return "keep", f"data_source={src or '未知'}"


def check_backup(backup: Path) -> None:
    if not backup.is_dir():
        raise SystemExit(f"[拒绝执行] 备份目录不存在: {backup}")
    try:
        backup.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        pass  # 位于项目外 —— 正确
    else:
        raise SystemExit(f"[拒绝执行] 备份目录必须在项目外: {backup}")
    if not (backup / "models" / "registry.json").is_file():
        raise SystemExit(f"[拒绝执行] 备份缺少 models/registry.json: {backup}")


def main() -> int:
    ap = argparse.ArgumentParser(description="清理退化 run 并收敛模型注册表")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认仅预览）")
    ap.add_argument("--backup", type=Path, default=None, help="项目外的备份目录（--apply 必填）")
    args = ap.parse_args()

    if os.environ.get("CAMPUS_IDS_DATA_DIR"):
        raise SystemExit(
            "[拒绝执行] 检测到 CAMPUS_IDS_DATA_DIR 已设置 —— 本脚本只针对真实 models/ 目录，"
            "请先清除该环境变量。"
        )

    from campus_ids.config import MODELS_DIR, REGISTRY_JSON, RUNS_DIR  # noqa: E402

    if MODELS_DIR.resolve() != (PROJECT_ROOT / "models").resolve():
        raise SystemExit(
            f"[拒绝执行] MODELS_DIR 解析为 {MODELS_DIR}，"
            f"与预期 {PROJECT_ROOT / 'models'} 不符。"
        )

    registry = _load(REGISTRY_JSON)
    print(f"注册表: {REGISTRY_JSON}（{len(registry)} 条）")
    print(f"run 目录: {RUNS_DIR}\n")

    drops: list[tuple[str, str]] = []
    keeps: list[tuple[str, str]] = []
    print(f"{'run_id':<24}{'pklKB':>7}  {'判定':<6} 理由")
    for entry in registry:
        run_id = entry.get("run_id", "")
        run_dir = RUNS_DIR / run_id
        pkl = run_dir / "model.pkl"
        pkl_kb = pkl.stat().st_size / 1024 if pkl.exists() else 0
        verdict, reason = classify(run_dir, entry)
        (drops if verdict == "drop" else keeps).append((run_id, reason))
        print(f"{run_id:<24}{pkl_kb:>7.0f}  {verdict:<6} {reason}")

    print(f"\n保留 {len(keeps)} 个 / 清理 {len(drops)} 个")

    if not args.apply:
        print("\n[dry-run] 未做任何改动。加 --apply --backup <dir> 执行。")
        return 0

    if args.backup is None:
        raise SystemExit("[拒绝执行] --apply 必须同时提供 --backup <项目外目录>")
    check_backup(args.backup)
    print(f"备份已核验: {args.backup}")

    # ── 1. 删除：走生产代码路径 ────────────────────────────────
    from campus_ids.services.model_service import ModelService

    svc = ModelService(state=None, dual_detector=None)
    deleted, failed = [], []
    for run_id, _ in drops:
        res = svc.delete_model(run_id)
        if res.get("status") == "deleted":
            deleted.append(run_id)
        else:
            failed.append((run_id, res))
    print(f"\n已删除 {len(deleted)} 个 run 目录")
    if failed:
        print(f"删除失败 {len(failed)} 个: {failed}")

    # ── 2. 收敛注册表指针（按 train.py 自身规则重算）──────────
    from campus_ids.model.train import _atomic_write_json
    from campus_ids.config import BEST_JSON, LATEST_JSON

    remaining = _load(REGISTRY_JSON)

    # 2a. best = 剩余 run 中 f1_score 最大者
    best_entry = None
    for e in remaining:
        score = (e.get("metrics") or {}).get("f1_score", 0.0)
        if isinstance(score, dict):
            score = 0.0
        if best_entry is None or score > best_entry[1]:
            best_entry = (e, score)

    if best_entry is not None:
        e, best_score = best_entry
        _atomic_write_json(BEST_JSON, {
            "run_id": e["run_id"],
            "run_dir": e["run_dir"],
            "metrics": {
                k: v for k, v in (e.get("metrics") or {}).items()
                if k in BEST_METRIC_KEYS
            },
        })
        print(f"best.json  -> {e['run_id']} (f1={best_score:.6f})")
    else:
        if BEST_JSON.exists():
            BEST_JSON.unlink()
        print("best.json  -> 已清空（无剩余 run）")

    # 2b. latest = created_at 最大者
    if remaining:
        newest = max(remaining, key=lambda e: str(e.get("created_at", "")))
        _atomic_write_json(LATEST_JSON, {
            "run_id": newest["run_id"],
            "run_dir": newest["run_dir"],
            "created_at": newest["created_at"],
        })
        print(f"latest.json -> {newest['run_id']}")
    else:
        if LATEST_JSON.exists():
            LATEST_JSON.unlink()
        print("latest.json -> 已清空")

    # 2c. is_best 标记与 best.json 对齐
    best_id = best_entry[0]["run_id"] if best_entry else None
    for e in remaining:
        e["is_best"] = (e.get("run_id") == best_id)
    _atomic_write_json(REGISTRY_JSON, remaining)
    print(f"registry.json is_best 已对齐（{best_id}）")

    # ── 3. 收尾一致性自证 ───────────────────────────────────
    print("\n=== 一致性自证 ===")
    final = _load(REGISTRY_JSON)
    disk = {p.name for p in RUNS_DIR.iterdir() if p.is_dir()}
    reg_ids = {e.get("run_id") for e in final}
    dangling = [e["run_id"] for e in final if not (RUNS_DIR / e["run_id"]).exists()]
    orphans = sorted(disk - reg_ids)
    print(f"registry 条目: {len(final)}   磁盘 run 目录: {len(disk)}")
    print(f"registry 悬挂(目录不存在): {len(dangling)} {dangling}")
    print(f"磁盘多余(不在 registry): {len(orphans)} {orphans}")
    flags = [e["run_id"] for e in final if e.get("is_best")]
    print(f"is_best 标记数: {len(flags)} {flags}")
    ok = not dangling and not orphans and len(flags) <= 1
    print("结论:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
