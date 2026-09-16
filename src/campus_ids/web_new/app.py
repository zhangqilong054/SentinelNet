"""web_new/app.py — FastAPI 应用工厂。

消除 import 期副作用（原 Flask app.py 的 init_db() / DB 配置覆盖在导入时执行）。
所有初始化在 create_app() 中显式执行。

ADR-0001 §4.1 约束：单 worker，违反即故障。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from campus_ids.runtime.db import init_db, get_connection
from campus_ids.runtime.events import EventBus
from campus_ids.runtime.repositories import ConfigRepository, UserRepository
from campus_ids.runtime.settings import Settings, get_settings, reset_settings
from campus_ids.runtime.state import RuntimeState
from campus_ids.runtime.tasks import Task, TaskKind, TaskRegistry
from campus_ids.web_new.errors import register_exception_handlers
from campus_ids.web_new.security import limiter

logger = logging.getLogger(__name__)


def _register_default_tasks(registry: TaskRegistry) -> None:
    """注册默认任务描述符（阶段1空壳，阶段2接入 services/）。"""
    tasks = [
        Task("capture", TaskKind.CONTINUOUS, description="基础抓包"),
        Task("capture_full", TaskKind.TIMED, default_duration=60, description="增强抓包（18维流特征+TLS）"),
        Task("detection", TaskKind.CONTINUOUS, description="检测节拍"),
        Task("ml", TaskKind.CONTINUOUS, description="ML 引擎"),
        Task("attack", TaskKind.TIMED, default_duration=30, description="攻击模拟"),
        Task("train", TaskKind.TIMED, default_duration=120, description="模型训练"),
        Task("auto", TaskKind.TIMED, default_duration=180, description="一键全流程"),
        Task("demo", TaskKind.TIMED, default_duration=60, description="一键演示"),
    ]
    registry.register_many(tasks)


# ── 安全响应头中间件 ──────────────────────────────────────────────

class SecurityHeadersMiddleware:
    """添加安全响应头（ADR-0001 §6 #3，T1.11 ③）。

    使用 secure 库 BALANCED 预设，取代自实现硬编码头列表。
    补齐 HSTS / Permissions-Policy / COOP / CORP，移除已弃用的
    X-XSS-Protection。相对旧版 Flask-Talisman 不再是功能回退。
    """

    _SECURE_HEADERS: dict[bytes, bytes] | None = None

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        # 延迟初始化：首次实例化时从 secure 库构建头映射
        if SecurityHeadersMiddleware._SECURE_HEADERS is None:
            from secure import Secure, Preset

            secure_obj = Secure.from_preset(Preset.BALANCED)
            SecurityHeadersMiddleware._SECURE_HEADERS = {
                k.lower().encode(): v.encode()
                for k, v in secure_obj.header_items()
            }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                for key, value in SecurityHeadersMiddleware._SECURE_HEADERS.items():
                    if key not in headers:
                        headers[key] = value
                message["headers"] = list(headers.items())
            await send(message)

        await self.app(scope, receive, send_with_headers)



@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期 — 启动时初始化，关闭时清理。"""
    settings = get_settings()

    # 初始化数据库（ADR-0001 §5.1）
    init_db()
    logger.info("数据库初始化完成")

    # 加载 DB 配置覆盖到 Settings
    with get_connection() as conn:
        db_config = ConfigRepository.get_all(conn)
        if db_config:
            for key, value in db_config.items():
                if hasattr(settings, key):
                    try:
                        # 尝试类型转换
                        field_type = type(getattr(settings, key))
                        setattr(settings, key, field_type(value))
                    except (ValueError, TypeError):
                        logger.warning("忽略无效配置 %s=%s", key, value)
            logger.info("DB 配置覆盖加载完成 (%d 项)", len(db_config))

        # 确保默认管理员用户
        UserRepository.ensure_default(
            conn, username="admin", password_hash=settings.api_token or ""
        )

    # 初始化运行时状态
    state: RuntimeState = app.state.runtime_state
    state.event_bus = EventBus()

    # 初始化任务注册表
    registry = TaskRegistry()
    _register_default_tasks(registry)
    app.state.task_registry = registry

    # 初始化剧本服务
    from campus_ids.services.scenario_service import ScenarioService
    app.state.scenario_service = ScenarioService(registry)

    logger.info("SentinelNet FastAPI 应用启动 (port=%d)", settings.web_port)

    yield

    # 关闭：停止所有运行中的任务
    for name in registry.registered_names:
        status = registry.status(name)
        if status.get("status") == "running":
            registry.stop(name)
            logger.info("关闭时停止任务: %s", name)

    logger.info("SentinelNet FastAPI 应用关闭")


