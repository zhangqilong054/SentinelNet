"""web_new/api/stream.py — SSE 实时推送路由。

端点：
- GET /api/stream  单条 SSE（?topics=traffic,alerts）

取代旧 Flask 2 条 SSE 连接：
- /api/stream/traffic  → ?topics=traffic
- /api/stream/alerts   → ?topics=alerts

ADR-0001 §5.2: async 端点 + call_soon_threadsafe 桥接。
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from campus_ids.runtime.events import EventBus, MAX_SUBSCRIBERS
from campus_ids.web_new.errors import ApiError
from campus_ids.web_new.security import Public

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["stream"])

# 心跳间隔（秒）
KEEPALIVE_INTERVAL = 30

# 支持的 topic 列表
VALID_TOPICS = {"traffic", "alerts", "tasks", "detection"}


def _get_event_bus(request: Request) -> EventBus:
    """从应用状态获取事件总线。"""
    bus = getattr(request.app.state.runtime_state, "event_bus", None)
    if bus is None:
        raise ApiError(
            error_code="SERVICE_UNAVAILABLE",
            detail="事件总线未初始化",
            status_code=503,
        )
    return bus


@router.get("/stream", dependencies=[Public], summary="SSE 实时事件流")
async def event_stream(
    request: Request,
    topics: str = Query(default="traffic,alerts", description="订阅主题（逗号分隔）"),
) -> StreamingResponse:
    """单条 SSE 事件流。

    通过 ?topics= 参数选择订阅主题，支持：traffic, alerts, tasks, detection。
    默认订阅 traffic + alerts。
    """
    bus = _get_event_bus(request)

    # 解析 topics
    topic_list = [t.strip() for t in topics.split(",") if t.strip() in VALID_TOPICS]
    if not topic_list:
        topic_list = ["traffic", "alerts"]

    # 订阅者上限检查
    if bus.subscriber_count() >= MAX_SUBSCRIBERS:
        raise ApiError(
            error_code="SSE_LIMIT_REACHED",
            detail=f"SSE 订阅数已达上限 ({MAX_SUBSCRIBERS})，请稍后重试",
            status_code=503,
        )

    async def generate():
        """SSE 事件生成器。"""
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)

        # 订阅所有请求的 topic
        for topic in topic_list:
            if not bus.subscribe_async(topic, queue, loop):
                # 订阅失败（已达上限），清理已订阅的
                for t in topic_list:
                    bus.unsubscribe_async(t, queue)
                yield f"event: error\ndata: {json.dumps({'error': '订阅数已达上限'})}\n\n"
                return

        try:
            # 发送初始连接确认
            yield f"event: connected\ndata: {json.dumps({'topics': topic_list})}\n\n"

            while True:
                try:
                    # 等待事件，超时则发送心跳
                    msg = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_INTERVAL)
                    event_type = msg.get("event", "message")
                    data = msg.get("data")
                    yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
                except asyncio.TimeoutError:
                    # 心跳保活
                    yield ":keepalive\n\n"
                except asyncio.CancelledError:
                    # 客户端断开
                    break
        finally:
            # 清理订阅
            for topic in topic_list:
                bus.unsubscribe_async(topic, queue)
            logger.debug("SSE 连接关闭，已清理订阅")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )