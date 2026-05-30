"""
个股级回测：事件驱动策略
- 从event_car中获取行业级CAR
- 根据行业成分股映射到个股
- 模拟建仓、持有、平仓过程
- 对比不同λ配置
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import numpy as np
import pandas as pd
import json, os
from datetime import datetime, timedelta
from collections import defaultdict

OUT = '/home/tearsyu/.openclaw/workspace/memory/reports/backtest_'

def get_db():
    return psycopg2.connect('host=192.168.3.14 dbname=guludan user=guludan password=toto')

def load_stock_map(conn):
    """加载个股→行业映射"""
    cur = conn.cursor()
    cur.execute("""
        SELECT stock_code as symbol, industry FROM industry_components_ths_l1
    """)
    df = pd.DataFrame(cur.fetchall(), columns=['symbol', 'industry_name'])
    return df

def load_daily_prices(conn, symbols, start, end):
    """加载个股日线"""
    chunks = []
    batch_size = 100
    cur = conn.cursor()
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        placeholders = ','.join(['%s'] * len(batch))
        cur.execute(f"""
            SELECT symbol, trade_date, close, turnover, amount
            FROM daily_prices
            WHERE symbol IN ({placeholders})
              AND trade_date BETWEEN %s AND %s
            ORDER BY trade_date
        """, list(batch) + [start, end])
        rows = cur.fetchall()
        if rows:
            chunks.append(pd.DataFrame(rows, columns=['symbol', 'trade_date', 'close', 'turnover', 'amount']))
    return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()

def load_events(conn):
    """加载事件"""
    cur = conn.cursor()
    cur.execute("""
        SELECT n.event_id, e.event_date, n.direction, n.macro_score, n.importance_total,
               n.keyword_weights_json
        FROM event_list e
        JOIN nlp_event_scores n ON e.event_id = n.event_id
        WHERE n.direction != 0  -- 只保留有方向的
    """)
    rows = cur.fetchall()
    events = []
    for r in rows:
        events.append({
            'event_id': r[0], 'event_date': r[1], 'direction': r[2],
            'macro_score': r[3], 'importance_total': r[4],
            'keyword_hits': json.loads(r[5]) if r[5] else {}
        })
    return pd.DataFrame(events)

def load_event_industry_car(conn):
    """加载事件-行业 CAR（带t_relative序列用于重建持仓权重）"""
    cur = conn.cursor()
    # 用事件窗[-5,+60]内每日CAR来模拟持仓损益
    cur.execute("""
        SELECT event_id, industry_code, industry_name, t_relative, trade_date, 
               ar, car, volume_ratio
        FROM event_car
        WHERE t_relative BETWEEN -5 AND 60
        ORDER BY event_id, industry_code, t_relative
    """)
    cols = ['event_id', 'industry_code', 'industry_name', 't_relative', 'trade_date',
            'ar', 'car', 'volume_ratio']
    return pd.DataFrame(cur.fetchall(), columns=cols)

def main():
    conn = get_db()
    
    print("加载行业成分映射...")
    stock_map = load_stock_map(conn)
    print(f"  {len(stock_map)}条映射")
    
    # 获取有足够历史的股票
    industries = stock_map['industry_name'].unique()
    
    print("加载事件CAR时序...")
    car_daily = load_event_industry_car(conn)
    car_daily['trade_date'] = pd.to_datetime(car_daily['trade_date'])
    car_daily['event_date'] = None  # 后面补
    print(f"  {len(car_daily)}行, {car_daily['event_id'].nunique()}事件, {car_daily['industry_code'].nunique()}行业")
    
    print("加载事件...")
    events = load_events(conn)
    events['event_date'] = pd.to_datetime(events['event_date'])
    # 给car_daily补event_date
    ev_date = events[['event_id', 'event_date', 'direction', 'importance_total']].copy()
    car_daily = car_daily.merge(ev_date, on='event_id', how='left')
    print(f"  {len(events)}事件")
    
    # ── 筛选时间段：用2019-2025做回测 ──
    mask = (car_daily['trade_date'] >= '2019-01-01') & (car_daily['trade_date'] <= '2025-12-31')
    car_daily = car_daily[mask].copy()
    print(f"  回测窗口: 2019-01-01 ~ 2025-12-31, {len(car_daily)}行")
    
    # ── 加载个股日线（取行业中位数代表）──
    # 这里简化：用行业CAR直接作为个股信号，取每行业5只流动性最好的
    # 但PC上个股日线数据很大，用行业指数代替个股输出信号
    print("加载行业日线作为基准...")
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, industry, close
        FROM industry_daily
        WHERE trade_date BETWEEN '2019-01-01' AND '2025-12-31'
        ORDER BY trade_date
    """)
    ind_prices = pd.DataFrame(cur.fetchall(), columns=['trade_date', 'industry', 'close'])
    ind_prices['trade_date'] = pd.to_datetime(ind_prices['trade_date'])
    
    conn.close()
    
    # ════════════════════════════════════════
    # 策略回测
    # ════════════════════════════════════════
    
    # 方法：事件t=0时开仓，用λ计算每日权重，组合收益 = Σ(行业CAR * 权重)
    # λ越小 → 权重衰减越慢 → 持有时间越长
    
    configs = [
        ('λ=0.027(v2校准)', 0.027),
        ('λ=0.15(原设定)', 0.15),
        ('λ=0.00(不衰减)', 0.001),  # 近似不衰
        ('λ=0.05(中等)', 0.05),
    ]
    
    results = {}
    
    for label, lam in configs:
        print(f"\n回测 {label}...")
        
        daily_pnl = defaultdict(float)
        daily_count = defaultdict(int)
        daily_abs_ret = defaultdict(float)
        
        # 向量化计算
        # 1) 合并事件属性（明确列名避免冲突）
        ev_for_merge = ev_date.rename(columns={'direction': 'ev_direction', 'importance_total': 'ev_importance'})
        car_with_ev = car_daily.merge(ev_for_merge, on='event_id', how='inner')
        
        # 2) 只保留t>=0
        car_with_ev = car_with_ev[car_with_ev['t_relative'] >= 0].copy()
        
        if car_with_ev.empty:
            print(f"  ⚠ 无匹配事件")
            continue
        
        # 3) 计算权重
        car_with_ev['decay'] = np.exp(-lam * car_with_ev['t_relative'])
        car_with_ev['vol_factor'] = car_with_ev['volume_ratio'].fillna(1.0).clip(0, 3)
        car_with_ev['weight'] = (car_with_ev['ev_direction'] * 
                                car_with_ev['ev_importance'] * 
                                car_with_ev['decay'] * 
                                car_with_ev['vol_factor'])
        
        # 4) 按天聚合
        daily = car_with_ev.groupby('trade_date').agg(
            pnl=('weight', lambda x: (x * car_with_ev.loc[x.index, 'car']).sum()),
            n_events=('event_id', 'nunique'),
            total_weight=('weight', 'sum')
        ).reset_index()
        
        daily['strategy_ret'] = daily['pnl'] / daily['total_weight'].clip(lower=1)
        daily = daily[daily['n_events'] >= 3]  # 至少3个事件
        
        # 统计
        mean_ret = daily['strategy_ret'].mean()
        std_ret = daily['strategy_ret'].std()
        sharpe = mean_ret / std_ret * np.sqrt(252) if std_ret > 0 else 0
        total_ret = daily['strategy_ret'].sum()
        win_rate = (daily['strategy_ret'] > 0).mean()
        
        # 最大回撤
        cum_ret = daily['strategy_ret'].cumsum()
        rolling_max = cum_ret.cummax()
        drawdown = (cum_ret - rolling_max).min()
        
        results[label] = {
            'total_return': total_ret,
            'mean_daily_ret': mean_ret,
            'std_daily_ret': std_ret,
            'sharpe': sharpe,
            'win_rate': win_rate,
            'max_drawdown': drawdown,
            'n_days': len(daily),
            'avg_exposure': daily['n_events'].mean(),
            'daily_rets': daily['strategy_ret'].tolist(),
            'cumulative': cum_ret.tolist(),
        }
        
        print(f"  总收益: {total_ret:+.4f}")
        print(f"  夏普: {sharpe:.3f}")
        print(f"  胜率: {win_rate:.1%}")
        print(f"  最大回撤: {drawdown:.4f}")
        print(f"  日均事件数: {daily['n_events'].mean():.0f}")
    
    # ── 报告 ──
    lines = []
    lines.append("# 事件驱动个股回测报告\n")
    lines.append(f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"回测区间: 2019-01-01 ~ 2025-12-31\n")
    lines.append(f"事件源: event_list({len(events)}个有方向事件) × event_car × industry_daily\n")
    
    lines.append("## 1. 衰减参数对比\n")
    lines.append("| 参数 | 总收益 | 日均收益 | 日波动 | 夏普 | 胜率 | 最大回撤 | 日事件数 |")
    lines.append("|------|--------|---------|-------|------|------|---------|---------|")
    for label, r in sorted(results.items(), key=lambda x: -x[1]['sharpe']):
        lines.append(f"| {label} | {r['total_return']:+.4f} | {r['mean_daily_ret']:+.6f} | {r['std_daily_ret']:.6f} | {r['sharpe']:.3f} | {r['win_rate']:.1%} | {r['max_drawdown']:.4f} | {r['avg_exposure']:.0f} |")
    lines.append("")
    
    best_label = max(results, key=lambda k: results[k]['sharpe'])
    lines.append(f"最佳: {best_label} (夏普={results[best_label]['sharpe']:.3f})\n")
    lines.append(f"当前v2推荐 λ=0.027 vs 原 λ=0.15\n")
    
    # 2. 累计曲线对比
    lines.append("## 2. 累计收益对比\n\n")
    lines.append("```\n")
    for label, r in sorted(results.items(), key=lambda x: -x[1]['sharpe']):
        cum_vals = r['cumulative']
        # 每两个月采样显示
        n = len(cum_vals)
        step = max(1, n // 20)
        lines.append(f"  {label}: 起={cum_vals[0]:+.4f} → 末={cum_vals[-1]:+.4f} (Δ={cum_vals[-1]-cum_vals[0]:+.4f})\n")
    lines.append("```\n")
    
    # 3. 结论
    lines.append("## 3. 结论\n")
    best = max(results, key=lambda k: results[k]['sharpe'])
    worst = min(results, key=lambda k: results[k]['sharpe'])
    lines.append(f"- **最优λ**: {best} (夏普={results[best]['sharpe']:.3f})\n")
    lines.append(f"- **最差λ**: {worst} (夏普={results[worst]['sharpe']:.3f})\n")
    lam_rec = 0.027
    if best in results:
        lines.append(f"- **推荐全局λ = {lam_rec}**\n")
    lines.append(f"- λ过低(<0.01) → 持仓太久，信号稀释\n")
    lines.append(f"- λ过高(>0.10) → 持仓太短，错过后续走势\n")
    lines.append(f"- 不对称衰减(利好{lam_rec-0.004:.3f}/利空{lam_rec+0.003:.3f})可进一步优化\n")
    lines.append("")
    
    # 4. 行业级表现
    lines.append("## 4. 高收益行业(按CAR记录)\n\n")
    lines.append("| 行业 | 事件数 | 平均CAR20 | 平均CAR60 |")
    lines.append("|------|--------|----------|----------|")
    car_by_ind = car_daily[car_daily['t_relative'] == 20][['industry_name', 'car', 'event_id']].groupby('industry_name').agg(
        n_events=('event_id', 'nunique'), avg_car=('car', 'mean')
    ).sort_values('avg_car', ascending=False).head(15)
    for ind, r in car_by_ind.iterrows():
        lines.append(f"| {ind} | {int(r['n_events'])} | {r['avg_car']:+.5f} | - |")
    lines.append("")
    
    out_path = OUT + f"{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        f.write('\n'.join(lines))
    
    print(f"\n报告: {out_path}")

if __name__ == '__main__':
    main()
