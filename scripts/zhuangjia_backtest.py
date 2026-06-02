#!/usr/bin/env python3
"""
庄家行为策略回测框架
验证四大战术策略的历史胜率，以及与regime/V8的联动效果
分阶段运行：先用采样版验证特征，再决定是否跑全量
"""
import sys, os, json, psycopg2, pickle
import numpy as np
import pandas as pd
from datetime import date, datetime, timedelta
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT_DIR = '/mnt/d/data/backtest'

# 参数
MIN_FLOAT_MV = 10      # 过滤微盘股
HISTORY_DAYS = 120     # 每只股票需要的历史日线天数
TEST_START = '2018-01-01'
TEST_END   = '2026-06-02'

os.makedirs(OUT_DIR, exist_ok=True)

def get_pool_energy(conn):
    """加载pool_energy大盘数据"""
    cur = conn.cursor()
    # 加载surplus序列 + regime
    cur.execute("""
        SELECT date, surplus, b_pool, a_pool, phase, extra
        FROM pool_energy WHERE level='market'
        ORDER BY date
    """)
    rows = cur.fetchall()
    
    dates = [r[0] for r in rows]
    surplus = np.array([float(r[1]) for r in rows])
    b_pool = np.array([float(r[2]) for r in rows])
    a_pool = np.array([float(r[3]) for r in rows])
    phase = [r[4] for r in rows]
    
    # regime从extra解析
    regime = {}
    for r in rows:
        try:
            e = r[5] if isinstance(r[5], dict) else (json.loads(r[5]) if isinstance(r[5], str) else {})
            if 'regime' in e:
                regime[r[0]] = e['regime']
        except: pass
    
    return dates, surplus, b_pool, a_pool, phase, regime

def get_regime_at(regime_dict, d):
    """获取某个日期的regime"""
    dates = sorted(regime_dict.keys())
    for dt in reversed(dates):
        if dt <= d:
            return regime_dict[dt]
    return 'SIDEWAYS'

