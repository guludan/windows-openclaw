#!/usr/bin/env python3
"""
stock_energy_incr.py — 个股 pool_energy 增量补全

问题：stock_energy_full 只在有 S1/S2/S3 信号的日子写记录，
导致无信号日个股 phase 查不到。

本脚本：对每只股票，在无信号的交易日也写入一条 baseline（大盘基线+0注入），
保证每天都有记录可查。

运行方式：
  python3 stock_energy_incr.py              # 补全 4/23~5/26
  python3 stock_energy_incr.py --start YYYY-MM-DD --end YYYY-MM-DD  # 指定范围
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, psycopg2.extras, json, time, argparse
from datetime import date, timedelta
import numpy as np

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2026-04-23')
    parser.add_argument('--end', default='2026-05-26')
    parser.add_argument('--batch-size', type=int, default=500)
    parser.add_argument('--limit-stocks', type=int, default=0, help='0=all')
    args = parser.parse_args()
    
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    t0 = time.time()
    
    # 1) 加载大盘基线
    print(f"\n[1] 加载大盘相空间...")
    cur.execute("SELECT date, b_pool, a_pool, phase FROM pool_energy WHERE level='market' ORDER BY date")
    mkt = {r[0]: {'b': r[1], 'a': r[2], 'phase': r[3] or 'NEUTRAL'} for r in cur.fetchall()}
    print(f"  {len(mkt)} 天 ({time.time()-t0:.0f}s)")
    
    # 2) 确定需要补的日期范围
    start_dt = date.fromisoformat(args.start)
    end_dt = date.fromisoformat(args.end)
    dates = [d for d in sorted(mkt.keys()) if start_dt <= d <= end_dt]
    print(f"\n[2] 补全范围: {len(dates)} 天 ({start_dt} ~ {end_dt})")
    
    # 3) 获取股票
    cur.execute("""
        SELECT DISTINCT symbol FROM daily_prices 
        WHERE trade_date >= '2020-01-01' AND close > 0
        GROUP BY symbol HAVING COUNT(*) > 300
        ORDER BY symbol
    """)
    stks = [r[0] for r in cur.fetchall()]
    if args.limit_stocks > 0:
        stks = stks[:args.limit_stocks]
    print(f"  股票: {len(stks)} 只 ({time.time()-t0:.0f}s)")
    
    # 4) 检查已有记录，只补缺失的 (level, date)
    sql = "SELECT level, date FROM pool_energy WHERE level LIKE 'code:A.%%' AND date >= %s AND date <= %s"
    cur.execute(sql, (start_dt, end_dt))
    existing = set(cur.fetchall())
    print(f"  已有记录: {len(existing)} ({time.time()-t0:.0f}s)")
    
    # 5) 逐只股票、逐日检查缺失
    batch = []
    total_added = 0
    missing_stocks = 0
    
    for i, sym in enumerate(stks):
        if sym.startswith('code:'):
            level = sym
        else:
            level = 'code:' + sym
        
        stk_missing = []
        for d in dates:
            if (level, d) not in existing:
                stk_missing.append(d)
        
        if not stk_missing:
            continue
        
        missing_stocks += 1
        for d in stk_missing:
            m = mkt.get(d)
            if not m:
                continue
            
            b_stock = m['b']
            a_stock = m['a']
            surplus = b_stock - a_stock
            imbalance = (b_stock - a_stock) / max(b_stock + a_stock, 0.01)
            phase = m['phase']
            
            extra = json.dumps({
                'sigs': 'no_signal',
                'b_in': 0, 'a_in': 0,
                'b_mkt': b_stock, 'a_mkt': a_stock,
                'total_pool': round(b_stock + a_stock, 4),
            })
            
            batch.append((
                level, d,
                round(b_stock, 6), round(a_stock, 6),
                0.0, 0.0,
                round(surplus, 6), round(imbalance, 5),
                phase if phase else 'NEUTRAL', extra,
            ))
        
        if len(batch) >= args.batch_size:
            _write_batch(conn, batch)
            total_added += len(batch)
            batch = []
        
        if (i+1) % 500 == 0 or (i+1) == len(stks):
            elapsed = time.time() - t0
            print(f"  [{i+1}/{len(stks)}] 有缺失: {missing_stocks} 只, 新增: {total_added} ({elapsed:.0f}s)")
    
    if batch:
        _write_batch(conn, batch)
        total_added += len(batch)
    
    # 6) 校验
    sql2 = "SELECT COUNT(*), MIN(date), MAX(date) FROM pool_energy WHERE level LIKE 'code:A.%%' AND date >= %s AND date <= %s"
    cur.execute(sql2, (start_dt, end_dt))
    final_count, min_d, max_d = cur.fetchone()
    
    print(f"\n[6] 完成")
    print(f"  新增记录: {total_added}")
    print(f"  有缺失股票: {missing_stocks} 只")
    print(f"  最终范围: {min_d} ~ {max_d}, {final_count} 条")
    print(f"  总耗时: {time.time()-t0:.0f}s")
    
    # 校验抽样：查一只无信号股每个交易日
    code = '000564'
    cur.execute("""
        SELECT date, phase, extra::text FROM pool_energy 
        WHERE level='code:A.000564' AND date >= '2026-05-20'
        ORDER BY date
    """)
    print(f"\n  校验 000564 (供销大集) 5/20~:")
    for r in cur.fetchall():
        extra = json.loads(r[2])
        sigs = extra.get('sigs', '')
        print(f"    {r[0]} phase={r[1] or 'NEUTRAL':15s} sigs={sigs}")
    
    conn.close()

def _write_batch(conn, batch):
    cur = conn.cursor()
    psycopg2.extras.execute_values(
        cur,
        """INSERT INTO pool_energy 
           (level, date, b_pool, a_pool, b_inject, a_inject, surplus, imbalance, phase, extra)
           VALUES %s
           ON CONFLICT (level, date) DO UPDATE SET
               b_pool=EXCLUDED.b_pool, a_pool=EXCLUDED.a_pool,
               b_inject=EXCLUDED.b_inject, a_inject=EXCLUDED.a_inject,
               surplus=EXCLUDED.surplus, imbalance=EXCLUDED.imbalance,
               phase=EXCLUDED.phase, extra=EXCLUDED.extra, updated_at=NOW()""",
        batch, page_size=500
    )
    conn.commit()
    cur.close()

if __name__ == '__main__':
    main()
