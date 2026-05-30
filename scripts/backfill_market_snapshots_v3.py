"""
market_snapshots 回填 v3 — 分批 INSERT FROM SELECT，去LATERAL
"""
import psycopg2, time
from datetime import datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
BATCH = 50

def main():
    conn = psycopg2.connect(DB)
    conn.autocommit = False
    cur = conn.cursor()
    
    print(f"【market_snapshots 回填 v3】{datetime.now()}")
    
    cur.execute("""SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date) 
                   FROM daily_prices WHERE trade_date >= '2019-01-01'""")
    print(f"  daily_prices 2019+: {cur.fetchall()}")
    
    cur.execute("SELECT DISTINCT trade_date FROM market_snapshots")
    existing = {r[0] for r in cur.fetchall()}
    
    cur.execute("""SELECT DISTINCT trade_date FROM daily_prices 
                   WHERE trade_date BETWEEN '2019-01-01' AND '2026-05-22'
                   ORDER BY trade_date""")
    all_dates = [r[0] for r in cur.fetchall()]
    need_dates = [d for d in all_dates if d not in existing]
    
    print(f"  待回填: {len(need_dates)}天 ({need_dates[0] if need_dates else 'N/A'}~{need_dates[-1] if need_dates else 'N/A'})")
    
    if not need_dates:
        print("  完成 ✅")
        conn.close()
        return
    
    total = 0
    t0 = time.time()
    
    for i in range(0, len(need_dates), BATCH):
        chunk = need_dates[i:i+BATCH]
        ds, de = chunk[0], chunk[-1]
        
        sql = """
            INSERT INTO market_snapshots 
                (code, trade_date, time, price, change_pct, amount, turnover,
                 pe, pb, total_mv, float_mv, volume, high, low, amplitude)
            SELECT 
                p.symbol, p.trade_date, p.trade_date, p.close,
                NULL, p.amount, p.turnover,
                NULL, NULL, NULL, NULL,
                p.volume, p.high, p.low,
                CASE WHEN p.close > 0 THEN (p.high - p.low) / p.close * 100 ELSE NULL END
            FROM daily_prices p
            WHERE p.trade_date BETWEEN %s AND %s
              AND p.close IS NOT NULL AND p.volume > 0
            ON CONFLICT (code, trade_date) DO NOTHING
        """
        
        t1 = time.time()
        cur.execute(sql, (ds, de))
        conn.commit()
        n = cur.rowcount
        total += n
        elapsed = time.time() - t1
        
        pct = min(100, (i + len(chunk)) / len(need_dates) * 100)
        rate = n / elapsed if elapsed > 0 else 0
        print(f"  [{pct:3.0f}%] {ds}~{de} → {n:>8,}条 | {elapsed:.1f}s | {rate:,.0f}条/s | 累计{total:,}")
    
    total_time = time.time() - t0
    print(f"\n✅ 完成！总写入: {total:,}条 | 耗时: {total_time:.0f}s ({total_time/60:.1f}min)")
    
    # 验证
    cur.execute("SELECT COUNT(*) FROM market_snapshots")
    print(f"  market_snapshots 总计: {cur.fetchone()[0]:,}条")
    cur.execute("SELECT MIN(trade_date), MAX(trade_date) FROM market_snapshots")
    print(f"  日期范围: {cur.fetchall()[0]}")
    
    conn.close()

if __name__ == '__main__':
    main()