def analyze_stock(df, regime_at_date):
    """
    对一只股票的历史数据，打标每个交易日的阶段
    返回: list of dict {date, stage, signals, price, regime, ...}
    """
    n = len(df)
    if n < HISTORY_DAYS:
        return []
    
    results = []
    
    closes = df['close'].values.astype(float)
    opens = df['open'].values.astype(float) if 'open' in df else closes.copy()
    highs = df['high'].values.astype(float) if 'high' in df else closes.copy()
    lows = df['low'].values.astype(float) if 'low' in df else closes.copy()
    volumes = df['volume'].values.astype(float) if 'volume' in df else np.ones(n)
    dates_list = list(df['trade_date'])
    
    r1d = np.full(n, np.nan)
    r1d[1:] = closes[1:] / closes[:-1] - 1
    
    # 阳线判定
    yang = closes >= opens
    
    # 各阶段信号记录
    for i in range(HISTORY_DAYS, n):
        d = dates_list[i]
        reg = get_regime_at(regime_at_date, d)
        is_bear = reg in ('BEAR', 'BOTTOM_WARNING')
        
        # 过去20天的指标
        idx20 = slice(i-19, i+1)
        yang_20 = np.mean(yang[idx20])
        
        # 量比
        vol_10 = np.mean(volumes[i-9:i+1])
        vol_60 = np.mean(volumes[max(0,i-59):i+1])
        vol_ratio = vol_10 / max(vol_60, 1)
        
        # 20日振幅
        amp_20 = np.mean([(highs[j]-lows[j])/max(opens[j],0.01) for j in range(max(0,i-19), i+1)])
        
        # 连跌
        conseq_dn = 0
        total_decline = 0.0
        for k in range(i, max(0, i-5), -1):
            if not np.isnan(r1d[k]) and r1d[k] < 0:
                conseq_dn += 1
                total_decline = (1 + total_decline) * (1 + r1d[k]) - 1
            else:
                break
        
        stage = "无主力特征"
        signals = []
        
        # 参照参数
        if is_bear:
            wash_depth = -0.05
            wash_vol_factor = 2.0
        else:
            wash_depth = -0.03
            wash_vol_factor = 1.8
        
        # === 建仓期 ===
        if (yang_20 > 0.55 and 1.2 <= vol_ratio <= 1.8 and amp_20 < 0.05):
            # 简化版——不检查逆势抗跌（太耗时）
            stage = "建仓期"
            signals.append("build")
        
        # === 洗盘期(建仓期内出现放量下跌后缩量回升) ===
        if stage == "建仓期" or True:  # 也检查非建仓期的洗盘
            for j in range(max(HISTORY_DAYS, i-10), i+1):
                if (conseq_dn >= 2 and r1d[i] < wash_depth and 
                    volumes[i] > wash_vol_factor * np.mean(volumes[max(0,i-5):i])):
                    # 向前检查后续缩量
                    post_vol_max = 0
                    for k in range(i+1, min(n, i+4)):
                        if volumes[k] < 0.3 * volumes[i]:
                            post_vol_max = max(post_vol_max, volumes[k])
                    if post_vol_max > 0:
                        post_vol_ratio = post_vol_max / max(np.mean(volumes[max(0,i-5):i]), 1)
                        if post_vol_ratio < 0.5:
                            stage = "洗盘期"
                            signals.append("wash")
                            break
        
        # === 试盘期(长上影+冲高回落) ===
        if stage == "无主力特征" or stage == "建仓期":
            candle_range = highs[i] - lows[i]
            upper_shadow = highs[i] - max(opens[i], closes[i])
            lower_shadow = min(opens[i], closes[i]) - lows[i]
            
            if (candle_range > 0 and 
                upper_shadow / max(candle_range, 0.001) > 0.5 and
                upper_shadow > lower_shadow and
                closes[i] < opens[i] and  # 收阴
                r1d[i] > -0.03 and         # 跌幅不大
                volumes[i] > 1.5 * np.mean(volumes[max(0,i-5):i]) and
                upper_shadow / max(closes[i], 0.01) > 0.03):   # 上影线>3%
                if stage == "建仓期":
                    signals.append("test_push")
                else:
                    stage = "试盘期"
                    signals.append("test_push")
        
        # === 拉升期 ===
        if stage in ("无主力特征", "建仓期"):
            i5 = max(0, i-4)
            yang_5 = np.sum(yang[i5:i+1])
            if (yang_5 >= 3 and vol_ratio > 1.1 and r1d[i] > 0 and amp_20 > 0.04 and r1d[i] < 0.105):
                high_60 = np.max(highs[max(0,i-59):i+1])
                if highs[i] >= high_60 or highs[i] > np.mean(closes[max(0,i-59):i]) * 1.1:
                    stage = "拉升期"
                    signals.append("pump" + ("_bear" if is_bear else ""))
        
        # === 出货期 ===
        if stage in ("拉升期", "无主力特征"):
            past_5_vol = np.mean(volumes[max(0,i-4):i+1])
            past_20_vol = np.mean(volumes[max(0,i-19):i+1])
            vol_increase = past_5_vol / max(past_20_vol, 1)
            
            shadow_count = 0
            for j in range(max(HISTORY_DAYS, i-9), i+1):
                cr = highs[j] - lows[j]
                us = highs[j] - max(opens[j], closes[j])
                if cr > 0 and us / cr > 0.4:
                    shadow_count += 1
            
            if (vol_increase > 1.3 and shadow_count >= 3):
                stage = "出货期"
                signals.append("distribute")
        
        results.append({
            'date': d,
            'close': closes[i],
            'stage': stage,
            'signals': signals,
            'regime': reg,
            'ret_5': (closes[i]/closes[max(0,i-5)]-1) if i >= 5 else None,
            'ret_10': (closes[i]/closes[max(0,i-10)]-1) if i >= 10 else None,
            'ret_20': (closes[i]/closes[max(0,i-20)]-1) if i >= 20 else None,
            'sma_dev': closes[i]/np.mean(closes[max(0,i-119):i+1]) - 1,
            'yang_20': yang_20,
            'vol_ratio': vol_ratio,
            'amp_20': amp_20,
            'conseq_dn': conseq_dn,
            'total_decline': total_decline,
        })
    
    return results

def backtest_one_stock(sc, rows):
    """回测一只股票——提取庄家行为阶段并与后续收益对比"""
    if len(rows) < HISTORY_DAYS + 60:
        return []
    
    df = pd.DataFrame(rows, columns=['trade_date','close','open','high','low','volume','amount'])
    df = df.sort_values('trade_date').reset_index(drop=True)
    
    conn2 = psycopg2.connect(DB)
    regime_dict = get_pool_energy(conn2)[5]
    conn2.close()
    
    results = analyze_stock(df, regime_dict)
    return results

