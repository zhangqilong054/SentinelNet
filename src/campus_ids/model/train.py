"""模型训练与评估模块 — 支持增强特征、公开数据集、算法对比。

P0-13: 替换仅用 Length+Duration 的训练流程
P0-14: 支持公开数据集加载
P0-15: 数据集预处理模块
P0-16: 输出准确率/精确率/召回率/F1
P0-17: 混淆矩阵保存为图片
P0-18: 移除/标注 _synthetic_data() 回退逻辑
P0-19: 类别均衡处理
P0-20: 算法对比（随机森林 vs 规则检测）
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder, StandardScaler

from campus_ids.config import (
    CONFUSION_MATRIX_PATH,
    MODEL_PATH,
    TRAFFIC_CSV,
)

# 从子模块重导出，保持向后兼容
from campus_ids.model.data_loader import (  # noqa: F401
    ENHANCED_FEATURE_COLUMNS,
    LEGACY_FEATURE_COLUMNS,
    MIN_TRAIN_SAMPLES,
    _detect_csv_format,
    _load_local_data,
    _synthetic_data,
    balance_classes,
    load_cicids2017,
    load_dataset,
    load_nsl_kdd,
    split_and_save_dataset,
)
from campus_ids.model.evaluation import (  # noqa: F401
    _benchmark_detection_latency,
    _evaluate_dual_fusion,
    _evaluate_rule_baseline,
    _print_comparison_table,
    _save_confusion_matrix,
    _save_evaluation_report,
    cross_validate_models,
    evaluate_model,
)

logger = logging.getLogger(__name__)


# ── 模型训练 ──────────────────────────────────────────────────────

def train_model(X: pd.DataFrame, y: pd.Series,
                class_weight_dict: dict | None = None,
                model_type: str = "rf",
                X_test: pd.DataFrame | None = None,
                y_test: pd.Series | None = None) -> tuple:
    """训练模型并返回 (model, scaler, le, X_test, y_test, y_pred)。

    Args:
        X: 训练特征
        y: 训练标签
        class_weight_dict: 类别权重。None=自动均衡, False=不加权(数据已均衡), dict=自定义权重
        model_type: rf / lr / xgb / lgb / mlp
        X_test: 测试特征（None 则从 X 中划分）
        y_test: 测试标签（None 则从 y 中划分）
    """
    # 标签编码
    le = LabelEncoder()
    y_encoded = le.fit_transform(y)

    # 解析 class_weight_dict → effective_cw（传给模型的 class_weight 参数）
    # None → "balanced"（默认自动均衡）
    # False → None（数据已均衡，不加权）
    # dict → 编码后的整数键字典
    effective_cw: dict | str | None = "balanced"
    if class_weight_dict is False:
        effective_cw = None
    elif class_weight_dict is not None:
        encoded_cw = {}
        for cls_name, weight in class_weight_dict.items():
            if cls_name in le.classes_:
                encoded_cw[le.transform([cls_name])[0]] = weight
            else:
                logger.warning("class_weight 中的类别 %r 不在标签中，已忽略", cls_name)
        effective_cw = encoded_cw if encoded_cw else "balanced"

    # 特征标准化 — 仅在训练集上 fit，避免数据泄漏
    X = X.replace([np.inf, -np.inf], np.nan).fillna(0)
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X)

    # 获取测试集
    if X_test is not None and y_test is not None:
        X_test_clean = X_test.replace([np.inf, -np.inf], np.nan).fillna(0)
        X_test_scaled = scaler.transform(X_test_clean)
        y_test_encoded = le.transform(y_test)
        y_train_encoded = y_encoded
    else:
        # 向后兼容：从训练数据中划分
        X_train_scaled, X_test_scaled, y_train_encoded, y_test_encoded = train_test_split(
            X_train_scaled, y_encoded, test_size=0.2, random_state=42, stratify=y_encoded
        )

    # 训练模型
    n_classes = len(le.classes_)

    if model_type == "rf":
        clf = RandomForestClassifier(
            n_estimators=100, random_state=42,
            class_weight=effective_cw,
            n_jobs=-1,
        )
    elif model_type == "lr":
        clf = LogisticRegression(
            max_iter=1000, random_state=42,
            class_weight=effective_cw,
        )
    # P1-7a: XGBoost / LightGBM 算法
    elif model_type == "xgb":
        try:
            from xgboost import XGBClassifier
        except ImportError:
            raise ImportError("xgboost 未安装，请运行: pip install xgboost")
        # P3: 多分类安全性 — scale_pos_weight 仅适用于二分类
        if n_classes == 2:
            from collections import Counter
            label_counts = Counter(y_train_encoded)
            neg_count = label_counts.get(0, 1)
            pos_count = label_counts.get(1, 1)
            spw = neg_count / pos_count if pos_count > 0 else 1.0
            # P5: 数据已均衡时不设置 scale_pos_weight
            if class_weight_dict is False:
                spw = 1.0
            clf = XGBClassifier(
                n_estimators=100, max_depth=6, learning_rate=0.1,
                random_state=42, n_jobs=-1,
                scale_pos_weight=spw,
            )
        else:
            logger.info("XGBoost: %d 分类模式，使用 sample_weight 替代 scale_pos_weight", n_classes)
            from sklearn.utils import compute_sample_weight
            clf = XGBClassifier(
                n_estimators=100, max_depth=6, learning_rate=0.1,
                random_state=42, n_jobs=-1,
            )
            if effective_cw is not None:
                sw = compute_sample_weight(
                    effective_cw if isinstance(effective_cw, dict) else "balanced",
                    y_train_encoded,
                )
                clf.fit(X_train_scaled, y_train_encoded, sample_weight=sw)
            else:
                clf.fit(X_train_scaled, y_train_encoded)
            y_pred = clf.predict(X_test_scaled)
            return clf, scaler, le, X_test_scaled, y_test_encoded, y_pred
    elif model_type == "lgb":
        try:
            from lightgbm import LGBMClassifier
        except ImportError:
            raise ImportError("lightgbm 未安装，请运行: pip install lightgbm")
        # P3: 多分类安全性 — scale_pos_weight 仅适用于二分类
        if n_classes == 2:
            from collections import Counter
            label_counts = Counter(y_train_encoded)
            neg_count = label_counts.get(0, 1)
            pos_count = label_counts.get(1, 1)
            spw = neg_count / pos_count if pos_count > 0 else 1.0
            if class_weight_dict is False:
                spw = 1.0
            clf = LGBMClassifier(
                n_estimators=100, max_depth=6, learning_rate=0.1,
                random_state=42, n_jobs=-1, verbose=-1,
                scale_pos_weight=spw,
            )
        else:
            logger.info("LightGBM: %d 分类模式，使用 sample_weight 替代 scale_pos_weight", n_classes)
            from sklearn.utils import compute_sample_weight
            clf = LGBMClassifier(
                n_estimators=100, max_depth=6, learning_rate=0.1,
                random_state=42, n_jobs=-1, verbose=-1,
            )
            if effective_cw is not None:
                sw = compute_sample_weight(
                    effective_cw if isinstance(effective_cw, dict) else "balanced",
                    y_train_encoded,
                )
                clf.fit(X_train_scaled, y_train_encoded, sample_weight=sw)
            else:
                clf.fit(X_train_scaled, y_train_encoded)
            y_pred = clf.predict(X_test_scaled)
            return clf, scaler, le, X_test_scaled, y_test_encoded, y_pred
    # P1-7b: MLP 深度学习模型
    elif model_type == "mlp":
        from sklearn.neural_network import MLPClassifier
        clf = MLPClassifier(
            hidden_layer_sizes=(128, 64, 32), max_iter=300,
            random_state=42, early_stopping=True,
            validation_fraction=0.1,
        )
    else:
        raise ValueError(f"未知模型类型: {model_type}")

    # MLP 不支持 class_weight 参数，使用 sample_weight 传入
    if model_type == "mlp" and effective_cw is not None:
        from sklearn.utils import compute_sample_weight
        sw = compute_sample_weight(
            effective_cw if isinstance(effective_cw, dict) else "balanced",
            y_train_encoded,
        )
        clf.fit(X_train_scaled, y_train_encoded, sample_weight=sw)
    else:
        clf.fit(X_train_scaled, y_train_encoded)
    y_pred = clf.predict(X_test_scaled)

    return clf, scaler, le, X_test_scaled, y_test_encoded, y_pred


# ── 模型持久化 ────────────────────────────────────────────────────

def save_model(clf, scaler, label_encoder, path: Path | None = None, feature_columns: list | None = None) -> None:
    """持久化模型（含 scaler 和 label_encoder）。"""
    import joblib
    out = path or MODEL_PATH
    artifact = {
        "model": clf,
        "scaler": scaler,
        "label_encoder": label_encoder,
        "feature_columns": feature_columns or ENHANCED_FEATURE_COLUMNS,
    }
    joblib.dump(artifact, out)
    logger.info("模型已保存至 %s（特征数: %d）", out, len(artifact["feature_columns"]))


def load_model(path: Path | None = None) -> dict | None:
    """加载已保存的模型 artifact。"""
    import joblib
    p = path or MODEL_PATH
    if not p.exists():
        return None
    try:
        artifact = joblib.load(p)
        if isinstance(artifact, dict):
            return artifact
        # 兼容旧格式（直接是模型对象）
        return {"model": artifact, "scaler": None, "label_encoder": None, "feature_columns": LEGACY_FEATURE_COLUMNS}
    except Exception as exc:
        logger.warning("加载模型失败: %s", exc)
        return None


def predict(csv_path: Path | None = None) -> tuple | None:
    """加载模型并对数据做预测，返回 (DataFrame, predictions) 或 None。"""
    artifact = load_model()
    if artifact is None:
        logger.warning("无可用模型，请先运行 train。")
        return None

    clf = artifact["model"]
    scaler = artifact["scaler"]
    le = artifact["label_encoder"]
    feature_cols = artifact.get("feature_columns", ENHANCED_FEATURE_COLUMNS)

    path = csv_path or TRAFFIC_CSV
    if not path.exists() or path.stat().st_size == 0:
        logger.warning("无抓包数据 (%s)，无法预测。", path)
        return None

    df = pd.read_csv(path)
    if df.empty:
        return None

    available_cols = [c for c in feature_cols if c in df.columns]
    if not available_cols:
        logger.warning("数据中无匹配特征列")
        return None

    X = df[available_cols].fillna(0)
    if scaler:
        X_scaled = scaler.transform(X)
    else:
        X_scaled = X.values

    preds_encoded = clf.predict(X_scaled)
    if le:
        preds = le.inverse_transform(preds_encoded)
    else:
        preds = preds_encoded

    return df, preds


# ── 主训练流程 ──────────────────────────────────────────────────────

def train(dataset_path: Path | None = None,
          dataset_type: str = "auto",
          balance_method: str = "class_weight") -> dict:
    """完整训练流程。

    Args:
        dataset_path: 数据集路径（None 则使用本地抓包数据）
        dataset_type: auto / local / cicids2017 / nsl_kdd
        balance_method: class_weight / smote / undersample

    Returns:
        训练结果字典
    """
    # P0-18: 优先使用真实数据，_synthetic_data 仅作为最后回退并标注
    X, y = None, None
    data_source = "unknown"

    if dataset_path:
        result = load_dataset(dataset_path, dataset_type)
        if result:
            X, y = result
            data_source = f"dataset:{dataset_path.name}"

    if X is None:
        result = _load_local_data()
        if result:
            X, y = result
            data_source = "local_capture"

    if X is None or len(X) < MIN_TRAIN_SAMPLES:
        logger.warning("=" * 60)
        logger.warning("⚠️  无足够真实数据（需 ≥ %d 条），使用随机数据演示。", MIN_TRAIN_SAMPLES)
        logger.warning("⚠️  此模型无实际检测能力，仅用于流程验证！")
        logger.warning("⚠️  答辩时请使用真实数据集训练。")
        logger.warning("=" * 60)
        X, y = _synthetic_data()
        data_source = "synthetic_demo"

    if len(X) < MIN_TRAIN_SAMPLES:
        logger.warning("数据仅 %d 条，建议增加数据量以获得可靠模型。", len(X))

    # P0 修复：划分训练/测试集，使用 held-out 测试集统一评估
    X_test_held, y_test_held = None, None
    if data_source != "synthetic_demo":
        logger.info("划分训练/测试集（测试比例 20%%）...")
        X_train_full, y_train_full, X_test_held, y_test_held = split_and_save_dataset(X, y)
        # 仅在训练集上做类别均衡，避免测试集信息泄漏
        X_bal, y_bal, class_weight_dict = balance_classes(X_train_full, y_train_full, balance_method)
    else:
        # 合成数据：全量做均衡（train_model 内部会划分）
        X_bal, y_bal, class_weight_dict = balance_classes(X, y, balance_method)

    # P0-20: 算法对比
    results = {}
    all_metrics = []

    # 随机森林
    logger.info("训练随机森林模型...")
    rf_clf, rf_scaler, rf_le, rf_X_test, rf_y_test, rf_y_pred = train_model(
        X_bal, y_bal, class_weight_dict, model_type="rf",
        X_test=X_test_held, y_test=y_test_held,
    )
    rf_metrics = evaluate_model(rf_y_test, rf_y_pred, rf_le, "RandomForest")
    rf_metrics["data_source"] = data_source
    all_metrics.append(rf_metrics)

    # P0-17: 混淆矩阵
    _save_confusion_matrix(rf_y_test, rf_y_pred, rf_le.classes_, CONFUSION_MATRIX_PATH)

    # 保存最优模型（随机森林）
    actual_feature_cols = list(X_bal.columns)
    save_model(rf_clf, rf_scaler, rf_le, feature_columns=actual_feature_cols)

    # 逻辑回归对比
    logger.info("训练逻辑回归模型（对比基线）...")
    try:
        lr_clf, lr_scaler, lr_le, lr_X_test, lr_y_test, lr_y_pred = train_model(
            X_bal, y_bal, class_weight_dict, model_type="lr",
            X_test=X_test_held, y_test=y_test_held,
        )
        lr_metrics = evaluate_model(lr_y_test, lr_y_pred, lr_le, "LogisticRegression")
        all_metrics.append(lr_metrics)
    except Exception as exc:
        logger.warning("逻辑回归训练失败: %s", exc)

    # P1-7a: XGBoost 对比
    logger.info("训练 XGBoost 模型...")
    try:
        xgb_clf, xgb_scaler, xgb_le, xgb_X_test, xgb_y_test, xgb_y_pred = train_model(
            X_bal, y_bal, class_weight_dict, model_type="xgb",
            X_test=X_test_held, y_test=y_test_held,
        )
        xgb_metrics = evaluate_model(xgb_y_test, xgb_y_pred, xgb_le, "XGBoost")
        all_metrics.append(xgb_metrics)
    except Exception as exc:
        logger.warning("XGBoost 训练失败（可能未安装）: %s", exc)

    # P1-7a: LightGBM 对比
    logger.info("训练 LightGBM 模型...")
    try:
        lgb_clf, lgb_scaler, lgb_le, lgb_X_test, lgb_y_test, lgb_y_pred = train_model(
            X_bal, y_bal, class_weight_dict, model_type="lgb",
            X_test=X_test_held, y_test=y_test_held,
        )
        lgb_metrics = evaluate_model(lgb_y_test, lgb_y_pred, lgb_le, "LightGBM")
        all_metrics.append(lgb_metrics)
    except Exception as exc:
        logger.warning("LightGBM 训练失败（可能未安装）: %s", exc)

    # P1-7b: MLP 深度学习模型
    logger.info("训练 MLP 神经网络模型...")
    try:
        mlp_clf, mlp_scaler, mlp_le, mlp_X_test, mlp_y_test, mlp_y_pred = train_model(
            X_bal, y_bal, class_weight_dict, model_type="mlp",
            X_test=X_test_held, y_test=y_test_held,
        )
        mlp_metrics = evaluate_model(mlp_y_test, mlp_y_pred, mlp_le, "MLP")
        all_metrics.append(mlp_metrics)
    except Exception as exc:
        logger.warning("MLP 训练失败: %s", exc)

    # P1-7c: 交叉验证 + ROC-AUC 对比（在训练集上做 CV）
    logger.info("执行 5 折交叉验证...")
    try:
        cv_results = cross_validate_models(X_bal, y_bal, class_weight_dict)
        if cv_results:
            all_metrics.extend(cv_results)
    except Exception as exc:
        logger.warning("交叉验证失败: %s", exc)

    # P1-7d: 双引擎融合方案 F1 对比（P1 修复：使用 held-out 测试集）
    try:
        fusion_metrics = _evaluate_dual_fusion(
            X_bal, y_bal, class_weight_dict,
            X_test=X_test_held, y_test=y_test_held,
        )
        if fusion_metrics:
            all_metrics.append(fusion_metrics)
    except Exception as exc:
        logger.warning("双引擎融合评估失败: %s", exc)

    # P0-20: 规则检测基线（P4 修复：使用 held-out 测试集）
    rule_metrics = _evaluate_rule_baseline(
        X_test_held if X_test_held is not None else X,
        y_test_held if y_test_held is not None else y,
    )
    if rule_metrics:
        all_metrics.append(rule_metrics)

    # P2-11: 检测延迟基准测试
    latency_metrics = _benchmark_detection_latency(X_bal)
    if latency_metrics:
        all_metrics.append(latency_metrics)

    # 输出对比表
    _print_comparison_table(all_metrics)

    # 保存评估报告
    _save_evaluation_report(all_metrics, data_source)

    results["metrics"] = all_metrics
    results["best_model"] = "RandomForest"
    results["data_source"] = data_source

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    train()