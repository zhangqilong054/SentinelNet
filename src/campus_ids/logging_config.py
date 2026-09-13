"""P2-10.4: 日志系统增强 — 文件输出 + 级别过滤 + 检测结果持久化。

支持按模块分文件输出日志，便于查阅和排查问题。

用法:
    from campus_ids.logging_config import setup_logging
    setup_logging()  # 使用默认配置
    setup_logging(log_dir="logs", level="DEBUG")  # 自定义配置

日志文件说明:
    app.log          — 全量日志（所有模块）
    capture.log      — 流量捕获模块日志（campus_ids.capture）
    detector.log     — 入侵检测模块日志（campus_ids.detector）
    model.log        — 模型训练/评估模块日志（campus_ids.model）
    web.log          — Web面板模块日志（campus_ids.web）
    detections.jsonl — 检测告警专用（JSON Lines 格式）
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from campus_ids.config import LOG_DIR as _DEFAULT_LOG_DIR

# 默认日志级别
DEFAULT_LOG_LEVEL = os.environ.get("CAMPUS_IDS_LOG_LEVEL", "INFO").upper()

# 日志格式
CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"

# 检测结果日志（单独文件，JSON Lines 格式）
DETECTION_LOG_FILE = "detections.jsonl"

# ── 模块日志文件映射 ──────────────────────────────────────────
# 每个模块对应一个独立日志文件，便于按需查阅
MODULE_LOG_FILES: dict[str, str] = {
    "campus_ids.capture": "capture.log",
    "campus_ids.detector": "detector.log",
    "campus_ids.model": "model.log",
    "campus_ids.web": "web.log",
}

# 单个日志文件最大 10MB，保留 5 个备份
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5


class DetectionLogHandler(logging.Handler):
    """检测结果持久化处理器 — 将检测告警写入 JSON Lines 文件。

    每条告警一行 JSON，便于后续分析和统计。
    """

    def __init__(self, log_path: Path, max_bytes: int = LOG_MAX_BYTES, backup_count: int = LOG_BACKUP_COUNT):
        super().__init__(level=logging.WARNING)
        self._log_path = log_path
        self._rotating = RotatingFileHandler(
            log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            log_entry = {
                "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "module": record.module,
                "func": record.funcName,
                "line": record.lineno,
            }
            # 如果有额外字段（如 attack_type, level 等），追加
            if hasattr(record, "detection_data"):
                log_entry["detection"] = record.detection_data  # type: ignore[attr-defined]
            # 仅写入 JSON 行（不调用 self._rotating.handle 避免双写）
            self._rotating.stream.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            self._rotating.stream.flush()
        except Exception:
            self.handleError(record)


def _create_module_file_handler(log_dir: Path, log_file: str) -> RotatingFileHandler:
    """创建模块专用文件处理器。"""
    handler = RotatingFileHandler(
        log_dir / log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(FILE_FORMAT))
    return handler


def setup_logging(log_dir: Path | str | None = None, level: str | None = None) -> None:
    """配置全局日志系统。

    Args:
        log_dir: 日志文件目录（默认: logs/ 或 CAMPUS_IDS_LOG_DIR 环境变量）
        level: 日志级别（默认: INFO 或 CAMPUS_IDS_LOG_LEVEL 环境变量）
    """
    log_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
    level = (level or DEFAULT_LOG_LEVEL).upper()

    # 创建日志目录
    log_dir.mkdir(parents=True, exist_ok=True)

    # 根日志配置
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level, logging.INFO))

    # 清除已有处理器（避免重复）
    root_logger.handlers.clear()

    # 1. 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, level, logging.INFO))
    console_handler.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    root_logger.addHandler(console_handler)

    # 2. 全量文件处理器（按大小轮转，10MB x 5个备份）
    all_log_path = log_dir / "app.log"
    file_handler = RotatingFileHandler(
        all_log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)  # 文件记录所有级别
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
    root_logger.addHandler(file_handler)

    # 3. 按模块创建独立日志文件
    for module_name, log_file in MODULE_LOG_FILES.items():
        module_logger = logging.getLogger(module_name)
        module_logger.propagate = True  # 仍传播到 root，写入 app.log
        module_handler = _create_module_file_handler(log_dir, log_file)
        module_logger.addHandler(module_handler)

    # 4. 检测结果专用处理器（WARNING 及以上，JSON Lines 格式）
    detection_log_path = log_dir / DETECTION_LOG_FILE
    detection_handler = DetectionLogHandler(detection_log_path)
    detection_handler.setFormatter(logging.Formatter("%(message)s"))
    detector_logger = logging.getLogger("campus_ids.detector")
    detector_logger.addHandler(detection_handler)

    logging.getLogger(__name__).info("日志系统已初始化: 目录=%s, 级别=%s", log_dir, level)