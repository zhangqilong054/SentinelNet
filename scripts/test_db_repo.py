"""测试 db.py + repositories.py 的基本 CRUD。"""
import sys, tempfile, os
sys.path.insert(0, "src")
os.environ["CAMPUS_IDS_DATA_DIR"] = tempfile.mkdtemp()

from campus_ids.runtime.db import init_db, get_connection, reset_engine
from campus_ids.runtime.repositories import (
    AlertRepository, TrafficRepository, ConfigRepository, UserRepository,
)

init_db()
print("init_db OK")

with get_connection() as conn:
    # Alert
    aid = AlertRepository.insert(conn, time="2026-01-01 00:00:00", level="high",
                                  attack_type="ddos", message="test alert")
    print(f"Alert insert id={aid}")
    rows = AlertRepository.query(conn)
    print(f"Alert query count={len(rows)}")
    cnt = AlertRepository.count(conn)
    print(f"Alert count={cnt}")
    dist = AlertRepository.get_type_distribution(conn)
    print(f"Alert distribution={dist}")

    # Traffic
    tid = TrafficRepository.insert(conn, time="2026-01-01 00:00:00", qps=100)
    print(f"Traffic insert id={tid}")
    trows = TrafficRepository.query(conn)
    print(f"Traffic query count={len(trows)}")

    # Config
    ConfigRepository.set(conn, "test_key", "test_value")
    cv = ConfigRepository.get(conn, "test_key")
    print(f"Config get={cv}")
    all_cfg = ConfigRepository.get_all(conn)
    print(f"Config get_all={all_cfg}")

    # User
    uid = UserRepository.create(conn, username="admin", password_hash="hash123")
    print(f"User create id={uid}")
    u = UserRepository.get_by_username(conn, "admin")
    print(f"User get_by_username={u.username}")
    UserRepository.update_password(conn, username="admin", password_hash="new_hash")
    u2 = UserRepository.get_by_username(conn, "admin")
    print(f"User update_password OK, hash={u2.password_hash}")

    print("ALL CRUD TESTS PASSED")

reset_engine()