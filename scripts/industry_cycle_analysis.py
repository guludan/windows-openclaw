#!/usr/bin/env python3
"""
A股货币周期 × 行业轮动分析

用 M2 同比 + 信用利差 (AAA-10Y) 把 2015~2026 切成货币周期阶段，
然后对每个阶段计算同花顺90个板块的累计收益率，输出领涨/领跌板块及敏感性分析。

依赖: psycopg2, pandas, numpy
运行: /home/tearsyu/miniconda3/envs/tt/bin/python3 scripts/industry_cycle_analysis.py
"""
import sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd
from datetime import datetime, date, timedelta
from collections import defaultdict, OrderedDict

from data_finance import _pg_execute, _get_pg

# ============================================================
# 0. 配置
# ============================================================
START_YEAR = 2015
END_YEAR = 2026

# M2 阈值（结合历史：2015-16 M2>10%，2017-19 M2~8%，2020 M2~10%，2022-23 M2~11%）
# 我们直接用 M2 同比的绝对值 + 趋势综合判断
# 宽货币：M2_YOY 趋势持续上升且 > 历史均值偏上
# 紧货币：M2_YOY 趋势持续下降且 < 历史均值偏下

# 直接使用 M2 同比值的折中阈值
# 历史 M2 均值约 10%，9% 以下是偏紧区间，11% 以上是偏松区间
M2_HIGH_THRESHOLD = 10.5     # 持续高于此 = 宽松
M2_LOW_THRESHOLD = 8.5       # 持续低于此 = 紧缩

# 信用利差（AAA-10Y）：历史范围 0.1~1.65
# 宽信用：利差 < 0.8 (信用环境友好)
# 紧信用：利差 > 1.3 (信用环境紧张)
SPREAD_LOOSE = 0.8
SPREAD_TIGHT = 1.3


# ============================================================
# 1. 数据拉取
# ============================================================
def fetch_macro():
    indicators = ['CN_M2_YOY', 'CN_AAA_SPREAD']
    data = {}
    for ind in indicators:
        rows, cols = _pg_execute(
            "SELECT date, value FROM macro_indicators WHERE indicator = %s AND date >= %s ORDER BY date",
            (ind, f"{START_YEAR}-01-01")
        )
        if rows:
            df = pd.DataFrame(rows, columns=['date', 'value'])
            df['date'] = pd.to_datetime(df['date'])
            data[ind] = df
            print(f"  📊 {ind}: {len(df)} rows, {df['date'].min().date()} ~ {df['date'].max().date()}")
        else:
            print(f"  ⚠️ {ind}: no data")
    return data


def fetch_industry_prices():
    print("\n📡 拉取板块日线数据...")
    rows, cols = _pg_execute(
        "SELECT trade_date, industry, close FROM industry_daily "
        "WHERE trade_date >= %s ORDER BY trade_date, industry",
        (f"{START_YEAR}-01-01",)
    )
    print(f"  📊 板块日线: {len(rows)} 条")
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=['trade_date', 'industry', 'close'])
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    pivoted = df.pivot(index='trade_date', columns='industry', values='close')
    print(f"  ✅ 板块矩阵: {pivoted.shape[0]} 天 × {pivoted.shape[1]} 板块")
    return pivoted


