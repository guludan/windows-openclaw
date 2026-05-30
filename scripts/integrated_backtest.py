"""
货币因子f + 市场状态 + 波动分层 + 年化超额回测

三层：
  Level 0: 市场状态 (BULL/BEAR/NEUTRAL) — 用货币因子f+60日均线
  Level 1: 信号类型 (r_warning / r_collapse / camp_diverge)
  Level 2: 个股波动分层 (低/中/高)

评估设计：
  - 信号超额 = 信号后收益 - 同时段10Y国债无风险收益
  - 个股信号超额 = 个股收益 - 同波动层无信号日的平均收益
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import numpy as np
import pandas as pd
import json, os
from datetime import datetime, timedelta

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT = '/home/tearsyu/.openclaw/workspace/memory/reports/'
WINDOWS = [5, 10, 20, 60]
VOL_QUANTILES = [0, 0.3, 0.6, 1.0]  # 波动率三分层

def get_conn():
    return psycopg2.connect(DB)

# ──────────────────────────────
# Ⅰ. 货币因子f
# ──────────────────────────────
def compute_monetary_factor(conn):
    cur = conn.cursor()
    cur.execute("SELECT date, value FROM macro_indicators WHERE indicator='CN_M2_YOY' ORDER BY date")
    m2 = {r[0]: r[1] for r in cur.fetchall()}
    
    cur.execute("SELECT date, value FROM macro_indicators WHERE indicator='CN_SHIBOR_3M' ORDER BY date")
    shibor = {r[0]: r[1] for r in cur.fetchall()}
    
    cur.execute("SELECT date, value FROM macro_indicators WHERE indicator='CN_AAA_SPREAD' ORDER BY date")
    spread = {r[0]: r[1] for r in cur.fetchall()}
    
    # 10Y国债
    cur.execute("SELECT date, value FROM macro_indicators WHERE indicator='CN_10Y_YIELD' ORDER BY date")
    bond = {r[0]: r[1] for r in cur.fetchall()}
    
    dates = sorted(set(list(shibor.keys()) + list(bond.keys())))
    
    cache = {'m2': list(m2.values())[0], 'shibor': list(shibor.values())[0],
             'spread': list(spread.values())[0], 'bond': list(bond.values())[0]}
    
    m2_l, shibor_l, spread_l, bond_l = [], [], [], []
    
    for d in dates:
        if d in m2: cache['m2'] = m2[d]
        if d in shibor: cache['shibor'] = shibor[d]
        if d in spread: cache['spread'] = spread[d]
        if d in bond: cache['bond'] = bond[d]
        
        m2_l.append(cache['m2'])
        shibor_l.append(cache['shibor'])
        spread_l.append(cache['spread'])
        bond_l.append(cache['bond'])
    
    m2_z = (np.array(m2_l) - np.mean(m2_l)) / (np.std(m2_l) + 1e-8)
    shibor_z = (np.array(shibor_l) - np.mean(shibor_l)) / (np.std(shibor_l) + 1e-8)
    spread_z = (np.array(spread_l) - np.mean(spread_l)) / (np.std(spread_l) + 1e-8)
    
    f = 1.0 + 0.5*(-m2_z) + 0.3*shibor_z + 0.2*spread_z
    f = np.clip(f, 0.3, 2.0)
    
    return pd.DataFrame({'date': pd.to_datetime(dates), 'f_factor': f, 'bond_yield': bond_l})

# ──────────────────────────────
# Ⅱ. 市场状态
# ──────────────────────────────
def compute_market_state(mkt_ret_series, f_factor_series, bond_series):
    """用f + 60日均线识别BULL/BEAR/NEUTRAL"""
    df = pd.DataFrame({'mkt_ret': mkt_ret_series, 'f': f_factor_series, 'bond': bond_series}).dropna()
    df = df[df.index >= pd.Timestamp('2019-01-01')].copy()
    
    df['ma60'] = df['mkt_ret'].rolling(60).mean()
    df['ma60_slope'] = df['ma60'].diff(20)
    
    # 年化超额收益
    df['cumret_annual'] = df['mkt_ret'].rolling(252).sum()
    
    def get_regime(row):
        if pd.isna(row.get('f')) or pd.isna(row.get('ma60_slope')):
            return 'NEUTRAL'
        f = row['f']
        slope = row['ma60_slope']
        
        if f < 0.9 and slope > -0.0005:
            return 'BULL'
        elif f > 1.1 and slope < 0.0005:
            return 'BEAR'
        else:
            return 'NEUTRAL'
    
    df['regime'] = df.apply(get_regime, axis=1)
    
    # 年化超额 = 252日累计 - 无风险
    daily_rf = bond_series / 100 / 365
    df['excess_annual'] = df['cumret_annual'] - daily_rf.reindex(df.index).ffill() * 252
    
    return df

# ──────────────────────────────
# Ⅲ. 个股波动率分层
# ──────────────────────────────
def compute_vol_layers(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT code, STDDEV(change_pct) as vol, COUNT(*) as days
        FROM market_snapshots
        WHERE trade_date >= '2023-01-01' AND change_pct IS NOT NULL AND change_pct != 0
        GROUP BY code
        HAVING COUNT(*) > 50
    """)
    rows = cur.fetchall()
    vols = sorted([r[1] for r in rows])
    
    p30 = np.percentile(vols, 30)
    p60 = np.percentile(vols, 60)
    
    layers = {}
    for code, vol, days in rows:
        if vol < p30:
            layer = 'LOW'
        elif vol < p60:
            layer = 'MID'
        else:
            layer = 'HIGH'
        layers[code] = {'vol': vol, 'layer': layer, 'days': days}
    
    return layers, {'p30': p30, 'p60': p60}

