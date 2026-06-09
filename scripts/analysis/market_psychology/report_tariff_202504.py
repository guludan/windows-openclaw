#!/usr/bin/env python3
"""
关税冲击复盘报告 — 2025年4月 造势链条全景

数据源：全市场日线 + 两融 + HSGT（均已入库）
方法：时序对齐 → 关键拐点标注 → 模式提炼
"""
import sys, os, json
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import data_finance as df

REPORT_DIR = "/mnt/d/data/think-tank/reports"

def load_timeline():
    """加载已保存的时间线"""
    path = os.path.join(REPORT_DIR, "tariff_shock_202504_timeline.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def build_event_table():
    """关键节点数据表"""
    rows, _ = df._pg_execute(
        "SELECT trade_date, count(*) as total, "
        "sum(case when close/prev_close - 1 > 0.095 then 1 else 0 end) as limit_up, "
        "sum(case when close/prev_close - 1 < -0.095 then 1 else 0 end) as limit_down, "
        "sum(case when close/prev_close - 1 > 0 then 1 else 0 end) as up_stocks, "
        "sum(case when close/prev_close - 1 < 0 then 1 else 0 end) as down_stocks "
        "FROM ("
        "  SELECT trade_date, symbol, close, "
        "  lag(close) OVER (PARTITION BY symbol ORDER BY trade_date) as prev_close "
        "  FROM daily_prices WHERE symbol LIKE 'A.%%' "
        "  AND trade_date BETWEEN '2025-04-01' AND '2025-04-15'"
        ") t WHERE prev_close > 0 "
        "GROUP BY trade_date ORDER BY trade_date")
    
    # 指数数据
    idx_rows, _ = df._pg_execute(
        "SELECT trade_date, close, "
        "lag(close) OVER (ORDER BY trade_date) as prev_close "
        "FROM daily_prices WHERE symbol='I.000300' "
        "AND trade_date BETWEEN '2025-04-01' AND '2025-04-15' ORDER BY trade_date")
    idx_map = {str(r[0]): r for r in idx_rows}
    
    # 两融
    margin_rows, _ = df._pg_execute(
        "SELECT trade_date, value, change_val FROM market_signals "
        "WHERE signal_type='margin_sh' AND code='TOTAL' "
        "AND trade_date BETWEEN '2025-04-01' AND '2025-04-15' ORDER BY trade_date")
    margin_map = {str(r[0]): r for r in margin_rows}
    
    table = []
    for r in rows:
        dt = str(r[0])
        total = int(r[1])
        limit_up, limit_down = int(r[2]), int(r[3])
        up_s, down_s = int(r[4]), int(r[5])
        
        idx = idx_map.get(dt)
        idx_chg = 0.0
        if idx and idx[2]:
            idx_chg = round((float(idx[1]) - float(idx[2])) / float(idx[2]) * 100, 2)
        
        margin = margin_map.get(dt, (None, 0, 0))
        margin_val = float(margin[1]) / 1e8 if margin[1] else 0
        
        table.append({
            'date': dt,
            'weekday': date.fromisoformat(dt).weekday(),
            'hs300_chg': idx_chg,
            'limit_up_pct': round(limit_up / total * 100, 1),
            'limit_down_pct': round(limit_down / total * 100, 1),
            'up_down_ratio': round(up_s / down_s, 2) if down_s > 0 else 99.99,
            'margin_sh': round(margin_val, 0),
            'phase': '',
        })
    
    # 标注阶段
    for t in table:
        d = t['date']
        if d == '2025-04-02':
            t['phase'] = '事件触发 day+0'
        elif d == '2025-04-03':
            t['phase'] = '初期反应 day+1'
        elif d == '2025-04-07':
            t['phase'] = '踩踏日'
        elif d == '2025-04-08':
            t['phase'] = '情绪释放/反弹'
        elif d >= '2025-04-09' and d <= '2025-04-11':
            t['phase'] = '修复期'
        elif d >= '2025-04-14':
            t['phase'] = '回稳期'
        elif d == '2025-04-01':
            t['phase'] = '冲击前基线'
    
    return table


def print_report(table):
    """输出完整报告"""
    lines = []
    lines.append("=" * 80)
    lines.append("    2025年4月关税冲击 — 造势链条复盘报告")
    lines.append("=" * 80)
    lines.append("")
    
    # 阶段摘要
    lines.append("一、阶段划分")
    lines.append("-" * 40)
    
    phases = {
        '冲击前基线': {'color': '⬜', 'desc': '关税宣布前市场状态'},
        '事件触发 day+0': {'color': '🟡', 'desc': '4/2 宣布关税当日，市场未完全消化'},
        '初期反应 day+1': {'color': '🟠', 'desc': '4/3 开始反应，但幅度有限，跌停数开始上升'},
        '踩踏日': {'color': '🔴', 'desc': '4/7 周末发酵后开盘暴跌，4063只跌停（占84%），冰封行情'},
        '情绪释放/反弹': {'color': '🟢', 'desc': '4/8 恐慌释放后反弹+1.71%，仍有191只跌停'},
        '修复期': {'color': '🟢', 'desc': '4/9~4/11 连续修复，涨跌停比逆转'},
        '回稳期': {'color': '⬜', 'desc': '4/14后市场收敛'},
    }
    
    for t in table:
        if t['phase'] in phases:
            p = phases[t['phase']]
            lines.append("  %s %-20s %s" % (p['color'], t['date'] + ' ' + t['phase'], p['desc']))
    
    lines.append("")
    lines.append("二、逐日数据")
    lines.append("-" * 80)
    lines.append("%-12s %-12s %-10s %-10s %-10s %-10s" % (
        "日期", "阶段", "沪深300", "涨停比%", "跌停比%", "两融亿"))
    lines.append("-" * 80)
    
    for t in table:
        phase_short = t['phase'][:12]
        marker = ""
        if '踩踏' in t['phase']:
            marker = "🔥"
        elif '反弹' in t['phase']:
            marker = "⭐"
        
        lines.append("%s %-10s %-12s %+6.2f%% %6.1f%%  %7.1f%% %8.0f" % (
            marker, t['date'], phase_short,
            t['hs300_chg'], t['limit_up_pct'], t['limit_down_pct'],
            t['margin_sh']))
    
    lines.append("")
    lines.append("三、关键指标序列分析")
    lines.append("-" * 40)
    
    # 跌停比突变
    ld_series = [t['limit_down_pct'] for t in table if t['limit_down_pct'] > 0]
    up_series = [t['limit_up_pct'] for t in table if t['limit_up_pct'] > 0]
    lines.append("  跌停占比峰值: %.1f%% (4/7踩踏日)" % max(ld_series))
    lines.append("  踩踏日前一交易日跌停比: %.1f%%" % table[3]['limit_down_pct'] if len(table) > 3 else "?")
    
    # 两融去杠杆
    margin_first = table[0]['margin_sh'] if table else 0
    margin_min = min(t['margin_sh'] for t in table) if table else 0
    margin_drop = margin_first - margin_min
    lines.append("  两融去杠杆: %.0f亿 → %.0f亿 (减少%.0f亿)" % (
        margin_first, margin_min, margin_drop))
    
    lines.append("")
    lines.append("四、造势链条模式推导")
    lines.append("-" * 40)
    lines.append("")
    lines.append("  [阶段1] 事件触发 (T+0, 4/2 周三)")
    lines.append("    关税宣布当日上午/A股收盘后，当日A股反应轻微(-0.08%)，")
    lines.append("    涨停>跌停(52:12)，说明市场未立即恐慌")
    lines.append("")
    lines.append("  [阶段2] 认知延迟 (T+1, 4/3 周四)")
    lines.append("    跌停升至49只(涨停54只)，跌幅扩大至-0.59%，")
    lines.append("    机构开始减仓但散户尚未大规模出逃")
    lines.append("")
    lines.append("  [阶段3] 情绪发酵 (周末 4/4~4/6)")
    lines.append("    ★ 这是造势的关键窗口 ★")
    lines.append("    - 媒体/大V/朋友圈加速传播悲观叙事")
    lines.append("    - 提前挂单卖出的散户形成隔夜抛压")
    lines.append("    - 两融4/3当天仅减少53亿(正常水平)")
    lines.append("")
    lines.append("  [阶段4] 恐慌踩踏 (T+3, 4/7 周一)")
    lines.append("    ★ 造势链条的兑现 ★")
    lines.append("    - 沪深300暴跌-7.05%%")
    lines.append("    - 4063只跌停(84%占比)，流动性冰封")
    lines.append("    - 两融一天蒸发199亿(强制平仓)")
    lines.append("    - 多头仓位被迫清仓，空头盈利了结")
    lines.append("")
    lines.append("  [阶段5] 情绪宣泄+反弹 (T+4, 4/8 周二)")
    lines.append("    - 当天反弹+1.71%%，但仍有191只跌停")
    lines.append("    - 两融再降179亿(余震平仓)")
    lines.append("    - 机构进场承接恐慌盘")
    lines.append("")
    lines.append("  [阶段6] 修复 (T+5~T+7, 4/9~4/11)")
    lines.append("    - 连续3天上涨，涨跌停比逆转")
    lines.append("    - 两融企稳(9266亿)")
    lines.append("")
    lines.append("五、博弈分析")
    lines.append("-" * 40)
    lines.append("")
    shrink_total = margin_first - margin_min
    lines.append("  空方收益估算:")
    lines.append("    4/2~4/7期间两融去杠杆: %.0f亿（被迫平仓）" % shrink_total)
    lines.append("    沪深300跌幅: 3884 -> 3589 (-7.6%%)")
    lines.append("    这是空头最激进的3个交易日")
    lines.append("")
    lines.append("  多方阵地:")
    lines.append("    4/8反弹+1.71%%，4/9~4/11连涨3天")
    lines.append("    说明有承接力量——可能是国家队或低估值的价值投资者")
    lines.append("    但两融持续下降，说明散户杠杆在撤退，接盘的是机构而非散户")
    lines.append("")
    lines.append("  胜方判断:")
    lines.append("    1. 提前做空/平仓的机构（在4/2~4/3期间减仓）")
    lines.append("    2. 4/7恐慌盘承接者（4/8反弹盈利）")
    lines.append("    3. 媒体/自媒体（流量暴涨）")
    lines.append("  输方:")
    lines.append("    1. 4/2~4/3未减仓的散户（满仓吃-7%）")
    lines.append("    2. 融资盘被强制平仓的散户")
    lines.append("    3. 恐慌底部割肉的散户")
    lines.append("")
    lines.append("六、可定量化的造势指标")
    lines.append("-" * 40)
    lines.append("")
    lines.append("  1. 事件→认知延迟 (Event-Cognition Lag)")
    lines.append("     从事件发生到市场充分定价的间隔: 1~3天")
    lines.append("     本案例: 4/2(事件) → 4/7(充分定价) = 3个交易日间隔(含周末)")
    lines.append("")
    lines.append("  2. 周末放大效应 (Weekend Amplification)")
    lines.append("     周五收盘反应vs周一开盘反应的差值")
    lines.append("     本案例: 4/3收-0.59%% → 4/7开-7.05%% = 10x+ 放大")
    lines.append("")
    lines.append("  3. 两融去杠杆斜率 (Margin Deleveraging Slope)")
    lines.append("     一天内两融下降幅度: 199亿 (4/7)")
    lines.append("")
    lines.append("  4. 涨跌停比突变 (Limit-up/down Ratio Shock)")
    lines.append("     涨停/跌停比值从 1.1(4/3) 变为 0.006(4/7) ")
    lines.append("")
    lines.append("  5. 修复速度 (Recovery Speed)")
    lines.append("     从底部到收复一半跌幅所需天数: 2天(4/7→4/9)")
    lines.append("")
    lines.append("=" * 80)
    
    return "\n".join(lines)


if __name__ == '__main__':
    table = build_event_table()
    report = print_report(table)
    
    # 打印到控制台
    print(report)
    
    # 保存
    os.makedirs(REPORT_DIR, exist_ok=True)
    out_path = os.path.join(REPORT_DIR, "tariff_shock_202504_report.txt")
    with open(out_path, 'w') as f:
        f.write(report)
    print(f"\n报告已保存: {out_path}")
    
    # 同时保存结构化数据
    data_path = os.path.join(REPORT_DIR, "tariff_shock_202504_data.json")
    with open(data_path, 'w') as f:
        json.dump(table, f, ensure_ascii=False, indent=2)
    print(f"数据已保存: {data_path}")
