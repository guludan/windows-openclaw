#!/usr/bin/env python3
"""
多案例造势检测框架 — 对选定的事件窗口，计算统一的造势指标

对比案例：
1. 2025-04 关税冲击：事件驱动→周末放大→踩踏
2. 2025-09 政治局行情：政策驱动→牛市幻想
3. 2026-03 地缘调整：持续下跌→局部踩踏

统一的造势指标集（每个案例一致计算）：
  1. 事件→认知延迟天数
  2. 周末放大倍数（事件在周三到周五发生 vs 周末后反应）
  3. 涨跌停比突变峰值
  4. 两融日最大去杠杆金额
  5. 涨跌家数比反转速度
  6. 恐慌持续时间（跌停>2%的天数）
  7. 修复速度（从底部到收复一半跌幅的天数）

输出：结构化数据 + 对比表格
"""
import sys, os, json, logging
import numpy as np
from datetime import date, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import data_finance as df

log = logging.getLogger('market_psychology')

REPORT_DIR = "/mnt/d/data/think-tank-data/market_psychology"

# ============ CASE DEFINITIONS ============

CASES = [
    {
        "id": "tariff_202504",
        "name": "关税冲击",
        "start": "2025-04-01",
        "end": "2025-04-15",
        "weekend_before": "2025-04-04",
        "weekend_after": "2025-04-07",
        "trigger_date": "2025-04-02",  # 关税宣布
        "desc": "美国宣布对华加征关税，周末发酵后4/7踩踏-7%",
    },
    {
        "id": "policy_202509",
        "name": "政治局行情",
        "start": "2025-09-01",
        "end": "2025-09-15",
        "weekend_before": "2025-09-05",
        "weekend_after": "2025-09-08",
        "trigger_date": "2025-09-04",
        "desc": "政治局会议政策组合拳，市场先跌后涨，造牛市起点幻想",
    },
    {
        "id": "correction_202603",
        "name": "地缘调整",
        "start": "2026-03-16",
        "end": "2026-03-30",
        "weekend_before": "2026-03-21",
        "weekend_after": "2026-03-23",
        "trigger_date": "2026-03-23",
        "desc": "地缘冲突引发连续下跌，局部踩踏",
    },
]


# ============ DATA LOADING ============

def load_index_data(case_start, case_end):
    """沪深300日线"""
    rows, _ = df._pg_execute(
        "SELECT trade_date::text, close FROM daily_prices WHERE symbol='I.000300' "
        "AND trade_date BETWEEN %s::date AND %s::date ORDER BY trade_date",
        (case_start, case_end))
    result = {}
    prev = None
    for r in rows:
        dt = r[0]
        close = float(r[1])
        chg = 0.0
        if prev:
            chg = round((close - prev) / prev * 100, 2)
        result[dt] = {'close': close, 'chg_pct': chg}
        prev = close
    return result


def load_market_daily(case_start, case_end):
    """逐日全市场涨跌停+涨跌家数"""
    rows, _ = df._pg_execute(
        "WITH pc AS ("
        "  SELECT trade_date::text, symbol, close, "
        "  lag(close) OVER (PARTITION BY symbol ORDER BY trade_date) AS prev_close "
        "  FROM daily_prices WHERE symbol LIKE 'A.%%' "
        "  AND trade_date BETWEEN %s::date AND %s::date"
        ") "
        "SELECT trade_date, count(*), "
        "  sum(case when close/prev_close > 1.095 then 1 else 0 end) as lu, "
        "  sum(case when close/prev_close < 0.905 then 1 else 0 end) as ld, "
        "  sum(case when close > prev_close then 1 else 0 end) as up, "
        "  sum(case when close < prev_close then 1 else 0 end) as down "
        "FROM pc WHERE prev_close > 0 "
        "GROUP BY trade_date ORDER BY trade_date",
        (case_start, case_end))
    
    result = {}
    for r in rows:
        dt = r[0]
        total = int(r[1])
        lu, ld = int(r[2]), int(r[3])
        up, down = int(r[4]), int(r[5])
        result[dt] = {
            'total': total,
            'limit_up': lu,
            'limit_down': ld,
            'up': up,
            'down': down,
            'ld_pct': round(ld / total * 100, 1) if total > 0 else 0,
            'lu_pct': round(lu / total * 100, 1) if total > 0 else 0,
            'up_pct': round(up / total * 100, 1) if total > 0 else 0,
            'down_pct': round(down / total * 100, 1) if total > 0 else 0,
            'up_down_ratio': round(up / down, 2) if down > 0 else 99.99,
        }
    return result


