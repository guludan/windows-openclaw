#!/usr/bin/env python3
"""
S2 + 行业政策共振回测
验证核心假设：行业政策共振能提升 S2 信号的 T+20 收益和胜率

流程:
1. 从 daily_prices 计算 S2 信号（vol_z>2.5, v_z>2.0, ret>5%）
2. 从 industry_components_ths_l1 获取个股行业
3. 从 event_list + industry_lexicon 识别各日的行业政策事件
4. 对比有/无行业共振的 S2 收益差异
"""
import psycopg2, psycopg2.extras
import numpy as np
import pandas as pd
import time
from datetime import datetime, date, timedelta
from collections import defaultdict
import sys
sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
from industry_lexicon import IndustryMatcher

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
LOOKBACK = 120

print("=" * 60)
print("[S2 + 行业共振回测]", datetime.now().strftime("%H:%M"))
print("=" * 60)
t0 = time.time()

conn = psycopg2.connect(DB)

# ===== 1) 行业映射 =====
print("\n[1] 加载行业成分...")
cur = conn.cursor()
cur.execute("""
    SELECT symbol, ind_code, ind_name 
    FROM industry_components_ths_l1
    WHERE date = (SELECT MAX(date) FROM industry_components_ths_l1)
""")
ind_rows = cur.fetchall()
cur.close()

stock_industry = {}
industry_names_set = set()
for r in ind_rows:
    sym = r[0]
    code = r[1]
    name = r[2] or ""
    stock_industry[sym] = (code, name)
    industry_names_set.add(name)

print(f"  {len(stock_industry)} 只有行业映射, {len(industry_names_set)} 个行业")

# 验证行业词库的 ths_code 是否在 PG 中出现
lexicon_codes = set(v["ths_code"] for v in IndustryMatcher().lexicon.values())
actual_codes = set(n for _, n in stock_industry.values())
missing = lexicon_codes - actual_codes
extra = actual_codes - lexicon_codes
print(f"  词库行业: {len(lexicon_codes)}, PG行业: {len(actual_codes)}")
if missing:
    print(f"  词库中有但PG无的: {missing}")
if extra:
    print(f"  PG中有但词库未覆盖: {sorted(extra)[:10]}")

# ===== 2) 日线 + S2 信号 =====
print("\n[2] S2 信号计算...")
cur = conn.cursor('stream_s2', cursor_factory=psycopg2.extras.DictCursor)
cur.execute("""
    SELECT symbol, trade_date, close, volume, amount
    FROM daily_prices 
    WHERE trade_date >= '2022-01-01' AND close > 0 AND volume > 0
    ORDER BY symbol, trade_date
""")
all_chunks = []
while True:
    chunk = cur.fetchmany(200000)
    if not chunk:
        break
    all_chunks.extend(chunk)
cur.close()

df_p = pd.DataFrame(all_chunks, columns=['symbol', 'trade_date', 'close', 'volume', 'amount'])
df_p['trade_date'] = pd.to_datetime(df_p['trade_date'])
print(f"  日线: {len(df_p):,} 行")

# 筛选有行业映射、且活跃的股票
active = df_p.groupby('symbol').size()
active = active[active >= 160].index.tolist()
active_with_ind = [s for s in active if s in stock_industry]
print(f"  活跃: {len(active)} 只, 有行业映射: {len(active_with_ind)}")

# 信号计算
records = []
for idx, sym in enumerate(active_with_ind):
    try:
        sd = df_p[df_p['symbol'] == sym].sort_values('trade_date').copy()
        n = len(sd)
        if n < LOOKBACK + 21:
            continue
        
        ind_code, ind_name = stock_industry[sym]
        sd['log_ret'] = np.log(sd['close'] / sd['close'].shift(1)).clip(-0.11, 0.11)
        sd['vol_20d'] = sd['log_ret'].rolling(20).std() * np.sqrt(252)
        sd['vol_z'] = (sd['vol_20d'] - sd['vol_20d'].rolling(120).mean()) / sd['vol_20d'].rolling(120).std(ddof=1).clip(lower=1e-6)
        sd['v_z'] = (sd['volume'] - sd['volume'].rolling(20).mean()) / sd['volume'].rolling(20).std().clip(lower=1)
        sd['ret'] = sd['close'].pct_change()
        
        # S2: vol_z > 2.5, v_z > 2.0, ret > 5%
        sd['s2'] = (sd['vol_z'] > 2.5) & (sd['v_z'] > 2.0) & (sd['ret'] > 0.05)
        
        last = -999
        for i in range(LOOKBACK, n - 20):
            if sd.iloc[i]['s2']:
                if last >= 0 and i - last < 20:
                    continue
                last = i
                d = sd.iloc[i]['trade_date'].date() if hasattr(sd.iloc[i]['trade_date'], 'date') else sd.iloc[i]['trade_date']
                c0 = float(sd.iloc[i]['close'])
                c20 = float(sd.iloc[i + 20]['close'])
                records.append({
                    'date': d,
                    'symbol': sym,
                    'ind_code': ind_code,
                    'ind_name': ind_name,
                    'ret_20': c20 / c0 - 1,
                    'period': 'train' if d <= date(2023, 12, 31) else 'test',
                })
    except Exception:
        continue
    
    if (idx + 1) % 1000 == 0:
        print(f"    {idx+1}/{len(active_with_ind)} 股, {len(records)} S2 信号", flush=True)

print(f"  总 S2 信号: {len(records)}")
df_s2 = pd.DataFrame(records)

# ===== 3) CCTV 文本 × 行业匹配 =====
print("\n[3] CCTV 行业文本匹配...")
matcher = IndustryMatcher()

