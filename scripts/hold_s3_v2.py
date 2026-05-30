#!/usr/bin/env python3
"""
上交所董监高增减持 × S3 联动回测

数据：直接从 SSE 接口获取的8200条2022~2026年董监高变动
S3信号：PG pool_energy
"""
import psycopg2, psycopg2.extras
import numpy as np
import json, time
from datetime import datetime, date, timedelta
from collections import defaultdict, Counter
import pandas as pd

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
t0 = time.time()

print("=" * 70)
print("[增减持 × S3 回测 v2]", datetime.now().strftime("%H:%M"))
print("=" * 70)

# ===== 1) 加载上交所董监高数据 =====
print("\n[1] 加载上交所董监高变动...")
with open('/tmp/sse_holdings.json') as f:
    rows = json.load(f)

# 过滤2022年以后的
events = []
for r in rows:
    d = r.get('CHANGE_DATE', '')
    if not d or d < '2022-01-01':
        continue
    try:
        d = datetime.strptime(d[:10], '%Y-%m-%d').date()
    except:
        continue
    code = r.get('COMPANY_CODE', '').strip()
    num = r.get('CHANGE_NUM', 0)
    if num is None: num = 0
    price = r.get('CHANGE_PRICE', 0)
    reason = r.get('CHANGE_REASON', '')
    
    events.append({
        'code': code,
        'date': d,
        'num': int(num),
        'price': float(price) if price else None,
        'reason': reason,
        'is_incr': int(num) > 0,
        'is_decr': int(num) < 0,
    })

print(f"  董监高变动: {len(events)} 条 ({sum(1 for e in events if e['is_incr'])} 增持, {sum(1 for e in events if e['is_decr'])} 减持)")

# 按股票+日期聚合
incr_by_stock = defaultdict(lambda: defaultdict(list))  # {code: {date: [nums]}}
decr_by_stock = defaultdict(lambda: defaultdict(list))
for e in events:
    if e['is_incr']:
        incr_by_stock[e['code']][e['date']].append(e['num'])
    if e['is_decr']:
        decr_by_stock[e['code']][e['date']].append(abs(e['num']))

# ===== 2) S3 信号 =====
print("\n[2] 加载 S3 信号...")
conn = psycopg2.connect(DB)
cur = conn.cursor()
cur.execute("""
    WITH s3_raw AS (
        SELECT level, date
        FROM pool_energy
        WHERE level LIKE 'code:%%' AND date >= '2022-01-01'
          AND (extra->>'sigs') LIKE '%%s3%%'
    ),
    s3_prices AS (
        SELECT s.level, s.date,
               p.close as c0,
               p2.close as c20,
               SUBSTRING(s.level FROM 8) as stock_code
        FROM s3_raw s
        JOIN daily_prices p ON p.symbol = 'A.' || SUBSTRING(s.level FROM 8) AND p.trade_date = s.date
        JOIN LATERAL (
            SELECT p2.close FROM daily_prices p2
            WHERE p2.symbol = 'A.' || SUBSTRING(s.level FROM 8) AND p2.trade_date > s.date
            ORDER BY p2.trade_date
            OFFSET 19 LIMIT 1
        ) p2 ON true
        WHERE p.close > 0 AND p2.close > 0
    )
    SELECT level, date, stock_code, c0, c20
    FROM s3_prices
    ORDER BY date
""")
rows = cur.fetchall()
cur.close()
conn.close()

records = []
for row in rows:
    d = row[1].date() if hasattr(row[1], 'date') else row[1]
    records.append({
        'date': d,
        'symbol': row[0],
        'stock_code': row[2],
        'ret_20': row[4] / row[3] - 1 if row[3] > 0 else 0,
        'period': 'train' if d <= date(2023, 12, 31) else 'test',
    })

# 只保留沪市
sh_records = [r for r in records if r['stock_code'].startswith('6')]
print(f"  S3 总计: {len(records)} 条, 沪市: {len(sh_records)} 条")

# ===== 3) 关联 =====
print("\n[3] 关联...")

# 前 N 日内是否有增持/减持
def has_event_before(records, by_stock, lookback_days, min_amount=0):
    results = []
    for r in records:
        sc = r['stock_code']
        found = False
        for d in range(1, lookback_days + 1):
            dd = r['date'] - timedelta(days=d)
            if sc in by_stock and dd in by_stock[sc]:
                total = sum(by_stock[sc][dd])
                if total >= min_amount:
                    found = True
                    break
        results.append(found)
    return results

