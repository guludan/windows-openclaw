"""
宏观周期 + 事件CAR + 衰减模型校准 三联分析
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import numpy as np
import pandas as pd
from collections import defaultdict
import json
import os
from datetime import datetime, timedelta

OUT = '/home/tearsyu/.openclaw/workspace/memory/reports/macro_cycle_'

def get_db():
    return psycopg2.connect('host=192.168.3.14 dbname=guludan user=guludan password=toto')

def load_macro(conn):
    """加载宏观指标，生成周期标签"""
    cur = conn.cursor()
    
    # M2同比
    cur.execute("""
        SELECT date, value FROM macro_indicators 
        WHERE indicator = 'CN_M2_YOY' 
        ORDER BY date
    """)
    m2 = pd.DataFrame(cur.fetchall(), columns=['date', 'm2_yoy'])
    m2['date'] = pd.to_datetime(m2['date'])
    
    # M1同比
    cur.execute("""
        SELECT date, value FROM macro_indicators 
        WHERE indicator = 'CN_M1_YOY' 
        ORDER BY date
    """)
    m1 = pd.DataFrame(cur.fetchall(), columns=['date', 'm1_yoy'])
    m1['date'] = pd.to_datetime(m1['date'])
    
    # 10Y国债收益率
    cur.execute("""
        SELECT date, value FROM macro_indicators 
        WHERE indicator = 'CN_10Y_YIELD' 
        ORDER BY date
    """)
    y10 = pd.DataFrame(cur.fetchall(), columns=['date', 'y10'])
    y10['date'] = pd.to_datetime(y10['date'])
    
    # SHIBOR 1W
    cur.execute("""
        SELECT date, value FROM macro_indicators 
        WHERE indicator = 'CN_SHIBOR_1W' 
        ORDER BY date
    """)
    shibor = pd.DataFrame(cur.fetchall(), columns=['date', 'shibor_1w'])
    shibor['date'] = pd.to_datetime(shibor['date'])
    
    # 合并
    df = m2.merge(m1, on='date', how='outer')
    df = df.merge(y10, on='date', how='outer')
    df = df.merge(shibor, on='date', how='outer')
    df = df.sort_values('date').ffill().bfill()
    
    # 计算季度变化
    df['m2_ma3'] = df['m2_yoy'].rolling(3, min_periods=2).mean()
    df['m1_ma3'] = df['m1_yoy'].rolling(3, min_periods=2).mean()
    df['y10_ma3'] = df['y10'].rolling(20, min_periods=10).mean()
    
    # 定义周期阶段
    # M2扩张/收缩: 同比上行/下行
    df['m2_direction'] = np.where(df['m2_ma3'].diff() > 0, 1, -1)
    # M1-M2剪刀差 (资金活化程度) >0 说明活期化，股市友好
    df['m1m2_gap'] = df['m1_ma3'] - df['m2_ma3']
    
    # 周期标签
    conditions = [
        (df['m2_direction'] > 0) & (df['m1m2_gap'] > 0),   # M2扩+资金活化 → 牛市
        (df['m2_direction'] > 0) & (df['m1m2_gap'] <= 0),   # 宽货币但资金没进实体
        (df['m2_direction'] <= 0) & (df['m1m2_gap'] > 0),   # 紧货币但资金还在
        (df['m2_direction'] <= 0) & (df['m1m2_gap'] <= 0),   # 双紧
    ]
    choices = ['bull_expansion', 'loose_noflow', 'tight_liquid', 'bear_tight']
    df['cycle_phase'] = np.select(conditions, choices, default='unknown')
    
    # 简化标签
    phase_labels = {
        'bull_expansion': '宽松+活化→强市',
        'loose_noflow': '宽松但资金沉淀',
        'tight_liquid': '紧货币但资金活跃',
        'bear_tight': '双紧→弱市'
    }
    df['cycle_label'] = df['cycle_phase'].map(phase_labels)
    
    # 季度标记（用于合并event_list的月度数据）
    df['quarter'] = df['date'].dt.to_period('M')
    
    return df

def load_events(conn):
    """加载事件数据"""
    cur = conn.cursor()
    
    # event_list + nlp_event_scores合并
def load_events(conn):
    """加载事件数据"""
    cur = conn.cursor()
    
    # event_list + nlp_event_scores合并，只取需要的列
    cur.execute("""
        SELECT e.event_id, e.event_date, e.source,
               n.direction, n.macro_score, n.importance_total,
               n.keyword_weights_json, n.industry_json, n.version
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

