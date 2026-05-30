#!/usr/bin/env python3
"""
龙虎榜 × S3 联动回测

验证：S3 恐慌触发时，如果当日/前N日有龙虎榜数据，能否区分反转质量。
特别关注：机构席位净买入 → 增强反转，机构净卖出 → 抑制反转。

csi_lhb_detail 关键列:
  code (纯6位), trade_date, close, change_pct
  lhb_net_buy (龙虎榜净买入, 负=卖超)
  inst_buyers, inst_sellers, inst_net_buy, inst_net_ratio
  reason (上榜原因)
"""
import psycopg2, psycopg2.extras
import numpy as np
from datetime import datetime, date, timedelta
from collections import defaultdict
import pandas as pd
import time

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
t0 = time.time()

print("=" * 60)
print("[龙虎榜 × S3 联动]", datetime.now().strftime("%H:%M"))
print("=" * 60)

conn = psycopg2.connect(DB)

# ===== 1) S3 信号 + T+20 收益 =====
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
               SUBSTRING(s.level FROM 8) as stock_code,
               'A.' || SUBSTRING(s.level FROM 8) as daily_symbol
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
    ret20 = row[4] / row[3] - 1 if row[3] > 0 else 0
    records.append({
        'date': d,
        'symbol': row[0],
        'stock_code': row[2],
        'ret_20': ret20,
        'period': 'train' if d <= date(2023, 12, 31) else 'test',
    })

print(f"  S3 信号: {len(records)} 条 ({sum(1 for r in records if r['period']=='train')} 训练, {sum(1 for r in records if r['period']=='test')} 测试)")

# ===== 2) 龙虎榜数据 =====
print("\n[2] 加载龙虎榜...")
cur = conn.cursor()
cur.execute("""
    SELECT trade_date, code, lhb_net_buy, inst_buyers, inst_sellers, inst_net_buy, inst_net_ratio, reason
    FROM csi_lhb_detail
    WHERE trade_date >= '2022-01-01'
    ORDER BY trade_date
""")
lhb_rows = cur.fetchall()
cur.close()

# 按股票+日期索引
lhb_idx = defaultdict(list)  # {(stock_code, trade_date): [row, ...]}
lhb_stock_by_date = defaultdict(lambda: defaultdict(list))  # {date: {stock_code: [rows]}}
for row in lhb_rows:
    code = row[1]
    d = row[0].date() if hasattr(row[0], 'date') else row[0]
    lhb_idx[(code, d)].append(row)
    lhb_stock_by_date[d][code].append(row)

print(f"  龙虎榜记录: {len(lhb_rows)} 条, 含 {len(lhb_idx)} 个(股票,日期)组合")

# ===== 3) 关联 S3 × 龙虎榜 =====
print("\n[3] 关联 S3 × 龙虎榜...")

# 3a) 当日上龙虎榜
on_lhb = []
on_lhb_inst_buy = []
on_lhb_inst_sell = []
for r in records:
    key = (r['stock_code'], r['date'])
    if key in lhb_idx:
        rows = lhb_idx[key]
        total_net = sum(x[2] or 0 for x in rows)
        total_inst_net = sum(x[5] or 0 for x in rows)
        total_inst_buyers = sum(x[3] or 0 for x in rows)
        total_inst_sellers = sum(x[4] or 0 for x in rows)
        is_inst_net_buy = total_inst_net > 0
        is_inst_net_sell = total_inst_net < 0
        on_lhb.append(True)
        on_lhb_inst_buy.append(is_inst_net_buy)
        on_lhb_inst_sell.append(is_inst_net_sell)
    else:
        on_lhb.append(False)
        on_lhb_inst_buy.append(False)
        on_lhb_inst_sell.append(False)

df = pd.DataFrame(records)
df['on_lhb'] = on_lhb
df['inst_buy'] = on_lhb_inst_buy
df['inst_sell'] = on_lhb_inst_sell

# ===== 表1: 龙虎榜整体区分度 =====
print(f"\n{'=' * 60}")
print("[表1] S3 × 龙虎榜")
print(f"{'=' * 60}")