def load_margin_data(case_start, case_end):
    """两融余额（上交所）"""
    rows, _ = df._pg_execute(
        "SELECT trade_date::text, value, change_val, change_pct "
        "FROM market_signals WHERE signal_type='margin_sh' AND code='TOTAL' "
        "AND trade_date BETWEEN %s::date AND %s::date ORDER BY trade_date",
        (case_start, case_end))
    result = {}
    prev = 0
    for r in rows:
        dt = r[0]
        val = float(r[1]) / 1e8 if r[1] else 0
        margin_buy = float(r[3]) / 1e8 if r[3] else 0
        daily_chg = val - prev if prev > 0 else 0
        result[dt] = {
            'margin': round(val, 0),
            'margin_chg': round(daily_chg, 0),
            'margin_buy': round(margin_buy, 0),
        }
        prev = val
    return result


# ============ METRIC CALCULATION ============

def calc_metrics(case):
    """对单个案例计算全部造势指标"""
    name = case['name']
    idx_data = load_index_data(case['start'], case['end'])
    mkt_data = load_market_daily(case['start'], case['end'])
    mg_data = load_margin_data(case['start'], case['end'])
    
    dates_all = sorted(set(list(idx_data.keys()) + list(mkt_data.keys()) + list(mg_data.keys())))
    
    metrics = {
        'case_id': case['id'],
        'case_name': name,
        'description': case['desc'],
        'total_trading_days': len([d for d in dates_all if d in idx_data]),
        'metrics': {},
    }
    
    # 1. 最大单日指数跌幅
    max_drop_date = None
    max_drop = 0.0
    for d in dates_all:
        if d in idx_data:
            chg = idx_data[d]['chg_pct']
            if chg < max_drop:
                max_drop = chg
                max_drop_date = d
    metrics['metrics']['max_index_drop'] = {
        'date': max_drop_date,
        'value': round(max_drop, 2),
        'label': f"最大单日跌幅 {max_drop:.2f}% ({max_drop_date})"
    }
    
    # 2. 跌停占比峰值
    ld_peak = 0.0
    ld_peak_date = None
    for d in dates_all:
        if d in mkt_data:
            pct = mkt_data[d]['ld_pct']
            if pct > ld_peak:
                ld_peak = pct
                ld_peak_date = d
    metrics['metrics']['limit_down_peak'] = {
        'date': ld_peak_date,
        'value': round(ld_peak, 1),
        'label': f"跌停占比峰值 {ld_peak:.1f}% ({ld_peak_date})"
    }
    
    # 3. 涨停占比峰值
    lu_peak = 0.0
    lu_peak_date = None
    for d in dates_all:
        if d in mkt_data:
            pct = mkt_data[d]['lu_pct']
            if pct > lu_peak:
                lu_peak = pct
                lu_peak_date = d
    metrics['metrics']['limit_up_peak'] = {
        'date': lu_peak_date,
        'value': round(lu_peak, 1),
        'label': f"涨停占比峰值 {lu_peak:.1f}% ({lu_peak_date})"
    }
    
    # 4. 涨跌比最小值
    ud_min = 999.0
    ud_min_date = None
    for d in dates_all:
        if d in mkt_data:
            udr = mkt_data[d]['up_down_ratio']
            if udr < ud_min:
                ud_min = udr
                ud_min_date = d
    metrics['metrics']['up_down_ratio_min'] = {
        'date': ud_min_date,
        'value': round(ud_min, 3),
        'label': f"涨跌比最小值 {ud_min:.3f} ({ud_min_date})"
    }
    
    # 5. 两融日最大去杠杆
    mg_max_chg = 99999.0
    mg_max_chg_date = None
    for d in dates_all:
        if d in mg_data:
            chg = abs(mg_data[d]['margin_chg'])  # 取绝对值最大的下降
            if chg > 0 and chg < mg_max_chg:  # 找最负的
                # 实际上应该找最负的（下降最大）
                pass
    # 重新算：找最大下降
    mg_max_drop = 0.0
    mg_max_drop_date = None
    for d in dates_all:
        if d in mg_data:
            chg = mg_data[d]['margin_chg']
            if chg < mg_max_drop:
                mg_max_drop = chg
                mg_max_drop_date = d
    metrics['metrics']['max_margin_drop'] = {
        'date': mg_max_drop_date,
        'value': round(abs(mg_max_drop), 0),
        'label': f"两融最大日降幅 {abs(mg_max_drop):.0f}亿 ({mg_max_drop_date})"
    }
    
    # 6. 事件→认知延迟（触发日到最大跌幅的天数）
    trigger = case['trigger_date']
    if max_drop_date:
        from datetime import date as dt_date
        td_start = dt_date.fromisoformat(trigger)
        td_end = dt_date.fromisoformat(max_drop_date)
        delay = (td_end - td_start).days
        metrics['metrics']['event_cognition_lag'] = {
            'trigger': trigger,
            'max_impact': max_drop_date,
            'value': delay,
            'label': f"事件→认知延迟: {delay}天 (含周末)"
        }
    
    # 7. 周末放大效应
    wb = case.get('weekend_before')
    wa = case.get('weekend_after')
    if wb and wa:
        before_chg = idx_data.get(wb, {}).get('chg_pct', 0)
        after_chg = idx_data.get(wa, {}).get('chg_pct', 0)
        amplification = round(after_chg / before_chg, 1) if before_chg != 0 else 0
        metrics['metrics']['weekend_amplification'] = {
            'before_date': wb,
            'before_chg': round(before_chg, 2),
            'after_date': wa,
            'after_chg': round(after_chg, 2),
            'amplification': amplification,
            'label': f"周末放大: {before_chg:.2f}%→{after_chg:.2f}% ({amplification}x)"
        }
    
    # 8. 恐慌持续天数（跌停>2%的天数）
    panic_days = [d for d in dates_all if d in mkt_data and mkt_data[d]['ld_pct'] > 2.0]
    metrics['metrics']['panic_duration'] = {
        'days': len(panic_days),
        'dates': panic_days,
        'label': f"恐慌持续{len(panic_days)}天 (跌停>2%): {', '.join(str(d) for d in panic_days)}"
    }
    
    # 9. 修复速度：从底部到反弹回一半跌幅的天数
    # 需要连续日期
    if max_drop_date:
        close_at_bottom = idx_data[max_drop_date]['close'] if max_drop_date in idx_data else 0
        trigger_close = idx_data.get(trigger, {}).get('close', 0)
        if trigger_close > 0 and close_at_bottom > 0:
            half_recovery = trigger_close - (trigger_close - close_at_bottom) / 2
            recovery_days = None
            for d in dates_all:
                if d in idx_data and d > max_drop_date:
                    if idx_data[d]['close'] >= half_recovery:
                        rd_start = dt_date.fromisoformat(max_drop_date)
                        rd_end = dt_date.fromisoformat(d)
                        recovery_days = (rd_end - rd_start).days
                        recovery_date = d
                        break
            if recovery_days is not None:
                metrics['metrics']['recovery_speed'] = {
                    'from_date': max_drop_date,
                    'to_date': recovery_date,
                    'days': recovery_days,
                    'half_recovery_level': round(half_recovery, 1),
                    'label': f"修复速度: {recovery_days}天回到一半跌幅 (触发前{trigger_close:.0f}→底部{close_at_bottom:.0f}→半程{half_recovery:.0f})"
                }
    
    # 10. 两融去杠杆总量
    mg_dates_with_data = [d for d in dates_all if d in mg_data]
    if len(mg_dates_with_data) >= 2:
        mg_start = mg_data[mg_dates_with_data[0]]['margin']
        mg_end = mg_data[mg_dates_with_data[-1]]['margin']
        total_delever = mg_start - mg_end
        metrics['metrics']['total_deleveraging'] = {
            'start_val': round(mg_start, 0),
            'end_val': round(mg_end, 0),
            'total_drop': round(total_delever, 0),
            'label': f"两融去杠杆总量: {mg_start:.0f}亿→{mg_end:.0f}亿 (减少{total_delever:.0f}亿)"
        }
    
    return metrics


