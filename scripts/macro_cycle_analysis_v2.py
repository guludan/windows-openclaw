"""
宏观周期 × 事件CAR 联合分析 + 衰减模型校准 v2
使用沪深300市场状态 + 信用利差/SHIBOR 做周期划分
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import numpy as np
import pandas as pd
import json, os
from datetime import datetime

OUT = '/home/tearsyu/.openclaw/workspace/memory/reports/macro_cycle_'

def get_db():
    return psycopg2.connect('host=192.168.3.14 dbname=guludan user=guludan password=toto')

def load_macro(conn):
    """加载宏观指标"""
    cur = conn.cursor()
    dfs = {}
    for ind in ['CN_M2_YOY', 'CN_M1_YOY', 'CN_10Y_YIELD', 'CN_AAA_10Y', 'CN_AAA_SPREAD',
                'CN_SHIBOR_1M', 'CN_SHIBOR_1W', 'CN_LPR_1Y', 'CN_LPR_5Y']:
        cur.execute("SELECT date, value FROM macro_indicators WHERE indicator=%s ORDER BY date", (ind,))
        rows = cur.fetchall()
        dfs[ind] = pd.DataFrame(rows, columns=['date', ind]).set_index('date')
    
    macro = dfs['CN_M2_YOY'].join([dfs[k] for k in list(dfs.keys())[1:]], how='outer')
    macro.index = pd.to_datetime(macro.index)
    macro = macro.sort_index().ffill().bfill()
    
    # 月度聚合（M2/M1本来就月度）
    monthly = macro.resample('ME').last()
    monthly['m2_ma3'] = monthly['CN_M2_YOY'].rolling(3, min_periods=2).mean()
    monthly['m1_ma3'] = monthly['CN_M1_YOY'].rolling(3, min_periods=2).mean()
    monthly['m1m2_gap'] = monthly['m1_ma3'] - monthly['m2_ma3']
    
    return monthly

def load_market_regimes(conn):
    """
    根据沪深300行情+行业等权指数划分市场状态
    使用market_snapshots中的沪深300指数
    """
    cur = conn.cursor()
    
    # 直接使用行业等权指数作为市场基准
    cur.execute("""
        SELECT trade_date, AVG(close) as mkt_level
        FROM industry_daily
        GROUP BY trade_date ORDER BY trade_date
    """)
    df = pd.DataFrame(cur.fetchall(), columns=['trade_date', 'mkt_level'])
    
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.set_index('trade_date')
    df['ret'] = df['mkt_level'].pct_change()
    
    # 滚动指标
    df['ma60'] = df['mkt_level'] / df['mkt_level'].rolling(60).mean()  # 相对60日均线
    df['vol_20'] = df['ret'].rolling(20).std()
    df['momentum_60'] = df['mkt_level'].pct_change(60)
    
    # 市场状态划分
    # 牛市: 60天动量 > 10% 且 价格在60均线上方
    # 熊市: 60天动量 < -10% 且 价格在60均线下方  
    # 震荡: 其他
    conditions = [
        (df['momentum_60'] > 0.10),
        (df['momentum_60'] < -0.10),
    ]
    choices = ['bull', 'bear']
    df['mkt_regime'] = np.select(conditions, choices, default='neutral')
    
    # 标签
    regime_labels = {'bull': '📈牛市', 'bear': '📉熊市', 'neutral': '→震荡'}
    df['mkt_label'] = df['mkt_regime'].map(regime_labels)
    
    return df

def load_events(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT e.event_id, e.event_date, e.source,
               n.direction, n.macro_score, n.importance_total,
               n.keyword_weights_json, n.industry_json
        FROM event_list e
        JOIN nlp_event_scores n ON e.event_id = n.event_id
    """)
    rows = cur.fetchall()
    events = []
    for r in rows:
        events.append({
            'event_id': r[0],
            'event_date': r[1], 'source': r[2], 'direction': r[3],
            'macro_score': r[4], 'importance_total': r[5],
            'keyword_hits': json.loads(r[6]) if r[6] else {},
            'industries': json.loads(r[7]) if r[7] else {}
        })
    return pd.DataFrame(events)

def load_car_by_event(conn):
    """加载事件CAR（按事件聚合）"""
    cur = conn.cursor()
    cur.execute("""
        SELECT event_id, industry_code, industry_name, 
               car_5, car_10, car_20, car_60
        FROM (
            SELECT event_id, industry_code, industry_name,
                   AVG(car) FILTER (WHERE t_relative BETWEEN -5 AND 5) as car_5,
                   AVG(car) FILTER (WHERE t_relative BETWEEN -5 AND 10) as car_10,
                   AVG(car) FILTER (WHERE t_relative BETWEEN -5 AND 20) as car_20,
                   AVG(car) FILTER (WHERE t_relative BETWEEN -5 AND 60) as car_60
            FROM event_car
            GROUP BY event_id, industry_code, industry_name
        ) sub
    """)
    cols = ['event_id', 'industry_code', 'industry_name', 'car_5', 'car_10', 'car_20', 'car_60']
    return pd.DataFrame(cur.fetchall(), columns=cols)

