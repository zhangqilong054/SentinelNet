"""services/model_service.py — 模型训练 / 加载 / 注册表业务用例。

合并原 bp_model.py 的训练、加载、删除、列表功能。

依赖注入：
- RuntimeState: train_running / train_progress / train_status
- DualDetector: load_model / start_ml_loop
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
from pathlib import Path
from typing import Any

from campus_ids.config import MODELS_DIR, TRAFFIC_CSV

logger = logging.getLogger(__name__)


class ModelService:
    """模型服务 — 管理模型训练、加载和注册表。

    通过 RuntimeState 管理训练状态，通过注入的依赖访问共享资源。
    """

    def __init__(
        self,
        state: Any,  # RuntimeState
        dual_detector: Any | None = None,  # DualDetector
    ) -> None:
        self._state = state
        self._dual_detector = dual_detector
        self._train_thread: threading.Thread | None = None
        self._train_lock = threading.Lock()

    def train(self, dataset: str = "auto", quick: bool = False) -> dict:
        """启动模型训练（后台线程）。

        对应旧实现: helpers._auto_worker Step2 / model/train.py train()
        """
        with self._train_lock:
            if self._state.train_running:
                return {"status": "already_running"}
            self._state.train_running = True
            self._state.train_status = "training"
            self._state.train_progress = 0.0

        def _train_worker():
            try:
                from campus_ids.model.train import train
                self._state.train_status = "training"
                self._state.train_progress = 0.1
                result = train(dataset_type=dataset, quick=quick)
                self._state.train_status = "completed"
                self._state.train_progress = 1.0
                logger.info("模型训练完成")
            except Exception as exc:
                logger.error("模型训练失败: %s", exc)
                self._state.train_status = "failed"
                self._state.train_progress = 0.0
            finally:
                self._state.train_running = False

        self._train_thread = threading.Thread(target=_train_worker, daemon=True)
        self._train_thread.start()
        return {"status": "started"}

    def train_status(self) -> dict:
        """查询训练状态。"""
        return {
            "running": self._state.train_running,
            "status": self._state.train_status,
            "progress": self._state.train_progress,
        }

    def list_models(self) -> list[dict]:
        """列出已训练模型。

        对应旧实现: web_new/api/models.py _read_registry()
        """
        registry_path = MODELS_DIR / "registry.json"
        if not registry_path.exists():
            return []
        try:
            return json.loads(registry_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("读取模型注册表失败: %s", exc)
            return []

    def delete_model(self, model_id: str) -> dict:
        """删除指定模型。

        对应旧实现: web_new/api/models.py delete_model()
        """
        runs = self.list_models()
        target_run = None
        for run in runs:
            if run.get("run_id") == model_id:
                target_run = run
                break

        if target_run is None:
            return {"status": "error", "message": f"模型 '{model_id}' 不存在"}

        # 删除 run 目录
        run_dir = target_run.get("run_dir")
        if run_dir:
            run_path = Path(run_dir)
            if run_path.exists():
                shutil.rmtree(run_path, ignore_errors=True)
                logger.info("已删除模型目录: %s", run_dir)

        # 从 registry.json 中移除
        runs = [r for r in runs if r.get("run_id") != model_id]
        registry_path = MODELS_DIR / "registry.json"
        try:
            registry_path.write_text(
                json.dumps(runs, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:
            logger.error("更新注册表失败: %s", exc)

        return {"status": "deleted", "model_id": model_id}