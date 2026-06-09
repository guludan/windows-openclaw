#!/usr/bin/env python3
"""
板块周期分析报告 — 行业轮动 + 机构行为突变点检测

产出：/mnt/d/data/think-tank/market_psychology/sector_cycle_report.txt
"""
import sys, os, logging, json, collections
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import data_finance as df

log = logging.getLogger('sector_report')

def fetch_sector_data():
    rows, _ = df._pg_execute(
        "SELECT trade_date::text, industry, return_pct, median_return, std_dev, "
        "relative_vs_hs300, hs300_return, up_stocks, down_stocks, "
        "limit_up, limit_down, valid_count "
        "FROM sector_daily ORDER BY trade_date, industry")
    
    data = {}
    for r in rows:
        dt = r[0]
        if dt not in data:
            data[dt] = {}
        data[dt][r[1]] = {
            'return_pct': float(r[2] or 0),
            'median_return': float(r[3] or 0),
            'std_dev': float(r[4] or 0),
            'relative_vs_hs300': float(r[5] or 0),
            'hs300_return': float(r[6] or 0),
            'up_stocks': int(r[7] or 0),
            'down_stocks': int(r[8] or 0),
            'limit_up': int(r[9] or 0),
            'limit_down': int(r[10] or 0),
            'valid_count': int(r[11] or 0),
        }
    
    # 获取行业列表
    industries = sorted(set(r[1] for r in rows))
    return data, industries

def analyze_sector_cycles(data, industries):
    """分析板块轮动特征"""
    dates = sorted(data.keys())
    n = len(dates)
    
    # 每日板块涨跌统计
    sector_up_ratio = {}
    sector_limit_up_ratio = {}
    sector_cohesion = {}  # 板块内一致度 (std_dev的倒数)
    sector_momentum = {}  # 板块轮动动量
    
    for dt in dates:
        day_data = data[dt]
        total = len(day_data)
        up = sum(1 for s in day_data.values() if s['return_pct'] > 0)
        limit_up_count = sum(1 for s in day_data.values() if s['limit_up'] > 2)
        mean_std = np.mean([s['std_dev'] for s in day_data.values()])
        mean_ret = np.mean([s['return_pct'] for s in day_data.values()])
        
        sector_up_ratio[dt] = up / total if total else 0
        sector_limit_up_ratio[dt] = limit_up_count / total if total else 0
        sector_cohesion[dt] = round(1 / (mean_std + 0.01), 2)
        sector_momentum[dt] = round(mean_ret, 2)
    
    return {
        'sector_up_ratio': sector_up_ratio,
        'sector_limit_up_ratio': sector_limit_up_ratio,
        'sector_cohesion': sector_cohesion,
        'sector_momentum': sector_momentum,
    }

