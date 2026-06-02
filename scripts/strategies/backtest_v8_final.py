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
    bvps_map = {r[0][2:]: r[1] for r in cur.fetchall()}
    log(f'  BVPS: {len(bvps_map):,}只')
    
    # [3] 加载日线
    log('\n[3] 加载日线...')
    # BVPS表中symbol是纯6位代码（如000001），daily_prices中是A.前缀
    # 取2020年后有至少200天数据的代码
    cur.execute("SELECT substring(symbol,3) FROM daily_prices WHERE trade_date>='2020-01-01' AND close>0 GROUP BY 1 HAVING COUNT(*)>200")
    active_pool = {r[0] for r in cur.fetchall()}
    
    # 预先加载所有日线数据（按股票分组，避免逐只查询）
    log('\n[3b] 批量加载全部日线...')
    cur.execute("SELECT substring(symbol,3), trade_date, close, open, volume FROM daily_prices WHERE trade_date>='2019-06-01' AND close>0 ORDER BY symbol, trade_date")
    all_rows = cur.fetchall()
    log(f'  日线记录: {len(all_rows):,}条')
    # 按code分组
    price_data = defaultdict(list)  # code -> [(date, close, open, volume), ...]
    for r in all_rows:
        c = float(r[2]) if r[2] is not None else 0.0
        o = float(r[3]) if r[3] is not None else 0.0
        v = int(r[4]) if r[4] is not None else 0
        if c <= 0:
            continue
        price_data[r[0]].append((r[1], c, o, v))
    log(f'  有效股票: {len(price_data):,}只')
    
    # 交集：有BVPS + 有行业密度 + 有200天以上日线
    codes = [c for c in bvps_map if c in density_map and c in active_pool and c in price_data and len(price_data[c]) >= 250]
    symbols = sorted(codes)
    log(f'  待分析: {len(symbols):,}只')
    
    # 加载regime序列
    log('\n[4] 加载regime序列...')
    strategy = V8Strategy()
    regime_map = strategy.load_regime_series()
    log(f'  regime序列: {len(regime_map):,}天')
    cur.close(); conn.close()
    
    # [5] 回测扫描
    log('\n[5] 回测扫描...')
    
    # 加载Phase一次
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT date, phase FROM pool_energy WHERE level='market' ORDER BY date")
    pm = {r[0]: r[1] for r in cur.fetchall()}
    cur.close(); conn.close()
    
    # 结果收集
    all_signals_3 = []  # L1+PB+密度（V8最终版）
    all_signals_2 = []  # L1+PB
    all_signals_1 = []  # 裸L1
    all_signals_3x = []  # V8+除权过滤
    
    processed = 0
    last_tick = time.time()
    
    for code in symbols:
        bvps = bvps_map[code]
        density = density_map.get(code, 'NORMAL')
        
        rows = price_data[code]
        dates_r = [r[0] for r in rows]
        closes = np.array([r[1] for r in rows], dtype=float)
        opens = np.array([r[2] for r in rows], dtype=float)
        volumes = np.array([r[3] for r in rows], dtype=float)
        n = len(closes)
        
        # 停牌检测：连续5天volume=0标记为停牌（暂停中不产生信号）
        suspended = np.full(n, False)
        for s in range(5, n):
            if np.all(volumes[s-4:s+1] == 0):
                suspended[s] = True
        
        # 除权检测：gap = open/prev_close - 1，<-4%视为除权
        prev_closes = np.roll(closes, 1)
        prev_closes[0] = np.nan
        gaps = opens / prev_closes - 1
        
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
        
        # 遍历每个交易日作为入场点
        for i in range(200, n):
            d = dates_r[i]
            if d < date(2020,1,1) or d > date(2026,5,26): continue
            if np.isnan(ret20[i]): continue
            if np.isnan(sma120[i]) or sma120[i] <= 0: continue
            
            regime = regime_map.get(d, 'SIDEWAYS')
            phase = pm.get(d, 'NEUTRAL')
            raw_sma = sma_dev[i]
            raw_pb = float(pb[i])
            
            # ====== 除权污染检测 ======
            # 看过去20天是否有除权(gap<-4%) — 如果有则跳过本信号
            recent_xr = False
            for j in range(max(0, i-20), i):
                if not np.isnan(gaps[j]) and gaps[j] < -0.04:
                    recent_xr = True
                    break
            # 中度污染（20-60天）：记录但不做信号过滤，后续统计用
            mid_xr = False
            if not recent_xr:
                for j in range(max(0, i-60), i):
                    if not np.isnan(gaps[j]) and gaps[j] < -0.04:
                        mid_xr = True
                        break
            
            # 停牌中不产生信号
            if suspended[i]:
                continue
            
            # === Version 1: 裸L1 ===
            if raw_sma < -0.05 and not recent_xr:
                all_signals_1.append({
                    'date': d, 'code': code, 'density': density,
                    'r5': float(ret5[i]), 'r20': float(ret20[i]),
                    'sma_dev': raw_sma, 'pb': raw_pb,
                    'regime': regime, 'phase': phase, 'year': d.year,
                    'mid_xr': mid_xr, 'recent_xr': recent_xr,
                })
            
            # === Version 2: L1+PB ===
            if raw_sma < -0.15 and raw_pb < 1.0 and not recent_xr:
                all_signals_2.append({
                    'date': d, 'code': code, 'density': density,
                    'r5': float(ret5[i]), 'r20': float(ret20[i]),
                    'sma_dev': raw_sma, 'pb': raw_pb,
                    'regime': regime, 'phase': phase, 'year': d.year,
                    'mid_xr': mid_xr, 'recent_xr': recent_xr,
                })
            
            # === Version 3: V8最终版 (动态矩阵) ===
            matrix = strategy.get_params(regime, phase)
            if matrix is not None:
                t_sma = matrix['sma']
                t_pb = matrix['pb']
                if density == 'RARE': t_sma *= 0.8
                
                if raw_sma < t_sma and not recent_xr:
                    if density == 'DENSE':
                        if raw_pb < 0.5:
                            all_signals_3.append({
                                'date': d, 'code': code, 'density': density,
                                'r5': float(ret5[i]), 'r20': float(ret20[i]),
                                'sma_dev': raw_sma, 'pb': raw_pb,
                                'regime': regime, 'phase': phase, 'year': d.year,
                                'mid_xr': mid_xr, 'recent_xr': recent_xr,
                            })
                    elif raw_pb < t_pb:
                        all_signals_3.append({
                            'date': d, 'code': code, 'density': density,
                            'r5': float(ret5[i]), 'r20': float(ret20[i]),
                            'sma_dev': raw_sma, 'pb': raw_pb,
                            'regime': regime, 'phase': phase, 'year': d.year,
                            'mid_xr': mid_xr, 'recent_xr': recent_xr,
                        })
                    
                    # V8+除权过滤版：连中度污染也排除
                    if not mid_xr:
                        if density == 'DENSE':
                            if raw_pb < 0.5:
                                all_signals_3x.append({
                                    'date': d, 'code': code, 'density': density,
                                    'r5': float(ret5[i]), 'r20': float(ret20[i]),
                                    'sma_dev': raw_sma, 'pb': raw_pb,
                                    'regime': regime, 'phase': phase, 'year': d.year,
                                })
                        elif raw_pb < t_pb:
                            all_signals_3x.append({
                                'date': d, 'code': code, 'density': density,
                                'r5': float(ret5[i]), 'r20': float(ret20[i]),
                                'sma_dev': raw_sma, 'pb': raw_pb,
                                'regime': regime, 'phase': phase, 'year': d.year,
                            })
        
        processed += 1
        if time.time() - last_tick > 15:
            log(f'  {processed}/{len(symbols)} — v1:{len(all_signals_1):,} v2:{len(all_signals_2):,} v3:{len(all_signals_3):,} v3x:{len(all_signals_3x):,}')
            last_tick = time.time()
    
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
    
    banner('四层版本全量对比')
    df1 = pd.DataFrame(all_signals_1)
    df2 = pd.DataFrame(all_signals_2)
    df3 = pd.DataFrame(all_signals_3)
    df3x = pd.DataFrame(all_signals_3x)
    
    stats(df1, 'v1裸L1')
    stats(df2, 'v2 L1+PB')
    stats(df3, 'v3 V8动态矩阵')
    stats(df3x, 'v3+除权过滤 V8x')
    
    # 除权污染对v3的量化影响
    if len(df3):
        clean_count = len(df3[~df3['mid_xr']]) if 'mid_xr' in df3.columns else 0
        mid_xr_count = len(df3[df3['mid_xr']==True]) if 'mid_xr' in df3.columns else 0
        log(f'  v3中除权污染: 中度{mid_xr_count:,}条, 无污染{clean_count:,}条')
        if mid_xr_count > 0:
            g_clean = df3[~df3['mid_xr']]
            if len(g_clean):
                stats(g_clean, '  v3去污染后')
    
    # [7] 逐年对比
    banner('逐年对比（V8 vs V8+除权过滤）')
    for yr in range(2020, 2027):
        log(f'\n  {yr}年:')
        g1 = df1[df1['year']==yr] if len(df1) else pd.DataFrame()
        g2 = df2[df2['year']==yr] if len(df2) else pd.DataFrame()
        g3 = df3[df3['year']==yr] if len(df3) else pd.DataFrame()
        g3x = df3x[df3x['year']==yr] if len(df3x) else pd.DataFrame()
        stats(g1, '  v1裸L1')
        stats(g2, '  v2 L1+PB')
        stats(g3, '  v3 V8')
        stats(g3x, '  v3x除权过滤')
    
    # [8] Regime分层统计
    banner('按Regime分层 (V8x除权过滤版)')
    for r in ['BEAR', 'BOTTOM_WARNING', 'SIDEWAYS', 'BULL', 'TOP_WARNING']:
        g = df3x[df3x['regime']==r] if len(df3x) else pd.DataFrame()
        if len(g):
            stats(g, f'  {r}')
        else:
            log(f'  {r:<16} ---')
    
    # [9] Phase分层统计
    banner('按Phase分层 (V8x除权过滤版)')
    for ph in ['A_RECOVER', 'A_ACCUM', 'NEUTRAL', 'B_ACCUM', 'B_DISTRIB']:
        g = df3x[df3x['phase']==ph] if len(df3x) else pd.DataFrame()
        if len(g):
            stats(g, f'  {ph:<16}')
        else:
            log(f'  {ph:<16} ---')
    
    # [10] 行业密度分层
    banner('按行业密度分层 (V8x除权过滤版)')
    for d in ['RARE', 'NORMAL', 'DENSE']:
        g = df3x[df3x['density']==d] if len(df3x) else pd.DataFrame()
        if len(g):
            stats(g, f'  {d:<16}')
        else:
            log(f'  {d:<16} ---')
    
    # [11] 2026年详细（当前环境）
    banner('2026年逐regime (V8x除权过滤版)')
    df3x_26 = df3x[df3x['year']==2026] if len(df3x) else pd.DataFrame()
    if len(df3x_26):
        for r in ['BEAR', 'BOTTOM_WARNING', 'SIDEWAYS', 'BULL', 'TOP_WARNING']:
            g = df3x_26[df3x_26['regime']==r]
            if len(g):
                stats(g, f'  {r:<20}')
    else:
        log('  2026年无V8信号')
    
    # v3x各版块
    banner('V8x除权过滤 — 2026年密度+Phase切片')
    df3x_26 = df3x[df3x['year']==2026] if len(df3x) else pd.DataFrame()
    if len(df3x_26):
        for d in ['RARE', 'NORMAL', 'DENSE']:
            g = df3x_26[df3x_26['density']==d]
            if len(g):
                stats(g, f'  {d:<16}')
        
        log()
        for ph in ['A_RECOVER', 'A_ACCUM', 'NEUTRAL', 'B_ACCUM', 'B_DISTRIB']:
            g = df3x_26[df3x_26['phase']==ph]
            if len(g):
                stats(g, f'  {ph:<16}')
    
    elapsed = time.time() - t0
    banner(f'完成! {elapsed/60:.1f}分')
    log(f'  日志: {LOG}')

if __name__ == '__main__':
    main()
