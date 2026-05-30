#!/usr/bin/env python3
"""
V8 共振细化分析 — SMA浅偏离分桶 + B阶段年份交叉 + 行业破净密度
"""
from __future__ import print_function
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, numpy as np, pandas as pd
from collections import defaultdict
from datetime import datetime, date
import time

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
LOG = '/tmp/resonance_v8_tune.log'

def log(msg='', end='\n'):
    with open(LOG, 'a') as f:
        f.write(str(msg) + end)
    print(msg, flush=True)

def banner(t):
    log(); log('='*70); log('  ' + t); log('='*70)

def stat(g, name=''):
    if len(g)==0: return f'{name}: 0条'
    return f'{name}: {len(g):>7,}条  T+5={g["r5"].mean():+7.2%}/w={(g["r5"]>0).mean():.1%}  T+20={g["r20"].mean():+7.2%}/w={(g["r20"]>0).mean():.1%}'

def main():
    open(LOG, 'w').close()
    log('='*70)
    log(f'  V8 共振细化 — 2026-05-28')
    log(f'  启动: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    log('='*70)
    
    t0 = time.time()
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # 加载数据
    log('\n[1] Load...')
    cur.execute("SELECT DISTINCT ON (symbol) symbol, bvps FROM financial_data WHERE bvps IS NOT NULL AND bvps>0 ORDER BY symbol, end_date DESC")
    bvps_map = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute("SELECT stock_code, industry FROM industry_components_ths_l1")
    ind_map = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute("SELECT date, phase FROM pool_energy WHERE level='market' ORDER BY date")
    phase_map = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute("SELECT substring(symbol,3) as code, COUNT(*) FROM daily_prices WHERE trade_date>='2020-01-01' AND close>0 GROUP BY 1 HAVING COUNT(*)>300")
    all_codes = {r[0] for r in cur.fetchall()}
    codes = [c for c in all_codes if c in bvps_map and c in ind_map]
    log(f'  候选:{len(all_codes)}, BVPS:{len(bvps_map)}, 行业:{len(ind_map)}, 交集:{len(codes)}')
    
    # 扫描
    log('\n[2] Scan...')
    records = []
    processed = 0; last_tick = time.time()
    
    for code in codes:
        bvps = bvps_map[code]; ind = ind_map[code]
        cur.execute("SELECT trade_date, close FROM daily_prices WHERE symbol=%s AND trade_date>='2020-01-01' AND close>0 ORDER BY trade_date", (f'A.{code}',))
        rows = cur.fetchall()
        if len(rows) < 200: continue
        dates, closes = zip(*rows)
        closes = np.array(closes, dtype=float); n = len(closes)
        
        cumsum = np.cumsum(closes)
        sma120 = np.full(n, np.nan)
        sma120[119] = cumsum[119]/120
        sma120[120:] = (cumsum[120:]-cumsum[:n-120])/120
        sma_dev = closes/sma120 - 1
        pb = closes/bvps
        pb_u1 = pb < 1.0
        
        ret5 = np.full(n, np.nan); ret10 = np.full(n, np.nan); ret20 = np.full(n, np.nan)
        ret5[:-5] = closes[5:]/closes[:-5]-1; ret10[:-10]=closes[10:]/closes[:-10]-1; ret20[:-20]=closes[20:]/closes[:-20]-1
        
        for i in range(120, n):
            if np.isnan(ret20[i]): continue
            d=dates[i]; dd=d if isinstance(d,date) else d.date() if hasattr(d,'date') else d
            records.append({
                'code':code,'industry':ind,'date':dd,'year':dd.year,'month':dd.month,
                'sma_dev':sma_dev[i],'pb':float(pb[i]),'l1':int(sma_dev[i]<-0.05),  # 放宽到-5%
                'l2':int(pb_u1[i]),'phase':phase_map.get(dd,'NEUTRAL'),
                'r5':ret5[i],'r10':ret10[i],'r20':ret20[i],
            })
        
        processed += 1
        if time.time()-last_tick>15:
            log(f'  {processed}/{len(codes)} ({len(records):,}条)')
            last_tick = time.time()
    
    cur.close(); conn.close()
    log(f'  完成: {processed}只, {len(records):,}条')
    
    df = pd.DataFrame(records)
    # SMA偏离分桶
    for name, lo, hi in [('<-30%', -np.inf, -0.30), ('-25~-30%', -0.30, -0.25), ('-20~-25%', -0.25, -0.20),
                         ('-15~-20%', -0.20, -0.15), ('-10~-15%', -0.15, -0.10), ('-5~-10%', -0.10, -0.05),
                         ('0~-5%', -0.05, 0), ('>0%', 0, np.inf)]:
        mask = (df['sma_dev'] >= lo) if lo > -np.inf else (df['sma_dev'] <= hi)
        if lo > -np.inf:
            mask = (df['sma_dev'] >= lo) & (df['sma_dev'] < hi) if hi < np.inf else (df['sma_dev'] >= lo)
        else:
            mask = df['sma_dev'] <= hi
        df.loc[mask, 'sma_bin'] = name
    
    # ===== TASK 1: SMA浅偏离分桶共振 =====
    banner('TASK1: SMA偏离分桶 × 共振条件')
    log(f'  {"SMA区间":<12} {"裸L1":>28} {"+破净":>32} {"+破净+非B":>34}')
    log(f'  {"-"*110}')
    
    for lo, hi, lbl in [(-np.inf, -0.30, '<-30%'), (-0.30, -0.25, '-25~-30%'), (-0.25, -0.20, '-20~-25%'),
                        (-0.20, -0.15, '-15~-20%'), (-0.15, -0.10, '-10~-15%'), (-0.10, -0.05, '-5~-10%'),
                        (-0.05, 0, '0~-5%'), (0, np.inf, '>0%')]:
        if lo == -np.inf:
            m = df['sma_dev'] <= hi
        elif hi == np.inf:
            m = df['sma_dev'] >= lo
        else:
            m = (df['sma_dev'] >= lo) & (df['sma_dev'] < hi)
        
        g1 = df[m]
        g2 = df[m & (df['l2'] == 1)]
        g3 = df[m & (df['l2'] == 1) & (~df['phase'].isin(['B_ACCUM','B_DISTRIB']))]
        
        s1 = f'{len(g1):,}条 T+5={g1["r5"].mean():+.2%}/w={(g1["r5"]>0).mean():.0%} T+20={g1["r20"].mean():+.2%}/w={(g1["r20"]>0).mean():.0%}' if len(g1) else '---'
        s2 = f'{len(g2):,}条 T+5={g2["r5"].mean():+.2%}/w={(g2["r5"]>0).mean():.0%} T+20={g2["r20"].mean():+.2%}/w={(g2["r20"]>0).mean():.0%}' if len(g2) else '---'
        s3 = f'{len(g3):,}条 T+5={g3["r5"].mean():+.2%}/w={(g3["r5"]>0).mean():.0%} T+20={g3["r20"].mean():+.2%}/w={(g3["r20"]>0).mean():.0%}' if len(g3) else '---'
        
        log(f'  {lbl:<12} {s1}')
        log(f'  {"":>12} {s2}')
        log(f'  {"":>12} {s3}')
        log()
    
    # ===== TASK 2: B阶段 × 年份交叉 =====
    banner('TASK2: B阶段年份交叉 (Layer1裸)')
    log(f'  {"年份":<8} {"B_ACCUM":>40} {"B_DISTRIB":>40}')
    log(f'  {"-"*90}')
    
    for yr in sorted(df['year'].unique()):
        for ph, label in [('B_ACCUM', 'B_ACCUM'), ('B_DISTRIB', 'B_DISTRIB')]:
            g = df[(df['year']==yr) & (df['phase']==ph) & (df['l1']==1)]
            s = stat(g, label) if len(g)>=3 else f'{label}: ---'
            log(f'  {yr:<8} {s}' if ph=='B_ACCUM' else f'  {"":<8} {s}')
    
    # B阶段 × 年份 × 是否破净
    banner('TASK2b: B阶段年份交叉 (L1+破净)')
    log(f'  {"年份":<8} {"B_ACCUM+破净":>45} {"B_DISTRIB+破净":>45}')
    log(f'  {"-"*100}')
    for yr in sorted(df['year'].unique()):
        for ph, label in [('B_ACCUM', 'B_ACCUM'), ('B_DISTRIB', 'B_DISTRIB')]:
            g = df[(df['year']==yr) & (df['phase']==ph) & (df['l1']==1) & (df['l2']==1)]
            s = stat(g, label) if len(g)>=3 else f'{label}: ---'
            log(f'  {yr:<8} {s}' if ph=='B_ACCUM' else f'  {"":<8} {s}')
    
    # ===== TASK 3: 行业破净密度 =====
    banner('TASK3: 行业破净密度 × 信号质量')
    ind_stats = []
    for ind in df['industry'].dropna().unique():
        g = df[df['industry']==ind]
        g_pb = g[g['l2']==1]
        g_l1_pb = g[(g['l1']==1) & (g['l2']==1)]
        g_l1_pb_nb = g[(g['l1']==1) & (g['l2']==1) & (~g['phase'].isin(['B_ACCUM','B_DISTRIB']))]
        
        density = len(g_pb)/len(g) if len(g) else 0
        ind_stats.append({
            'industry': ind, 'n_total': len(g), 'n_pb': len(g_pb), 
            'density_pct': density*100,
            'n_l1pb': len(g_l1_pb), 'l1pb_r20': g_l1_pb['r20'].mean() if len(g_l1_pb) else 0,
            'l1pb_w20': (g_l1_pb['r20']>0).mean() if len(g_l1_pb) else 0,
            'n_l1pb_nb': len(g_l1_pb_nb), 'l1pb_nb_r20': g_l1_pb_nb['r20'].mean() if len(g_l1_pb_nb) else 0,
            'l1pb_nb_w20': (g_l1_pb_nb['r20']>0).mean() if len(g_l1_pb_nb) else 0,
        })
    
    ind_df = pd.DataFrame(ind_stats).sort_values('density_pct', ascending=False)
    
    log(f'\n 行业破净密度降序:')
    log(f'  {"行业":<12} {"总量":>7} {"破净":>6} {"密度":>6} {"L1PB":>6} {"L1PB_r20":>10} {"L1PB_w20":>8} {"+Phase_r20":>10} {"+Phase_w20":>8}')
    log(f'  {"-"*75}')
    for _, r in ind_df.iterrows():
        if r['n_pb'] < 10: continue
        log(f"  {r['industry']:<12} {r['n_total']:>7,} {r['n_pb']:>6,} {r['density_pct']:>5.1f}% {r['n_l1pb']:>6,} {r['l1pb_r20']:>+9.2%} {r['l1pb_w20']:>7.1%} {r['l1pb_nb_r20']:>+9.2%} {r['l1pb_nb_w20']:>7.1%}")
    
    # 密度 vs 信号质量 关系
    log(f'\n 按密度分组:')
    density_bins = [(0, 0.1, '<1%稀'), (0.1, 1, '1-10%'), (1, 10, '10-50%'), (10, 100, '>50%密集')]
    for lo, hi, lbl in density_bins:
        m = (ind_df['density_pct'] >= lo) & (ind_df['density_pct'] < hi)
        g = ind_df[m]
        log(f'  {lbl:<12} {len(g):>3}个行业  L1PB avg_r20={g["l1pb_r20"].mean():+.2%} avg_w20={g["l1pb_w20"].mean():.1%}')
    
    # 重/轻资产分层
    banner('TASK3b: 重资产 vs 轻资产 — 破净分层')
    heavy = ['银行','钢铁','房地产','建筑装饰','建筑材料','石油石化','煤炭','有色金属','交通运输','公用事业']
    light = ['软件开发','半导体','计算机设备','传媒','通信','IT服务','电子','游戏','互联网','医疗器械']
    
    for label, inds in [('重资产', heavy), ('轻资产', light)]:
        mask = df['industry'].isin(inds)
        g = df[mask & (df['l1']==1)]
        # 分PB区间
        pb_bins = [(-np.inf, 0.5, '<0.5'), (0.5, 0.7, '0.5-0.7'), (0.7, 0.9, '0.7-0.9'), (0.9, 1.0, '0.9-1.0')]
        log(f'\n  {label} L1信号按PB分层:')
        log(f'  {"PB":<10} {"次数":>6} {"T+5":>8} {"T+5w":>6} {"T+20":>8} {"T+20w":>6}')
        for lo, hi, lbl in pb_bins:
            gm = g[(g['pb']>=lo) & (g['pb']<hi)]
            if len(gm) < 5: continue
            log(f'  {lbl:<10} {len(gm):>6,} {gm["r5"].mean():>+7.2%} {(gm["r5"]>0).mean():>5.1%} {gm["r20"].mean():>+7.2%} {(gm["r20"]>0).mean():>5.1%}')
    
    # ===== TASK 4: 动态阈值初探 =====
    banner('TASK4: Phase×SMA阈值优化')
    log(f'  {"Phase":<12} {"SMA<-5%":>32} {"SMA<-10%":>32} {"SMA<-15%":>32} {"SMA<-20%":>32}')
    log(f'  {"-"*130}')
    sma_cuts = [(-0.05, 'SMA<-5%'), (-0.10, 'SMA<-10%'), (-0.15, 'SMA<-15%'), (-0.20, 'SMA<-20%')]
    for ph in ['A_ACCUM', 'A_RECOVER', 'NEUTRAL', 'B_ACCUM', 'B_DISTRIB', 'HI_EQ']:
        parts = []
        for thresh, lbl in sma_cuts:
            g = df[(df['phase']==ph) & (df['sma_dev'] < thresh) & (df['l2']==1)]
            if len(g) >= 3:
                parts.append(f'{len(g):,}条/T+5={g["r5"].mean():+.2%}/w={(g["r5"]>0).mean():.0%}/T+20={g["r20"].mean():+.2%}/w={(g["r20"]>0).mean():.0%}')
            else:
                parts.append('---')
        log(f'  {ph:<12} {parts[0]}')
        log(f'  {"":<12} {parts[1]}')
        log(f'  {"":<12} {parts[2]}')
        log(f'  {"":<12} {parts[3]}')
        log()
    
    elapsed = time.time()-t0
    banner(f'完成! {elapsed/60:.1f}分')
    
if __name__ == '__main__':
    main()