for period, lbl in [('train', '2022~2023'), ('test', '2024~2026')]:
    sub = df[df['period'] == period]
    if len(sub) < 3:
        continue
    print(f"\n  [{lbl}] {len(sub)} S3")
    print(f"  {'条件':<30} {'n':<10} {'T+20均值':<14} {'胜率':<10}")
    print(f"  {'-'*64}")
    
    for cond_name, mask in [
        ('上龙虎榜', sub['on_lhb']),
        ('未上龙虎榜', ~sub['on_lhb']),
        ('全部', pd.Series([True]*len(sub), index=sub.index)),
    ]:
        fil = sub[mask]
        if len(fil) < 3:
            continue
        rets = fil['ret_20'].values
        print(f"  {cond_name:<30} {len(fil):<10} {np.mean(rets):+10.2%}   {np.mean(rets > 0):.1%}")

# ===== 表2: 机构席位方向 =====
print(f"\n[表2] 机构席位方向（仅上龙虎榜的 S3）")

for period, lbl in [('train', '2022~2023'), ('test', '2024~2026')]:
    sub = df[(df['period'] == period) & df['on_lhb']]
    if len(sub) < 5:
        continue
    print(f"\n  [{lbl}] {len(sub)} S3 上龙虎榜")
    print(f"  {'机构方向':<20} {'n':<10} {'T+20均值':<14} {'胜率':<10}")
    print(f"  {'-'*54}")
    
    for cond_name, mask in [
        ('机构净买入', sub['inst_buy']),
        ('机构净卖出', sub['inst_sell']),
        ('机构无/持平', (~sub['inst_buy']) & (~sub['inst_sell'])),
    ]:
        fil = sub[mask]
        if len(fil) < 3:
            continue
        rets = fil['ret_20'].values
        print(f"  {cond_name:<20} {len(fil):<10} {np.mean(rets):+10.2%}   {np.mean(rets > 0):.1%}")

# ===== 表3: 回看前 N 日龙虎榜 =====
print(f"\n{'=' * 60}")
print("[表3] 前N日龙虎榜累计净方向（测试集）")
print(f"{'=' * 60}")

test_df = df[df['period'] == 'test']
for lb_days in [1, 3, 5, 10]:
    labels = []
    for _, r in test_df.iterrows():
        sd = r['date']
        sc = r['stock_code']
        total_inst_net = 0
        for delta in range(1, lb_days + 1):
            dd = sd - timedelta(days=delta)
            key = (sc, dd)
            if key in lhb_idx:
                for row in lhb_idx[key]:
                    total_inst_net += row[5] or 0
        if total_inst_net > 50000:  # 超过5万元
            labels.append('累计机构净买入')
        elif total_inst_net < -50000:
            labels.append('累计机构净卖出')
        else:
            labels.append('无/微弱')
    
    test_df_tmp = test_df.copy()
    test_df_tmp['label_d'] = labels
    print(f"\n  lookback={lb_days}d:")
    print(f"  {'条件':<24} {'n':<10} {'T+20均值':<14} {'胜率':<10}")
    for cond_name in ['累计机构净买入', '累计机构净卖出', '无/微弱']:
        fil = test_df_tmp[test_df_tmp['label_d'] == cond_name]
        if len(fil) < 3:
            continue
        rets = fil['ret_20'].values
        print(f"  {cond_name:<24} {len(fil):<10} {np.mean(rets):+10.2%}   {np.mean(rets > 0):.1%}")

# ===== 汇总 =====
print(f"\n{'=' * 60}")
print("汇总")
print(f"{'=' * 60}")
elapsed = time.time() - t0

for p, pl in [('train', '训练集'), ('test', '测试集')]:
    sub = df[df['period'] == p]
    if len(sub) < 3:
        continue
    print(f"\n  {pl}: {len(sub)} S3")
    for cond_name in ['on_lhb', 'inst_buy', 'inst_sell']:
        fil = sub[sub[cond_name]]
        if len(fil) >= 3:
            gain = fil['ret_20'].mean() - sub['ret_20'].mean()
            print(f"    {cond_name:20s}: n={len(fil):>4}, T+20={fil['ret_20'].mean():+7.2%}, 胜率={fil['ret_20'].gt(0).mean():.1%}, vs全样本 {sub['ret_20'].mean():+7.2%} (增益{gain:+7.2%})")
            
conn.close()
print(f"\n耗时: {elapsed:.0f}s")
print("✅ 完成")
