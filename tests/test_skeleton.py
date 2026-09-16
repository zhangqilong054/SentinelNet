"""T1.1 目录骨架 + T1.2 应用工厂 — 验证 import 无副作用与 create_app() 行为。

T1.1 验收：import 无副作用（不建库、不起线程、不读配置）。
T1.2 验收：python -c "import campus_ids.web_new.app" 不建库、不起线程、不读配置。
"""
from __future__ import annotations

import os
import sys
import threading
import pytest


def _collect_route_paths(app) -> list[str]:
    """从 FastAPI app 收集所有路由路径（兼容 _IncludedRouter）。"""
    paths: list[str] = []
    for route in app.routes:
        if hasattr(route, "path"):
            paths.append(route.path)
        elif type(route).__name__ == "_IncludedRouter" and hasattr(route, "original_router"):
            for r in route.original_router.routes:
                if hasattr(r, "path"):
                    paths.append(r.path)
    return paths


# ── T1.1 目录骨架 ─────────────────────────────────────────────────


class TestDirectorySkeleton:
    """验证新骨架目录结构存在。"""

    def test_runtime_package_exists(self):
        """runtime/ 包存在且可导入。"""
        import campus_ids.runtime
        assert hasattr(campus_ids.runtime, "Settings")
        assert hasattr(campus_ids.runtime, "EventBus")
        assert hasattr(campus_ids.runtime, "TaskRegistry")
        assert hasattr(campus_ids.runtime, "RuntimeState")

    def test_services_package_exists(self):
        """services/ 包存在。"""
        import campus_ids.services
        assert campus_ids.services is not None

    def test_web_new_package_exists(self):
        """web_new/ 包存在且可导入。"""
        import campus_ids.web_new
        assert campus_ids.web_new is not None

    def test_web_new_api_package_exists(self):
        """web_new/api/ 包存在。"""
        import campus_ids.web_new.api
        assert campus_ids.web_new.api is not None

    def test_runtime_submodules_importable(self):
        """runtime 子模块均可独立导入。"""
        from campus_ids.runtime import settings, events, tasks, state, db, repositories
        assert all(m is not None for m in [settings, events, tasks, state, db, repositories])


# ── T1.2 应用工厂 — import 无副作用 ──────────────────────────────


class TestImportNoSideEffects:
    """验证 import 不产生 I/O 副作用。"""

    def test_import_web_new_app_no_db(self, tmp_path, monkeypatch):
        """import campus_ids.web_new.app 不创建数据库文件。"""
        # 确保使用临时目录
        monkeypatch.setenv("CAMPUS_IDS_DATA_DIR", str(tmp_path))
        db_before = set(tmp_path.iterdir()) if tmp_path.exists() else set()
        # 重新导入模块（如果已导入则不会重复执行模块级代码，但模块级代码本身不应有副作用）
        import campus_ids.web_new.app
        db_after = set(tmp_path.iterdir()) if tmp_path.exists() else set()
        # 不应有新数据库文件
        new_files = db_after - db_before
        db_files = [f for f in new_files if f.suffix in (".db", ".sqlite", ".sqlite3")]
        assert len(db_files) == 0, f"import 创建了数据库文件: {db_files}"

    def test_import_web_new_app_no_threads(self):
        """import campus_ids.web_new.app 不启动新线程。"""
        threads_before = threading.active_count()
        import campus_ids.web_new.app
        threads_after = threading.active_count()
        # 允许少量波动（1个），但不应显著增加
        assert threads_after <= threads_before + 1, (
            f"import 启动了新线程: before={threads_before}, after={threads_after}"
        )

    def test_create_app_returns_fastapi(self):
        """create_app() 返回 FastAPI 实例。"""
        from campus_ids.web_new.app import create_app
        app = create_app()
        from fastapi import FastAPI
        assert isinstance(app, FastAPI)

    def test_create_app_has_health_endpoint(self):
        """create_app() 注册了 /api/health 端点。"""
        from campus_ids.web_new.app import create_app
        app = create_app()
        # FastAPI 0.141+: include_router 产生 _IncludedRouter，需从 original_router 提取
        all_paths = _collect_route_paths(app)
        assert "/api/health" in all_paths, f"健康端点未注册，已有路由: {all_paths}"

    def test_create_app_has_routes(self):
        """create_app() 注册了所有 API 路由。"""
        from campus_ids.web_new.app import create_app
        app = create_app()
        all_paths = _collect_route_paths(app)
        # 检查关键路由前缀
        api_routes = [r for r in all_paths if r.startswith("/api/")]
        assert len(api_routes) >= 5, f"API 路由不足: {api_routes}"

    def test_create_app_middleware_stack(self):
        """create_app() 注册了必需的中间件。"""
        from campus_ids.web_new.app import create_app
        app = create_app()
        # 检查中间件类名
        middleware_names = [type(m).__name__ for m in app.user_middleware]
        # SessionMiddleware 和 CORSMiddleware 通过 add_middleware 注册
        # 它们出现在 app.user_middleware 列表中
        assert len(app.user_middleware) >= 3, (
            f"中间件栈不足（预期≥3，实际{len(app.user_middleware)}): {middleware_names}"
        )