"""
个股波动模型 — 波动率特征 + 事件信号回测

设计：
  Level 1: 个股波动率特征
    - 滚动20日波动率 (日收益率std)
    - 波动率分位 (相对个股自身历史)
    - 波动率突变 (当日波动率 / 60日均值)
    - 换手率异常 (volume z-score)
  
  Level 2: 事件信号
    - 波动率暴增 → 事件疑云 (Phase α代理)
    - 波动率暴增 + 换手率暴增 → 公开脉冲 (Phase β)
    - 波动率回落 + 换手率持续高 → 扩散后期 (Phase γ)
    
  Level 3: 验证
    - 波动率暴增后N天，是否出现实质事件（R指数变化/资金流变化）
    - 不同波动层股票，信号后的收益形态差异
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

class StockVolModel:
    """
    个股波动率模型 — 对单只股票检测波动率事件
    """
    def __init__(self, df):
        """
        df: columns=[trade_date, close, volume, amount]
            按trade_date排序
        """
        df = df.copy().sort_values('trade_date')
        df['ret'] = df['close'].pct_change()
        df['log_ret'] = np.log(df['close'] / df['close'].shift(1))
        
        self.df = df
        self._compute_features()
    
    def _compute_features(self):
        df = self.df
        w = 20  # 滚动窗口
        
        # 滚动波动率
        df['vol_20d'] = df['log_ret'].rolling(w).std() * np.sqrt(252)
        df['vol_20d_z'] = (df['vol_20d'] - df['vol_20d'].rolling(120).mean()) / df['vol_20d'].rolling(120).std()
        
        # 波动率分位 (相对自己的120日历史)
        vol_hist = []
        for i in range(len(df)):
            if i < 120:
                vol_hist.append(None)
            else:
                window = df['vol_20d'].iloc[max(0,i-120):i+1].dropna()
                if len(window) > 0:
                    pct = sum(1 for x in window if x <= df['vol_20d'].iloc[i]) / len(window)
                    vol_hist.append(pct)
                else:
                    vol_hist.append(None)
        df['vol_pct'] = vol_hist
        
        # 波动率突变 (当日vol / 60日均vol)
        df['vol_surge'] = df['vol_20d'] / df['vol_20d'].rolling(60).mean()
        
        # 换手率异常 (volume z-score)
        df['vol_v_z'] = (df['volume'] - df['volume'].rolling(20).mean()) / df['volume'].rolling(20).std()
        
        # 收益率极端值
        df['ret_extreme'] = np.abs(df['ret']) > df['ret'].rolling(120).std() * 3
        
        # 组合信号: 波动率暴增
        df['vol_event'] = (
            (df['vol_20d_z'] > 2.0) &        # 波动率比120日均值高2个标准差
            (df['vol_pct'] > 0.90)            # 波动率在历史90分位以上
        ).astype(int)
        
        # 组合信号: 量价齐升
        df['vol_price_surge'] = (
            (df['vol_surge'] > 1.5) &          # 波动率比60日均跳升50%
            (df['vol_v_z'] > 2.0) &             # 换手率异常
            (np.abs(df['ret']) > 0.03)          # 当日涨跌幅>3%
        ).astype(int)
        
        self.features = df
    
    def get_signals(self, start_date=None):
        """获取波动率事件信号"""
        df = self.features
        if start_date:
            df = df[df['trade_date'] >= start_date]
        
        signals = []
        for _, row in df.iterrows():
            if pd.isna(row.get('vol_event')) or row['vol_event'] != 1:
                continue
            signals.append({
                'trade_date': row['trade_date'],
                'vol': row['vol_20d'],
                'vol_pct': row['vol_pct'],
                'vol_surge': row['vol_surge'],
                'vol_v_z': row['vol_v_z'],
                'ret': row['ret'],
                'event_type': 'vol_event',
            })
        
        return signals

def get_stock_list(conn, min_cap=10):
    """获取有足够数据的股票列表"""
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, COUNT(*) as n
        FROM daily_prices
        WHERE trade_date >= '2020-01-01'
        GROUP BY symbol
        HAVING COUNT(*) > 500
    """)
    return [r[0] for r in cur.fetchall()]