def _assert_single_worker() -> None:
    """断言单 worker 约束（ADR-0001 §4.1）。

    检查两个入口：
    1. 环境变量 WEB_CONCURRENCY > 1
    2. CLI 参数 --workers > 1（Uvicorn 命令行）

    非数字值给出明确错误而非 ValueError。
    """
    import sys

    # 1. 检查 WEB_CONCURRENCY 环境变量
    web_concurrency = os.environ.get("WEB_CONCURRENCY", "")
    if web_concurrency:
        try:
            if int(web_concurrency) > 1:
                raise RuntimeError(
                    f"WEB_CONCURRENCY={web_concurrency}，违反单 worker 约束（ADR-0001 §4.1）。"
                    "运行时状态全部驻留进程内，多 worker 会导致状态分裂。"
                    "请移除 WEB_CONCURRENCY 或设为 1。"
                )
        except ValueError:
            raise RuntimeError(
                f"WEB_CONCURRENCY={web_concurrency!r} 不是有效整数，违反单 worker 约束（ADR-0001 §4.1）。"
                "请移除 WEB_CONCURRENCY 或设为 1。"
            )

    # 2. 检查 CLI --workers 参数
    argv = sys.argv or []
    for i, arg in enumerate(argv):
        if arg == "--workers" and i + 1 < len(argv):
            try:
                if int(argv[i + 1]) > 1:
                    raise RuntimeError(
                        f"--workers={argv[i + 1]}，违反单 worker 约束（ADR-0001 §4.1）。"
                        "运行时状态全部驻留进程内，多 worker 会导致状态分裂。"
                        "请移除 --workers 参数或设为 1。"
                    )
            except ValueError:
                raise RuntimeError(
                    f"--workers={argv[i + 1]!r} 不是有效整数，违反单 worker 约束（ADR-0001 §4.1）。"
                    "请移除 --workers 参数或设为 1。"
                )
        # 处理 --workers=N 形式
        if arg.startswith("--workers="):
            val = arg.split("=", 1)[1]
            try:
                if int(val) > 1:
                    raise RuntimeError(
                        f"--workers={val}，违反单 worker 约束（ADR-0001 §4.1）。"
                        "运行时状态全部驻留进程内，多 worker 会导致状态分裂。"
                        "请移除 --workers 参数或设为 1。"
                    )
            except ValueError:
                raise RuntimeError(
                    f"--workers={val!r} 不是有效整数，违反单 worker 约束（ADR-0001 §4.1）。"
                    "请移除 --workers 参数或设为 1。"
                )


# ── 已知不安全的默认密钥 ──────────────────────────────────────────
_INSECURE_SECRET_KEYS = frozenset({
    "change-me-in-production",
    "sentinelnet-dev-secret-key-change-in-prod",
})


def _assert_secret_key(settings: Settings) -> None:
    """断言 secret_key 不是公开默认值（ADR-0001 §6.1 安全要求）。

    生产模式（debug=False）：使用公开默认密钥 → 拒绝启动。
    开发模式（debug=True）：使用公开默认密钥 → 打印 WARNING。
    """
    if settings.secret_key not in _INSECURE_SECRET_KEYS:
        return  # 密钥已自定义，安全

    if settings.debug:
        logger.warning(
            "⚠️  secret_key 仍为公开默认值 '%s'，仅限开发环境使用！"
            "生产环境请设置 CAMPUS_IDS_SECRET_KEY 环境变量。",
            settings.secret_key,
        )
    else:
        raise RuntimeError(
            f"secret_key 为公开默认值 '{settings.secret_key}'，拒绝启动。"
            "CSRF 签名和会话 cookie 可被伪造，存在严重安全风险。"
            "请设置 CAMPUS_IDS_SECRET_KEY 环境变量，"
            "或在开发环境设置 CAMPUS_IDS_DEBUG=1。"
        )


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例。

    约束（ADR-0001 §4.1）：
    - 单 worker：检测到 WEB_CONCURRENCY>1 或 CLI --workers>1 时拒绝启动
    - secret_key：不得使用公开默认值（生产模式拒绝启动）
    - 所有初始化在此函数内完成，import 不产生副作用
    """
    # ── 单 worker 断言 ──────────────────────────────────────────
    _assert_single_worker()

    # ── secret_key 安全断言 ─────────────────────────────────────
    settings = get_settings()
    _assert_secret_key(settings)

    app = FastAPI(
        title="SentinelNet",
        description="校园网加密流量入侵检测系统",
        version="0.2.0",
        lifespan=lifespan,
    )

    # 注入运行时状态
    app.state.runtime_state = RuntimeState()
    app.state.settings = settings

    # ── 中间件栈（按添加顺序的逆序执行，即最后添加的最先执行）────────
    # 1. CORS（ADR-0001 §6 #5）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. 会话中间件（ADR-0001 §6 #1）
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="campus_ids_session",
        same_site="lax",
        https_only=False,  # 开发环境；生产环境应启用
    )

    # 3. 安全响应头（ADR-0001 §6 #3）
    app.add_middleware(SecurityHeadersMiddleware)

    # ── 限流（ADR-0001 §6 #4）─────────────────────────────────────
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)  # 必须注册中间件，否则 @limiter.limit 不生效
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # ── 注册路由 ──────────────────────────────────────────────
    from campus_ids.web_new.api import system, traffic, alerts, tasks, models, scenarios, tls, payload

    app.include_router(system.router, tags=["system"])
    app.include_router(traffic.router, tags=["traffic"])
    app.include_router(alerts.router, tags=["alerts"])
    app.include_router(tasks.router, tags=["tasks"])
    app.include_router(models.router, tags=["models"])
    app.include_router(scenarios.router, tags=["scenarios"])
    app.include_router(tls.router, tags=["tls"])
    app.include_router(payload.router, tags=["payload"])

    # ── 注册全局异常处理器 ──────────────────────────────────────
    register_exception_handlers(app)

    return app