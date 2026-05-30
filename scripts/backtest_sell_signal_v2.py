"""
大盘级卖出信号回测 v2 — 状态转换路径检测

核心创新：信号不是「R指数绝对值」，而是「R指数状态转换」
  - 原有r_warning信号 vs 新定义的R指数转换信号

状态转换信号定义：
  - 卖出信号：R指数从 > 75分位 跌到 < 30分位，历时 ≤ 5天
  - 买入信号：[B] 从 < 0.4 持续增长到 > 0.7，历时 ≥ 5天
    
评估逻辑：
  - 信号后20日内的价格/状态路径是否符合预期
  - 预期跌幅、最大回撤、假阳性率
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import numpy as np
import pandas as pd
import json, os
from datetime import datetime, timedelta

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT = '/home/tearsyu/.openclaw/workspace/memory/reports/'
MIN_WINDOW = 5

def get_conn():
    return psycopg2.connect(DB)

def load_market_signal(conn, signal_type, start='2019-01-01'):
    """加载一个市场信号"""
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, value, extra::text
        FROM market_signals
        WHERE signal_type = %s AND trade_date >= %s
        ORDER BY trade_date
    """, (signal_type, start))
    rows = []
    for r in cur.fetchall():
        extra = json.loads(r[2]) if r[2] else {}
        rows.append({'trade_date': r[0], 'value': r[1], 'extra': extra})
    return pd.DataFrame(rows)

def load_all_state(conn, start='2019-01-01'):
    """加载大盘完整状态日序列"""
    cur = conn.cursor()
    
    # 所有signal_types
    cur.execute("""
        SELECT trade_date, signal_type, value, extra::text
        FROM market_signals
        WHERE trade_date >= %s
          AND signal_type IN ('r_index', 'r_warning', 'ba_surplus')
        ORDER BY trade_date, signal_type
    """, (start,))
    
    state = {}
    for r in cur.fetchall():
        d, stype, val, extra = r
        if d not in state:
            state[d] = {'trade_date': d}
        state[d][f'{stype}_val'] = val
        extra_d = json.loads(extra) if extra else {}
        for k in ['r_pct', 'b_conc', 'a_conc', 'advance_ratio', 'vol_z', 'warning']:
            if k in extra_d:
                state[d][k] = extra_d[k]
    
    df = pd.DataFrame(list(state.values()))
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df.sort_values('trade_date').reset_index(drop=True)

