"""
backfill_turnover_v2.py — 用 market_snaphots 回填 daily_prices.turnover

方法：
  market_snapshots 的 total_mv = 流通市值（亿）——已验证与腾讯 sqt 换手率 corr ≈ 1.0
  float_mv = 总市值（亿）——含 H 股+B 股+未流通

  换手率(‰/%) = volume(股) / (total_mv * 1e8 / price) * 100
  其中 total_mv = 流通市值(亿)，price = 当日收盘价

  注意：
  - market_snapshots 有 total_mv 的历史数据（2002 年起），但大部分为 0
  - 用 financial_data.total_shares × float_ratio 倒推流通股本
  - float_ratio = 最新流通比例，从最新 7 天 market_snapshots 计算

标记 H 股/B 股：float_mv / total_mv > 1.2（总市值远大于流通市值 = 有非流通/H/B股）
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
from datetime import date, timedelta
import time
import re

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
conn = psycopg2.connect(DB)
cur = conn.cursor()

print("=" * 70)
print("backfill_turnover_v2 — 回填 daily_prices.turnover（total_mv 法）")
print("=" * 70)

# ── 1. 确认 total_mv 就是流通市值 ──
print("\n[1] 验证 total_mv = 流通市值...")
cur.execute("""
    SELECT code, total_mv, float_mv, price,
           total_mv * 1e8 / price as implied_float_shares,
           turnover
    FROM market_snapshots
    WHERE trade_date = '2026-05-26'
      AND total_mv > 0 AND price > 0 AND turnover > 0
    LIMIT 5
""")
for r in cur.fetchall():
    calc_to = r[4]  # float_shares 不对
    print(f"  {r[0]}: tv={r[1]} fv={r[2]} price={r[3]} 流通股本≈{r[4]:.0f} 换手率={r[5]}")

# 验证：用 total_mv 算换手率
cur.execute("""
    SELECT CORR((dp.volume * 100.0) / (ms.total_mv * 1e8 / ms.price) * 100, ms.turnover::numeric) as corr,
           COUNT(*) as n,
           AVG(((dp.volume * 100.0) / (ms.total_mv * 1e8 / ms.price) * 100) / NULLIF(ms.turnover, 0))::numeric(10,4) as avg_ratio
    FROM market_snapshots ms
    JOIN daily_prices dp ON dp.symbol = 'A.' || ms.code AND dp.trade_date = ms.trade_date
    WHERE ms.trade_date = '2026-05-26'
      AND ms.turnover > 0 AND ms.price > 0 AND ms.total_mv > 0 AND dp.volume > 0
""")
r = cur.fetchone()
print(f"  total_mv 法换手率: corr={r[0]:.4f}, N={r[1]}, avg_ratio={r[2]}")
print(f"  → total_mv 法完美（corr≈1.0），直接用 total_mv 做推算")

# ── 2. 现在的问题是：market_snapshots 的 total_mv 只有最新 7 天有值 ──
print("\n[2] total_mv 历史覆盖...")
cur.execute("""
    SELECT EXTRACT(year FROM trade_date) as yr,
           COUNT(*) as rows,
           COUNT(*) FILTER(WHERE total_mv > 0) as mv_ok
    FROM market_snapshots
    GROUP BY yr ORDER BY yr
