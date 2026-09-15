"""SSE（Server-Sent Events）实时推送基础设施。"""
from __future__ import annotations

import json
import queue
import threading

# M3: SSE 订阅者队列管理
_sse_subscribers: list[queue.Queue] = []
_sse_lock = threading.Lock()

# O-03: SSE 全局订阅上限（每个 SSE 连接占 1 个 Waitress 线程）
MAX_SSE_SUBSCRIBERS = 20


def _broadcast_sse(event: str, data: dict) -> None:
    """向所有 SSE 订阅者广播事件。"""
    msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _sse_lock:
        dead = []
        for i, q in enumerate(_sse_subscribers):
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(i)
        for i in reversed(dead):
            _sse_subscribers.pop(i)