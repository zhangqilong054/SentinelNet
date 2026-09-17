# -*- coding: utf-8 -*-
"""SSE 真实端到端测试 —— 用真实 uvicorn + 真实 socket 验证"事件真的推得出去"。

## 为什么必须是真实 uvicorn

`TestClient` 走 Starlette 的**同步** transport，对无限 SSE 流会**静默挂死**：
2026-09-17 实测 —— reader 线程连首个 `connected` 帧都读不到，`frames` 恒为空，
且**不抛异常、不超时**（要靠外部 timeout 才能杀掉进程）。把这种调用放进测试套件，
一次就会把整个 pytest 会话拖死。所以本文件用真实 `uvicorn` + 真实 socket。

## 为什么消费端用裸 socket 而不是 `requests`

`requests`（urllib3）在**有线程阻塞于 `iter_lines()`** 时调用 `resp.close()`
并不会中断那次阻塞读（连接池语义，Windows 上尤其明显）：
socket 迟迟不关 → 服务端收不到 FIN → 订阅不回收 → 断言随机失败，
且每个用例都要等满 read timeout（实测 8s/用例）。

裸 socket 由本文件自管，`shutdown(SHUT_RDWR)` + `close()` 立即生效
（实测订阅回收 **0.00s**）。这也是"测量工具本身不能拖累被测对象"的教训。

## 这个文件抓到过的真实缺陷

首版运行即失败：`connected` 帧正常到达（证明流式推送本身没坏），但
`bus.publish("alert", ...)` 之后**订阅者收不到任何事件**。根因是 topic 名三方漂移
（生产者发 `"traffic_update"`、SSE 订阅 `"traffic"`、常量写 `"alert"` 单数）——
详见 `tests/test_sse_topics.py` 的模块 docstring。
"""
from __future__ import annotations

import socket
import threading
import time

import pytest
import requests
import uvicorn

from campus_ids.runtime.events import TOPIC_ALERT, TOPIC_TRAFFIC


def _free_port() -> int:
    """让 OS 分配一个空闲端口（避免与本机已占用端口冲突）。"""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def live_server():
    """真实 uvicorn 服务器，跑在本进程的后台线程里。

    同进程的好处：可以直接访问 `app.state.runtime_state.event_bus`，
    从测试线程调用 `publish()` —— 这正是生产者的真实形态
    （抓包/检测线程 → `loop.call_soon_threadsafe`）。

    注意：本 fixture 是 module 级，**早于** `conftest.py` 里 function 级的
    `_test_debug_mode`（它负责设 `CAMPUS_IDS_DEBUG=1`）实例化，因此在
    fixture 内自行提供测试专用 `secret_key`，不依赖那支 fixture。
    """
    import os

    from campus_ids.runtime.settings import reset_settings
    from campus_ids.web_new.app import create_app

    prev_key = os.environ.get("CAMPUS_IDS_SECRET_KEY")
    os.environ["CAMPUS_IDS_SECRET_KEY"] = "live-sse-test-secret-key-9f3a2b1c"
    reset_settings()
    try:
        app = create_app()
    except BaseException:
        if prev_key is None:
            os.environ.pop("CAMPUS_IDS_SECRET_KEY", None)
        else:
            os.environ["CAMPUS_IDS_SECRET_KEY"] = prev_key
        raise

    port = _free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port,
        log_level="warning", access_log=False, lifespan="on",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="uvicorn-test")
    thread.start()

    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        if not thread.is_alive():  # pragma: no cover - 启动失败路径
            raise RuntimeError("uvicorn 线程提前退出，无法完成 SSE 测试")
        time.sleep(0.05)
    assert server.started, "uvicorn 未在 20s 内启动"

    yield "127.0.0.1", port, app

    server.should_exit = True
    # force_exit 兜底：若仍有活跃 SSE 连接，优雅关闭会等待连接结束而挂住
    # （实测 teardown 被卡 10s）。正常路径下所有连接已在用例里关闭。
    server.force_exit = True
    thread.join(timeout=8)
    if prev_key is None:
        os.environ.pop("CAMPUS_IDS_SECRET_KEY", None)
    else:
        os.environ["CAMPUS_IDS_SECRET_KEY"] = prev_key
    reset_settings()


