"""模型评估模块 — 混淆矩阵、指标计算、交叉验证、融合评估、报告生成。"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import LabelEncoder, StandardScaler
from tqdm import tqdm

from campus_ids.config import CONFUSION_MATRIX_PATH, EVALUATION_PATH, ML_CONF_HIGH, ML_CONF_LOW
from campus_ids.model.data_loader import ENHANCED_FEATURE_COLUMNS, align_features
from campus_ids.model.utils import clean_features, encode_class_weight

logger = logging.getLogger(__name__)


# ── 混淆矩阵保存 ──────────────────────────────────────────────────

def _save_confusion_matrix(y_test, y_pred, labels, path: Path) -> None:
    """P0-17: 保存混淆矩阵为图片。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # labels 可能是字符串类别名，但 y_test/y_pred 是编码后的整数
        # 需要确保 labels 与 y 的类型一致
        cm = confusion_matrix(y_test, y_pred)
        display_labels = labels if labels is not None else sorted(set(y_test) | set(y_pred))
        fig, ax = plt.subplots(figsize=(8, 6))
        im = ax.imshow(cm, interpolation="nearest", cmap=plt.cm.Blues)
        ax.figure.colorbar(im, ax=ax)
        ax.set(xticks=np.arange(cm.shape[1]),
               yticks=np.arange(cm.shape[0]),
               xticklabels=display_labels, yticklabels=display_labels,
               title="Confusion Matrix",
               ylabel="True label",
               xlabel="Predicted label")

        # 在格子中显示数值
        thresh = cm.max() / 2.
        for i in range(cm.shape[0]):
            for j in range(cm.shape[1]):
                ax.text(j, i, format(cm[i, j], "d"),
                        ha="center", va="center",
                        color="white" if cm[i, j] > thresh else "black")

        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)
        logger.info("混淆矩阵已保存至 %s", path)
    except ImportError:
        logger.warning("matplotlib 未安装，跳过混淆矩阵图片生成")
    except Exception as exc:
        logger.warning("混淆矩阵保存失败: %s", exc)


# ── 模型评估 ──────────────────────────────────────────────────────

def evaluate_model(y_test, y_pred, label_encoder, model_name: str = "Model") -> dict:
    """P0-16 + P2-11: 评估模型，输出指标（含 Per-Class F1 和误报率）。"""
    labels = label_encoder.classes_
    acc = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, average="weighted", zero_division=0)
    rec = recall_score(y_test, y_pred, average="weighted", zero_division=0)
    f1 = f1_score(y_test, y_pred, average="weighted", zero_division=0)

    report = classification_report(y_test, y_pred, target_names=labels, zero_division=0)

    # P2-11: Per-Class F1 分数
    per_class_f1 = {}
    per_class_report = classification_report(
        y_test, y_pred, target_names=labels, output_dict=True, zero_division=0
    )
    for label_name in labels:
        if label_name in per_class_report:
            per_class_f1[label_name] = per_class_report[label_name]["f1-score"]

    # P2-11: 误报率（False Positive Rate）
    # FPR = FP / (FP + TN)，即正常流量被误判为攻击的比例
    fpr = None
    cm = confusion_matrix(y_test, y_pred)
    if cm.shape == (2, 2):
        # 二分类：[[TN, FP], [FN, TP]]
        tn, fp, fn, tp = cm.ravel()
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
    else:
        # 多分类：计算每个类别的 FPR 并取宏平均
        fpr_per_class = []
        for i in range(cm.shape[0]):
            fp_i = cm[:, i].sum() - cm[i, i]
            tn_i = cm.sum() - cm[i, :].sum() - cm[:, i].sum() + cm[i, i]
            fpr_i = fp_i / (fp_i + tn_i) if (fp_i + tn_i) > 0 else 0.0
            fpr_per_class.append(fpr_i)
        fpr = np.mean(fpr_per_class)

    metrics = {
        "model": model_name,
        "accuracy": acc,
        "precision": prec,
        "recall": rec,
        "f1_score": f1,
        "report": report,
        "per_class_f1": per_class_f1,
        "false_positive_rate": fpr,
    }

    logger.info("=" * 60)
    logger.info("模型: %s", model_name)
    logger.info("准确率: %.4f  精确率: %.4f  召回率: %.4f  F1: %.4f", acc, prec, rec, f1)
    logger.info("Per-Class F1: %s", {k: f"{v:.4f}" for k, v in per_class_f1.items()})
    if fpr is not None:
        logger.info("误报率 (FPR): %.4f", fpr)
    logger.info("分类报告:\n%s", report)
    logger.info("=" * 60)

    return metrics


