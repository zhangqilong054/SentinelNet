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
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder, StandardScaler
from tqdm import tqdm

from campus_ids.config import (
    BEST_JSON,
    CONFUSION_MATRIX_PATH,
    LATEST_JSON,
    MODEL_PATH,
    MODELS_DIR,
    REGISTRY_JSON,
    RUNS_DIR,
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
        rf_n_estimators = 100
        rf_batch = max(1, rf_n_estimators // 10)
        clf = RandomForestClassifier(
            n_estimators=rf_batch, random_state=42,
            class_weight=effective_cw,
            n_jobs=-1, warm_start=True,
        )
        # tqdm 实时进度条（每批 10 棵树更新，抑制 warm_start+class_weight 兼容警告）
        pbar_rf = tqdm(total=rf_n_estimators, desc="RF 迭代", unit="树", leave=False)
        fitted = 0
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="class_weight presets.*warm_start")
                while fitted < rf_n_estimators:
                    batch = min(rf_batch, rf_n_estimators - fitted)
                    clf.n_estimators = fitted + batch
                    clf.fit(X_train_scaled, y_train_encoded)
                    pbar_rf.update(batch)
                    fitted += batch
        finally:
            pbar_rf.close()
        y_pred = clf.predict(X_test_scaled)
        return clf, scaler, le, X_test_scaled, y_test_encoded, y_pred
    elif model_type == "lr":
        lr_max_iter = 1000
        clf = LogisticRegression(
            max_iter=1, random_state=42,
            class_weight=effective_cw,
            warm_start=True,
        )
        # tqdm 实时进度条（每迭代更新，自动收敛检测）
        pbar_lr = tqdm(total=lr_max_iter, desc="LR 迭代", unit="iter", leave=False)
        prev_coef = None
        converged = False
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=ConvergenceWarning)
                for _ in range(lr_max_iter):
                    clf.fit(X_train_scaled, y_train_encoded)
                    pbar_lr.update(1)
                    if hasattr(clf, "coef_") and prev_coef is not None:
                        if np.allclose(clf.coef_, prev_coef, atol=1e-6):
                            pbar_lr.set_description("LR 收敛")
                            converged = True
                            break
                    if hasattr(clf, "coef_"):
                        prev_coef = clf.coef_.copy()
        finally:
            pbar_lr.close()
        y_pred = clf.predict(X_test_scaled)
        return clf, scaler, le, X_test_scaled, y_test_encoded, y_pred
    # P1-7a: XGBoost — 带 tqdm 实时进度条
    elif model_type == "xgb":
        try:
            from xgboost import XGBClassifier
        except ImportError:
            raise ImportError("xgboost 未安装，请运行: pip install xgboost")
        xgb_n_estimators = 100
        # P3: 多分类安全性 — scale_pos_weight 仅适用于二分类
        if n_classes == 2:
            from collections import Counter
            label_counts = Counter(y_train_encoded)
            neg_count = label_counts.get(0, 1)
            pos_count = label_counts.get(1, 1)
            spw = neg_count / pos_count if pos_count > 0 else 1.0
            if class_weight_dict is False:
                spw = 1.0
            clf = XGBClassifier(
                n_estimators=xgb_n_estimators, max_depth=6, learning_rate=0.1,
                random_state=42, n_jobs=-1, scale_pos_weight=spw,
            )
        else:
            logger.info("XGBoost: %d 分类模式，使用 sample_weight 替代 scale_pos_weight", n_classes)
            clf = XGBClassifier(
                n_estimators=xgb_n_estimators, max_depth=6, learning_rate=0.1,
                random_state=42, n_jobs=-1,
            )
        # 构造 fit 参数
        xgb_fit_kwargs = {"eval_set": [(X_test_scaled, y_test_encoded)], "verbose": False}
        if n_classes > 2 and effective_cw is not None:
            from sklearn.utils import compute_sample_weight
            xgb_fit_kwargs["sample_weight"] = compute_sample_weight(
                effective_cw if isinstance(effective_cw, dict) else "balanced",
                y_train_encoded,
            )
        # tqdm 实时进度条（每轮提升）
        pbar_xgb = tqdm(total=xgb_n_estimators, desc="XGBoost 迭代", unit="轮", leave=False)
        try:
            from xgboost.callback import TrainingCallback
            class _XGBTqdm(TrainingCallback):
                def after_iteration(self, model, epoch, evals_log):
                    pbar_xgb.update(1)
                    if evals_log:
                        for _d, metrics in evals_log.items():
                            for mn, vs in metrics.items():
                                if vs:
                                    pbar_xgb.set_postfix_str(f"{mn}={vs[-1]:.4f}")
                    return False  # 继续训练
            xgb_fit_kwargs["callbacks"] = [_XGBTqdm()]
        except (ImportError, AttributeError):
            pass  # 旧版 xgboost 不支持 TrainingCallback
        clf.fit(X_train_scaled, y_train_encoded, **xgb_fit_kwargs)
        pbar_xgb.close()
        y_pred = clf.predict(X_test_scaled)
        return clf, scaler, le, X_test_scaled, y_test_encoded, y_pred

    # P1-7a: LightGBM — 带 tqdm 实时进度条
    elif model_type == "lgb":
        try:
            from lightgbm import LGBMClassifier
        except ImportError:
            raise ImportError("lightgbm 未安装，请运行: pip install lightgbm")
        lgb_n_estimators = 100
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
                n_estimators=lgb_n_estimators, max_depth=6, learning_rate=0.1,
                random_state=42, n_jobs=-1, verbose=-1, scale_pos_weight=spw,
            )
        else:
            logger.info("LightGBM: %d 分类模式，使用 sample_weight 替代 scale_pos_weight", n_classes)
            clf = LGBMClassifier(
                n_estimators=lgb_n_estimators, max_depth=6, learning_rate=0.1,
                random_state=42, n_jobs=-1, verbose=-1,
            )
        # 构造 fit 参数
        lgb_fit_kwargs = {"eval_set": [(X_test_scaled, y_test_encoded)]}
        if n_classes > 2 and effective_cw is not None:
            from sklearn.utils import compute_sample_weight
            lgb_fit_kwargs["sample_weight"] = compute_sample_weight(
                effective_cw if isinstance(effective_cw, dict) else "balanced",
                y_train_encoded,
            )
        # tqdm 实时进度条（每轮提升）
        pbar_lgb = tqdm(total=lgb_n_estimators, desc="LightGBM 迭代", unit="轮", leave=False)
        def _lgb_tqdm_cb(env):
            pbar_lgb.update(1)
            if env.evaluation_result_list:
                for item in env.evaluation_result_list:
                    if len(item) >= 3:
                        pbar_lgb.set_postfix_str(f"{item[1]}={item[2]:.4f}")
        lgb_fit_kwargs["callbacks"] = [_lgb_tqdm_cb]
        clf.fit(X_train_scaled, y_train_encoded, **lgb_fit_kwargs)
        pbar_lgb.close()
        y_pred = clf.predict(X_test_scaled)
        return clf, scaler, le, X_test_scaled, y_test_encoded, y_pred

    # P1-7b: MLP 深度学习模型 — 带 tqdm 实时进度条
    elif model_type == "mlp":
        from sklearn.neural_network import MLPClassifier
        mlp_max_iter = 300
        mlp_n_iter_no_change = 10
        clf = MLPClassifier(
            hidden_layer_sizes=(128, 64, 32), max_iter=1,
            random_state=42, warm_start=True,
            early_stopping=False,  # 手动实现早停
        )
        # 计算样本权重
        sw = None
        if effective_cw is not None:
            from sklearn.utils import compute_sample_weight
            sw = compute_sample_weight(
                effective_cw if isinstance(effective_cw, dict) else "balanced",
                y_train_encoded,
            )
        # tqdm 实时进度条（每 epoch 更新，抑制 max_iter=1 的 ConvergenceWarning）
        pbar_mlp = tqdm(total=mlp_max_iter, desc="MLP 迭代", unit="epoch", leave=False)
        best_loss = float("inf")
        no_improve_count = 0
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=ConvergenceWarning)
            for _epoch in range(mlp_max_iter):
                if sw is not None:
                    clf.fit(X_train_scaled, y_train_encoded, sample_weight=sw)
                else:
                    clf.fit(X_train_scaled, y_train_encoded)
                pbar_mlp.update(1)
                if hasattr(clf, "loss_curve_") and clf.loss_curve_:
                    cur_loss = clf.loss_curve_[-1]
                    pbar_mlp.set_postfix(loss=f"{cur_loss:.4f}")
                    if cur_loss < best_loss - 1e-4:
                        best_loss = cur_loss
                        no_improve_count = 0
                    else:
                        no_improve_count += 1
                    if no_improve_count >= mlp_n_iter_no_change:
                        pbar_mlp.set_description("MLP 早停")
                        break
        pbar_mlp.close()
        y_pred = clf.predict(X_test_scaled)
        return clf, scaler, le, X_test_scaled, y_test_encoded, y_pred

    else:
        raise ValueError(f"未知模型类型: {model_type}")