def detect_signals(state_df):
    """
    从状态序列中检测信号
    
    1) r_warning_signal: 已有r_warning
    2) r_collapse_signal: R指数从高分位急跌到低分位
    3) b_accumulate_signal: [B]持续积累
    """
    df = state_df.copy()
    
    signals = []
    
    # 滚动窗口扫描
    for i in range(MIN_WINDOW, len(df)):
        # --- r_collapse_signal ---
        # R指数从>75分位5天内跌到<30分位
        window = df.iloc[max(0,i-5):i+1]
        if len(window) >= 3 and 'r_pct' in window.columns:
            r_pcts = window['r_pct'].dropna()
            if len(r_pcts) >= 3:
                first_half = r_pcts.iloc[:len(r_pcts)//2]
                second_half = r_pcts.iloc[len(r_pcts)//2:]
                if first_half.max() >= 0.75 and second_half.min() <= 0.30:
                    rt_drop = first_half.max() - second_half.min()
                    signals.append({
                        'signal_date': df.iloc[i]['trade_date'],
                        'signal_type': 'r_collapse_signal',
                        'signal_strength': round(rt_drop, 2),
                        'r_before': round(first_half.max(), 3),
                        'r_after': round(second_half.min(), 3),
                    })
        
        # --- b_accumulate_signal ---
        # [B]从<0.4持续增长到>0.7
        if 'b_conc' in window.columns:
            b_vals = window['b_conc'].dropna()
            if len(b_vals) >= 5:
                first3 = b_vals.iloc[:3].mean()
                last3 = b_vals.iloc[-3:].mean()
                if first3 < 0.4 and last3 > 0.65:
                    signals.append({
                        'signal_date': df.iloc[i]['trade_date'],
                        'signal_type': 'b_accumulate_signal',
                        'signal_strength': round(last3 - first3, 2),
                        'b_start': round(first3, 3),
                        'b_end': round(last3, 3),
                    })
    
    sig_df = pd.DataFrame(signals)
    
    # 去重：同类型信号离太近的只保留第一个
    if len(sig_df) > 0:
        # 每类信号至少间隔3天
        keep = []
        for stype, grp in sig_df.groupby('signal_type'):
            grp = grp.sort_values('signal_date')
            last_kept = None
            for _, row in grp.iterrows():
                if last_kept is None or (row['signal_date'] - last_kept).days > 3:
                    keep.append(row)
                    last_kept = row['signal_date']
        sig_df = pd.DataFrame(keep)
    
    return sig_df

def evaluate_signal(signal_date, state_df, windows=[5, 10, 20, 30]):
    """
    评估单个信号后的状态路径
    """
    future = state_df[state_df['trade_date'] >= signal_date].copy()
    if len(future) < max(windows) + 1:
        return None
    
    result = {'signal_date': signal_date}
    
    # 窗口收益（用市场平均change_pct，从market_snapshots查）
    # 这里直接从state_df的r_pxt列无法直接算收益，需要mkt_ret
    
    return result

def main():
    conn = get_conn()
    
    print("="*60)
    print("大盘级卖出信号回测 v2 — 状态转换信号")
    print("="*60)
    
    # 1) 加载状态
    print("\n[1] 加载大盘状态...")
    state = load_all_state(conn)
    print(f"  {len(state)}天 ({state['trade_date'].min().date()} ~ {state['trade_date'].max().date()})")
    
    # 2) 从market_snapshots加载每日收益
    print("[2] 加载市场收益...")
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, AVG(change_pct)/100 as mkt_ret, COUNT(*) as stock_count,
               SUM(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END)::float / COUNT(*) * 100 as adv_ratio_pct,
               AVG(volume) as avg_vol
        FROM market_snapshots
        WHERE trade_date >= '2019-01-01' AND change_pct IS NOT NULL
        GROUP BY trade_date ORDER BY trade_date
    """)
    mkt = pd.DataFrame(cur.fetchall(), columns=['trade_date', 'mkt_ret', 'stock_count', 'adv_ratio_pct', 'avg_vol'])
    mkt['trade_date'] = pd.to_datetime(mkt['trade_date'])
    mkt.set_index('trade_date', inplace=True)
    print(f"  {len(mkt)}天")
    
    # 3) 检测信号
    print("\n[3] 检测信号...")
    sig_df = detect_signals(state)
    print(f"  信号分布:")
    for stype, grp in sig_df.groupby('signal_type'):
        print(f"    {stype}: {len(grp)}次")
    
    # 4) 评估
    print("\n[4] 评估信号...")
    
    all_signal_types = ['r_warning', 'r_collapse_signal', 'b_accumulate_signal']
    
    for signal_type in all_signal_types:
        if signal_type == 'r_warning':
            dates = state[state['r_warning_val'].notna()]['trade_date'].tolist()
        else:
            if signal_type not in sig_df['signal_type'].values:
                print(f"\n  {signal_type}: 无信号")
                continue
            dates = sig_df[sig_df['signal_type'] == signal_type]['signal_date'].tolist()
        
        if not dates:
            continue
        
        results = []
        for d in dates:
            future = mkt[mkt.index >= d].copy()
            if len(future) < max(WINDOWS) + 1:
                continue
            
            r = {'signal_date': d}
            
            # 各窗口统计
            for w in WINDOWS:
                if len(future) > w:
                    ret = future.iloc[1:w+1]['mkt_ret'].sum()
                    cum = future.iloc[1:w+1]['mkt_ret'].cumsum()
                    max_dd = cum.min()
                    
                    r[f'ret_{w}d'] = ret
                    r[f'maxdd_{w}d'] = max_dd
                    r[f'pos_{w}d'] = (ret > 0)
                    
                    # 后续20日的最大下跌
                    if w == 20:
                        r['worst_drawdown'] = cum.min()
                        r['best_runup'] = cum.max()
            
            results.append(r)
        
        df = pd.DataFrame(results)
        
        print(f"\n  [{signal_type}] {len(df)}次信号")
        
        print(f"  {'窗口':<8} {'均值':<10} {'中位数':<10} {'正收益%':<10} {'中位回撤':<10}")
        for w in WINDOWS:
            col_ret = f'ret_{w}d'
            col_dd = f'maxdd_{w}d'
            col_pos = f'pos_{w}d'
            if col_ret in df.columns:
                vals = df[col_ret].dropna()
                dds = df[col_dd].dropna() if col_dd in df.columns else pd.Series()
                pos_rates = df[col_pos].dropna() if col_pos in df.columns else pd.Series()
                
                mean_v = vals.mean()
                med_v = vals.median()
                pos_rate = pos_rates.mean() if len(pos_rates) > 0 else 0
                med_dd = dds.median() if len(dds) > 0 else 0
                
                print(f"  T+{w:<4d} {mean_v:+.2%}     {med_v:+.2%}      {pos_rate:.1%}        {med_dd:.2%}")
        
        # 综合指标
        df['cum20'] = df['ret_20d'] if 'ret_20d' in df else 0
        df['is_bad'] = df['cum20'] < -0.02  # 跌幅>2%
        df['is_worse_than_random'] = df['cum20'] < -0.01
        
        bad_rate = df['is_bad'].mean()
        worse_rate = df['is_worse_than_random'].mean()
        print(f"  跌幅>2%: {bad_rate:.1%}")
        print(f"  跌幅>1%: {worse_rate:.1%}")
    
    conn.close()

if __name__ == '__main__':
    WINDOWS = [5, 10, 20, 30]
    main()
