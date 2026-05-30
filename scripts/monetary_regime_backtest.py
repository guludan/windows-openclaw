"""
货币因子f + 市场状态 + 分状态卖出信号回测

步骤：
1. 从macro_indicators合成货币因子f（M2/ SHIBOR/ 信用利差）
2. 用f + 60日均线定义市场状态 (BULL/BEAR/NEUTRAL)
3. 分状态评估卖出信号的有效性
4. 计算超额收益（vs同状态无信号日）
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import numpy as np
import pandas as pd
import json, os
from datetime import datetime, timedelta

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT = '/home/tearsyu/.openclaw/workspace/memory/reports/'
WINDOWS = [5, 10, 20, 30, 60]

def get_conn():
    return psycopg2.connect(DB)

def compute_monetary_factor(conn, start_date='2005-01-01'):
    """
    货币因子 f
    综合 M2增速、SHIBOR、信用利差 的标准化合成
    
    f > 1.0 → 紧货币 (M2低增速 + SHIBOR高 + 利差大)
    f < 1.0 → 宽货币 (M2高增速 + SHIBOR低 + 利差小)
    """
    cur = conn.cursor()
    
    # M2 (月度)
    cur.execute("""
        SELECT date, value FROM macro_indicators 
        WHERE indicator='CN_M2_YOY' ORDER BY date
    """)
    m2 = {r[0]: r[1] for r in cur.fetchall()}
    
    # SHIBOR 3M
    cur.execute("""
        SELECT date, value FROM macro_indicators 
        WHERE indicator='CN_SHIBOR_3M' ORDER BY date
    """)
    shibor = {r[0]: r[1] for r in cur.fetchall()}
    
    # 信用利差 (AAA企业债 - 国债)
    cur.execute("""
        SELECT date, value FROM macro_indicators 
        WHERE indicator='CN_AAA_SPREAD' ORDER BY date
    """)
    spread = {r[0]: r[1] for r in cur.fetchall()}
    
    # 合成: 每日用最新值
    dates = sorted(shibor.keys())  # SHIBOR是日频，作为主时间轴
    
    m2_vals = []
    shibor_vals = []
    spread_vals = []
    
    last_m2 = list(m2.values())[0]
    last_shibor = list(shibor.values())[0]
    last_spread = list(spread.values())[0]
    
    for d in dates:
        if d in m2:
            last_m2 = m2[d]
        if d in shibor:
            last_shibor = shibor[d]
        if d in spread:
            last_spread = spread[d]
        
        m2_vals.append(last_m2)
        shibor_vals.append(last_shibor)
        spread_vals.append(last_spread)
    
    # 标准化为z-score并合成
    m2_z = (np.array(m2_vals) - np.mean(m2_vals)) / np.std(m2_vals)
    shibor_z = (np.array(shibor_vals) - np.mean(shibor_vals)) / np.std(shibor_vals)
    spread_z = (np.array(spread_vals) - np.mean(spread_vals)) / np.std(spread_vals)
    
    # f = 1 + 0.5*(-m2_z) + 0.3*shibor_z + 0.2*spread_z
    # 反向M2: M2高增速→f低(宽货币)；正SHIBOR: SHIBOR高→f高(紧货币)
    f_vals = 1.0 + 0.5*(-m2_z) + 0.3*shibor_z + 0.2*spread_z
    f_vals = np.clip(f_vals, 0.3, 2.0)  # 裁剪极端值
    
    result = pd.DataFrame({
        'date': dates,
        'f_factor': f_vals,
        'm2_z': m2_z,
        'shibor_z': shibor_z,
        'spread_z': spread_z,
    })
    
    return result

def compute_market_state(f_df, mkt_df):
    """
    用货币因子f + 60日均线定义市场状态
    
    BULL: f < 0.9 或 60日均线向上斜率>2%
    BEAR: f > 1.1 或 60日均线向下斜率<-2%  
    NEUTRAL: 其他
    """
    mkt = mkt_df.copy()
    mkt['date'] = pd.to_datetime(mkt['trade_date'])
    
    # 60日均线
    mkt['ma60'] = mkt['mkt_ret'].rolling(60).mean()
    mkt['ma60_slope'] = mkt['ma60'].diff(20)
    
    # 对齐f因子（按月频插值到日频）
    f_df['date'] = pd.to_datetime(f_df['date'])
    mkt = mkt.merge(f_df[['date', 'f_factor']], on='date', how='left')
    mkt['f_factor'] = mkt['f_factor'].ffill()  # 月度M2前向填充
    
    def get_regime(row):
        if pd.isna(row.get('f_factor')) or pd.isna(row.get('ma60_slope')):
            return 'NEUTRAL'
        f = row['f_factor']
        slope = row['ma60_slope']
        
        if f < 0.9 and slope > -0.001:
            return 'BULL'
        elif f > 1.1 and slope < 0.001:
            return 'BEAR'
        else:
            return 'NEUTRAL'
    
    mkt['regime'] = mkt.apply(get_regime, axis=1)
    
    return mkt[['trade_date', 'mkt_ret', 'regime', 'f_factor', 'ma60', 'ma60_slope']]

def load_signals(conn):
    """加载r_warning + 新检测的r_collapse_signal"""
    cur = conn.cursor()
    
    # r_warning
    cur.execute("""
        SELECT trade_date, value, extra::text FROM market_signals
        WHERE signal_type='r_warning' AND trade_date >= '2019-01-01'
        ORDER BY trade_date
    """)
    rw = []
    for r in cur.fetchall():
        extra = json.loads(r[2]) if r[2] else {}
        rw.append({'trade_date': r[0], 'signal_type': 'r_warning', 'strength': r[1],
                   'subtype': extra.get('warning', '')})
    
    # r_index
    cur.execute("""
        SELECT trade_date, value, extra::text FROM market_signals
        WHERE signal_type='r_index' AND trade_date >= '2019-01-01'
        ORDER BY trade_date
    """)
    r_idx = {r[0]: {'r_val': r[1], 'extra': json.loads(r[2]) if r[2] else {}}
             for r in cur.fetchall()}
    
    # ba_surplus
    cur.execute("""
        SELECT trade_date, value FROM market_signals
        WHERE signal_type='ba_surplus' AND trade_date >= '2019-01-01'
        ORDER BY trade_date
    """)
    ba = {r[0]: r[1] for r in cur.fetchall()}
    
    # 检测r_collapse_signal
    dates = sorted(r_idx.keys())
    collapse_signals = []
    b_accum_signals = []
    
    for i in range(5, len(dates)):
        window = dates[max(0,i-5):i+1]
        r_pcts = []
        for d in window:
            if d in r_idx and 'r_pct' in r_idx[d]['extra']:
                r_pcts.append(r_idx[d]['extra']['r_pct'])
        
        if len(r_pcts) >= 3:
            first = r_pcts[:len(r_pcts)//2]
            second = r_pcts[len(r_pcts)//2:]
            if max(first) >= 0.75 and min(second) <= 0.3:
                collapse_signals.append({
                    'trade_date': dates[i], 'signal_type': 'r_collapse',
                    'strength': max(first) - min(second)
                })
        
        # b_accum
        if dates[i] in ba:
            # 需要连续5天的ba
            ba_win = []
            for d in window:
                if d in ba:
                    ba_win.append(ba[d])
            if len(ba_win) >= 3:
                if ba_win[0] < 0.2 and ba_win[-1] > 0.4:
                    b_accum_signals.append({
                        'trade_date': dates[i], 'signal_type': 'b_accum',
                        'strength': ba_win[-1] - ba_win[0]
                    })
    
    return pd.DataFrame(rw + collapse_signals + b_accum_signals)

def main():
    conn = get_conn()
    
    print("="*60)
    print("货币因子f + 市场状态 + 分状态回测")
    print("="*60)
    
    # 1) 市场收益
    print("\n[1] 加载市场收益...")
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, AVG(change_pct)/100 as mkt_ret, 
               COUNT(*) as stock_count
        FROM market_snapshots
        WHERE trade_date >= '2010-01-01' AND change_pct IS NOT NULL
        GROUP BY trade_date ORDER BY trade_date
    """)
    mkt = pd.DataFrame(cur.fetchall(), columns=['trade_date', 'mkt_ret', 'stock_count'])
    mkt['trade_date'] = pd.to_datetime(mkt['trade_date'])
    print(f"  {len(mkt)}天")
    
    # 2) 货币因子f
    print("[2] 计算货币因子f...")
    f_df = compute_monetary_factor(conn)
    print(f"  {len(f_df)}天, f 范围=[{f_df['f_factor'].min():.2f}, {f_df['f_factor'].max():.2f}]")
    print(f"  最近f: {f_df['f_factor'].iloc[-5:].tolist()}")
    
    # 3) 市场状态
    print("[3] 计算市场状态...")
    state_df = compute_market_state(f_df, mkt)
    state_df = state_df[state_df['trade_date'] >= '2019-01-01'].copy()
    regime_counts = state_df['regime'].value_counts()
    print(f"  状态分布:")
    for r, c in regime_counts.items():
        print(f"    {r}: {c}天 ({c/len(state_df)*100:.1f}%)")
    
    # 4) 信号
    print("[4] 加载信号...")
    sig_df = load_signals(conn)
    # 只保留2019-01-01后
    cutoff = pd.Timestamp('2019-01-01')
    for col in ['trade_date']:
        if col in sig_df.columns and sig_df[col].dtype == 'object':
            sig_df[col] = pd.to_datetime(sig_df[col])
    sig_df = sig_df[sig_df['trade_date'] >= cutoff].copy()
    sig_type_counts = sig_df['signal_type'].value_counts()
    print(f"  信号:")
    for t, c in sig_type_counts.items():
        print(f"    {t}: {c}次")
    conn.close()
    
    # 5) 分状态回测
    print("\n[5] 分状态回测...")
    
    results = {}
    
    for sig_type in sig_df['signal_type'].unique():
        sig_dates = sig_df[sig_df['signal_type'] == sig_type]['trade_date'].tolist()
        
        for regime in ['BULL', 'BEAR', 'NEUTRAL']:
            # 信号日在该状态下的
            regime_dates = state_df[state_df['regime'] == regime]['trade_date'].tolist()
            valid_sigs = [d for d in sig_dates if d in regime_dates]
            
            if len(valid_sigs) < 3:
                continue
            
            # 对照组：同状态无信号日的同期收益
            control_dates = [d for d in regime_dates 
                           if d not in set(sig_dates)
                           and d >= state_df['trade_date'].iloc[60]]  # 确保有足够历史
            
            sig_returns = {}
            control_returns = {}
            
            for w in WINDOWS:
                sr = []
                for d in valid_sigs:
                    future = state_df[state_df['trade_date'] >= d].iloc[1:w+1]
                    if len(future) >= w:
                        sr.append(future['mkt_ret'].sum())
                
                cr = []
                for d in np.random.choice(control_dates, min(len(control_dates), 500), replace=False):
                    future = state_df[state_df['trade_date'] >= d].iloc[1:w+1]
                    if len(future) >= w:
                        cr.append(future['mkt_ret'].sum())
                
                if sr and cr:
                    sig_returns[w] = np.array(sr)
                    control_returns[w] = np.array(cr)
            
            key = f"{sig_type}@{regime}"
            results[key] = {
                'n': len(valid_sigs),
                'sig_returns': sig_returns,
                'control_returns': control_returns,
            }
    
    # 6) 输出
    print("\n[6] 结果\n")
    
    print(f"{'信号@状态':<25} {'次数':<6} {'窗口':<6} {'信号均值':<10} {'对照均值':<10} {'超额':<10} {'优于对照':<10} {'信号回撤':<10}")
    print("-"*90)
    
    report_lines = []
    report_lines.append("# 货币因子f + 市场状态 + 分状态回测\n")
    report_lines.append(f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    report_lines.append("")
    
    for key, r in sorted(results.items()):
        if r['n'] < 5:
            continue
        
        report_lines.append(f"## {key}\n")
        report_lines.append(f"次数: {r['n']}\n")
        report_lines.append(f"| 窗口 | 信号收益 | 对照收益 | 超额 | 优于对照 | 信号回撤 |\n")
        report_lines.append(f"|------|---------|---------|------|---------|---------|\n")
        
        for w in WINDOWS:
            if w not in r['sig_returns']:
                continue
            
            sr = r['sig_returns'][w]
            cr = r['control_returns'][w]
            
            sig_mean = sr.mean()
            sig_med = np.median(sr)
            ctrl_mean = cr.mean()
            ctrl_med = np.median(cr)
            excess = sig_mean - ctrl_mean
            better = (sr > ctrl_med).mean()  # 信号收益 > 对照中位数的比例
            
            # 信号日最大回撤
            sig_dd = np.percentile(sr, 5)  # 5分位
            
            print(f"{key:<25} {r['n']:<6} T+{w:<4} {sig_mean:+8.2%}  {ctrl_mean:+8.2%}  {excess:+8.2%}  {better:<8.1%} {sig_dd:+8.2%}")
            
            report_lines.append(f"| T+{w} | {sig_mean:+.2%} | {ctrl_mean:+.2%} | {excess:+.2%} | {better:.1%} | {sig_dd:.2%} |\n")
        
        report_lines.append("")
    
    out_path = OUT + f"monetary_regime_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        f.write(''.join(report_lines))
    
    print(f"\n报告: {out_path}")

if __name__ == '__main__':
    main()
