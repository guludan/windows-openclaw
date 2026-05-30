"""
[B]/[A] 递推引擎 v1 — 大盘级 (修正版)

参数校准：
  - 注入强度: 涨跌比偏差 × 日均涨跌幅幅度 × 0.5
  - 中和效率: η = 0.1 + 0.3 × (1 - R_pct) — R越低中和越快，但不会吃光整个池
  - 初始值: [B]=[A]=1.0 (放大让数值稳定)
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

def load_data(start='2005-01-01'):
    """加载状态序列"""
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # 每日聚合
    cur.execute("""
        SELECT trade_date, COUNT(*) as stock_count,
               SUM(CASE WHEN (close - open) / NULLIF(open, 0) * 100 > 0 THEN 1 ELSE 0 END)::float 
                    / COUNT(*) as advance_ratio,
               AVG((close - open) / NULLIF(open, 0) * 100) as avg_pct,
               STDDEV((close - open) / NULLIF(open, 0) * 100) as cs_vol,
               SUM(volume) as total_volume,
               SUM(amount) as total_amount
        FROM daily_prices
        WHERE trade_date >= %s AND close > 0 AND volume > 0 AND open > 0 AND (amount IS NULL OR amount > 0)
        GROUP BY trade_date ORDER BY trade_date
    """, (start,))
    
    cols = ['trade_date', 'stock_count', 'advance_ratio', 'avg_pct', 'cs_vol', 'total_volume', 'total_amount']
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df.set_index('trade_date', inplace=True)
    
    # R指数
    cur.execute("""
        SELECT trade_date, value, extra::text FROM market_signals
        WHERE signal_type='r_index' AND trade_date >= %s ORDER BY trade_date
    """, (start,))
    r_map = {}
    for r in cur.fetchall():
        extra = json.loads(r[2]) if r[2] else {}
        r_map[r[0]] = {'r_val': r[1], 'r_pct': extra.get('r_pct', 0.5)}
    
    df['r_val'] = df.index.map(lambda d: r_map.get(d.date(), {}).get('r_val', 1.0) if hasattr(d, 'date') else 1.0)
    df['r_pct'] = df.index.map(lambda d: r_map.get(d.date(), {}).get('r_pct', 0.5) if hasattr(d, 'date') else 0.5)
    
    conn.close()
    return df.dropna(subset=['advance_ratio'])

def compute_injection(adv_ratio, avg_pct, cs_vol, r_pct):
    """
    计算当日注入
    
    注入逻辑：
      - 截面涨跌比偏离0.5 → 方向性注入
      - 平均涨跌幅大小 → 注入强度缩放
      - 波动率横截异动 → 双向不确定性注入（分歧大=能量大）
    """
    adv_dev = adv_ratio - 0.5
    pct_mag = min(abs(avg_pct) / 1.0, 1.5)
    vol_mag = min(cs_vol / 2.0, 1.0)
    
    # 方向性注入 (涨跌比驱动 + 平均涨跌幅放大)
    b_dir = max(adv_dev, 0) * pct_mag * 0.5
    a_dir = max(-adv_dev, 0) * pct_mag * 0.5 + abs(adv_dev) * 0.05  # 额外空头补偿
    
    # 分歧注入 (高波动 = 双向都有可能)
    b_div = vol_mag * 0.05
    a_div = vol_mag * 0.05
    
    return b_dir + b_div, a_dir + a_div

def compute_eta(r_pct):
    """
    中和效率 η(R)
    
    R=0(极度分歧): η=0.5 — 分歧大，成交慢
    R=0.5(中性): η=0.3 — 正常成交
    R=1.0(极度一致): η=0.1 — 一致方向，反向少
    """
    return 0.1 + 0.4 * (1.0 - r_pct)

def compute_phase(b_pool, a_pool, b_change, a_change, surplus, imbalance, eta):
    """相空间定位"""
    # [B]远大于[A]且仍在积累
    if surplus > 0.3 and b_change > 0.01:
        return 'B_ACCUM'
    # [B]远大于[A]但开始转弱
    if surplus > 0.3 and b_change < -0.01:
        return 'B_DISTRIB'
    # [A]远大于[B]且还在积累
    if surplus < -0.3 and a_change > 0.01:
        return 'A_ACCUM'
    # [A]远大于[B]但开始转弱
    if surplus < -0.3 and a_change < -0.01:
        return 'A_RECOVER'
    # 高均衡
    if abs(imbalance) < 0.1 and b_pool + a_pool > 1.5:
        return 'HI_EQ'
    # 低均衡
    if abs(imbalance) < 0.1 and b_pool + a_pool < 0.5:
        return 'LO_EQ'
    return 'NEUTRAL'

def main():
    print("="*60)
    print("[B]/[A] 递推引擎 v1 — 大盘级 (修正)")
    print("="*60)
    
    # 1) 加载数据
    print("\n[1] 加载数据...")
    df = load_data()
    print(f"  {len(df)} 天 ({df.index[0].date()} ~ {df.index[-1].date()})")
    
    # 2) 递推
    print("[2] 递推计算...")
    b_pool = 1.0
    a_pool = 1.0
    
    records = []
    
    for i, (d, row) in enumerate(df.iterrows()):
        r_pct = row['r_pct'] if not np.isnan(row['r_pct']) else 0.5
        
        # 注入 — 加nan防护
        cs_vol = row['cs_vol'] if not np.isnan(row['cs_vol']) else 1.0
        avg_pct = row['avg_pct'] if not np.isnan(row['avg_pct']) else 0.0
        adv_ratio = row['advance_ratio'] if not np.isnan(row['advance_ratio']) else 0.5
        b_in, a_in = compute_injection(
            adv_ratio, avg_pct, cs_vol, r_pct
        )
        
        # 中和
        eta = compute_eta(r_pct)
        neut = min(b_pool, a_pool) * eta
        
        # 递推
        b_new = b_pool + b_in - neut
        a_new = a_pool + a_in - neut
        
        b_pool = max(b_new, 0.01)
        a_pool = max(a_new, 0.01)
        
        total = b_pool + a_pool
        surplus = b_pool - a_pool
        imbalance = (b_pool - a_pool) / total if total > 0 else 0
        
        b_change = b_pool - records[-1]['b_pool'] if records else 0
        a_change = a_pool - records[-1]['a_pool'] if records else 0
        
        phase = compute_phase(b_pool, a_pool, b_change, a_change, surplus, imbalance, eta)
        
        records.append({
            'date': d, 'b_pool': b_pool, 'a_pool': a_pool,
            'b_in': b_in, 'a_in': a_in, 'neutralized': neut, 'eta': eta,
            'surplus': surplus, 'imbalance': imbalance,
            'r_pct': r_pct, 'phase': phase,
            'adv_ratio': row['advance_ratio'],
        })
    
    result = pd.DataFrame(records)
    
    # 3) 统计
    print("\n[3] 统计")
    print(f"  [B]: 均值={result['b_pool'].mean():.4f}, 范围=[{result['b_pool'].min():.4f}, {result['b_pool'].max():.4f}]")
    print(f"  [A]: 均值={result['a_pool'].mean():.4f}, 范围=[{result['a_pool'].min():.4f}, {result['a_pool'].max():.4f}]")
    print(f"  盈余: 均值={result['surplus'].mean():+.4f}, 范围=[{result['surplus'].min():+.4f}, {result['surplus'].max():+.4f}]")
    print(f"  η: 均值={result['eta'].mean():.4f}, 范围=[{result['eta'].min():.4f}, {result['eta'].max():.4f}]")
    
    phase_dist = result['phase'].value_counts()
    print(f"  相空间:")
    for p, c in phase_dist.items():
        print(f"    {p}: {c}天 ({c/len(result)*100:.1f}%)")
    
    # 与现有ba_surplus对比
    print("\n[4] 与ba_surplus对比...")
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT trade_date, value FROM market_signals WHERE signal_type='ba_surplus' AND trade_date >= '2023-01-01' ORDER BY trade_date")
    old = {r[0]: r[1] for r in cur.fetchall()}
    conn.close()
    
    result['old_surplus'] = result['date'].map(lambda d: old.get(d.date(), None) if hasattr(d, 'date') else None)
    comp = result.dropna(subset=['old_surplus'])
    if len(comp) > 50:
        corr = comp['surplus'].corr(comp['old_surplus'])
        print(f"  与老ba_surplus相关系数: {corr:.4f}")
    
    # 5) 写入pool_energy
    print("\n[5] 写入pool_energy...")
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
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
            json.dumps({'advance_ratio': round(float(row['adv_ratio']), 4) if not np.isnan(row['adv_ratio']) else 0.5}, allow_nan=False),
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
    print(f"  ✅ {len(batch)}条已写入")
    
    # 6) 近30天打印
    print("\n[6] 近30天")
    print(f"{'日期':<12} {'[B]':<8} {'[A]':<8} {'盈余':<10} {'注入B/A':<14} {'中和':<8} {'η':<6} {'Phase':<14}")
    for _, row in result.tail(30).iterrows():
        d = row['date'].date() if hasattr(row['date'], 'date') else row['date']
        print(f"{d}  {row['b_pool']:<8.4f} {row['a_pool']:<8.4f} {row['surplus']:<+9.4f} "
              f"{row['b_in']:.4f}/{row['a_in']:.4f}  {row['neutralized']:<8.4f} {row['eta']:<6.4f} {row['phase']:<14}")

if __name__ == '__main__':
    main()
