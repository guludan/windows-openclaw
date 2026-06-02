#!/usr/bin/env python3
"""
庄家行为策略回测框架 v2 — 优化版
核心策略验证 + V8联动分析
"""
import sys, os, json, psycopg2
import numpy as np
import pandas as pd
from datetime import date, datetime, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
MIN_FLOAT_MV = 10
HISTORY_DAYS = 120

def get_regime_map(conn):
    """返回 {date: regime} 快速字典"""
    cur = conn.cursor()
    cur.execute("SELECT date, extra FROM pool_energy WHERE level='market' AND extra IS NOT NULL ORDER BY date")
    reg = {}
    for r in cur.fetchall():
        e = r[1] if isinstance(r[1], dict) else (json.loads(r[1]) if isinstance(r[1], str) else {})
        reg[r[0]] = e.get('regime', 'SIDEWAYS')
    return reg

def signal_one_stock(df, regime_map):
    """
    对一只股票打出所有信号
    返回 DataFrame 每一行 = 一个交易日，含信号列
    """
    n = len(df)
    if n < HISTORY_DAYS:
        return pd.DataFrame()
    
    r1d = df['close'].pct_change()
    yang = df['close'] >= df['open']
    
    # 滚动指标
    yang_20 = yang.rolling(20).mean()
    vol_10 = df['volume'].rolling(10).mean()
    vol_60 = df['volume'].rolling(60).mean()
    vol_ratio = vol_10 / vol_60.replace(0, np.nan).ffill()
    amp_20 = ((df['high'] - df['low']) / df['open'].replace(0, np.nan).ffill()).rolling(20).mean()
    
    # 上影线比例
    candle_range = df['high'] - df['low']
    upper_shadow = df['high'] - df[['open','close']].max(axis=1)
    lower_shadow = df[['open','close']].min(axis=1) - df['low']
    
    shadow_ratio = upper_shadow / candle_range.replace(0, np.nan).ffill()
    
    # 量比
    vol_ma20 = df['volume'].rolling(20).mean()
    vol_ratio_today = df['volume'] / vol_ma20.replace(0, np.nan).ffill()
    
    # SMA偏离
    sma120 = df['close'].rolling(120).mean()
    sma_dev = df['close'] / sma120.replace(0, np.nan).ffill() - 1
    
    # 前60天线最高价
    high_60 = df['high'].rolling(60).max().shift(1)
    
    # 前10天长上影线计数
    shadow_cond = (shadow_ratio > 0.4) & (candle_range > 0)
    shadow_count_10 = shadow_cond.rolling(10).sum()
    
    # 连跌计数
    conseq_dn = np.zeros(n, dtype=int)
    for i in range(1, n):
        if r1d.iloc[i] < 0:
            conseq_dn[i] = conseq_dn[i-1] + 1
    
    # 总跌幅（连跌期间）
    total_decline = np.zeros(n)
    for i in range(1, n):
        if conseq_dn[i] > 0:
            total_decline[i] = df['close'].iloc[i] / df['close'].iloc[i - conseq_dn[i]] - 1
    
    # regime按日期对齐
    regime_series = df['trade_date'].map(regime_map).fillna('SIDEWAYS').values
    
    # 阶段判定（向量化条件）
    is_accumulating = (yang_20 > 0.55) & (vol_ratio.between(1.0, 1.8)) & (amp_20 < 0.05)
    
    # 试盘期：长上影+冲高回落+放量
    is_test_push = (shadow_ratio > 0.5) & (upper_shadow > lower_shadow) & \
                   (df['close'] < df['open']) & (r1d > -0.03) & (r1d < 0.05) & \
                   (vol_ratio_today > 1.5) & (upper_shadow / df['close'].replace(0, np.nan).ffill() > 0.03)
    
    # 拉升期：阳线多+放量+突破60日高
    yang_5 = yang.rolling(5).sum()
    is_pump = (yang_5 >= 3) & (vol_ratio > 1.1) & (r1d > 0) & (r1d < 0.105) & (amp_20 > 0.035)
    is_pump = is_pump & (df['high'] > high_60 * 0.95)
    
    # 出货期：放量滞涨+长上影多
    past_5_vol = df['volume'].rolling(5).mean()
    past_20_vol = df['volume'].rolling(20).mean()
    vol_increase = past_5_vol / past_20_vol.replace(0, np.nan).ffill()
    
    is_distribute = (vol_increase > 1.3) & (shadow_count_10 >= 3)
    
    # 洗盘期：连跌+放量下跌+后续缩量
    is_wash = pd.Series(np.zeros(n, dtype=bool))
    for i in range(HISTORY_DAYS, n):
        if not is_accumulating.iloc[i] and not is_test_push.iloc[i] and not is_pump.iloc[i]:
            continue
        if conseq_dn[i] >= 2 and r1d.iloc[i] < -0.03:
            # 检查后续缩量
            if i + 3 < n:
                post_vol = df['volume'].iloc[i:i+3]
                pre_vol = df['volume'].iloc[max(0,i-5):i].mean()
                if post_vol.max() < 0.4 * max(pre_vol, 1):
                    is_wash.iloc[i] = True
    
    # 合并信号
    df_result = df[['trade_date','close']].copy()
    df_result['regime'] = regime_series
    
    # 阶段优先级：出货 > 拉升 > 洗盘 > 试盘 > 建仓 > 无主力
    stage = pd.Series(['无主力特征'] * n)
    stage[is_wash] = '洗盘期'
    stage[is_test_push] = '试盘期'
    stage[is_accumulating & (stage == '无主力特征')] = '建仓期'
    stage[is_pump] = '拉升期'
    stage[is_distribute] = '出货期'
    
    df_result['stage'] = stage.values
    df_result['sma_dev'] = sma_dev.values
    df_result['yang_20'] = yang_20.values
    df_result['vol_ratio'] = vol_ratio.values
    df_result['amp_20'] = amp_20.values
    
    # 后续收益
    df_result['ret_5'] = df['close'].pct_change(5).shift(-5).values
    df_result['ret_10'] = df['close'].pct_change(10).shift(-10).values
    df_result['ret_20'] = df['close'].pct_change(20).shift(-20).values
    df_result['ret_40'] = df['close'].pct_change(40).shift(-40).values
    
    return df_result