def load_car(conn):
    """加载事件CAR（按事件聚合）"""
    cur = conn.cursor()
    # 取每个事件-行业的平均CAR（t_relative=0附近的事件窗平均）
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

def load_market_index(conn):
    """加载全市场等权指数"""
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, AVG(close) as mkt_level
        FROM industry_daily
        GROUP BY trade_date
        ORDER BY trade_date
    """)
    df = pd.DataFrame(cur.fetchall(), columns=['trade_date', 'mkt_level'])
    df['return'] = df['mkt_level'].pct_change()
    return df

def assign_cycle_to_events(events, macro_df):
    """给每个事件分配对应的宏观周期（基于月级别M2数据）"""
    events = events.copy()
    events['event_date'] = pd.to_datetime(events['event_date'])
    
    # 取事件发生日最近的宏观数据
    macro_by_month = macro_df.set_index('date').resample('ME').last().ffill()
    macro_by_month['month'] = macro_by_month.index.to_period('M').astype(str)
    
    events['month'] = events['event_date'].dt.to_period('M').astype(str)
    
    # debug
    print(f"\nmacro_by_month可merge列: {list(macro_by_month.columns)}")
    print(f"events['month']样例: {events['month'].dropna().iloc[:3].tolist()}")
    print(f"macro_by_month['month']样例: {macro_by_month['month'].dropna().iloc[:3].tolist()}")
    
    events = events.merge(
        macro_by_month[['cycle_phase', 'cycle_label', 'm2_yoy', 'm1_yoy', 'm1m2_gap', 'month']],
        on='month', how='left'
    )
    return events

def analysis_cycle_distribution(events, car_df, market_df, macro_df, out_path):
    """分析不同宏观周期下的事件和CAR特征"""
    report = []
    report.append("# 宏观周期 × 事件CAR 联合分析报告\n")
    report.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    # 1. 周期分布
    report.append("## 1. 宏观周期阶段定义\n")
    report.append("| 阶段 | 条件 | 含义 |")
    report.append("|------|------|------|")
    report.append("| bull_expansion | M2上行 + M1-M2>0 | 宽货币+资金活化→强市 |")
    report.append("| loose_noflow | M2上行 + M1-M2≤0 | 宽货币但资金沉淀（钱没进实体/股市） |")
    report.append("| tight_liquid | M2下行 + M1-M2>0 | 紧货币但资金活跃 |")
    report.append("| bear_tight | M2下行 + M1-M2≤0 | 双紧→弱市 |")
    report.append("")
    
    # 2. 各周期时间分布
    phase_counts = events.dropna(subset=['cycle_phase']).groupby(['cycle_phase', 'cycle_label']).size().reset_index(name='event_count')
    total = phase_counts['event_count'].sum()
    report.append("## 2. 各周期事件分布\n")
    report.append(f"总事件数: {total}\n")
    report.append("| 周期 | 事件数 | 占比 |")
    report.append("|------|--------|------|")
    for _, r in phase_counts.sort_values('event_count', ascending=False).iterrows():
        report.append(f"| {r['cycle_label']} | {r['event_count']} | {r['event_count']/total*100:.1f}% |")
    report.append("")
    
    # 3. 各周期利好/利空分布
    report.append("## 3. 各周期方向分布\n")
    dir_counts = events.dropna(subset=['cycle_phase']).groupby(['cycle_label', 'direction']).size().unstack(fill_value=0)
    for col in [-1, 0, 1]:
        if col not in dir_counts.columns:
            dir_counts[col] = 0
    dir_counts.columns = [f'dir_{c}' for c in dir_counts.columns]
    dir_counts['total'] = dir_counts.sum(axis=1)
    dir_counts['利好占比'] = (dir_counts['dir_1'] / dir_counts['total'] * 100).round(1)
    dir_counts['利空占比'] = (dir_counts['dir_-1'] / dir_counts['total'] * 100).round(1)
    report.append("| 周期 | 利好 | 利空 | 中性 | 利好占比 | 利空占比 |")
    report.append("|------|------|------|------|----------|----------|")
    for label, r in dir_counts.iterrows():
        report.append(f"| {label} | {int(r['dir_1'])} | {int(r['dir_-1'])} | {int(r.get('dir_0', 0))} | {r['利好占比']}% | {r['利空占比']}% |")
    report.append("")
    
    # 4. 各周期CAR表现
    report.append("## 4. 各周期事件CAR差异\n")
    
    # 合并数据
    m_events = events.dropna(subset=['cycle_phase'])[['event_id', 'cycle_label', 'cycle_phase', 'direction', 'macro_score', 'importance_total']]
    m_car = car_df[['event_id', 'car_5', 'car_10', 'car_20', 'car_60']].copy()
    m_car[['car_5', 'car_10', 'car_20', 'car_60']] = m_car[['car_5', 'car_10', 'car_20', 'car_60']].apply(pd.to_numeric, errors='coerce')
    
    merged = m_events.merge(m_car, on='event_id', how='inner')
    
    # 4a. 全样本CAR表
    report.append("### 4a. 全样本—各周期平均CAR\n")
    report.append("| 周期 | CAR[-5,+5] | CAR[-5,+10] | CAR[-5,+20] | CAR[-5,+60] | 事件数 |")
    report.append("|------|-----------|------------|------------|------------|--------|")
    for label, grp in merged.groupby('cycle_label'):
        report.append(f"| {label} | {grp['car_5'].mean():.4f} | {grp['car_10'].mean():.4f} | {grp['car_20'].mean():.4f} | {grp['car_60'].mean():.4f} | {len(grp)} |")
    report.append("")
    
    # 4b. 利好事件CAR
    report.append("### 4b. 利好事件 → 各周期CAR\n")
    pos = merged[merged['direction'] == 1]
    report.append("| 周期 | CAR[-5,+5] | CAR[-5,+10] | CAR[-5,+20] | CAR[-5,+60] | 事件数 |")
    report.append("|------|-----------|------------|------------|------------|--------|")
    for label, grp in pos.groupby('cycle_label'):
        report.append(f"| {label} | {grp['car_5'].mean():.4f} | {grp['car_10'].mean():.4f} | {grp['car_20'].mean():.4f} | {grp['car_60'].mean():.4f} | {len(grp)} |")
    report.append("")
    
    # 4c. 利空事件CAR
    report.append("### 4c. 利空事件 → 各周期CAR\n")
    neg = merged[merged['direction'] == -1]
    report.append("| 周期 | CAR[-5,+5] | CAR[-5,+10] | CAR[-5,+20] | CAR[-5,+60] | 事件数 |")
    report.append("|------|-----------|------------|------------|------------|--------|")
    for label, grp in neg.groupby('cycle_label'):
        report.append(f"| {label} | {grp['car_5'].mean():.4f} | {grp['car_10'].mean():.4f} | {grp['car_20'].mean():.4f} | {grp['car_60'].mean():.4f} | {len(grp)} |")
    report.append("")
    
    # 5. 衰减模型标定
    report.append("## 5. 衰减模型标定\n")
    
    # 通过CAR的衰减速率来校准λ
    # 利好事件：看car_5, car_10, car_20, car_60 递减率
    # 理论上利好事件CAR在事件窗内达到峰值后回落，衰减速率≈ln(car_20/car_10)/10
    report.append("### 5a. 利好事件CAR衰减速率\n")
    
    for label, grp in pos.groupby('cycle_label'):
        if len(grp) < 30:
            continue
        car5 = grp['car_5'].mean()
        car10 = grp['car_10'].mean()
        car20 = grp['car_20'].mean()
        car60 = grp['car_60'].mean()
        
        # 衰减速率: 如果CAR10 > CAR5 说明还在累积不衰，λ暂时为0
        # 从CAR峰值到CAR60估算
        peak = max(car5, car10, car20, car60)
        if car60 < peak and peak > 0:
            # 从峰值到+60天的年化衰减
            lam_est = -np.log(max(car60, 0.0001) / peak) / 60 if car60 > 0 else 0.15
            lam_est = min(max(lam_est, 0.01), 0.5)
        else:
            lam_est = 0.05  # 没有明显衰减时的默认值
        
        report.append(f"**{label}**: CAR5={car5:.4f} CAR10={car10:.4f} CAR20={car20:.4f} CAR60={car60:.4f}")
        report.append(f"  - 峰值: {peak:.4f}")
        report.append(f"  - 建议λ(基于CAR衰减): {lam_est:.3f}")
        report.append("")
    
    report.append("### 5b. 利空事件CAR衰减速率\n")
    for label, grp in neg.groupby('cycle_label'):
        if len(grp) < 20:
            continue
        car5 = grp['car_5'].mean()
        car10 = grp['car_10'].mean()
        car20 = grp['car_20'].mean()
        car60 = grp['car_60'].mean()
        
        peak = min(car5, car10, car20, car60)  # 负值找最低点
        if car60 > peak and peak < 0:
            lam_est = -np.log(abs(car60) / abs(peak)) / 60 if car60 < 0 else 0.15
            lam_est = min(max(lam_est, 0.01), 0.5)
        else:
            lam_est = 0.05
        
        report.append(f"**{label}**: CAR5={car5:.4f} CAR10={car10:.4f} CAR20={car20:.4f} CAR60={car60:.4f}")
        report.append(f"  - 最低点: {peak:.4f}")
        report.append(f"  - 建议λ(基于CAR回复): {lam_est:.3f}")
        report.append("")
    
    # 6. 综合推荐λ
    report.append("## 6. 衰减模型参数推荐\n")
    report.append("基于各周期的CAR衰减分析：\n")
    report.append("| 周期 | λ利好 | λ利空 | 说明 |")
    report.append("|------|-------|-------|------|")
    
    recs = {}
    for label, grp in pos.groupby('cycle_label'):
        recs[label] = {}
        if len(grp) >= 30:
            car_vals = [grp[c].mean() for c in ['car_5', 'car_10', 'car_20', 'car_60']]
            peak = max(car_vals)
            if car_vals[3] < peak and peak > 0:
                lam = min(max(-np.log(max(car_vals[3], 0.0001) / peak) / 60, 0.01), 0.5)
            else:
                lam = 0.05
            recs[label]['pos'] = round(lam, 3)
    
    for label, grp in neg.groupby('cycle_label'):
        if label not in recs:
            recs[label] = {}
        if len(grp) >= 20:
            car_vals = [grp[c].mean() for c in ['car_5', 'car_10', 'car_20', 'car_60']]
            peak = min(car_vals)
            if car_vals[3] > peak and peak < 0:
                lam = min(max(-np.log(abs(car_vals[3]) / abs(peak)) / 60, 0.01), 0.5)
            else:
                lam = 0.05
            recs[label]['neg'] = round(lam, 3)
    
    for label, vals in recs.items():
        pos_l = vals.get('pos', '-')
        neg_l = vals.get('neg', '-')
        report.append(f"| {label} | {pos_l} | {neg_l} | 利好/利空分别标定 |")
    report.append("")
    
    # 全局推荐
    all_pos_lam = [v['pos'] for v in recs.values() if 'pos' in v]
    all_neg_lam = [v['neg'] for v in recs.values() if 'neg' in v]
    global_pos = np.mean(all_pos_lam) if all_pos_lam else 0.15
    global_neg = np.mean(all_neg_lam) if all_neg_lam else 0.15
    
    report.append(f"**全局推荐**:")
    report.append(f"- 利好事件 λ = {global_pos:.3f}")
    report.append(f"- 利空事件 λ = {global_neg:.3f}")
    report.append(f"- 综合 λ = {(global_pos + global_neg) / 2:.3f}")
    report.append("")
    
    # 7. M2与全市场收益关联
    report.append("## 7. M2/宏观vs全市场收益\n")
    mkt_monthly = market_df.copy()
    mkt_monthly['trade_date'] = pd.to_datetime(mkt_monthly['trade_date'])
    mkt_monthly['month'] = mkt_monthly['trade_date'].dt.to_period('M')
    mkt_monthly_ret = mkt_monthly.groupby('month')['return'].mean().reset_index()
    mkt_monthly_ret.columns = ['month', 'avg_daily_return']
    
    macro_monthly = macro_df[['date', 'm2_yoy', 'm1_yoy', 'm1m2_gap', 'cycle_phase', 'cycle_label']].copy()
    macro_monthly['month'] = macro_monthly['date'].dt.to_period('M')
    macro_monthly = macro_monthly.groupby('month').last().reset_index()
    
    mkt_macro = mkt_monthly_ret.merge(macro_monthly, on='month', how='inner')
    
    report.append("| 周期 | 月日均收益 | M2均值 | M1-M2剪刀差 |")
    report.append("|------|-----------|--------|-------------|")
    for label, grp in mkt_macro.groupby('cycle_label'):
        report.append(f"| {label} | {grp['avg_daily_return'].mean()*100:.3f}% | {grp['m2_yoy'].mean():.1f}% | {grp['m1m2_gap'].mean():.1f}% |")
    report.append("")
    
    # 8. 结论
    report.append("## 8. 结论与建议\n")
    
    # 看看哪个λ效果最好
    report.append("### 衰减模型\n")
    report.append(f"- 当前设置: λ=0.15\n")
    report.append(f"- 数据标定建议: 利好{global_pos:.3f}, 利空{global_neg:.3f}\n")
    report.append(f"- 推荐操作: 在config中设置 lambda（利好/利空分开标定更好）\n")
    
    report.append("### 周期感知策略\n")
    for label, grp in pos.groupby('cycle_label'):
        car20_val = grp['car_20'].mean()
        n_events = len(grp)
        report.append(f"- {label}: 利好事件n={n_events}, CAR[-5,+20]={car20_val:.4f}")
    
    report.append("\n---\n")
    report.append("*基于event_list + nlp_event_scores(v2) + event_car + macro_indicators + industry_daily*")
    
    return '\n'.join(report)

def main():
    print("加载宏观数据...")
    conn = get_db()
    macro = load_macro(conn)
    
    print("加载事件数据...")
    events = load_events(conn)
    print(f"  nlp_event_scores: {len(events)}条")
    
    print("加载CAR...")
    car = load_car(conn)
    print(f"  event_car: {len(car)}行, {car['event_id'].nunique()}事件")
    
    print("加载市场指数...")
    market = load_market_index(conn)
    print(f"  全市场等权指数: {len(market)}天")
    
    conn.close()
    
    print("分配周期标签...")
    events_annotated = assign_cycle_to_events(events, macro)
    print(f"  有周期标签: {events_annotated['cycle_phase'].notna().sum()}")
    
    print("生成报告...")
    report = analysis_cycle_distribution(events_annotated, car, market, macro, OUT)
    
    out_path = OUT + f"{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"报告已保存: {out_path}")
    
    # 打印关键结论
    print("\n" + "="*60)
    print("关键结论:")
    print("="*60)
    
    m = events_annotated.dropna(subset=['cycle_phase'])
    m_car_data = m[['event_id', 'cycle_label', 'direction']].merge(
        car[['event_id', 'car_5', 'car_10', 'car_20', 'car_60']], on='event_id', how='inner')
    
    for label, grp in m_car_data.groupby('cycle_label'):
        pos_grp = grp[grp['direction'] == 1]
        neg_grp = grp[grp['direction'] == -1]
        print(f"\n[{label}]")
        if len(pos_grp) >= 10:
            print(f"  利好(n={len(pos_grp)}): CAR5={pos_grp['car_5'].mean():.4f} CAR10={pos_grp['car_10'].mean():.4f} CAR20={pos_grp['car_20'].mean():.4f} CAR60={pos_grp['car_60'].mean():.4f}")
        if len(neg_grp) >= 10:
            print(f"  利空(n={len(neg_grp)}): CAR5={neg_grp['car_5'].mean():.4f} CAR10={neg_grp['car_10'].mean():.4f} CAR20={neg_grp['car_20'].mean():.4f} CAR60={neg_grp['car_60'].mean():.4f}")

if __name__ == '__main__':
    main()