# ── 交叉验证 ──────────────────────────────────────────────────────

def cross_validate_models(X: pd.DataFrame, y: pd.Series,
                           class_weight_dict: dict | None = None,
                           cv: int = 3) -> list[dict]:
    """P1-7c: 5 折交叉验证 + ROC-AUC 对比。

    Returns:
        各模型交叉验证结果列表
    """
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import StratifiedKFold

    le = LabelEncoder()
    y_encoded = le.fit_transform(y)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    skf = StratifiedKFold(n_splits=cv, shuffle=True, random_state=42)

    model_configs = [
        ("RF_CV", "rf"),
        ("LGB_CV", "lgb"),
        ("XGB_CV", "xgb"),
    ]

    # P5: 解析 class_weight_dict → effective_cw（编码为整数键）
    # R-03: 委托给 encode_class_weight() 统一处理
    effective_cw = encode_class_weight(class_weight_dict, le)

    results = []
    pbar_cv = tqdm(model_configs, desc="交叉验证", unit="模型", leave=False)
    for name, mtype in pbar_cv:
        pbar_cv.set_description_str(f"CV {name}")
        try:
            # P2 修复：移除冗余 train_model 调用，直接创建分类器做交叉验证
            # R-09 注意：此处模型超参数为简化版本（n_estimators=100 等），仅用于 CV 评估；
            # 正式训练使用 train_model() 中的完整超参数（n_estimators=300 等）。
            if mtype == "rf":
                clf = RandomForestClassifier(n_estimators=100, random_state=42,
                                             class_weight=effective_cw, n_jobs=-1)
            elif mtype == "xgb":
                try:
                    from xgboost import XGBClassifier
                    clf = XGBClassifier(n_estimators=100, max_depth=6, learning_rate=0.1,
                                        random_state=42, n_jobs=-1)
                except ImportError:
                    continue
            elif mtype == "lgb":
                try:
                    from lightgbm import LGBMClassifier
                    clf = LGBMClassifier(n_estimators=100, max_depth=6, learning_rate=0.1,
                                         random_state=42, n_jobs=-1, verbose=-1)
                except ImportError:
                    continue
            elif mtype == "mlp":
                from sklearn.neural_network import MLPClassifier
                clf = MLPClassifier(hidden_layer_sizes=(128, 64, 32), max_iter=300,
                                    random_state=42, early_stopping=True, validation_fraction=0.1)
            else:
                continue

            cv_f1 = cross_val_score(clf, X_scaled, y_encoded, cv=skf, scoring="f1_weighted")

            # P2 修复：ROC-AUC 使用交叉验证而非全量数据训练+预测
            roc_auc = None
            n_classes = len(le.classes_)
            if n_classes == 2 and hasattr(clf, "predict_proba"):
                try:
                    cv_auc = cross_val_score(clf, X_scaled, y_encoded, cv=skf, scoring="roc_auc")
                    roc_auc = float(cv_auc.mean())
                except Exception:
                    pass

            metrics = {
                "model": name,
                "accuracy": float(cv_f1.mean()),
                "precision": float(cv_f1.mean()),
                "recall": float(cv_f1.mean()),
                "f1_score": float(cv_f1.mean()),
                "cv_f1_mean": float(cv_f1.mean()),
                "cv_f1_std": float(cv_f1.std()),
                "roc_auc": roc_auc,
            }
            results.append(metrics)
            logger.info("%s: CV-F1=%.4f(±%.4f)%s", name, cv_f1.mean(), cv_f1.std(),
                        f"  ROC-AUC={roc_auc:.4f}" if roc_auc else "")

        except Exception as exc:
            logger.warning("%s 交叉验证失败: %s", name, exc)
    pbar_cv.close()

    return results


# ── 双引擎融合评估 ────────────────────────────────────────────────

