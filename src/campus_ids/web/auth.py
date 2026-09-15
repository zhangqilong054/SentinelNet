"""M4: 用户认证模块 — Flask-Login 集成。

提供 User 模型、登录/登出路由、@login_required 装饰器。
默认管理员: admin/admin（首次启动自动创建，请尽快修改密码）。
"""
from __future__ import annotations

import hashlib
import logging
import os

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

from campus_ids.web.database import (
    get_user_by_username,
    get_user_by_id,
    ensure_default_user,
    update_user_password,
)

logger = logging.getLogger(__name__)

# Flask-Login 管理器（由 app.py 初始化时调用 init_auth(app)）
login_manager = LoginManager()

# 认证蓝图
auth_bp = Blueprint("auth", __name__)


class User(UserMixin):
    """Flask-Login 用户模型。"""

    def __init__(self, user_dict: dict) -> None:
        self.id = user_dict["id"]
        self.username = user_dict["username"]
        self.password_hash = user_dict["password_hash"]
        self.is_active_flag = bool(user_dict.get("is_active", 1))

    def get_id(self) -> str:
        return str(self.id)

    @property
    def is_active(self) -> bool:
        return self.is_active_flag

    def verify_password(self, password: str) -> bool:
        """验证密码（兼容旧 SHA-256 哈希，成功后自动升级为 werkzeug 格式）。"""
        # 先尝试 werkzeug 格式
        from werkzeug.security import check_password_hash as _check, generate_password_hash as _gen
        if self.password_hash.startswith("pbkdf2:") or self.password_hash.startswith("sha256$"):
            result = _check(self.password_hash, password)
            return result

        # 兼容旧 SHA-256 无盐哈希
        legacy_hash = hashlib.sha256(password.encode()).hexdigest()
        if self.password_hash == legacy_hash:
            # 一次性升级：用 werkzeug 格式重哈希
            new_hash = _gen(password)
            try:
                update_user_password(self.id, new_hash)
                logger.info("用户 %s 密码已从 SHA-256 升级为 werkzeug 格式", self.username)
            except Exception as exc:
                logger.warning("密码升级失败: %s", exc)
            return True

        return False


@login_manager.user_loader
def _load_user(user_id: str) -> User | None:
    """Flask-Login 用户加载回调。"""
    user_dict = get_user_by_id(int(user_id))
    if user_dict is None:
        return None
    return User(user_dict)


@login_manager.unauthorized_handler
def _unauthorized():
    """未登录用户访问受保护页面时重定向到登录页。"""
    if request.path.startswith("/api/"):
        from flask import jsonify
        return jsonify({"error": "Unauthorized", "message": "请先登录"}), 401
    return redirect(url_for("auth.login"))


# ── 认证路由 ────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """登录页面。"""
    if request.method == "GET":
        # 已登录用户直接跳转首页
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        return render_template("login.html")

    # POST: 处理登录表单
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        flash("请输入用户名和密码", "error")
        return render_template("login.html"), 400

    user_dict = get_user_by_username(username)
    if user_dict is None:
        flash("用户名或密码错误", "error")
        return render_template("login.html"), 401

    user = User(user_dict)
    if not user.verify_password(password):
        flash("用户名或密码错误", "error")
        return render_template("login.html"), 401

    if not user.is_active:
        flash("账户已被禁用", "error")
        return render_template("login.html"), 403

    login_user(user, remember=True)
    logger.info("用户 %s 登录成功", username)

    # O-10: 默认 admin/admin 首次登录强制改密
    # 检查是否使用 werkzeug 默认哈希（admin 密码）
    from werkzeug.security import check_password_hash
    if username == "admin" and check_password_hash(user.password_hash, "admin"):
        flash("您正在使用默认密码，请立即修改密码", "warning")
        return redirect(url_for("auth.change_password"))

    # 跳转到请求来源页或首页
    next_page = request.args.get("next") or url_for("dashboard")
    return redirect(next_page)


@auth_bp.route("/logout", methods=["POST", "GET"])
@login_required
def logout():
    """登出。"""
    username = current_user.username if hasattr(current_user, "username") else "unknown"
    logout_user()
    logger.info("用户 %s 已登出", username)
    return redirect(url_for("auth.login"))


# O-10: 改密路由
@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    """修改密码页面。"""
    if request.method == "GET":
        return render_template("change_password.html")

    old_password = request.form.get("old_password", "")
    new_password = request.form.get("new_password", "")

    if not old_password or not new_password:
        flash("请填写所有字段", "error")
        return render_template("change_password.html"), 400

    if len(new_password) < 6:
        flash("新密码至少 6 个字符", "error")
        return render_template("change_password.html"), 400

    if not current_user.verify_password(old_password):
        flash("旧密码不正确", "error")
        return render_template("change_password.html"), 401

    from werkzeug.security import generate_password_hash
    new_hash = generate_password_hash(new_password)
    update_user_password(current_user.id, new_hash)
    logger.info("用户 %s 已修改密码", current_user.username)
    flash("密码修改成功", "success")
    return redirect(url_for("dashboard"))


# ── 初始化函数 ──────────────────────────────────────────────────────

def init_auth(app) -> None:
    """初始化 Flask-Login 和认证蓝图，注册到 Flask 应用。

    Args:
        app: Flask 应用实例
    """
    # 生成或使用固定的 secret_key（会话签名用）
    secret_key = os.environ.get("CAMPUS_IDS_SECRET_KEY", "sentinelnet-dev-secret-key-change-in-prod")
    app.secret_key = secret_key

    # O-10: 生产模式使用默认 secret_key 时显著告警
    if secret_key == "sentinelnet-dev-secret-key-change-in-prod":
        if os.environ.get("CAMPUS_IDS_DEV_MODE", "0") != "1":
            logger.warning(
                "⚠️ 生产环境使用默认 SECRET_KEY，请设置 CAMPUS_IDS_SECRET_KEY 环境变量！"
            )

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "请先登录以访问此页面"

    app.register_blueprint(auth_bp)

    # 确保默认管理员用户存在
    ensure_default_user()
    logger.info("认证模块初始化完成")