# ============================================================
# 2. 周期划分（改进版：使用 M2 3MMA + 月度均值判断）
# ============================================================
def determine_cycles_v2():
    """
    改进版周期判定：
    1. M2 用 3 个月移动平均（消除月度波动）
    2. 信用利差用月度中位数
    3. 连续 3 个月同一分类才确认阶段切换
    4. 用 M2 3MMA 分层：
       - > 10.5 = 宽松
       - 8.5 ~ 10.5 = 中性（看趋势方向）
       - < 8.5 = 紧缩
    """
    print("\n" + "=" * 60)
    print("🏛️  货币周期阶段分析 (v2)")
    print("=" * 60)
    
    data = fetch_macro()
    m2 = data.get('CN_M2_YOY')
    spread = data.get('CN_AAA_SPREAD')
    
    if m2 is None or m2.empty:
        print("❌ 缺少 M2 数据")
        return []
    
    # Compute M2 3-month moving average
    m2 = m2.sort_values('date').copy()
    m2['m2_3ma'] = m2['value'].rolling(3, min_periods=1).mean()
    m2['m2_3ma_diff'] = m2['m2_3ma'].diff()
    
    # Compute monthly spread
    sp = spread.sort_values('date').copy()
    sp['month'] = sp['date'].dt.to_period('M')
    spread_monthly = sp.groupby('month')['value'].median().reset_index()
    spread_monthly['date'] = spread_monthly['month'].dt.to_timestamp()
    spread_monthly = spread_monthly.rename(columns={'value': 'spread_med'})
    
    # Merge M2 monthly with spread monthly
    merged = m2.merge(spread_monthly[['date', 'spread_med']], on='date', how='left')
    merged['spread_med'] = merged['spread_med'].ffill()
    
    # Monthly classification
    def classify_row(m2_3ma, m2_diff, spread_med):
        # Money stage
        if m2_3ma > M2_HIGH_THRESHOLD:
            if m2_diff > 0:  # 还在上升
                money = 'loose'
            elif m2_diff > -0.2:
                money = 'loose'   # 高位持平
            else:
                money = 'loose_to_tight'  # 高位回落
        elif m2_3ma < M2_LOW_THRESHOLD:
            if m2_diff < 0:
                money = 'tight'
            elif m2_diff < 0.2:
                money = 'tight'
            else:
                money = 'tight_to_loose'
        else:
            # 中性区间看方向
            if m2_diff > 0.3:
                money = 'loose'   # 从低位回升
            elif m2_diff < -0.3:
                money = 'tight'
            else:
                money = 'neutral'
        
        # Credit stage
        if spread_med < SPREAD_LOOSE:
            credit = 'loose'
        elif spread_med > SPREAD_TIGHT:
            credit = 'tight'
        else:
            credit = 'neutral'
        
        return money, credit
    
    classifications = []
    for _, row in merged.iterrows():
        m_diff = row.get('m2_3ma_diff', 0) if pd.notna(row.get('m2_3ma_diff')) else 0
        money, credit = classify_row(row['m2_3ma'], m_diff, row['spread_med'])
        classifications.append((row['date'], money, credit, row['value'], row['m2_3ma'], row['spread_med']))
    
    # Print monthly classification
    print(f"\n📋 每月周期分类:")
    print(f"{'日期':<10} {'M2%':<7} {'M2_3MA':<9} {'利差':<7} {'货币':<16} {'信用':<8} {'综合':<20}")
    print("-" * 77)
    
    # We also track smoothed composite label for stage merging
    # Use a 3-month consensus approach
    stages_raw = []
    for i, (dt, money, credit, m2v, m2ma, spv) in enumerate(classifications):
        # Simplified composite
        m_short = 'M宽松' if money == 'loose' else ('M紧缩' if money == 'tight' else 'M中性')
        c_short = 'C宽松' if credit == 'loose' else ('C紧缩' if credit == 'tight' else 'C中性')
        
        if money in ('loose', 'loose_to_tight') and credit == 'loose':
            label = '宽货币宽信用'
        elif money in ('loose', 'loose_to_tight') and credit == 'tight':
            label = '宽货币紧信用'
        elif money in ('tight', 'tight_to_loose') and credit == 'loose':
            label = '紧货币宽信用'
        elif money in ('tight', 'tight_to_loose') and credit == 'tight':
            label = '紧货币紧信用'
        elif money == 'loose':
            label = '宽货币中性信用'
        elif money == 'tight':
            label = '紧货币中性信用'
        elif credit == 'loose':
            label = '中性货币宽信用'
        elif credit == 'tight':
            label = '中性货币紧信用'
        else:
            label = '中性过渡期'
        
        stages_raw.append((dt, m_short, c_short, label))
        
        if i % 3 == 0 or (i > 100 and i % 6 == 0) or i == len(classifications) - 1:
            print(f"{dt.strftime('%Y-%m'):<10} {m2v:<7.2f} {m2ma:<9.2f} {spv:<7.3f} {m_short:<16} {c_short:<8} {label:<20}")
    
    # --- Merge consecutive same labels into stages ---
    # Use majority vote in 3-month rolling window for stability
    raw_labels = [s[3] for s in stages_raw]
    dates = [s[0] for s in stages_raw]
    
    # Smooth: take a 3-month rolling mode
    smoothed = []
    for i in range(len(raw_labels)):
        start = max(0, i - 1)
        end = min(len(raw_labels), i + 2)
        window = raw_labels[start:end]
        from collections import Counter
        most_common = Counter(window).most_common(1)[0][0]
        smoothed.append(most_common)
    
    # Merge consecutive same labels
    stages = OrderedDict()
    current_label = None
    current_start = None
    count = 0
    
    for i, (dt, label) in enumerate(zip(dates, smoothed)):
        if label != current_label:
            if current_label is not None and count >= 2:
                stages[current_label] = (current_start, dates[i-1])
            current_label = label
            current_start = dt
            count = 1
        else:
            count += 1
    
    if current_label is not None and count >= 2:
        stages[current_label] = (current_start, dates[-1])
    
    # Filter but keep all meaningful stages - just remove pure transitions
    print(f"\n📋 所有连续周期阶段:")
    print(f"{'阶段名称':<22} {'起始':<12} {'结束':<12}")
    print("-" * 46)
    
    # Keep all stages (including transition periods), they contain useful info
    all_stages_list = list(stages.items())
    for label, (start, end) in all_stages_list:
        print(f"{label:<22} {start.strftime('%Y-%m'):<12} {end.strftime('%Y-%m'):<12}")
    
    return all_stages_list


