#!/usr/bin/env python3
"""批量导入 incr_decr，分批提交"""
import psycopg2
import json
from datetime import datetime
import time

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

with open('/tmp/sse_holdings.json') as f:
    rows = json.load(f)

conn = psycopg2.connect(DB)
cur = conn.cursor()

# 建表
cur.execute("""
    CREATE TABLE IF NOT EXISTS incr_decr (
        id SERIAL PRIMARY KEY,
        code VARCHAR(10),
        name VARCHAR(100),
        person VARCHAR(100),
        position VARCHAR(100),
        change_date DATE,
        change_num BIGINT,
        change_price NUMERIC(12,2),
        change_reason VARCHAR(200),
        is_increase BOOLEAN,
        is_decrease BOOLEAN,
        abs_change BIGINT,
        source VARCHAR(10) DEFAULT 'SSE',
        updated_at TIMESTAMP DEFAULT NOW()
    )
""")
conn.commit()
cur.execute("DELETE FROM incr_decr WHERE source = 'SSE'")
conn.commit()

batch = []
inserted = 0
for r in rows:
    d = r.get('CHANGE_DATE', '')
    if not d or d < '2022-01-01':
        continue
    try:
        dd = datetime.strptime(d[:10], '%Y-%m-%d').date()
    except:
        continue
    num = int(r.get('CHANGE_NUM', 0) or 0)
    price = r.get('CHANGE_PRICE')
    price = float(price) if price else None
    
    batch.append((
        r.get('COMPANY_CODE', '').strip(),
        r.get('COMPANY_ABBR', ''),
        r.get('PERSON_NAME', ''),
        r.get('POSITION_NAME', ''),
        dd, num, price,
        r.get('CHANGE_REASON', ''),
        num > 0, num < 0, abs(num), 'SSE'
    ))
    
    if len(batch) >= 500:
        cur.executemany("""
            INSERT INTO incr_decr (code, name, person, position, change_date, change_num, change_price, change_reason, is_increase, is_decrease, abs_change, source)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, batch)
        conn.commit()
        inserted += len(batch)
        batch = []
        print(f"  已导入 {inserted} 条")

# 最后一批
if batch:
    cur.executemany("""
        INSERT INTO incr_decr (code, name, person, position, change_date, change_num, change_price, change_reason, is_increase, is_decrease, abs_change, source)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, batch)
    conn.commit()
    inserted += len(batch)

cur.execute("SELECT MIN(change_date), MAX(change_date), COUNT(*), SUM(CASE WHEN is_increase THEN 1 ELSE 0 END), SUM(CASE WHEN is_decrease THEN 1 ELSE 0 END) FROM incr_decr")
for r in cur.fetchall():
    print(f"\n完成: {inserted} 条")
    print(f"  日期: {r[0]} ~ {r[1]}")
    print(f"  总: {r[2]}, 增持: {r[3]}, 减持: {r[4]}")

cur.close()
conn.close()
