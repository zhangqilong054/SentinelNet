"""web_new/app.py — FastAPI 应用工厂。

消除 import 期副作用（原 Flask app.py 的 init_db() / DB 配置覆盖在导入时执行）。
所有初始化在 create_app() 中显式执行。

ADR-0001 §4.1 约束：单 worker，违反即故障。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import FileResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from campus_ids.runtime.db import init_db, get_connection
from campus_ids.runtime.events import EventBus
from campus_ids.runtime.repositories import ConfigRepository, UserRepository
from campus_ids.runtime.settings import Settings, get_settings
from campus_ids.runtime.state import RuntimeState
from campus_ids.runtime.tasks import Task, TaskKind, TaskRegistry
from campus_ids.web_new.errors import register_exception_handlers
from campus_ids.web_new.security import limiter

logger = logging.getLogger(__name__)


def _register_default_tasks(registry: TaskRegistry, *, capture_service, detection_service,
                             model_service, attack_simulator, scenario_service) -> None:
    """注册默认任务描述符并接入工作函数（P0-1 编排域接线）。"""
    import threading

    # ── capture: 基础抓包（CONTINUOUS） ──────────────────────────
    def capture_target(stop_event: threading.Event, **kwargs) -> None:
        capture_service.start_capture()
        stop_event.wait()
        capture_service.stop_capture()

    # ── capture_full: 增强抓包（TIMED） ──────────────────────────
    def capture_full_target(stop_event: threading.Event, duration: int = 60, **kwargs) -> None:
        capture_service.start_enhanced(duration=duration)
        stop_event.wait()

    # ── detection: 检测节拍（CONTINUOUS） ──────────────────────────
    def detection_target(stop_event: threading.Event, **kwargs) -> None:
        detection_service.start_detection()
        stop_event.wait()
        detection_service.stop_detection()

    # ── ml: ML 引擎（CONTINUOUS） ──────────────────────────────────
    def ml_target(stop_event: threading.Event, **kwargs) -> None:
        detection_service.load_ml()
        stop_event.wait()
        detection_service.stop_ml()

    # ── attack: 攻击模拟（TIMED） ──────────────────────────────────
    def attack_target(stop_event: threading.Event, duration: int = 30, **kwargs) -> None:
        attack_simulator.start_all(duration=duration)
        stop_event.wait()
        attack_simulator.stop()

    # ── train: 模型训练（TIMED） ──────────────────────────────────
    def train_target(stop_event: threading.Event, duration: int = 120, **kwargs) -> None:
        # T2.13：透传 dataset / quick，否则 `POST /api/models/train` 的入参
        # 会被静默丢弃（旧端点收 dataset_type/quick，新 schema 是 dataset/epochs）。
        forwarded = {k: v for k, v in kwargs.items() if k in ("dataset", "quick")}
        model_service.train(**forwarded)

    # ── auto: 一键全流程（TIMED）— 委托 ScenarioService("full") ──
    def auto_target(stop_event: threading.Event, duration: int = 180, **kwargs) -> None:
        scenario_service.start_scenario("full", duration=duration)
        stop_event.wait()
        scenario_service.stop_scenario("full")

    # ── demo: 一键演示（TIMED）— 委托 ScenarioService("demo") ────
    def demo_target(stop_event: threading.Event, duration: int = 60, **kwargs) -> None:
        scenario_service.start_scenario("demo", duration=duration)
        stop_event.wait()
        scenario_service.stop_scenario("demo")

    tasks = [
        Task("capture", TaskKind.CONTINUOUS, target=capture_target, description="基础抓包"),
        Task("capture_full", TaskKind.TIMED, target=capture_full_target, default_duration=60, description="增强抓包（18维流特征+TLS）"),
        Task("detection", TaskKind.CONTINUOUS, target=detection_target, description="检测节拍"),
        Task("ml", TaskKind.CONTINUOUS, target=ml_target, description="ML 引擎"),
        Task("attack", TaskKind.TIMED, target=attack_target, default_duration=30, description="攻击模拟"),
        Task("train", TaskKind.TIMED, target=train_target, default_duration=120, description="模型训练"),
        Task("auto", TaskKind.TIMED, target=auto_target, default_duration=180, description="一键全流程"),
        Task("demo", TaskKind.TIMED, target=demo_target, default_duration=60, description="一键演示"),
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

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                secure_headers = SecurityHeadersMiddleware._SECURE_HEADERS or {}
                for key, value in secure_headers.items():
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

        # 确保默认管理员用户。
        # bootstrap 密码取 CAMPUS_IDS_API_TOKEN 的值（首次部署由部署方设定），
        # **必须哈希后写入** —— 直接写裸 token 会让 verify_password 拿到非哈希串，
        # werkzeug 解析失败抛 ValueError → 登录端点 500（认证失败应当是 401）。
        # api_token 未配置时写空哈希：verify_password 对空哈希恒为 False，
        # 即"没有任何密码可登录"，与"认证关闭"的语义一致。
        from campus_ids.web_new.auth import hash_password
        bootstrap_hash = hash_password(settings.api_token) if settings.api_token else ""
        UserRepository.ensure_default(
            conn, username="admin", password_hash=bootstrap_hash
        )

    # 初始化运行时状态
    state: RuntimeState = app.state.runtime_state
    state.event_bus = EventBus()

    # 初始化任务注册表
    registry = TaskRegistry()
    app.state.task_registry = registry

    # 初始化剧本服务
    from campus_ids.services.scenario_service import ScenarioService
    scenario_service = ScenarioService(registry)
    app.state.scenario_service = scenario_service

    # 初始化流量服务
    from campus_ids.services.traffic_service import TrafficService
    app.state.traffic_service = TrafficService(state)

    # 初始化告警服务
    from campus_ids.services.alert_service import AlertService
    alert_service = AlertService(event_bus=state.event_bus)
    app.state.alert_service = alert_service

    # 初始化 TLS 分析器
    from campus_ids.capture.tls_analyzer import tls_analyzer
    app.state.tls_analyzer = tls_analyzer

    # 初始化双引擎检测器
    from campus_ids.detector.detector import create_rule_detector
    from campus_ids.detector.dual_detector import DualDetector
    rule_detector = create_rule_detector(
        ddos_threshold=settings.ddos_threshold,
        port_scan_threshold=settings.port_scan_threshold,
        syn_flood_threshold=settings.syn_flood_threshold,
        udp_flood_threshold=settings.udp_flood_threshold,
        brute_force_threshold=settings.brute_force_threshold,
        brute_force_window=settings.brute_force_window,
        lateral_movement_threshold=settings.lateral_movement_threshold,
    )
    dual_detector = DualDetector(rule_detector=rule_detector)
    app.state.rule_detector = rule_detector
    app.state.dual_detector = dual_detector

    # 初始化业务服务（P0-1 编排域接线）
    from campus_ids.services.capture_service import CaptureService
    from campus_ids.services.detection_service import DetectionService
    from campus_ids.services.model_service import ModelService
    from campus_ids.demo.attack_sim import AttackSimulator

    capture_service = CaptureService(
        state=state,
        dual_detector=dual_detector,
        tls_analyzer=tls_analyzer,
        alert_service=alert_service,
    )
    app.state.capture_service = capture_service

    detection_service = DetectionService(
        state=state,
        dual_detector=dual_detector,
        alert_service=alert_service,
        event_bus=state.event_bus,
        settings=settings,
    )
    app.state.detection_service = detection_service

    model_service = ModelService(state=state, dual_detector=dual_detector)
    app.state.model_service = model_service

    attack_simulator = AttackSimulator(packet_queue=state.packet_queue)
    app.state.attack_simulator = attack_simulator

    # 注册任务并接入工作函数
    _register_default_tasks(
        registry,
        capture_service=capture_service,
        detection_service=detection_service,
        model_service=model_service,
        attack_simulator=attack_simulator,
        scenario_service=scenario_service,
    )

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
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    # ── 注册路由 ──────────────────────────────────────────────
    from campus_ids.web_new.api import system, traffic, alerts, tasks, models, scenarios, tls, payload, stream, admin, auth_routes

    app.include_router(system.router, tags=["system"])
    app.include_router(traffic.router, tags=["traffic"])
    app.include_router(alerts.router, tags=["alerts"])
    app.include_router(tasks.router, tags=["tasks"])
    app.include_router(models.router, tags=["models"])
    app.include_router(scenarios.router, tags=["scenarios"])
    app.include_router(tls.router, tags=["tls"])
    app.include_router(payload.router, tags=["payload"])
    app.include_router(stream.router, tags=["stream"])
    app.include_router(admin.router, tags=["admin"])
    app.include_router(auth_routes.router, tags=["auth"])

    # ── 页面路由与静态资源（T3.1 新增前端模式切换）────────────────
    # settings.frontend=new → Vue3 SPA（frontend/dist/）
    # settings.frontend=legacy 或未设置 → 旧 Jinja2 模板（T2.17 pages）
    # 2026-09-19 起从 Settings 读取（支持 .env）；此前直接 os.environ.get，
    # 写进 .env 的 CAMPUS_IDS_FRONTEND 会被静默忽略。
    from fastapi.staticfiles import StaticFiles
    from campus_ids.web_new import pages

    frontend_mode = (settings.frontend or "legacy").lower()

    if frontend_mode == "new":
        # ── Vue3 SPA 模式 ──────────────────────────────────────
        # 查找前端构建产物：优先项目根目录 frontend/dist/，其次 /app/frontend/dist/
        # 注意：app.py 位于 src/campus_ids/web_new/，parents[3] 才是项目根
        # （parents[2] 是 src/，曾导致拼出 src/frontend/dist 静默回退 legacy）
        _candidates = [
            Path(__file__).resolve().parents[3] / "frontend" / "dist",
            Path("/app/frontend/dist"),
        ]
        _frontend_dist = next((p for p in _candidates if p.exists()), None)
        if _frontend_dist is not None:
            app.mount(
                "/assets",
                StaticFiles(directory=str(_frontend_dist / "assets")),
                name="frontend-assets",
            )

            # GET /logout 守卫（2026-09-19）：与 legacy 口径对齐。
            # 此前 SPA 回退会把它吃掉返回 200 index.html，登出语义丢失；
            # 实际登出走 POST /api/logout，这里只拦 GET（防跨站强制登出）。
            # 必须注册在下方 /{path:path} 回退之前才能优先生效。
            from fastapi.responses import JSONResponse

            @app.get("/logout", include_in_schema=False)
            def spa_logout_guard() -> JSONResponse:
                return JSONResponse(
                    {"detail": "Method Not Allowed（登出走 POST /api/logout）"},
                    status_code=405,
                )

            # SPA 回退：所有非 API/非静态路由返回 index.html
            _index_html = _frontend_dist / "index.html"

            @app.get("/{path:path}", include_in_schema=False)
            def spa_fallback(path: str) -> FileResponse:
                """SPA 回退路由 — Vue Router 使用 HTML5 History 模式。"""
                return FileResponse(str(_index_html), media_type="text/html")

            logger.info("前端 SPA 模式：从 %s 提供静态资源", _frontend_dist)
        else:
            # 显式失败：静默回退 legacy 会掩盖配置/构建错误（T3 审计 P0-1）
            _attempted = ", ".join(str(p) for p in _candidates)
            raise RuntimeError(
                "CAMPUS_IDS_FRONTEND=new 但未找到前端构建产物（尝试过: %s）。"
                "请先在 frontend/ 下执行 npm run build，或改回 legacy 模式。" % _attempted
            )

    if frontend_mode != "new":
        # ── Legacy 模式（T2.17 原始页面路由）────────────────────
        app.mount("/static", StaticFiles(directory=str(pages.STATIC_DIR)), name="static")
        app.include_router(pages.router)

    # ── 注册全局异常处理器 ──────────────────────────────────────
    register_exception_handlers(app)

    return app


# ── 首次启动引导（2026-09-19，可用性 P0-U1）──────────────────────

_ENV_TEMPLATE = """\
# SentinelNet 配置（由首次启动引导自动生成 —— {created_at}）
# 完整可配置项与说明见项目根目录 .env.example。
# 注意：修改本文件后需重启应用生效。