# ──────────────────────────────
# Ⅳ. 创业板/科创板波动溢价
# ──────────────────────────────
def get_stock_tags(conn):
    """获取股票的板块标签、所属概念"""
    cur = conn.cursor()
    cur.execute("SELECT code, name FROM stock_list")
    return {r[0]: r[1] for r in cur.fetchall()}

# ──────────────────────────────
# Ⅴ. 信号检测 + 收益评估
# ──────────────────────────────
def evaluate_signal_group(signal_dates, title, state_df, conn, windows=WINDOWS):
    """
    对一组信号日期做评估
    
    返回每个窗口的超额收益统计（vs 10Y国债）
    """
    results = {}
    
    # 按市场状态分组
    state_df = state_df.copy()
    
    for regime in ['BULL', 'BEAR', 'NEUTRAL']:
        regime_dates = set(state_df[state_df['regime'] == regime].index)
        sig_in_regime = [d for d in signal_dates if d in regime_dates]
        
        if len(sig_in_regime) < 3:
            continue
        
        for w in windows:
            rets = []
            rf_rets = []
            
            for d in sig_in_regime:
                if d not in state_df.index:
                    continue
                try:
                    idx = state_df.index.get_loc(d)
                except:
                    continue
                future = state_df.iloc[idx+1:idx+w+1]
                if len(future) < w:
                    continue
                
                ret = future['mkt_ret'].sum()
                rf = future['daily_rf'].sum() if 'daily_rf' in future.columns else 0
                
                rets.append(ret)
                rf_rets.append(rf)
            
            if len(rets) > 0:
                key = f"{title}@{regime}@T+{w}"
                rets_a = np.array(rets)
                rf_a = np.array(rf_rets)
                excess = rets_a - rf_a
                
                # 年化
                annual_excess = excess.mean() * (252 / w)
                
                results[key] = {
                    'n': len(rets), 'sig_type': title, 'regime': regime,
                    'window': w,
                    'mean_ret': rets_a.mean(),
                    'median_ret': np.median(rets_a),
                    'mean_rf': rf_a.mean(),
                    'mean_excess': excess.mean(),
                    'annual_excess': annual_excess,
                    'pos_rate': (rets_a > rf_a).mean(),
                    'excess_std': excess.std(),
                    'excess_sharpe': excess.mean() / (excess.std() + 1e-8) * np.sqrt(252 / w),
                }
    
    return results

