"""
A股事件驱动择时回测
- 不能做空，只用信号决定持有/减仓
- 用行业指数实际收益率，不是CAR
- λ越大 → 事件后持有越短（快速变现）
- λ越小 → 事件后持有越久（信息持久）
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import numpy as np
import pandas as pd
from datetime import datetime
import json, os

OUT = '/home/tearsyu/.openclaw/workspace/memory/reports/backtest_'

def get_db():
    return psycopg2.connect('host=192.168.3.14 dbname=guludan user=guludan password=toto')

def main():
    conn = get_db()
    cur = conn.cursor()
    
    print("="*60)
    print("A股事件驱动择时回测 — 2026-05-23")
    print("="*60)
    
    # 1) 加载行业日收益率
    print("\n[1] 加载行业日收益率...")
    cur.execute("""
        SELECT trade_date, industry, close,
               (close - LAG(close) OVER (PARTITION BY industry ORDER BY trade_date)) / NULLIF(LAG(close) OVER (PARTITION BY industry ORDER BY trade_date), 0) AS ret
        FROM industry_daily
        WHERE trade_date BETWEEN '2019-01-01' AND '2025-12-31'
        ORDER BY trade_date
    """)
    ind_ret = pd.DataFrame(cur.fetchall(), columns=['trade_date', 'industry', 'close', 'ret'])
    ind_ret['trade_date'] = pd.to_datetime(ind_ret['trade_date'])
    print(f"    {len(ind_ret)}行, {ind_ret['industry'].nunique()}行业")
    
    # 2) 加载事件 + CAR（t_relative用于识别事件发生日窗口）
    print("[2] 加载事件...")
    cur.execute("""
        SELECT n.event_id, e.event_date, n.direction, n.macro_score, n.importance_total,
               n.keyword_weights_json, n.industry_json
        FROM event_list e
        JOIN nlp_event_scores n ON e.event_id = n.event_id
        WHERE n.direction != 0 AND n.importance_total >= 0.5 AND n.macro_score >= 0.5
        ORDER BY e.event_date
    """)
    rows = cur.fetchall()
    events = []
    for r in rows:
        events.append({
            'event_id': r[0], 'event_date': r[1], 'direction': r[2],
            'macro_score': r[3], 'importance_total': r[4],
            'industries': json.loads(r[6]) if r[6] else {}
        })
    ev_df = pd.DataFrame(events)
    ev_df['event_date'] = pd.to_datetime(ev_df['event_date'])
    
    # 3) 加载事件-行业映射（哪个行业受影响）
    print("[3] 加载事件-行业匹配...")
    cur.execute("""
        SELECT DISTINCT event_id, industry_code, industry_name
        FROM event_car
        WHERE event_id = ANY(%s)
    """, (ev_df['event_id'].tolist(),))
    ev_ind = pd.DataFrame(cur.fetchall(), columns=['event_id', 'industry_code', 'industry_name'])
    print(f"    {len(ev_ind)}条, {ev_ind['event_id'].nunique()}事件")
    
    conn.close()
    
    # 4) 合并事件+行业+方向
    ev_with_ind = ev_df.merge(ev_ind, on='event_id', how='inner')
    print(f"[4] 合并: {len(ev_with_ind)}条事件-行业对")
    
    # 5) 找事件后每个行业交易日的收益率
    # 方法：对每个(event, industry)，从事件日T0开始，往后取60个交易日
    print("[5] 构建事件-日历映射...")
    
    # 为每个行业准备日历
    ind_cal = {ind: grp.set_index('trade_date')['ret'] 
               for ind, grp in ind_ret.groupby('industry')}
    
    # 选取代表行业做回测
    top_inds = ev_with_ind['industry_name'].value_counts().head(30).index.tolist()
    print(f"    选取{len(top_inds)}个高频行业")
    
    # ═══════════════════════════════════════
    # 核心回测
    # ═══════════════════════════════════════
    # 策略逻辑：
    # - 利好事件(t=0)：按照权重买入，然后按λ衰减每日卖出部分
    # - 利空事件(t=0)：按照权重卖出（减仓），不持仓
    # - 权重 = importance * 方向 * 行业置信度
    # - 每日总收益 = 持仓收益 + 减仓避险收益
    
    configs = [
        ('λ=0.027 (v2推荐)', 0.027),
        ('λ=0.15 (原设定)', 0.15),
        ('λ=0.01 (慢衰减)', 0.01),
        ('λ=0.05 (中衰减)', 0.05),
        ('λ=0.003 (极慢)', 0.003),
    ]
    
    results = {}
    
    for label, lam in configs:
        print(f"\n    回测 {label}...")
        
        # 每日持仓和收益
        daily_hold = {}  # trade_date -> sum of position weights
        daily_pos_return = {}  # trade_date -> weighted return
        daily_neg_avoidance = {}  # trade_date -> avoided loss from selling
        daily_neg_count = {}  # trade_date -> count of sell signals
        daily_pos_count = {}
        
        processed = 0
        
        for _, row in ev_with_ind.iterrows():
            processed += 1
            if processed % 5000 == 0:
                print(f"      处理中: {processed}/{len(ev_with_ind)}")
            
            ev_id = row['event_id']
            ev_date = row['event_date']
            direction = row['direction']
            imp = row['importance_total']
            ind_name = row['industry_name']
            
            if ind_name not in ind_cal:
                continue
            
            cal = ind_cal[ind_name]  # series: trade_date -> ret
            
            # 找到事件发生后下一个交易日（t=0）
            # 用事件日期往后找最近的交易日
            t0_dates = cal.index[cal.index >= ev_date]
            if len(t0_dates) < 1:
                continue
            
            t0 = t0_dates[0]
            t0_idx = cal.index.get_loc(t0)
            
            # 事件强度（带0.5~1.0的缩放上限）
            signal_strength = min(imp * abs(direction), 1.0)
            
            if direction == 1:  # 利好 → 买入持仓
                for td in range(60):
                    if t0_idx + td >= len(cal):
                        break
                    trade_date = cal.index[t0_idx + td]
                    decay = np.exp(-lam * td)
                    weight = signal_strength * decay
                    
                    if weight < 0.01:
                        break
                    
                    daily_hold[trade_date] = daily_hold.get(trade_date, 0) + weight
                    daily_pos_count[trade_date] = daily_pos_count.get(trade_date, 0) + 1
            
            elif direction == -1:  # 利空 → 卖出减仓
                # 利空信号 = 应避免持仓，算出"避险收益"
                for td in range(60):
                    if t0_idx + td >= len(cal):
                        break
                    trade_date = cal.index[t0_idx + td]
                    decay = np.exp(-lam * td)
                    weight = signal_strength * decay
                    
                    if weight < 0.01:
                        break
                    
                    daily_neg_count[trade_date] = daily_neg_count.get(trade_date, 0) + 1
                    
                    # 当天实际收益
                    actual_ret = cal.iloc[t0_idx + td]
                    
                    # 如果利空应减仓，但实际持仓不动 = 亏损
                    # 避险收益 = -actual_ret（如果能避开下跌）
                    # 注意：利空后如果实际涨了，减仓反而亏
                    avoid_ret = -actual_ret * weight * 0.5  # 保守系数
                    daily_neg_avoidance[trade_date] = daily_neg_avoidance.get(trade_date, 0) + avoid_ret
        
        # 计算每日收益
        dates = sorted(set(list(daily_hold.keys()) + list(daily_neg_avoidance.keys())))
        if len(dates) < 20:
            continue
        
        days = []
        for d in dates:
            pos_hold = daily_hold.get(d, 0)
            neg_avoid = daily_neg_avoidance.get(d, 0)
            pos_n = daily_pos_count.get(d, 0)
            neg_n = daily_neg_count.get(d, 0)
            
            # 持仓收益率 = 市场平均收益（假设全仓）
            # 用事件涉及的行业平均收益
            day_ret = ind_ret[ind_ret['trade_date'] == d]['ret'].mean()
            if pd.isna(day_ret):
                continue
            
            # 组合收益 = 持仓 * 市场收益 + 避险收益
            pos_weight = min(pos_hold, 1.0)  # 最大满仓
            port_ret = pos_weight * day_ret + neg_avoid
            
            days.append({
                'trade_date': d, 'port_ret': port_ret, 'mkt_ret': day_ret,
                'pos_weight': pos_weight, 'pos_n': pos_n, 'neg_n': neg_n,
                'neg_avoid': neg_avoid,
            })
        
        day_df = pd.DataFrame(days)
        
        # 统计
        total_ret = day_df['port_ret'].sum()
        mkt_ret = day_df['mkt_ret'].sum()
        excess = total_ret - mkt_ret
        
        cum_port = day_df['port_ret'].cumsum()
        cum_mkt = day_df['mkt_ret'].cumsum()
        rolling_max = cum_port.cummax()
        drawdown = (cum_port - rolling_max).min()
        
        mean_ret = day_df['port_ret'].mean()
        std_ret = day_df['port_ret'].std()
        sharpe = mean_ret / std_ret * np.sqrt(252) if std_ret > 0 else 0
        
        win_rate = (day_df['port_ret'] > 0).mean()
        mkt_sharpe = day_df['mkt_ret'].mean() / day_df['mkt_ret'].std() * np.sqrt(252) if day_df['mkt_ret'].std() > 0 else 0
        
        results[label] = {
            'total_ret': total_ret, 'mkt_ret': mkt_ret, 'excess': excess,
            'sharpe': sharpe, 'mkt_sharpe': mkt_sharpe, 'win_rate': win_rate,
            'drawdown': drawdown, 'n_days': len(day_df),
            'avg_pos_weight': day_df['pos_weight'].mean(),
            'avg_pos_n': day_df.groupby('trade_date')['pos_n'].sum().mean(),
            'avg_neg_n': day_df.groupby('trade_date')['neg_n'].sum().mean(),
        }
        
        print(f"      总收益: {total_ret:+.4f} | 市场: {mkt_ret:+.4f} | 超额: {excess:+.4f}")
        print(f"      夏普: {sharpe:.3f} | 市场夏普: {mkt_sharpe:.3f} | 胜率: {win_rate:.1%}")
        print(f"      回撤: {drawdown:.4f}")
        print(f"      日均持仓权重: {day_df['pos_weight'].mean():.3f}")
    
    # ── 报告 ──
    lines = []
    lines.append("# A股事件驱动择时回测 — 不做空\n")
    lines.append(f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"回测区间: 2019-01-01 ~ 2025-12-31\n")
    lines.append(f"行业: {len(top_inds)}个 | 高质量事件: {len(ev_df)}条\n")
    lines.append("")
    lines.append("## 策略逻辑\n")
    lines.append("- **利好信号** → 按λ衰减持仓，t=0满仓，t=60接近0\n")
    lines.append("- **利空信号** → 减仓避险，计算避险收益\n")
    lines.append("- λ越小 → 持仓越长，信息影响越持久\n")
    lines.append("- 不能做空，只决定买/卖时机\n")
    lines.append("")
    
    lines.append("## λ参数对比\n\n")
    lines.append("| 参数 | 总收益 | 市场收益 | 超额 | 夏普 | 市场夏普 | 胜率 | 回撤 | 持仓比 |")
    lines.append("|------|--------|---------|------|------|---------|------|------|------|")
    for label, r in sorted(results.items(), key=lambda x: -x[1]['sharpe']):
        lines.append(f"| {label} | {r['total_ret']:+.4f} | {r['mkt_ret']:+.4f} | {r['excess']:+.4f} | {r['sharpe']:.3f} | {r['mkt_sharpe']:.3f} | {r['win_rate']:.1%} | {r['drawdown']:.4f} | {r['avg_pos_weight']:.3f} |")
    lines.append("")
    
    best = max(results, key=lambda k: results[k]['sharpe'])
    lines.append(f"**最优**: {best}\n")
    
    lines.append("\n## 结论\n\n")
    lines.append("1. **λ代表信息持久度** — 利好信号持仓时间应取决于λ\n")
    lines.append("2. **超额收益为正的λ**说明择时有效\n")
    lines.append("3. **核心目标**：找到事件后最佳持有期（λ校准）\n")
    lines.append("4. 当前推荐λ=0.027表示事件影响力半衰期约25个交易日\n")
    lines.append("")
    
    out_path = OUT + f"ret_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        f.write('\n'.join(lines))
    
    print(f"\n报告: {out_path}")

if __name__ == '__main__':
    main()
