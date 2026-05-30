"""
market_snapshots 全量回填（优化版）
从 daily_prices 逐日重建快照
- 只回填 2019-01-01 之后（与event_list对齐）
- 限 active_stock（日线非空的）
- 每批处理，避免 CPU 挂掉
"""
import sys, time
import psycopg2
import psycopg2.extras
import numpy as np
from datetime import datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
START_DATE = '2019-01-01'
END_DATE = '2026-05-22'
BATCH_DAYS = 50  # 每批50天

def get_db():
    return psycopg2.connect(DB)

def main():
    conn = get_db()
    cur = conn.cursor()
    
    print(f"【market_snapshots 回填】{datetime.now()}")
    print(f"  窗口: {START_DATE} ~ {END_DATE}")
    
    # 已有日期
    cur.execute("SELECT DISTINCT trade_date FROM market_snapshots")
    existing = {r[0] for r in cur.fetchall()}
    print(f"  已有 {len(existing)} 天")
    
    # 需要回填的日期
    cur.execute("""
        SELECT DISTINCT trade_date FROM daily_prices 
        WHERE trade_date BETWEEN %s AND %s
        ORDER BY trade_date
    """, (START_DATE, END_DATE))
    all_dates = [r[0] for r in cur.fetchall()]
    need_dates = [d for d in all_dates if d not in existing]
    
    print(f"  总交易日: {len(all_dates)}, 待回填: {len(need_dates)}")
    
    if not need_dates:
        print("  无需回填 ✅")
        conn.close()
        return
    
    total_inserted = 0
    batch_count = 0
    
    for i in range(0, len(need_dates), BATCH_DAYS):
        chunk = need_dates[i:i+BATCH_DAYS]
        batch_count += 1
        print(f"\n  批 {batch_count}: {chunk[0]} ~ {chunk[-1]} ({len(chunk)}天)")
        
        t0 = time.time()
        inserted = 0
        
        for day in chunk:
            # 当天所有股票
            cur.execute("""
                SELECT symbol, close, high, low, volume, amount, turnover
                FROM daily_prices
                WHERE trade_date = %s AND close IS NOT NULL AND volume > 0
                ORDER BY symbol
            """, (day,))
            rows = cur.fetchall()
            if not rows:
                continue
            
            vals = []
            for r in rows:
                sym, close, high, low, vol, amount, turnover = r
                close_f = float(close)
                high_f = float(high) if high else close_f
                low_f = float(low) if low else close_f
                vals.append((
                    sym, day, day,
                    close_f,
                    None,  # change_pct
                    float(amount) if amount else None,
                    float(turnover) if turnover else None,
                    None, None, None, None,
                    float(vol) if vol else 0,
                    high_f, low_f,
                    float((high_f-low_f)/close_f*100) if high_f != low_f else 0.0,
                ))
            
            if vals:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO market_snapshots 
                       (code, trade_date, time, price, change_pct, amount, turnover,
                        pe, pb, total_mv, float_mv, volume, high, low, amplitude)
                       VALUES %s ON CONFLICT (code, trade_date) DO NOTHING""",
                    vals,
                    page_size=2000
                )
                inserted += len(vals)
        
        conn.commit()
        elapsed = time.time() - t0
        total_inserted += inserted
        print(f"    +{inserted}条 | {elapsed:.1f}s | {inserted/elapsed:.0f}条/s | 累计{total_inserted}")
    
    conn.close()
    print(f"\n✅ 完成！总写入: {total_inserted}条")

if __name__ == '__main__':
    main()
