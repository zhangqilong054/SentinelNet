"""web_new/api/models.py — 模型管理 API 路由。

端点：
- GET   /api/models                模型列表（从 registry.json 读取）
- POST  /api/models/train          启动训练（委托 TaskRegistry 的 train 任务）
- GET   /api/models/train/status   训练状态（读 TaskRegistry，单一真相源）
- DELETE /api/models/{name}        删除模型

范围边界（D4）：只读注册表与产物列表，不重构训练链路内部。

## T2.13 收敛记录（2026-09-17）

**问题**：本模块原先自建 `_train_status` 模块级状态机 + 独立 daemon 线程，
与 `TaskRegistry` 的 `train` 任务**互不感知** —— 从
`POST /api/tasks/train/start` 起训练，`GET /api/models/train/status` 仍报 `idle`。
训练状态有两份真相，且 `/api/models/train` 会覆盖三个产物却
**无确认、无 dry-run、无备份**。

**现在**：
1. 状态单一真相源 = `TaskRegistry.status("train")`；
   `progress` 由任务窗口 elapsed/duration **算出**，不是硬编码 0.0。
2. `POST /api/models/train` 不再自己起线程，而是 `registry.start("train", ...)`，
   并透传 dataset/quick（`app.py` 的 `train_target` 已支持）。
3. 破坏性保护：必须 `confirm=true`；启动前自动把
   `model.pkl` / `evaluation_report.txt` / `confusion_matrix.png`
   备份到 `<data_dir>/training_backup/<时间戳>/`。
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request

from campus_ids.runtime.settings import get_settings
from campus_ids.web_new.errors import ApiError, ConflictError, NotFoundError, ValidationError
from campus_ids.web_new.security import Readonly, Write, limiter
from campus_ids.web_new.schemas import (
    MessageResponse,
    ModelInfoResponse,
    ModelListResponse,
    TrainRequest,
    TrainStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["models"])

# 训练会覆盖的产物（相对 data_dir）
TRAINING_PRODUCTS: tuple[str, ...] = (
    "model.pkl",
    "evaluation_report.txt",
    "confusion_matrix.png",
)

# 训练任务在 TaskRegistry 里的名字
TRAIN_TASK = "train"

# `epochs <= QUICK_EPOCH_THRESHOLD` 时按 quick 模式训练（沿用旧端点语义）
QUICK_EPOCH_THRESHOLD = 3


def _get_models_dir() -> Path:
    """获取 models 目录路径。"""
    settings = get_settings()
    return settings.data_dir / "models"


def _read_registry() -> list[dict]:
    """读取 registry.json 中的模型列表。"""
    registry_path = _get_models_dir() / "registry.json"
    if not registry_path.exists():
        return []
    try:
        return json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("读取模型注册表失败: %s", exc)
        return []


def _task_registry(request: Request):
    """取应用级任务注册表；未初始化时抛 503（lifespan 未跑）。"""
    registry = getattr(request.app.state, "task_registry", None)
    if registry is None:
        raise ApiError(
            error_code="TASK_REGISTRY_UNAVAILABLE",
            detail="任务注册表未初始化（应用 lifespan 未执行）",
            status_code=503,
        )
    return registry


def backup_training_products() -> Path | None:
    """训练前备份会被覆盖的产物。

    Returns:
        备份目录；若三个产物都不存在则返回 None（首次训练，无物可备）。

    ⚠️ 只备份**存在**的文件，且不删除任何旧备份 —— 备份目录按时间戳分层，
    多次训练不会互相覆盖。
    """
    data_dir = get_settings().data_dir
    existing = [data_dir / name for name in TRAINING_PRODUCTS if (data_dir / name).exists()]
    if not existing:
        logger.info("训练产物均不存在，跳过备份（首次训练）")
        return None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = data_dir / "training_backup"
    # 时间戳只到秒 —— 同一秒内第二次备份会撞名并**覆盖**上一次的备份，
    # 与下面"多次训练不会互相覆盖"的承诺矛盾。追加序号消除这个窗口。
    target = backup_root / stamp
    suffix = 1
    while target.exists():
        suffix += 1
        target = backup_root / f"{stamp}-{suffix}"
    target.mkdir(parents=True, exist_ok=True)
    for src in existing:
        shutil.copy2(src, target / src.name)
    logger.info("训练前已备份 %d 个产物到 %s", len(existing), target)
    return target


# ── GET /api/models ────────────────────────────────────────────────

@router.get("/models", dependencies=[Readonly], summary="获取可用模型列表")
async def list_models(request: Request) -> ModelListResponse:
    """获取可用模型列表 — 从 registry.json 读取。"""
    runs = _read_registry()
    models = []
    for run in runs:
        models.append(ModelInfoResponse(
            name=run.get("run_id", "unknown"),
            version=run.get("model_type", "unknown"),
            created_at=run.get("created_at", ""),
            metrics=run.get("metrics", {}),
        ))
    return ModelListResponse(models=models)


# ── POST /api/models/train ─────────────────────────────────────────

@router.post("/models/train", dependencies=[Write], summary="启动模型训练")
@limiter.limit("30/minute")
async def start_training(body: TrainRequest, request: Request) -> MessageResponse:
    """启动模型训练 —— 委托 `TaskRegistry` 的 `train` 任务（T2.13）。

    三步保护：
    1. `confirm=true` 必填，否则 400 且不启动；
    2. 已在运行时返回 409（判据来自 TaskRegistry，不是本地状态机）；
    3. 启动前备份三个产物到 `<data_dir>/training_backup/<时间戳>/`。

    `epochs` 仅用于判定 quick 模式（≤3），不直接等于训练轮数 ——
    训练链路的轮数由数据集配置决定（D4：不重构训练链路内部）。
    """
    registry = _task_registry(request)

    if not body.confirm:
        raise ValidationError(
            "训练会覆盖 model.pkl / evaluation_report.txt / confusion_matrix.png，"
            "需显式确认：请在请求体传 confirm=true"
        )

    try:
        current = registry.status(TRAIN_TASK)
    except KeyError as exc:
        raise NotFoundError("训练任务", TRAIN_TASK) from exc

    if current.get("status") == "running":
        raise ConflictError("模型训练正在进行中")

    backup_dir = backup_training_products()
    quick = body.epochs <= QUICK_EPOCH_THRESHOLD

    result = registry.start(TRAIN_TASK, dataset=body.dataset, quick=quick)
    state = result.get("status")
    if state == "error":
        raise ApiError(
            error_code="TRAIN_START_FAILED",
            detail=result.get("message", "训练任务启动失败"),
            status_code=503,
        )
    if state == "already_running":
        raise ConflictError("模型训练正在进行中")

    message = f"训练已启动: dataset={body.dataset}, quick={quick}"
    if backup_dir is not None:
        message += f"；产物已备份至 {backup_dir}"
    logger.info("模型训练已启动: dataset=%s quick=%s backup=%s", body.dataset, quick, backup_dir)
    return MessageResponse(message=message)


# ── GET /api/models/train/status ───────────────────────────────────

@router.get("/models/train/status", dependencies=[Readonly], summary="获取训练状态")
async def get_train_status(request: Request) -> TrainStatusResponse:
    """获取训练状态 —— 真相源是 `TaskRegistry`（T2.13）。

    状态映射（TaskRegistry → 本响应）：
    `idle`→`idle`、`running`→`training`、`finished`→`completed`、`failed`→`failed`。

    `progress` 由任务窗口 elapsed/duration 算出而非硬编码：
    训练任务在本架构里是 TIMED 任务（窗口时长 = `default_duration`，默认 120s），
    所以它是"任务窗口进度"而不是"训练收敛进度" —— 训练链路内部进度需要
    改 `model/train.py` 才能拿到，属 D4 范围外。
    """
    registry = _task_registry(request)

    try:
        info = registry.status(TRAIN_TASK)
    except KeyError as exc:
        raise NotFoundError("训练任务", TRAIN_TASK) from exc

    raw_status = info.get("status", "idle")
    mapping = {
        "idle": "idle",
        "running": "training",
        "stopping": "training",
        "finished": "completed",
        "failed": "failed",
    }
    status = mapping.get(raw_status, "idle")

    elapsed = info.get("elapsed")
    # 优先用实际窗口时长（`duration`），回退到 default_duration 兼容旧返回
    duration = info.get("duration") or info.get("default_duration")
    progress = 0.0
    if status == "training" and elapsed is not None and duration:
        progress = min(round(float(elapsed) / float(duration), 4), 1.0)
    elif status == "completed":
        progress = 1.0

    return TrainStatusResponse(
        status=status,
        progress=progress,
        epoch=0,
        total_epochs=0,
        error=info.get("error"),
        elapsed_seconds=float(elapsed) if elapsed is not None else None,
        duration_seconds=int(duration) if duration is not None else None,
    )


# ── DELETE /api/models/{name} ──────────────────────────────────────

@router.delete("/models/{name}", dependencies=[Write], summary="删除指定模型")
@limiter.limit("30/minute")
async def delete_model(name: str, request: Request) -> MessageResponse:
    """删除指定模型 — 从 registry.json 移除并删除 run 目录。"""
    runs = _read_registry()
    target_run = None
    for run in runs:
        if run.get("run_id") == name:
            target_run = run
            break

    if target_run is None:
        raise NotFoundError(f"模型 '{name}' 不存在")

    # 删除 run 目录
    run_dir = target_run.get("run_dir")
    if run_dir:
        run_path = Path(run_dir)
        if run_path.exists():
            import shutil
            shutil.rmtree(run_path, ignore_errors=True)
            logger.info("已删除模型目录: %s", run_dir)

    # 从 registry.json 中移除
    runs = [r for r in runs if r.get("run_id") != name]
    registry_path = _get_models_dir() / "registry.json"
    try:
        registry_path.write_text(json.dumps(runs, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.error("更新注册表失败: %s", exc)

    return MessageResponse(message=f"模型 '{name}' 已删除")