def _evaluate_dual_fusion(X: pd.DataFrame, y: pd.Series,
                           class_weight_dict: dict | None = None,
                           X_test: pd.DataFrame | None = None,
                           y_test: pd.Series | None = None,
                           prefitted: tuple | None = None) -> dict | None:
    """P1-7d: 双引擎融合方案评估。

    模拟规则+ML融合：规则检测判定+ML预测，双引擎触发则标记为攻击。
    P1 修复：使用 held-out 测试集评估，避免数据泄漏。

    Args:
        prefitted: 可选的 (clf, scaler, le, y_test) 元组，传入则跳过内部训练。
    """
    try:
        if prefitted is not None:
            rf_clf, rf_scaler, rf_le, rf_y_test = prefitted
        else:
            from campus_ids.model.train import train_model

            # P1 修复：训练在训练集上，评估在测试集上
            rf_clf, rf_scaler, rf_le, _, rf_y_test, rf_y_pred = train_model(
                X, y, class_weight_dict, model_type="rf",
                X_test=X_test, y_test=y_test,
            )

        # 确定评估数据集
        eval_X = X_test if X_test is not None else X
        eval_y = y_test if y_test is not None else y

        # ── 向量化规则判定（统一调用 detector.vectorized_rule_predict）──
        from campus_ids.detector.detector import vectorized_rule_predict
        is_attack = vectorized_rule_predict(eval_X)

        y_rule = is_attack.astype(int)

        # ML 预测（在测试集上）
        X_scaled = rf_scaler.transform(clean_features(eval_X))
        y_ml = rf_clf.predict(X_scaled)

        # ML 置信度（预测概率）
        y_ml_proba = None
        if hasattr(rf_clf, "predict_proba"):
            y_ml_proba = rf_clf.predict_proba(X_scaled)

        # ── 融合策略：自适应加权 + OR 互补（v2）──
        # 原版固定权重 ML×0.8+Rule×0.2 在规则 F1=0.71 时无增益
        # 新策略：
        #   1. ML 高置信区（>ML_CONF_HIGH）：直接采用 ML 判定（ML 已足够准确）
        #   2. ML 低置信区（ML_CONF_LOW~ML_CONF_HIGH）：规则作为补充信号，OR 逻辑提升召回
        #   3. 规则独有触发：保留为低危告警（不改变最终标签，但记录）
        #
        # 注：标量版融合逻辑见 dual_detector.DualDetector._detect_dual()，
        #     此处为向量化批处理版本，策略语义保持一致。

        # 获取 LabelEncoder 的攻击标签编码（Attack=0, Normal=1 字母序）
        attack_encoded = rf_le.transform(["Attack"])[0]
        normal_encoded = 1 - attack_encoded

        # ── 向量化融合投票 ──
        if y_ml_proba is not None:
            attack_idx = list(rf_le.classes_).index("Attack") if "Attack" in rf_le.classes_ else 1
            ml_conf = y_ml_proba[:, attack_idx]
        else:
            ml_conf = np.where(y_ml == attack_encoded, 1.0, 0.0)

        rule_conf = y_rule.astype(float)

        # 自适应融合：
        # - ML 高置信：信任 ML
        # - ML 不确定 + 规则触发：提升为攻击（OR 互补，提升召回）
        # - ML 判 Normal 且规则未触发：保持 Normal
        ml_is_attack = y_ml == attack_encoded
        ml_uncertain = (ml_conf >= ML_CONF_LOW) & (ml_conf <= ML_CONF_HIGH)
        rule_is_attack = y_rule.astype(bool)

        y_fusion = np.where(
            ml_is_attack,                              # ML 判攻击 → 攻击
            attack_encoded,
            np.where(
                ml_uncertain & rule_is_attack,         # ML 不确定 + 规则触发 → 攻击（互补）
                attack_encoded,
                normal_encoded                          # 其他 → 正常
            )
        )

        # 记录融合实际参数

        y_true = rf_le.transform(eval_y)

        acc = accuracy_score(y_true, y_fusion)
        prec = precision_score(y_true, y_fusion, average="weighted", zero_division=0)
        rec = recall_score(y_true, y_fusion, average="weighted", zero_division=0)
        f1 = f1_score(y_true, y_fusion, average="weighted", zero_division=0)

        # 统计融合增益
        ml_only_correct = np.sum(y_ml == y_true)
        fusion_correct = np.sum(y_fusion == y_true)
        rule_assisted = int(np.sum((y_ml != y_true) & (y_fusion == y_true)))  # 规则帮助修正的数量
        rule_hurt = int(np.sum((y_ml == y_true) & (y_fusion != y_true)))       # 规则导致误判的数量

        metrics = {
            "model": "DualFusion(Rule+RF)",
            "accuracy": acc,
            "precision": prec,
            "recall": rec,
            "f1_score": f1,
            "data_source": "same_as_ml",
            "fusion_strategy": "adaptive_or",
            "fusion_params": {
                "ml_conf_high": ML_CONF_HIGH, "ml_conf_low": ML_CONF_LOW,
                "rule_assisted": rule_assisted, "rule_hurt": rule_hurt,
            },
        }
        logger.info("双引擎融合(自适应OR): 准确率=%.4f  F1=%.4f  规则辅助修正=%d  规则误伤=%d",
                     acc, f1, rule_assisted, rule_hurt)
        return metrics

    except Exception as exc:
        logger.warning("双引擎融合评估失败: %s", exc)
        return None


