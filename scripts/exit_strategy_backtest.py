#!/usr/bin/env python3
# preflight-ok: true
"""
exit_strategy_backtest.py — 离场策略回测

对比两种离场方案：
1. 压力位止盈：回到SMA-25%卖半 → SMA-15%清仓
2. 固定周期止盈：入场后T=5/10/20/40/60天后强制退出

数据源：V8信号 + daily_prices + pool_energy
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
from strategies.v8_strategy import V8Strategy
import psycopg2, numpy as np, pandas as pd
from datetime import datetime, date, timedelta
import json

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

def main():
    t0 = datetime.now()
    print(f"=== 离场策略回测 === {t0.strftime('%Y-%m-%d %H:%M')}")
    print()
    
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # 加载V8策略和regime
    strategy = V8Strategy()
    regime_map = strategy.load_regime_series()
    
    # 加载行业密度
    cur.execute("SELECT stock_code, bk_density_level FROM industry_components_ths_l1")
    density_map = {r[0]: r[1] for r in cur.fetchall()}
    
    # 加载BVPS
    cur.execute("SELECT DISTINCT ON (symbol) symbol, bvps FROM financial_data WHERE bvps IS NOT NULL AND bvps>0 ORDER BY symbol, end_date DESC")
    bvps_map = {}
    for r in cur.fetchall():
        sym = r[0]
        if sym.startswith('A.'):
            bvps_map[sym[2:]] = r[1]
    
    # 加载Phase
    cur.execute("SELECT date, phase FROM pool_energy WHERE level='market' ORDER BY date")
    phase_map = {r[0]: r[1] for r in cur.fetchall()}
    
    # 筛选有日线数据的代码
    cur.execute("SELECT DISTINCT substring(symbol,3) FROM daily_prices WHERE trade_date>='2020-01-01' AND close>0")
    active_codes = {r[0] for r in cur.fetchall()}
    codes = sorted([c for c in bvps_map if c in active_codes and c in density_map])
    print(f"候选股票: {len(codes):,}只")
    
    # === 生成入场信号（V8最终版）===
    print("生成V8入场信号...")
    entries = []  # (code, entry_date, entry_px, regime, phase, sma120_at_entry)
    
    for code in codes:
        bvps = bvps_map[code]
        density = density_map.get(code, 'NORMAL')
        
        sym = 'A.' + code
        cur.execute("SELECT trade_date, close FROM daily_prices WHERE symbol=%s AND close>0 ORDER BY trade_date", (sym,))
        rows = cur.fetchall()
        if len(rows) < 200: continue
        
        trade_dates = [r[0] for r in rows]
        closes = np.array([float(r[1]) for r in rows], dtype=float)
        n = len(closes)
        
        # SMA120
        cumsum = np.cumsum(closes)
        sma120 = np.full(n, np.nan)
        sma120[119] = cumsum[119]/120
        sma120[120:] = (cumsum[120:] - cumsum[:n-120]) / 120
        sma_dev = closes/sma120 - 1
        pb = closes/bvps
        
        for i in range(200, n):
            d = trade_dates[i]
            if d < date(2020,1,1) or d > date(2026,5,26): continue
            if np.isnan(sma120[i]) or sma120[i] <= 0: continue
            
            regime = regime_map.get(d, 'SIDEWAYS')
            phase = phase_map.get(d, 'NEUTRAL')
            matrix = strategy.get_params(regime, phase)
            if matrix is None: continue
            
            t_sma = matrix['sma']
            t_pb = matrix['pb']
            if density == 'RARE': t_sma *= 0.8
            
            if sma_dev[i] < t_sma:
                if density == 'DENSE':
                    if pb[i] < 0.5:
                        entries.append((code, d, float(closes[i]), regime, phase, float(sma120[i])))
                elif pb[i] < t_pb:
                    entries.append((code, d, float(closes[i]), regime, phase, float(sma120[i])))
    
    print(f"V8入场信号: {len(entries):,}条")
    
    # === 压力位止盈策略 ===
    print("\n--- 策略A: 压力位止盈 ---")
    results_a = backtest_exit_rule(conn, entries, 'target_price')
    
    # === 固定周期止盈策略 ===
    print("\n--- 策略B: 固定周期止盈 ---")
    for hold_days in [5, 10, 20, 30, 40, 60]:
        results_b = backtest_exit_rule(conn, entries, 'fixed_period', hold_days)
        report(results_b, f'固定周期 T+{hold_days}')
    
    # === 混合策略：压力位优先，超时强制卖 ===
    print("\n--- 策略C: 混合策略(压力位+超时强制) ---")
    for max_hold in [20, 40, 60]:
        results_c = backtest_exit_rule(conn, entries, 'hybrid', max_hold)
        report(results_c, f'压力位+最长{max_hold}天')
    
    conn.close()
    print(f"\n总耗时: {(datetime.now()-t0).total_seconds():.0f}s")

def backtest_exit_rule(conn, entries, mode, param=None):
    """
    回测退出规则
    mode: 'target_price' | 'fixed_period' | 'hybrid'
    param: 参数（holding days等）
    """
    cur = conn.cursor()
    results = []
    check_cnt = 0
    
    for code, entry_d, entry_px, regime, phase, sma120_e in entries:
        sym = 'A.' + code
        cur.execute("SELECT trade_date, close FROM daily_prices WHERE symbol=%s AND trade_date>=%s ORDER BY trade_date",
                   (sym, entry_d))
        rows = cur.fetchall()
        if len(rows) < 2: continue
        has_sma = False
        
        # 定位入场index
        entry_idx = None
        for j, r in enumerate(rows):
            if r[0] == entry_d:
                entry_idx = j
                break
        if entry_idx is None:
            continue
        
        check_cnt += 1
        if check_cnt % 10000 == 0:
            print(f"  处理: {check_cnt}/{len(entries)}")
        
        traded_dates = [r[0] for r in rows]
        closes = np.array([float(r[1]) for r in rows])
        
        # 自算SMA120
        all_closes = np.array([float(r[1]) for r in rows])
        n_all = len(all_closes)
        cur_sma120 = np.full(n_all, sma120_e)
        cum = np.cumsum(all_closes)
        for k in range(119, n_all):
            cur_sma120[k] = (cum[k] - (cum[k-120] if k>=120 else 0)) / min(120, k+1)
        
        # 截取入场后
        cur_sma120 = cur_sma120[entry_idx:]
        closes = closes[entry_idx:]
        traded_dates = traded_dates[entry_idx:]
        
        if len(closes) < 2:
            continue
        
        exit_found = False
        for j in range(1, min(len(closes), 200)):
            d2 = traded_dates[j]
            cp = closes[j]
            sma120_j = cur_sma120[j]
            
            if mode == 'target_price':
                # 回到SMA-25%卖半，SMA-15%清仓
                if cp >= sma120_j * 0.85:  # SMA-15% => 清仓
                    ret = cp/entry_px - 1
                    results.append({
                        'code': code, 'entry': entry_d, 'exit': d2,
                        'hold_days': j, 'ret': ret, 'exit_reason': 'tp_full',
                        'regime': regime, 'phase': phase,
                    })
                    exit_found = True
                    break
                elif cp >= sma120_j * 0.75:  # SMA-25% => 半仓
                    ret = cp/entry_px - 1
                    results.append({
                        'code': code, 'entry': entry_d, 'exit': d2,
                        'hold_days': j, 'ret': ret * 0.5,  # 卖半
                        'exit_reason': 'tp_half',
                        'regime': regime, 'phase': phase,
                    })
                    # 继续持有剩余半仓
                    
            elif mode == 'fixed_period':
                if j >= param:
                    ret = cp/entry_px - 1
                    results.append({
                        'code': code, 'entry': entry_d, 'exit': d2,
                        'hold_days': j, 'ret': ret,
                        'exit_reason': f'timeout_{param}d',
                        'regime': regime, 'phase': phase,
                    })
                    exit_found = True
                    break
            
            elif mode == 'hybrid':
                # 先看压力位
                if cp >= sma120_j * 0.85:
                    ret = cp/entry_px - 1
                    results.append({
                        'code': code, 'entry': entry_d, 'exit': d2,
                        'hold_days': j, 'ret': ret, 'exit_reason': 'tp_full',
                        'regime': regime, 'phase': phase,
                    })
                    exit_found = True
                    break
                # 超时强制
                elif j >= param:
                    ret = cp/entry_px - 1
                    results.append({
                        'code': code, 'entry': entry_d, 'exit': d2,
                        'hold_days': j, 'ret': ret,
                        'exit_reason': f'timeout_{param}d',
                        'regime': regime, 'phase': phase,
                    })
                    exit_found = True
                    break
        
        if not exit_found:
            # 未触发退出，按最后价格结算
            ret = closes[-1]/entry_px - 1
            results.append({
                'code': code, 'entry': entry_d, 'exit': traded_dates[-1],
                'hold_days': len(closes)-1, 'ret': ret,
                'exit_reason': 'force_close',
                'regime': regime, 'phase': phase,
            })
    
    cur.close()
    return results

def report(results, label):
    """输出回测统计"""
    if not results:
        print(f"{label:>30s}: 无信号")
        return
    
    df = pd.DataFrame(results)
    n = len(df)
    
    ret_mean = df['ret'].mean()
    win_rate = (df['ret'] > 0).mean()
    max_ret = df['ret'].max()
    min_ret = df['ret'].min()
    avg_hold = df['hold_days'].mean()
    median_hold = df['hold_days'].median()
    
    # 年化夏普
    ann_factor = np.sqrt(252.0/avg_hold) if avg_hold > 0 else 1
    sr = df['ret'].mean() / df['ret'].std() * ann_factor if df['ret'].std() > 0 else 0
    
    # 最大回撤（时序排序非常重要——按exit日期排序）
    df_sorted = df.sort_values('exit')
    eq = (1 + df_sorted['ret']).cumprod()
    peak = eq.expanding().max()
    dd = (eq/peak - 1).min() if len(eq) > 0 else 0
    
    # 退出原因分布
    reason_dist = df['exit_reason'].value_counts().to_dict()
    
    print(f"{label:>30s}: {n:>6,}次 收益={ret_mean:+.2%} 胜率={win_rate:+.1%} "
          f"最大回撤={dd:.1%} "
          f"持有={avg_hold:.0f}d/{median_hold:.0f}d SR={sr:.2f}")
    print(f"  exit reasons: {reason_dist}")

if __name__ == '__main__':
    main()
