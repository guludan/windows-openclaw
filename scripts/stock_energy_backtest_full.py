"""
pool_energy 全量回测 v2 — 纯SQL分组统计 + 收益回测
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, json
import numpy as np
import pandas as pd
from collections import defaultdict
from datetime import datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

def main():
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    print("="*60)
    print("pool_energy 全量回测 v2", datetime.now().strftime('%Y-%m-%d %H:%M'))
    print("="*60)
    
    # 1) 加载pool_energy信号 + 对应的daily_prices收益一次性关联
    print("\n[1] 加载信号+收益...")
    cur.execute("""
        SELECT e.level, e.date, e.b_pool, e.a_pool, e.surplus, e.b_inject, e.a_inject,
               (e.extra->>'sigs') as sig_type,
               (e.extra->>'b_mkt')::float as b_mkt,
               (e.extra->>'a_mkt')::float as a_mkt,
               p5.close as close_0,
               p20.close as close_20
        FROM pool_energy e
        LEFT JOIN LATERAL (
            SELECT close FROM daily_prices
            WHERE symbol = substring(e.level, 6) AND trade_date = e.date
            LIMIT 1
        ) p5 ON true
        LEFT JOIN LATERAL (
            SELECT close FROM daily_prices dp
            WHERE dp.symbol = substring(e.level, 6) AND dp.trade_date > e.date
            ORDER BY dp.trade_date OFFSET 19 LIMIT 1
        ) p20 ON true
        WHERE e.level LIKE 'code:%%'
          AND e.date >= '2020-01-01'
          AND p5.close IS NOT NULL AND p20.close IS NOT NULL
        ORDER BY e.date
    """)
    
    rows = cur.fetchall()
    print(f"  信号+收益: {len(rows)}条")
    
    # 2) 分组统计
    print("\n[2] 分组统计:")
    
    groups = defaultdict(list)
    for r in rows:
        sig = r[7]  # sig_type
        ret_20 = r[11] / r[10] - 1  # close_20 / close_0 - 1
        groups[sig].append({
            'ret_20': ret_20,
            'surplus': r[4],
            'b_mkt': r[7],
            'a_mkt': r[8],
        })
    
    print(f"\n{'信号类型':<15} {'次数':<8} {'T+20均值':<10} {'T+20中位':<10} {'T+20胜率':<8} {'盈余均值':<10}")
    print("-"*65)
    
    for sig in ['s1_surge', 's2_pulse', 's3_panic']:
        grp = groups.get(sig, [])
        if len(grp) < 5:
            continue
        r20s = [g['ret_20'] for g in grp]
        surps = [g['surplus'] for g in grp]
        mean_r = np.mean(r20s)
        med_r = np.median(r20s)
        win = sum(1 for r in r20s if r > 0) / len(r20s)
        print(f"{sig:<15} {len(grp):<8} {mean_r:+8.2%}  {med_r:+8.2%}  {win:<7.1%} {np.mean(surps):+.4f}")
    
    # 3) S2详细分层
    print("\n[3] S2脉冲分层...")
    s2 = groups.get('s2_pulse', [])
    if len(s2) > 100:
        s2_df = pd.DataFrame(s2)
        
        # 按surplus分层
        s2_df['surp_bin'] = pd.qcut(s2_df['surplus'].rank(method='first'), 3, 
                                       labels=['低盈余', '中盈余', '高盈余'])
        print("\n  按盈余分层:")
        for label, grp in s2_df.groupby('surp_bin', observed=True):
            r20 = grp['ret_20'].dropna() if hasattr(grp['ret_20'], 'dropna') else grp['ret_20']
            print(f"    {label}: {len(grp)}次, T+20均值={np.mean(r20):+.2%}, 中位={np.median(r20):+.2%}, 胜率={sum(r20>0)/len(r20):.1%}")
    
    conn.close()
    print(f"\n✅ 完成 ({datetime.now().strftime('%H:%M')})")

if __name__ == '__main__':
    main()
