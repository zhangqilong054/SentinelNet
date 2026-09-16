"""T1.9 OpenAPI 自动生成 — 验证 FastAPI 自动生成的 OpenAPI 规范。

验收标准：
- /openapi.json 可访问
- title/version/description 正确
- 所有 API 路由出现在 paths 中
- Pydantic 模型自动生成 schemas
- 契约门禁：路径数量与预期一致
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    """创建测试用 FastAPI 应用。"""
    from campus_ids.web_new.app import create_app
    return create_app()


@pytest.fixture
def client(app):
    """创建测试客户端。"""
    return TestClient(app)


@pytest.fixture
def spec(client):
    """获取 OpenAPI 规范。"""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    return resp.json()


# ── 基础可达性 ──────────────────────────────────────────────────


class TestOpenAPIReachable:
    """验证 OpenAPI 端点可访问。"""

    def test_openapi_json_returns_200(self, client):
        """GET /openapi.json 返回 200。"""
        resp = client.get("/openapi.json")
        assert resp.status_code == 200

    def test_openapi_json_content_type(self, client):
        """GET /openapi.json 返回 application/json。"""
        resp = client.get("/openapi.json")
        assert "application/json" in resp.headers.get("content-type", "")

    def test_openapi_json_valid_json(self, client):
        """GET /openapi.json 返回有效 JSON。"""
        resp = client.get("/openapi.json")
        data = resp.json()
        assert isinstance(data, dict)


# ── 规范元信息 ──────────────────────────────────────────────────


class TestOpenAPIInfo:
    """验证 OpenAPI 规范元信息。"""

    def test_openapi_version(self, spec):
        """OpenAPI 版本为 3.x。"""
        assert spec["openapi"].startswith("3.")

    def test_title(self, spec):
        """应用标题为 SentinelNet。"""
        assert spec["info"]["title"] == "SentinelNet"

    def test_version(self, spec):
        """应用版本为 0.2.0。"""
        assert spec["info"]["version"] == "0.2.0"

    def test_description(self, spec):
        """应用描述非空。"""
        assert len(spec["info"].get("description", "")) > 0


# ── 路径覆盖 ────────────────────────────────────────────────────


class TestOpenAPIPaths:
    """验证 API 路径出现在 OpenAPI 规范中。"""

    # 预期的 API 路径（基于 web_new/api/ 路由模块）
    EXPECTED_PATHS = [
        "/api/health",
        "/api/settings",
        "/api/csrf-token",
        "/api/alerts",
        "/api/alerts/stats",
        "/api/traffic",
        "/api/traffic/history",
        "/api/tasks",
        "/api/tasks/{name}/start",
        "/api/tasks/{name}/stop",
        "/api/models",
        "/api/models/train",
        "/api/models/train/status",
        "/api/models/{name}",
        "/api/scenarios",
        "/api/scenarios/start",
        "/api/scenarios/stop",
        "/api/tls/analyze",
        "/api/payload/analyze",
    ]

    def test_paths_exist(self, spec):
        """所有预期路径出现在规范中。"""
        paths = spec.get("paths", {})
        missing = [p for p in self.EXPECTED_PATHS if p not in paths]
        assert len(missing) == 0, f"缺少路径: {missing}"

    def test_path_count(self, spec):
        """路径数量与预期一致。"""
        paths = spec.get("paths", {})
        assert len(paths) == len(self.EXPECTED_PATHS), (
            f"路径数量不匹配: 预期 {len(self.EXPECTED_PATHS)}, "
            f"实际 {len(paths)}, "
            f"实际路径: {sorted(paths.keys())}"
        )

    def test_health_get_method(self, spec):
        """健康检查端点有 GET 方法。"""
        path = spec["paths"]["/api/health"]
        assert "get" in path

    def test_settings_put_method(self, spec):
        """设置更新端点有 PUT 方法。"""
        path = spec["paths"]["/api/settings"]
        assert "put" in path

    def test_task_start_post_method(self, spec):
        """任务启动端点有 POST 方法。"""
        path = spec["paths"]["/api/tasks/{name}/start"]
        assert "post" in path

    def test_model_delete_method(self, spec):
        """模型删除端点有 DELETE 方法。"""
        path = spec["paths"]["/api/models/{name}"]
        assert "delete" in path


# ── Schemas 自动生成 ────────────────────────────────────────────


class TestOpenAPISchemas:
    """验证 Pydantic 模型自动生成 schemas。"""

    EXPECTED_SCHEMAS = [
        "HealthResponse",
        "MessageResponse",
        # ErrorResponse 未被端点引用，FastAPI 不自动生成
        "SettingsResponse",
        "ThresholdUpdateRequest",
        "AlertListResponse",
        "AlertStatsResponse",
        "TrafficStatsResponse",
        "TrafficHistoryResponse",
        "TaskListResponse",
        "TaskStatusResponse",
        "TaskActionRequest",
        "ModelListResponse",
        "ModelInfoResponse",
        "TrainRequest",
        "TrainStatusResponse",
        "ScenarioListResponse",
        "ScenarioStartRequest",
        "TlsAnalysisResponse",
        "PayloadAnalysisRequest",
        "PayloadAnalysisResponse",
    ]

    def test_schemas_exist(self, spec):
        """关键 Pydantic 模型出现在 schemas 中。"""
        schemas = spec.get("components", {}).get("schemas", {})
        missing = [s for s in self.EXPECTED_SCHEMAS if s not in schemas]
        assert len(missing) == 0, f"缺少 schemas: {missing}"

    def test_health_response_schema(self, spec):
        """HealthResponse schema 包含正确字段。"""
        schemas = spec.get("components", {}).get("schemas", {})
        health = schemas.get("HealthResponse", {})
        props = health.get("properties", {})
        assert "status" in props
        assert "version" in props
        assert "uptime_seconds" in props

    def test_message_response_schema(self, spec):
        """MessageResponse schema 包含 message 和 status 字段。"""
        schemas = spec.get("components", {}).get("schemas", {})
        msg = schemas.get("MessageResponse", {})
        props = msg.get("properties", {})
        assert "message" in props
        assert "status" in props

    def test_error_response_not_auto_generated(self, spec):
        """ErrorResponse 未被端点引用，不出现在 schemas 中（预期行为）。"""
        schemas = spec.get("components", {}).get("schemas", {})
        # ErrorResponse 仅在异常处理器中使用，FastAPI 不自动生成
        assert "ErrorResponse" not in schemas


# ── Tags 分组 ────────────────────────────────────────────────────


class TestOpenAPITags:
    """验证 API 按 tag 正确分组。"""

    EXPECTED_TAGS = ["system", "traffic", "alerts", "tasks", "models", "scenarios", "tls", "payload"]

    def test_tags_used_in_paths(self, spec):
        """所有预期 tag 出现在路径的 tags 中。"""
        paths = spec.get("paths", {})
        found_tags: set[str] = set()
        for path_item in paths.values():
            for method_item in path_item.values():
                if isinstance(method_item, dict) and "tags" in method_item:
                    found_tags.update(method_item["tags"])
        missing = [t for t in self.EXPECTED_TAGS if t not in found_tags]
        assert len(missing) == 0, f"缺少 tags: {missing}"

    def test_health_tagged_system(self, spec):
        """健康检查端点标记为 system tag。"""
        path = spec["paths"]["/api/health"]["get"]
        tags = path.get("tags", [])
        assert "system" in tags

    def test_alerts_tagged_alerts(self, spec):
        """告警端点标记为 alerts tag。"""
        path = spec["paths"]["/api/alerts"]["get"]
        tags = path.get("tags", [])
        assert "alerts" in tags


# ── 契约门禁 ────────────────────────────────────────────────────


class TestContractGate:
    """契约门禁：验证 OpenAPI 规范整体结构符合预期。"""

    def test_no_extra_paths_beyond_expected(self, spec):
        """无预期之外的额外路径（防止意外添加端点）。"""
        paths = spec.get("paths", {})
        expected = set(TestOpenAPIPaths.EXPECTED_PATHS)
        actual = set(paths.keys())
        # 允许 /openapi.json /docs /redoc 等内置路径
        builtin = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
        extra = actual - expected - builtin
        assert len(extra) == 0, f"发现额外路径: {extra}"

    def test_all_paths_have_api_prefix(self, spec):
        """所有自定义路径以 /api/ 开头。"""
        paths = spec.get("paths", {})
        builtin = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
        for path in paths:
            if path in builtin:
                continue
            assert path.startswith("/api/"), f"非 /api/ 前缀路径: {path}"