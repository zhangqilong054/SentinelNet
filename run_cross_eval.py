"""快速跨数据集评估脚本 — 验证 feature_columns 修复。"""
import sys
import json
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from campus_ids.model.train import load_model
from campus_ids.model.evaluation import cross_dataset_evaluate
from campus_ids.config import DATA_DIR

def main():
    # 1. 加载最新模型
    artifact = load_model(which="latest")
    if artifact is None:
        print("ERROR: 无法加载模型")
        return

    clf = artifact["model"]
    scaler = artifact.get("scaler")
    le = artifact.get("label_encoder")
    feature_cols = artifact.get("feature_columns")

    print(f"模型类型: {type(clf).__name__}")
    print(f"特征列数: {len(feature_cols) if feature_cols else 'N/A'}")
    print(f"特征列表: {feature_cols}")
    print(f"Scaler: {type(scaler).__name__ if scaler else 'None'}")
    print(f"LabelEncoder: {type(le).__name__ if le else 'None'}")
    if scaler and hasattr(scaler, "feature_names_in_"):
        print(f"Scaler 训练特征: {list(scaler.feature_names_in_)}")
    print()

    # 2. 查找 CICIDS2017 数据集
    cicids_dir = Path(DATA_DIR) / "data"
    if not cicids_dir.exists():
        cicids_dir = Path(DATA_DIR)

    csv_files = sorted(cicids_dir.glob("*.csv"))
    print(f"找到 {len(csv_files)} 个 CSV 文件:")
    for f in csv_files:
        print(f"  - {f.name}")
    print()

    # 排除训练数据集 (Wednesday)
    current_ds_name = "Wednesday-workingHours.pcap_ISCX.csv"
    other_csvs = [
        (f, f.stem.replace(".pcap_ISCX", ""))
        for f in csv_files
        if f.name != current_ds_name
    ]

    if not other_csvs:
        print("ERROR: 无可用跨数据集")
        return

    # 3. 运行跨数据集评估
    print(f"开始跨数据集评估 ({len(other_csvs)} 个数据集)...")
    results = cross_dataset_evaluate(artifact, other_csvs)

    # 4. 输出结果
    if results:
        print("\n" + "=" * 70)
        print("跨数据集评估结果")
        print("=" * 70)
        for r in results:
            ds = r.get("data_source", "unknown")
            acc = r.get("accuracy", 0)
            f1 = r.get("f1_score", 0)
            print(f"  {ds:50s}  准确率={acc:.4f}  F1={f1:.4f}")
        print("=" * 70)
    else:
        print("WARNING: 跨数据集评估无结果")

if __name__ == "__main__":
    main()