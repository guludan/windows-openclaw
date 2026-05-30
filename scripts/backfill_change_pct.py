"""
补 market_snapshots change_pct
"""
import psycopg2, time
from datetime import datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
BATCH = 200  # 每批200天

def main():
    conn = psycopg2.connect(DB)
    conn.autocommit = False
    cur = conn.cursor()
    
    print(f"【补 change_pct】{datetime.now()}")
    
    # 获取所有需要补的天
    cur.execute("""
        SELECT DISTINCT trade_date FROM market_snapshots
        WHERE change_pct IS NULL AND trade_date >= '2019-01-01'
        ORDER BY trade_date
    """)
    dates = [r[0] for r in cur.fetchall()]
    print(f"  待补: {len(dates)}天 ({dates[0]}~{dates[-1]})")
    
    total = 0
    t0 = time.time()
    
    for i in range(0, len(dates), BATCH):
        chunk = dates[i:i+BATCH]
        ds, de = chunk[0], chunk[-1]
        
        # 用lag算前一个交易日的close
        sql = """
            UPDATE market_snapshots ms
            SET change_pct = (ms.price - p.prev_price) / NULLIF(p.prev_price, 0) * 100
            FROM (
                SELECT code, trade_date,
                       LAG(price) OVER (PARTITION BY code ORDER BY trade_date) as prev_price
                FROM market_snapshots
                WHERE trade_date BETWEEN %s AND %s
            ) p
            WHERE ms.code = p.code 
              AND ms.trade_date = p.trade_date
              AND ms.change_pct IS NULL
              AND p.prev_price IS NOT NULL
        """
        
        t1 = time.time()
        cur.execute(sql, (ds, de))
        conn.commit()
        n = cur.rowcount
        total += n
        elapsed = time.time() - t1
        
        pct = min(100, (i + len(chunk)) / len(dates) * 100)
        print(f"  [{pct:3.0f}%] {ds}~{de} → {n:>8,}条 | {elapsed:.1f}s | 累计{total:,}")
    
    conn.close()
    elapsed = time.time() - t0
    print(f"\n✅ 完成！总更新: {total:,}条 | {elapsed:.0f}s")
    
if __name__ == '__main__':
    main()
