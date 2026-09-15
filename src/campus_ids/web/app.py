"""Flask Web 监控面板 — 应用入口与蓝图注册。"""
from __future__ import annotations

import logging
import os
import signal as _signal

from flask import Flask, render_template, jsonify, request

from campus_ids.config import AUTH_ENABLED
from campus_ids.web.helpers import (
    CONFIG, dual_detector,
    stop_capture_thread,
)
from campus_ids.web.sse import _broadcast_sse
from campus_ids.web.utils import _check_auth
import campus_ids.web.utils as _utils
import campus_ids.web.helpers as _helpers_module

logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── M6: Flasgger Swagger 文档 ────────────────────────────────────────
try:
    from flasgger import Swagger
    _swagger = Swagger(app, template={
        "info": {
            "title": "SentinelNet API",
            "version": "1.0",
            "description": "校园网加密流量入侵检测系统 — REST API 文档",
        },
        "basePath": "/",
        "tags": [
            {"name": "流量监控", "description": "实时流量统计与监控"},
            {"name": "告警", "description": "入侵检测告警查询"},
            {"name": "配置", "description": "系统配置管理"},
            {"name": "数据管理", "description": "数据集与特征管理"},
            {"name": "抓包控制", "description": "网络抓包启停与状态"},
            {"name": "TLS分析", "description": "TLS 加密流量分析"},
            {"name": "双引擎检测", "description": "规则引擎 + ML 双引擎检测"},
            {"name": "载荷检测", "description": "恶意载荷深度检测"},
            {"name": "SSE实时推送", "description": "Server-Sent Events 实时推送"},
            {"name": "攻击模拟", "description": "攻击模拟与演示"},
            {"name": "模型管理", "description": "ML 模型训练与管理"},
            {"name": "演示模式", "description": "演示模式控制"},
            {"name": "增强抓包", "description": "增强版抓包（18维流特征 + TLS 分析）"},
            {"name": "一键全流程", "description": "一键全流程：抓包 → 训练 → 检测"},
            {"name": "环境自检", "description": "运行环境检查与诊断"},
            {"name": "运维", "description": "系统运维与健康检查"},
        ],
    })
    logger.info("Flasgger Swagger 文档已初始化 → /apidocs/")
except ImportError:
    _swagger = None
    logger.warning("flasgger 未安装，Swagger 文档未启用")

# ── M4: 安全中间件初始化 ────────────────────────────────────────────

# Flask-CORS: 限制跨域来源
try:
    from flask_cors import CORS
    _cors_origins = os.environ.get("CAMPUS_IDS_CORS_ORIGINS", "").split(",")
    _cors_origins = [o.strip() for o in _cors_origins if o.strip()]
    CORS(app, origins=_cors_origins or None)  # None = 同源策略
    logger.info("Flask-CORS 已初始化")
except ImportError:
    logger.warning("flask-cors 未安装，CORS 保护未启用")

# Flask-Limiter: API 速率限制
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    _limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["60 per minute"],
        storage_uri="memory://",
    )
    logger.info("Flask-Limiter 已初始化 (60 次/分钟)")
except ImportError:
    _limiter = None
    logger.warning("flask-limiter 未安装，速率限制未启用")

# Flask-WTF: CSRF 保护
try:
    from flask_wtf.csrf import CSRFProtect
    _csrf = CSRFProtect(app)
    _utils._csrf = _csrf  # 供蓝图中的 @_csrf_exempt 使用
    logger.info("Flask-WTF CSRF 保护已初始化")
except ImportError:
    _csrf = None
    logger.warning("flask-wtf 未安装，CSRF 保护未启用")

# Flask-Talisman: 安全响应头
try:
    from flask_talisman import Talisman
    _talisman = Talisman(
        app,
        force_https=False,  # 开发环境不强制 HTTPS
        content_security_policy={
            'default-src': "'self'",
            'script-src': "'self' 'unsafe-inline' 'unsafe-eval'",
            'style-src': "'self' 'unsafe-inline'",
            'img-src': "'self' data:",
            'connect-src': "'self'",
        },
        strict_transport_security=True,
        frame_options='DENY',
    )
    logger.info("Flask-Talisman 安全头已初始化")