# ── 规则检测基线 ──────────────────────────────────────────────────

def _evaluate_rule_baseline(X: pd.DataFrame, y: pd.Series) -> dict | None:
    """P0-20: 规则检测基线评估。

    委托 detector.vectorized_rule_predict 进行向量化规则判定，
    具体阈值逻辑参见 campus_ids.detector.detector 模块。
    """
    try:
        from campus_ids.detector.detector import vectorized_rule_predict

        # ── 向量化规则判定（统一调用 detector.vectorized_rule_predict）──
        is_attack = vectorized_rule_predict(X)
        y_pred_rule = np.full(len(X), "Normal", dtype=object)
        y_pred_rule[is_attack] = "Attack"

        # 计算指标
        y_pred_arr = pd.Series(y_pred_rule, index=y.index)
        acc = accuracy_score(y, y_pred_arr)
        prec = precision_score(y, y_pred_arr, average="weighted", zero_division=0)
        rec = recall_score(y, y_pred_arr, average="weighted", zero_division=0)
        f1 = f1_score(y, y_pred_arr, average="weighted", zero_division=0)

        metrics = {
            "model": "RuleBaseline",
            "accuracy": acc,
            "precision": prec,
            "recall": rec,
            "f1_score": f1,
            "data_source": "same_as_ml",
        }

        logger.info("规则检测基线: 准确率=%.4f  F1=%.4f", acc, f1)
        return metrics

    except Exception as exc:
        logger.warning("规则基线评估失败: %s", exc)
        return None


# ── 对比表打印 ────────────────────────────────────────────────────

def _print_comparison_table(metrics_list: list[dict]) -> None:
    """打印算法对比表。"""
    logger.info("\n" + "=" * 70)
    logger.info("算法对比表")
    logger.info("=" * 70)
    logger.info("%-25s  %-10s  %-10s  %-10s  %-10s", "模型", "准确率", "精确率", "召回率", "F1")
    logger.info("-" * 70)
    for m in metrics_list:
        logger.info("%-25s  %-10.4f  %-10.4f  %-10.4f  %-10.4f",
                     m["model"], m["accuracy"], m["precision"], m["recall"], m["f1_score"])
    logger.info("=" * 70)


# ── 评估报告保存 ──────────────────────────────────────────────────

