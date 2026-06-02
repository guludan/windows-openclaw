# ====================================================================
# [ARCHIVED] 事件驱动路线 — 2026-05-25 证伪结论
# ====================================================================
# 验证结果：CCTV 文本在 S1/S2/S3 信号增强/门控中无统计显著区分度
#
# 1. 宏观政策底 → S3 门控：L0 无底日 S3 反转最强(+22%)，政策底日弱(+3.69%)
# 2. 行业共振 → S2 增强：所有回看窗口(3~14d)，有共振的 S2 均比无共振差
# 3. surplus_z ρ 规则：无单调性，极端恐慌区非最优
#
# 根因：CCTV 是滞后确认信号(产业政策已消化)，不适用于日频增强
# 用途切换：IndustryLexicon 可复用做中线行业轮动，不参与信号层
# ====================================================================

#!/usr/bin/env python3
"""
S2 + 行业政策共振回测 v2

验证核心假设：行业政策共振能提升 S2 信号的 T+20 收益和胜率

关键列名:
  industry_components_ths_l1: stock_code (纯6位), industry (同花顺行业名)
  daily_prices: symbol ('A.{stock_code}'), trade_date, close, volume, amount
"""
import psycopg2
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

PG_TO_LEXICON = {
    '光伏设备': '电力设备', '风电设备': '电力设备', '电池': '电力设备',
    '电网设备': '电力设备', '电机': '电力设备', '其他电源设备': '电力设备',
    '半导体': '电子', '消费电子': '电子', '元件': '电子',
    '光学光电子': '电子', '其他电子': '电子',
    '计算机设备': '计算机', '软件开发': '计算机', 'IT服务': '计算机',
    '汽车整车': '汽车', '汽车零部件': '汽车', '汽车服务及其他': '汽车',
    '通信设备': '通信', '通信服务': '通信',
    '中药': '医药生物', '化学制药': '医药生物', '生物制品': '医药生物',
    '医疗器械': '医药生物', '医疗服务': '医药生物', '医药商业': '医药生物',
    '白酒': '食品饮料', '饮料制造': '食品饮料', '食品加工制造': '食品饮料',
    '房地产': '房地产', '建筑装饰': '房地产', '建筑材料': '房地产', '家居用品': '房地产',
    '军工装备': '国防军工', '军工电子': '国防军工',
    '银行': '银行', '证券': '银行', '保险': '银行', '多元金融': '银行',
    '工业金属': '有色金属', '小金属': '有色金属', '贵金属': '有色金属', '金属新材料': '有色金属',
    '化学原料': '基础化工', '化学制品': '基础化工', '化学纤维': '基础化工',
    '农化制品': '基础化工', '橡胶制品': '基础化工', '塑料制品': '基础化工',
    '石油加工贸易': '基础化工',
}

print("=" * 60)
print("[S2 + 行业共振回测 v2]", datetime.now().strftime("%H:%M"))
print("=" * 60)
t0 = time.time()

conn = psycopg2.connect(DB)

# ===== 1) 行业映射 =====
print("\n[1] 加载行业成分...")
cur = conn.cursor()
cur.execute("SELECT stock_code, industry FROM industry_components_ths_l1")
stock_ind_map = {}  # {code: [lexicon_ind, pg_ind]}
pg_ind_set = set()
for r in cur.fetchall():
    code = r[0]
    pg_ind = r[1]
    pg_ind_set.add(pg_ind)
    lex_ind = PG_TO_LEXICON.get(pg_ind)
    if lex_ind:
        stock_ind_map[code] = lex_ind
cur.close()

print(f"  有行业映射的股票: {len(stock_ind_map)}")
lexicon_inds = set(PG_TO_LEXICON.values())
print(f"  PG行业: {len(pg_ind_set)}, 词库行业: {len(lexicon_inds)}: {sorted(lexicon_inds)}")
# 检查哪些 PG 行业未被映射
unmapped = sorted(pg_ind_set - set(PG_TO_LEXICON.keys()))
print(f"  未映射行业 (不参与共振): {unmapped[:10]}{'...' if len(unmapped) > 10 else ''}")