def detect_institutional_signal_shifts(data, industries):
    """
    检测机构/量化策略调整触发点
    
    方法：
    1. 板块涨停潮（5+行业同时涨停>2只）→ 造势信号
    2. 板块集体跌停潮 → 踩踏信号
    3. 板块轮动拐点（动量由正转负/由负转正）
    4. 行业离散度骤升（板块内分歧突增）→ 机构分化
    """
    dates = sorted(data.keys())
    signals = []
    
    for idx, dt in enumerate(dates):
        if idx < 5:
            continue
        prev_dt = dates[idx-1]
        
        day_data = data[dt]
        prev_data = data[prev_dt]
        
        # 1. 涨停潮检测
        sectors_with_blow = sum(1 for s in day_data.values() if s['limit_up'] >= 5)
        sectors_with_crash = sum(1 for s in day_data.values() if s['limit_down'] >= 5)
        
        # 2. 板块动量反转（过去5日平均正→单日负or反之）
        if idx >= 5:
            prev5 = [np.mean([data[dates[j]][ind]['return_pct'] for ind in industries if ind in data[dates[j]]]) 
                     for j in range(idx-5, idx)]
            current_momentum = np.mean([day_data.get(ind, {}).get('return_pct', 0) for ind in industries])
            
            if prev5[-1] > 0.5 and current_momentum < -1:
                signals.append({
                    'date': dt,
                    'type': 'momentum_reversal_dn',
                    'desc': f'板块动量反转: 5日均{prev5[-1]:.1f}%→当日{current_momentum:.1f}%',
                    'strength': 'strong' if abs(current_momentum) > 2 else 'medium'
                })
            elif prev5[-1] < -0.5 and current_momentum > 1:
                signals.append({
                    'date': dt,
                    'type': 'momentum_reversal_up',
                    'desc': f'板块动量反弹: 5日均{prev5[-1]:.1f}%→当日{current_momentum:.1f}%',
                    'strength': 'strong' if abs(current_momentum) > 2 else 'medium'
                })
        
        # 3. 行业离散度骤升（板块内部分歧突然变大）
        cohesion_now = np.mean([s['std_dev'] for s in day_data.values()])
        cohesion_prev = np.mean([s['std_dev'] for s in prev_data.values()])
        
        if cohesion_now > 4 and cohesion_now > 1.8 * cohesion_prev:
            signals.append({
                'date': dt,
                'type': 'cohesion_break',
                'desc': f'板块内离散度骤升: {cohesion_prev:.1f}→{cohesion_now:.1f}（板块内分歧爆发）',
                'strength': 'strong' if cohesion_now > 5.5 else 'medium'
            })
        
        # 4. 全市场涨停潮（5+行业同时涨停>3只）
        if sectors_with_blow >= 5:
            signals.append({
                'date': dt,
                'type': 'wide_blowup',
                'desc': f'{sectors_with_blow}个行业共涨停潮（每行业≥5只涨停）',
                'strength': 'strong' if sectors_with_blow >= 10 else 'medium'
            })
        
        if sectors_with_crash >= 3:
            signals.append({
                'date': dt,
                'type': 'wide_crash',
                'desc': f'{sectors_with_crash}个行业同时集体跌停潮',
                'strength': 'strong' if sectors_with_crash >= 5 else 'critical'
            })
        
        # 5. 板块涨跌比极端（>80%板块涨或>80%跌）
        total_sec = max(len(day_data), 1)
        up_sec = sum(1 for s in day_data.values() if s['return_pct'] > 0)
        dn_sec = sum(1 for s in day_data.values() if s['return_pct'] < 0)
        up_ratio = up_sec / total_sec
        dn_ratio = dn_sec / total_sec
        
        if up_ratio >= 0.9:
            signals.append({
                'date': dt,
                'type': 'sector_unanimity_up',
                'desc': f'{up_sec}/{total_sec}行业普涨({up_ratio*100:.0f}%)',
                'strength': 'strong'
            })
        elif dn_ratio >= 0.8:
            signals.append({
                'date': dt,
                'type': 'sector_unanimity_dn',
                'desc': f'{dn_sec}/{total_sec}行业普跌({dn_ratio*100:.0f}%)',
                'strength': 'strong' if dn_ratio < 0.95 else 'critical'
            })
    
    # 去重：同一天保留最重要的信号
    signals.sort(key=lambda s: {'critical': 0, 'strong': 1, 'medium': 2}.get(s['strength'], 3))
    seen_dates = set()
    deduped = []
    for s in signals:
        if s['date'] not in seen_dates:
            seen_dates.add(s['date'])
            deduped.append(s)
    
    return deduped