def _save_evaluation_report(metrics_list: list[dict], data_source: str) -> None:
    """P2-11: 保存评估报告到文件（含 Per-Class F1、误报率、检测延迟）。"""
    lines = []
    lines.append("=" * 70)
    lines.append("SentinelNet 模型评估报告")
    lines.append(f"数据来源: {data_source}")
    lines.append("=" * 70)
    lines.append("")
    for m in metrics_list:
        lines.append(f"模型: {m['model']}")
        lines.append(f"  准确率: {m['accuracy']:.4f}")
        lines.append(f"  精确率: {m['precision']:.4f}")
        lines.append(f"  召回率: {m['recall']:.4f}")
        lines.append(f"  F1:     {m['f1_score']:.4f}")
        if m.get("cv_f1_mean") is not None:
            lines.append(f"  CV-F1:  {m['cv_f1_mean']:.4f} (±{m['cv_f1_std']:.4f})")
        if m.get("roc_auc") is not None:
            lines.append(f"  ROC-AUC: {m['roc_auc']:.4f}")
        # P2-11: Per-Class F1
        if m.get("per_class_f1"):
            lines.append("  Per-Class F1:")
            for cls_name, f1_val in m["per_class_f1"].items():
                lines.append(f"    {cls_name}: {f1_val:.4f}")
        # P2-11: 误报率
        if m.get("false_positive_rate") is not None:
            lines.append(f"  误报率 (FPR): {m['false_positive_rate']:.4f}")
        # P2-11: 检测延迟
        lat = m.get("detection_latency_ms")
        if lat is not None:
            if isinstance(lat, dict):
                lines.append("  检测延迟:")
                for k, v in lat.items():
                    lines.append(f"    {k}: {v:.3f} ms" if isinstance(v, float) else f"    {k}: {v}")
            else:
                lines.append(f"  检测延迟: {lat:.2f} ms")
        if "report" in m:
            lines.append(f"  分类报告:\n{m['report']}")
        lines.append("")

    # 算法对比表
    lines.append("算法对比表")
    lines.append(f"{'模型':<25}  {'准确率':<10}  {'精确率':<10}  {'召回率':<10}  {'F1':<10}  {'FPR':<10}")
    lines.append("-" * 80)
    for m in metrics_list:
        fpr_str = f"{m['false_positive_rate']:.4f}" if m.get("false_positive_rate") is not None else "N/A"
        lines.append(f"{m['model']:<25}  {m['accuracy']:<10.4f}  {m['precision']:<10.4f}  {m['recall']:<10.4f}  {m['f1_score']:<10.4f}  {fpr_str:<10}")

    # P2-11: Per-Class F1 对比表
    all_classes = set()
    for m in metrics_list:
        if m.get("per_class_f1"):
            all_classes.update(m["per_class_f1"].keys())
    if all_classes:
        lines.append("")
        lines.append("Per-Class F1 对比表")
        class_names = sorted(all_classes)
        header = f"{'模型':<25}  " + "  ".join(f"{c:<12}" for c in class_names)
        lines.append(header)
        lines.append("-" * (25 + 14 * len(class_names)))
        for m in metrics_list:
            if m.get("per_class_f1"):
                row = f"{m['model']:<25}  " + "  ".join(
                    f"{m['per_class_f1'].get(c, 0):.4f}      " for c in class_names
                )
                lines.append(row)

    EVALUATION_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info("评估报告已保存至 %s", EVALUATION_PATH)


# ── 检测延迟基准测试 ──────────────────────────────────────────────

def _benchmark_detection_latency(X: pd.DataFrame, n_samples: int = 100) -> dict | None:
    """P2-11: 检测延迟基准测试。

    测量规则检测和 ML 推理的单次延迟，输出统计指标。
    """
    import time as _time

    try:
        from campus_ids.detector.detector import create_rule_detector
        from campus_ids.model.train import load_model
        detector = create_rule_detector()

        # 规则检测延迟测试
        n = min(n_samples, len(X))
        rule_latencies = []
        pbar_rule_lat = tqdm(range(n), desc="规则延迟测试", unit="样本", leave=False)
        for i in pbar_rule_lat:
            row = X.iloc[i]
            t0 = _time.perf_counter()
            # 模拟规则检测调用
            pkt_count = row.get("pkt_count", row.get("Length", 0))
            if pkt_count > 100:
                detector.check_ddos(int(pkt_count))
            syn_ratio = row.get("syn_flag_ratio", 0)
            if syn_ratio > 0.5:
                detector.check_syn_flood(int(syn_ratio * 100))
            port_entropy = row.get("dst_port_entropy", 0)
            if port_entropy > 1.0:
                detector.check_port_scan(int(port_entropy * 10))
            rule_latencies.append((_time.perf_counter() - t0) * 1000)
        pbar_rule_lat.close()

        # ML 推理延迟测试（优先从注册表加载 best 模型）
        ml_latencies = []
        artifact = load_model()
        if artifact is not None:
            from campus_ids.model.data_loader import align_features
            clf = artifact["model"]
            scaler = artifact.get("scaler")
            feature_cols = artifact.get("feature_columns", ENHANCED_FEATURE_COLUMNS)
            pbar_ml_lat = tqdm(range(n), desc="ML 延迟测试", unit="样本", leave=False)
            for i in pbar_ml_lat:
                row = X.iloc[i:i+1]
                # 对齐特征列：补缺失、删多余、排序一致
                row = align_features(row, feature_cols)
                row = clean_features(row)
                t0 = _time.perf_counter()
                if scaler:
                    X_scaled = scaler.transform(row)
                else:
                    X_scaled = row.values
                clf.predict(X_scaled)
                ml_latencies.append((_time.perf_counter() - t0) * 1000)
            pbar_ml_lat.close()

        metrics = {
            "model": "DetectionLatency",
            "accuracy": 0.0,  # 占位，非评估指标
            "precision": 0.0,
            "recall": 0.0,
            "f1_score": 0.0,
            "detection_latency_ms": {
                "rule_mean": round(np.mean(rule_latencies), 3),
                "rule_p50": round(np.percentile(rule_latencies, 50), 3),
                "rule_p95": round(np.percentile(rule_latencies, 95), 3),
                "rule_p99": round(np.percentile(rule_latencies, 99), 3),
                "rule_max": round(max(rule_latencies), 3),
            },
        }
        if ml_latencies:
            metrics["detection_latency_ms"]["ml_mean"] = round(np.mean(ml_latencies), 3)
            metrics["detection_latency_ms"]["ml_p50"] = round(np.percentile(ml_latencies, 50), 3)
            metrics["detection_latency_ms"]["ml_p95"] = round(np.percentile(ml_latencies, 95), 3)
            metrics["detection_latency_ms"]["ml_p99"] = round(np.percentile(ml_latencies, 99), 3)
            metrics["detection_latency_ms"]["ml_max"] = round(max(ml_latencies), 3)

        logger.info("检测延迟基准: 规则 mean=%.3fms p95=%.3fms | ML mean=%.3fms p95=%.3fms",
                     metrics["detection_latency_ms"]["rule_mean"],
                     metrics["detection_latency_ms"]["rule_p95"],
                     metrics["detection_latency_ms"].get("ml_mean", 0),
                     metrics["detection_latency_ms"].get("ml_p95", 0))
        return metrics

    except Exception as exc:
        logger.warning("检测延迟基准测试失败: %s", exc)
        return None