except ImportError:
    logger.warning("flask-talisman 未安装，安全响应头未启用")

# Flask-Login: 用户认证（可选启用）
# L6: 仅启动时读取一次 — Flask-Login 初始化不可运行时切换
_LOGIN_ENABLED = os.environ.get("CAMPUS_IDS_LOGIN_ENABLED", "0") == "1"
if _LOGIN_ENABLED:
    try:
        from campus_ids.web.auth import init_auth, login_required
        init_auth(app)
        logger.info("Flask-Login 用户认证已启用")
    except ImportError:
        _LOGIN_ENABLED = False
        logger.warning("flask-login 未安装，用户认证未启用")


# ── 全局请求钩子 ─────────────────────────────────────────────────────

@app.before_request
def _require_auth():
    """全局请求钩子：API 端点需要认证，HTML 页面免认证。"""
    # M4: Flask-Login 会话认证优先
    if _LOGIN_ENABLED:
        from flask_login import current_user
        # 登录页、静态资源和健康检查免认证
        if (request.path.startswith("/login") or request.path.startswith("/logout")
                or request.path.startswith("/static") or request.path == "/api/health"):
            return None
        if not current_user.is_authenticated:
            from flask import redirect, url_for
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized", "message": "请先登录"}), 401
            return redirect(url_for("auth.login"))
        return None

    # P2-10.5: API Token 认证（无 Flask-Login 时的降级方案）
    if not AUTH_ENABLED:
        return None
    if request.path == "/" or request.path.startswith("/static") or request.path == "/api/health":
        return None
    if request.path.startswith("/api/"):
        if not _check_auth():
            return jsonify({"error": "Unauthorized", "message": "需要有效的 API token"}), 401
    return None


# ── M3: 注册 SSE 广播回调 ────────────────────────────────────────────
def _on_alert_callback(alert_entry: dict) -> None:
    _broadcast_sse("alert", alert_entry)

def _on_traffic_callback(traffic_entry: dict) -> None:
    _broadcast_sse("traffic", traffic_entry)

_helpers_module._on_alert_callbacks.append(_on_alert_callback)
_helpers_module._on_traffic_callbacks.append(_on_traffic_callback)


# ── 蓝图注册 ─────────────────────────────────────────────────────────
from campus_ids.web.bp_monitor import bp_monitor
from campus_ids.web.bp_capture import bp_capture
from campus_ids.web.bp_model import bp_model
from campus_ids.web.bp_admin import bp_admin

app.register_blueprint(bp_monitor)
app.register_blueprint(bp_capture)
app.register_blueprint(bp_model)
app.register_blueprint(bp_admin)


# ── 首页路由 ─────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template('index.html')


# ── M6: 信号处理（优雅关闭） ────────────────────────────────────────

def _graceful_shutdown(signum, frame):
    """SIGTERM/SIGINT 优雅关闭：停止抓包和 ML 循环。"""
    import sys
    logger.info("收到信号 %d，开始优雅关闭…", signum)
    try:
        stop_capture_thread()
        if dual_detector.ml_running:
            dual_detector.stop_ml_loop()
        logger.info("优雅关闭完成")
    except Exception as e:
        logger.error("优雅关闭出错: %s", e)
    sys.exit(0)


_signal.signal(_signal.SIGTERM, _graceful_shutdown)
_signal.signal(_signal.SIGINT, _graceful_shutdown)


def run_app():
    """启动 Web 应用（生产模式使用 Waitress，开发模式使用 Flask 开发服务器）。"""
    import os
    port = CONFIG['port']
    host = '0.0.0.0'
    logger.info("SentinelNet 监控系统启动成功")
    logger.info("访问地址: http://localhost:%s", port)
    logger.info("刷新间隔: %sms", CONFIG['refresh_interval'])

    # 生产模式：Waitress（Windows 原生支持，无需 WSL）
    if os.environ.get('CAMPUS_IDS_DEV_MODE', '0') != '1':
        try:
            from waitress import serve
            logger.info("使用 Waitress 生产服务器")
            serve(app, host=host, port=port, _quiet=True)
        except ImportError:
            logger.warning("waitress 未安装，回退到 Flask 开发服务器（不推荐生产使用）")
            app.run(debug=False, port=port, host=host)