# ===== 2) 日线 + S2 信号 =====
print("\n[2] 加载日线...")
cur = conn.cursor('stream_s2')
# 只加载有行业映射的股票，格式 'A.{code}'
valid_symbols = [f'A.{c}' for c in stock_ind_map.keys()]
# 用批次查询避免超大游标
chunk_size = 1000
df_parts = []
for i in range(0, len(valid_symbols), chunk_size):
    batch = valid_symbols[i:i+chunk_size]
    placeholders = ','.join(['%s'] * len(batch))
    with conn.cursor() as cc:
        cc.execute(f"""
            SELECT symbol, trade_date, close, volume, amount
            FROM daily_prices 
            WHERE symbol IN ({placeholders}) AND trade_date >= '2022-01-01' AND close > 0 AND volume > 0
            ORDER BY symbol, trade_date
        """, batch)
        chunk = cc.fetchall()
    if chunk:
        df_parts.extend(chunk)
    if (i // chunk_size + 1) % 10 == 0:
        print(f"    {i+len(batch)}/{len(valid_symbols)} 股, {len(df_parts):,} 行")
# 不需要 explicit close 因为用了 with

df_p = pd.DataFrame(df_parts, columns=['symbol', 'trade_date', 'close', 'volume', 'amount'])
df_p['trade_date'] = pd.to_datetime(df_p['trade_date'])
print(f"  日线: {len(df_p):,} 行, {df_p['symbol'].nunique()} 只")

# ===== 3) S2 信号计算（按股票分组） =====
print("\n[3] 计算 S2 信号...")
records = []

for idx, (sym, grp) in enumerate(df_p.groupby('symbol', sort=False)):
    if len(grp) < LOOKBACK + 21:
        continue
    sd = grp.sort_values('trade_date').copy()
    
    # 从 symbol 提取股票代码
    stock_code = sym.replace('A.', '')
    lex_ind = stock_ind_map.get(stock_code, '')
    if not lex_ind:
        continue  # 无行业映射
    
    n = len(sd)
    sd['log_ret'] = np.log(sd['close'] / sd['close'].shift(1)).clip(-0.11, 0.11)
    sd['vol_20d'] = sd['log_ret'].rolling(20).std() * np.sqrt(252)
    sd['vol_z'] = (sd['vol_20d'] - sd['vol_20d'].rolling(120).mean()) / sd['vol_20d'].rolling(120).std(ddof=1).clip(lower=1e-6)
    sd['v_z'] = (sd['volume'] - sd['volume'].rolling(20).mean()) / sd['volume'].rolling(20).std().clip(lower=1)
    sd['ret'] = sd['close'].pct_change()
    
    # S2: vol_z > 2.5, v_z > 2.0, ret > 5%
    sd['s2'] = (sd['vol_z'] > 2.5) & (sd['v_z'] > 2.0) & (sd['ret'] > 0.05)
    
    last_idx = -999
    for i in range(LOOKBACK, n - 20):
        if not sd.iloc[i]['s2']:
            continue
        if last_idx >= 0 and i - last_idx < 20:
            continue
        last_idx = i
        
        d = sd.iloc[i]['trade_date'].date() if hasattr(sd.iloc[i]['trade_date'], 'date') else sd.iloc[i]['trade_date']
        c0 = float(sd.iloc[i]['close'])
        c20 = float(sd.iloc[i + 20]['close'])
        records.append({
            'date': d,
            'symbol': sym,
            'stock_code': stock_code,
            'lex_ind': lex_ind,
            'pg_ind_name': '',  # 会后面补
            'ret_20': c20 / c0 - 1,
            'period': 'train' if d <= date(2023, 12, 31) else 'test',
        })
    
    if (idx + 1) % 500 == 0:
        print(f"    {idx+1}/{df_p['symbol'].nunique()} 股, {len(records)} S2", flush=True)

print(f"  总 S2 信号: {len(records)}")
df_s2 = pd.DataFrame(records)
if len(df_s2) == 0:
    print("❌ 无信号，退出")
    sys.exit(1)

# ===== 4) CCTV 行业匹配 =====
print("\n[4] CCTV 行业政策匹配...")
matcher = IndustryMatcher()
cur = conn.cursor()
cur.execute("""
    SELECT event_id, event_date, title, full_text
    FROM event_list
    WHERE source = 'news_cctv' AND event_date >= '2022-01-01'
      AND full_text IS NOT NULL AND LENGTH(full_text) > 20
    ORDER BY event_date
""")
cctv = cur.fetchall()
cur.close()
print(f"  CCTV 文本: {len(cctv)} 条")

daily_ind_hits = defaultdict(lambda: defaultdict(int))  # {date: {lex_ind: cnt}}
for row in cctv:
    d = row[1]
    if hasattr(d, 'date'):
        d = d.date()
    text = ((row[2] or '') + ' ' + (row[3] or ''))
    matches = matcher.match(text)
    for lex_ind, total, conf in matches:
        daily_ind_hits[d][lex_ind] += total

print(f"  有行业匹配的天数: {len(daily_ind_hits)}")

# ===== 5) 关联 S2 × 共振 =====
print("\n[5] S2 × 行业共振关联...")
LOOKBACK_DAYS = 10  # 回看 10 个日历日

def get_ind_hits(signal_date, lookback=LOOKBACK_DAYS):
    start = signal_date - timedelta(days=lookback)
    hits = defaultdict(int)
    for delta in range(1, lookback + 1):
        dd = signal_date - timedelta(days=delta)
        if dd in daily_ind_hits:
            for ind, cnt in daily_ind_hits[dd].items():
                hits[ind] += cnt
    return hits

has_res = []
ind_hit_counts = []
for _, r in df_s2.iterrows():
    ind_hits = get_ind_hits(r['date'])
    n_hits = ind_hits.get(r['lex_ind'], 0)
    ind_hit_counts.append(n_hits)
    has_res.append(n_hits >= 1)

df_s2['has_resonance'] = has_res
df_s2['ind_hit_count'] = ind_hit_counts

# ===== 表1: 整体对比 =====
print(f"\n{'=' * 60}")
print("[表1] S2 整体 + 行业共振")
print(f"{'=' * 60}")

for period, lbl in [('train', '2022~2023'), ('test', '2024~2026')]:
    sub = df_s2[df_s2['period'] == period]
    if len(sub) < 3:
        print(f"\n  [{lbl}] 信号不足 (n={len(sub)})")
        continue
    
    print(f"\n  [{lbl}] {len(sub)} S2")
    print(f"  {'条件':<24} {'n':<10} {'T+20均值':<14} {'胜率':<10}")
    print(f"  {'-'*58}")
    
    for cond, fil, label in [
        ('有行业共振', sub[sub['has_resonance']], '有共振'),
        ('无行业共振', sub[~sub['has_resonance']], '无共振'),
        ('全部', sub, '全部'),
    ]:
        if len(fil) < 3:
            continue
        rets = fil['ret_20'].values
        print(f"  {label:<24} {len(fil):<10} {np.mean(rets):+10.2%}   {np.mean(rets > 0):.1%}")

# ===== 表2: 行业细分（只看测试集 S2 + 有共振） =====
print(f"\n{'=' * 60}")
print("[表2] 各行业共振效果（测试集）")
print(f"{'=' * 60}")

test = df_s2[df_s2['period'] == 'test']
if len(test) > 3:
    print(f"\n  {'行业':<12} {'总信号':<10} {'共振率':<10} {'有共振均值':<14} {'无共振均值':<14} {'差':>10}")
    print(f"  {'-'*70}")
    for lex_ind in sorted(test['lex_ind'].unique()):
        grp = test[test['lex_ind'] == lex_ind]
        if len(grp) < 5:
            continue
        n_res = grp['has_resonance'].sum()
        n_total = len(grp)
        res_mean = grp[grp['has_resonance']]['ret_20'].mean() if n_res >= 3 else np.nan
        nores_mean = grp[~grp['has_resonance']]['ret_20'].mean() if (n_total - n_res) >= 3 else np.nan
        diff = (res_mean - nores_mean) if (not np.isnan(res_mean) and not np.isnan(nores_mean)) else np.nan
        res_str = f'{res_mean:+9.2%}' if not np.isnan(res_mean) else '     N/A'
        nores_str = f'{nores_mean:+9.2%}' if not np.isnan(nores_mean) else '     N/A'
        diff_str = f'{diff:+8.2%}' if not np.isnan(diff) else '    N/A'
        print(f"  {lex_ind:<12} {n_total:<10} {n_res/n_total:>7.0%}    {res_str:<14} {nores_str:<14} {diff_str:>10}")

# ===== 表3: 不同回看窗口的共振效果 =====
print(f"\n{'=' * 60}")
print("[表3] 回看窗口敏感性（测试集）")
print(f"{'=' * 60}")

test = df_s2[df_s2['period'] == 'test']
for lookback_days in [3, 5, 7, 10, 14]:
    # 构建布尔Series，索引对齐
    res_mask = pd.Series(index=test.index, dtype=bool)
    for idx, r in test.iterrows():
        h = get_ind_hits(r['date'], lookback=lookback_days)
        res_mask[idx] = h.get(r['lex_ind'], 0) >= 1
    n_res = res_mask.sum()
    n_nores = len(test) - n_res
    res_mean = test.loc[res_mask, 'ret_20'].mean() if n_res >= 3 else np.nan
    nores_mean = test.loc[~res_mask, 'ret_20'].mean() if n_nores >= 3 else np.nan
    res_s = f'{res_mean:+7.2%}' if not np.isnan(res_mean) else '   N/A'
    nores_s = f'{nores_mean:+7.2%}' if not np.isnan(nores_mean) else '   N/A'
    print(f"  lookback={lookback_days:>2}d: 共振率={n_res/len(test):.0%} ({n_res}/{len(test)}), "
          f"有={res_s}, 无={nores_s}")

# ===== 汇总 =====
print(f"\n{'=' * 60}")
print("汇总")
print(f"{'=' * 60}")

for p, pl in [('train', '训练集'), ('test', '测试集')]:
    sub = df_s2[df_s2['period'] == p]
    if len(sub) < 3:
        continue
    print(f"\n  {pl}: {len(sub)} S2")
    res = sub[sub['has_resonance']]
    nores = sub[~sub['has_resonance']]
    print(f"    共振率: {len(res)/len(sub):.0%}")
    if len(res) >= 3:
        print(f"    有共振: n={len(res)}, T+20={res['ret_20'].mean():+.2%}, 胜率={res['ret_20'].gt(0).mean():.1%}")
    if len(nores) >= 3:
        print(f"    无共振: n={len(nores)}, T+20={nores['ret_20'].mean():+.2%}, 胜率={nores['ret_20'].gt(0).mean():.1%}")
    # 增强效果
    if len(res) >= 3 and len(nores) >= 3:
        print(f"    ➡ 共振增益: {res['ret_20'].mean() - nores['ret_20'].mean():+.2%}")

elapsed = time.time() - t0
print(f"\n耗时: {elapsed:.0f}s")
conn.close()
print("✅ 完成")
