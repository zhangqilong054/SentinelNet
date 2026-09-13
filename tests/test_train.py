"""train.py 单元测试 — 覆盖模型训练、加载、预测核心流程。"""
import pickle
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

from campus_ids.model.train import (
    ENHANCED_FEATURE_COLUMNS,
    LEGACY_FEATURE_COLUMNS,
    _detect_csv_format,
    _load_local_data,
    train_model,
)


# ── 辅助函数 ──────────────────────────────────────────────────

def _make_legacy_csv(path: Path, n_rows: int = 200, attack_ratio: float = 0.3):
    """生成 legacy 格式 CSV（Length + Duration + Label）。"""
    rng = np.random.default_rng(42)
    n_attack = int(n_rows * attack_ratio)
    n_normal = n_rows - n_attack

    normal = pd.DataFrame({
        "Src_IP": ["192.168.1.1"] * n_normal,
        "Dst_IP": ["10.0.0.1"] * n_normal,
        "Src_Port": rng.integers(10000, 60000, n_normal),
        "Dst_Port": rng.integers(1, 100, n_normal),
        "Protocol": ["TCP"] * n_normal,
        "Length": rng.integers(40, 200, n_normal),
        "Timestamp": np.sort(rng.uniform(0, 60, n_normal)),
        "Duration": rng.uniform(0, 0.5, n_normal),
        "Label": ["Normal"] * n_normal,
    })
    attack = pd.DataFrame({
        "Src_IP": ["10.0.0.99"] * n_attack,
        "Dst_IP": ["192.168.1.1"] * n_attack,
        "Src_Port": rng.integers(10000, 60000, n_attack),
        "Dst_Port": rng.integers(1, 65535, n_attack),
        "Protocol": ["TCP"] * n_attack,
        "Length": rng.integers(200, 1500, n_attack),
        "Timestamp": np.sort(rng.uniform(0, 60, n_attack)),
        "Duration": rng.uniform(0, 0.5, n_attack),
        "Label": ["Attack"] * n_attack,
    })
    df = pd.concat([normal, attack], ignore_index=True)
    df.to_csv(path, index=False)
    return path


def _make_enhanced_csv(path: Path, n_rows: int = 200, attack_ratio: float = 0.3):
    """生成 enhanced 格式 CSV（17个增强特征 + Label）。"""
    rng = np.random.default_rng(42)
    n_attack = int(n_rows * attack_ratio)
    n_normal = n_rows - n_attack

    data = {}
    for col in ENHANCED_FEATURE_COLUMNS:
        if "ratio" in col or "entropy" in col:
            data[col] = rng.uniform(0, 1, n_rows)
        elif "hash" in col or "enc" in col:
            data[col] = rng.integers(0, 100, n_rows)
        else:
            data[col] = rng.uniform(0, 1000, n_rows)
    data["Label"] = ["Normal"] * n_normal + ["Attack"] * n_attack
    df = pd.DataFrame(data)
    df.to_csv(path, index=False)
    return path


# ── CSV 格式检测 ──────────────────────────────────────────────

class TestDetectCSVFormat:
    def test_legacy_format(self, tmp_path):
        p = tmp_path / "legacy.csv"
        _make_legacy_csv(p)
        assert _detect_csv_format(p) == "legacy"

    def test_enhanced_format(self, tmp_path):
        p = tmp_path / "enhanced.csv"
        _make_enhanced_csv(p)
        assert _detect_csv_format(p) == "enhanced"

    def test_nonexistent_file(self, tmp_path):
        p = tmp_path / "nonexistent.csv"
        assert _detect_csv_format(p) == "unknown"


# ── 数据加载 ──────────────────────────────────────────────────

class TestLoadLocalData:
    def test_load_legacy(self, tmp_path):
        p = tmp_path / "legacy.csv"
        _make_legacy_csv(p, n_rows=200)
        result = _load_local_data(p)
        assert result is not None
        X, y = result
        assert len(X) == 200
        assert len(y) == 200
        assert set(y.unique()) == {"Normal", "Attack"}

    def test_load_enhanced(self, tmp_path):
        p = tmp_path / "enhanced.csv"
        _make_enhanced_csv(p, n_rows=200)
        result = _load_local_data(p)
        assert result is not None
        X, y = result
        assert len(X) == 200

    def test_load_empty_csv(self, tmp_path):
        p = tmp_path / "empty.csv"
        pd.DataFrame(columns=["Length", "Duration", "Label"]).to_csv(p, index=False)
        result = _load_local_data(p)
        assert result is None

    def test_load_no_label_column(self, tmp_path):
        p = tmp_path / "nolabel.csv"
        pd.DataFrame({"Length": [1, 2], "Duration": [0.1, 0.2]}).to_csv(p, index=False)
        result = _load_local_data(p)
        assert result is None


# ── 模型训练 ──────────────────────────────────────────────────

class TestTrainModel:
    def _make_data(self, n_rows=200):
        """生成训练数据 (X, y)。"""
        rng = np.random.default_rng(42)
        n_attack = int(n_rows * 0.3)
        n_normal = n_rows - n_attack

        X_normal = rng.uniform(0, 100, (n_normal, len(ENHANCED_FEATURE_COLUMNS)))
        X_attack = rng.uniform(200, 1500, (n_attack, len(ENHANCED_FEATURE_COLUMNS)))
        X = pd.DataFrame(
            np.vstack([X_normal, X_attack]),
            columns=ENHANCED_FEATURE_COLUMNS,
        )
        y = pd.Series(["Normal"] * n_normal + ["Attack"] * n_attack)
        return X, y

    def test_train_rf(self):
        """训练随机森林模型并验证输出。"""
        X, y = self._make_data()
        model, scaler, le, X_test, y_test, y_pred = train_model(X, y, model_type="rf")
        from sklearn.metrics import accuracy_score
        acc = accuracy_score(y_test, y_pred)
        assert acc > 0.5

    def test_train_xgb(self):
        """训练 XGBoost 模型（替代已移除的 LR）。"""
        X, y = self._make_data()
        try:
            model, scaler, le, X_test, y_test, y_pred = train_model(X, y, model_type="xgb")
            from sklearn.metrics import accuracy_score
            acc = accuracy_score(y_test, y_pred)
            assert acc > 0.5
        except ImportError:
            pass  # XGBoost 未安装时跳过

    def test_model_types(self):
        """验证模型对象类型。"""
        X, y = self._make_data()
        model, scaler, le, X_test, y_test, y_pred = train_model(X, y, model_type="rf")
        assert isinstance(model, RandomForestClassifier)
        assert isinstance(scaler, StandardScaler)

    def test_label_encoder(self):
        """验证标签编码器。"""
        X, y = self._make_data()
        model, scaler, le, X_test, y_test, y_pred = train_model(X, y, model_type="rf")
        assert set(le.classes_) == {"Attack", "Normal"}