# ──────────────────────────────
# Ⅵ. camp_diverge个体信号评估
# ──────────────────────────────
def evaluate_camp_diverge(camp_signal_type, conn, state_df, vol_layers, windows=WINDOWS):
    """评估camp_diverge信号在个股上的表现，按波动分层"""
    cur = conn.cursor()
    
    # 获取具体camp_diverge条目
    cur.execute("""
        SELECT trade_date, code, signal, strength, extra::text
        FROM market_signals
        WHERE signal_type = 'camp_diverge'
          AND trade_date >= '2019-01-01'
          AND signal = %s
        ORDER BY trade_date
    """, (camp_signal_type,))
    
    rows = cur.fetchall()
    print(f"    {camp_signal_type}: {len(rows)}条")
    
    if len(rows) < 10:
        return {}
    
    # 按波动分层 + 市场状态分组
    results = {}
    layer_groups = {'LOW': [], 'MID': [], 'HIGH': []}
    
    for r in rows:
        code = r[1]
        layer = vol_layers.get(code, {}).get('layer', 'MID')
        if layer in layer_groups:
            layer_groups[layer].append(r)
    
    for layer_name, layer_rows in layer_groups.items():
        if len(layer_rows) < 5:
            continue
        
        # 按市场状态分组
        for regime in ['BULL', 'BEAR', 'NEUTRAL']:
            regime_dates = set(state_df[state_df['regime'] == regime].index)
            
            reg_rows = [r for r in layer_rows if r[0] in regime_dates]
            if len(reg_rows) < 3:
                continue
            
            for w in [5, 10]:
                rets = []
                for r in reg_rows:
                    d, code = r[0], r[1]
                    # 查询daily_prices得到个股在信号后的涨幅
                    cur2 = conn.cursor()
                    try:
                        cur2.execute("""
                            SELECT close FROM daily_prices
                            WHERE symbol = %s AND trade_date BETWEEN %s AND %s
                            ORDER BY trade_date
                        """, (code, d, d + timedelta(days=w)))
                        prices = [p[0] for p in cur2.fetchall() if p[0] is not None]
                        if len(prices) >= 2:
                            ret = (prices[-1] - prices[0]) / prices[0]
                            rets.append(ret)
                    except:
                        pass
                    finally:
                        cur2.close()
                
                if len(rets) > 0:
                    key = f"camp_{camp_signal_type}@{layer_name}@{regime}@T+{w}"
                    results[key] = {
                        'n': len(rets), 'sig_type': camp_signal_type,
                        'layer': layer_name, 'regime': regime, 'window': w,
                        'mean_ret': np.mean(rets), 'median_ret': np.median(rets),
                        'pos_rate': (np.array(rets) > 0).mean(),
                    }
    
    return results

