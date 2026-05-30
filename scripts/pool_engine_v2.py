"""
[B]/[A] 递推引擎 v2 — 大盘级 + 自然衰减 + S2脉冲注入

改进：
  1. 自然衰减：b_pool *= 0.999, a_pool *= 0.999 (每日0.1%消失)
     半衰期693天，长期牛市积累的能量会慢慢衰减
  2. S2脉冲信号作为精确事件注入源
  3. 注入参数调低，让[B]保持在[0.1, 5]范围
  4. 重新初始化：B=A=0.5
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import psycopg2.extras
import numpy as np
import pandas as pd
import json, os
from datetime import datetime, timedelta

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT = '/home/tearsyu/.openclaw/workspace/memory/reports/'

DECAY_RATE = 0.0003  # 每日自然衰减 0.03% (半衰期2310天)

def load_data(start='2015-01-01'):
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    cur.execute("""
        SELECT trade_date, COUNT(*) as stock_count,
               SUM(CASE WHEN (close - open) / NULLIF(open, 0) * 100 > 0 THEN 1 ELSE 0 END)::float 
                    / COUNT(*) as advance_ratio,
               AVG((close - open) / NULLIF(open, 0) * 100) as avg_pct,
               STDDEV((close - open) / NULLIF(open, 0) * 100) as cs_vol,
               SUM(volume) as total_volume,
               SUM(amount) as total_amount
        FROM daily_prices
        WHERE trade_date >= %s AND close > 0 AND volume > 0 AND open > 0 AND amount > 0
        GROUP BY trade_date ORDER BY trade_date
    """, (start,))
    
    cols = ['trade_date', 'stock_count', 'advance_ratio', 'avg_pct', 'cs_vol', 'total_volume', 'total_amount']
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df.set_index('trade_date', inplace=True)
    
    # R指数
    cur.execute("""SELECT trade_date, value, extra::text FROM market_signals
        WHERE signal_type='r_index' AND trade_date >= %s ORDER BY trade_date""", (start,))
    r_map = {}
    for r in cur.fetchall():
        extra = json.loads(r[2]) if r[2] else {}
        r_map[r[0]] = {'r_pct': extra.get('r_pct', 0.5)}
    df['r_pct'] = df.index.map(lambda d: r_map.get(d.date(), {}).get('r_pct', 0.5))
    
    # S2脉冲事件 — 大盘级脉冲计数：当日全市场S2信号数量
    cur.execute("""
        SELECT trade_date, value::int FROM market_signals
        WHERE signal_type='s2_pulse_count' AND trade_date >= %s ORDER BY trade_date
    """, (start,))
    s2 = {r[0]: r[1] for r in cur.fetchall()}
    if not s2:
        print("  注: s2_pulse_count 信号不存在，用0填充")
    df['s2_count'] = df.index.map(lambda d: s2.get(d.date(), 0))
    
    conn.close()
    return df.dropna(subset=['advance_ratio'])

def compute_injection(adv_ratio, avg_pct, cs_vol, r_pct, prev_surplus):
    """
    注入计算 (v2, 更平衡)
    
    核心改动：
    - 方向性注入大幅降低系数 (0.5→0.15)
    - 盈余过大时均衡反馈 (prev_surplus * 0.015 反向拉动)
    """
    adv_dev = adv_ratio - 0.5
    pct_mag = min(abs(avg_pct) / 1.5, 1.0)
    vol_mag = min(cs_vol / 2.5, 1.0)
    
    # 方向性注入
    b_dir = max(adv_dev, 0) * pct_mag * 0.15
    a_dir = max(-adv_dev, 0) * pct_mag * 0.15
    
    # 分歧注入
    b_div = vol_mag * 0.02
    a_div = vol_mag * 0.02
    
    # 均衡反馈（盈余过大时反向拉动）
    equil = -prev_surplus * 0.008
    b_eq = max(equil, 0)
    a_eq = max(-equil, 0)
    
    return b_dir + b_div + b_eq, a_dir + a_div + a_eq

def compute_eta(r_pct):
    """中和效率 η(R) — 更温和"""
    return 0.08 + 0.32 * (1.0 - r_pct)

def compute_phase(b_pool, a_pool, b_change, a_change, surplus, imbalance):
    if surplus > 0.2 and b_change > 0.005:
        return 'B_ACCUM'
    if surplus > 0.2 and b_change < -0.005:
        return 'B_DISTRIB'
    if surplus < -0.2 and a_change > 0.005:
        return 'A_ACCUM'
    if surplus < -0.2 and a_change < -0.005:
        return 'A_RECOVER'
    if abs(imbalance) < 0.05:
        return 'EQUILIBRIUM'
    return 'NEUTRAL'

def main():
    print("="*60)
    print("[B]/[A] 递推引擎 v2 — 大盘级 + 衰减 + 均衡")
    print("="*60)
    
    print("\n[1] 加载数据...")
    df = load_data()
    print(f"  {len(df)} 天 ({df.index[0].date()} ~ {df.index[-1].date()})")
    
    print("[2] 递推...")
    b_pool = 1.0
    a_pool = 1.0
    records = []
    
    for i, (d, row) in enumerate(df.iterrows()):
        r_pct = row['r_pct'] if not np.isnan(row['r_pct']) else 0.5
        prev_surplus = b_pool - a_pool
        
        # 注入
        b_in, a_in = compute_injection(
            row['advance_ratio'], row['avg_pct'], row['cs_vol'], r_pct, prev_surplus
        )
        
        # S2事件注入 (每只S2信号+0.02，但大盘级的事件不会太多)
        s2 = int(row.get('s2_count', 0))
        b_in += s2 * 0.02
        a_in += s2 * 0.01  # S2脉冲也有空头注入（见顶后转空）
        
        # 中和
        eta = compute_eta(r_pct)
        neut = min(b_pool, a_pool) * eta
        
        # 自然衰减
        b_decay = b_pool * DECAY_RATE
        a_decay = a_pool * DECAY_RATE
        
        # 递推
        b_pool = max(b_pool + b_in - neut - b_decay, 0.01)
        a_pool = max(a_pool + a_in - neut - a_decay, 0.01)
        
        total = b_pool + a_pool
        surplus = b_pool - a_pool
        imbalance = (b_pool - a_pool) / total if total > 0 else 0
        
        b_change = b_pool - records[-1]['b_pool'] if records else 0
        a_change = a_pool - records[-1]['a_pool'] if records else 0
        
        phase = compute_phase(b_pool, a_pool, b_change, a_change, surplus, imbalance)
        
        records.append({
            'date': d, 'b_pool': b_pool, 'a_pool': a_pool,
            'b_in': b_in, 'a_in': a_in, 'neutralized': neut, 'decay': b_decay,
            'eta': eta, 'surplus': surplus, 'imbalance': imbalance,
            'r_pct': r_pct, 'phase': phase, 'adv_ratio': row['advance_ratio'],
            's2_count': s2,
        })
    
    result = pd.DataFrame(records)
    
    print(f"\n[3] 统计")
    print(f"  [B]: 均值={result['b_pool'].mean():.4f}, 范围=[{result['b_pool'].min():.4f}, {result['b_pool'].max():.4f}]")
    print(f"  [A]: 均值={result['a_pool'].mean():.4f}, 范围=[{result['a_pool'].min():.4f}, {result['a_pool'].max():.4f}]")
    print(f"  盈余: 均值={result['surplus'].mean():+.4f}, 范围=[{result['surplus'].min():+.4f}, {result['surplus'].max():+.4f}]")
    
    phase_dist = result['phase'].value_counts()
    print(f"  相空间:")
    for p, c in phase_dist.items():
        print(f"    {p}: {c}天 ({c/len(result)*100:.1f}%)")
    
    print(f"\n  S2事件分布: 非零={sum(result['s2_count']>0)}天, 均值={result['s2_count'].mean():.2f}")
    
    # 写入PG (高精度覆盖)
    print("\n[4] 写入pool_energy...")
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    conn.autocommit = False
    
    batch = []
    for _, row in result.iterrows():
        d = row['date'].date() if hasattr(row['date'], 'date') else row['date']
        batch.append((
            'market', d,
            round(row['b_pool'], 6), round(row['a_pool'], 6),
            round(row['b_in'], 6), round(row['a_in'], 6),
            round(row['neutralized'], 6), round(row['eta'], 6),
            round(row['r_pct'], 4), round(row['surplus'], 6), round(row['imbalance'], 5),
            row['phase'],
            json.dumps({'advance_ratio': round(row['adv_ratio'], 4), 's2_count': int(row['s2_count'])}),
        ))
    
    psycopg2.extras.execute_values(
        cur,
        """INSERT INTO pool_energy 
           (level, date, b_pool, a_pool, b_inject, a_inject, neutralized, eta,
            r_pct, surplus, imbalance, phase, extra)
           VALUES %s
           ON CONFLICT (level, date) DO UPDATE SET
               b_pool=EXCLUDED.b_pool, a_pool=EXCLUDED.a_pool,
               b_inject=EXCLUDED.b_inject, a_inject=EXCLUDED.a_inject,
               neutralized=EXCLUDED.neutralized, eta=EXCLUDED.eta,
               r_pct=EXCLUDED.r_pct, surplus=EXCLUDED.surplus,
               imbalance=EXCLUDED.imbalance, phase=EXCLUDED.phase,
               extra=EXCLUDED.extra, updated_at=NOW()""",
        batch, page_size=500
    )
    conn.commit()
    conn.close()
    print(f"  ✅ {len(batch)}条")
    
    # 打印诊断
    print("\n[5] 关键时期:")
    for label, start, end in [("2015-01~06", '2015-01-01', '2015-06-30'),
                               ("2015-07~12", '2015-07-01', '2015-12-31'),
                               ("2024-09~10牛市", '2024-09-01', '2024-10-31'),
                               ("2026-05", '2026-05-01', '2026-05-31')]:
        sub = result[(result['date'] >= start) & (result['date'] <= end)]
        if len(sub) == 0:
            continue
        print(f"  {label}: [B]均值={sub['b_pool'].mean():.3f}, [A]均值={sub['a_pool'].mean():.3f}, "
              f"盈余均值={sub['surplus'].mean():+.3f}")
    
    print("\n[6] 近20天:")
    print(f"{'日期':<12} {'[B]':<7} {'[A]':<7} {'盈余':<8} {'注入':<12} {'中和':<7} {'η':<5} {'Phase':<14}")
    for _, row in result.tail(20).iterrows():
        d = row['date'].date() if hasattr(row['date'], 'date') else row['date']
        print(f"{d}  {row['b_pool']:<7.4f} {row['a_pool']:<7.4f} {row['surplus']:<+8.4f} "
              f"{row['b_in']:.4f}/{row['a_in']:.4f}  {row['neutralized']:<7.4f} {row['eta']:<5.4f} {row['phase']:<14}")

if __name__ == '__main__':
    main()
