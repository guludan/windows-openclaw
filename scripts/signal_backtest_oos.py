#!/usr/bin/env python3
"""
信号体系样本外回测 — 独立于已有 pool_energy，直接从 daily_prices 计算

训练集: 2002-01 ~ 2023-12
测试集: 2024-01 ~ 2026-05

功能:
  1. 计算 S1/S2/S3 信号及 T+5/10/20 收益
  2. 按盈余（surplus = b_pool - a_pool）每日截面三等分
  3. 同股票同类型信号 20 日内去重，避免重叠虚增
  4. 输出训练集 vs 测试集对比

用法:
  python3 signal_backtest_oos.py
"""

import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, json, time
import numpy as np
import pandas as pd
from collections import defaultdict
from datetime import datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

TRAIN_END = '2023-12-31'
TEST_START = '2024-01-01'
DATA_START = '2002-01-04'

LOOKBACK = 120   # vol_z 用的滚动窗口
COOLDOWN = 20    # 同股票同信号 20 日内去重

def load_pool_energy_surplus(conn):
    """加载每日大盘盈余，用于截面排序"""
    cur = conn.cursor()
    cur.execute("""
        SELECT date, surplus FROM pool_energy
        WHERE level='market' AND date >= %s
        ORDER BY date
    """, (DATA_START,))
    return {r[0]: r[1] for r in cur.fetchall()}

def compute_signals_for_stock(symbol, start_date, market_surplus):
    """对单只股票计算所有信号，返回信号记录列表"""
    conn2 = psycopg2.connect(DB)
    cur2 = conn2.cursor()
    cur2.execute("""
        SELECT trade_date, close, volume, amount
        FROM daily_prices WHERE symbol=%s AND trade_date>=%s
        ORDER BY trade_date
    """, (symbol, start_date))
    rows = cur2.fetchall()
    cur2.close()
    conn2.close()
    
    if len(rows) < LOOKBACK + 40:
        return []
    
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
    
    # 信号判定
    df['s1'] = (df['vol_z'] > 3.0) & (df['v_z'].abs() < 1.5)
    df['s2'] = (df['vol_z'] > 2.5) & (df['v_z'] > 2.0) & (df['ret'] > 0.05)
    df['s3'] = (df['vol_z'] > 2.5) & (df['v_z'] > 2.0) & (df['ret'] < -0.05)
    
    records = []
    # 去重追踪: {sig_type: last_signal_idx}
    last_sig = {}
    
    for idx in range(LOOKBACK, n - COOLDOWN):
        d = df.index[idx]
        d_key = d.date() if hasattr(d, 'date') else d
        
        # 跳过无盈余数据的日期
        if d_key not in market_surplus:
            continue
        
        surplus = market_surplus[d_key]
        
        sig_types = []
        if df['s1'].iloc[idx]:
            sig_types.append('s1_surge')
        if df['s2'].iloc[idx]:
            sig_types.append('s2_pulse')
        if df['s3'].iloc[idx]:
            sig_types.append('s3_panic')
        
        if not sig_types:
            continue
        
        for sig in sig_types:
            # 去重: 同股票同信号类型 COOLDOWN 日内只记录第一次
            last = last_sig.get(sig)
            if last is not None and idx - last < COOLDOWN:
                continue
            last_sig[sig] = idx
            
            ret_5 = float(df['close'].iloc[idx+5] / df['close'].iloc[idx] - 1) if idx+5 < n else None
            ret_10 = float(df['close'].iloc[idx+10] / df['close'].iloc[idx] - 1) if idx+10 < n else None
            ret_20 = float(df['close'].iloc[idx+20] / df['close'].iloc[idx] - 1) if idx+20 < n else None
            
            records.append({
                'symbol': symbol,
                'date': d_key,
                'sig': sig,
                'surplus': surplus,
                'ret_5': ret_5,
                'ret_10': ret_10,
                'ret_20': ret_20,
            })
    
    return records