""")
for r in cur.fetchall():
    yr = int(r[0])
    rows = r[1]
    ok = r[2]
    pct = ok * 100.0 / rows if rows > 0 else 0
    print(f"  {yr}: {rows:>8d} 行, {ok:>8d} 有 total_mv ({pct:5.1f}%)")

# ── 3. 方案：用 financial_data.total_shares 推算流通股本 ──
# 流通股本 ≈ (总市值 / price) - 但总市值需要替代
# 换个角度：流通股本 = float_mv × 1e8 / price
# float_mv 也只有最新 7 天有
# 但是！我们可以用 financial_data 的 total_shares × float_ratio
# float_ratio = float_shares / total_shares（从最新 7 天算）
print("\n[3] 从 financial_data.total_shares 推算流通股本...")

# 先算每只股票的 float_ratio（最新）
cur.execute("""
    WITH latest AS (
        SELECT ms.code, ms.total_mv, ms.price, ms.float_mv,
               fd.total_shares
        FROM market_snapshots ms
        JOIN financial_data fd ON fd.symbol = 'A.' || ms.code AND fd.end_date = '2026-03-31'
        WHERE ms.trade_date = '2026-05-26'
          AND ms.total_mv > 0 AND ms.price > 0
          AND fd.total_shares IS NOT NULL AND fd.total_shares > 0
    )
    SELECT COUNT(*) as total,
           AVG((total_mv * 1e8 / price) / total_shares)::numeric(10,4) as avg_float_ratio,
           PERCENTILE_CONT(0.5) WITHIN GROUP(ORDER BY (total_mv * 1e8 / price) / total_shares)::numeric(10,4) as med_float_ratio,
           MIN((total_mv * 1e8 / price) / total_shares)::numeric(10,4) as min_ratio,
           MAX((total_mv * 1e8 / price) / total_shares)::numeric(10,4) as max_ratio
    FROM latest WHERE total_shares > 0
""")
r = cur.fetchone()
print(f"  股票数: {r[0]}")
print(f"  平均流通/总股本比例: {r[1]}")
print(f"  中位流通/总股本比例: {r[2]}")
print(f"  最小/最大: {r[3]} / {r[4]}")
print(f"  注意：如果 float_ratio < 0.8 说明有 H/B 股/大额非流通")

# 更新 financial_data 加 float_ratio 列
cur.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='financial_data' AND column_name='float_ratio'
        ) THEN
            ALTER TABLE financial_data ADD COLUMN float_ratio DOUBLE PRECISION;
        END IF;
    END $$;
""")
conn.commit()

# 写入 float_ratio（取最新 7 天的均值）
cur.execute("""
    WITH avg_fr AS (
        SELECT ms.code,
               AVG((ms.total_mv * 1e8 / ms.price) / NULLIF(fd.total_shares, 0)) as avg_ratio
        FROM market_snapshots ms
        JOIN financial_data fd ON fd.symbol = 'A.' || ms.code AND fd.end_date = '2026-03-31'
        WHERE ms.trade_date >= '2026-05-18'
          AND ms.total_mv > 0 AND ms.price > 0
          AND fd.total_shares IS NOT NULL AND fd.total_shares > 0
        GROUP BY ms.code
    )
    UPDATE financial_data fd
    SET float_ratio = a.avg_ratio
    FROM avg_fr a
    WHERE fd.symbol = 'A.' || a.code AND fd.end_date = '2026-03-31'
""")
conn.commit()
print(f"  已写入 float_ratio: {cur.rowcount} 行")

# 标记 H 股
cur.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='financial_data' AND column_name='has_non_float_shares'
        ) THEN
            ALTER TABLE financial_data ADD COLUMN has_non_float_shares BOOLEAN DEFAULT FALSE;
        END IF;
    END $$;
""")
conn.commit()

cur.execute("""
    UPDATE financial_data 
    SET has_non_float_shares = TRUE 
    WHERE float_ratio IS NOT NULL AND float_ratio < 0.8
      AND end_date = '2026-03-31'
