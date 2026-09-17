"""契约基线与回放（T2.18）。

- `baseline/` —— T0.3/T2.18 录制的旧应用响应样本（golden files）
- `mapping.py` —— 旧 → 新 端点映射表，有意差异必须带解释
- `replay.py` —— 回放与规格门禁的核心实现（CLI 与 pytest 共用）
"""
