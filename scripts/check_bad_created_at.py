"""Check for literal 'CURRENT_TIMESTAMP' values in created_at columns."""
import sqlite3

DB = "sentinelnet.db"

def main():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    for table in ["alerts", "traffic_history", "users"]:
        c.execute(
            f"SELECT COUNT(*) FROM {table} "
            f"WHERE created_at = 'CURRENT_TIMESTAMP' "
            f"OR created_at LIKE '%CURRENT%'"
        )
        bad = c.fetchone()[0]
        c.execute(f"SELECT COUNT(*) FROM {table}")
        total = c.fetchone()[0]
        print(f"{table}: {bad} bad rows / {total} total")
    conn.close()

if __name__ == "__main__":
    main()