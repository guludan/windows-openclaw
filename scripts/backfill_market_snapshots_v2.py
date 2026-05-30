"""
market_snapshots 全量回填 v2 — 纯SQL批量
从 daily_prices 用 INSERT INTO SELECT 一次性回填
"""
import psycopg2, time
from datetime import datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
START = '2019-01-01'
END = '2026-05-22'
BATCH = 100  # 每批天数

def main():
    conn = psycopg2.connect(DB)
    conn.autocommit = False
    cur = conn.cursor()
    
    print(f"【market_snapshots 回填 v2】{datetime.now()}")
    
    # 已有日期
    cur.execute("SELECT DISTINCT trade_date FROM market_snapshots")
    existing = {r[0] for r in cur.fetchall()}
    
    cur.execute("""
        SELECT DISTINCT trade_date FROM daily_prices 
        WHERE trade_date BETWEEN %s AND %s
        ORDER BY trade_date
    """, (START, END))
    all_dates = [r[0] for r in cur.fetchall()]
    need_dates = [d for d in all_dates if d not in existing]
    
    print(f"  总: {len(all_dates)}天, 已有: {len(existing)}天, 待回填: {len(need_dates)}天")
    
    if not need_dates:
        print("  完成 ✅")
        conn.close()
        return
    
    total = 0
    t0 = time.time()
    
    for i in range(0, len(need_dates), BATCH):
        chunk = need_dates[i:i+BATCH]
        d_start, d_end = chunk[0], chunk[-1]
        
        # 直接用 INSERT INTO SELECT 批量迁移
        sql = """
            INSERT INTO market_snapshots 
                (code, trade_date, time, price, change_pct, amount, turnover, 
                 pe, pb, total_mv, float_mv, volume, high, low, amplitude)
            SELECT 
                p.symbol, p.trade_date, p.trade_date, 
                p.close, 
                (p.close - pg.close) / NULLIF(pg.close, 0) * 100 AS change_pct,
                p.amount, p.turnover,
                NULL, NULL, NULL, NULL, 
                p.volume, p.high, p.low,
                (p.high - p.low) / NULLIF(p.close, 0) * 100 AS amplitude
            FROM daily_prices p
            LEFT JOIN LATERAL (
                SELECT close FROM daily_prices prev
                WHERE prev.symbol = p.symbol 
                  AND prev.trade_date < p.trade_date
                  AND prev.trade_date >= p.trade_date - 30
                ORDER BY prev.trade_date DESC
                LIMIT 1
            ) pg ON TRUE
            WHERE p.trade_date BETWEEN %s AND %s
              AND p.close IS NOT NULL
              AND p.volume > 0
            ON CONFLICT (code, trade_date) DO NOTHING
        """
        
        t1 = time.time()
        cur.execute(sql, (d_start, d_end))
        conn.commit()
        n = cur.rowcount
        total += n
        elapsed = time.time() - t1
        
        rate = n / elapsed if elapsed > 0 else 0
        pct = (i + len(chunk)) / len(need_dates) * 100
        eta = (len(need_dates) - i - len(chunk)) / max(1, len(chunk)) * elapsed / 60
        
        print(f"  [{pct:4.0f}%] {d_start}~{d_end} ({len(chunk)}天) → {n:>10,}条 | {elapsed:.1f}s | {rate:,.0f}条/s | ETA {eta:.0f}min")
    
    conn.close()
    total_time = (time.time() - t0) / 60
    print(f"\n✅ 完成！总写入: {total:,}条 | 耗时: {total_time:.1f}min")

if __name__ == '__main__':
    main()