def detect_quant_strategy_shifts(data, industries):
    """
    量化策略调整触发点 — 更精确
    
    量化策略的特征：
    - 行业内部离散度骤降（量化集中买入/卖出）
    - 板块间动量持续性突然中断
    - 涨停板/跌停板数量与大盘走向严重背离
    - 行业间相关系数短期突变
    """
    dates = sorted(data.keys())
    shifts = []
    
    for idx, dt in enumerate(dates):
        if idx < 10: continue
        prev_5 = dates[max(0, idx-5):idx]
        
        day_data = data[dt]
        
        # 涨停/跌停背离：大盘跌但某行业暴涨停
        hs300_ret = list(day_data.values())[0]['hs300_return'] if day_data else 0
        if hs300_ret < -1:
            blowup_high = [(ind, s['limit_up'], s['return_pct']) 
                          for ind, s in day_data.items() if s['limit_up'] >= 5]
            if blowup_high:
                for ind, lu, ret in blowup_high[:3]:
                    shifts.append({
                        'date': dt,
                        'type': 'divergent_blowup',
                        'desc': f'大盘跌{hs300_ret:.1f}%但{ind}板块涨停{lu}只(+{ret:.1f}%) — 逆势造势',
                        'strength': 'strong'
                    })
        elif hs300_ret > 1:
            crash_high = [(ind, s['limit_down'], s['return_pct'])
                         for ind, s in day_data.items() if s['limit_down'] >= 5]
            if crash_high:
                for ind, ld, ret in crash_high[:3]:
                    shifts.append({
                        'date': dt,
                        'type': 'divergent_crash',
                        'desc': f'大盘涨{hs300_ret:.1f}%但{ind}板块跌停{ld}只({ret:.1f}%) — 逆势出货',
                        'strength': 'strong'
                    })
        
        # 行业间相关系数突变（量化调仓信号）
        if idx >= 21:  # 至少1个月数据
            recent_21 = dates[max(0, idx-21):idx+1]
            ind_returns = {}
            for d in recent_21:
                day = data.get(d, {})
                for ind in day:
                    if ind not in ind_returns:
                        ind_returns[ind] = []
                    ind_returns[ind].append(day[ind]['return_pct'])
            
            # 检查行业间正相关性（短期背离）
            ind_list = list(ind_returns.keys())
            if len(ind_list) >= 5:
                # 计算收益差异最大的行业对
                day_rets = [day_data.get(ind, {}).get('return_pct', 0) for ind in ind_list]
                if day_rets:
                    best = max(day_rets)
                    worst = min(day_rets)
                    spread = best - worst
                    if spread > 10:
                        best_ind = ind_list[day_rets.index(best)]
                        worst_ind = ind_list[day_rets.index(worst)]
                        shifts.append({
                            'date': dt,
                            'type': 'extreme_sector_spread',
                            'desc': f'行业日收益极差{spread:.1f}%: {best_ind}+{best:.1f}% vs {worst_ind}{worst:.1f}% — 资金从{worst_ind}腾挪至{best_ind}',
                            'strength': 'strong' if spread > 15 else 'medium'
                        })
    
    return shifts

