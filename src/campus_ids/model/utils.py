"""模型工具函数 — 避免 train.py ↔ evaluation.py 循环导入的公共模块。

R-03: encode_class_weight() — 统一 class_weight 编码逻辑
R-06: clean_features() — 统一 inf/nan 清理逻辑
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder

logger = logging.getLogger(__name__)


def clean_features(X: pd.DataFrame) -> pd.DataFrame:
    """清理特征数据中的 inf/nan 值。

    R-06: 统一 inf/nan 清理逻辑，避免 6 处重复。

    Args:
        X: 特征 DataFrame，可能包含 inf/nan 值。

    Returns:
        清理后的 DataFrame（inf → nan → 0）。
    """
    return X.replace([np.inf, -np.inf], np.nan).fillna(0)


def encode_class_weight(
    class_weight_dict: dict | None,
    le: LabelEncoder,
) -> dict | str | None:
    """将 class_weight_dict 编码为模型可接受的格式。

    R-03: 统一 class_weight 编码逻辑，避免 train.py 和 evaluation.py 重复。

    Args:
        class_weight_dict: 原始类别权重字典（字符串键）。
            None → 返回 "balanced"（默认自动均衡）
            False → 返回 None（数据已均衡，不加权）
            dict → 返回编码后的整数键字典
        le: 已拟合的 LabelEncoder，用于将字符串类别名编码为整数。

    Returns:
        effective_cw: 可直接传给 sklearn 模型的 class_weight 参数。
    """
    if class_weight_dict is False:
        return None
    if class_weight_dict is not None and isinstance(class_weight_dict, dict):
        encoded_cw = {}
        for cls_name, weight in class_weight_dict.items():
            if cls_name in le.classes_:
                encoded_cw[le.transform([cls_name])[0]] = weight
            else:
                logger.warning("class_weight 中的类别 %r 不在标签中，已忽略", cls_name)
        return encoded_cw if encoded_cw else "balanced"
    return "balanced"