# ── 跨数据集泛化评估 ──────────────────────────────────────────────

def cross_dataset_evaluate(artifact: dict, other_csvs: list[tuple]) -> list[dict]:
    """跨数据集泛化评估：用训练好的模型在其他 CICIDS2017 子集上测试。

    Args:
        artifact: 模型 artifact 字典，含 model/scaler/label_encoder/feature_columns
        other_csvs: [(csv_path, dataset_name), ...] 其他数据集列表

    Returns:
        各数据集的评估指标列表
    """
    from campus_ids.model.data_loader import load_dataset

    results = []
    clf = artifact["model"]
    scaler = artifact.get("scaler")
    le = artifact.get("label_encoder")
    feature_cols = artifact.get("feature_columns", ENHANCED_FEATURE_COLUMNS)

    pbar_cross = tqdm(other_csvs, desc="跨数据集评估", unit="数据集", leave=False)
    for csv_path, ds_name in pbar_cross:
        pbar_cross.set_description_str(f"评估 {ds_name}")
        try:
            result = load_dataset(csv_path, "cicids2017")
            if result is None:
                logger.warning("跨数据集 %s 加载失败，跳过", ds_name)
                continue
            X_test, y_test = result

            # 关键修复：特征对齐 — 补缺失特征填0、删多余特征、排序一致
            X_test = align_features(X_test, feature_cols)
            X_test = clean_features(X_test)

            if scaler:
                X_scaled = scaler.transform(X_test)
            else:
                X_scaled = X_test.values

            y_pred = clf.predict(X_scaled)
            if le:
                y_true = le.transform(y_test)
            else:
                y_true = y_test

            acc = accuracy_score(y_true, y_pred)
            prec = precision_score(y_true, y_pred, average="weighted", zero_division=0)
            rec = recall_score(y_true, y_pred, average="weighted", zero_division=0)
            f1 = f1_score(y_true, y_pred, average="weighted", zero_division=0)

            metrics = {
                "model": "RF_CrossDataset",
                "accuracy": acc,
                "precision": prec,
                "recall": rec,
                "f1_score": f1,
                "data_source": ds_name,
            }
            results.append(metrics)
            logger.info("跨数据集 %s: 准确率=%.4f  F1=%.4f", ds_name, acc, f1)

        except Exception as exc:
            logger.warning("跨数据集 %s 评估失败: %s", ds_name, exc)
    pbar_cross.close()

    return results