cur = conn.cursor()
cur.execute("""
    SELECT event_id, event_date, title, full_text
    FROM event_list
    WHERE source = 'news_cctv' AND event_date >= '2022-01-01'
      AND full_text IS NOT NULL AND LENGTH(full_text) > 20
    ORDER BY event_date
""")
cctv_rows = cur.fetchall()
cur.close()
print(f"  CCTV 文本: {len(cctv_rows)} 条")

# 逐日行业匹配
daily_ind_hits = defaultdict(lambda: defaultdict(int))
for row in cctv_rows:
    d = row[1]
    if hasattr(d, 'date'):
        d = d.date()
    text = ((row[2] or '') + ' ' + (row[3] or ''))
    matches = matcher.match(text)
    for ind_code, total, conf in matches:
        daily_ind_hits[d][ind_code] += total

print(f"  有行业匹配的天数: {len(daily_ind_hits)}")

# 统计各行业的命中频率
ind_freq = defaultdict(int)
for d, inds in daily_ind_hits.items():
    for ind_code, cnt in inds.items():
        ind_freq[ind_code] += cnt
print(f"  行业命中频次 (top 10):")
for ind_code, cnt in sorted(ind_freq.items(), key=lambda x: -x[1])[:10]:
    print(f"    {ind_code}: {cnt} 次")

# ===== 4) 关联 S2 × 行业共振 =====
print("\n[4] S2 × 行业共振关联...")

def get_industry_hits(signal_date, lookback_cal=7):
    """获取信号日前 N 天的所有行业政策命中"""
    start = signal_date - timedelta(days=lookback_cal)
    all_hits = defaultdict(int)
    for d in range((start - signal_date).days, 1):
        dd = signal_date + timedelta(days=d)
        if dd in daily_ind_hits:
            for ind_code, cnt in daily_ind_hits[dd].items():
                all_hits[ind_code] += cnt
    return dict(all_hits)


# 逐信号检查行业共振
s2_df = pd.DataFrame(records)
has_resonance = []
for _, r in s2_df.iterrows():
    ind_hits = get_industry_hits(r['date'])
    # 该股票所属行业是否在命中列表中
    ind_code = r['ind_code']
    is_resonant = ind_code in ind_hits and ind_hits[ind_code] >= 1
    has_resonance.append(is_resonant)

s2_df['has_resonance'] = has_resonance
s2_df['ind_hits'] = s2_df.apply(
    lambda r: get_industry_hits(r['date']).get(r['ind_code'], 0), axis=1
)

# ===== 表1: 整体对比 =====
print(f"\n{'=' * 60}")
print("[表1] S2 整体 + 行业共振")
print(f"{'=' * 60}")

for period, lbl in [('train', '2022~2023'), ('test', '2024~2026')]:
    sub = s2_df[s2_df['period'] == period]
    if len(sub) < 3:
        continue
    
    print(f"\n  [{lbl}] {len(sub)} S2 信号")
    print(f"  {'条件':<24} {'n':<8} {'T+20均值':<12} {'胜率':<10}")
    print(f"  {'-'*54}")
    
    for cond, fil in [
        ('有行业共振', sub[sub['has_resonance']]),
        ('无行业共振', sub[~sub['has_resonance']]),
        ('全部', sub),
    ]:
        if len(fil) < 3:
            continue
        rets = fil['ret_20'].values
        print(f"  {cond:<24} {len(fil):<8} {np.mean(rets):+7.2%}   {np.mean(rets > 0):.1%}")

# ===== 表2: 各行业细分 =====
print(f"\n{'=' * 60}")
print("[表2] 行业细分（测试集）")
print(f"{'=' * 60}")

test = s2_df[s2_df['period'] == 'test']
if len(test) > 3:
    # 按行业分组
    grouped = test.groupby('ind_code')
    print(f"\n  {'行业':<14} {'总信号':<8} {'共振率':<10} {'有共振均值':<12} {'无共振均值':<12}")
    print(f"  {'-'*60}")
    
    for ind, grp in grouped:
        if len(grp) < 5:
            continue
        n_res = grp['has_resonance'].sum()
        n_total = len(grp)
        res_mean = grp[grp['has_resonance']]['ret_20'].mean() if n_res >= 3 else 0
        nores_mean = grp[~grp['has_resonance']]['ret_20'].mean() if (n_total - n_res) >= 3 else 0
        print(f"  {ind:<14} {n_total:<8} {n_res/n_total:>7.0%}    {res_mean:+8.2%}        {nores_mean:+8.2%}")

# ===== 表3: S3 额外验证（看行业共振对 S3 有无效果）=====
print(f"\n{'=' * 60}")
print("[表3] S3 + 行业共振（验证冗余性）")
print(f"{'=' * 60}")

# 重新从 records 算 S3（如果有的话）
# 这里只从已有 data 看 S3 的行业共振情况

# ===== 汇总 =====
print(f"\n{'=' * 60}")
print("汇总")
print(f"{'=' * 60}")

print(f"\n  S2 总信号: {len(s2_df)}")
for p, pl in [('train', '训练'), ('test', '测试')]:
    sub = s2_df[s2_df['period'] == p]
    if len(sub) > 3:
        print(f"  {pl}: {len(sub)} 条, 共振率 {sub['has_resonance'].mean():.0%}")
        for cond, label in [(True, '有共振'), (False, '无共振')]:
            ss = sub[sub['has_resonance'] == cond]
            if len(ss) >= 3:
                print(f"    {label}: n={len(ss)}, T+20={ss['ret_20'].mean():+.2%}, 胜率={ss['ret_20'].gt(0).mean():.1%}")

elapsed = time.time() - t0
print(f"\n耗时: {elapsed:.0f}s")
conn.close()
print(f"✅ 完成")
