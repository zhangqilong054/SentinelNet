"""数据库表行数统计（T0.6 基线记录）"""
import sqlite3

conn = sqlite3.connect('sentinelnet.db')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = cursor.fetchall()
for t in tables:
    cursor.execute(f'SELECT COUNT(*) FROM [{t[0]}]')
    count = cursor.fetchone()[0]
    print(f'{t[0]:20s} {count:6d} rows')
conn.close()