# ============================================================
# 3. 板块收益率计算
# ============================================================
def calculate_sector_returns(stages, industry_prices):
    print("\n" + "=" * 60)
    print("📊 板块阶段收益率计算")
    print("=" * 60)
    
    trading_days = industry_prices.index.sort_values()
    stage_trading_ranges = []
    
    for label, (start_m, end_m) in stages:
        start_dt = start_m
        end_dt = end_m + pd.offsets.MonthEnd(0)  # End of month
        # Find nearest trading days
        candidates_after = trading_days[trading_days >= pd.Timestamp(start_dt)]
        candidates_before = trading_days[trading_days <= pd.Timestamp(end_dt)]
        if len(candidates_after) == 0 or len(candidates_before) == 0:
            print(f"  ⚠️ {label}: no trading data in range")
            continue
        actual_start = candidates_after[0]
        actual_end = candidates_before[-1]
        stage_trading_ranges.append((label, actual_start, actual_end))
    
    stage_results = {}
    for label, start_dt, end_dt in stage_trading_ranges:
        mask = (industry_prices.index >= start_dt) & (industry_prices.index <= end_dt)
        phase_data = industry_prices[mask]
        
        if len(phase_data) < 5:
            print(f"  ⚠️ {label} ({start_dt.date()} ~ {end_dt.date()}): 仅 {len(phase_data)} 交易日，跳过")
            continue
        
        first_prices = phase_data.iloc[0]
        last_prices = phase_data.iloc[-1]
        
        returns = {}
        for industry in phase_data.columns:
            f = first_prices[industry]
            l = last_prices[industry]
            if f and f > 0 and l and l > 0:
                returns[industry] = (l / f - 1) * 100
        
        sorted_returns = sorted(returns.items(), key=lambda x: x[1], reverse=True)
        
        stage_results[label] = {
            'start': start_dt,
            'end': end_dt,
            'trading_days': len(phase_data),
            'sorted_returns': sorted_returns,
            'top5': sorted_returns[:5],
            'bottom5': sorted_returns[-5:],
            'returns_dict': returns,
            'all_industries': list(returns.keys()),
        }
        
        print(f"\n📈 {label} ({start_dt.date()} ~ {end_dt.date()}, {len(phase_data)} 交易日)")
        print(f"   🏆 Top 5: ", end="")
        for ind, ret in stage_results[label]['top5']:
            print(f"{ind}={ret:+.2f}% ", end="")
        print()
        print(f"   📉 Bottom 5: ", end="")
        for ind, ret in stage_results[label]['bottom5']:
            print(f"{ind}={ret:+.2f}% ", end="")
        print()
    
    return stage_results