def run_backtest(sample_pct=0.20):
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    regime_map = get_regime_map(conn)
    print(f"Regime数据: {len(regime_map)}天")
    regime_counts = pd.Series(list(regime_map.values())).value_counts()
    print(f"分布: {regime_counts.to_dict()}")
    
    # 选股
    th = pd.to_datetime(TEST_START) if 'TEST_START' not in dir() else pd.to_datetime('2022-01-01')
    cur.execute(f"""
        SELECT code FROM market_snapshots 
        WHERE trade_date = (SELECT max(trade_date) FROM market_snapshots)
        AND float_mv >= {MIN_FLOAT_MV}
    """)
    all_codes = [r[0] for r in cur.fetchall()]
    
    if sample_pct < 1.0:
        import random
        random.seed(42)
        sample_size = max(500, int(len(all_codes) * sample_pct))
        all_codes = random.sample(all_codes, min(sample_size, len(all_codes)))
    
    print(f"待分析: {len(all_codes)}只")
    
    # 批量加载日线
    all_signals = []
    
    for idx, sc in enumerate(all_codes):
        sym = f"A.{sc}"
        cur.execute(f"""
            SELECT trade_date, close, open, high, low, volume 
            FROM daily_prices WHERE symbol='{sym}' AND close > 0 
            ORDER BY trade_date
        """)
        rows = cur.fetchall()
        if len(rows) < HISTORY_DAYS + 40:
            continue
        
        df = pd.DataFrame(rows, columns=['trade_date','close','open','high','low','volume'])
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        
        sig = signal_one_stock(df, regime_map)
        if len(sig) == 0:
            continue
        sig['code'] = sc
        all_signals.append(sig)
        
        if (idx + 1) % 100 == 0:
            print(f"  [{idx+1}/{len(all_codes)}] 已收集 {sum(len(s) for s in all_signals)} 条信号")
    
    if not all_signals:
        print("没有信号！")
        conn.close()
        return pd.DataFrame()
    
    df = pd.concat(all_signals, ignore_index=True)
    print(f"\n总信号: {len(df)}条")
    print(f"日期范围: {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")
    
    # === 统计分析 ===
    stage_freq = df['stage'].value_counts()
    print(f"\n## 阶段频率分布")
    for s, c in stage_freq.items():
        print(f"  {s}: {c} ({c/len(df)*100:.1f}%)")
    
    print(f"\n## 各阶段T+20收益")
    for stage_name in ["建仓期", "洗盘期", "试盘期", "拉升期", "出货期", "无主力特征"]:
        sub = df[df['stage'] == stage_name]
        if len(sub) < 10:
            continue
        rets = sub['ret_20'].dropna()
        win_rate = np.mean(rets > 0) if len(rets) > 0 else 0
        print(f"  {stage_name:6s} (n={len(sub):>8d}): T+20中位={np.median(rets):+.2%} 胜率={win_rate:.1%}")
    
    # 不同regime交叉
    print(f"\n## Regime × 阶段 T+20收益")
    for reg in ['BULL', 'BEAR', 'SIDEWAYS']:
        sub = df[df['regime'] == reg]
        if len(sub) < 10:
            continue
        print(f"\n### {reg} ({len(sub)}条)")
        for stage_name in ["建仓期", "洗盘期", "试盘期", "拉升期", "出货期", "无主力特征"]:
            ss = sub[sub['stage'] == stage_name]
            if len(ss) < 5:
                continue
            rets = ss['ret_20'].dropna()
            print(f"  {stage_name:6s}: T+20={np.median(rets):+.2%} 胜率={np.mean(rets>0):.1%} (n={len(rets)})")
    
    # V8联动（sma_dev < -15%作为近似）
    print(f"\n## V8超跌联动 (sma_dev < -15%)")
    df_v8 = df[df['sma_dev'] < -0.15].copy()
    print(f"V8信号数: {len(df_v8)}")
    
    for stage_name in ["建仓期", "洗盘期", "试盘期", "出货期", "无主力特征"]:
        ss = df_v8[df_v8['stage'] == stage_name]
        if len(ss) < 5:
            continue
        rets = ss['ret_20'].dropna()
        print(f"  V8+{stage_name:6s}: T+20={np.median(rets):+.2%} 胜率={np.mean(rets>0):.1%} (n={len(rets)})")
    
    # 也检查非V8的表现
    df_nv8 = df[df['sma_dev'] >= -0.15].copy()
    print(f"\n  非V8信号数: {len(df_nv8)}")
    for stage_name in ["建仓期", "洗盘期", "试盘期", "拉升期", "出货期"]:
        ss = df_nv8[df_nv8['stage'] == stage_name]
        if len(ss) < 5:
            continue
        rets = ss['ret_20'].dropna()
        print(f"  非V8+{stage_name:6s}: T+20={np.median(rets):+.2%} 胜率={np.mean(rets>0):.1%} (n={len(rets)})")
    
    # 保存
    df.to_pickle(f"{OUT_DIR}/bt_v2_{date.today()}.pkl")
    
    conn.close()
    return df

if __name__ == '__main__':
    OUT_DIR = '/mnt/d/data/backtest'
    TEST_START = '2022-01-01'
    os.makedirs(OUT_DIR, exist_ok=True)
    df = run_backtest(sample_pct=0.20)
