#!/usr/bin/env python3
"""
将上交所董监高变动数据导入 PG，建立 incr_decr 表
2022~2026年，20000条
"""
import psycopg2
import json
from datetime import datetime

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

# 加载数据
with open('/tmp/sse_holdings.json') as f:
    rows = json.load(f)

print(f"总记录: {len(rows)}")

# 建表
conn = psycopg2.connect(DB)
cur = conn.cursor()

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

# 删旧数据
cur.execute("DELETE FROM incr_decr WHERE source = 'SSE'")
conn.commit()

# 批量插入
inserted = 0
for r in rows:
    try:
        d = r.get('CHANGE_DATE', '')
        if not d: continue
        dd = datetime.strptime(d[:10], '%Y-%m-%d').date()
    except:
        continue
    
    num = int(r.get('CHANGE_NUM', 0) or 0)
    price = r.get('CHANGE_PRICE')
    price = float(price) if price else None
    
    cur.execute("""
        INSERT INTO incr_decr (code, name, person, position, change_date, change_num, change_price, change_reason, is_increase, is_decrease, abs_change, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'SSE')
    """, (
        r.get('COMPANY_CODE', '').strip(),
        r.get('COMPANY_ABBR', ''),
        r.get('PERSON_NAME', ''),
        r.get('POSITION_NAME', ''),
        dd,
        num,
        price,
        r.get('CHANGE_REASON', ''),
        num > 0,
        num < 0,
        abs(num),
    ))
    inserted += 1

conn.commit()
cur.close()
conn.close()

print(f"入库: {inserted} 条")
# 统计
import psycopg2
c2 = psycopg2.connect(DB)
cur2 = c2.cursor()
cur2.execute("SELECT MIN(change_date), MAX(change_date), COUNT(*), SUM(CASE WHEN is_increase THEN 1 ELSE 0 END), SUM(CASE WHEN is_decrease THEN 1 ELSE 0 END) FROM incr_decr")
for r in cur2.fetchall():
    print(f"  日期: {r[0]} ~ {r[1]}")
    print(f"  总: {r[2]}, 增持: {r[3]}, 减持: {r[4]}")
cur2.close()
c2.close()