# ============================================================
# 4. 敏感性分析
# ============================================================
def analyze_sensitivity(stage_results):
    print("\n" + "=" * 60)
    print("🔬 板块周期敏感性分析")
    print("=" * 60)
    
    if len(stage_results) < 2:
        print("  阶段不足，无法分析")
        return
    
    # Categorize stages: use money-side label in the name to classify
    # (宽货币 = monetary loosening period regardless of credit condition)
    loose_stages = [s for s in stage_results.keys() if '宽货币' in s]
    tight_stages = [s for s in stage_results.keys() if '紧货币' in s]
    neutral_stages = [s for s in stage_results.keys() if s not in loose_stages and s not in tight_stages]
    
    print(f"\n📌 货币宽松阶段: {len(loose_stages)} 个")
    for s in loose_stages:
        print(f"   - {s}: {stage_results[s]['start'].date()} ~ {stage_results[s]['end'].date()}")
    print(f"📌 货币紧缩阶段: {len(tight_stages)} 个")
    for s in tight_stages:
        print(f"   - {s}: {stage_results[s]['start'].date()} ~ {stage_results[s]['end'].date()}")
    
    # Volatility across all stages
    all_industries = set()
    for s in stage_results:
        all_industries.update(stage_results[s]['returns_dict'].keys())
    
    industry_volatility = {}
    for ind in all_industries:
        returns_across = []
        for s_name, s_data in stage_results.items():
            if ind in s_data['returns_dict']:
                returns_across.append(s_data['returns_dict'][ind])
        if len(returns_across) >= 2:
            industry_volatility[ind] = np.std(returns_across)
    
    sorted_vol = sorted(industry_volatility.items(), key=lambda x: x[1], reverse=True)
    
    print(f"\n📊 跨阶段波动最大 (最敏感) 的 10 个板块:")
    for ind, vol in sorted_vol[:10]:
        print(f"    {ind}: σ={vol:.2f}%")
    
    print(f"\n🛡️  跨阶段波动最小 (最防御) 的 10 个板块:")
    for ind, vol in sorted_vol[-10:]:
        print(f"    {ind}: σ={vol:.2f}%")
    
    # 进攻型板块：宽松期收益高 - 紧缩期收益低 = 周期β大
    if loose_stages and tight_stages:
        print(f"\n⚔️  进攻型板块（宽松期领涨 vs 紧缩期表现，顺周期β最大）:")
        industry_score = {}
        for ind in all_industries:
            l_returns = []
            t_returns = []
            for s_name in loose_stages:
                if ind in stage_results[s_name]['returns_dict']:
                    l_returns.append(stage_results[s_name]['returns_dict'][ind])
            for s_name in tight_stages:
                if ind in stage_results[s_name]['returns_dict']:
                    t_returns.append(stage_results[s_name]['returns_dict'][ind])
            
            if l_returns and t_returns:
                avg_l = np.mean(l_returns)
                avg_t = np.mean(t_returns)
                industry_score[ind] = avg_l - avg_t
        
        sorted_score = sorted(industry_score.items(), key=lambda x: x[1], reverse=True)
        print(f"   (宽松期平均 - 紧缩期平均, %):")
        for ind, score in sorted_score[:10]:
            l_avg = np.mean([stage_results[s]['returns_dict'][ind] for s in loose_stages if ind in stage_results[s]['returns_dict']])
            t_avg = np.mean([stage_results[s]['returns_dict'][ind] for s in tight_stages if ind in stage_results[s]['returns_dict']])
            print(f"    {ind:<16} Δ={score:+7.2f}%  (宽松期={l_avg:+7.2f}%, 紧缩期={t_avg:+7.2f}%)")
        
        # 防御型板块：无论宽松还是紧缩都稳定，β接近0
        print(f"\n🛡️  防御型板块（宽松期 & 紧缩期都表现稳定，Δ最小）:")
        for ind, score in sorted_score[-10:]:
            l_avg = np.mean([stage_results[s]['returns_dict'][ind] for s in loose_stages if ind in stage_results[s]['returns_dict']])
            t_avg = np.mean([stage_results[s]['returns_dict'][ind] for s in tight_stages if ind in stage_results[s]['returns_dict']])
            print(f"    {ind:<16} Δ={score:+7.2f}%  (宽松期={l_avg:+7.2f}%, 紧缩期={t_avg:+7.2f}%)")
        
        # 逆周期板块：紧缩期反而涨
        print(f"\n🔄  逆周期板块（紧缩期表现 > 宽松期）:")
        for ind, score in sorted_score[:5]:
            if score < 0:
                break
        # Show tight-performing ones
        tight_winners = {}
        for ind in all_industries:
            t_returns = []
            l_returns = []
            for s_name in tight_stages:
                if ind in stage_results[s_name]['returns_dict']:
                    t_returns.append(stage_results[s_name]['returns_dict'][ind])
            for s_name in loose_stages:
                if ind in stage_results[s_name]['returns_dict']:
                    l_returns.append(stage_results[s_name]['returns_dict'][ind])
            if t_returns and l_returns:
                tight_winners[ind] = np.mean(t_returns) - np.mean(l_returns)
        sorted_tw = sorted(tight_winners.items(), key=lambda x: x[1], reverse=True)[:5]
        for ind, score in sorted_tw:
            l_avg = np.mean([stage_results[s]['returns_dict'][ind] for s in loose_stages if ind in stage_results[s]['returns_dict']])
            t_avg = np.mean([stage_results[s]['returns_dict'][ind] for s in tight_stages if ind in stage_results[s]['returns_dict']])
            print(f"    {ind:<16} Δ={score:+7.2f}%  (紧缩期={t_avg:+7.2f}%, 宽松期={l_avg:+7.2f}%)")
    
    return industry_volatility


