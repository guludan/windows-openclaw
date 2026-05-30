"""
大盘级卖出信号回测 — 状态转换路径检测

核心思路：不是测「信号后收益多少」，而是测
「信号后的状态转换路径是否按预期走」

预期路径：
  r_warning → R持续低位 → [B]下降 → 成交量萎缩 → 价格下跌/滞涨

异常路径（假阳性）：
  r_warning → R反弹 → [B]回升 → 价格继续涨

回测指标：
  1. 路径准确率（20日内状态转换符合预期）
  2. 信号后5/10/20日市场收益分布（期望左偏）
  3. 信号后最大回撤分布
  4. 假阳性率
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import numpy as np
import pandas as pd
import json, os
from datetime import datetime, timedelta

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT = '/home/tearsyu/.openclaw/workspace/memory/reports/'

def get_conn():
    return psycopg2.connect(DB)

def load_r_warnings(conn, start='2019-01-01'):
    """加载所有r_warning信号"""
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, value, extra::text
        FROM market_signals
        WHERE signal_type = 'r_warning'
          AND trade_date >= %s
        ORDER BY trade_date
    """, (start,))
    rows = []
    for r in cur.fetchall():
        extra = json.loads(r[2]) if r[2] else {}
        rows.append({'trade_date': r[0], 'r_value': r[1], 'warning': extra.get('warning', ''), 
                     'extra': extra})
    return pd.DataFrame(rows)

def load_market_state(conn, start='2019-01-01'):
    """加载大盘状态日序列: R指数 + [B]/[A] + 市场收益"""
    cur = conn.cursor()
    
    # r_index
    cur.execute("""
        SELECT trade_date, value, extra::text
        FROM market_signals
        WHERE signal_type = 'r_index'
          AND trade_date >= %s
        ORDER BY trade_date
    """, (start,))
    r_rows = {r[0]: {'r_val': r[1], 'extra': json.loads(r[2]) if r[2] else {}}
              for r in cur.fetchall()}
    
    # ba_surplus
    cur.execute("""
        SELECT trade_date, value, extra::text
        FROM market_signals
        WHERE signal_type = 'ba_surplus'
          AND trade_date >= %s
        ORDER BY trade_date
    """, (start,))
    b_rows = {r[0]: {'ba_val': r[1], 'extra': json.loads(r[2]) if r[2] else {}}
              for r in cur.fetchall()}
    
    # 合并
    all_dates = sorted(set(list(r_rows.keys()) + list(b_rows.keys())))
    rows = []
    for d in all_dates:
        r_info = r_rows.get(d, {})
        b_info = b_rows.get(d, {})
        re = r_info.get('extra', {})
        be = b_info.get('extra', {})
        rows.append({
            'trade_date': d,
            'r_val': r_info.get('r_val'),
            'r_pct': re.get('r_pct'),
            'b_conc': re.get('b_conc'),
            'a_conc': re.get('a_conc'),
            'surplus': b_info.get('ba_val'),
            'advance_ratio': re.get('advance_ratio'),
            'vol_z': re.get('vol_z'),
        })
    
    return pd.DataFrame(rows)

