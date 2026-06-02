"""
market_snapshots 补缺 — 只补daily_prices有但market_snapshots没有的
2019-2024年日均缺失1,500~2,500只，逐日补录
"""
import psycopg2, time, sys
from datetime import datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

def main():
    conn = psycopg2.connect(DB)
    conn.autocommit = False
    cur = conn.cursor()
    
    print(f"【market_snapshots 补缺】{datetime.now()}")
    
    # 选半年跑
    year_ranges = [
        ('2019-01-01', '2020-06-30'),
        ('2020-07-01', '2021-12-31'),
        ('2022-01-01', '2023-06-30'),
        ('2023-07-01', '2024-12-31'),
    ]
    
    total_inserted = 0
    
    for idx, (sd, ed) in enumerate(year_ranges):
        print(f"\n  期 {idx+1}/{len(year_ranges)}: {sd} ~ {ed}")
        
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
            LEFT JOIN market_snapshots ms 
                ON p.symbol = ms.code AND p.trade_date = ms.trade_date
            WHERE p.trade_date BETWEEN %s AND %s
              AND p.close IS NOT NULL AND p.volume > 0
              AND ms.code IS NULL
            ON CONFLICT (code, trade_date) DO NOTHING
        """
        
        t1 = time.time()
        cur.execute(sql, (sd, ed))
        conn.commit()
        n = cur.rowcount
        total_inserted += n
        elapsed = time.time() - t1
        
        rate = n / elapsed if elapsed > 0 else 0
        print(f"    +{n:,}条 | {elapsed:.0f}s | {rate:,.0f}条/s | 累计{total_inserted:,}")
    
    total_time = time.time() - t1
    conn.close()
    print(f"\n✅ 完成！补录: {total_inserted:,}条 | 耗时: {total_time:.0f}s")

if __name__ == '__main__':
    main()