# ============================================================
# 5. 输出摘要
# ============================================================
def print_summary(stages, stage_results):
    print("\n\n" + "=" * 80)
    print("📋  A股货币周期 × 行业轮动分析 — 完整摘要")
    print("=" * 80)
    
    print(f"\n{'阶段名称':<22} {'区间':<24} {'交易日':<8} {'Top1':<22} {'Worst':<22}")
    print("-" * 96)
    
    for label, (start, end) in stages:
        if label in stage_results:
            res = stage_results[label]
            top = res['top5'][0] if res['top5'] else ('?', 0)
            bot = res['bottom5'][0] if res['bottom5'] else ('?', 0)
            interval = f"{res['start'].date()} ~ {res['end'].date()}"
            print(f"{label:<22} {interval:<24} {res['trading_days']:<8} {top[0]}({top[1]:+.1f}%){'':>6} {bot[0]}({bot[1]:+.1f}%)")
    
    print("\n" + "=" * 80)
    print("🏆  各阶段领涨 Top5 板块")
    print("=" * 80)
    
    for label, (start, end) in stages:
        if label in stage_results:
            res = stage_results[label]
            print(f"\n📈 {label} ({res['start'].date()} ~ {res['end'].date()})")
            print(f"{'排名':<6} {'板块':<18} {'阶段收益率':<12}")
            print("-" * 36)
            for i, (ind, ret) in enumerate(res['top5'], 1):
                print(f"{i:<6} {ind:<18} {ret:+.2f}%")
    
    print("\n" + "=" * 80)
    print("📉  各阶段领跌 Bottom5 板块")
    print("=" * 80)
    
    for label, (start, end) in stages:
        if label in stage_results:
            res = stage_results[label]
            print(f"\n📉 {label} ({res['start'].date()} ~ {res['end'].date()})")
            print(f"{'排名':<6} {'板块':<18} {'阶段收益率':<12}")
            print("-" * 36)
            for i, (ind, ret) in enumerate(res['bottom5'], 1):
                print(f"{i:<6} {ind:<18} {ret:+.2f}%")


# ============================================================
# 6. 主流程
# ============================================================
def main():
    print("=" * 80)
    print("🏛️  A股货币周期 × 行业轮动分析 (2015~2026)")
    print("   数据源: PG @ 192.168.3.14 | 同花顺90行业板块")
    print("   分析方法: M2(3MMA) + AAA信用利差(月度中位) → 4象限周期")
    print("=" * 80)
    
    # Step 1: Determine monetary cycles
    stages = determine_cycles_v2()
    if not stages:
        print("❌ 无法确定周期阶段")
        return
    
    # Step 2: Fetch industry prices
    print("\n🔍 拉取板块日线...")
    industry_prices = fetch_industry_prices()
    if industry_prices.empty:
        print("❌ 板块数据为空")
        return
    
    # Step 3: Calculate returns per stage
    print("\n🔍 计算各阶段板块收益率...")
    stage_results = calculate_sector_returns(stages, industry_prices)
    
    # Step 4: Sensitivity analysis
    print("\n🔍 板块周期敏感性分析...")
    analyze_sensitivity(stage_results)
    
    # Step 5: Summary
    print("\n🔍 输出完整摘要...")
    print_summary(stages, stage_results)
    
    print("\n" + "=" * 80)
    print("✅ 分析完成!")
    print("=" * 80)


if __name__ == "__main__":
    main()
