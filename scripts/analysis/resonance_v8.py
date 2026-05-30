#!/usr/bin/env python3
"""
V8 共振分析 v2 — 修正PB偏离问题 + 破净过滤

关键发现：PB_dev = SMA_dev 数学恒成立（BVPS为常数时），
Layer2不能是PB偏离（等价于Layer1）。
改为：PB < 1（破净）作为价值锚定。
"""
from __future__ import print_function
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, psycopg2.extras
import numpy as np
import pandas as pd
from collections import defaultdict
from datetime import datetime, date
import json, time, os

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
LOG_FILE = '/tmp/resonance_v8.log'

def log(msg='', end='\n'):
    with open(LOG_FILE, 'a') as f:
        f.write(str(msg) + end)
    print(msg, flush=True)

def banner(title):
    log()
    log('=' * 70)
    log('  ' + title)
    log('=' * 70)

def main():
    open(LOG_FILE, 'w').close()
    
    log('=' * 70)
    log(f'  V8 共振分析 v2 — 修正Layer2: PB<1破净过滤')
    log(f'  启动: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    log('=' * 70)
    
    t0 = time.time()
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # ===== 1) 加载数据 =====
    log('\n[1] 加载数据...')
    
    cur.execute("""
        SELECT DISTINCT ON (symbol) symbol, bvps
        FROM financial_data
        WHERE bvps IS NOT NULL AND bvps > 0
        ORDER BY symbol, end_date DESC
    """)
    bvps_map = {r[0]: r[1] for r in cur.fetchall()}
    log(f'  BVPS: {len(bvps_map)}只')
    
    cur.execute("SELECT stock_code, industry FROM industry_components_ths_l1")
    ind_map = {r[0]: r[1] for r in cur.fetchall()}
    log(f'  行业: {len(ind_map)}只')
    
    cur.execute("SELECT date, phase FROM pool_energy WHERE level='market' ORDER BY date")
    phase_map = {}
    for r in cur.fetchall():
        phase_map[r[0]] = r[1]
    pc = defaultdict(int)
    for p in phase_map.values():
        pc[p] += 1
    log(f'  Phase: {sum(pc.values())}天')
    for p, c in sorted(pc.items(), key=lambda x: -x[1]):
        log(f'    {p}: {c}')
    
    cur.execute("""
        SELECT substring(symbol, 3) as code, COUNT(*) as days
        FROM daily_prices
        WHERE trade_date >= '2020-01-01' AND close > 0
        GROUP BY substring(symbol, 3)
        HAVING COUNT(*) > 300
    """)
    all_codes = {r[0] for r in cur.fetchall()}
    codes = [c for c in all_codes if c in bvps_map and c in ind_map]
    log(f'  待分析: {len(codes)}只')
    
    # ===== 2) 扫描 =====
    log(f'\n[2] 扫描...')
    records = []
    processed = 0
    total_sigs = 0
    last_tick = time.time()
    
    for code in codes:
        bvps = bvps_map[code]
        industry = ind_map[code]
        
        cur.execute("""
            SELECT trade_date, close FROM daily_prices
            WHERE symbol = %s AND trade_date >= '2020-01-01' AND close > 0
            ORDER BY trade_date
        """, (f'A.{code}',))
        rows = cur.fetchall()
        if len(rows) < 200:
            continue
        
        dates, closes = zip(*rows)
        closes = np.array(closes, dtype=float)
        n = len(closes)
        
        # SMA120 & SMA偏离
        cumsum = np.cumsum(closes)
        sma120 = np.full(n, np.nan)
        sma120[119] = cumsum[119] / 120
        sma120[120:] = (cumsum[120:] - cumsum[:n-120]) / 120
        sma_dev = closes / sma120 - 1
        
        # PB 及破净判断 (不是PB偏离！)
        pb = closes / bvps
        pb_under_1 = pb < 1.0  # 破净
        
        # 未来收益
        ret5 = np.full(n, np.nan)
        ret10 = np.full(n, np.nan)
        ret20 = np.full(n, np.nan)
        ret5[:-5] = closes[5:] / closes[:-5] - 1
        ret10[:-10] = closes[10:] / closes[:-10] - 1
        ret20[:-20] = closes[20:] / closes[:-20] - 1
        
        for i in range(120, n):
            if np.isnan(ret20[i]):
                continue
            d = dates[i]
            date_d = d if isinstance(d, date) else d.date() if hasattr(d, 'date') else d
            
            sd = sma_dev[i]
            pb_u1 = bool(pb_under_1[i])
            ph = phase_map.get(date_d, 'NEUTRAL')
            
            # Layer1: SMA偏离 < -15%
            l1 = sd < -0.15
            
            # Layer2 (修正): PB < 1 (破净)
            l2 = pb_u1
            
            # Layer3: Phase门控
            l3 = ph not in ('B_ACCUM', 'B_DISTRIB')
            
            records.append({
                'code': code, 'industry': industry, 'date': date_d,
                'sma_dev': sd, 'pb': float(pb[i]), 'pb_u1': int(pb_u1),
                'phase': ph, 'l1': int(l1), 'l2': int(l2), 'l3': int(l3),
                'r5': ret5[i], 'r10': ret10[i], 'r20': ret20[i],
            })
            total_sigs += 1
        
        processed += 1
        if time.time() - last_tick > 15:
            log(f'    {processed}/{len(codes)} (sig:{total_sigs:,})')
            last_tick = time.time()
    
    log(f'  完成: {processed}只, {total_sigs:,}条')
    cur.close()
    
    # ===== 3) 分析 =====
    log(f'\n[3] 分析...')
    df = pd.DataFrame(records)
    
    def pstats2(title, cond_a, na, cond_b, nb, cond_c=None, nc=None, sub=None):
        if sub is None: sub = df
        a, b = sub[cond_a], sub[cond_b]
        c = sub[cond_c] if cond_c is not None else None
        
        banner(title)
        log(f'  {"条件":<24} {"次数":>8} {"T+5均值":>8} {"T+5胜率":>7} {"T+20均值":>8} {"T+20胜率":>7}')
        log(f'  {"-"*65}')
        
        rows_out = [(na, a), (nb, b)]
        if c is not None:
            rows_out.append((nc, c))
        
        for name, g in rows_out:
            log(f'  {name:<24} {len(g):>8,}  {g["r5"].mean():>+8.2%}  {(g["r5"]>0).mean():>7.1%}  {g["r20"].mean():>+8.2%}  {(g["r20"]>0).mean():>7.1%}')
        
        d5 = b['r5'].mean() - a['r5'].mean()
        w5 = (b['r5']>0).mean() - (a['r5']>0).mean()
        d20 = b['r20'].mean() - a['r20'].mean()
        w20 = (b['r20']>0).mean() - (a['r20']>0).mean()
        log(f'  {"提升":<24} {"":>8}  {d5:>+8.2%}  {w5:>+7.1%}  {d20:>+8.2%}  {w20:>+7.1%}')
    
    # ---- 发现1 ----
    l1 = df['l1'] == 1
    l12 = (df['l1'] == 1) & (df['l2'] == 1)
    l123 = (df['l1'] == 1) & (df['l2'] == 1) & (df['l3'] == 1)
    
    pstats2('发现1: 共振 > 单一', l1, 'Layer1 (SMA<-15%)', l12, 'L1+破净', l123, 'L1+破净+Phase')
    
    # ---- 大跌场景 ----
    banner('大跌后(5%+)场景')
    crash_l1 = df[(df['l1'] == 1) & (df['r5'] < -0.05)]
    crash_l12 = df[(df['l1'] == 1) & (df['l2'] == 1) & (df['r5'] < -0.05)]
    crash_l123 = df[(df['l1'] == 1) & (df['l2'] == 1) & (df['l3'] == 1) & (df['r5'] < -0.05)]
    log(f'  {"条件":<24} {"次数":>8} {"T+5均值":>8} {"T+5胜率":>7} {"T+20均值":>8} {"T+20胜率":>7}')
    log(f'  {"-"*65}')
    for name, g in [('Layer1(大跌)', crash_l1), ('L1+破净', crash_l12), ('L1+破净+Phase', crash_l123)]:
        log(f'  {name:<24} {len(g):>8,}  {g["r5"].mean():>+8.2%}  {(g["r5"]>0).mean():>7.1%}  {g["r20"].mean():>+8.2%}  {(g["r20"]>0).mean():>7.1%}')
    
    # ---- 发现2 ----
    banner('发现2: SMA/U型曲线 (L1+破净)')
    res = df[l12].copy()
    bins = [-np.inf, -0.30, -0.25, -0.20, -0.15]
    labels = ['<-30%', '-25~-30%', '-20~-25%', '-15~-20%']
    res['sma_bin'] = pd.cut(res['sma_dev'], bins=bins, labels=labels)
    log(f'  {"SMA偏离":<15} {"次数":>8} {"T+5均值":>8} {"T+5胜率":>7} {"T+20均值":>8} {"T+20胜率":>7}')
    log(f'  {"-"*60}')
    for lbl in labels:
        g = res[res['sma_bin'] == lbl]
        if len(g) == 0: continue
        log(f'  {lbl:<15} {len(g):>8,}  {g["r5"].mean():>+8.2%}  {(g["r5"]>0).mean():>7.1%}  {g["r20"].mean():>+8.2%}  {(g["r20"]>0).mean():>7.1%}')
    
    # ---- PB本身分桶 ----
    banner('PB值分桶 (L1+破净)')
    pb_bins = [-np.inf, 0.5, 0.7, 0.9, 1.0]
    pb_labels = ['PB<0.5', '0.5-0.7', '0.7-0.9', '0.9-1.0']
    res['pb_bin'] = pd.cut(res['pb'], bins=pb_bins, labels=pb_labels)
    log(f'  {"PB区间":<15} {"次数":>8} {"T+5均值":>8} {"T+5胜率":>7} {"T+20均值":>8} {"T+20胜率":>7}')
    log(f'  {"-"*60}')
    for lbl in pb_labels:
        g = res[res['pb_bin'] == lbl]
        if len(g) == 0: continue
        log(f'  {lbl:<15} {len(g):>8,}  {g["r5"].mean():>+8.2%}  {(g["r5"]>0).mean():>7.1%}  {g["r20"].mean():>+8.2%}  {(g["r20"]>0).mean():>7.1%}')
    
    # ---- 发现3 ----
    banner('发现3: 2026诊断')
    # Fix year extraction for mixed types
    res_26 = res[res['date'].apply(lambda d: (d if isinstance(d, date) else d).year) == 2026]
    res_25_lbl = res[res['date'].apply(lambda d: (d if isinstance(d, date) else d).year) == 2025]
    
    log(f'  2026年: {len(res_26)}条, T+5={res_26["r5"].mean():+.2%}w={(res_26["r5"]>0).mean():.1%}, T+20={res_26["r20"].mean():+.2%}w={(res_26["r20"]>0).mean():.1%}')
    log(f'  2025年: {len(res_25_lbl)}条, T+5={res_25_lbl["r5"].mean():+.2%}w={(res_25_lbl["r5"]>0).mean():.1%}, T+20={res_25_lbl["r20"].mean():+.2%}w={(res_25_lbl["r20"]>0).mean():.1%}')
    
    log(f'\n  2026按Phase:')
    log(f'  {"Phase":<15} {"次数":>8} {"占比":>6} {"T+5均值":>8} {"T+5胜率":>7} {"T+20均值":>8} {"T+20胜率":>7}')
    log(f'  {"-"*62}')
    for ph in ['B_ACCUM', 'B_DISTRIB', 'NEUTRAL', 'A_ACCUM', 'A_RECOVER']:
        g = res_26[res_26['phase'] == ph]
        if len(g) < 3: continue
        log(f'  {ph:<15} {len(g):>8,}  {len(g)/len(res_26)*100:5.1f}%  {g["r5"].mean():>+8.2%}  {(g["r5"]>0).mean():>7.1%}  {g["r20"].mean():>+8.2%}  {(g["r20"]>0).mean():>7.1%}')
    
    # ---- Phase分组 ----
    banner('Phase分组 (L1+破净)')
    log(f'  {"Phase":<15} {"次数":>8} {"占比":>6} {"T+5均值":>8} {"T+5胜率":>7} {"T+20均值":>8} {"T+20胜率":>7}')
    log(f'  {"-"*62}')
    for ph in ['NEUTRAL', 'A_ACCUM', 'A_RECOVER', 'HI_EQ', 'B_ACCUM', 'B_DISTRIB']:
        g = res[res['phase'] == ph]
        if len(g) < 5: continue
        log(f'  {ph:<15} {len(g):>8,}  {len(g)/len(res)*100:5.1f}%  {g["r5"].mean():>+8.2%}  {(g["r5"]>0).mean():>7.1%}  {g["r20"].mean():>+8.2%}  {(g["r20"]>0).mean():>7.1%}')
    
    # ---- 按年份 ----
    banner('按年份 (L1+破净)')
    log(f'  {"年份":<8} {"次数":>8} {"T+5均值":>8} {"T+5胜率":>7} {"T+20均值":>8} {"T+20胜率":>7}')
    log(f'  {"-"*50}')
    years = sorted(res['date'].apply(lambda d: (d if isinstance(d, date) else d).year).unique())
    for yr in years:
        g = res[res['date'].apply(lambda d: (d if isinstance(d, date) else d).year) == yr]
        log(f'  {yr:<8} {len(g):>8,}  {g["r5"].mean():>+8.2%}  {(g["r5"]>0).mean():>7.1%}  {g["r20"].mean():>+8.2%}  {(g["r20"]>0).mean():>7.1%}')
    
    # ---- 行业 ----
    banner('行业分层—破净过滤效果')
    l1_df = df[df['l1'] == 1]
    l12_df = res
    
    ind_rows = []
    for ind in df['industry'].dropna().unique():
        a = l1_df[l1_df['industry'] == ind]
        b = l12_df[l12_df['industry'] == ind]
        na, nb = len(a), len(b)
        if na < 50: continue
        ind_rows.append({
            'industry': ind, 'n_l1': na, 'n_l12': nb,
            'pass_rate': nb/na,
            'l1_r20': a['r20'].mean(), 'l12_r20': b['r20'].mean(),
            'delta_r20': b['r20'].mean() - a['r20'].mean(),
            'l1_w20': (a['r20']>0).mean(), 'l12_w20': (b['r20']>0).mean(),
            'delta_w20': (b['r20']>0).mean() - (a['r20']>0).mean(),
        })
    ind_df = pd.DataFrame(ind_rows).sort_values('delta_r20', ascending=False)
    
    log(f'  {"行业":<12} {"L1":>6} {"L12":>6} {"通过率":>7} {"L1收益":>7} {"L12收益":>7} {"差值":>7} {"L1胜率":>7} {"L12胜率":>7} {"胜率差":>7}')
    log(f'  {"-"*80}')
    for _, r in ind_df.iterrows():
        log(f'  {r["industry"]:<12} {r["n_l1"]:>6,} {r["n_l12"]:>6,} {r["pass_rate"]:>6.1%} {r["l1_r20"]:>+6.2%} {r["l12_r20"]:>+6.2%} {r["delta_r20"]:>+6.2%} {r["l1_w20"]:>6.1%} {r["l12_w20"]:>6.1%} {r["delta_w20"]:>+6.1%}')
    
    log(f'\n  Top5:')
    for _, r in ind_df.head(5).iterrows():
        log(f'    {r["industry"]:<12} 提升={r["delta_r20"]:+7.2%} 胜率差={r["delta_w20"]:+7.1%}')
    log(f'  Bottom5:')
    for _, r in ind_df.tail(5).iterrows():
        log(f'    {r["industry"]:<12} 提升={r["delta_r20"]:+7.2%} 胜率差={r["delta_w20"]:+7.1%}')
    
    # ---- 重资产 vs 轻资产 ----
    heavy = ['银行','钢铁','房地产','建筑装饰','建筑材料','石油石化','煤炭','有色金属','交通运输','公用事业']
    light = ['软件开发','半导体','计算机设备','传媒','通信','IT服务','电子','游戏','互联网','医疗器械']
    
    banner('重资产 vs 轻资产—破净过滤')
    for label, inds in [('重资产', heavy), ('轻资产', light)]:
        mask = df['industry'].isin(inds)
        l1g = df[mask & (df['l1'] == 1)]
        l12g = df[mask & (df['l1'] == 1) & (df['l2'] == 1)]
        log(f'  {label} L1: {len(l1g):,}次 T+5={l1g["r5"].mean():+.2%} T+20={l1g["r20"].mean():+.2%}')
        log(f'  {label} L1+破净: {len(l12g):,}次 T+5={l12g["r5"].mean():+.2%} T+20={l12g["r20"].mean():+.2%}')
        if len(l12g) > 0 and len(l1g) > 0:
            log(f'  {label} 提升: T+5={l12g["r5"].mean()-l1g["r5"].mean():+.2%} T+20={l12g["r20"].mean()-l1g["r20"].mean():+.2%}')
        log()
    
    elapsed = time.time() - t0
    banner(f'完成! 耗时 {elapsed/60:.1f}分')
    conn.close()

if __name__ == '__main__':
    main()
