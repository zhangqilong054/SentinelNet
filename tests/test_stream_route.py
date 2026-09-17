# -*- coding: utf-8 -*-
"""`web_new/api/stream.py` 单元级行为测试（R5 覆盖率补齐）。

## 与 `tests/test_sse_live.py` 的分工

`test_sse_live.py` 用**真实 uvicorn + 真实 socket** 验证端到端连通（那是必要的，
因为 `TestClient` 打无限 SSE 会静默挂死）。但端到端测试很难覆盖**拒绝路径**：

- 未知 topic → 400
- 事件总线未初始化 → 503
- 订阅数已达上限 → 503 / 生成器内 error 帧
- 心跳保活帧（真实端到端要等 30 秒）

本文件直接调用端点函数与生成器来覆盖这些分支，不经过网络栈。
`TestClient` 在这里依然**不能用**（见上），所以用 `SimpleNamespace` 伪造一个最小
Request —— 端点只用到 `request.app.state.runtime_state.event_bus` 这一条路径。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from campus_ids.runtime.events import (
    DEFAULT_TOPICS,
    MAX_SUBSCRIBERS,
    PLANNED_TOPICS,
    TOPIC_ALERT,
    TOPIC_TRAFFIC,
    VALID_TOPICS,
    EventBus,
)
from campus_ids.web_new.api import stream as stream_mod
from campus_ids.web_new.api.stream import event_stream
from campus_ids.web_new.errors import ApiError


# ── 伪造 Request ──────────────────────────────────────────────────


def _request(bus) -> SimpleNamespace:
    """最小 Request 替身：只需 `app.state.runtime_state.event_bus`。"""
    state = SimpleNamespace(runtime_state=SimpleNamespace(event_bus=bus))
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _request_without_bus() -> SimpleNamespace:
    state = SimpleNamespace(runtime_state=SimpleNamespace(event_bus=None))
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _call(bus, topics: str):
    """直接调用端点协程并返回 StreamingResponse（无需 HTTP 层）。

    ⚠️ 不能省略 `topics` 来测默认值 —— 直调时 FastAPI 不会做参数注入，
    `topics` 会是 `Query(...)` 对象本身。默认值的验证见
    `TestTopicParsing.test_declared_default_covers_all_valid_topics`。
    """
    return asyncio.run(event_stream(_request(bus), topics=topics))


def _frames(bus, topics: str, *, steps: int) -> list[str]:
    """在**单个事件循环**内驱动生成器 `steps` 次并返回原始帧文本。

    必须整体交给一次 `asyncio.run` —— 生成器绑定到创建它的循环，
    分多次 run 会抛 RuntimeError。
    """
    resp = _call(bus, topics)

    async def _main() -> list[str]:
        agen = resp.body_iterator
        out = []
        try:
            for _ in range(steps):
                out.append(await agen.__anext__())
        finally:
            await agen.aclose()
        return out

    return asyncio.run(_main())


def _connected_topics(frame: str) -> list[str]:
    """从 `connected` 帧里取出 topics。"""
    assert frame.startswith("event: connected\n"), frame
    return json.loads(frame.split("data: ", 1)[1].strip())["topics"]


# ══ topic 参数解析 ════════════════════════════════════════════════


class TestTopicParsing:
    def test_declared_default_covers_all_valid_topics(self):
        """端点签名里声明的 `?topics=` 默认值必须恰好等于可订阅集合。

        直接调用端点函数时，`topics` 收到的是 FastAPI 的 `Query` 对象（真实默认值
        由 HTTP 层注入），所以这里从签名里取 `Query.default` 来断言 —— 这是唯一
        能在不走网络栈的前提下验证"声明默认值"的方式。

        旧两条 SSE 连接（/api/stream/traffic、/api/stream/alerts）被合并成一条后，
        默认订阅必须是两者的并集，否则合并过程中会丢主题。
        """
        import inspect

        declared = inspect.signature(event_stream).parameters["topics"].default
        default_str = getattr(declared, "default", declared)

        assert set(default_str.split(",")) == VALID_TOPICS, (
            f"声明默认值 {default_str!r} 与 VALID_TOPICS 不一致"
        )

    def test_default_topics_cover_all_valid_topics(self):
        """`DEFAULT_TOPICS` 常量本身也必须覆盖全部可订阅主题。"""
        assert set(DEFAULT_TOPICS) == VALID_TOPICS

    def test_single_topic(self):
        bus = EventBus()
        frame = _frames(bus, TOPIC_TRAFFIC, steps=1)[0]
        assert _connected_topics(frame) == [TOPIC_TRAFFIC]

    def test_multiple_topics(self):
        bus = EventBus()
        frame = _frames(bus, f"{TOPIC_TRAFFIC},{TOPIC_ALERT}", steps=1)[0]
        assert _connected_topics(frame) == [TOPIC_TRAFFIC, TOPIC_ALERT]

    def test_whitespace_and_empty_entries_are_dropped(self):
        """`?topics= alert , ` 必须被归一化成 ['alert']（前端拼接容易带空格）。"""
        bus = EventBus()
        frame = _frames(bus, " alert , ", steps=1)[0]
        assert _connected_topics(frame) == [TOPIC_ALERT]

    def test_all_empty_entries_fall_back_to_defaults(self):
        """`?topics=,,` 解析后为空 → 回落默认，而不是订阅 0 个主题后静默无数据。"""
        bus = EventBus()
        frame = _frames(bus, ",,", steps=1)[0]
        assert _connected_topics(frame) == list(DEFAULT_TOPICS)

    def test_duplicate_topics_are_tolerated(self):
        """重复订阅同一 topic 只会注册两次（订阅端用 is 比对队列，不重复计数）。"""
        bus = EventBus()
        frame = _frames(bus, f"{TOPIC_ALERT},{TOPIC_ALERT}", steps=1)[0]
        assert _connected_topics(frame) == [TOPIC_ALERT, TOPIC_ALERT]


class TestUnknownTopics:
    """未知 topic 必须显式失败。

    旧实现把未知值过滤掉，全部无效时静默回落到默认订阅 —— 于是 `?topics=tasks`
    "成功"返回一个 traffic+alert 流，调用方以为订阅成功却收的是别的东西。
    """

    def test_unknown_topic_is_400(self):
        with pytest.raises(ApiError) as exc:
            _call(EventBus(), "nope")
        assert exc.value.status_code == 400
        assert exc.value.error_code == "INVALID_TOPIC"

    def test_error_lists_valid_values(self):
        with pytest.raises(ApiError) as exc:
            _call(EventBus(), "nope")
        for topic in VALID_TOPICS:
            assert topic in exc.value.detail, f"错误提示未列出合法 topic {topic}"

    @pytest.mark.parametrize("planned", sorted(PLANNED_TOPICS))
    def test_planned_topics_are_rejected_until_they_have_a_producer(self, planned):
        """预留 topic 尚无生产者 → 订阅它必须 400，而不是给一个永远静默的流。"""
        with pytest.raises(ApiError) as exc:
            _call(EventBus(), planned)
        assert exc.value.status_code == 400
        assert exc.value.error_code == "INVALID_TOPIC"

    def test_one_bad_topic_rejects_the_whole_request(self):
        """合法+非法混合也必须整体拒绝 —— 否则调用方以为两个都订阅上了。"""
        with pytest.raises(ApiError) as exc:
            _call(EventBus(), f"{TOPIC_ALERT},bogus")
        assert exc.value.status_code == 400

    def test_rejection_happens_before_subscribing(self):
        """被拒绝的请求不得留下任何订阅。"""
        bus = EventBus()
        with pytest.raises(ApiError):
            _call(bus, "bogus")
        assert bus.subscriber_count() == 0


# ══ 服务可用性 ════════════════════════════════════════════════════


class TestServiceAvailability:
    def test_missing_event_bus_is_503(self):
        """lifespan 未跑（事件总线未注入）→ 503 而不是 NoneType 崩溃。"""
        with pytest.raises(ApiError) as exc:
            asyncio.run(event_stream(_request_without_bus()))
        assert exc.value.status_code == 503
        assert exc.value.error_code == "SERVICE_UNAVAILABLE"

    def test_subscriber_limit_is_503(self, monkeypatch):
        """已达订阅上限 → 503（提前拒绝，避免连接挂住再报错）。"""
        bus = EventBus()
        monkeypatch.setattr(stream_mod, "MAX_SUBSCRIBERS", 0)

        with pytest.raises(ApiError) as exc:
            _call(bus, TOPIC_ALERT)

        assert exc.value.status_code == 503
        assert exc.value.error_code == "SSE_LIMIT_REACHED"

    def test_subscribe_failure_inside_generator_yields_error_frame(self):
        """生成器内 `subscribe_async` 失败 → 发 error 帧并**清掉已注册的订阅**。

        这里是"逐个 topic 订阅、中途失败"的清理分支：若不清，队列会以已释放的
        身份留在表里，`subscriber_count()` 会虚高，最终把上限吃满。
        """
        bus = EventBus(max_subscribers=0)  # 任何订阅都失败
        frames = _frames(bus, TOPIC_ALERT, steps=1)

        assert frames[0].startswith("event: error\n"), frames[0]
        assert "订阅数已达上限" in frames[0]
        # 必须回滚掉（虽然 subscribe 没成功，但清理逻辑要保证计数不残留）
        assert bus.subscriber_count() == 0


# ══ 生成器行为 ════════════════════════════════════════════════════


class TestGenerator:
    def test_connected_frame_shape(self):
        bus = EventBus()
        frame = _frames(bus, TOPIC_ALERT, steps=1)[0]

        assert frame.startswith("event: connected\n")
        assert frame.endswith("\n\n")
        assert '"topics": ["alert"]' in frame

    def test_frame_name_equals_topic_name(self):
        """帧的 `event:` 名必须**逐字**等于 topic 名（前端按 `addEventListener(topic)` 订阅）。"""
        bus = EventBus()
        resp = _call(bus, TOPIC_ALERT)

        async def _main():
            agen = resp.body_iterator
            try:
                await agen.__anext__()  # connected
                bus.publish(TOPIC_ALERT, {"id": 1})
                await asyncio.sleep(0.02)
                return await agen.__anext__()
            finally:
                await agen.aclose()

        frame = asyncio.run(_main())
        assert frame.startswith(f"event: {TOPIC_ALERT}\n"), frame

    def test_published_event_is_serialised_with_payload(self):
        bus = EventBus()
        resp = _call(bus, TOPIC_ALERT)

        async def _main():
            agen = resp.body_iterator
            try:
                await agen.__anext__()
                bus.publish(TOPIC_ALERT, {"attack_type": "SYN_FLOOD", "中文": "洪泛"})
                await asyncio.sleep(0.02)
                return await agen.__anext__()
            finally:
                await agen.aclose()

        frame = asyncio.run(_main())
        payload = json.loads(frame.split("data: ", 1)[1].strip())
        assert payload == {"attack_type": "SYN_FLOOD", "中文": "洪泛"}
        assert "洪泛" in frame, "非 ASCII 不得被转义成 \\uXXXX（前端直接读 UTF-8）"

    def test_event_for_unsubscribed_topic_is_not_delivered(self):
        """只订阅 alert 的连接不得被 traffic 事件打扰 —— 否则前端要自己过滤。"""
        bus = EventBus()
        resp = _call(bus, TOPIC_ALERT)

        async def _main():
            agen = resp.body_iterator
            try:
                await agen.__anext__()
                bus.publish(TOPIC_TRAFFIC, {"qps": 1})
                await asyncio.sleep(0.02)
                return bus.subscriber_count(TOPIC_TRAFFIC)
            finally:
                await agen.aclose()

        assert asyncio.run(_main()) == 0

    def test_keepalive_frame_on_timeout(self, monkeypatch):
        """无事件时必须按 KEEPALIVE_INTERVAL 发心跳注释帧，否则中间代理会掐连接。"""
        monkeypatch.setattr(stream_mod, "KEEPALIVE_INTERVAL", 0.05)
        bus = EventBus()
        frames = _frames(bus, TOPIC_ALERT, steps=2)

        assert frames[1] == ":keepalive\n\n", frames[1]

    def test_event_arriving_before_keepalive_is_yielded_first(self, monkeypatch):
        """有事件就不发心跳 —— 心跳是兜底，不是固定节拍。"""
        monkeypatch.setattr(stream_mod, "KEEPALIVE_INTERVAL", 5)
        bus = EventBus()
        resp = _call(bus, TOPIC_ALERT)

        async def _main():
            agen = resp.body_iterator
            try:
                await agen.__anext__()
                bus.publish(TOPIC_ALERT, "ping")
                await asyncio.sleep(0.02)
                return await agen.__anext__()
            finally:
                await agen.aclose()

        assert asyncio.run(_main()).startswith(f"event: {TOPIC_ALERT}\n")

    def test_closing_generator_releases_subscriptions(self):
        """客户端断开（生成器 close）必须回收订阅 —— 否则第 21 个连接会被 503 拒掉。"""
        bus = EventBus()
        resp = _call(bus, f"{TOPIC_TRAFFIC},{TOPIC_ALERT}")

        async def _main():
            agen = resp.body_iterator
            await agen.__anext__()
            during = bus.subscriber_count()
            await agen.aclose()
            return during, bus.subscriber_count()

        during, after = asyncio.run(_main())
        assert during == 2, f"两个 topic 应各注册1个订阅者，实际 {during}"
        assert after == 0, f"断开后订阅未回收，残留 {after}"

    def test_subscription_count_allows_up_to_limit_then_503(self):
        """边界：第 MAX_SUBSCRIBERS 个连接成功，第 MAX_SUBSCRIBERS+1 个被 503 拒绝。"""
        bus = EventBus()

        # 直接占用满（绕过端点，避免真的开 MAX_SUBSCRIBERS 条连接）
        loop = asyncio.new_event_loop()
        try:
            for _ in range(MAX_SUBSCRIBERS):
                assert bus.subscribe_async(TOPIC_ALERT, asyncio.Queue(), loop) is True
        finally:
            loop.close()

        with pytest.raises(ApiError) as exc:
            _call(bus, TOPIC_ALERT)
        assert exc.value.status_code == 503
        assert exc.value.error_code == "SSE_LIMIT_REACHED"


class TestResponseMetadata:
    def test_headers_disable_buffering(self):
        """缺 `X-Accel-Buffering: no` 时 Nginx 会缓冲整个流 —— SSE 就形同虚设。"""
        resp = _call(EventBus(), TOPIC_ALERT)

        assert resp.media_type == "text/event-stream"
        assert resp.headers["cache-control"] == "no-cache"
        assert resp.headers["x-accel-buffering"] == "no"
