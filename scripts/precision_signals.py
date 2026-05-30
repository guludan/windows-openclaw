"""
个股波动率模型 v3 — 精确信号体系

维度: 波动率(vol) / 换手率(turnover) / 成交额(amount) / 价格(close)
组合信号类型:

S1: 波动率暴增(3σ) + 换手率正常 → 未定价的潜在事件 (Phase α)
S2: 波动率暴增 + 换手率暴增(3σ) + 涨幅>5% → 公开脉冲 (Phase β)
S3: 波动率暴增 + 换手率暴增 + 跌幅>5% → 恐慌出逃
S4: 换手率持续高位 + 价格不动 → 出货迹象 (Phase γ)
S5: 成交额暴增 + 低换手率 → 机构大单 (Phase α深)
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import numpy as np
import pandas as pd
import json, os
from datetime import datetime, timedelta

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT = '/home/tearsyu/.openclaw/workspace/memory/reports/'

def get_conn():
    return psycopg2.connect(DB)

def extract_signals(conn, sample_n=300, start='2022-01-01'):
    """
    批量提取精确信号
    
    用每天的单只股票close/volume/amount数据，
    计算多个维度信号后标记精确事件类型
    """
    cur = conn.cursor()
    
    # 获取股票
    cur.execute("""
        SELECT symbol FROM daily_prices
        WHERE trade_date >= %s
        GROUP BY symbol HAVING COUNT(*) > 500
        ORDER BY symbol
    """, (start,))
    all_stocks = [r[0] for r in cur.fetchall()]
    np.random.seed(42)
    sample = list(np.random.choice(all_stocks, min(sample_n, len(all_stocks)), replace=False))
    print(f"  抽样: {len(sample)}只 / {len(all_stocks)}只")
    
    # 批量加载
    batch_size = 30
    all_data = []
    
    for i in range(0, len(sample), batch_size):
        batch = sample[i:i+batch_size]
        placeholders = ','.join(['%s'] * len(batch))
        cur.execute(f"""
            SELECT symbol, trade_date, close, volume, amount
            FROM daily_prices
            WHERE symbol IN ({placeholders}) AND trade_date >= '2020-01-01'
            ORDER BY symbol, trade_date
        """, batch)
        all_data.extend(cur.fetchall())
        if i % 150 == 0:
            print(f"    加载: {i+len(batch)}/{len(sample)}")
    
    df = pd.DataFrame(all_data, columns=['symbol', 'trade_date', 'close', 'volume', 'amount'])
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    
    signal_list = []
    
    for sym, grp in df.groupby('symbol'):
        grp = grp.sort_values('trade_date').reset_index(drop=True)
        grp['ret'] = grp['close'].pct_change()
        grp['log_ret'] = np.log(grp['close'] / grp['close'].shift(1))
        
        n = len(grp)
        if n < 100:
            continue
        
        # 波动率(20日年化)
        grp['vol_20d'] = grp['log_ret'].rolling(20).std() * np.sqrt(252)
        
        # vol z-score (120日)
        vol_ma = grp['vol_20d'].rolling(120).mean()
        vol_std = grp['vol_20d'].rolling(120).std(ddof=1)
        grp['vol_z'] = (grp['vol_20d'] - vol_ma) / vol_std
        
        # 换手率 (用volume代替，因为turnover列可能为空)
        grp['v_z'] = (grp['volume'] - grp['volume'].rolling(20).mean()) / grp['volume'].rolling(20).std().clip(lower=1)
        
        # 成交额 z-score
        grp['amt_z'] = (grp['amount'] - grp['amount'].rolling(20).mean()) / grp['amount'].rolling(20).std().clip(lower=1)
        
        # 价格位置 (60日z-score)
        grp['price_z'] = (grp['close'] - grp['close'].rolling(60).mean()) / grp['close'].rolling(60).std().clip(lower=1)
        
        # 连续特征: 换手率持续高位 (最近5日中位数vol)
        grp['v_z_5d_med'] = grp['v_z'].rolling(5).median()
        
        # ─── 信号标记 (不用vol_pct，只用z-score)───
        # S1: 波动率暴增(3σ) + 换手率正常
        grp['S1_vol_surge'] = (
            (grp['vol_z'] > 3.0) &
            (np.abs(grp['v_z']) < 1.5)
        ).astype(int)
        
        # S2: 公开脉冲 — 波动率暴增 + 换手率暴增 + 大涨
        grp['S2_pulse'] = (
            (grp['vol_z'] > 2.5) &
            (grp['v_z'] > 2.0) &
            (grp['ret'] > 0.05)
        ).astype(int)
        
        # S3: 恐慌出逃 — 波动率暴增 + 换手率暴增 + 大跌
        grp['S3_panic'] = (
            (grp['vol_z'] > 2.5) &
            (grp['v_z'] > 2.0) &
            (grp['ret'] < -0.05)
        ).astype(int)
        
        # S4: 出货 — 换手率持续高位 + 价格不动
        grp['S4_dump'] = (
            (grp['v_z_5d_med'] > 2.0) &  # 近5日换手中位数偏高
            (np.abs(grp['price_z']) < 0.5) &  # 价格在60日均线附近
            (grp['v_z'] > 0) &  # 当日还在放量
            (np.abs(grp['ret']) < 0.02)  # 价格不涨
        ).astype(int)
        
        # S5: 机构大单 — 成交额暴增 + 低换手率(说明是大单而非散户)
        grp['S5_inst_buy'] = (
            (grp['amt_z'] > 2.5) &   # 成交额暴增
            (grp['v_z'] < 1.0) &     # 换手率正常偏低
            (grp['ret'] > 0.02) &    # 价格上涨
            (grp['vol_z'] > 1.0)     # 波动率也有异动
        ).astype(int)
        
        # 提取
        signal_types = ['S1_vol_surge', 'S2_pulse', 'S3_panic', 'S4_dump', 'S5_inst_buy']
        
        for stype in signal_types:
            if stype not in grp.columns:
                continue
            
            mask = grp[stype] == 1
            if not mask.any():
                continue
            
            sig_idx = grp.index[mask]
            
            for idx in sig_idx[:10]:  # 每只股票每个信号类型最多取10次
                if idx < 60 or idx >= n - 20:
                    continue
                
                def fr(days):
                    if idx + days < n:
                        return float(grp.iloc[idx+days]['close'] / grp.iloc[idx]['close'] - 1)
                    return None
                
                signal_list.append({
                    'symbol': sym,
                    'trade_date': grp.iloc[idx]['trade_date'],
                    'signal_type': stype,
                    'vol_z': float(grp.iloc[idx]['vol_z']),
                    'v_z': float(grp.iloc[idx]['v_z']),
                    'amt_z': float(grp.iloc[idx]['amt_z']),
                    'ret_0': float(grp.iloc[idx]['ret']),
                    'price_z': float(grp.iloc[idx]['price_z']),
                    'ret_3': fr(3),
                    'ret_5': fr(5),
                    'ret_10': fr(10),
                    'ret_20': fr(20),
                })
    
    sig = pd.DataFrame(signal_list)
    return sig

def main():
    conn = get_conn()
    
    print("="*60)
    print("精确信号体系 v3")
    print("="*60)
    
    # 1) 提取信号
    print("\n[1] 提取精确信号...")
    sig = extract_signals(conn, sample_n=200)
    print(f"  总信号: {len(sig)}次")
    
    if len(sig) < 10:
        conn.close()
        print("信号太少退出")
        return
    
    # 2) 各信号类型统计
    print("\n[2] 信号类型分布: ")
    for stype, grp in sig.groupby('signal_type'):
        print(f"  {stype}: {len(grp)}次")
    
    # 3) 各信号类型的收益形态
    print("\n[3] 各信号类型收益形态: ")
    print(f"\n{'信号类型':<18} {'次数':<6} {'T+3均值':<10} {'T+3中位':<10} {'T+3胜率':<8} "
          f"{'T+10均值':<10} {'T+10中位':<10} {'T+10胜率':<8} "
          f"{'T+20均值':<10} {'T+20中位':<10} {'T+20胜率':<8}")
    print("-"*105)
    
    for stype in ['S1_vol_surge', 'S2_pulse', 'S3_panic', 'S4_dump', 'S5_inst_buy']:
        grp = sig[sig['signal_type'] == stype]
        if len(grp) < 3:
            continue
        
        row = f"{stype:<18} {len(grp):<6} "
        for w in ['ret_3', 'ret_10', 'ret_20']:
            vals = grp[w].dropna()
            if len(vals) > 0:
                row += f"{vals.mean():+8.2%}  {np.median(vals):+8.2%}  {(vals>0).mean():<7.1%} "
            else:
                row += "   N/A       N/A       N/A    "
        print(row)
    
    # 4) 信号对比
    print("\n[4] 关键对比 — 买入信号 vs 卖出信号...")
    
    buy_signals = sig[sig['signal_type'].isin(['S5_inst_buy', 'S1_vol_surge'])]
    sell_signals = sig[sig['signal_type'].isin(['S3_panic', 'S4_dump'])]
    
    for label, grp in [('买入信号(S5+S1)', buy_signals), 
                        ('卖出信号(S3+S4)', sell_signals),
                        ('公开脉冲(S2)', sig[sig['signal_type']=='S2_pulse'])]:
        if len(grp) < 5:
            continue
        print(f"\n  {label} ({len(grp)}次):")
        for w in ['ret_3', 'ret_5', 'ret_10', 'ret_20']:
            vals = grp[w].dropna()
            if len(vals) > 0:
                print(f"    {w}: 均值={vals.mean():+.2%}, 中位数={np.median(vals):+.2%}, "
                      f"正收益={(vals>0).mean():.1%}")
    
    # 5) 信号条件优化建议
    print("\n[5] 信号质量评估 & 优化建议...")
    for stype in ['S1_vol_surge', 'S2_pulse', 'S3_panic', 'S4_dump', 'S5_inst_buy']:
        grp = sig[sig['signal_type'] == stype]
        if len(grp) < 10:
            continue
        
        # 计算信号的信息比率
        ret20 = grp['ret_20'].dropna()
        if len(ret20) > 0:
            ir = ret20.mean() / (ret20.std() + 1e-8)
            pos_rate = (ret20 > 0).mean()
            print(f"  {stype}: {len(grp)}次, T+20IR={ir:.2f}, 胜率={pos_rate:.1%}")
            
            # 更优的条件
            if pos_rate < 0.40:
                print(f"    → 卖出信号有效（胜率<40%即为有效看跌）")
            elif pos_rate > 0.60:
                print(f"    → 买入信号有效（胜率>60%即为有效看涨）")
            else:
                print(f"    → 信号混杂，需要二级条件过滤")
    
    conn.close()

if __name__ == '__main__':
    main()
