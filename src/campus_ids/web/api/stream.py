"""web/api/stream.py — SSE 实时推送路由。

端点：
- GET /api/stream  单条 SSE（?topics=traffic,alert）

取代旧 Flask 2 条 SSE 连接：
- /api/stream/traffic  → ?topics=traffic
- /api/stream/alerts   → ?topics=alert

⚠️ 帧名契约：SSE 帧的 `event:` 名 == topic 名，取值沿用旧应用
`web/app.py` 的 `_broadcast_sse("alert"|"traffic", ...)`，即 **单数 `alert`**
（不是 `alerts`）。旧前端 `addEventListener('alert', ...)` 因此无需改动。
topic 集合由 `campus_ids.runtime.events.VALID_TOPICS` 单点定义，本模块不再自持字面量。

ADR-0001 §5.2: async 端点 + call_soon_threadsafe 桥接。
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from campus_ids.runtime.events import DEFAULT_TOPICS, MAX_SUBSCRIBERS, VALID_TOPICS, EventBus
from campus_ids.web.errors import ApiError
from campus_ids.web.security import Public

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["stream"])

# 心跳间隔（秒）
KEEPALIVE_INTERVAL = 30

_DEFAULT_TOPICS_STR = ",".join(DEFAULT_TOPICS)
_VALID_TOPICS_STR = ", ".join(sorted(VALID_TOPICS))


def _get_event_bus(request: Request) -> EventBus:
    """从应用状态获取事件总线。"""
    state = getattr(request.app.state, "runtime_state", None)
    if state is None or getattr(state, "event_bus", None) is None:
        raise ApiError(
            error_code="SERVICE_UNAVAILABLE",
            detail="事件总线未初始化",
            status_code=503,
        )
    return state.event_bus


@router.get("/stream", dependencies=[Public], summary="SSE 实时事件流")
async def event_stream(
    request: Request,
    topics: str = Query(default=_DEFAULT_TOPICS_STR, description="订阅主题（逗号分隔）"),
) -> StreamingResponse:
    """单条 SSE 事件流。

    通过 ?topics= 参数选择订阅主题。**帧的 `event:` 名与 topic 名相同。**
    """
    bus = _get_event_bus(request)

    # 解析 topics —— 未知值必须显式失败。
    # 旧实现把未知值过滤掉，全部无效时静默回落到默认订阅，
    # 于是 `?topics=tasks` 会"成功"返回默认的 traffic+alert 流，
    # 调用方以为订阅成功却收的是别的 —— 属静默失败。
    requested = [t.strip() for t in topics.split(",") if t.strip()]
    if not requested:
        requested = list(DEFAULT_TOPICS)

    unknown = [t for t in requested if t not in VALID_TOPICS]
    if unknown:
        raise ApiError(
            error_code="INVALID_TOPIC",
            detail=f"未知 topic: {', '.join(unknown)}；合法值: {_VALID_TOPICS_STR}",
            status_code=400,
        )
    topic_list = requested

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
                # 与正常事件帧保持一致的 `ensure_ascii=False` —— 否则同一条流里
                # 中文在前者被转义成 \uXXXX、在后者是明文，抓包/日志里非常难读。
                payload = json.dumps({"error": "订阅数已达上限"}, ensure_ascii=False)
                yield f"event: error\ndata: {payload}\n\n"
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