def load_market_returns(conn, start='2019-01-01'):
    """用market_snapshots算每日市场收益"""
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, 
               AVG(change_pct)/100 as mkt_ret,
               COUNT(*) as stock_count,
               SUM(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END)::float / COUNT(*) as advance_ratio,
               AVG(volume) as avg_vol
        FROM market_snapshots
        WHERE trade_date >= %s AND change_pct IS NOT NULL
        GROUP BY trade_date
        ORDER BY trade_date
    """, (start,))
    cols = ['trade_date', 'mkt_ret', 'stock_count', 'advance_ratio', 'avg_vol']
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df

WINDOWS = [5, 10, 20, 30, 60]

def evaluate_signal(date, mkt_df, state_df, windows=WINDOWS):
    """
    评估一个信号日后的状态转换路径
    
    返回:
        expected_path: 是否符合预期路径
        anomaly_type: 异常类型
        window_returns: 各窗口收益
        max_drawdown: 信号后最大回撤
        r_trajectory: R指数路径
    """
    mkt = mkt_df[mkt_df['trade_date'] >= pd.Timestamp(date)].copy()
    state = state_df[state_df['trade_date'] >= pd.Timestamp(date)].copy()
    
    if len(mkt) < max(windows) + 1:
        return None
    
    result = {'signal_date': date, 'date_index': 0}
    
    # 1) 各窗口收益
    for w in windows:
        if len(mkt) > w:
            result[f'ret_{w}d'] = mkt.iloc[1:w+1]['mkt_ret'].sum()
            result[f'max_dd_{w}d'] = (mkt.iloc[1:w+1]['mkt_ret'].cumsum().min() if w > 0 else 0)
            result[f'cumret_{w}d'] = mkt.iloc[1:w+1]['mkt_ret'].cumsum().tolist()
    
    # 2) 状态转换路径检测（20日内）
    # 预期：R低位(<20分位)持续、advance_ratio<0.5、跌幅确认
    future_mkt = mkt.iloc[1:22] if len(mkt) > 21 else mkt.iloc[1:]
    future_state = state[state['trade_date'].isin(future_mkt['trade_date'])]
    
    r20 = future_mkt['mkt_ret'].sum()  # 20日累计收益
    max_dd = future_mkt['mkt_ret'].cumsum().min()  # 最大回撤
    
    cost = len(future_mkt) if len(future_mkt) > 0 else 1
    # R指数状态
    r_low_count = 0
    r_recover = False
    if 'r_pct' in future_state.columns and len(future_state) > 5:
        r_pcts = future_state['r_pct'].dropna()
        r_low_count = (r_pcts < 0.2).sum()  # 严格：<20分位
        after_5 = r_pcts.iloc[5:] if len(r_pcts) > 5 else []
        r_recover = any(after_5 > 0.5) if len(after_5) > 0 else False  # 恢复到50分位以上
    
    # 成交量变化
    vol_shrink = False
    if 'avg_vol' in future_mkt.columns and len(future_mkt) > 3:
        vol_shrink = future_mkt['avg_vol'].iloc[:5].mean() < future_mkt['avg_vol'].iloc[0] * 0.8
    
    r_expected = (r_low_count >= 2) or (r20 < -0.03)  # R持续低位(<20分位) 或 明显下跌(-3%)
    no_anomaly = not r_recover  # 没有R反弹超过50分位
    
    result['r_low_days'] = r_low_count
    result['r_recovered'] = r_recover
    result['vol_shrank'] = vol_shrink
    result['r20_sum'] = r20
    result['max_dd'] = max_dd
    
    # 预期路径：跌幅明显 或 R低位+缩量
    result['path_expected'] = bool((r20 < -0.02) or (r_low_count >= 2 and vol_shrink))
    # 假警报：R反弹后20日还涨
    result['path_anomaly'] = bool(r_recover and r20 > 0.02)
    
    return result

def main():
    conn = get_conn()
    
    print("="*60)
    print("大盘级卖出信号回测 v1")
    print("核心：状态转换路径检测")
    print("="*60)
    
    # 1) 加载数据
    print("\n[1] 加载数据...")
    warnings = load_r_warnings(conn)
    print(f"  r_warning: {len(warnings)}天")
    
    market = load_market_returns(conn)
    market['trade_date'] = pd.to_datetime(market['trade_date'])
    market.set_index('trade_date', inplace=True)
    print(f"  市场收益: {len(market)}天")
    
    state = load_market_state(conn)
    state['trade_date'] = pd.to_datetime(state['trade_date'])
    print(f"  大盘状态: {len(state)}天")
    
    # 附加advance_ratio到market
    state_s = state[['trade_date', 'advance_ratio', 'b_conc', 'a_conc', 'r_pct']].copy()
    market = market.reset_index().merge(state_s, on='trade_date', how='left').set_index('trade_date')
    
    conn.close()
    
    # 2) 评估每个信号
    print("\n[2] 评估信号...")
    results = []
    for _, row in warnings.iterrows():
        sig_date = row['trade_date']
        r = evaluate_signal(sig_date, market.reset_index(), state)
        if r:
            r['warning_type'] = row.get('warning', '')
            results.append(r)
    
    df = pd.DataFrame(results)
    print(f"  有效信号: {len(df)}天")
    
    # 3) 统计
    print("\n[3] 状态转换路径统计")
    expected_rate = df['path_expected'].mean()
    anomaly_rate = df['path_anomaly'].mean()
    print(f"  预期路径率: {expected_rate:.1%}")
    print(f"  异常路径率（假阳性）: {anomaly_rate:.1%}")
    
    print("\n[4] 窗口收益统计")
    for w in WINDOWS:
        col = f'ret_{w}d'
        if col in df.columns:
            vals = df[col].dropna()
            pos_rate = (vals > 0).mean()
            median_val = vals.median()
            mean_val = vals.mean()
            skew = '左偏' if mean_val < median_val else '右偏' if mean_val > median_val else '对称'
            print(f"  T+{w:>2d}: 均值={mean_val:+.2%}, 中位数={median_val:+.2%}, "
                  f"正收益={pos_rate:.1%}, 偏度={skew}")
    
    print("\n[5] 最大回撤统计")
    for w in [10, 20]:
        col = f'max_dd_{w}d'
        if col in df.columns:
            vals = df[col].dropna()
            median_dd = vals.median()
            p75 = vals.quantile(0.75)
            p95 = vals.quantile(0.95)
            print(f"  T+{w:>2d}: 中位数回撤={median_dd:.2%}, P75={p75:.2%}, P95={p95:.2%}")
    
    print("\n[6] 分类统计")
    for wtype, grp in df.groupby('warning_type'):
        print(f"  {wtype}: {len(grp)}次, 预期路径率={grp['path_expected'].mean():.1%}, "
              f"假阳性率={grp['path_anomaly'].mean():.1%}")
    
    # 4) 报告
    lines = []
    lines.append("# 大盘级卖出信号回测 — 状态转换路径检测\n")
    lines.append(f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"信号源: r_warning ({len(warnings)}次)\n")
    lines.append(f"评估方法: 信号后20日内状态转换路径检测\n")
    lines.append("")
    
    lines.append("## 状态转换路径\n\n")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| 预期路径率 | {expected_rate:.1%} |")
    lines.append(f"| 异常路径率（假阳性）| {anomaly_rate:.1%} |")
    lines.append(f"| 有效信号 | {len(df)}次 |")
    lines.append("")
    
    lines.append("## 窗口收益\n\n")
    lines.append(f"| 窗口 | 均值 | 中位数 | 正收益占比 | 偏度 |")
    lines.append(f"|------|------|--------|-----------|------|")
    for w in WINDOWS:
        col = f'ret_{w}d'
        if col in df.columns:
            vals = df[col].dropna()
            pos_rate = (vals > 0).mean()
            median_val = vals.median()
            mean_val = vals.mean()
            skew = '左偏' if mean_val < median_val else '右偏' if mean_val > median_val else '对称'
            lines.append(f"| T+{w} | {mean_val:+.2%} | {median_val:+.2%} | {pos_rate:.1%} | {skew} |")
    lines.append("")
    
    lines.append("## 最大回撤\n\n")
    lines.append(f"| 窗口 | 中位数 | P75 | P95 |")
    lines.append(f"|------|--------|-----|-----|")
    for w in [10, 20]:
        col = f'max_dd_{w}d'
        if col in df.columns:
            vals = df[col].dropna()
            lines.append(f"| T+{w} | {vals.median():.2%} | {vals.quantile(0.75):.2%} | {vals.quantile(0.95):.2%} |")
    lines.append("")
    
    lines.append("## 信号类型对比\n\n")
    lines.append(f"| 类型 | 次数 | 预期路径率 | 假阳性率 | 20日收益均值 |")
    lines.append(f"|------|------|-----------|---------|-------------|")
    for wtype, grp in df.groupby('warning_type'):
        r20 = grp['ret_20d'].mean() if 'ret_20d' in grp else 0
        lines.append(f"| {wtype} | {len(grp)} | {grp['path_expected'].mean():.1%} | {grp['path_anomaly'].mean():.1%} | {r20:+.2%} |")
    lines.append("")
    
    out_path = OUT + f"signal_backtest_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        f.write('\n'.join(lines))
    
    print(f"\n报告: {out_path}")

if __name__ == '__main__':
    main()
