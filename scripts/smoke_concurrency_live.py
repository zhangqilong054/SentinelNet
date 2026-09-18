# -*- coding: utf-8 -*-
"""T2.19 真机并发冒烟 —— 真实服务进程 + 真实 Npcap 抓包 + 检测/ML/攻击 + 跨进程 SQLite 写竞争。

    C:/Users/18551/anaconda3/python.exe scripts/smoke_concurrency_live.py            # 默认 600s
    SN_LIVE_SECONDS=60 C:/Users/18551/anaconda3/python.exe scripts/smoke_concurrency_live.py

与 `smoke_concurrency.py`（纯进程内库层多线程）的区别 —— 本脚本是**真机版**：

| 维度 | smoke_concurrency.py | 本脚本 |
|---|---|---|
| 抓包 | ❌ 不覆盖 | ✅ 真实 Npcap（`POST /api/tasks/capture/start`） |
| 检测节拍 | ❌ 不覆盖 | ✅ `POST /api/tasks/detection/start` |
| ML 引擎 | ❌ 不覆盖 | ✅ `POST /api/tasks/ml/start` |
| 演练注入 | ❌ 不覆盖 | ✅ `POST /api/tasks/attack/start`（周期触发） |
| HTTP 并发 | ❌ 不覆盖 | ✅ 真实 uvicorn 上多线程读 + 写 |
| 写竞争 | 同进程多线程 | ✅ **跨进程** sqlite3 直连（服务进程 + 2 个写入进程） |

**判定（全部满足才 ✅）**：
1. `database is locked` == 0（服务日志 + 客户端异常 + 写入进程异常）
2. 确定性写入无丢失：DB 实际行数 == 写入进程自报成功数（客户端真相 vs 服务端真相）
3. 无重复：唯一标记 `COUNT(*) == COUNT(DISTINCT message)`
4. 真实抓包生效：capture 任务全程 running（采样 100%）且（`packet_count`/`qps` > 0 或检测链路产出告警 > 0）
5. HTTP 读端点无 5xx（429 限流不计）
6. 结束时任务全部干净停止

**安全**：`CAMPUS_IDS_DATA_DIR` 隔离到临时目录，绝不碰真实 `sentinelnet.db` / `logs/` / 模型产物。
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import http.cookiejar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = os.environ.get("PY", r"C:\Users\18551\anaconda3\python.exe")
PORT = int(os.environ.get("SN_LIVE_PORT", "8897"))
DURATION = float(os.environ.get("SN_LIVE_SECONDS", "600"))
WRITER_EACH = int(os.environ.get("SN_LIVE_WRITER_TARGET", "40000"))
BASE = f"http://127.0.0.1:{PORT}"

TMP = Path(tempfile.mkdtemp(prefix="sn_live_"))
DB_PATH = TMP / "sentinelnet.db"
LOG_PATH = TMP / "uvicorn.log"

_stop = threading.Event()
_lock = threading.Lock()
stats = {
    "http_ok": 0, "http_5xx": 0, "http_429": 0, "http_4xx_other": 0,
    "http_err": 0, "csrf_refresh": 0,
}
errors: list[str] = []
# 真实抓包证据采样（判据 4）
capture_samples = {"running": 0, "total": 0, "packet_max": 0, "qps_max": 0.0, "dns_max": 0}


# ── 写入子进程模式（跨进程写竞争） ─────────────────────────────────

def _writer_main(tag: str, target: int, db_path: str, stop_file: str) -> None:
    """子进程：直连 sqlite 写 alerts + traffic_history，自带唯一标记。"""
    conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=5000")
    seq = 0
    errs: list[str] = []
    locked = 0
    stop = Path(stop_file)
    t0 = time.time()
    while not stop.exists() and seq < target:
        msg = f"LIVE-{tag}-{seq}"
        try:
            conn.execute(
                "INSERT INTO alerts (time, level, attack_type, message, ml_confidence) "
                "VALUES (?, ?, ?, ?, ?)",
                (time.strftime("%Y-%m-%d %H:%M:%S"), "low", "live_smoke", msg, 0.0),
            )
            conn.execute(
                "INSERT INTO traffic_history (time, qps, connections, packet_count,"
                " port_count, src_ip_count, alert) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.strftime("%Y-%m-%d %H:%M:%S"), 1, 1, 1, 1, 1, "Normal"),
            )
            seq += 1
        except Exception as exc:  # noqa: BLE001
            errs.append(f"{type(exc).__name__}: {exc}")
            if "locked" in str(exc).lower():
                locked += 1
        time.sleep(0.02)
    conn.close()
    print(json.dumps({
        "tag": tag, "written": seq, "n_errors": len(errs), "locked": locked,
        "elapsed": round(time.time() - t0, 1), "sample_errors": errs[:3],
    }), flush=True)


# ── HTTP 客户端 ───────────────────────────────────────────────────

class Client:
    """urllib + cookie jar，显式禁用代理（本机 curl 曾因系统代理误判服务未起）。"""

    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.jar),
        )
        self.token = ""

    def _record(self, code: int) -> None:
        with _lock:
            if code == 429:
                stats["http_429"] += 1
            elif 500 <= code < 600:
                stats["http_5xx"] += 1
            elif 400 <= code < 500:
                stats["http_4xx_other"] += 1
            else:
                stats["http_ok"] += 1

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict | str]:
        url = BASE + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if method == "POST" and self.token:
            req.add_header("X-CSRFToken", self.token)
        try:
            with self.opener.open(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8", "replace")
                self._record(resp.status)
                parsed: dict | str
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = raw[:200]
                return resp.status, parsed
        except urllib.error.HTTPError as exc:
            self._record(exc.code)
            return exc.code, ""
        except Exception as exc:  # noqa: BLE001
            with _lock:
                stats["http_err"] += 1
            errors.append(f"http {method} {path}: {type(exc).__name__}: {exc}")
            return 0, ""

    def refresh_csrf(self) -> None:
        code, payload = self.call("GET", "/api/csrf-token")
        if code == 200 and isinstance(payload, dict):
            self.token = payload.get("csrf_token", "")
            with _lock:
                stats["csrf_refresh"] += 1


# ── 真实流量发生器（让 Npcap 有东西可抓） ─────────────────────────

def traffic_maker() -> None:
    while not _stop.is_set():
        try:
            socket.getaddrinfo("www.baidu.com", 80)
        except Exception:  # noqa: BLE001
            pass
        try:
            s = socket.create_connection(("1.1.1.1", 53), timeout=1.5)
            s.close()
        except Exception:  # noqa: BLE001
            pass
        _stop.wait(3)


# ── 并发负载线程 ──────────────────────────────────────────────────

def http_reader(name: str) -> None:
    c = Client()
    paths = ["/api/tasks", "/api/alerts?limit=20", "/api/traffic", "/api/alerts/stats"]
    i = 0
    while not _stop.is_set():
        c.call("GET", paths[i % len(paths)])
        i += 1
        _stop.wait(0.4)


def http_writer() -> None:
    """低频写端点（限流 30/min，避免把 429 打成噪声）。"""
    c = Client()
    c.refresh_csrf()
    n = 0
    while not _stop.is_set():
        c.call("POST", "/api/tasks/detection/start", {})   # 幂等，返回 409 已运行
        if n % 3 == 0:
            c.refresh_csrf()                                # CSRF 轮换压力
        n += 1
        _stop.wait(10)


def http_post_payload() -> None:
    """载荷分析写路径（30/min 限流下的真实业务写）。"""
    c = Client()
    c.refresh_csrf()
    while not _stop.is_set():
        c.call("POST", "/api/payload/analyze", {"payload": "48454c4c4f"})
        _stop.wait(4)


# ── 服务生命周期 ──────────────────────────────────────────────────

def start_server() -> subprocess.Popen:
    log = open(LOG_PATH, "w", encoding="utf-8")
    env = {
        **os.environ,
        "CAMPUS_IDS_DATA_DIR": str(TMP),
        "CAMPUS_IDS_DEBUG": "1",
        "CAMPUS_IDS_FRONTEND": "legacy",
    }
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "campus_ids.web_new.app:create_app", "--factory",
         "--port", str(PORT), "--log-level", "info"],
        cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    return proc


def wait_health(client: Client, timeout: float = 40) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        code, payload = client.call("GET", "/api/health")
        if code == 200 and isinstance(payload, dict):
            return payload
        time.sleep(0.5)
    raise RuntimeError("服务 40s 内未就绪（见 %s）" % LOG_PATH)


def stop_tasks(c: Client) -> None:
    for name in ("capture", "detection", "ml", "attack", "capture_full"):
        try:
            c.call("POST", f"/api/tasks/{name}/stop", {})
        except Exception:  # noqa: BLE001
            pass


def _count_locked() -> tuple[int, int]:
    """(database is locked 出现次数, 日志行数)"""
    if not LOG_PATH.exists():
        return 0, 0
    text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    return text.lower().count("database is locked"), len(text.splitlines())


def main() -> int:
    print("=" * 78)
    print(f"T2.19 真机并发冒烟：{DURATION:.0f}s | 真实 uvicorn + Npcap 抓包 + 检测 + ML + 攻击")
    print(f"隔离 data_dir: {TMP}")
    print(f"端口: {PORT} | 写入目标: 2 进程 × {WRITER_EACH} 条")
    print("=" * 78, flush=True)

    boot = Client()
    proc = start_server()
    try:
        health = wait_health(boot)
        print(f"[就绪] /api/health = {json.dumps(health, ensure_ascii=False)[:160]}", flush=True)
        boot.refresh_csrf()

        # 抓包基线（真实字段：qps / packet_count / dns_packets）
        _, tf0 = boot.call("GET", "/api/traffic")
        pkt_before = int(tf0.get("packet_count", 0)) if isinstance(tf0, dict) else 0

        # 启动真实任务
        for name, body in (("capture", {}), ("detection", {}), ("ml", {})):
            code, payload = boot.call("POST", f"/api/tasks/{name}/start", body)
            msg = payload.get("message") if isinstance(payload, dict) else payload
            print(f"[任务] start {name}: HTTP {code} {msg}", flush=True)

        stop_file = TMP / "writers.stop"
        writers = [
            subprocess.Popen([PY, str(Path(__file__)), "--writer", tag, str(WRITER_EACH),
                              str(DB_PATH), str(stop_file)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for tag in ("w1", "w2")
        ]

        threads = [
            threading.Thread(target=http_reader, args=(f"r{i}",), daemon=True) for i in range(3)
        ] + [
            threading.Thread(target=http_writer, daemon=True),
            threading.Thread(target=http_post_payload, daemon=True),
            threading.Thread(target=traffic_maker, daemon=True),
        ]
        for t in threads:
            t.start()

        # ── 主循环：周期触发攻击演练 + 采样 ──
        t0 = time.time()
        attack_rounds = 0
        while time.time() - t0 < DURATION:
            elapsed = time.time() - t0
            if attack_rounds < int(DURATION // 45):
                code, _ = boot.call("POST", "/api/tasks/attack/start", {"duration": 20})
                if code in (200, 409):
                    attack_rounds += 1
                elif code == 429:          # 限流退避，稍后重试
                    pass
            code, tasks = boot.call("GET", "/api/tasks")
            tlist = tasks.get("tasks", []) if isinstance(tasks, dict) else []
            running = [t["name"] for t in tlist if t.get("status") == "running"]
            _, tf = boot.call("GET", "/api/traffic")
            pk = int(tf.get("packet_count", 0)) if isinstance(tf, dict) else 0
            qps = float(tf.get("qps", 0)) if isinstance(tf, dict) else 0.0
            dns = int(tf.get("dns_packets", 0)) if isinstance(tf, dict) else 0
            capture_samples["total"] += 1
            if "capture" in running:
                capture_samples["running"] += 1
            capture_samples["packet_max"] = max(capture_samples["packet_max"], pk)
            capture_samples["qps_max"] = max(capture_samples["qps_max"], qps)
            capture_samples["dns_max"] = max(capture_samples["dns_max"], dns)
            print(f"[{int(time.time()-t0):4d}s] running={running} packet_count={pk} qps={qps:.0f} "
                  f"dns={dns} attack_rounds={attack_rounds} http_ok={stats['http_ok']} "
                  f"5xx={stats['http_5xx']} 429={stats['http_429']}", flush=True)
            _stop.wait(15)

        # ── 收尾 ──
        print("\n[收尾] 停止并发负载与写入进程 ...", flush=True)
        _stop.set()
        for t in threads:
            t.join(timeout=10)
        stop_file.write_text("stop", encoding="utf-8")
        writer_reports = []
        for w in writers:
            try:
                out, err = w.communicate(timeout=30)
                line = [ln for ln in out.strip().splitlines() if ln.startswith("{")]
                writer_reports.append(json.loads(line[-1]) if line else
                                      {"tag": "?", "written": 0, "n_errors": 1,
                                       "sample_errors": [err[:200]]})
            except subprocess.TimeoutExpired:
                w.kill()
                writer_reports.append({"tag": "?", "written": 0, "n_errors": 1,
                                       "sample_errors": ["writer timeout"]})

        stop_tasks(boot)
        time.sleep(1)
        _, tf_final = boot.call("GET", "/api/traffic")
        pkt_after = int(tf_final.get("packet_count", 0)) if isinstance(tf_final, dict) else 0
        _, final_tasks = boot.call("GET", "/api/tasks")
        still_running = [t["name"] for t in final_tasks.get("tasks", [])
                         if t.get("status") == "running"] if isinstance(final_tasks, dict) else ["<query failed>"]

        # ── 服务端真相：DB 行数与唯一性 ──
        time.sleep(0.5)
        conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
        total_live = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE message LIKE 'LIVE-%'").fetchone()[0]
        distinct_live = conn.execute(
            "SELECT COUNT(DISTINCT message) FROM alerts WHERE message LIKE 'LIVE-%'").fetchone()[0]
        alerts_all = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        # 非 LIVE- 标记 = 抓包→队列→检测/攻击链路真实产出的告警
        detector_alerts = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE message NOT LIKE 'LIVE-%'").fetchone()[0]
        traffic_all = conn.execute("SELECT COUNT(*) FROM traffic_history").fetchone()[0]
        conn.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    locked_log, log_lines = _count_locked()
    written_client = sum(r.get("written", 0) for r in writer_reports)
    locked_writer = sum(r.get("locked", 0) for r in writer_reports)
    writer_errs = sum(r.get("n_errors", 0) for r in writer_reports)

    checks = {
        "1. 无 database is locked": (
            locked_log == 0 and locked_writer == 0,
            f"服务日志 {locked_log} 次 / 写入进程 {locked_writer} 次",
        ),
        "2. 确定性写入无丢失": (
            total_live == written_client and written_client > 0,
            f"客户端自报成功 {written_client} 条 vs DB 实存 {total_live} 条",
        ),
        "3. 无重复标记": (
            total_live == distinct_live,
            f"总行 {total_live} vs 去重 {distinct_live}",
        ),
        "4. 真实抓包生效": (
            capture_samples["total"] > 0
            and capture_samples["running"] == capture_samples["total"]
            and (capture_samples["packet_max"] > 0 or detector_alerts > 0),
            f"capture 运行采样 {capture_samples['running']}/{capture_samples['total']}；"
            f"packet_count 峰值 {capture_samples['packet_max']}、qps 峰值 {capture_samples['qps_max']:.0f}、"
            f"dns 峰值 {capture_samples['dns_max']}；检测/攻击链路产出告警 {detector_alerts} 条",
        ),
        "5. HTTP 无 5xx": (
            stats["http_5xx"] == 0,
            f"5xx={stats['http_5xx']} / 4xx={stats['http_4xx_other']} / 429={stats['http_429']} "
            f"/ 连接错误={stats['http_err']}",
        ),
        "6. 任务干净停止": (
            not still_running,
            f"残留运行中: {still_running or '无'}",
        ),
    }

    print("\n" + "=" * 78)
    print("写入进程自报：", json.dumps(writer_reports, ensure_ascii=False))
    print(f"HTTP 统计：ok={stats['http_ok']} 5xx={stats['http_5xx']} "
          f"4xx={stats['http_4xx_other']} 429={stats['http_429']} err={stats['http_err']} "
          f"csrf刷新={stats['csrf_refresh']}")
    print(f"DB 总计：alerts={alerts_all}（其中 LIVE- 标记 {total_live}）traffic_history={traffic_all}")
    print(f"服务日志：{log_lines} 行，含 'database is locked' {locked_log} 次")
    print(f"写入进程异常：{writer_errs} 次")
    if errors:
        print("客户端异常样本：")
        for e in errors[:8]:
            print("   ", e)
    print("-" * 78)
    for name, (ok, detail) in checks.items():
        print(f"{'✅' if ok else '🔴'} {name}: {detail}")
    failed = [n for n, (ok, _) in checks.items() if not ok]
    print("-" * 78)
    print("判定:", "✅ T2.19 真机并发通过" if not failed
          else f"🔴 未通过 {len(failed)} 项: {failed}")
    print(f"（隔离 data_dir 保留供核查: {TMP}）")
    return len(failed)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--writer":
        _writer_main(sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5])
    else:
        sys.exit(main())