# ──────────────────────────────
# 主函数
# ──────────────────────────────
def main():
    conn = get_conn()
    print("="*60)
    print("整合回测 v1 — 货币因子f + 市场状态 + 波动分层 + 年化超额")
    print("="*60)
    
    # 1) 市场收益
    print("\n[1] 加载市场数据...")
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, AVG(change_pct)/100 as mkt_ret
        FROM market_snapshots
        WHERE trade_date >= '2019-01-01' AND change_pct IS NOT NULL
        GROUP BY trade_date ORDER BY trade_date
    """)
    mkt = pd.DataFrame(cur.fetchall(), columns=['trade_date', 'mkt_ret'])
    mkt['trade_date'] = pd.to_datetime(mkt['trade_date'])
    mkt.set_index('trade_date', inplace=True)
    print(f"  {len(mkt)}天")
    
    # 2) 货币因子f + 10Y国债
    print("[2] 计算货币因子f...")
    f_df = compute_monetary_factor(conn)
    f_series = f_df.set_index('date')['f_factor'].reindex(mkt.index).ffill()
    bond_series = f_df.set_index('date')['bond_yield'].reindex(mkt.index).ffill()
    print(f"  最近f={f_series.iloc[-1]:.3f}, 10Y={bond_series.iloc[-1]:.2f}%")
    
    # 3) 市场状态
    print("[3] 计算市场状态...")
    state_df = compute_market_state(mkt['mkt_ret'], f_series, bond_series)
    
    # 增加daily_rf列到state_df
    state_df['daily_rf'] = bond_series / 100 / 365
    
    regime_counts = state_df['regime'].value_counts()
    print(f"  状态:")
    for r, c in regime_counts.items():
        print(f"    {r}: {c}天 ({c/len(state_df)*100:.1f}%)")
    
    # 4) 个股波动率分层
    print("[4] 计算个股波动率分层...")
    vol_layers, vol_thresh = compute_vol_layers(conn)
    print(f"  阈值: LOW<{vol_thresh['p30']:.2f}%  MID<{vol_thresh['p60']:.2f}%  HIGH>={vol_thresh['p60']:.2f}%")
    low_n = sum(1 for v in vol_layers.values() if v['layer'] == 'LOW')
    mid_n = sum(1 for v in vol_layers.values() if v['layer'] == 'MID')
    high_n = sum(1 for v in vol_layers.values() if v['layer'] == 'HIGH')
    print(f"  分布: LOW={low_n}  MID={mid_n}  HIGH={high_n}")
    
    # 5) 信号检测 + 大盘级回测
    print("\n[5] 大盘级信号检测...")
    
    # r_warning
    cur.execute("SELECT trade_date FROM market_signals WHERE signal_type='r_warning' AND trade_date >= '2019-01-01'")
    rw_dates = sorted(set(pd.to_datetime([r[0] for r in cur.fetchall()])))
    print(f"  r_warning: {len(rw_dates)}次")
    
    # r_collapse (从r_index检测)
    cur.execute("SELECT trade_date, extra::text FROM market_signals WHERE signal_type='r_index' AND trade_date >= '2019-01-01' ORDER BY trade_date")
    r_idx = {r[0]: json.loads(r[1]) if r[1] else {} for r in cur.fetchall()}
    
    collapse_dates = []
    sorted_dates = sorted(r_idx.keys())
    for i in range(5, len(sorted_dates)):
        win = sorted_dates[max(0,i-5):i+1]
        r_pcts = []
        for d in win:
            if d in r_idx and 'r_pct' in r_idx[d]:
                r_pcts.append(r_idx[d]['r_pct'])
        if len(r_pcts) >= 3:
            first_half = r_pcts[:len(r_pcts)//2]
            second_half = r_pcts[len(r_pcts)//2:]
            if max(first_half) >= 0.75 and min(second_half) <= 0.30:
                collapse_dates.append(pd.to_datetime(sorted_dates[i]))
    
    collapse_dates = sorted(set(collapse_dates))
    print(f"  r_collapse: {len(collapse_dates)}次")
    
    print("\n[6] 大盘级分状态回测...")
    all_results = {}
    
    for title, dates in [('r_warning', rw_dates), ('r_collapse', collapse_dates)]:
        results = evaluate_signal_group(dates, title, state_df, conn)
        all_results.update(results)
    
    # 6) 输出大盘级
    print(f"\n{'信号@状态@窗口':<30} {'次数':<6} {'收益':<10} {'无风险':<10} {'超额':<10} {'年化超额':<10} {'胜率':<8} {'夏普':<8}")
    print("-"*90)
    
    for key in sorted(all_results.keys()):
        r = all_results[key]
        if r['n'] < 5:
            continue
        print(f"{key:<30} {r['n']:<6} {r['mean_ret']:+7.2%}  {r['mean_rf']:+7.2%}  {r['mean_excess']:+7.2%}  "
              f"{r['annual_excess']:+7.2%}  {r['pos_rate']:<7.1%} {r['excess_sharpe']:<+7.2f}")
    
    # 7) 个股级camp_diverge信号
    print("\n[7] 个股级camp_diverge信号...")
    cur.execute("SELECT DISTINCT signal FROM market_signals WHERE signal_type='camp_diverge'")
    camp_types = [r[0] for r in cur.fetchall()]
    print(f"  camp_diverge类型: {camp_types}")
    
    inst_sell_hot_buy = None
    for ct in camp_types:
        if 'inst_sell' in ct and 'hot_buy' in ct:
            inst_sell_hot_buy = ct
            break
    
    if inst_sell_hot_buy:
        camp_results = evaluate_camp_diverge(inst_sell_hot_buy, conn, state_df, vol_layers)
        all_results.update(camp_results)
        
        if camp_results:
            print(f"\n{'信号@波动@状态@窗口':<35} {'次数':<6} {'收益':<10} {'胜率':<8}")
            print("-"*65)
            for key in sorted(camp_results.keys()):
                r = camp_results[key]
                print(f"{key:<35} {r['n']:<6} {r['mean_ret']:+7.2%}  {r['pos_rate']:<7.1%}")
    
    conn.close()
    
    # 8) 报告
    print("\n[8] 生成报告...")
    lines = []
    lines.append("# 整合回测报告\n")
    lines.append(f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append("")
    
    lines.append("## 参数\n\n")
    lines.append(f"- 货币因子f合成: M2(-0.5z) + SHIBOR_3M(+0.3z) + 信用利差(+0.2z)\n")
    lines.append(f"- 市场状态: BULL(f<0.9+均线上行), BEAR(f>1.1+均线下行), NEUTRAL(其他)\n")
    lines.append(f"- 波动分层: LOW(<{vol_thresh['p30']:.2f}%), MID({vol_thresh['p30']:.2f}-{vol_thresh['p60']:.2f}%), HIGH(>={vol_thresh['p60']:.2f}%)\n")
    lines.append(f"- 无风险基准: CN_10Y_YIELD\n")
    lines.append(f"- 覆盖: 2019-01 ~ 2026-05\n")
    lines.append("")
    
    lines.append("## 大盘信号 — 分市场状态\n\n")
    lines.append(f"| 信号 | 状态 | 窗口 | 次数 | 绝对收益 | 无风险 | 超额收益 | 年化超额 | 胜率 | 夏普 |\n")
    lines.append(f"|------|------|------|------|---------|--------|---------|---------|------|------|\n")
    for key in sorted(all_results.keys()):
        r = all_results[key]
        if r['n'] < 5 or 'camp_' in key:
            continue
        lines.append(f"| {r['sig_type']} | {r['regime']} | T+{r['window']} | {r['n']} | "
                     f"{r['mean_ret']:+.2%} | {r['mean_rf']:+.2%} | {r['mean_excess']:+.2%} | "
                     f"{r['annual_excess']:+.2%} | {r['pos_rate']:.1%} | {r['excess_sharpe']:.2f} |\n")
    
    if camp_results:
        lines.append("\n## camp_diverge (inst_sell + hot_buy) — 分波动层+市场状态\n\n")
        lines.append(f"| 波动层 | 状态 | 窗口 | 次数 | 收益 | 胜率 |\n")
        lines.append(f"|--------|------|------|------|------|------|\n")
        for key in sorted(camp_results.keys()):
            r = camp_results[key]
            lines.append(f"| {r['layer']} | {r['regime']} | T+{r['window']} | {r['n']} | "
                         f"{r['mean_ret']:+.2%} | {r['pos_rate']:.1%} |\n")
    
    out_path = OUT + f"integrated_backtest_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        f.write(''.join(lines))
    
    print(f"报告: {out_path}")

if __name__ == '__main__':
    main()
