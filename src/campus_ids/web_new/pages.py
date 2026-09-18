"""web_new/pages.py — 服务端渲染页面路由（T2.17）。

背景：重写期新应用**完全不提供界面** —— `GET /`、`/login`、`/logout`、
`/change-password` 全部 404，`web_new/` 下既无 `templates/` 也无 `static/`。
本模块补上这一层，使新应用可以独立启动并被人使用。

路径与旧 Flask 保持一致（`/`、`/login`、`/logout`、`/change-password`），
但**逻辑适配新安全层**：

| 项 | 旧（Flask） | 新（本模块） |
|---|---|---|
| CSRF | Flask-WTF 的 `csrf_token()` + session | 双提交 cookie + 表单域 `csrf_token`（`verify_csrf_pair`） |
| 会话 | Flask-Login | Starlette `SessionMiddleware` + `login_user()` |
| 未登录访问受保护页 | `redirect(url_for("auth.login"))` | `302 /login?next=...` |
| `GET /logout` | 允许（可被跨站触发） | **405**，登出必须 `POST`（有意收窄，见下） |
| 提示消息 | `flash()` | 模板变量（`error` / `message`），无 session 依赖 |

⚠️ **两处有意的破坏性变更**（需同步 `docs/冒烟清单.md` / README）：
1. `GET /logout` 返回 405 —— 旧实现用 GET 登出，可被跨站强制登出；
   新实现只接受 `POST /logout` 且要求 CSRF 双提交。
2. 登录成功后默认跳转 `/`（`?next=` 仍然支持，但仅接受站内相对路径，防开放重定向）。

视图函数一律写**同步 `def`**（ADR-0001 §5：SSE 端点是 `async def` 的唯一例外）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from campus_ids.runtime.db import get_connection
from campus_ids.runtime.repositories import UserRepository
from campus_ids.runtime.settings import get_settings
from campus_ids.web_new.auth import (
    AuthenticationError,
    authenticate,
    get_current_user,
    hash_password,
    login_user,
    logout_user,
    verify_password,
)
from campus_ids.web_new.security import (
    CSRF_COOKIE_NAME,
    generate_csrf_token,
    limiter,
    verify_csrf_pair,
)

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = _BASE_DIR / "templates"
STATIC_DIR = _BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["pages"], include_in_schema=False)
"""页面路由**不进 OpenAPI 规格**。