def generate_report():
    log.info("加载板块日线数据...")
    data, industries = fetch_sector_data()
    dates = sorted(data.keys())
    log.info(f"数据: {len(dates)}天, {len(industries)}行业")
    
    cycle_metrics = analyze_sector_cycles(data, industries)
    inst_signals = detect_institutional_signal_shifts(data, industries)
    quant_signals = detect_quant_strategy_shifts(data, industries)
    
    # 提取重点事件
    critical_dates = [s for s in inst_signals if s['strength'] in ('critical', 'strong')]
    critical_quant = [s for s in quant_signals if s['strength'] in ('critical', 'strong')]
    
    # 关联到宏观周期
    macro_periods = {
        '2025-01~2025-02': '关税前平稳期',
        '2025-03~2025-04': '关税冲击期',
        '2025-05~2025-08': '政策蓄力期',
        '2025-09~2025-10': '政策爆发牛市幻想',
        '2025-11~2025-12': '地缘升温期',
        '2026-01~2026-02': '全球避险期',
        '2026-03~2026-05': '震荡修复期',
    }
    
    def get_period_label(dt):
        for k, v in macro_periods.items():
            parts = k.split('~')
            start = parts[0] + '-01' if len(parts[0].split('-')) == 2 else parts[0]
            end = parts[1] + '-28' if len(parts[1].split('-')) == 2 else parts[1]
            if start <= dt <= end:
                return v
        return '其他'
    
    report = []
    report.append("=" * 70)
    report.append("   A股板块周期分析报告（2025-01 ~ 2026-05）")
    report.append("=" * 70)
    report.append(f"  交易日: {len(dates)} | 行业数: {len(industries)}")
    report.append(f"  数据: sector_daily 表 | 10168 行")
    report.append("=" * 70)
    report.append("")
    
    # ===== 1. 板块动量概览 =====
    report.append("── 一、板块动量概览 ──")
    report.append("")
    
    # 每行业累计日收益
    sector_total = {}
    for dt in dates:
        for ind, s in data[dt].items():
            if ind not in sector_total:
                sector_total[ind] = 0
            sector_total[ind] += s['return_pct']
    
    sorted_by_return = sorted(sector_total.items(), key=lambda x: x[1], reverse=True)
    report.append("  行业累计收益 Top 10（2025~今）:")
    for ind, ret in sorted_by_return[:10]:
        report.append(f"    +{ind}: {ret:+.1f}%")
    report.append("  ...")
    report.append("  行业累计收益 Bottom 10:")
    for ind, ret in sorted_by_return[-10:]:
        report.append(f"    -{ind}: {ret:+.1f}%")
    report.append("")
    
    # 行业动量持续性（正收益天数占比）
    report.append("  行业上涨天数占比:")
    for ind, tot in sorted_by_return[:5] + sorted_by_return[-5:]:
        up_days = sum(1 for dt in dates if data[dt].get(ind, {}).get('return_pct', 0) > 0)
        pct = up_days / len(dates) * 100
        arrow = '+'
        if ind in dict(sorted_by_return[-5:]):
            arrow = '-'
        report.append(f"    {arrow}{ind}: {pct:.0f}%天数上涨")
    report.append("")
    
    # ===== 2. 机构策略调整触发点 =====
    report.append("── 二、机构/量化策略调整触发点 ──")
    report.append("")
    
    # 按类型分组
    from collections import Counter
    type_counts = Counter(s['type'] for s in inst_signals + quant_signals)
    
    report.append("  信号类型分布:")
    for t, c in type_counts.most_common():
        names = {
            'wide_blowup': '全市场涨停潮', 'wide_crash': '全市场跌停潮',
            'sector_unanimity_up': '板块普涨日', 'sector_unanimity_dn': '板块普跌日',
            'momentum_reversal_dn': '动量反转向下', 'momentum_reversal_up': '动量反转向上',
            'cohesion_break': '板块内离散度爆发',
            'divergent_blowup': '逆势造势（大盘跌+某板块涨停）',
            'divergent_crash': '逆势出货（大盘涨+某板块跌停）',
            'extreme_sector_spread': '行业收益极差+资金腾挪',
        }
        report.append(f"    {names.get(t, t)}: {c}次")
    report.append("")
    
    # 关键触发点时间线
    report.append("  📅 关键机构行为信号时间线:")
    all_signals = sorted(inst_signals + quant_signals, key=lambda s: s['date'])
    for s in all_signals:
        period = get_period_label(s['date'])
        symbol = {'critical': '🔴', 'strong': '🟡', 'medium': '⚪'}.get(s['strength'], '⚪')
        report.append(f"    {symbol} [{s['date']}] ({period}) {s['desc']}")
    report.append("")
    
    # ===== 3. 核心触发点分析 =====
    report.append("── 三、核心触发点分析 ──")
    report.append("")
    
    # 找出的策略调整点
    strategy_turning_points = []
    for s in all_signals:
        dt = s['date']
        day = data[dt]
        hs300 = list(day.values())[0]['hs300_return'] if day else 0
        
        # 计算当日板块成交集中度
        top_sector = max(day.items(), key=lambda x: abs(x[1]['return_pct']))
        
        strategy_turning_points.append({
            'date': dt,
            'type': s['type'],
            'hs300_ret': hs300,
            'signal_strength': s['strength'],
            'top_sector': top_sector[0],
            'top_sector_ret': top_sector[1]['return_pct'],
            'desc': s['desc'],
        })
    
    for i, pt in enumerate(strategy_turning_points[:20]):
        report.append(f"  {i+1}. [{pt['date']}] {pt['desc']}")
        report.append(f"     大盘: {pt['hs300_ret']:+.1f}% | 领军行业: {pt['top_sector']}({pt['top_sector_ret']:+.1f}%)")
        report.append("")
    
    # ===== 4. 量化策略信号检测 =====
    report.append("── 四、量化策略信号特性 ──")
    report.append("")
    report.append("  🔍 检测到以下量化策略特征:")
    
    # 逆势造势/出货
    divergent = [s for s in quant_signals if 'divergent' in s['type']]
    if divergent:
        report.append(f"  【逆势信号】共{len(divergent)}次:")
        for s in divergent[:5]:
            report.append(f"    {s['desc']}")
    report.append("")
    
    # 极差信号（资金腾挪）
    spread_sig = [s for s in quant_signals if 'extreme_sector' in s['type']]
    if spread_sig:
        report.append(f"  【资金腾挪信号】共{len(spread_sig)}次:")
        for s in spread_sig[:5]:
            report.append(f"    {s['desc']}")
    report.append("")
    
    # ===== 5. 结论 =====
    report.append("── 五、结论与模型更新参照点 ──")
    report.append("")
    
    # 统计关键触发点分布
    critical_list = [s for s in all_signals if s['strength'] == 'critical']
    strong_list = [s for s in all_signals if s['strength'] == 'strong']
    
    report.append(f"  共检测到 {len(all_signals)} 个行为信号:")
    report.append(f"    - 关键(critical): {len(critical_list)}")
    report.append(f"    - 强(strong): {len(strong_list)}")
    report.append(f"    - 中(medium): {len(all_signals) - len(critical_list) - len(strong_list)}")
    report.append("")
    report.append("  📌 可作为模型更新参照点的关键日期:")
    for s in critical_list + strong_list[:15]:
        report.append(f"    {s['date']}: {s['desc']}")
    report.append("")
    
    report.append("  💡 量化策略调整触发条件模式:")
    patterns = [
        "1. 板块涨停潮（5+行业同时≥5只涨停）→ 造势信号，模型需避免追高",
        "2. 板块动量回落+离散度骤升 → 机构内部出现分歧，模型应减仓",
        "3. 逆势涨停（大盘跌但某板块涨停潮）→ 可能是护盘或下跌中继",
        "4. 行业收益极差>10% → 资金正在大规模轮动，模型需快速切换行业配置",
        "5. 连续3天板块普涨后→普跌 → 典型的量化集体获利了结模式",
        "6. 跌停潮（3+行业同时≥5只跌停）→ 系统性踩踏，模型应风控",
    ]
    for p in patterns:
        report.append(f"  {p}")
    report.append("")
    
    report.append("  ⚠️ 需要回测的模型更新策略:")
    strategies = [
        "A. 板块涨停潮次日：减仓对应板块（高潮次日 = 卖点）",
        "B. 离散度骤升+D1D2区间：定性为机构博弈区，降仓等待方向",
        "C. 板块动量反转+两融流出>50亿：确认踩踏，全仓风控",
        "D. 板块轮动加速（3+行业切换）：跟随最强动量板块",
    ]
    for s in strategies:
        report.append(f"  {s}")
    report.append("")
    
    report.append("=" * 70)
    report.append(f"  报告生成时间: {__import__('datetime').datetime.now()}")
    report.append("=" * 70)
    
    return '\n'.join(report)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    report = generate_report()
    
    output_path = '/mnt/d/data/think-tank/market_psychology/sector_cycle_report.txt'
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(report)
    log.info(f"报告已保存: {output_path}")