# 多个回看窗口
for lb in [7, 14, 30]:
    incrs = has_event_before(sh_records, incr_by_stock, lb)
    decrs = has_event_before(sh_records, decr_by_stock, lb)
    
    for i, r in enumerate(sh_records):
        r[f'incr_{lb}d'] = incrs[i]
        r[f'decr_{lb}d'] = decrs[i]

df = pd.DataFrame(sh_records)

# ===== 回测表 =====
for period, lbl in [('train', '2022~2023'), ('test', '2024~2026')]:
    sub = df[df['period'] == period]
    if len(sub) < 10:
        continue
    baseline = sub['ret_20'].mean()
    
    print(f"\n{'=' * 70}")
    print(f"[{lbl}] {len(sub)} S3 (沪市) — 基线 {baseline:+8.2%}")
    print(f"{'=' * 70}")
    
    for lb in [7, 14, 30]:
        print(f"\n  --- lookback={lb}d ---")
        print(f"  {'条件':<30} {'n':<8} {'T+20均值':<14} {'胜率':<10} {'增益':>10}")
        for cond_name, mask in [
            (f'高管增持≤{lb}d', sub[f'incr_{lb}d']),
            (f'高管减持≤{lb}d', sub[f'decr_{lb}d']),
            (f'增持+无减持', sub[f'incr_{lb}d'] & ~sub[f'decr_{lb}d']),
            (f'减持+无增持', sub[f'decr_{lb}d'] & ~sub[f'incr_{lb}d']),
            (f'无变动', (~sub[f'incr_{lb}d']) & (~sub[f'decr_{lb}d'])),
        ]:
            fil = sub[mask]
            if len(fil) < 3:
                continue
            rets = fil['ret_20'].values
            gain = np.mean(rets) - baseline
            print(f"  {cond_name:<30} {len(fil):<8} {np.mean(rets):+10.2%}  {np.mean(rets > 0):.1%}  {gain:+9.2%}")

# ===== 细化：增持金额分级 =====
print(f"\n{'=' * 70}")
print("[细化] 增持金额分级（测试集 lookback=30d）")
print(f"{'=' * 70}")

test = df[df['period'] == 'test']
total_incr = []
for _, r in test.iterrows():
    sc = r['stock_code']
    total = 0
    for d in range(1, 31):
        dd = r['date'] - timedelta(days=d)
        if sc in incr_by_stock and dd in incr_by_stock[sc]:
            total += sum(incr_by_stock[sc][dd])
    total_incr.append(total)

test = test.copy()
test['total_incr'] = total_incr

# 分级
high_q = test['total_incr'].quantile(0.75) if test['total_incr'].max() > 0 else 0
non_zero = test[test['total_incr'] > 0]
zero = test[test['total_incr'] == 0]

print(f"\n  总测试集: {len(test)}")
print(f"  有增持: {len(non_zero)}, 平均 T+20: {non_zero['ret_20'].mean():+8.2%}")
print(f"  无增持: {len(zero)}, 平均 T+20: {zero['ret_20'].mean():+8.2%}")

if len(non_zero) >= 20:
    # 分位数
    med = test['total_incr'].median()
    hi = non_zero[non_zero['total_incr'] > med]
    lo = non_zero[non_zero['total_incr'] <= med]
    print(f"  增持>中位数: {len(hi)}, T+20: {hi['ret_20'].mean():+8.2%}")
    print(f"  增持≤中位数: {len(lo)}, T+20: {lo['ret_20'].mean():+8.2%}")

# ===== 时间敏感性 =====
print(f"\n{'=' * 70}")
print("[时间敏感性] 增持发生在S3前 vs 较远前（测试集）")
print(f"{'=' * 70}")

test['incr_7d'] = has_event_before(test.to_dict('records'), incr_by_stock, 7)
test['incr_14d'] = has_event_before(test.to_dict('records'), incr_by_stock, 14)
test['incr_30d'] = has_event_before(test.to_dict('records'), incr_by_stock, 30)
# 7d+14d = 仅新增的覆盖
test['incr_7_14'] = test['incr_14d'] & ~test['incr_7d']
test['incr_14_30'] = test['incr_30d'] & ~test['incr_14d']

for cond, lbl in [('incr_7d', '7d内增持'), ('incr_7_14', '7~14d内增持'), ('incr_14_30', '14~30d内增持')]:
    fil = test[test[cond]]
    if len(fil) >= 3:
        print(f"  {lbl:<20} n={len(fil):>4}, T+20={fil['ret_20'].mean():+8.2%}, 胜率={fil['ret_20'].gt(0).mean():.1%}")

elapsed = time.time() - t0
print(f"\n耗时: {elapsed:.0f}s")
print("✅ 完成")