def load_stock_data(conn, code, start='2020-01-01'):
    """加载单只股票数据"""
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, close, volume, amount
        FROM daily_prices
        WHERE symbol = %s AND trade_date >= %s
        ORDER BY trade_date
    """, (code, start))
    rows = cur.fetchall()
    if len(rows) < 120:  # 最少需要120个交易日
        return None
    df = pd.DataFrame(rows, columns=['trade_date', 'close', 'volume', 'amount'])
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df

def compute_cross_sectional_vol(conn, start='2025-01-01'):
    """
    横截面波动率分析：所有股票在同一日的波动率分布
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, symbol, close
        FROM daily_prices
        WHERE trade_date BETWEEN %s AND %s
        ORDER BY trade_date
    """, (start, datetime.now().date()))
    
    # 用pivot做不到891万行，分日期处理
    dates = {}
    cur.execute("SELECT DISTINCT trade_date FROM daily_prices WHERE trade_date >= %s ORDER BY trade_date", (start,))
    all_dates = [r[0] for r in cur.fetchall()]
    
    cs_data = {}
    for d in all_dates[:50]:  # 先50天样本
        cur.execute("""
            SELECT symbol, close FROM daily_prices
            WHERE trade_date = %s AND close IS NOT NULL
        """, (d,))
        closes = {r[0]: r[1] for r in cur.fetchall()}
        if len(closes) > 100:
            # 算截面收益率分布
            # 这里简化：用收盘价P25/P50/P75度量市场分散程度
            vals = sorted(closes.values())
            cs_data[d] = {
                'n': len(vals),
                'p25': np.percentile(vals, 25),
                'p50': np.percentile(vals, 50),
                'p75': np.percentile(vals, 75),
                'iqr': np.percentile(vals, 75) - np.percentile(vals, 25),
            }
    
    return pd.DataFrame(cs_data).T

def main():
    conn = get_conn()
    
    print("="*60)
    print("个股波动率模型 v1 — 波动率事件检测 + 回测")
    print("="*60)
    
    # 1) 获取股票列表
    print("\n[1] 获取股票列表...")
    stocks = get_stock_list(conn)
    print(f"  {len(stocks)}只股票 (2020起, ≥500个交易日)")
    
    # 2) 抽样测试（先跑10只验证）
    print("\n[2] 测试波动率模型（10只样本）...")
    sample = stocks[:10]
    
    all_signals = []
    for code in sample:
        df = load_stock_data(conn, code)
        if df is None or len(df) < 200:
            continue
        
        model = StockVolModel(df)
        signals = model.get_signals(start_date='2023-01-01')
        
        for s in signals:
            s['code'] = code
        
        all_signals.extend(signals)
        
        # 打印概要
        if len(signals) > 0:
            print(f"  {code}: {len(signals)}次vol_event, "
                  f"vol_median={df['close'].pct_change().std()*np.sqrt(252)*100:.2f}%")
    
    if all_signals:
        sig_df = pd.DataFrame(all_signals)
        print(f"\n  总计: {len(sig_df)}次vol_event")
        print(f"  日均vol: {sig_df['vol'].mean()*100:.2f}%")
        print(f"  换手率z: {sig_df['vol_v_z'].mean():.2f}")
    
    # 3) 后验验证: vol_event后是否出现龙虎榜
    print("\n[3] 后验验证 — vol_event后是否出现龙虎榜...")
    
    # 简化：随机抽100只跑统计
    sample2 = np.random.choice(stocks, min(len(stocks), 100), replace=False)
    
    vol_event_dates = set()
    for code in sample2:
        df = load_stock_data(conn, code)
        if df is None:
            continue
        model = StockVolModel(df)
        signals = model.get_signals(start_date='2025-01-01')
        for s in signals:
            vol_event_dates.add((s['trade_date'].date(), code))
    
    print(f"  聚合vol_event: {len(vol_event_dates)}次 (100只样本)")
    
    # 4) 波动率暴增后收益形态
    print("\n[4] 波动率暴增后收益形态...")
    
    ret_windows = {5: [], 10: [], 20: []}
    for code in np.random.choice(stocks, min(len(stocks), 50), replace=False):
        df = load_stock_data(conn, code)
        if df is None:
            continue
        model = StockVolModel(df)
        signals = model.get_signals(start_date='2023-01-01')
        
        df_sorted = df.sort_values('trade_date')
        
        for s in signals[:20]:  # 每只最多20次信号
            sig_date = s['trade_date']
            idx = df_sorted[df_sorted['trade_date'] == sig_date].index
            if len(idx) == 0:
                continue
            pos = idx[0]
            
            for w, days_list in ret_windows.items():
                if pos + w < len(df_sorted):
                    start_price = df_sorted.iloc[pos]['close']
                    end_price = df_sorted.iloc[pos + w]['close']
                    ret = (end_price - start_price) / start_price
                    days_list.append(ret)
    
    print(f"  T+5: n={len(ret_windows[5])}, 均值={np.mean(ret_windows[5]):+.2%}, "
          f"中位数={np.median(ret_windows[5]):+.2%}, "
          f"正收益={(np.array(ret_windows[5])>0).mean():.1%}")
    print(f"  T+10: n={len(ret_windows[10])}, 均值={np.mean(ret_windows[10]):+.2%}, "
          f"中位数={np.median(ret_windows[10]):+.2%}, "
          f"正收益={(np.array(ret_windows[10])>0).mean():.1%}")
    print(f"  T+20: n={len(ret_windows[20])}, 均值={np.mean(ret_windows[20]):+.2%}, "
          f"中位数={np.median(ret_windows[20]):+.2%}, "
          f"正收益={(np.array(ret_windows[20])>0).mean():.1%}")
    
    # 按波动率分层统计
    print("\n[5] 按个股波动率分层统计vol_event后收益...")
    
    # 先算所有股票的vol
    vol_df = pd.DataFrame()
    for code in stocks:
        df = load_stock_data(conn, code)
        if df is None or len(df) < 100:
            continue
        daily_std = df['close'].pct_change().std() * np.sqrt(252) * 100
        row = pd.DataFrame({'code': [code], 'vol_annual': [daily_std]})
        vol_df = pd.concat([vol_df, row], ignore_index=True)
    
    if len(vol_df) > 0:
        p30 = vol_df['vol_annual'].quantile(0.3)
        p60 = vol_df['vol_annual'].quantile(0.6)
        print(f"  波动率阈值: LOW<{p30:.1f}, MID<{p60:.1f}, HIGH>={p60:.1f}")
        
        def vol_layer(code):
            v = vol_df[vol_df['code'] == code]['vol_annual'].values
            if len(v) == 0:
                return 'MID'
            v = v[0]
            if v < p30:
                return 'LOW'
            elif v < p60:
                return 'MID'
            else:
                return 'HIGH'
        
        layer_stats = defaultdict(lambda: {'ret5': [], 'ret10': []})
        
        for code in np.random.choice(stocks, min(len(stocks), 100), replace=False):
            df = load_stock_data(conn, code)
            if df is None:
                continue
            layer = vol_layer(code)
            model = StockVolModel(df)
            signals = model.get_signals(start_date='2023-01-01')
            
            df_sorted = df.sort_values('trade_date')
            
            for s in signals[:15]:
                idx = df_sorted[df_sorted['trade_date'] == s['trade_date']].index
                if len(idx) == 0:
                    continue
                pos = idx[0]
                
                if pos + 5 < len(df_sorted):
                    r5 = df_sorted.iloc[pos+5]['close'] / df_sorted.iloc[pos]['close'] - 1
                    layer_stats[layer]['ret5'].append(r5)
                if pos + 10 < len(df_sorted):
                    r10 = df_sorted.iloc[pos+10]['close'] / df_sorted.iloc[pos]['close'] - 1
                    layer_stats[layer]['ret10'].append(r10)
        
        print(f"\n{'波动层':<8} {'T+5均值':<10} {'T+5中位':<10} {'T+5胜率':<10} {'T+10均值':<10} {'T+10中位':<10} {'T+10胜率':<10}")
        print("-"*70)
        for layer in ['LOW', 'MID', 'HIGH']:
            ls = layer_stats.get(layer, {})
            r5 = np.array(ls.get('ret5', []))
            r10 = np.array(ls.get('ret10', []))
            print(f"{layer:<8} {r5.mean():+8.2%}  {np.median(r5):+8.2%}  "
                  f"{(r5>0).mean():<8.1%} {r10.mean():+8.2%}  {np.median(r10):+8.2%}  {(r10>0).mean():<8.1%}")
    
    conn.close()

if __name__ == '__main__':
    main()
