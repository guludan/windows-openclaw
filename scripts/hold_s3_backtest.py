#!/usr/bin/env python3
"""
增减持数据获取 + S3 关联回测

数据源：东方财富 API（通过 akshare stock_share_hold_change_sse + 按股票查 szse）
回测：S3 触发前 N 日，是否有大股东/高管增持或公司回购
"""
import psycopg2, psycopg2.extras
import numpy as np
import pandas as pd
import time, json
from datetime import datetime, date, timedelta
from collections import defaultdict
import akshare as ak

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
t0 = time.time()

print("=" * 60)
print("[增减持 × S3 联动]", datetime.now().strftime("%H:%M"))
print("=" * 60)

conn = psycopg2.connect(DB)

# ===== 1) S3 信号 + T+20 =====
print("\n[1] 加载 S3 信号...")
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

print(f"  S3: {len(records)} 条")

# ===== 2) 拉取增减持数据 =====
print("\n[2] 拉取增减持...")

# 拉取上交所高管增减持
print("  ⏳ 上交所董监高变动...")
sse_df = ak.stock_share_hold_change_sse()
sse_df.columns = [c.replace(" ", "") for c in sse_df.columns]
# 解析：只有变动数>0和<0才是增减持
sse_events = []
for _, r in sse_df.iterrows():
    code = str(r['公司代码']).strip()
    d = r['变动日期']
    if isinstance(d, str):
        d = datetime.strptime(d, '%Y-%m-%d').date()
    elif hasattr(d, 'date'):
        d = d.date()
    change = r['变动数']
    avg_price = r['本次变动平均价格']
    sse_events.append({
        'code': code,
        'date': d,
        'change': change,
        'price': float(avg_price) if avg_price else 0,
        'is_buy': change > 0,
        'is_sell': change < 0,
        'source': 'sse_exec',
    })
print(f"    上交所: {len(sse_events)} 条")

# 按股票+日期聚合
changes_by_stock = defaultdict(lambda: defaultdict(lambda: {'total_buy': 0, 'total_sell': 0, 'n_buy': 0, 'n_sell': 0}))
for ev in sse_events:
    key = f"{ev['code']}@{ev['date']}"
    if ev['is_buy']:
        changes_by_stock[ev['code']][ev['date']]['total_buy'] += abs(ev['change'])
        changes_by_stock[ev['code']][ev['date']]['n_buy'] += 1
    if ev['is_sell']:
        changes_by_stock[ev['code']][ev['date']]['total_sell'] += abs(ev['change'])
        changes_by_stock[ev['code']][ev['date']]['n_sell'] += 1

# 拉取回购数据
print("  ⏳ 回购数据...")
repu_df = ak.stock_repurchase_em()
repu_df = repu_df[repu_df['实施进度'] != '完成实施'].copy()  # 只看进行中未完成的
repu_df['股票代码'] = repu_df['股票代码'].astype(str)
repu_events = []
for _, r in repu_df.iterrows():
    code = r['股票代码'].strip().zfill(6)
    d = r['最新公告日期']
    if isinstance(d, str):
        d = datetime.strptime(d[:10], '%Y-%m-%d').date()
    elif hasattr(d, 'date'):
        d = d.date()
    repu_events.append({
        'code': code,
        'date': d,
        'source': 'repurchase',
    })
print(f"    回购: {len(repu_events)} 条")

# 回购按股票索引
repu_by_stock = defaultdict(set)
for ev in repu_events:
    repu_by_stock[ev['code']].add(ev['date'])

# ===== 3) 关联 S3 × 增减持 × 回购 =====
print("\n[3] 关联 S3 × 增减持 × 回购...")

# S3 全部是沪市股票？检查
ss_codes = set()
for r in records:
    sc = r['stock_code']
    # 6xxxxx = 沪市, 0xxxxx/002xxx/3xxxxx = 深市
    if sc.startswith('60') or sc.startswith('68'):
        ss_codes.add(sc)

print(f"  沪市 S3: {len(ss_codes)} 只股票")

# 只分析沪市（因为上交所才有高管增减持数据）
sh_records = [r for r in records if r['stock_code'].startswith('60') or r['stock_code'].startswith('68')]
print(f"  仅沪市 S3: {len(sh_records)} 条")

# 关联
for r in sh_records:
    sc = r['stock_code']
    d = r['date']
    r['has_buy_before'] = False
    r['has_sell_before'] = False
    r['has_repurchase'] = False
    
    # 前 30 日
    for delta in range(7, 31):
        dd = d - timedelta(days=delta)
        if sc in changes_by_stock and dd in changes_by_stock[sc]:
            info = changes_by_stock[sc][dd]
            if info['n_buy'] > 0:
                r['has_buy_before'] = True
            if info['n_sell'] > 0:
                r['has_sell_before'] = True
    
    # 回购
    if sc in repu_by_stock:
        for repu_date in repu_by_stock[sc]:
            if repu_date < d and (d - repu_date).days <= 60:
                r['has_repurchase'] = True
                break

df = pd.DataFrame(sh_records)

# ===== 表1: 增减持 × S3 =====
print(f"\n{'=' * 60}")
print("[表1] 董监高增减持 × S3")
print(f"{'=' * 60}")

for period, lbl in [('train', '2022~2023'), ('test', '2024~2026')]:
    sub = df[df['period'] == period]
    if len(sub) < 3:
        continue
    print(f"\n  [{lbl}] {len(sub)} S3 (仅沪市)")
    print(f"  {'条件':<30} {'n':<10} {'T+20均值':<14} {'胜率':<10} {'增益':>10}")
    print(f"  {'-'*74}")
    
    baseline = sub['ret_20'].mean()
    for cond_name, mask in [
        ('有高管增持(30d内)', sub['has_buy_before']),
        ('有高管减持(30d内)', sub['has_sell_before']),
        ('有回购(60d内)', sub['has_repurchase']),
        ('无高管变动', (~sub['has_buy_before']) & (~sub['has_sell_before'])),
    ]:
        fil = sub[mask]
        if len(fil) < 3:
            continue
        rets = fil['ret_20'].values
        gain = np.mean(rets) - baseline
        print(f"  {cond_name:<30} {len(fil):<10} {np.mean(rets):+10.2%}   {np.mean(rets > 0):.1%}  {gain:+9.2%}")

# ===== 表2: 联合方向 =====
print(f"\n{'=' * 60}")
print("[表2] 联合方向（测试集）")
print(f"{'=' * 60}")

test = df[df['period'] == 'test']
if len(test) > 3:
    print(f"\n  {'联合条件':<36} {'n':<10} {'T+20均值':<14} {'胜率':<10}")
    print(f"  {'-'*70}")
    
    for cond_name, mask in [
        ('增持+无减持', test['has_buy_before'] & ~test['has_sell_before']),
        ('减持+无增持', test['has_sell_before'] & ~test['has_buy_before']),
        ('回购+无减持', test['has_repurchase'] & ~test['has_sell_before']),
        ('增持+回购', test['has_buy_before'] & test['has_repurchase']),
        ('无任何利好', (~test['has_buy_before']) & (~test['has_repurchase'])),
    ]:
        fil = test[mask]
        if len(fil) < 2:
            continue
        rets = fil['ret_20'].values
        print(f"  {cond_name:<36} {len(fil):<10} {np.mean(rets):+10.2%}   {np.mean(rets > 0):.1%}")

elapsed = time.time() - t0
print(f"\n耗时: {elapsed:.0f}s")
conn.close()
print("✅ 完成")
