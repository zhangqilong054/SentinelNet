# -*- coding: utf-8 -*-
"""T2.19 并发冒烟 —— 多路写入 + 读取并存，统计 `database is locked`。

    C:/Users/18551/anaconda3/python.exe scripts/smoke_concurrency.py            # 默认 60s
    SN_CONC_SECONDS=600 C:/Users/18551/anaconda3/python.exe scripts/smoke_concurrency.py   # 10 分钟

**覆盖**：SQLite 并发写/读（`AlertRepository.insert`、`TrafficRepository.insert`、
`query`）+ 事件总线多线程发布订阅。对应风险登记册 R1/R2。

**不覆盖**（需真实环境，本脚本不假装覆盖）：
- 真实 Npcap 抓包 → 需要物理网卡权限
- 真实 ML 推理线程、真实演练注入 → `dangerously` 会写产物，见 `_probe_safety`

**安全**：`data_dir` 隔离到临时目录，绝不碰真实 `sentinelnet.db`。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="sn_conc_"))
os.environ["CAMPUS_IDS_DATA_DIR"] = str(TMP)   # ← 必须在 import 项目模块之前
os.environ.setdefault("CAMPUS_IDS_DEBUG", "1")

from campus_ids.runtime.db import get_connection, init_db  # noqa: E402
from campus_ids.runtime.events import EventBus  # noqa: E402
from campus_ids.runtime.repositories import AlertRepository, TrafficRepository  # noqa: E402

DURATION = float(os.environ.get("SN_CONC_SECONDS", "60"))

init_db()

errors: list[str] = []
counts = {"alert_write": 0, "traffic_write": 0, "read": 0, "publish": 0}
_lock = threading.Lock()
stop = threading.Event()


def _inc(key: str) -> None:
    with _lock:
        counts[key] += 1


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def writer_alerts() -> None:
    while not stop.is_set():
        try:
            with get_connection() as conn:
                AlertRepository.insert(conn, time=_stamp(), level="high",
                                       attack_type="conc_smoke", message="并发冒烟")
            _inc("alert_write")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"alert_write: {type(exc).__name__}: {exc}")


def writer_traffic() -> None:
    while not stop.is_set():
        try:
            with get_connection() as conn:
                TrafficRepository.insert(conn, time=_stamp(), qps=1, connections=1,
                                         packet_count=1, port_count=1,
                                         src_ip_count=1, alert="Normal")
            _inc("traffic_write")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"traffic_write: {type(exc).__name__}: {exc}")


def reader() -> None:
    while not stop.is_set():
        try:
            with get_connection() as conn:
                AlertRepository.query(conn, limit=20)
                TrafficRepository.query(conn, limit=20)
            _inc("read")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"read: {type(exc).__name__}: {exc}")


bus = EventBus()
bus_errors: list[str] = []
_received = {"sync": 0, "async": 0}


def _on_event(_event_type: str, _data) -> None:
    _received["sync"] += 1


def publisher() -> None:
    while not stop.is_set():
        try:
            bus.publish("traffic", {"qps": 1, "ts": time.time()})
            _inc("publish")
            time.sleep(0.001)          # 别把总线打成 CPU 压测
        except Exception as exc:  # noqa: BLE001
            bus_errors.append(f"publish: {type(exc).__name__}: {exc}")


def subscriber() -> None:
    """同时挂两条订阅路径：同步回调 + 异步队列（SSE 走的那条）。"""
    import asyncio

    try:
        bus.subscribe("traffic", _on_event)
    except Exception as exc:  # noqa: BLE001
        bus_errors.append(f"subscribe(sync): {type(exc).__name__}: {exc}")
        return

    try:
        loop = asyncio.new_event_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        if not bus.subscribe_async("traffic", queue, loop):
            bus_errors.append("subscribe_async 被拒（已达订阅者上限）")
            return

        async def drain() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(queue.get(), timeout=0.3)
                    _received["async"] += 1
                except asyncio.TimeoutError:
                    pass

        loop.run_until_complete(drain())
        bus.unsubscribe_async("traffic", queue)
        loop.close()
    except Exception as exc:  # noqa: BLE001
        bus_errors.append(f"subscribe(async): {type(exc).__name__}: {exc}")


threads = [
    threading.Thread(target=writer_alerts, name="w-alerts-1"),
    threading.Thread(target=writer_alerts, name="w-alerts-2"),
    threading.Thread(target=writer_traffic, name="w-traffic-1"),
    threading.Thread(target=writer_traffic, name="w-traffic-2"),
    threading.Thread(target=reader, name="r-1"),
    threading.Thread(target=reader, name="r-2"),
    threading.Thread(target=reader, name="r-3"),
    threading.Thread(target=publisher, name="pub-1"),
    threading.Thread(target=subscriber, name="sub-1"),
]

print("=" * 78)
print(f"T2.19 并发冒烟：{DURATION:.0f}s / 2+2 写 + 3 读 + 1 发布 + 1 订阅")
print(f"data_dir（隔离）: {TMP}")
print("=" * 78)

for t in threads:
    t.start()
time.sleep(DURATION)
stop.set()
for t in threads:
    t.join(timeout=15)

all_errors = errors + bus_errors
locked = [e for e in all_errors if "locked" in e.lower()]
print(f"\n成功计数: {counts}")
print(f"事件送达: {_received}（同步回调 / 异步队列）")
print(f"错误总数: {len(all_errors)}（其中 database is locked: {len(locked)}）")
for e in all_errors[:12]:
    print("   ", e)
print()
print("判定:", "✅ 未出现 database is locked" if not locked
      else f"🔴 出现 {len(locked)} 次 database is locked")
print("说明: 本脚本不覆盖真实 Npcap 抓包与真实 ML 推理线程，那两项需在带网卡的机器上另测。")