# ── 模型注册表（版本化保存）────────────────────────────────────────

def make_run_id() -> str:
    """生成唯一 run_id: YYYYMMDD_HHMMSS_<6位随机>。"""
    import random, string
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{ts}_{suffix}"


def _atomic_write_json(path: Path, data: dict) -> None:
    """原子写入 JSON 文件：先写 .tmp 再 rename，防止半写。"""
    import json
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def save_run(
    clf, scaler, label_encoder,
    metrics: dict | None = None,
    feature_columns: list | None = None,
    params: dict | None = None,
    confusion_matrix_path: Path | None = None,
) -> tuple[str, Path]:
    """将训练产物保存到版本化 run 目录。

    Returns:
        (run_id, run_dir) 元组
    """
    import json, joblib
    from datetime import datetime, timezone

    run_id = make_run_id()
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1. 保存模型和 scaler
    joblib.dump(clf, run_dir / "model.pkl")
    if scaler is not None:
        joblib.dump(scaler, run_dir / "scaler.pkl")

    # 2. 保存特征列表
    feat_cols = feature_columns or ENHANCED_FEATURE_COLUMNS
    (run_dir / "feature_list.json").write_text(
        json.dumps(feat_cols, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    # 3. 保存标签映射
    label_mapping = None
    if label_encoder is not None:
        label_mapping = {str(cls): int(idx) for idx, cls in enumerate(label_encoder.classes_)}
        (run_dir / "label_mapping.json").write_text(
            json.dumps(label_mapping, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    # 4. 保存指标
    if metrics is not None:
        (run_dir / "metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
        )

    # 5. 保存元数据
    metadata = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_type": type(clf).__name__,
        "n_features": len(feat_cols),
        "params": params or {},
    }
    (run_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    # 6. 复制混淆矩阵图片（如已生成）
    if confusion_matrix_path is not None and confusion_matrix_path.exists():
        import shutil
        shutil.copy2(confusion_matrix_path, run_dir / "confusion_matrix.png")

    # 7. 原子更新 latest.json
    latest_data = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "created_at": metadata["created_at"],
    }
    _atomic_write_json(LATEST_JSON, latest_data)

    # 8. 更新 registry.json
    _update_registry(run_id, run_dir, metadata, metrics)

    # 9. 同时保存到传统 model.pkl（向后兼容）
    save_model(clf, scaler, label_encoder, feature_columns=feat_cols)

    logger.info("模型已保存至 run: %s（特征数: %d）", run_dir, len(feat_cols))
    return run_id, run_dir


def _update_registry(run_id: str, run_dir: Path, metadata: dict, metrics: dict | None) -> None:
    """更新 registry.json，追加新 run 记录。"""
    import json
    registry: list[dict] = []
    if REGISTRY_JSON.exists():
        try:
            registry = json.loads(REGISTRY_JSON.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            registry = []

    entry = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "created_at": metadata.get("created_at", ""),
        "model_type": metadata.get("model_type", ""),
        "n_features": metadata.get("n_features", 0),
        "is_best": False,
    }
    if metrics:
        entry["metrics"] = {
            k: v for k, v in metrics.items()
            if k in ("accuracy", "precision", "recall", "f1_score", "cv_f1_mean")
        }
    registry.append(entry)
    _atomic_write_json(REGISTRY_JSON, registry)


def update_best(run_id: str, run_dir: Path, metrics: dict, key: str = "f1_score") -> bool:
    """检查新 run 是否为最佳模型，若是则更新 best.json。

    Args:
        key: 用于比较的指标键，默认 f1_score

    Returns:
        True 表示更新了 best（新模型更优）
    """
    import json
    new_score = metrics.get(key, 0.0)
    if isinstance(new_score, dict):
        new_score = 0.0

    # 读取当前 best
    current_best: dict | None = None
    if BEST_JSON.exists():
        try:
            current_best = json.loads(BEST_JSON.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            current_best = None

    current_score = 0.0
    if current_best and "metrics" in current_best:
        current_score = current_best["metrics"].get(key, 0.0)
        if isinstance(current_score, dict):
            current_score = 0.0

    is_best = new_score > current_score
    if is_best:
        best_data = {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "metrics": {
                k: v for k, v in metrics.items()
                if k in ("accuracy", "precision", "recall", "f1_score", "cv_f1_mean")
            },
        }
        _atomic_write_json(BEST_JSON, best_data)

        # 更新 registry 中 is_best 标记
        if REGISTRY_JSON.exists():
            try:
                registry = json.loads(REGISTRY_JSON.read_text(encoding="utf-8"))
                for entry in registry:
                    entry["is_best"] = (entry.get("run_id") == run_id)
                _atomic_write_json(REGISTRY_JSON, registry)
            except (json.JSONDecodeError, OSError):
                pass

        logger.info("新最佳模型: %s (%s=%.4f)", run_id, key, new_score)

    return is_best


def load_run(run_id: str | None = None, which: str = "best") -> dict | None:
    """按 run_id 或指针加载模型 run。

    Args:
        run_id: 指定 run_id，优先级最高
        which: "best" 或 "latest"，当 run_id 为 None 时使用

    Returns:
        与 load_model() 相同格式的 artifact dict，额外含 run_id
    """
    import json, joblib

    # 1. 如果指定了 run_id，直接定位
    if run_id is not None:
        run_dir = RUNS_DIR / run_id
        if not run_dir.exists():
            logger.warning("run 目录不存在: %s", run_dir)
            return None
    else:
        # 2. 按指针加载
        pointer_map = {"best": BEST_JSON, "latest": LATEST_JSON}
        pointer_path = pointer_map.get(which, BEST_JSON)
        if not pointer_path.exists():
            logger.info("指针文件不存在: %s，回退到传统 model.pkl", pointer_path)
            return load_model()  # 向后兼容
        try:
            ptr = json.loads(pointer_path.read_text(encoding="utf-8"))
            run_dir = Path(ptr["run_dir"])
            if not run_dir.exists():
                logger.warning("指针指向的 run 目录不存在: %s", run_dir)
                return load_model()
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            logger.warning("读取指针文件失败: %s，回退到 model.pkl", exc)
            return load_model()

    # 3. 加载 run 目录中的产物
    model_path = run_dir / "model.pkl"
    if not model_path.exists():
        logger.warning("run 中无 model.pkl: %s", run_dir)
        return None

    try:
        clf = joblib.load(model_path)

        # 加载 scaler
        scaler = None
        scaler_path = run_dir / "scaler.pkl"
        if scaler_path.exists():
            scaler = joblib.load(scaler_path)

        # 加载 label_mapping → 重建 LabelEncoder
        label_encoder = None
        lm_path = run_dir / "label_mapping.json"
        if lm_path.exists():
            try:
                lm = json.loads(lm_path.read_text(encoding="utf-8"))
                from sklearn.preprocessing import LabelEncoder
                le = LabelEncoder()
                le.classes_ = np.array(sorted(lm, key=lambda k: lm[k]))
                label_encoder = le
            except Exception:
                pass

        # 加载 feature_list
        feature_columns = ENHANCED_FEATURE_COLUMNS
        fl_path = run_dir / "feature_list.json"
        if fl_path.exists():
            try:
                feature_columns = json.loads(fl_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        artifact = {
            "model": clf,
            "scaler": scaler,
            "label_encoder": label_encoder,
            "feature_columns": feature_columns,
            "run_id": run_dir.name,  # 额外信息
        }
        logger.info("从 run 加载模型: %s", run_dir.name)
        return artifact
    except Exception as exc:
        logger.warning("加载 run 失败: %s", exc)
        return None


# ── 模型持久化（传统格式，向后兼容）────────────────────────────────

def save_model(clf, scaler, label_encoder, path: Path | None = None, feature_columns: list | None = None) -> None:
    """持久化模型（含 scaler 和 label_encoder）到单个 pkl 文件。"""
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


def load_model(path: Path | None = None, which: str = "best") -> dict | None:
    """加载已保存的模型 artifact。

    优先从模型注册表加载（best/latest 指针），回退到传统 model.pkl。

    Args:
        path: 传统 pkl 路径（向后兼容，指定后跳过注册表）
        which: 注册表指针 "best" 或 "latest"
    """
    # 如果显式指定了 path，走传统加载逻辑
    if path is not None:
        import joblib
        if not path.exists():
            return None
        try:
            artifact = joblib.load(path)
            if isinstance(artifact, dict):
                return artifact
            return {"model": artifact, "scaler": None, "label_encoder": None, "feature_columns": LEGACY_FEATURE_COLUMNS}
        except Exception as exc:
            logger.warning("加载模型失败: %s", exc)
            return None

    # 优先从注册表加载
    artifact = load_run(which=which)
    if artifact is not None:
        return artifact

    # 回退到传统 model.pkl
    import joblib
    p = MODEL_PATH
    if not p.exists():
        return None
    try:
        artifact = joblib.load(p)
        if isinstance(artifact, dict):
            return artifact
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

    # ── 进度条：训练流程 12 步 ──
    steps = [
        ("数据预处理", "preprocess"),
        ("随机森林", "rf"),
        ("逻辑回归", "lr"),
        ("XGBoost", "xgb"),
        ("LightGBM", "lgb"),
        ("MLP 神经网络", "mlp"),
        ("5 折交叉验证", "cv"),
        ("双引擎融合", "fusion"),
        ("规则基线", "rule"),
        ("延迟基准", "latency"),
        ("对比表输出", "table"),
        ("保存报告", "report"),
    ]

    # P0-20: 算法对比
    results = {}
    all_metrics = []

    pbar = tqdm(steps, desc="训练流程", unit="步", bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}] {desc}")

    # Step 1: 数据预处理（已完成，直接更新）
    pbar.set_description_str("✓ 数据预处理完成")
    pbar.update(1)

    # Step 2: 随机森林
    pbar.set_description_str("训练随机森林...")
    rf_clf, rf_scaler, rf_le, rf_X_test, rf_y_test, rf_y_pred = train_model(
        X_bal, y_bal, class_weight_dict, model_type="rf",
        X_test=X_test_held, y_test=y_test_held,
    )
    rf_metrics = evaluate_model(rf_y_test, rf_y_pred, rf_le, "RandomForest")
    rf_metrics["data_source"] = data_source
    all_metrics.append(rf_metrics)
    _save_confusion_matrix(rf_y_test, rf_y_pred, rf_le.classes_, CONFUSION_MATRIX_PATH)
    actual_feature_cols = list(X_bal.columns)
    # 模型注册表：版本化保存 + best 指针更新
    run_id, run_dir = save_run(
        rf_clf, rf_scaler, rf_le,
        metrics=rf_metrics, feature_columns=actual_feature_cols,
        confusion_matrix_path=CONFUSION_MATRIX_PATH,
    )
    update_best(run_id, run_dir, rf_metrics)
    pbar.set_description_str("✓ 随机森林完成")
    pbar.update(1)

    # Step 3: 逻辑回归
    pbar.set_description_str("训练逻辑回归...")
    try:
        lr_clf, lr_scaler, lr_le, lr_X_test, lr_y_test, lr_y_pred = train_model(
            X_bal, y_bal, class_weight_dict, model_type="lr",
            X_test=X_test_held, y_test=y_test_held,
        )
        lr_metrics = evaluate_model(lr_y_test, lr_y_pred, lr_le, "LogisticRegression")
        all_metrics.append(lr_metrics)
    except Exception as exc:
        logger.warning("逻辑回归训练失败: %s", exc)
    pbar.set_description_str("✓ 逻辑回归完成")
    pbar.update(1)

    # Step 4: XGBoost
    pbar.set_description_str("训练 XGBoost...")
    try:
        xgb_clf, xgb_scaler, xgb_le, xgb_X_test, xgb_y_test, xgb_y_pred = train_model(
            X_bal, y_bal, class_weight_dict, model_type="xgb",
            X_test=X_test_held, y_test=y_test_held,
        )
        xgb_metrics = evaluate_model(xgb_y_test, xgb_y_pred, xgb_le, "XGBoost")
        all_metrics.append(xgb_metrics)
    except Exception as exc:
        logger.warning("XGBoost 训练失败（可能未安装）: %s", exc)
    pbar.set_description_str("✓ XGBoost 完成")
    pbar.update(1)

    # Step 5: LightGBM
    pbar.set_description_str("训练 LightGBM...")
    try:
        lgb_clf, lgb_scaler, lgb_le, lgb_X_test, lgb_y_test, lgb_y_pred = train_model(
            X_bal, y_bal, class_weight_dict, model_type="lgb",
            X_test=X_test_held, y_test=y_test_held,
        )
        lgb_metrics = evaluate_model(lgb_y_test, lgb_y_pred, lgb_le, "LightGBM")
        all_metrics.append(lgb_metrics)
    except Exception as exc:
        logger.warning("LightGBM 训练失败（可能未安装）: %s", exc)
    pbar.set_description_str("✓ LightGBM 完成")
    pbar.update(1)

    # Step 6: MLP
    pbar.set_description_str("训练 MLP...")
    try:
        mlp_clf, mlp_scaler, mlp_le, mlp_X_test, mlp_y_test, mlp_y_pred = train_model(
            X_bal, y_bal, class_weight_dict, model_type="mlp",
            X_test=X_test_held, y_test=y_test_held,
        )
        mlp_metrics = evaluate_model(mlp_y_test, mlp_y_pred, mlp_le, "MLP")
        all_metrics.append(mlp_metrics)
    except Exception as exc:
        logger.warning("MLP 训练失败: %s", exc)
    pbar.set_description_str("✓ MLP 完成")
    pbar.update(1)

    # Step 7: 交叉验证
    pbar.set_description_str("5 折交叉验证...")
    try:
        cv_results = cross_validate_models(X_bal, y_bal, class_weight_dict)
        if cv_results:
            all_metrics.extend(cv_results)
    except Exception as exc:
        logger.warning("交叉验证失败: %s", exc)
    pbar.set_description_str("✓ 交叉验证完成")
    pbar.update(1)

    # Step 8: 双引擎融合
    pbar.set_description_str("双引擎融合评估...")
    try:
        fusion_metrics = _evaluate_dual_fusion(
            X_bal, y_bal, class_weight_dict,
            X_test=X_test_held, y_test=y_test_held,
        )
        if fusion_metrics:
            all_metrics.append(fusion_metrics)
    except Exception as exc:
        logger.warning("双引擎融合评估失败: %s", exc)
    pbar.set_description_str("✓ 融合评估完成")
    pbar.update(1)

    # Step 9: 规则基线
    pbar.set_description_str("规则基线评估...")
    rule_metrics = _evaluate_rule_baseline(
        X_test_held if X_test_held is not None else X,
        y_test_held if y_test_held is not None else y,
    )
    if rule_metrics:
        all_metrics.append(rule_metrics)
    pbar.set_description_str("✓ 规则基线完成")
    pbar.update(1)

    # Step 10: 延迟基准
    pbar.set_description_str("延迟基准测试...")
    latency_metrics = _benchmark_detection_latency(X_bal)
    if latency_metrics:
        all_metrics.append(latency_metrics)
    pbar.set_description_str("✓ 延迟基准完成")
    pbar.update(1)

    # Step 11: 对比表
    pbar.set_description_str("输出对比表...")
    _print_comparison_table(all_metrics)
    pbar.set_description_str("✓ 对比表完成")
    pbar.update(1)

    # Step 12: 保存报告
    pbar.set_description_str("保存评估报告...")
    _save_evaluation_report(all_metrics, data_source)
    pbar.set_description_str("✓ 报告已保存")
    pbar.update(1)

    pbar.close()

    results["metrics"] = all_metrics
    results["best_model"] = "RandomForest"
    results["data_source"] = data_source

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    train()