def run_backtest(sample_pct=0.1, max_workers=4):
    """
    主回测函数
    sample_pct: 采样比例（0.1 = 10%股票，默认先用小样本验证逻辑）
    """
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # 加载pool_energy大盘数据
    pe_dates, surplus, b_pool, a_pool, phase, regime_dict = get_pool_energy(conn)
    print(f"大盘数据: {len(pe_dates)}天 ({pe_dates[0]} ~ {pe_dates[-1]})")
    print(f"Regime分布: {pd.Series(regime_dict.values()).value_counts().to_dict()}")
    
    # 获取轮动样本
    cur.execute(f"""
        SELECT code FROM market_snapshots 
        WHERE trade_date = (SELECT max(trade_date) FROM market_snapshots)
        AND float_mv >= {MIN_FLOAT_MV}
    """)
    all_codes = [r[0] for r in cur.fetchall()]
    
    # 过滤掉没有足够日线数据的
    valid_codes = []
    if sample_pct < 1.0:
        import random
        random.seed(42)
        sample_size = max(200, int(len(all_codes) * sample_pct))
        all_codes = random.sample(all_codes, min(sample_size, len(all_codes)))
    
    print(f"待分析的股票: {len(all_codes)}只")
    
    # 加载所有股票日线
    all_trades = defaultdict(list)
    batch_size = 200
    for i in range(0, len(all_codes), batch_size):
        batch = all_codes[i:i+batch_size]
        syms = ["'A."+c+"'" for c in batch]
        cur.execute(f"""
            SELECT symbol, trade_date, close, open, high, low, volume, amount
            FROM daily_prices
            WHERE symbol IN ({','.join(syms)}) AND trade_date >= '2015-01-01' AND close > 0
            ORDER BY symbol, trade_date
        """)
        for r in cur.fetchall():
            sc = r[0].replace('A.','')
            all_trades[sc].append(r[1:])
        if (i+1) % 500 == 0:
            print(f"  加载 {i+1}/{len(all_codes)}...")
    
    print(f"已加载 {len(all_trades)}只股票的数据")
    conn.close()
    
    # 逐只回测
    all_trades_records = []
    
    for sc, rows in all_trades.items():
        if len(rows) < HISTORY_DAYS + 60:
            continue
        results = backtest_one_stock(sc, rows)
        for r in results:
            r['code'] = sc
        all_trades_records.extend(results)
    
    print(f"总分析交易日: {len(all_trades_records)}")
    
    # === 统计分析 ===
    df = pd.DataFrame(all_trades_records)
    
    stats = {}
    
    # 1. 各阶段的频率统计
    stage_freq = df['stage'].value_counts()
    print(f"\n## 阶段频率分布")
    for s, c in stage_freq.items():
        print(f"  {s}: {c} ({c/len(df)*100:.1f}%)")
    
    # 2. 各阶段后续收益表现
    print(f"\n## 各阶段后续收益")
    for stage_name in ["建仓期", "洗盘期", "试盘期", "拉升期", "出货期", "无主力特征"]:
        sub = df[df['stage'] == stage_name]
        if len(sub) < 10:
            continue
        print(f"\n### {stage_name} ({len(sub)}次)")
        for window, col in [("T+5", 'ret_5'), ("T+10", 'ret_10'), ("T+20", 'ret_20')]:
            rets = sub[col].dropna()
            if len(rets) > 0:
                win_rate = np.mean(rets > 0)
                print(f"  {window}: 中位={np.median(rets):+.2%} 均值={np.mean(rets):+.2%} 胜率={win_rate:.1%}")
    
    # 3. 不同Regime下的阶段表现
    print(f"\n## Regime vs 阶段交叉分析")
    for reg in ['BULL', 'BEAR', 'SIDEWAYS']:
        sub = df[df['regime'] == reg]
        if len(sub) < 10:
            continue
        print(f"\n### {reg} regime ({len(sub)}次)")
        for stage_name in ["建仓期", "洗盘期", "试盘期", "拉升期", "出货期"]:
            ss = sub[sub['stage'] == stage_name]
            if len(ss) < 5:
                continue
            rets = ss['ret_20'].dropna()
            if len(rets) > 0:
                print(f"  {stage_name}: T+20中位={np.median(rets):+.2%} 胜率={np.mean(rets>0):.1%} (n={len(rets)})")
    
    # 4. V8关联分析（用sma_dev < -15%作为V8超跌的近似）
    print(f"\n## V8超跌联动分析 (sma_dev < -15%)")
    df_v8 = df[df['sma_dev'] < -0.15].copy()
    print(f"V8超跌信号总次数: {len(df_v8)}")
    for stage_name in ["建仓期", "洗盘期", "试盘期", "出货期"]:
        ss = df_v8[df_v8['stage'] == stage_name]
        if len(ss) < 5:
            continue
        rets = ss['ret_20'].dropna()
        if len(rets) > 0:
            print(f"  V8+{stage_name}: T+20中位={np.median(rets):+.2%} 胜率={np.mean(rets>0):.1%} (n={len(rets)})")
    
    # V8超跌但无主力特征 vs V8超跌有主力特征
    df_v8_none = df_v8[df_v8['stage'] == '无主力特征']
    df_v8_active = df_v8[df_v8['stage'] != '无主力特征']
    print(f"\n  V8无主力: {len(df_v8_none)}次, T+20={np.median(df_v8_none['ret_20'].dropna()):+.2%}")
    print(f"  V8+有主力: {len(df_v8_active)}次, T+20={np.median(df_v8_active['ret_20'].dropna()):+.2%}")
    
    # 5. 保存回测结果
    df.to_pickle(f"{OUT_DIR}/backtest_results_{date.today()}.pkl")
    print(f"\n结果已保存到 {OUT_DIR}/backtest_results_{date.today()}.pkl")
    
    # 生成报告
    report_lines = ["# 庄家行为策略回测报告", f"", f"**样本**: {len(all_trades)}只股票 | **交易日**: {len(df)}条", f"**数据范围**: {df['date'].min()} ~ {df['date'].max()}", f""]
    report_lines.append("## 阶段频率分布")
    for s, c in stage_freq.items():
        report_lines.append(f"- {s}: {c} ({c/len(df)*100:.1f}%)")
    
    report_lines.append(f"\n## 各阶段T+20收益")
    for stage_name in ["建仓期", "洗盘期", "试盘期", "拉升期", "出货期", "无主力特征"]:
        sub = df[df['stage'] == stage_name]
        if len(sub) < 10:
            continue
        rets = sub['ret_20'].dropna()
        win_rate = np.mean(rets > 0) if len(rets) > 0 else 0
        report_lines.append(f"\n### {stage_name} ({len(sub)}次)")
        report_lines.append(f"- T+20中位: {np.median(rets):+.2%} | 均值: {np.mean(rets):+.2%} | 胜率: {win_rate:.1%}")
    
    report_lines.append(f"\n## Regime交叉分析")
    for reg in ['BULL', 'BEAR', 'SIDEWAYS']:
        sub = df[df['regime'] == reg]
        report_lines.append(f"\n### {reg}")
        for stage_name in ["建仓期", "洗盘期", "试盘期", "拉升期", "出货期"]:
            ss = sub[sub['stage'] == stage_name]
            if len(ss) < 5:
                continue
            rets = ss['ret_20'].dropna()
            report_lines.append(f"- {stage_name}: T+20={np.median(rets):+.2%} 胜率={np.mean(rets>0):.1%} (n={len(rets)})")
    
    report_lines.append(f"\n## V8超跌联动(sma_dev<-15%)")
    report_lines.append(f"V8信号总数: {len(df_v8)}")
    for stage_name in ["建仓期", "洗盘期", "试盘期", "出货期", "无主力特征"]:
        ss = df_v8[df_v8['stage'] == stage_name]
        if len(ss) < 5:
            continue
        rets = ss['ret_20'].dropna()
        report_lines.append(f"- V8+{stage_name}: T+20={np.median(rets):+.2%} 胜率={np.mean(rets>0):.1%} (n={len(rets)})")
    
    report_path = f"{OUT_DIR}/backtest_report_{date.today()}.md"
    with open(report_path, 'w') as f:
        f.write('\n'.join(report_lines))
    print(f"\n报告: {report_path}")
    
    return df

if __name__ == '__main__':
    # 用20%样本快速验证（约1000只）
    df = run_backtest(sample_pct=0.20, max_workers=4)
