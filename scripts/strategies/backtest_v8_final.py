#!/usr/bin/env python3
"""
V8 最终回测 — 2020-01 ~ 2026-05 全量数据

三层版本对比：
1. 裸L1：仅SMA偏离
2. L1+PB：SMA偏离+破净
3. L1+PB+密度：SMA偏离+破净+行业密度（V8最终版）

输出: /tmp/backtest_v8_final.log
"""
from __future__ import print_function
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
from strategies.v8_strategy import V8Strategy, REGIME_ALIAS
import psycopg2, numpy as np, pandas as pd
from datetime import datetime, date, timedelta
from collections import defaultdict
import time

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
LOG = '/tmp/backtest_v8_final.log'

def log(msg='', end='\n'):
    with open(LOG, 'a') as f:
        f.write(str(msg) + end)
    print(msg, flush=True)

def banner(t):
    log(); log('='*80); log('  '+t); log('='*80)

def col(s, w=12): return s.ljust(w)

def main():
    open(LOG, 'w').close()
    banner(f'V8 最终回测 2020-01 ~ 2026-05')
    log(f'  启动: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    t0 = time.time()
    
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # [1] 加载行业密度
    log('\n[1] 加载行业密度...')
    cur.execute("SELECT stock_code, bk_density_level FROM industry_components_ths_l1")
    density_map = {r[0]: r[1] for r in cur.fetchall()}
    log(f'  行业: {len(density_map):,}只')
    
    # [2] 加载BVPS
    log('\n[2] 加载BVPS...')
    cur.execute("SELECT DISTINCT ON (symbol) symbol, bvps FROM financial_data WHERE bvps IS NOT NULL AND bvps>0 ORDER BY symbol, end_date DESC")
    bvps_map = {r[0]: r[1] for r in cur.fetchall()}
    log(f'  BVPS: {len(bvps_map):,}只')
    
    # [3] 加载日线
    log('\n[3] 加载日线...')
    # BVPS表中symbol是纯6位代码（如000001），daily_prices中是A.前缀
    # 取2020年后有至少200天数据的代码
    cur.execute("SELECT substring(symbol,3) FROM daily_prices WHERE trade_date>='2020-01-01' AND close>0 GROUP BY 1 HAVING COUNT(*)>200")
    active_pool = {r[0] for r in cur.fetchall()}
    # 交集：有BVPS + 有行业密度 + 有200天以上日线
    codes = [c for c in bvps_map if c in density_map and c in active_pool]
    symbols = sorted(codes)  # 纯6位代码列表
    log(f'  待分析: {len(symbols):,}只')
    log(f'  待分析: {len(symbols):,}只')
    
    # 加载regime序列
    log('\n[4] 加载regime序列...')
    strategy = V8Strategy()
    regime_map = strategy.load_regime_series()
    log(f'  regime序列: {len(regime_map):,}天')
    cur.close(); conn.close()
    
    # [5] 逐只股票扫描
    log('\n[5] 回测扫描...')
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # 结果收集
    all_signals_3 = []  # L1+PB+密度（V8最终版）
    all_signals_2 = []  # L1+PB
    all_signals_1 = []  # 裸L1
    
    processed = 0
    last_tick = time.time()
    
    for code in symbols:
        bvps = bvps_map[code]
        density = density_map.get(code, 'NORMAL')
        
        sym = 'A.' + code
        cur.execute("SELECT trade_date, close FROM daily_prices WHERE symbol=%s AND close>0 ORDER BY trade_date", (sym,))
        rows = cur.fetchall()
        if len(rows) < 200: continue
        
        dates_r = [r[0] for r in rows]
        closes = np.array([float(r[1]) for r in rows], dtype=float)
        n = len(closes)
        
        # SMA120 + 偏离
        cumsum = np.cumsum(closes)
        sma120 = np.full(n, np.nan)
        sma120[119] = cumsum[119]/120
        sma120[120:] = (cumsum[120:] - cumsum[:n-120]) / 120
        sma_dev = closes/sma120 - 1
        pb = closes/bvps
        
        ret5 = np.full(n, np.nan); ret20 = np.full(n, np.nan)
        ret5[:-5] = closes[5:]/closes[:-5]-1
        ret20[:-20] = closes[20:]/closes[:-20]-1
        
        # Phase
        cur.execute("SELECT date, phase FROM pool_energy WHERE level='market' ORDER BY date")
        pm = {r[0]: r[1] for r in cur.fetchall()}
        
        for i in range(200, n):
            d = dates_r[i]
            if d < date(2020,1,1) or d > date(2026,5,26): continue
            if np.isnan(ret20[i]): continue
            if np.isnan(sma120[i]) or sma120[i] <= 0: continue
            
            regime = regime_map.get(d, 'SIDEWAYS')
            phase = pm.get(d, 'NEUTRAL')
            raw_sma = sma_dev[i]
            raw_pb = float(pb[i])
            
            # === Version 1: 裸L1 ===
            if raw_sma < -0.05:
                all_signals_1.append({
                    'date': d, 'code': code, 'density': density,
                    'r5': float(ret5[i]), 'r20': float(ret20[i]),
                    'sma_dev': raw_sma, 'pb': raw_pb,
                    'regime': regime, 'phase': phase, 'year': d.year,
                })
            
            # === Version 2: L1+PB ===
            if raw_sma < -0.15 and raw_pb < 1.0:
                all_signals_2.append({
                    'date': d, 'code': code, 'density': density,
                    'r5': float(ret5[i]), 'r20': float(ret20[i]),
                    'sma_dev': raw_sma, 'pb': raw_pb,
                    'regime': regime, 'phase': phase, 'year': d.year,
                })
            
            # === Version 3: V8最终版 (动态矩阵) ===
            matrix = strategy.get_params(regime, phase)
            if matrix is None: continue
            t_sma = matrix['sma']
            t_pb = matrix['pb']
            if density == 'RARE': t_sma *= 0.8
            
            if raw_sma < t_sma:
                if density == 'DENSE':
                    if raw_pb < 0.5:
                        all_signals_3.append({  # DENSE加严: PB<0.5
                            'date': d, 'code': code, 'density': density,
                            'r5': float(ret5[i]), 'r20': float(ret20[i]),
                            'sma_dev': raw_sma, 'pb': raw_pb,
                            'regime': regime, 'phase': phase, 'year': d.year,
                        })
                elif raw_pb < t_pb:
                    all_signals_3.append({
                        'date': d, 'code': code, 'density': density,
                        'r5': float(ret5[i]), 'r20': float(ret20[i]),
                        'sma_dev': raw_sma, 'pb': raw_pb,
                        'regime': regime, 'phase': phase, 'year': d.year,
                    })
        
        processed += 1
        if time.time() - last_tick > 15:
            log(f'  {processed}/{len(symbols)} — v1:{len(all_signals_1):,} v2:{len(all_signals_2):,} v3:{len(all_signals_3):,}')
            last_tick = time.time()
    
    cur.close(); conn.close()
    
    # [6] 汇总统计
    log(f'\n  完成: {processed}只')
    log(f'  v1裸L1: {len(all_signals_1):,}条')
    log(f'  v2 L1+PB: {len(all_signals_2):,}条')
    log(f'  v3 V8最终: {len(all_signals_3):,}条')
    
    def stats(df, label=''):
        if len(df) == 0: return
        r5 = df['r5'].mean(); r20 = df['r20'].mean()
        w5 = (df['r5']>0).mean(); w20 = (df['r20']>0).mean()
        max_dd = 0
        eq = (1+df['r20']).cumprod()
        peak = eq.expanding().max()
        dd = (eq/peak - 1).min()
        sharpe = r20 / df['r20'].std() * np.sqrt(52) if df['r20'].std() > 0 else 0
        log(f'  {label:<32} {len(df):>8,}条  T+5={r5:+.2%}/w={w5:.1%}  T+20={r20:+.2%}/w={w20:.1%}  DD={dd:.1%}  SR={sharpe:.2f}')
    
    banner('三层版本全量对比')
    df1 = pd.DataFrame(all_signals_1)
    df2 = pd.DataFrame(all_signals_2)
    df3 = pd.DataFrame(all_signals_3)
    
    stats(df1, 'v1裸L1')
    stats(df2, 'v2 L1+PB')
    stats(df3, 'v3 V8动态矩阵')
    
    # [7] 逐年对比
    banner('逐年对比')
    for yr in range(2020, 2027):
        log(f'\n  {yr}年:')
        g1 = df1[df1['year']==yr] if len(df1) else pd.DataFrame()
        g2 = df2[df2['year']==yr] if len(df2) else pd.DataFrame()
        g3 = df3[df3['year']==yr] if len(df3) else pd.DataFrame()
        stats(g1, '  v1裸L1')
        stats(g2, '  v2 L1+PB')
        stats(g3, '  v3 V8动态矩阵' if len(g3) else '  v3 无信号')
    
    # [8] Regime分层统计
    banner('按Regime分层')
    for r in ['BEAR', 'BOTTOM_WARNING', 'SIDEWAYS', 'BULL', 'TOP_WARNING']:
        g = df3[df3['regime']==r] if len(df3) else pd.DataFrame()
        if len(g):
            stats(g, f'  {r}')
        else:
            log(f'  {r:<16} ---')
    
    # [9] Phase分层统计
    banner('按Phase分层 (V8最终版)')
    for ph in ['A_RECOVER', 'A_ACCUM', 'NEUTRAL', 'B_ACCUM', 'B_DISTRIB']:
        g = df3[df3['phase']==ph] if len(df3) else pd.DataFrame()
        if len(g):
            stats(g, f'  {ph:<16}')
        else:
            log(f'  {ph:<16} ---')
    
    # [10] 行业密度分层
    banner('按行业密度分层 (V8最终版)')
    for d in ['RARE', 'NORMAL', 'DENSE']:
        g = df3[df3['density']==d] if len(df3) else pd.DataFrame()
        if len(g):
            stats(g, f'  {d:<16}')
        else:
            log(f'  {d:<16} ---')
    
    # [11] 2026年详细（当前环境）
    banner('2026年逐regime (V8最终版)')
    df3_26 = df3[df3['year']==2026] if len(df3) else pd.DataFrame()
    if len(df3_26):
        for r in ['BEAR', 'BOTTOM_WARNING', 'SIDEWAYS', 'BULL', 'TOP_WARNING']:
            g = df3_26[df3_26['regime']==r]
            if len(g):
                stats(g, f'  {r:<20}')
    else:
        log('  2026年无V8信号')
    
    # v3各版块
    banner('V8最终版 — 2026年密度+Phase切片')
    df3_26 = df3[df3['year']==2026] if len(df3) else pd.DataFrame()
    if len(df3_26):
        for d in ['RARE', 'NORMAL', 'DENSE']:
            g = df3_26[df3_26['density']==d]
            if len(g):
                stats(g, f'  {d:<16}')
        
        log()
        for ph in ['A_RECOVER', 'A_ACCUM', 'NEUTRAL', 'B_ACCUM', 'B_DISTRIB']:
            g = df3_26[df3_26['phase']==ph]
            if len(g):
                stats(g, f'  {ph:<16}')
    
    elapsed = time.time() - t0
    banner(f'完成! {elapsed/60:.1f}分')
    log(f'  日志: {LOG}')

if __name__ == '__main__':
    main()
