"""
个股波动率模型 v2 — 纯SQL + 精简回测

计算每只股票每20日的波动率特征、标记异常事件、回测收益形态
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import numpy as np
import pandas as pd
import json, os
from datetime import datetime, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT = '/home/tearsyu/.openclaw/workspace/memory/reports/'

def get_conn():
    return psycopg2.connect(DB)

def compute_stock_vol_features(conn, start='2023-01-01', sample_n=500):
    """
    用SQL计算所有股票的特征，避免逐只加载
    
    返回每只股票每交易日的波动率特征
    """
    cur = conn.cursor()
    
    # 先获取有足够数据的股票
    cur.execute("""
        SELECT symbol FROM daily_prices
        WHERE trade_date >= %s
        GROUP BY symbol HAVING COUNT(*) > 400
        ORDER BY symbol
    """, (start,))
    all_stocks = [r[0] for r in cur.fetchall()]
    print(f"  可用股票: {len(all_stocks)}只")
    
    # 抽样
    np.random.seed(42)
    sample = list(np.random.choice(all_stocks, min(sample_n, len(all_stocks)), replace=False))
    print(f"  抽样: {len(sample)}只")
    
    # 批量加载所有数据 (单次大查询比逐只要快)
    # 构建IN子句
    batch_size = 50
    all_data = []
    
    for i in range(0, len(sample), batch_size):
        batch = sample[i:i+batch_size]
        placeholders = ','.join(['%s'] * len(batch))
        cur.execute(f"""
            SELECT symbol, trade_date, close, volume
            FROM daily_prices
            WHERE symbol IN ({placeholders}) AND trade_date >= '2020-01-01'
            ORDER BY symbol, trade_date
        """, batch)
        all_data.extend(cur.fetchall())
        if (i // batch_size) % 5 == 0:
            print(f"    加载: {i}/{len(sample)}")
    
    print(f"  总数据行: {len(all_data):,}")
    
    # 转成pandas分组处理
    df = pd.DataFrame(all_data, columns=['symbol', 'trade_date', 'close', 'volume'])
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    
    # 按股票分组算特征
    results = []
    
    for sym, grp in df.groupby('symbol'):
        grp = grp.sort_values('trade_date').reset_index(drop=True)
        grp['ret'] = grp['close'].pct_change()
        
        n = len(grp)
        if n < 60:
            continue
        
        # 滚动特征
        vol_20d = grp['ret'].rolling(20).std() * np.sqrt(252)
        
        # 波动率120日z-score
        vol_mean_120 = vol_20d.rolling(120).mean()
        vol_std_120 = vol_20d.rolling(120).std()
        vol_z = (vol_20d - vol_mean_120) / vol_std_120
        
        # 波动率分位 (120日)
        def calc_pct(series, window):
            result = np.full(len(series), np.nan)
            for i in range(window, len(series)):
                w = series.iloc[i-window:i+1].dropna()
                if len(w) > 0:
                    result[i] = sum(1 for x in w if x <= series.iloc[i]) / len(w)
            return result
        
        vol_pct = calc_pct(vol_20d, 120)
        
        # volume z-score
        vol_v = grp['volume']
        vol_v_mean = vol_v.rolling(20).mean()
        vol_v_std = vol_v.rolling(20).std()
        vol_v_z = (vol_v - vol_v_mean) / vol_v_std
        
        # 信号标记
        vol_event = (
            (vol_z > 2.0) & 
            (np.array(vol_pct) > 0.90)
        )
        
        price_surge = (
            (np.array(vol_z) > 1.5) &
            (np.array(vol_v_z) > 2.0) &
            (np.abs(grp['ret'].values) > 0.03)
        )
        
        for j in range(60, n):
            if not vol_event[j]:
                continue
            
            # 信号后收益
            def future_ret(pos, days):
                if pos + days < n:
                    return grp.iloc[pos+days]['close'] / grp.iloc[pos]['close'] - 1
                return None
            
            results.append({
                'symbol': sym,
                'trade_date': grp.iloc[j]['trade_date'],
                'vol_annual': vol_20d.iloc[j],
                'vol_pct': vol_pct[j],
                'vol_v_z': vol_v_z.iloc[j],
                'ret_today': grp.iloc[j]['ret'],
                'ret_5': future_ret(j, 5),
                'ret_10': future_ret(j, 10),
                'ret_20': future_ret(j, 20),
                'is_surge': bool(price_surge[j]),
            })
    
    return pd.DataFrame(results), df

def main():
    conn = get_conn()
    
    print("="*60)
    print("个股波动率模型 v2 — 纯SQL批量 + 收益回测")
    print("="*60)
    
    # 1) 算特征
    print("\n[1] 计算股票波动率特征...")
    sig_df, all_prices = compute_stock_vol_features(conn, sample_n=500)
    
    print(f"\n  总信号: {len(sig_df)}次")
    
    if len(sig_df) < 10:
        print("信号太少，无法分析")
        conn.close()
        return
    
    # 2) 基础统计
    print("\n[2] 基础统计...")
    print(f"  信号vol年化: P50={sig_df['vol_annual'].median()*100:.2f}%, "
          f"P90={sig_df['vol_annual'].quantile(0.9)*100:.2f}%")
    print(f"  vol分位: P50={sig_df['vol_pct'].median():.2%}")
    print(f"  换手率z: P50={sig_df['vol_v_z'].median():.1f}")
    
    price_surge_count = sig_df['is_surge'].sum()
    print(f"  其中量价齐升: {price_surge_count}次 ({price_surge_count/len(sig_df)*100:.1f}%)")
    
    # 3) 收益形态
    print("\n[3] 收益形态...")
    for w in ['ret_5', 'ret_10', 'ret_20']:
        vals = sig_df[w].dropna().values
        if len(vals) > 0:
            print(f"  {w}: 均值={vals.mean():+.2%}, 中位数={np.median(vals):+.2%}, "
                  f"正收益={(vals>0).mean():.1%}, 左尾5%={np.percentile(vals,5):.2%}")
    
    # 4) 分成量价齐升 vs 纯波动率暴增
    print("\n[4] 量价齐升 vs 纯波动率暴增...")
    surge = sig_df[sig_df['is_surge'] == True]
    no_surge = sig_df[sig_df['is_surge'] == False]
    
    for label, grp in [('量价齐升', surge), ('纯波动暴增', no_surge)]:
        if len(grp) < 5:
            continue
        print(f"\n  {label} ({len(grp)}次):")
        for w in ['ret_5', 'ret_10', 'ret_20']:
            vals = grp[w].dropna()
            if len(vals) > 0:
                print(f"    {w}: 均值={vals.mean():+.2%}, 中位数={np.median(vals):+.2%}, "
                      f"正收益={(vals>0).mean():.1%}")
    
    # 5) 个股波动率分层
    print("\n[5] 按个股自身波动率分层...")
    stock_vols = all_prices.groupby('symbol')['close'].apply(
        lambda x: x.pct_change().std() * np.sqrt(252) * 100
    ).reset_index()
    stock_vols.columns = ['symbol', 'vol_annual_pct']
    
    p30 = stock_vols['vol_annual_pct'].quantile(0.3)
    p60 = stock_vols['vol_annual_pct'].quantile(0.6)
    print(f"  波动率阈值: LOW<{p30:.1f}%, MID<{p60:.1f}%, HIGH>={p60:.1f}%")
    
    def get_layer(code):
        v = stock_vols[stock_vols['symbol'] == code]['vol_annual_pct'].values
        if len(v) == 0:
            return 'MID'
        v = v[0]
        if v < p30: return 'LOW'
        elif v < p60: return 'MID'
        else: return 'HIGH'
    
    sig_df['layer'] = sig_df['symbol'].apply(get_layer)
    
    for layer in ['LOW', 'MID', 'HIGH']:
        grp = sig_df[sig_df['layer'] == layer]
        if len(grp) < 5:
            continue
        print(f"\n  {layer} ({len(grp)}次):")
        for w in ['ret_5', 'ret_10', 'ret_20']:
            vals = grp[w].dropna()
            if len(vals) > 0:
                print(f"    {w}: 均值={vals.mean():+.2%}, 中位数={np.median(vals):+.2%}, "
                      f"正收益={(vals>0).mean():.1%}, 左尾5%={np.percentile(vals,5):.2f}")
    
    conn.close()

if __name__ == '__main__':
    main()
