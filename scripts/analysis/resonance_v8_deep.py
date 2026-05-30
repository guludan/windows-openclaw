#!/usr/bin/env python3
"""
V8 深度诊断 — 2026年B阶段失效分析 + 破净分层优化
"""
from __future__ import print_function
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, numpy as np, pandas as pd
from collections import defaultdict
from datetime import datetime, date
import time

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
LOG_FILE = '/tmp/resonance_v8_deep.log'

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
    log(f'  V8 深度诊断')
    log(f'  启动: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    log('=' * 70)
    
    t0 = time.time()
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # ===== 数据加载 =====
    log('\n[1] 加载数据...')
    cur.execute("SELECT DISTINCT ON (symbol) symbol, bvps FROM financial_data WHERE bvps IS NOT NULL AND bvps > 0 ORDER BY symbol, end_date DESC")
    bvps_map = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute("SELECT stock_code, industry FROM industry_components_ths_l1")
    ind_map = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute("SELECT date, phase FROM pool_energy WHERE level='market' ORDER BY date")
    phase_map = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute("SELECT substring(symbol,3) as code, COUNT(*) FROM daily_prices WHERE trade_date>='2020-01-01' AND close>0 GROUP BY 1 HAVING COUNT(*)>300")
    all_codes = {r[0] for r in cur.fetchall()}
    codes = [c for c in all_codes if c in bvps_map and c in ind_map]
    log(f'  待分析: {len(codes)}只')
    
    # ===== 扫描 =====
    log('\n[2] 扫描...')
    records = []
    processed = 0
    last_tick = time.time()
    
    for code in codes:
        bvps = bvps_map[code]
        industry = ind_map[code]
        cur.execute("SELECT trade_date, close FROM daily_prices WHERE symbol=%s AND trade_date>='2020-01-01' AND close>0 ORDER BY trade_date", (f'A.{code}',))
        rows = cur.fetchall()
        if len(rows) < 200: continue
        
        dates, closes = zip(*rows)
        closes = np.array(closes, dtype=float)
        n = len(closes)
        
        cumsum = np.cumsum(closes)
        sma120 = np.full(n, np.nan)
        sma120[119] = cumsum[119] / 120
        sma120[120:] = (cumsum[120:] - cumsum[:n-120]) / 120
        sma_dev = closes / sma120 - 1
        
        pb = closes / bvps
        pb_u1 = pb < 1.0
        
        ret5 = np.full(n, np.nan); ret10 = np.full(n, np.nan); ret20 = np.full(n, np.nan)
        ret5[:-5] = closes[5:] / closes[:-5] - 1
        ret10[:-10] = closes[10:] / closes[:-10] - 1
        ret20[:-20] = closes[20:] / closes[:-20] - 1
        
        for i in range(120, n):
            if np.isnan(ret20[i]): continue
            d = dates[i]; dd = d if isinstance(d, date) else d.date() if hasattr(d,'date') else d
            records.append({
                'code': code, 'industry': industry, 'date': dd,
                'year': dd.year, 'month': dd.month,
                'sma_dev': sma_dev[i], 'pb': float(pb[i]),
                'l1': int(sma_dev[i] < -0.15),
                'l2': int(pb_u1[i]),
                'phase': phase_map.get(dd, 'NEUTRAL'),
                'r5': ret5[i], 'r10': ret10[i], 'r20': ret20[i],
            })
        
        processed += 1
        if time.time() - last_tick > 15:
            log(f'  {processed}/{len(codes)}')
            last_tick = time.time()
    
    log(f'  完成: {processed}只, {len(records):,}条')
    cur.close()
    
    df = pd.DataFrame(records)
    l1 = df[df['l1'] == 1]
    l12 = df[(df['l1'] == 1) & (df['l2'] == 1)]
    l123 = df[(df['l1'] == 1) & (df['l2'] == 1) & (df['phase'].isin(['NEUTRAL','A_ACCUM','A_RECOVER','HI_EQ']))]
    
    def s(group, name=''):
        if len(group) == 0: return f'{name} 0条'
        return f'{name}: {len(group):,}条 T+5={group["r5"].mean():+7.2%}/w={(group["r5"]>0).mean():.1%} T+20={group["r20"].mean():+7.2%}/w={(group["r20"]>0).mean():.1%}'
    
    # ===== 深度1: B阶段为什么2026年失效 =====
    banner('深度1: B阶段为何2026失效？')
    
    for ph in ['B_ACCUM', 'B_DISTRIB']:
        for yr in [2024, 2025, 2026]:
            g = l12[(l12['phase'] == ph) & (l12['year'] == yr)]
            log(f'  {ph} {yr}: {s(g)}')
        log()
    
    # 拆月份看看趋势
    log('B阶段按月趋势:')
    log(f'  {"年月":<10} {"B_ACCUM":>30} {"B_DISTRIB":>30}')
    log(f'  {"-"*70}')
    for yr in [2024, 2025, 2026]:
        for mo in range(1, 13):
            g_a = l12[(l12['year']==yr)&(l12['month']==mo)&(l12['phase']=='B_ACCUM')]
            g_d = l12[(l12['year']==yr)&(l12['month']==mo)&(l12['phase']=='B_DISTRIB')]
            if len(g_a) < 3 and len(g_d) < 3: continue
            sa = s(g_a) if len(g_a) >= 3 else '---'
            sd = s(g_d) if len(g_d) >= 3 else '---'
            log('  %s-%02d  %-40s %-40s' % (yr, mo, sa, sd))
    
    # 2026年B_DISTRIB的信号质量分析
    banner('深度2: 2026年B_DISTRIB信号质量')
    bd26 = l12[(l12['phase']=='B_DISTRIB') & (l12['year']==2026)]
    ba26 = l12[(l12['phase']=='B_ACCUM') & (l12['year']==2026)]
    
    log('2026年B_DISTRIB按SMA深度分桶:')
    bins = [-np.inf, -0.30, -0.25, -0.20, -0.15]
    labels = ['<-30%', '-25~-30%', '-20~-25%', '-15~-20%']
    bd26['sma_bin'] = pd.cut(bd26['sma_dev'], bins=bins, labels=labels)
    log(f'  {"SMA偏离":<15} {"次数":>6} {"T+5":>8} {"T+5w":>6} {"T+20":>8} {"T+20w":>6}')
    log(f'  {"-"*50}')
    for lbl in labels:
        g = bd26[bd26['sma_bin'] == lbl]
        if len(g) < 2: continue
        log(f'  {lbl:<15} {len(g):>6,}  {g["r5"].mean():>+7.2%}  {(g["r5"]>0).mean():>5.1%}  {g["r20"].mean():>+7.2%}  {(g["r20"]>0).mean():>5.1%}')
    
    log(f'\n2026年B_DISTRIB按PB分桶:')
    pb_bins = [-np.inf, 0.5, 0.7, 0.9, 1.0]
    pb_labels = ['<0.5', '0.5-0.7', '0.7-0.9', '0.9-1.0']
    bd26['pb_bin'] = pd.cut(bd26['pb'], bins=pb_bins, labels=pb_labels)
    log(f'  {"PB区间":<15} {"次数":>6} {"T+5":>8} {"T+5w":>6} {"T+20":>8} {"T+20w":>6}')
    log(f'  {"-"*50}')
    for lbl in pb_labels:
        g = bd26[bd26['pb_bin'] == lbl]
        if len(g) < 2: continue
        log(f'  {lbl:<15} {len(g):>6,}  {g["r5"].mean():>+7.2%}  {(g["r5"]>0).mean():>5.1%}  {g["r20"].mean():>+7.2%}  {(g["r20"]>0).mean():>5.1%}')
    
    # ===== 深度3: 叠加高管增减持 =====
    banner('深度3: 叠加上交所增减持')
    cur2 = conn.cursor()
    cur2.execute("""
        SELECT code, change_date, is_increase, is_decrease 
        FROM incr_decr 
        WHERE change_date >= '2020-01-01' AND change_date <= '2026-05-27'
    """)
    incr = defaultdict(list)
    decr = defaultdict(list)
    for r in cur2.fetchall():
        sym = r[0]
        d = r[1]
        if r[2]:
            incr[sym].append(d)
        if r[3]:
            decr[sym].append(d)
    
    def has_recent_action(sym, dt, action_map, days):
        if sym not in action_map: return False
        for ad in action_map[sym]:
            days_diff = (dt - ad).days
            if 0 <= days_diff <= days:
                return True
        return False
    
    # 检查破净+大跌+L1信号中，有增持的
    sig_l12 = l12.copy()
    sig_l12['has_incr_7d'] = sig_l12.apply(lambda r: has_recent_action(r['code'], r['date'], incr, 7), axis=1)
    sig_l12['has_decr_7d'] = sig_l12.apply(lambda r: has_recent_action(r['code'], r['date'], decr, 7), axis=1)
    
    log('L1+破净 + 增减持:')
    for label, cond in [('有增持7d', sig_l12['has_incr_7d']), ('无增持7d', ~sig_l12['has_incr_7d'])]:
        g = sig_l12[cond]
        log(f'  {label:<12} {s(g)}')
    log()
    for label, cond in [('有减持7d', sig_l12['has_decr_7d']), ('无减持7d', ~sig_l12['has_decr_7d'])]:
        g = sig_l12[cond]
        log(f'  {label:<12} {s(g)}')
    
    # 交叉: 增持+Phase
    log(f'\nL1+破净+增持+NON_B:')
    g = sig_l12[sig_l12['has_incr_7d'] & ~sig_l12['phase'].isin(['B_ACCUM','B_DISTRIB'])]
    log(f'  {s(g)}')
    log(f'\nL1+破净+增持+B_DISTRIB:')
    g = sig_l12[sig_l12['has_incr_7d'] & (sig_l12['phase']=='B_DISTRIB')]
    log(f'  {s(g)}')
    
    # ===== 深度4: 只看2025-2026 =====
    banner('深度4: 近期(2025-2026)逐月表现')
    recent = l12[l12['year'].isin([2025, 2026])]
    log(f'  {"年月":<10} {"T+5均值":>8} {"T+5胜率":>7} {"T+20均值":>10} {"T+20胜率":>7} {"次数":>6}')
    log(f'  {"-"*48}')
    for yr in [2025, 2026]:
        for mo in range(1, 13):
            g = recent[(recent['year']==yr)&(recent['month']==mo)]
            if len(g) < 3: continue
            log('  %s-%02d     %+8.2f   %6.1f%%    %+9.2f   %6.1f%%   %6d' % (yr, mo, g['r5'].mean()*100, (g['r5']>0).mean()*100, g['r20'].mean()*100, (g['r20']>0).mean()*100, len(g)))
    
    # ===== 深度5: PB<1的行业集中度 =====
    banner('深度5: 破净行业集中度')
    top_pb_inds = l1[l1['l2']==1]['industry'].value_counts().head(20)
    log(f'  {"行业":<15} {"破净次数":>8} {"占比":>6}')
    log(f'  {"-"*30}')
    total_pb = top_pb_inds.sum()
    for ind, cnt in top_pb_inds.items():
        log(f'  {ind:<15} {cnt:>8,}  {cnt/total_pb*100:>5.1f}%')
    
    log(f'\n  Total破净信号: {total_pb:,}')
    
    elapsed = time.time() - t0
    banner(f'完成! {elapsed/60:.1f}分')
    conn.close()

if __name__ == '__main__':
    main()
