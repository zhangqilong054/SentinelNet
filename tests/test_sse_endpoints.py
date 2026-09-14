"""SSE 端点验收测试 — T-01 验证：/api/stream/traffic 和 /api/stream/alerts 返回 200 + text/event-stream。

C-05 验收记录：使用 Flask test_client 验证 SSE 端点基础契约（状态码、Content-Type、初始事件格式）。
"""
import json
import pytest

from campus_ids.web.app import app


@pytest.fixture
def client():
    """Flask test client（禁用 Bearer 认证以便测试）。"""
    import os
    os.environ.setdefault("CAMPUS_IDS_AUTH_ENABLED", "0")
    os.environ.setdefault("CAMPUS_IDS_LOGIN_ENABLED", "0")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestSSEStreamTraffic:
    """T-01 验收：/api/stream/traffic SSE 端点。"""

    def test_returns_200(self, client):
        resp = client.get("/api/stream/traffic")
        assert resp.status_code == 200

    def test_content_type_is_event_stream(self, client):
        resp = client.get("/api/stream/traffic")
        assert resp.content_type.startswith("text/event-stream")

    def test_initial_event_format(self, client):
        """首个 SSE 事件应为 event: traffic + JSON data。
        从生成器直接读取首个 yield，避免无限流阻塞。
        """
        resp = client.get("/api/stream/traffic")
        chunk = next(resp.response)
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        assert "event: traffic" in text
        assert "data:" in text
        resp.close()


class TestSSEStreamAlerts:
    """T-01 验收：/api/stream/alerts SSE 端点。"""

    def test_returns_200(self, client):
        resp = client.get("/api/stream/alerts")
        assert resp.status_code == 200

    def test_content_type_is_event_stream(self, client):
        resp = client.get("/api/stream/alerts")
        assert resp.content_type.startswith("text/event-stream")

    def test_cache_control_no_cache(self, client):
        """SSE 响应应禁用缓存。"""
        resp = client.get("/api/stream/alerts")
        assert resp.headers.get("Cache-Control") == "no-cache"