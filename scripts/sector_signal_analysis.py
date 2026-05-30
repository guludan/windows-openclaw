"""
按行业分解pool_energy信号 → 行业级胜率矩阵
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, json
import numpy as np
import pandas as pd
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
conn = psycopg2.connect(DB)
cur = conn.cursor()

print("="*60)
print("行业级 [B]/[A] 信号分析")
print("="*60)

# 1) 加载行业映射
print("\n[1] 加载行业映射 (level1)...")
cur.execute("""
    SELECT industry, stock_code as code FROM industry_components_ths_l1
    UNION
    SELECT i.industry_name, i.stock_code FROM industry_components_ths i
""")
stock_to_industry = {}
industry_stocks = defaultdict(set)
for r in cur.fetchall():
    ind = r[0]
    code = r[1]
    stock_to_industry[code] = ind
    industry_stocks[ind].add(code)

print(f"  映射股票: {len(stock_to_industry)}只, 行业: {len(industry_stocks)}个")
industries = sorted(industry_stocks.keys(), key=lambda x: -len(industry_stocks[x]))
for ind in industries[:10]:
    print(f"    {ind}: {len(industry_stocks[ind])}只")

# 2) 加载pool_energy信号
print("\n[2] 加载pool_energy信号+T20收益...")
cur.execute("""
    WITH signals AS (
        SELECT replace(e.level, 'code:A.', '') as code, e.date, e.surplus, e.b_inject, e.a_inject,
               e.extra->>'sigs' as sig_type, e.extra->>'ret_0' as ret_0,
               e.extra->>'vol_z' as vol_z, e.extra->>'v_z' as v_z
        FROM pool_energy e
        WHERE e.level LIKE 'code:A.%'
          AND e.date >= '2020-01-01'
          AND e.extra->>'sigs' IN ('s2_pulse', 's3_panic', 's1_surge')
    )
    SELECT s.code, s.date, s.surplus, s.sig_type, 
           s.ret_0, s.vol_z, s.v_z,
           p5.close as c0, p20.close as c20
    FROM signals s
    LEFT JOIN daily_prices p5 ON p5.symbol = 'A.' || s.code AND p5.trade_date = s.date
    LEFT JOIN LATERAL (
        SELECT close FROM daily_prices dp WHERE dp.symbol = 'A.' || s.code AND dp.trade_date > s.date
        ORDER BY dp.trade_date OFFSET 19 LIMIT 1
    ) p20 ON true
    WHERE p5.close IS NOT NULL AND p20.close IS NOT NULL
""")
rows = cur.fetchall()
print(f"  总信号: {len(rows)}条")

# 3) 按行业归类
print("\n[3] 按行业分析...")

# 按行业统计各信号
industry_sig = defaultdict(lambda: defaultdict(list))

mapped = 0
not_mapped = 0
for r in rows:
    code = r[0]
    ind = stock_to_industry.get(code)
    if not ind:
        not_mapped += 1
        continue
    mapped += 1
    
    sig = r[3]
    ret_20 = r[8] / r[7] - 1 if r[7] and r[8] else None
    if ret_20 is None:
        continue
    
    industry_sig[ind][sig].append({
        'ret_20': ret_20,
        'surplus': r[2],
        'date': r[1],
    })

print(f"  有行业映射: {mapped}条, 无映射: {not_mapped}条")

# 4) 输出行业×信号统计表
print("\n[4] 行业×S2脉冲 表现 (要求≥30次)")
print(f"\n{'行业':<15} {'S2次数':<8} {'S2中位':<10} {'S2胜率':<8} "
      f"{'S3次数':<8} {'S3中位':<10} {'S3胜率':<8} "
      f"{'S1次数':<8} {'S1中位':<10} {'S1胜率':<8}")
print("-"*95)

summary = []
for ind in sorted(industry_sig.keys()):
    s2 = industry_sig[ind]['s2_pulse']
    s3 = industry_sig[ind]['s3_panic']
    s1 = industry_sig[ind]['s1_surge']
    
    s2_med = np.median([x['ret_20'] for x in s2]) if len(s2) >= 30 else None
    s2_win = sum(1 for x in s2 if x['ret_20'] > 0) / len(s2) * 100 if len(s2) >= 30 else None
    s3_med = np.median([x['ret_20'] for x in s3]) if len(s3) >= 30 else None
    s3_win = sum(1 for x in s3 if x['ret_20'] > 0) / len(s3) * 100 if len(s3) >= 30 else None
    s1_med = np.median([x['ret_20'] for x in s1]) if len(s1) >= 30 else None
    s1_win = sum(1 for x in s1 if x['ret_20'] > 0) / len(s1) * 100 if len(s1) >= 30 else None
    
    if s2_med is not None or s3_med is not None:
        s2s = f"{len(s2):<8} {s2_med:+8.2f}  {s2_win:<7.1f}" if s2_med else f"{len(s2):<8} {'-':>8}  {'-':>7}"
        s3s = f"{len(s3):<8} {s3_med:+8.2f}  {s3_win:<7.1f}" if s3_med else f"{len(s3):<8} {'-':>8}  {'-':>7}"
        s1s = f"{len(s1):<8} {s1_med:+8.2f}  {s1_win:<7.1f}" if s1_med else f"{len(s1):<8} {'-':>8}  {'-':>7}"
        print(f"{ind:<15} {s2s}  {s3s}  {s1s}")
        
        summary.append({
            'industry': ind,
            's2_n': len(s2), 's2_med': s2_med, 's2_win': s2_win,
            's3_n': len(s3), 's3_med': s3_med, 's3_win': s3_win,
            's1_n': len(s1), 's1_med': s1_med, 's1_win': s1_win,
        })

# 5) S2卖出信号最强/最弱行业
print(f"\n[5] 最佳S2卖出行业 (S2中位<2%)")
s2_sorted = sorted([s for s in summary if s['s2_med'] is not None and s['s2_n'] >= 30], 
                   key=lambda x: x['s2_med'])
for s in s2_sorted[:10]:
    print(f"  {s['industry']:<15} S2: {s['s2_n']}次, 中位={s['s2_med']:.2f}, 胜率={s['s2_win']:.1f}")

print(f"\n[6] 最佳S3买入行业 (S3中位>+5%)")
s3_sorted = sorted([s for s in summary if s['s3_med'] is not None and s['s3_n'] >= 20], 
                   key=lambda x: -x['s3_med'])
for s in s3_sorted[:10]:
    print(f"  {s['industry']:<15} S3: {s['s3_n']}次, 中位={s['s3_med']:.2f}, 胜率={s['s3_win']:.1f}")

conn.close()
print(f"\n✅ 完成")
