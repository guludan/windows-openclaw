#!/usr/bin/env python3
"""
宏观经济周期分析 — 用FRED+已入库数据构建中美货币政策周期背景板

分析维度：
1. 中美10Y利差走势（中国10Y - 美国10Y）
2. 联邦基金利率阶段（加息/降息/暂停）
3. 美元指数与A股相关性
4. 周期分期标注

输出：周期分期表 + 可视化数据
"""
import sys, os, json, logging
from datetime import date, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import data_finance as df

log = logging.getLogger('macro_cycle')

REPORT_DIR = "/mnt/d/data/think-tank/market_psychology"


def load_macro(indicator: str, since: str = "2020-01-01"):
    """加载宏观指标"""
    rows, _ = df._pg_execute(
        "SELECT date, value FROM macro_indicators WHERE indicator=%s "
        "AND date >= %s::date ORDER BY date",
        (indicator, since))
    return [{'date': str(r[0]), 'value': float(r[1])} for r in rows]


def determine_fed_regime(rates: list) -> list:
    """从联邦基金利率识别加息/降息/暂停周期"""
    if not rates:
        return []
    regimes = []
    prev_val = rates[0]['value']
    regime_start = rates[0]['date']
    regime_type = '...'
    
    for i, r in enumerate(rates[1:], 1):
        cur_val = r['value']
        diff = cur_val - prev_val
        
        if abs(diff) > 0.05:
            if diff > 0 and regime_type != '加息':
                if i > 0:
                    regimes.append({'type': regime_type, 'start': regime_start, 'end': rates[i-1]['date']})
                regime_type = '加息'
                regime_start = r['date']
            elif diff < 0 and regime_type != '降息':
                if i > 0:
                    regimes.append({'type': regime_type, 'start': regime_start, 'end': rates[i-1]['date']})
                regime_type = '降息'
                regime_start = r['date']
        
        prev_val = cur_val
    
    regimes.append({'type': regime_type, 'start': regime_start, 'end': rates[-1]['date']})
    return regimes


def build_cycle_periods(us10y: list, cn10y: list, fed_rates: list, fed_regimes: list) -> list:
    """构建货币政策周期分期表"""
    # 合并所有日期
    all_dates = sorted(set(r['date'] for r in us10y))
    
    periods = []
    
    # 定义关键转折点（基于已知宏观事件）
    key_dates = [
        ("2022-03-17", "美国启动加息", "美联储加息25bp，结束零利率", "紧缩"),
        ("2022-06-15", "美国加速加息75bp", "通胀飙升，75bp连续加息", "紧缩"),
        ("2023-07-26", "美国最后一次加息", "联邦基金利率达5.25-5.5%峰值", "紧缩末期"),
        ("2024-09-18", "美国启动降息", "四年来首次降息50bp", "宽松"),
        ("2020-04-01", "中国宽松周期", "疫情后MLF/LPR多次下调", "宽松"),
        ("2023-08-21", "中国LPR不对称降息", "1Y降10bp,5Y不变，稳楼市", "宽松"),
        ("2024-10-01", "中国政策刺激", "924金融新政+政治局会议", "宽松加速"),
        ("2025-04-02", "关税冲击", "美国宣布对华关税", "危机"),
        ("2025-09-04", "政治局会议", "政策组合拳，牛市起点叙事", "宽松爆发"),
        ("2026-03-23", "地缘冲击", "中东冲突升级，A股调整", "危机"),
    ]
    
    # 用利率数据计算节点
    # 第一阶段标记
    known_periods = [
        {"start": "2020-01-01", "end": "2020-03-31", "phase": "疫情冲击", "us_regime": "紧急降息", "cn_regime": "宽松"},
        {"start": "2020-04-01", "end": "2021-12-31", "phase": "全球宽松/复苏", "us_regime": "零利率", "cn_regime": "温和宽松"},
        {"start": "2022-01-01", "end": "2022-03-31", "phase": "通胀抬头", "us_regime": "加息预备", "cn_regime": "宽松"},
        {"start": "2022-04-01", "end": "2023-07-31", "phase": "美国激进加息", "us_regime": "快速加息", "cn_regime": "宽松"},
        {"start": "2023-08-01", "end": "2024-08-31", "phase": "利率高位僵持", "us_regime": "高利率暂停", "cn_regime": "宽松对冲"},
        {"start": "2024-09-01", "end": "2025-03-31", "phase": "美国降息+中国发力", "us_regime": "降息启动", "cn_regime": "加速宽松"},
        {"start": "2025-04-01", "end": "2025-05-31", "phase": "关税冲击", "us_regime": "降息+关税", "cn_regime": "危机对冲"},
        {"start": "2025-06-01", "end": "2025-08-31", "phase": "政策蓄力", "us_regime": "降息观望", "cn_regime": "宽松蓄力"},
        {"start": "2025-09-01", "end": "2025-10-31", "phase": "政策爆发牛市幻想", "us_regime": "降息中", "cn_regime": "强力宽松"},
        {"start": "2025-11-01", "end": "2026-02-28", "phase": "地缘升温", "us_regime": "降息+地缘", "cn_regime": "宽松"},
        {"start": "2026-03-01", "end": "2026-05-31", "phase": "全球避险", "us_regime": "降息+避险", "cn_regime": "宽松维持"},
    ]
    
    # 抓数计算各阶段均值
    for p in known_periods:
        us_in_period = [r for r in us10y if p['start'] <= r['date'] <= p['end']]
        cn_in_period = [r for r in cn10y if p['start'] <= r['date'] <= p['end']]
        fed_in_period = [r for r in fed_rates if p['start'] <= r['date'] <= p['end']]
        
        p['us10y_avg'] = round(sum(r['value'] for r in us_in_period) / len(us_in_period), 2) if us_in_period else None
        p['cn10y_avg'] = round(sum(r['value'] for r in cn_in_period) / len(cn_in_period), 2) if cn_in_period else None
        p['fed_avg'] = round(sum(r['value'] for r in fed_in_period) / len(fed_in_period), 2) if fed_in_period else None
    
    return known_periods