# ============ FORMATTING ============

def format_metrics_table(all_metrics):
    """所有案例指标对比表"""
    lines = []
    lines.append("=" * 100)
    lines.append("    市场造势模式 — 多案例指标对比")
    lines.append("=" * 100)
    lines.append("")
    
    # 选核心指标名
    core_metrics = [
        'max_index_drop', 'limit_down_peak', 'limit_up_peak',
        'up_down_ratio_min', 'max_margin_drop', 'event_cognition_lag',
        'weekend_amplification', 'panic_duration', 'recovery_speed',
        'total_deleveraging'
    ]
    
    metric_labels = {
        'max_index_drop': '最大单日跌幅',
        'limit_down_peak': '跌停占比峰值',
        'limit_up_peak': '涨停占比峰值',
        'up_down_ratio_min': '涨跌比最小值',
        'max_margin_drop': '两融日最大降幅',
        'event_cognition_lag': '事件→认知延迟',
        'weekend_amplification': '周末放大效应',
        'panic_duration': '恐慌持续天数',
        'recovery_speed': '修复速度(到半程)',
        'total_deleveraging': '两融去杠杆总量',
    }
    
    # 表头
    headers = ['指标'] + [m['case_name'] for m in all_metrics]
    col_width = max(len(h) for h in headers) + 2
    lines.append("  " + "".join(h.ljust(col_width) for h in ['指标', '关税冲击', '政治局行情', '地缘调整']))
    lines.append("  " + "-" * (col_width * len(headers)))
    
    for mk in core_metrics:
        row_label = metric_labels.get(mk, mk).ljust(col_width)
        row = f"  {row_label}"
        for m in all_metrics:
            val = m['metrics'].get(mk, {})
            lbl = val.get('label', 'N/A')
            # 截断到col_width
            short_lbl = lbl[:col_width - 1] if len(lbl) > col_width - 1 else lbl
            row += short_lbl.ljust(col_width)
        lines.append(row)
    
    lines.append("")
    lines.append("=" * 100)
    
    return "\n".join(lines)


def format_case_detail(metrics):
    """单个案例的详情"""
    lines = []
    lines.append(f"\n\n{'='*60}")
    lines.append(f"  案例: {metrics['case_name']}")
    lines.append(f"  描述: {metrics['description']}")
    lines.append(f"{'='*60}")
    for mk, mv in sorted(metrics['metrics'].items()):
        lbl = mv.get('label', str(mv))
        lines.append(f"  • {lbl}")
    return "\n".join(lines)


def run_all():
    """跑全部案例"""
    all_results = []
    for case in CASES:
        log.info(f"处理案例: {case['name']}")
        metrics = calc_metrics(case)
        all_results.append(metrics)
        print(format_case_detail(metrics))
    
    print("\n\n")
    print(format_metrics_table(all_results))
    
    # 保存
    os.makedirs(REPORT_DIR, exist_ok=True)
    out_path = os.path.join(REPORT_DIR, "multi_case_metrics.json")
    with open(out_path, 'w') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    log.info(f"已保存: {out_path}")
    
    out_txt = os.path.join(REPORT_DIR, "multi_case_comparison.txt")
    with open(out_txt, 'w') as f:
        f.write(format_metrics_table(all_results))
    log.info(f"对比表已保存: {out_txt}")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    run_all()