页面返回 HTML 而非 JSON，混进规格会污染阶段 3 的 TS 类型生成
（T3.2 用 `app.openapi()` 生成 API 客户端）。这条由
`tests/test_pages.py::test_auth_pages_are_not_api_routes` 守着。
"""

# 登录页最小密码长度（与 api/auth_routes.ChangePasswordRequest 保持一致）
MIN_PASSWORD_LENGTH = 6


# ── 辅助 ──────────────────────────────────────────────────────────


def auth_required() -> bool:
    """当前配置下页面是否需要登录。

    与 `security._verify_bearer_token` 的判据保持完全一致：
    `auth_enabled=False` **或** `api_token` 为空 → 认证未启用 → 页面直接放行。
    两处判据必须同源，否则会出现"API 放行但页面拦人"的割裂。
    """
    settings = get_settings()
    return bool(settings.auth_enabled and settings.api_token)


def _safe_next(next_url: str) -> str:
    """只接受站内相对路径，防止开放重定向。"""
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


def _render(
    request: Request,
    template_name: str,
    *,
    status_code: int = 200,
    **context: object,
) -> Response:
    """渲染模板，并把**本次新生成**的 CSRF token 同时写进表单域与 cookie。

    每次 GET 页面都换一个新 token：旧 token 不需要在服务端留存状态，
    因此换发是零成本的（HMAC 签名自校验），但能缩短泄漏窗口。
    """
    token = generate_csrf_token()
    context.setdefault("csrf_token", token)
    context.setdefault("username", get_current_user(request))
    context.setdefault("auth_required", auth_required())
    response = templates.TemplateResponse(
        request, template_name, context, status_code=status_code
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # 开发环境；生产环境应通过配置启用
        path="/",
    )
    return response


# ── GET / ─────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse, summary="控制台首页")
def dashboard(request: Request) -> Response:
    """控制台首页（最小壳，依赖新 API）。

    未登录且认证已启用时重定向到登录页。首页本身**不渲染任何服务端数据**，
    而是由 `/static/js/app.js` 调用 `/api/health` 与 `/api/tasks` 填充
    —— 阶段 3 的前端重写会替换掉这个壳，此处不做旧页面的 1:1 平移。
    """
    if auth_required() and not get_current_user(request):
        return RedirectResponse("/login?next=/", status_code=302)
    return _render(request, "index.html", message=request.query_params.get("message", ""))


# ── GET|POST /login ───────────────────────────────────────────────


@router.get("/login", response_class=HTMLResponse, summary="登录页")
def login_page(request: Request, next: str = "/") -> Response:
    """登录页。已登录用户直接回首页。"""
    if get_current_user(request):
        return RedirectResponse("/", status_code=302)
    return _render(request, "login.html", next=_safe_next(next), error="")


@router.post("/login", response_class=HTMLResponse, summary="登录提交")
@limiter.limit("10/minute")
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
    next: str = Form("/"),
) -> Response:
    """处理登录表单。

    CSRF 失败渲染回登录页（400），而不是抛裸 403 —— 用户面对的是浏览器，
    需要一个可读的页面而不是错误页。API 端点仍然抛 403（见 `validate_csrf`）。
    """
    target = _safe_next(next)

    try:
        verify_csrf_pair(request.cookies.get(CSRF_COOKIE_NAME), csrf_token)
    except HTTPException:
        return _render(
            request, "login.html",
            error="表单已过期，请重新提交", next=target,
            status_code=400,
        )

    username = username.strip()
    if not username or not password:
        return _render(
            request, "login.html",
            error="请输入用户名和密码", username=username, next=target,
            status_code=400,
        )

    if not auth_required():
        return _render(
            request, "login.html",
            error="认证未启用（未配置 CAMPUS_IDS_API_TOKEN），无需登录",
            username=username, next=target,
            status_code=400,
        )

    with get_connection() as conn:
        try:
            authenticate(conn, username, password)
        except AuthenticationError as exc:
            logger.info("登录失败: username=%s reason=%s", username, exc.reason)
            if exc.reason == "account_disabled":
                status = 403
            else:
                status = 401
            return _render(
                request, "login.html",
                error=exc.message, username=username, next=target,
                status_code=status,
            )

    login_user(request, username)
    logger.info("用户 %s 登录成功（页面）", username)
    return RedirectResponse(target, status_code=302)


# ── GET|POST /logout ──────────────────────────────────────────────


@router.get("/logout", response_class=HTMLResponse, summary="登出（仅 POST）")
def logout_get() -> Response:
    """`GET /logout` 拒绝：登出是状态变更，不能被跨站 GET 触发。

    旧 Flask 实现同时接受 GET 与 POST，任何人都能用 `<img src="/logout">`
    把已登录用户踢下线（低危但真实）。此处有意收窄为 405。
    """
    return HTMLResponse(
        "<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
        "<title>405 Method Not Allowed</title></head><body>"
        "<h1>405 Method Not Allowed</h1>"
        "<p>登出必须使用 POST 方法（防止跨站强制登出）。</p>"
        '<form method="POST" action="/logout">'
        '<button type="submit">登出</button></form>'
        "</body></html>",
        status_code=405,
    )


@router.post("/logout", response_class=HTMLResponse, summary="登出提交")
def logout_submit(request: Request, csrf_token: str = Form("")) -> Response:
    """清除会话并回到登录页。要求 CSRF 双提交。"""
    try:
        verify_csrf_pair(request.cookies.get(CSRF_COOKIE_NAME), csrf_token)
    except HTTPException:
        return _render(
            request, "login.html",
            error="登出请求校验失败，请重试", next="/", status_code=400,
        )
    username = get_current_user(request)
    logout_user(request)
    if username:
        logger.info("用户 %s 已登出（页面）", username)
    return RedirectResponse("/login", status_code=302)


# ── GET|POST /change-password ─────────────────────────────────────


@router.get("/change-password", response_class=HTMLResponse, summary="修改密码页")
def change_password_page(request: Request) -> Response:
    """修改密码页。未登录时重定向到登录页并记住来源。"""
    if auth_required() and not get_current_user(request):
        return RedirectResponse("/login?next=/change-password", status_code=302)
    return _render(request, "change_password.html", error="", message="")


@router.post("/change-password", response_class=HTMLResponse, summary="修改密码提交")
@limiter.limit("5/minute")
def change_password_submit(
    request: Request,
    old_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    csrf_token: str = Form(""),
) -> Response:
    """修改当前登录用户的密码。"""
    username = get_current_user(request)
    if auth_required() and not username:
        return RedirectResponse("/login?next=/change-password", status_code=302)
    if not username:
        # 认证关闭时无人可改密
        return _render(
            request, "change_password.html",
            error="认证未启用，无需修改密码", message="", status_code=400,
        )

    try:
        verify_csrf_pair(request.cookies.get(CSRF_COOKIE_NAME), csrf_token)
    except HTTPException:
        return _render(
            request, "change_password.html",
            error="表单已过期，请重新提交", message="", status_code=400,
        )

    if not old_password or not new_password:
        return _render(
            request, "change_password.html",
            error="请填写所有字段", message="", status_code=400,
        )

    if new_password != confirm_password:
        return _render(
            request, "change_password.html",
            error="两次输入的新密码不一致", message="", status_code=400,
        )

    if len(new_password) < MIN_PASSWORD_LENGTH:
        return _render(
            request, "change_password.html",
            error=f"新密码至少 {MIN_PASSWORD_LENGTH} 个字符", message="", status_code=400,
        )

    with get_connection() as conn:
        user = UserRepository.get_by_username(conn, username)
        if user is None:
            return _render(
                request, "change_password.html",
                error="用户不存在", message="", status_code=404,
            )
        if not verify_password(old_password, user.password_hash):
            return _render(
                request, "change_password.html",
                error="旧密码不正确", message="", status_code=401,
            )
        UserRepository.update_password(
            conn, username=username, password_hash=hash_password(new_password)
        )

    logger.info("用户 %s 修改密码成功（页面）", username)
    return RedirectResponse("/?message=密码修改成功", status_code=302)