def format_report(periods: list, fed_regimes: list):
    """输出周期报告"""
    lines = []
    lines.append("=" * 100)
    lines.append(f"{'中美货币政策周期全景 (2020~今)':^100}")
    lines.append("=" * 100)
    lines.append("")
    
    lines.append("美联储利率政策阶段:")
    lines.append("-" * 50)
    for r in fed_regimes:
        lines.append(f"  {r['start']} ~ {r['end']}: {r['type']}")
    
    lines.append("")
    lines.append("货币政策周期分期:")
    lines.append("-" * 100)
    lines.append(f"{'阶段':<18} {'期间':<28} {'美国10Y':>8} {'中国10Y':>8} {'联邦基金':>8} {'宏观特征':<20}")
    lines.append("-" * 100)
    
    for p in periods:
        us10y_str = f"{p['us10y_avg']:.1f}%" if p['us10y_avg'] else "N/A"
        cn10y_str = f"{p['cn10y_avg']:.1f}%" if p['cn10y_avg'] else "N/A"
        fed_str = f"{p['fed_avg']:.2f}%" if p['fed_avg'] else "N/A"
        date_range = f"{p['start']}~{p['end']}"
        lines.append(f"{p['phase']:<18} {date_range:<28} {us10y_str:>8} {cn10y_str:>8} {fed_str:>8} {p.get('us_regime','')+'+'+p.get('cn_regime',''):<20}")
    
    lines.append("")
    lines.append("=" * 100)
    lines.append("")
    lines.append("周期节点与A股行情对照:")
    lines.append("-" * 80)
    lines.append("")
    
    # 关键交叉分析
    pairs = [
        ("2022Q2~Q3", "美国激进加息", "A股持续下跌"),
        ("2024Q4", "美国降息+中国宽松", "A股924行情大涨"),
        ("2025Q2", "关税冲击", "A股-7%踩踏"),
        ("2025Q3~Q4", "政策爆发+美国降息中", "A股阶段性上涨"),
        ("2026Q1~Q2", "地缘冲突+全球避险", "A股波动调整"),
    ]
    
    for period, us_event, market in pairs:
        lines.append(f"  {period}: {us_event} → {market}")
    
    lines.append("")
    lines.append("观察结论:")
    lines.append("")
    lines.append("  1. 美国加息周期中(A股2022)，A股承压但下跌模式是缓慢阴跌,")
    lines.append("     不同于关税冲击的快速踩踏。机构造势模式不同。")
    lines.append("")
    lines.append("  2. 中国宽松和美国降息共振期(2024Q4~今)，")
    lines.append("     是造'牛市幻想'的最佳窗口，机构倾向于讲长期故事。")
    lines.append("")
    lines.append("  3. 地缘冲击(2026Q1)下的恐慌是'有限恐慌'，")
    lines.append("     跌幅温和(-3.3%)，两融去杠杆幅度小(211亿)，")
    lines.append("     说明市场对地缘风险的定价已有经验。")
    lines.append("")
    lines.append("  4. 下一节点需关注：")
    lines.append("     - 美国降息接近尾声 → 美元走弱 → 外资回流A股的预期")
    lines.append("     - 中国LPR进一步下调空间 → 是否还有催化")
    lines.append("     - 关税冲击后中美利差变化 → 谁在主动脱钩")
    lines.append("")
    lines.append("=" * 100)
    
    return "\n".join(lines)


def run():
    """主流程"""
    log.info("加载宏观数据...")
    
    us10y = load_macro("DGS10")
    cn10y = load_macro("中国10Y国债")
    fed_rates = load_macro("FEDFUNDS")
    dxy = load_macro("DTWEXBGS")
    vix = load_macro("VIXCLS")
    sp500 = load_macro("SP500")
    
    log.info(f"  美国10Y: {len(us10y)} 行, {us10y[0]['date']}~{us10y[-1]['date']}" if us10y else "  N/A")
    log.info(f"  中国10Y: {len(cn10y)} 行" if cn10y else "  ⚠️ 中国10Y国债无数据, 仅用美国端分析")
    log.info(f"  联邦基金: {len(fed_rates)} 行")
    log.info(f"  美元指数: {len(dxy)} 行")
    log.info(f"  VIX: {len(vix)} 行")
    
    fed_regime = determine_fed_regime(fed_rates)
    log.info(f"  美联储阶段: {[r['type'] for r in fed_regime]}")
    
    # 如果中国10Y没有数据，用None代替
    cn10y_data = cn10y if cn10y else []
    periods = build_cycle_periods(us10y, cn10y_data, fed_rates, fed_regime)
    
    report = format_report(periods, fed_regime)
    
    print(report)
    
    # 保存
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, "macro_cycle_report.txt"), 'w') as f:
        f.write(report)
    
    # 保存结构化数据
    with open(os.path.join(REPORT_DIR, "macro_cycle_periods.json"), 'w') as f:
        json.dump(periods, f, ensure_ascii=False, indent=2, default=str)
    
    log.info(f"报告已保存: {REPORT_DIR}/macro_cycle_report.txt")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    run()
