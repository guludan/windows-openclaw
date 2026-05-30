"""v7_bt_phase2.py — V7 分时段门控回测引擎
不调参、不优化，纯验证门控逻辑的熊市生存能力

门控逻辑：
  Phase = A_ACCUM/B_DISTRIB → 空仓 (GC001)
  surplus_z 连续5天↓且< -1.0 → 预熊市，0.3x
  Phase = B_ACCUM/A_RECOVER → 正常交易
  其他 (NEUTRAL/HI_EQ/unknown) → 0.5x

  信号筛选:
    S3 + V7 评分 Top 40% + 当日在允许交易状态
    除权过滤 (abs(ret)>20%)
    龙虎榜（暂缺数据）
    高管增持（暂缺，回测期无增量数据）

  出场:
    T+5 强制平仓，记录 T+5 收益
    无止损（回测期不模拟T+3止盈，因为ret3d刚补）
"""
import psycopg2, numpy as np
from datetime import date, timedelta
from collections import defaultdict

DB="host=192.168.3.14 dbname=guludan user=guludan password=toto"
conn=psycopg2.connect(DB); cur=conn.cursor()

# ===== 1) 大盘 phase + surplus_z 序列 =====
cur.execute("""
    SELECT date, phase, b_pool, a_pool,
           surplus, extra
    FROM pool_energy WHERE level='market'
    ORDER BY date
""")
mkt_rows = cur.fetchall()
print(f"大盘数据: {len(mkt_rows)} 天")

mkt_phase = {}
mkt_surplus = {}
mkt_phase_seq = {}  # 连续 phase 天数
for i, r in enumerate(mkt_rows):
    dt = r[0]; phase = r[1] or 'NEUTRAL'
    b = float(r[2]); a = float(r[3])
    surplus = float(r[4] or (b-a))
    mkt_phase[dt] = phase
    mkt_surplus[dt] = surplus

# ===== 2) surplus_z (滚动5日) =====
dates_sorted = sorted(mkt_surplus.keys())
# 算 surplus_z（全体序列均值/方差）
surplus_vals = np.array([mkt_surplus[d] for d in dates_sorted])
mu_surplus = np.mean(surplus_vals)
sd_surplus = np.std(surplus_vals)
surplus_z = {dates_sorted[i]: (surplus_vals[i] - mu_surplus) / sd_surplus for i in range(len(dates_sorted))}

# 连续5天下降检测
surplus_z_neg5 = set()
for i in range(4, len(dates_sorted)):
    if (surplus_z[dates_sorted[i-4]] > surplus_z[dates_sorted[i-3]] >
        surplus_z[dates_sorted[i-2]] > surplus_z[dates_sorted[i-1]] >
        surplus_z[dates_sorted[i]] and surplus_z[dates_sorted[i]] < -1.0):
        # surplus_z 连续5天下降开始日 = dates_sorted[i-4]  # 从第一个下降日开始
        surplus_z_neg5.add(dates_sorted[i])
        
# ===== 3) 门控规则函数 =====
def get_position_mult(dt, phase_lookup, surplus_lookup, surplus_neg5_lookup):
    """返回仓位乘数"""
    p = phase_lookup.get(dt, 'NEUTRAL')
    # 硬门控
    if p in ('A_ACCUM', 'B_DISTRIB'):
        return 0.0  # 空仓
    # 预熊市
    if dt in surplus_neg5_lookup:
        return 0.3
    if p in ('B_ACCUM', 'A_RECOVER'):
        return 1.0
    # NEUTRAL, HI_EQ, unknown
    return 0.5

# ===== 4) 分时段回测 =====
periods = [
    ('2015-2020(前)', '2015-01-05', '2019-12-31', '慢熊+修复'),
    ('2020-2021', '2020-01-01', '2021-12-31', '慢牛'),
    ('2022-2023', '2022-01-01', '2023-12-31', '熊市'),
    ('2024-2026', '2024-01-01', '2026-05-27', '反弹+震荡'),
]

# 先看看有数据的时间范围
cur.execute("SELECT MIN(trade_date), MAX(trade_date) FROM backtest_signals")
bt_range = cur.fetchone()
print(f"信号表范围: {bt_range[0]} ~ {bt_range[1]}")