def main():
    conn = psycopg2.connect(DB)
    
    print("="*60)
    print("信号体系样本外回测", datetime.now().strftime('%Y-%m-%d %H:%M'))
    print("="*60)
    
    # 1) 加载大盘盈余
    print("\n[1] 加载大盘 surplus...")
    mkt_surplus = load_pool_energy_surplus(conn)
    print(f"  {len(mkt_surplus)} 天")
    
    # 2) 获取股票列表（2020年后有 >300 交易日的活跃股）
    print("\n[2] 股票列表...")
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol FROM daily_prices
        WHERE trade_date >= '2020-01-01' AND close > 0
        GROUP BY symbol HAVING COUNT(*) > 300
        ORDER BY symbol
    """)
    stocks = [r[0] for r in cur.fetchall()]
    print(f"  {len(stocks)} 只")
    cur.close()
    conn.close()
    
    # 3) 逐只计算信号
    print("\n[3] 信号计算（逐只）...")
    all_signals = []  # [{symbol, date, sig, surplus, ret_5/10/20}]
    
    for i, sym in enumerate(stocks):
        try:
            sigs = compute_signals_for_stock(sym, DATA_START, mkt_surplus)
            all_signals.extend(sigs)
        except Exception as e:
            print(f"  {sym} 失败: {e}")
        
        if (i+1) % 200 == 0 or i == len(stocks)-1:
            print(f"  {i+1}/{len(stocks)}, 累计信号: {len(all_signals)}", end='\r')
    
    print(f"\n  总信号: {len(all_signals)}")
    
    df = pd.DataFrame(all_signals)
    if df.empty:
        print("❌ 无信号")
        return
    
    # 4) 按时间分为训练集/测试集
    print("\n[4] 训练集/测试集分割...")
    df['train'] = df['date'] <= pd.to_datetime(TRAIN_END).date()
    train = df[df['train']].copy()
    test = df[~df['train']].copy()
    print(f"  训练集: {len(train)} 信号（~2002~2023）")
    print(f"  测试集: {len(test)} 信号（2024~2026）")
    
    def compute_oos_stats(subset, label):
        """计算按盈余分层的统计"""
        if len(subset) < 50:
            print(f"\n[{label}] 信号不足 ({len(subset)}), 跳过")
            return
        
        print(f"\n{'='*45}")
        print(f"[{label}] {len(subset)} 个信号")
        print(f"{'='*45}")
        
        # 整体统计
        print(f"\n{'信号类型':<15} {'次数':<8} {'T+20均值':<10} {'T+20中位':<10} {'T+20胜率':<8} {'T+10均值':<10} {'T+5均值':<8}")
        print("-"*70)
        for sig in ['s1_surge', 's2_pulse', 's3_panic']:
            grp = subset[subset['sig'] == sig]
            if len(grp) < 3:
                continue
            for k in ['ret_20', 'ret_10', 'ret_5']:
                grp[k] = pd.to_numeric(grp[k], errors='coerce')
            r20 = grp['ret_20'].dropna()
            r10 = grp['ret_10'].dropna()
            r5 = grp['ret_5'].dropna()
            if len(r20) < 3:
                continue
            print(f"{sig:<15} {len(grp):<8} {r20.mean():+8.2%}  {r20.median():+8.2%}  {r20.gt(0).mean():<7.1%} {r10.mean():+9.2%} {r5.mean():+7.2%}")
        
        # 按盈余分三层
        for sig in ['s1_surge', 's2_pulse', 's3_panic']:
            grp = subset[subset['sig'] == sig].copy()
            if len(grp) < 30:
                continue
            grp['ret_20'] = pd.to_numeric(grp['ret_20'], errors='coerce')
            grp = grp.dropna(subset=['ret_20'])
            if len(grp) < 20:
                continue
            
            # 每日截面排序（按日期分组排序）
            grp['surp_rank'] = grp.groupby('date')['surplus'].rank(method='first', pct=True)
            grp['surp_bin'] = pd.qcut(grp['surp_rank'], 3, labels=['低盈余', '中盈余', '高盈余'], duplicates='drop')
            
            print(f"\n  {sig} 按盈余分层:")
            if grp['surp_bin'].nunique() < 2:
                print(f"    分层不足 (nunique={grp['surp_bin'].nunique()})")
                continue
            for label, sub in grp.groupby('surp_bin', observed=True):
                r20 = sub['ret_20']
                print(f"    {label}: {len(sub)}次, T+20均值={r20.mean():+.2%}, 中位={r20.median():+.2%}, 胜率={r20.gt(0).mean():.1%}")
    
    compute_oos_stats(train, "训练集 2002~2023")
    compute_oos_stats(test, "测试集 2024~2026")
    
    # 5) 输出对比摘要
    print(f"\n{'='*60}")
    print("样本内 vs 样本外 对比摘要")
    print(f"{'='*60}")
    
    def get_stats(subset, sig):
        grp = subset[subset['sig'] == sig].copy()
        if len(grp) < 10:
            return None
        grp['ret_20'] = pd.to_numeric(grp['ret_20'], errors='coerce')
        grp = grp.dropna(subset=['ret_20'])
        if len(grp) < 5:
            return None
        
        grp['surp_rank'] = grp.groupby('date')['surplus'].rank(method='first', pct=True)
        grp['surp_bin'] = pd.qcut(grp['surp_rank'], 3, labels=['低', '中', '高'], duplicates='drop')
        
        result = {'n': len(grp), 'mean_all': grp['ret_20'].mean(), 'med_all': grp['ret_20'].median(),
                  'win_all': grp['ret_20'].gt(0).mean()}
        
        for label in ['低', '中', '高']:
            sub = grp[grp['surp_bin'] == label]
            if len(sub) >= 5:
                result[f'{label}盈余_n'] = len(sub)
                result[f'{label}盈余_mean'] = sub['ret_20'].mean()
                result[f'{label}盈余_med'] = sub['ret_20'].median()
                result[f'{label}盈余_win'] = sub['ret_20'].gt(0).mean()
        
        return result
    
    for sig in ['s1_surge', 's2_pulse', 's3_panic']:
        tr = get_stats(train, sig)
        te = get_stats(test, sig)
        if tr is None or te is None:
            continue
        print(f"\n{sig}:")
        for key in ['n', 'mean_all', 'win_all']:
            v_tr = tr.get(key)
            v_te = te.get(key)
            if v_tr is None or v_te is None:
                continue
            if isinstance(v_tr, float):
                print(f"  {key}: 训练={v_tr:+.2%}  测试={v_te:+.2%}  差值={v_te-v_tr:+.2%}")
            else:
                print(f"  {key}: 训练={v_tr}  测试={v_te}")
    
    print(f"\n✅ 完成 ({datetime.now().strftime('%H:%M')})")

if __name__ == '__main__':
    main()