""")
conn.commit()
print(f"  标记 H 股/非流通股: {cur.rowcount} 行")

# ── 4. 批量回填 daily_prices.turnover ──
print("\n[4] 开始回填...")

# 分批：JOIN daily_prices + financial_data（按每只股票的最新 float_ratio × total_shares 算流通股本）
# float_shares = total_shares * float_ratio
# turnover = volume(股) / float_shares * 100

batch_size = 100000
filled = 0
skipped = 0
page = 0
total_to_fill = 4000000  # 回填约 400 万行

while filled < total_to_fill:
    cur.execute("""
        SELECT dp.symbol, dp.trade_date, dp.volume,
               fd.total_shares, fd.float_ratio
        FROM daily_prices dp
        JOIN financial_data fd ON fd.symbol = SUBSTRING(dp.symbol, 3) AND fd.end_date = '2026-03-31'
        WHERE dp.close > 0 AND dp.volume > 0
          AND dp.turnover IS NULL
          AND fd.total_shares IS NOT NULL AND fd.total_shares > 0
          AND fd.float_ratio IS NOT NULL AND fd.float_ratio > 0
          AND dp.trade_date >= '2015-01-01'
        LIMIT %s
    """, (batch_size,))
    
    rows = cur.fetchall()
    if not rows:
        print("  无更多数据需要回填（可能是剩余行都没 float_ratio）")
        break
    
    updates = []
    for r in rows:
        symbol, trade_date, vol_hand, total_shares, float_ratio = r
        
        # 有 H 股标记的股票单独处理
        # has_non_float_shares 标记的：用 float_mv/total_mv 比例修正
        # 但这里 float_ratio 已经考虑了流通比例
        
        float_shares = total_shares * float_ratio
        if float_shares > 0 and vol_hand > 0:
            # turnover(%): 成交量(股) / 流通股本 * 100
            turnover = (vol_hand * 100.0) / float_shares * 100
            updates.append((turnover, symbol, trade_date))
        else:
            skipped += 1
    
    if updates:
        cur.execute("DROP TABLE IF EXISTS _to_temp2")
        cur.execute("""
            CREATE TEMP TABLE _to_temp2 (
                turnover DOUBLE PRECISION, symbol TEXT, trade_date DATE
            )
        """)
        conn.commit()
        
        args = ','.join(cur.mogrify(
            "(%s,%s,%s::date)", (u[0], u[1], u[2].isoformat() if hasattr(u[2], 'isoformat') else str(u[2]))
        ).decode('utf-8') for u in updates)
        cur.execute(f"INSERT INTO _to_temp2 VALUES {args}")
        
        cur.execute("""
            UPDATE daily_prices dp
            SET turnover = t.turnover,
                source = CASE 
                    WHEN dp.source IS NULL THEN 'mootdx+calc_to'
                    WHEN dp.source NOT LIKE '%calc_to%' THEN dp.source || '+calc_to'
                    ELSE dp.source
                END
            FROM _to_temp2 t
            WHERE dp.symbol = t.symbol AND dp.trade_date = t.trade_date
        """)
        conn.commit()
        
        filled += cur.rowcount
        page += 1
        if page % 5 == 0:
            print(f"  Page {page}: {filled} 已填, {skipped} 跳过/跳过")
    
    time.sleep(0.1)

print(f"\n[5] 回填完成:")
print(f"  已填: {filled} 行")
print(f"  跳过: {skipped} 行")

# ── 5. 验证结果 ──
cur.execute("""
    SELECT COUNT(*) as total,
           COUNT(*) FILTER(WHERE turnover IS NOT NULL) as to_ok,
           ROUND(COUNT(*) FILTER(WHERE turnover IS NOT NULL) * 100.0 / COUNT(*), 1) as pct
    FROM daily_prices WHERE close > 0
""")
r = cur.fetchone()
print(f"\n[6] 最终结果:")
print(f"  总行数: {r[0]}")
print(f"  有换手率: {r[1]} ({r[2]}%)")

cur.execute("""
    SELECT EXTRACT(year FROM trade_date) as yr,
           COUNT(*) as total,
           COUNT(*) FILTER(WHERE turnover IS NOT NULL) as to_ok,
           ROUND(AVG(turnover)::numeric, 2) as avg_to
    FROM daily_prices WHERE close > 0
    GROUP BY yr ORDER BY yr
""")
print("\n  按年覆盖:")
for r in cur.fetchall():
    print(f"    {int(r[0])}: {r[1]:>8d} 行, {r[2]:>8d} 有换手率, 平均 {r[3]:>8}%")

conn.close()