@pytest.fixture
def event_bus(live_server):
    """事件总线（顺带断言上一测试没有泄漏订阅）。"""
    _host, _port, app = live_server
    bus = app.state.runtime_state.event_bus
    assert bus.subscriber_count() == 0, (
        f"上一个测试留下了 {bus.subscriber_count()} 个未清理的订阅"
    )
    return bus


class SSEConsumer:
    """裸 socket 消费 SSE 流，把 (event, data) 存入列表。

    设计要点：
    - **不做无限阻塞读**：`recv` 带 0.5s 超时，循环里可响应 `_stopped`；
    - **停止立即生效**：`shutdown(SHUT_RDWR)` 直接让服务端收到 FIN。
    """

    READ_TIMEOUT = 0.5

    def __init__(self, host: str, port: int, query: str) -> None:
        self._host = host
        self._port = port
        self._query = query
        self.frames: list[tuple[str, str]] = []
        self.status_code: int | None = None
        self.content_type: str | None = None
        self.error: BaseException | None = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._sock: socket.socket | None = None
        self._pending_event = "message"
        self._thread = threading.Thread(target=self._run, daemon=True, name="sse-consumer")

    # ── 生命周期 ─────────────────────────────────────────────────

    def __enter__(self) -> SSEConsumer:
        self._sock = socket.create_connection((self._host, self._port), timeout=5)
        self._sock.sendall(
            f"GET /api/stream?{self._query} HTTP/1.1\r\n"
            f"Host: {self._host}:{self._port}\r\n"
            "Accept: text/event-stream\r\n"
            "Connection: keep-alive\r\n\r\n".encode()
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        self._stopped.set()
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
        self._thread.join(timeout=3)

    # ── 读循环 ───────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            buf = b""
            if self._sock is None:
                return
            self._sock.settimeout(self.READ_TIMEOUT)
            while not self._stopped.is_set():
                try:
                    chunk = self._sock.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                if self.status_code is None and b"\r\n\r\n" in buf:
                    head, buf = buf.split(b"\r\n\r\n", 1)
                    self._parse_head(head.decode("latin-1"))
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._handle_line(line.decode("utf-8", "replace").rstrip("\r"))
        except BaseException as exc:  # noqa: BLE001 - 记录后由断言处理
            with self._lock:
                self.error = exc

    def _parse_head(self, head: str) -> None:
        lines = head.split("\r\n")
        if lines and lines[0].startswith("HTTP/"):
            try:
                self.status_code = int(lines[0].split()[1])
            except (IndexError, ValueError):  # pragma: no cover
                pass
        for line in lines[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "content-type":
                self.content_type = value.strip()

    def _handle_line(self, line: str) -> None:
        if line.startswith("event: "):
            self._pending_event = line[len("event: "):].strip()
        elif line.startswith("data: "):
            with self._lock:
                self.frames.append((self._pending_event, line[len("data: "):].strip()))

    # ── 断言辅助 ─────────────────────────────────────────────────

    def events(self) -> list[str]:
        with self._lock:
            return [name for name, _ in self.frames]

    def data_for(self, event: str) -> str | None:
        with self._lock:
            for name, data in self.frames:
                if name == event:
                    return data
        return None

    def wait_for(self, event: str, timeout: float = 8.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if event in self.events():
                return True
            time.sleep(0.01)
        return False


def _wait_for(predicate, timeout: float = 6.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ── 基础契约 ──────────────────────────────────────────────────────

def test_stream_headers_and_connected_frame(live_server):
    """真发请求：200 + text/event-stream + 首帧 connected 回显订阅主题。"""
    host, port, _app = live_server
    with SSEConsumer(host, port, f"topics={TOPIC_ALERT}") as consumer:
        assert consumer.wait_for("connected"), (
            f"未收到 connected 帧；status={consumer.status_code} err={consumer.error}"
        )
        assert consumer.status_code == 200
        assert consumer.content_type is not None
        assert consumer.content_type.startswith("text/event-stream")
        assert TOPIC_ALERT in (consumer.data_for("connected") or "")


def test_alert_event_is_actually_delivered(live_server, event_bus):
    """🔴 核心：订阅 alert → 生产者 publish → 订阅者**真的收到** `event: alert`。"""
    host, port, _app = live_server
    with SSEConsumer(host, port, f"topics={TOPIC_ALERT}") as consumer:
        assert consumer.wait_for("connected")
        assert _wait_for(lambda: event_bus.subscriber_count() == 1), "订阅未注册到事件总线"

        payload = {"level": "high", "attack_type": "SYN_FLOOD", "message": "e2e"}
        event_bus.publish(TOPIC_ALERT, payload)

        assert consumer.wait_for("alert"), (
            f"publish({TOPIC_ALERT!r}) 后订阅者未收到 alert 事件；"
            f"已有帧={consumer.events()}，err={consumer.error}"
        )
        data = consumer.data_for("alert") or ""
        assert "SYN_FLOOD" in data, f"事件载荷未透传，实际: {data}"


def test_traffic_event_is_actually_delivered(live_server, event_bus):
    """🔴 核心：traffic topic 修复前**永远收不到**事件（生产者发的是 traffic_update）。"""
    host, port, _app = live_server
    with SSEConsumer(host, port, f"topics={TOPIC_TRAFFIC}") as consumer:
        assert consumer.wait_for("connected")
        assert _wait_for(lambda: event_bus.subscriber_count() == 1)

        event_bus.publish(TOPIC_TRAFFIC, {"qps": 42, "alert": "Normal"})
        assert consumer.wait_for("traffic"), (
            f"publish({TOPIC_TRAFFIC!r}) 后未收到 traffic 事件；已有帧={consumer.events()}"
        )
        assert "42" in (consumer.data_for("traffic") or "")


def test_frame_name_equals_topic_name(live_server, event_bus):
    """帧名与 topic 名一致 —— 前端 `addEventListener(topic, ...)` 才能对上。"""
    host, port, _app = live_server
    with SSEConsumer(host, port, f"topics={TOPIC_TRAFFIC},{TOPIC_ALERT}") as consumer:
        assert consumer.wait_for("connected")
        assert _wait_for(lambda: event_bus.subscriber_count() == 2), "两个 topic 应各注册一个订阅"

        event_bus.publish(TOPIC_ALERT, {"k": 1})
        event_bus.publish(TOPIC_TRAFFIC, {"k": 2})

        assert consumer.wait_for("alert")
        assert consumer.wait_for("traffic")
        assert set(consumer.events()) >= {"connected", "alert", "traffic"}


def test_subscription_is_released_on_disconnect(live_server, event_bus):
    """客户端断开后订阅必须回收 —— 否则 20 个上限会被僵尸订阅吃光。"""
    host, port, _app = live_server
    consumer = SSEConsumer(host, port, f"topics={TOPIC_ALERT}")
    consumer.__enter__()
    try:
        assert consumer.wait_for("connected")
        assert _wait_for(lambda: event_bus.subscriber_count() == 1)
    finally:
        consumer.close()

    assert _wait_for(lambda: event_bus.subscriber_count() == 0, timeout=8.0), (
        f"断开后订阅未回收，仍剩 {event_bus.subscriber_count()} 个"
    )


# ── 参数校验 ──────────────────────────────────────────────────────

def test_unknown_topic_rejected_with_400(live_server):
    """预留/未知 topic 必须 400，而不是静默回落到默认订阅。

    非流式响应，用 `requests` 安全（不会挂死）。
    """
    from campus_ids.runtime.events import PLANNED_TOPICS

    host, port, _app = live_server
    planned = sorted(PLANNED_TOPICS)[0]
    resp = requests.get(
        f"http://{host}:{port}/api/stream", params={"topics": planned}, timeout=10
    )
    assert resp.status_code == 400, (
        f"?topics={planned}（预留、无生产者）应 400，实际 {resp.status_code}"
    )
    detail = str(resp.json().get("detail") or resp.text)
    assert TOPIC_TRAFFIC in detail and TOPIC_ALERT in detail, (
        f"错误信息应列出合法 topic，实际: {detail}"
    )


def test_empty_topics_uses_defaults(live_server, event_bus):
    """未指定/空 topics → 订阅默认集合（traffic + alert）。"""
    host, port, _app = live_server
    with SSEConsumer(host, port, "topics=") as consumer:
        assert consumer.wait_for("connected"), f"err={consumer.error}"
        assert _wait_for(lambda: event_bus.subscriber_count() == 2), (
            f"默认应订阅 2 个 topic，实际 {event_bus.subscriber_count()}"
        )
        data = consumer.data_for("connected") or ""
        assert TOPIC_TRAFFIC in data and TOPIC_ALERT in data