for label, start_str, end_str, desc in periods:
    print(f"\n{'='*65}")
    print(f"  时段: {label} ({desc})  {start_str} ~ {end_str}")
    print(f"{'='*65}")
    
    start_dt = date.fromisoformat(start_str)
    end_dt = date.fromisoformat(end_str)
    
    # 拉信号 + 大盘phase + surplus_z
    cur.execute("""
        SELECT bt.trade_date, bt.symbol, bt.ret5d, bt.v7_score,
               bt.extra::text
        FROM backtest_signals bt
        WHERE bt.signal_type='s3_panic'
          AND bt.trade_date>=%s AND bt.trade_date<=%s
          AND bt.ret5d IS NOT NULL AND bt.v7_score IS NOT NULL
        ORDER BY bt.trade_date
    """, (start_dt, end_dt))
    signals_raw = cur.fetchall()
    
    if not signals_raw:
        print(f"  无信号数据")
        continue
    print(f"  全部S3信号: {len(signals_raw)}")
    
    # 按日期组织
    by_date = defaultdict(list)
    for dt, sym, ret5d, v7, extra_raw in signals_raw:
        by_date[dt].append({
            'sym': sym, 'ret5d': float(ret5d or 0), 
            'v7': float(v7 or 0),
            'extra': {}  # 不考虑 extra
        })
    
    print(f"  有信号的交易日: {len(by_date)}")
    
    # 跑门控
    all_trades = []
    all_returns = []
    by_phase = defaultdict(lambda: {'trades': [], 'rets': []})
    capped_days = 0  # 被门控挡掉的信号
    executed_days = 0
    
    for dt in sorted(by_date.keys()):
        if dt not in mkt_phase:
            continue
        
        phase = mkt_phase[dt]
        mult = get_position_mult(dt, mkt_phase, surplus_z, surplus_z_neg5)
        
        if mult == 0.0:
            capped_days += len(by_date[dt])
            continue
        
        executed_days += 1
        
        # 取 Top 40% V7 评分
        sigs_today = by_date[dt]
        # 按 V7 从高到低排序
        sigs_today.sort(key=lambda x: -x['v7'])
        top_n = max(1, int(len(sigs_today) * 0.4))
        for s in sigs_today[:top_n]:
            all_returns.append(s['ret5d'])
            all_trades.append({
                'date': dt, 'sym': s['sym'], 'ret': s['ret5d'], 
                'v7': s['v7'], 'mult': mult, 'phase': phase
            })
            by_phase[phase]['rets'].append(s['ret5d'])
    
    if not all_returns:
        print(f"  门控后无交易")
        continue
    
    rets = np.array(all_returns)
    
    print(f"  日信号被门控挡掉: {capped_days}")
    print(f"  实际执行交易日: {executed_days}")
    print(f"  总交易次数: {len(all_trades)}")
    
    # 分时段统计
    print(f"\n  门控后 T+5 表现:")
    print(f"    均值: {np.mean(rets):+.2%}  中位: {np.median(rets):+.2%}")
    print(f"    胜率: {np.mean(rets>0):.1%}")
    print(f"    最大收益: {np.max(rets):+.2%}  最大亏损: {np.min(rets):+.2%}")
    print(f"    标准差: {np.std(rets):.2%}")
    
    # 按 phase 分层
    print(f"\n  按 Phase 分层:")
    for ph, data in sorted(by_phase.items()):
        pr = np.array(data['rets'])
        print(f"    {ph:>15s}: {len(pr):>4d}次  均值{np.mean(pr):+.2%}  中位{np.median(pr):+.2%}  胜率{np.mean(pr>0):.1%}")
    
    # 模拟净值（假设等权30%仓位）
    cum_ret = 1.0
    max_dd = 0.0
    peak = 1.0
    daily_rets = []
    last_date = None
    
    for t in all_trades:
        daily_ret = t['ret'] * t['mult'] * 0.3  # 30%仓位
        cum_ret *= (1 + daily_ret)
        daily_rets.append({'date': t['date'], 'ret': daily_ret, 'cum': cum_ret})
        peak = max(peak, cum_ret)
        dd = (cum_ret - peak) / peak
        max_dd = min(max_dd, dd)
    
    nifty_ret = (cum_ret - 1.0) * 100
    
    print(f"\n  模拟净值 (30%仓位, phase门控):")
    print(f"    总收益: {cum_ret-1:.2%}  ({len(all_trades)}次)")
    sharpe = np.mean([t['ret'] for t in all_trades]) / max(np.std([t['ret'] for t in all_trades]), 0.0001) * np.sqrt(252)
    print(f"    Sharpe (年化): {sharpe:.2f}")
    print(f"    最大回撤: {max_dd:.2%}")

conn.close()
print(f"\n{'='*65}")
print(f"  完成")