# 会话密钥（随机生成，泄露等同账号被接管；更换后所有已登录会话失效）
CAMPUS_IDS_SECRET_KEY={secret}
"""


def _ensure_env_file(env_path: Path | None = None) -> bool:
    """首次启动引导：无 .env 且未显式提供密钥时，自动生成含随机密钥的 .env。

    背景（可用性 P0）：非 DEBUG 模式 + 默认 secret_key 会被 create_app() 拒绝启动，
    而新环境既没有 .env 也没有环境变量 → 按操作手册执行 `python main.py app`
    直接崩溃。本引导让"开箱即启动"与"密钥安全防线"同时成立。

    幂等语义：
    - .env 已存在 → 不动（返回 False）；
    - 显式设置过 CAMPUS_IDS_SECRET_KEY 环境变量 → 不生成（用户已自行配置）；
    - 其余情况生成 .env 并写入随机密钥（返回 True）。

    同时把密钥写入 os.environ：Settings 单例可能在引导前已被物化
    （config.py 兼容层 import 期即调用 get_settings()），仅写文件救不了本次进程。

    Returns:
        是否新生成了 .env。
    """
    import secrets
    from datetime import datetime

    path = env_path if env_path is not None else Path.cwd() / ".env"
    if path.exists():
        return False
    if os.environ.get("CAMPUS_IDS_SECRET_KEY"):
        return False

    secret = secrets.token_hex(32)
    path.write_text(
        _ENV_TEMPLATE.format(
            secret=secret,
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
        encoding="utf-8",
    )
    # 兜底：当前进程内立即生效（不依赖 Settings 单例的物化时机）
    os.environ["CAMPUS_IDS_SECRET_KEY"] = secret
    logger.warning(
        "已生成配置文件 %s（含随机生成的 CAMPUS_IDS_SECRET_KEY）。"
        "首次启动引导完成；如需自定义端口/阈值等，参见 .env.example。",
        path,
    )
    return True


def run_app() -> None:
    """启动 SentinelNet Web 应用（uvicorn 单 worker）。

    取代旧 campus_ids.web.app.run_app()（Flask + Waitress）。
    ADR-0001 §4.1：强制单 worker，多 worker 启动会被 create_app() 拒绝。
    """
    import uvicorn

    # 首次启动引导必须在 get_settings() 之前执行：
    # 单例一旦物化，.env 的新增内容对本次进程不可见。
    _ensure_env_file()

    settings = get_settings()
    port = settings.web_port
    host = "0.0.0.0"

    logger.info("SentinelNet 监控系统启动（FastAPI + uvicorn）")
    logger.info("访问地址: http://localhost:%s", port)

    uvicorn.run(
        "campus_ids.web_new.app:create_app",
        host=host,
        port=port,
        workers=1,
        log_level="info",
        factory=True,
    )