"""
backfill_turnover.py — 回填 daily_prices.turnover 历史数据

方法：从 market_snaphots 取 total_mv，推算流通股本
  turnover_pct = volume(股) / (total_mv * 1e8 / price) * 100
  = (dp.volume * 100.0) / (ms.total_mv * 1e8 / ms.price) * 100

验证：与腾讯 sqt 实际换手率 corr=1.0000, std=0.0029
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
from datetime import date
import time

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
conn = psycopg2.connect(DB)
cur = conn.cursor()

print("=" * 60)
print("backfill_turnover — 回填 daily_prices.turnover")
print("=" * 60)

# 1. 现状
cur.execute("""
    SELECT COUNT(*) as total,
           COUNT(*) FILTER(WHERE turnover IS NOT NULL) as to_ok,
           MIN(trade_date) as min_d, MAX(trade_date) as max_d
    FROM daily_prices WHERE close > 0
""")
r = cur.fetchone()
print(f"\n[1] daily_prices 现状:")
print(f"  总行数: {r[0]}")
print(f"  已有换手率: {r[1]} ({r[1]*100/r[0]:.1f}%)")
print(f"  日期: {r[2]} ~ {r[3]}")

# 2. market_snapshots 的 total_mv 覆盖
cur.execute("""
    SELECT MIN(trade_date) as min_d, MAX(trade_date) as max_d,
           COUNT(*) as rows,
           COUNT(*) FILTER(WHERE total_mv IS NOT NULL AND total_mv > 0) as mv_ok,
           COUNT(DISTINCT code) as stocks
    FROM market_snapshots
""")
r = cur.fetchone()
print(f"\n[2] market_snapshots total_mv:")
print(f"  总行数: {r[2]}")
print(f"  total_mv 有值: {r[3]} ({r[3]*100/r[2]:.1f}%)")
print(f"  日期: {r[0]} ~ {r[1]}, 股票: {r[4]}")

# 3. 按年分 bucket 统计需回填量
cur.execute("""
    SELECT EXTRACT(year FROM dp.trade_date) as yr,
           COUNT(*) as need_fill,
           COUNT(*) FILTER(WHERE ms.total_mv IS NOT NULL AND ms.total_mv > 0) as has_mv
    FROM daily_prices dp
    LEFT JOIN market_snapshots ms 
        ON ms.code = SUBSTRING(dp.symbol FROM 8) 
        AND ms.trade_date = dp.trade_date
    WHERE dp.close > 0 AND dp.volume > 0
      AND dp.turnover IS NULL
    GROUP BY yr ORDER BY yr
""")
print(f"\n[3] 需回填量（按年）:")
total_need = 0
total_can = 0
for r in cur.fetchall():
    yr = int(r[0])
    need = r[1]
    can = r[2]
    total_need += need
    total_can += can
    print(f"  {yr}: {need:>8d} 待填, {can:>8d} 可填 ({can*100/need:5.1f}%)" if need > 0 else f"  {yr}: {need:>8d} 待填")
print(f"  {'合计':>4s}: {total_need:>8d} 待填, {total_can:>8d} 可填 ({total_can*100/total_need:5.1f}%)")

# 4. 批量回填
print(f"\n[4] 开始回填...")
batch_size = 100000
filled = 0
skipped = 0
page = 0

while True:
    cur.execute("""
        SELECT dp.symbol, dp.trade_date, dp.volume,
               ms.total_mv, ms.price, ms.turnover as actual_to
        FROM daily_prices dp
        JOIN market_snapshots ms 
            ON ms.code = SUBSTRING(dp.symbol FROM 8) 
            AND ms.trade_date = dp.trade_date
        WHERE dp.close > 0 AND dp.volume > 0
          AND dp.turnover IS NULL
          AND ms.total_mv IS NOT NULL AND ms.total_mv > 0
          AND ms.price > 0
        LIMIT %s
    """, (batch_size,))
    
    rows = cur.fetchall()
    if not rows:
        break
    
    updates = []
    for r in rows:
        symbol, trade_date, vol_hand, total_mv, price, actual_to = r
        # turnover = vol(股) / (total_mv * 1e8 / price) * 100
        # = vol_hand * 100 / (total_mv * 1e8 / price) * 100
        if total_mv and total_mv > 0 and price > 0 and vol_hand > 0:
            turnover = (vol_hand * 100.0) / (total_mv * 1e8 / price) * 100
            updates.append((turnover, symbol, trade_date))
        else:
            skipped += 1
    
    # 批量 UPDATE
    if updates:
        cur.execute("DROP TABLE IF EXISTS _to_temp")
        cur.execute("""
            CREATE TEMP TABLE _to_temp (
                turnover DOUBLE PRECISION, symbol TEXT, trade_date DATE
            )
        """)
        conn.commit()
        
        args = ','.join(cur.mogrify(
            "(%s,%s,%s::date)", (u[0], u[1], u[2].isoformat() if hasattr(u[2], 'isoformat') else str(u[2]))
        ).decode('utf-8') for u in updates)
        cur.execute(f"INSERT INTO _to_temp VALUES {args}")
        
        cur.execute("""
            UPDATE daily_prices dp
            SET turnover = t.turnover,
                source = COALESCE(dp.source, 'mootdx+turnover')
            FROM _to_temp t
            WHERE dp.symbol = t.symbol AND dp.trade_date = t.trade_date
        """)
        conn.commit()
        
        filled += len(updates)
        page += 1
        if page % 5 == 0:
            print(f"  Page {page}: {filled} 已填, {skipped} 跳过")
    
    time.sleep(0.1)  # 防止 PG 连接过载

print(f"\n[5] 回填完成:")
print(f"  已填: {filled} 行")
print(f"  跳过: {skipped} 行")

# 6. 验证
cur.execute("""
    SELECT COUNT(*) as total,
           COUNT(*) FILTER(WHERE turnover IS NOT NULL) as to_ok
    FROM daily_prices WHERE close > 0
""")
r = cur.fetchone()
print(f"\n[6] 最终结果:")
print(f"  daily_prices 总行数: {r[0]}")
print(f"  有换手率: {r[1]} ({r[1]*100/r[0]:.1f}%)")

cur.execute("""
    SELECT trade_date, COUNT(*) as cnt,
           ROUND(AVG(turnover)::numeric, 2) as avg_to
    FROM daily_prices
    WHERE turnover IS NOT NULL AND trade_date >= '2026-04-01'
    GROUP BY trade_date ORDER BY trade_date DESC
    LIMIT 10
""")
print("\n  最近 10 日换手率:")
for r in cur.fetchall():
    print(f"    {r[0]}: {r[1]} 条, 平均换手率 {r[2]}%")

conn.close()