def load_industry_daily(conn):
    """全市场等权指数"""
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, AVG(close) as mkt_level
        FROM industry_daily GROUP BY trade_date ORDER BY trade_date
    """)
    df = pd.DataFrame(cur.fetchall(), columns=['trade_date', 'mkt_level'])
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df['ret'] = df['mkt_level'].pct_change()
    return df

def main():
    conn = get_db()
    
    print("加载宏观数据...")
    macro = load_macro(conn)
    
    print("加载市场状态(沪深300)...")
    market_regimes = load_market_regimes(conn)
    print(f"  市场状态分布: {market_regimes['mkt_label'].value_counts().to_dict()}")
    print(f"  日期: {market_regimes.index.min()} ~ {market_regimes.index.max()}")
    
    print("加载事件...")
    events = load_events(conn)
    events['event_date'] = pd.to_datetime(events['event_date'])
    print(f"  {len(events)}条事件")
    
    print("加载CAR...")
    car = load_car_by_event(conn)
    # 预处理前向填充缺失值
    for col in ['car_5', 'car_10', 'car_20', 'car_60']:
        car[col] = pd.to_numeric(car[col], errors='coerce')
    print(f"  {len(car)}行, {car['event_id'].nunique()}事件")
    
    print("加载市场指数...")
    ind_daily = load_industry_daily(conn)
    print(f"  {len(ind_daily)}天")
    
    conn.close()
    
    # ── 合并: 事件 + 市场状态 + 宏观周期 ──
    print("合并数据...")
    
    # 为每个事件找到对应日期的市场状态
    events_with_mkt = events.merge(
        market_regimes[['mkt_regime', 'mkt_label', 'ma60', 'momentum_60', 'vol_20', 'ret']].reset_index(),
        left_on='event_date', right_on='trade_date', how='left'
    )
    
    # 为缺失状态的日期做前向填充（最多5天）
    events_with_mkt['mkt_regime'] = events_with_mkt['mkt_regime'].ffill()
    events_with_mkt['mkt_label'] = events_with_mkt['mkt_label'].ffill()
    
    # 加宏观周期
    macro['macro_month'] = macro.index.to_period('M').astype(str)
    events_with_mkt['month'] = events_with_mkt['event_date'].dt.to_period('M').astype(str)
    events_with_mkt = events_with_mkt.merge(
        macro[['CN_M2_YOY', 'CN_M1_YOY', 'm1m2_gap', 'CN_10Y_YIELD', 'CN_AAA_SPREAD', 'CN_SHIBOR_1W', 'macro_month']],
        left_on='month', right_on='macro_month', how='left'
    )
    
    events_with_mkt = events_with_mkt.dropna(subset=['mkt_regime'])
    
    print(f"  有周期标签: {len(events_with_mkt)}")
    
    # ── 衰减模型校准 ──
    print("\n" + "="*60)
    print("衰减模型校准（基于市场状态×方向×CAR时间衰减）")
    print("="*60)
    
    # 合并事件CAR
    merged = events_with_mkt.merge(car, on='event_id', how='inner')
    print(f"  CAR可配对数: {len(merged)}")
    
    # 按市场状态+方向分组，每个组的CAR衰减
    calibrations = {}
    
    for (mkt_label, direction), grp in merged.groupby(['mkt_label', 'direction']):
        if len(grp) < 20:
            continue
        
        car5 = grp['car_5'].mean()
        car10 = grp['car_10'].mean()
        car20 = grp['car_20'].mean()
        car60 = grp['car_60'].mean()
        
        dir_name = '利好' if direction == 1 else ('利空' if direction == -1 else '中性')
        
        # 利好: 正CAR衰减；利空: 负CAR回复
        if direction == 1:
            peak = max(car5, car10, car20, car60)
            if car60 < peak and peak > 0.0001:
                lam = -np.log(max(car60, 0.0001) / peak) / 55
                lam = min(max(lam, 0.005), 0.5)
            else:
                lam = 0.03
        elif direction == -1:
            trough = min(car5, car10, car20, car60)
            if car60 > trough and trough < -0.0001:
                lam = -np.log(abs(car60) / abs(trough)) / 55
                lam = min(max(lam, 0.005), 0.5)
            else:
                lam = 0.03
        else:
            lam = 0.03
        
        calibrations[(mkt_label, dir_name)] = {
            'n': len(grp),
            'car5': car5, 'car10': car10, 'car20': car20, 'car60': car60,
            'lambda': round(lam, 3)
        }
        
        print(f"\n  [{mkt_label}|{dir_name}] n={len(grp):5d}  "
              f"CAR5={car5:+.5f} CAR10={car10:+.5f} CAR20={car20:+.5f} CAR60={car60:+.5f}  "
              f"λ={lam:.3f}")
    
    # 全样本综合
    for direction, label in [(1, '利好'), (-1, '利空')]:
        grp = merged[merged['direction'] == direction]
        if len(grp) < 20: continue
        car5, car10, car20, car60 = [grp[c].mean() for c in ['car_5', 'car_10', 'car_20', 'car_60']]
        
        if direction == 1:
            peak = max(car5, car10, car20, car60)
            lam = -np.log(max(car60, 0.0001) / peak) / 55 if peak > 0.0001 and car60 < peak else 0.03
        else:
            trough = min(car5, car10, car20, car60)
            lam = -np.log(abs(car60) / abs(trough)) / 55 if trough < -0.0001 and car60 > trough else 0.03
        
        lam = min(max(lam, 0.005), 0.5)
        print(f"\n  [全样本|{label}] n={len(grp):5d}  "
              f"CAR5={car5:+.5f} CAR10={car10:+.5f} CAR20={car20:+.5f} CAR60={car60:+.5f}  "
              f"λ={lam:.3f}")
    
    # ── 报告 ──
    print("\n生成报告...")
    lines = []
    lines.append("# 宏观周期 × 事件CAR 联合分析 v2\n")
    lines.append(f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    # A. 市场状态分布
    lines.append("## A. 市场状态分布（沪深300 60天动量为标准）\n")
    mkt_dist = events_with_mkt.groupby('mkt_label').agg(
        事件数=('event_id', 'count'),
        利好=('direction', lambda x: (x==1).sum()),
        利空=('direction', lambda x: (x==-1).sum())
    ).sort_values('事件数', ascending=False)
    mkt_dist['利好占比'] = (mkt_dist['利好'] / mkt_dist['事件数'] * 100).round(1)
    lines.append("| 市场状态 | 事件数 | 占比 | 利好 | 利空 | 利好占 |")
    lines.append("|---------|--------|------|------|------|--------|")
    for label, r in mkt_dist.iterrows():
        pct = r['事件数'] / mkt_dist['事件数'].sum() * 100
        lines.append(f"| {label} | {int(r['事件数'])} | {pct:.1f}% | {int(r['利好'])} | {int(r['利空'])} | {r['利好占比']}% |")
    lines.append("")
    
    # B. 各市场状态 × 方向 CAR
    lines.append("## B. 各市场状态下事件CAR\n")
    lines.append("| 市场状态 | 方向 | 事件数 | CAR[-5,+5] | CAR[-5,+10] | CAR[-5,+20] | CAR[-5,+60] | 建议λ |")
    lines.append("|---------|------|--------|-----------|------------|------------|------------|-------|")
    for (mkt_label, dir_name), v in sorted(calibrations.items()):
        lines.append(f"| {mkt_label} | {dir_name} | {v['n']} | {v['car5']:+.5f} | {v['car10']:+.5f} | {v['car20']:+.5f} | {v['car60']:+.5f} | {v['lambda']} |")
    lines.append("")
    
    # C. 各个周期内的CAR 分段分析
    lines.append("## C. 不同宏观状态 + 市场状态 组合分析\n")
    
    # 高M2增速 vs 低M2
    events_with_mkt['m2_high'] = events_with_mkt['CN_M2_YOY'] > events_with_mkt['CN_M2_YOY'].median()
    events_with_mkt['利率环境'] = np.where(events_with_mkt['CN_SHIBOR_1W'] > events_with_mkt['CN_SHIBOR_1W'].median(), '高利率', '低利率')
    events_with_mkt['信用环境'] = np.where(events_with_mkt['CN_AAA_SPREAD'] > events_with_mkt['CN_AAA_SPREAD'].median(), '高信用利差', '低信用利差')
    
    # 直接附加上下文到merged（避免列冲突）
    ctx_cols = events_with_mkt.set_index('event_id')[['mkt_label', 'm2_high', '利率环境', '信用环境']].rename(
        columns={'mkt_label': 'mkt_state'})
    merged_w_context = merged.join(ctx_cols, on='event_id', how='left')
    
    lines.append("### C1. M2高低 × 市场状态\n")
    lines.append("| M2水平 | 市场状态 | 方向 | 事件数 | CAR20 | CAR60 | 建议λ |")
    lines.append("|--------|---------|------|--------|-------|-------|-------|")
    for (m2_high, mkt_state, direction), grp in merged_w_context.groupby(['m2_high', 'mkt_state', 'direction']):
        if len(grp) < 15: continue
        car20 = grp['car_20'].mean()
        car60 = grp['car_60'].mean()
        dir_name = '利好' if direction == 1 else ('利空' if direction == -1 else '中性')
        m2_lab = '高M2' if m2_high else '低M2'
        
        if direction == 1:
            lam = -np.log(max(car60, 0.0001) / max(car20, 0.0001)) / 40 if car60 < car20 and car20 > 0.0001 else 0.03
        elif direction == -1:
            lam = -np.log(abs(car60) / abs(car20)) / 40 if car60 > car20 and car20 < -0.0001 else 0.03
        else:
            lam = 0.03
        lam = min(max(lam, 0.005), 0.5)
        lines.append(f"| {m2_lab} | {mkt_state} | {dir_name} | {len(grp)} | {car20:+.5f} | {car60:+.5f} | {lam:.3f} |")
    lines.append("")
    
    lines.append("### C2. 利率环境 × 市场状态\n")
    lines.append("| 利率 | 市场状态 | 方向 | 事件数 | CAR20 | CAR60 | 建议λ |")
    lines.append("|------|---------|------|--------|-------|-------|-------|")
    for (rate, mkt_state, direction), grp in merged_w_context.groupby(['利率环境', 'mkt_state', 'direction']):
        if len(grp) < 15: continue
        car20 = grp['car_20'].mean()
        car60 = grp['car_60'].mean()
        dir_name = '利好' if direction == 1 else ('利空' if direction == -1 else '中性')
        
        if direction == 1:
            lam = -np.log(max(car60, 0.0001) / max(car20, 0.0001)) / 40 if car60 < car20 and car20 > 0.0001 else 0.03
        elif direction == -1:
            lam = -np.log(abs(car60) / abs(car20)) / 40 if car60 > car20 and car20 < -0.0001 else 0.03
        else:
            lam = 0.03
        lam = min(max(lam, 0.005), 0.5)
        lines.append(f"| {rate} | {mkt_state} | {dir_name} | {len(grp)} | {car20:+.5f} | {car60:+.5f} | {lam:.3f} |")
    lines.append("")
    
    # D. 综合优化λ推荐
    lines.append("## D. 衰减模型优化推荐\n")
    lam_pos = np.mean([v['lambda'] for k, v in calibrations.items() if '利好' in k[1]])
    lam_neg = np.mean([v['lambda'] for k, v in calibrations.items() if '利空' in k[1]])
    lam_all = np.mean([v['lambda'] for v in calibrations.values()])
    
    lines.append(f"**基于{len(merged)}个事件-行业对的CAR时变特征**:\n")
    lines.append(f"| 维度 | 推荐λ | 说明 |")
    lines.append(f"|------|-------|------|")
    lines.append(f"| 利好事件 | {lam_pos:.3f} | 利好CAR峰值后衰减速率均值 |")
    lines.append(f"| 利空事件 | {lam_neg:.3f} | 利空CAR低谷后回复速率均值 |")
    lines.append(f"| 综合 | {lam_all:.3f} | 全样本均值 |")
    lines.append(f"| 当前设置 | 0.150 | v1手工设定 |")
    lines.append("")
    
    lam_final = (lam_pos + lam_neg) / 2
    lines.append(f"**推荐全局λ = {lam_final:.3f}**（利好{lam_pos:.3f} / 利空{lam_neg:.3f} 加权平均）\n")
    
    if lam_final < 0.10:
        lines.append("→ 信息衰减比预期慢，政策影响更持久。建议降低λ，用更长事件窗(60天)衡量效果。\n")
    elif lam_final > 0.20:
        lines.append("→ 信息衰减速度快，短期交易信号为主。建议聚焦[-5,+10]事件窗。\n")
    else:
        lines.append("→ 中等衰减速率，当前配置基本合理。可在不同市场状态下使用不同λ。\n")
    
    lines.append(f"**建议配置**:\n")
    lines.append(f"- 默认: λ={lam_final:.3f}\n")
    lines.append(f"- 牛市利好: 可略低(信息沉淀更久)\n")
    lines.append(f"- 熊市利好: 可略高(利好在弱市快速消耗)\n")
    lines.append("\n---\n")
    lines.append("*基于event_list + nlp_event_scores(v2, 229词) + event_car + macro_indicators + industry_daily*\n")
    
    # 保存
    out_path = OUT + f"{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        f.write('\n'.join(lines))
    
    print(f"\n报告保存: {out_path}")
    print(f"全局推荐λ: {lam_final:.3f}")
    print(f"  利好: {lam_pos:.3f}  利空: {lam_neg:.3f}")

if __name__ == '__main__':
    main()
