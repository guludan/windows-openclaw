"""
个股 [B]/[A] 递推引擎 v1 — 从precision_signals结果提取S2/S3/S1注入

大盘级 [B]/[A] 已上线到 pool_energy level='market'
现在用S2/S3/S1精确信号做个股级递推

采用简化的个股参数（继承大盘R_pct，但事件注入驱动个股偏差）
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import psycopg2.extras
import numpy as np
import pandas as pd
import json, os
from datetime import datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT = '/home/tearsyu/.openclaw/workspace/memory/reports/'

def get_stock_list(conn, min_days=500):
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol FROM daily_prices
        WHERE trade_date >= '2020-01-01' AND close > 0
        GROUP BY symbol HAVING COUNT(*) > %s
        ORDER BY symbol
    """, (min_days,))
    return [r[0] for r in cur.fetchall()]

def extract_signals_from_stock(conn, symbol, start='2022-01-01'):
    """对单只股票提取S2/S3/S1信号"""
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, close, volume, amount
        FROM daily_prices
        WHERE symbol = %s AND trade_date >= %s
        ORDER BY trade_date
    """, (symbol, start))
    
    rows = cur.fetchall()
    if len(rows) < 120:
        return []
    
    df = pd.DataFrame(rows, columns=['trade_date', 'close', 'volume', 'amount'])
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df.set_index('trade_date', inplace=True)
    
    df['ret'] = df['close'].pct_change()
    df['log_ret'] = np.log(df['close'] / df['close'].shift(1)).clip(-0.11, 0.11)
    
    n = len(df)
    
    # 波动率
    df['vol_20d'] = df['log_ret'].rolling(20).std() * np.sqrt(252)
    vol_ma = df['vol_20d'].rolling(120).mean()
    vol_std = df['vol_20d'].rolling(120).std(ddof=1)
    df['vol_z'] = (df['vol_20d'] - vol_ma) / vol_std
    
    # 换手率
    df['v_z'] = (df['volume'] - df['volume'].rolling(20).mean()) / df['volume'].rolling(20).std().clip(lower=1)
    
    # 成交额 z-score
    df['amt_z'] = (df['amount'] - df['amount'].rolling(20).mean()) / df['amount'].rolling(20).std().clip(lower=1)
    
    # S2: 脉冲卖出 — 波动暴增 + 放量 + 大涨
    s2 = (df['vol_z'] > 2.5) & (df['v_z'] > 2.0) & (df['ret'] > 0.05)
    
    # S3: 恐慌修复 — 波动暴增 + 放量 + 大跌
    s3 = (df['vol_z'] > 2.5) & (df['v_z'] > 2.0) & (df['ret'] < -0.05)
    
    # S1: 波动暴增 + 换手正常（Phase α 预注入）
    s1 = (df['vol_z'] > 3.0) & (np.abs(df['v_z']) < 1.5)
    
    results = []
    for idx in range(120, n - 20):
        d = df.index[idx]
        
        events = []
        if s2.iloc[idx]:
            events.append(('s2_pulse', float(df['ret'].iloc[idx]), float(df['vol_z'].iloc[idx]), float(df['v_z'].iloc[idx])))
        if s3.iloc[idx]:
            events.append(('s3_panic', float(df['ret'].iloc[idx]), float(df['vol_z'].iloc[idx]), float(df['v_z'].iloc[idx])))
        if s1.iloc[idx]:
            events.append(('s1_surge', float(df['ret'].iloc[idx]), float(df['vol_z'].iloc[idx]), float(df['v_z'].iloc[idx])))
        
        if events:
            results.append({
                'date': d.date(),
                'symbol': symbol,
                'events': events,
                'b_pool_pre': 0,  # 占位
                'a_pool_pre': 0,
            })
    
    return results

def run_stock_energy(symbol, df, market_baseline_df=None):
    """
    对单只股票计算个股 [B]/[A] 递推
    
    改进：用大盘 [B] 作为基准，事件信号产生额外的个股偏差
    """
    signals = extract_signals_from_stock(None, symbol)  # 需要conn
    return []

def main():
    conn = psycopg2.connect(DB)
    
    print("="*60)
    print("个股 [B]/[A] 递推 — S2/S3/S1信号提取 + 回测")
    print("="*60)
    
    # 1) 加载大盘市场energy做基线
    print("\n[1] 加载大盘能量基线...")
    cur = conn.cursor()
    cur.execute("""
        SELECT date, b_pool, a_pool, b_inject, a_inject, r_pct, surplus, phase
        FROM pool_energy WHERE level='market' ORDER BY date
    """)
    mkt = {r[0]: {'b': r[1], 'a': r[2], 'bi': r[3], 'ai': r[4], 'r': r[5], 'sur': r[6], 'ph': r[7]}
           for r in cur.fetchall()}
    print(f"  {len(mkt)}天")
    
    # 2) 抽样股票提取信号
    print("\n[2] 抽样...")
    stocks = get_stock_list(conn, min_days=400)
    np.random.seed(42)
    sample = list(np.random.choice(stocks, min(200, len(stocks)), replace=False))
    print(f"  {len(sample)}只")
    
    # 3) 逐股信号提取
    print("\n[3] 提取信号...")
    all_signals = []
    for i, sym in enumerate(sample):
        cur = conn.cursor()
        cur.execute("""
            SELECT trade_date, close, volume, amount
            FROM daily_prices WHERE symbol=%s AND trade_date>='2020-01-01'
            ORDER BY trade_date
        """, (sym,))
        rows = cur.fetchall()
        if len(rows) < 120:
            continue
        
        df = pd.DataFrame(rows, columns=['trade_date', 'close', 'volume', 'amount'])
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df.set_index('trade_date', inplace=True)
        df['ret'] = df['close'].pct_change()
        df['log_ret'] = np.log(df['close'] / df['close'].shift(1)).clip(-0.11, 0.11)
        df['vol_20d'] = df['log_ret'].rolling(20).std() * np.sqrt(252)
        
        n = len(df)
        vol_ma = df['vol_20d'].rolling(120).mean()
        vol_std = df['vol_20d'].rolling(120).std(ddof=1)
        df['vol_z'] = (df['vol_20d'] - vol_ma) / vol_std
        df['v_z'] = (df['volume'] - df['volume'].rolling(20).mean()) / df['volume'].rolling(20).std().clip(lower=1)
        
        s2_mask = (df['vol_z'] > 2.5) & (df['v_z'] > 2.0) & (df['ret'] > 0.05)
        s3_mask = (df['vol_z'] > 2.5) & (df['v_z'] > 2.0) & (df['ret'] < -0.05)
        s1_mask = (df['vol_z'] > 3.0) & (np.abs(df['v_z']) < 1.5)
        
        for idx in range(120, n - 20):
            d = df.index[idx]
            d_key = d.date() if hasattr(d, 'date') else d
            if d_key not in mkt:
                continue
            if d_key < pd.Timestamp('2020-01-01').date():
                continue
            
            # 获取大盘能量
            mb = mkt[d_key]
            b_mkt = mb['b']
            a_mkt = mb['a']
            
            b_in = 0
            a_in = 0
            sig_types = []
            
            if s2_mask.iloc[idx]:
                # S2 脉冲注入：多头注入（拉到顶部了）+ 空头注入更大（接下来要调整）
                b_in += 0.08
                a_in += 0.20
                sig_types.append('s2_pulse')
            if s3_mask.iloc[idx]:
                # S3 恐慌注入：空头恐慌耗尽 + 修复预期
                b_in += 0.15
                a_in += 0.05
                sig_types.append('s3_panic')
            if s1_mask.iloc[idx]:
                # S1 预注入：波动暴增但没放量 → 潜在未定价事件
                b_in += 0.06
                sig_types.append('s1_surge')
            
            if not sig_types:
                continue
            
            # 计算后验收益
            fr_5 = float(df['close'].iloc[idx+5] / df['close'].iloc[idx] - 1) if idx+5 < n else None
            fr_10 = float(df['close'].iloc[idx+10] / df['close'].iloc[idx] - 1) if idx+10 < n else None
            fr_20 = float(df['close'].iloc[idx+20] / df['close'].iloc[idx] - 1) if idx+20 < n else None
            
            # 个股能量递推
            # 用大盘 [B]_mkt作为基础，事件注入造成个股偏差
            b_stock = b_mkt + b_in
            a_stock = a_mkt + a_in
            surplus = b_stock - a_stock
            imbalance = (b_stock - a_stock) / max(b_stock + a_stock, 0.01)
            
            # 模拟注入后的B/A变化
            old_surplus = b_mkt - a_mkt
            delta_surplus = surplus - old_surplus
            
            all_signals.append({
                'date': d.date() if hasattr(d, 'date') else d,
                'symbol': sym,
                'sig_types': '_'.join(sig_types),
                'b_mkt': b_mkt,
                'a_mkt': a_mkt,
                'b_stock': b_stock,
                'a_stock': a_stock,
                'stock_surplus': surplus,
                'delta_surplus': delta_surplus,
                'ret_5': fr_5,
                'ret_10': fr_10,
                'ret_20': fr_20,
                'ret_0': float(df['ret'].iloc[idx]),
            })
        
        if (i+1) % 20 == 0:
            print(f"  {i+1}/{len(sample)}, 信号: {len(all_signals)}")
    
    sig_df = pd.DataFrame(all_signals)
    print(f"\n  总信号: {len(sig_df)}次")
    
    if len(sig_df) < 10:
        print("信号太少，退出")
        conn.close()
        return
    
    # 4) 信号统计
    print("\n[4] 信号统计:")
    print(f"\n{'信号类型':<25} {'次数':<6} {'T+5均值':<10} {'T+5中位':<10} {'T+5胜率':<8} "
          f"{'T+10均值':<10} {'T+10中位':<10} {'T+10胜率':<8} "
          f"{'T+20均值':<10} {'T+20中位':<10} {'T+20胜率':<8} "
          f"{'δ盈余':<10}")
    print("-"*110)
    
    for stype in ['s2_pulse', 's3_panic', 's1_surge', 's2_pulse_s3_panic']:
        if stype == 's2_pulse_s3_panic':
            grp = sig_df[sig_df['sig_types'].str.contains('s2_pulse.*s3_panic|s3_panic.*s2_pulse', na=False)]
        else:
            grp = sig_df[sig_df['sig_types'] == stype]
        if len(grp) < 5:
            continue
        row = f"{stype:<25} {len(grp):<6} "
        for w in ['ret_5', 'ret_10', 'ret_20']:
            vals = grp[w].dropna()
            if len(vals) > 0:
                row += f"{vals.mean():+8.2%}  {np.median(vals):+8.2%}  {(vals>0).mean():<7.1%} "
            else:
                row += "   N/A       N/A       N/A    "
        row += f"{grp['delta_surplus'].mean():+.4f}"
        print(row)
    
    # 5) 按delta_surplus分层
    print("\n[5] δ盈余分层 (delta_surplus 越大说明信号越推多个股偏离大盘):")
    sig_df['delta_bin'] = pd.cut(sig_df['delta_surplus'], 
                                  bins=[-10, -0.2, 0, 0.2, 10],
                                  labels=['<-0.2', '-0.2~0', '0~0.2', '>0.2'])
    for label, grp in sig_df.groupby('delta_bin', observed=True):
        if len(grp) < 5:
            continue
        r20 = grp['ret_20'].dropna()
        print(f"  δ盈余={label}: {len(grp)}次, T+20均值={r20.mean():+.2%}, 胜率={(r20>0).mean():.1%}")
    
    conn.close()
    print("\n✅ 完成")

if __name__ == '__main__':
    main()
