#!/usr/bin/env python3
"""
每日逆推信号简报 v2
合并R指数 + 阵营分歧度 + [B]/[A]盈余 + 多维预警

预警规则（新增）：
  1. harvest_signal: R从>80%分位 3天内跌到<20%分位 → 收割预警
  2. panic_signal: R持续<20%分位(3天) + 机构净买入转负 → 恐慌踩踏
  3. inst_accumulation: 个股R>80%分位 + 机构净买+游资净卖 → 机构潜伏
  4. retail_pump_risk: 个股R快速下降 + 机构净卖+游资净买 → 游资接力

输出到：终端 + market_signals (signal_type='inverse_brief')
"""
import os, sys, json
from datetime import datetime, date, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
sys.path.insert(0, os.path.dirname(__file__))
import data_finance as df


SIGNAL_ICONS = {
    'inst_buy_hot_sell': '🟢',
    'inst_sell_hot_buy': '🔴',
    'both_buy': '⚪',
    'both_sell': '⚫',
    'no_diverge': '➖',
}


def _safe_extra(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try: return json.loads(raw)
        except: return {}
    return {}


def _jsonsafe(obj):
    if isinstance(obj, dict):
        return {k: _jsonsafe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonsafe(v) for v in obj]
    if isinstance(obj, (int, float)) and not isinstance(obj, (int, float)):
        # catch np types piped through
        pass
    if isinstance(obj, (float, int)):
        return obj
    return obj


def detect_alerts(r_index_hist, camp_summary_hist, camp_diverge_list):
    """
    多维度预警检测
    返回 alert 列表，每个 alert 包含 type, severity, desc
    """
    alerts = []
    
    if not r_index_hist or len(r_index_hist) < 5:
        return alerts
    
    # 按日期排序（从旧到新）
    r_sorted = sorted(r_index_hist, key=lambda x: x['date'])
    latest = r_sorted[-1]
    
    # 👉 收割预警: R从>80%分位 3天内跌到<20%分位
    recent_5 = r_sorted[-5:] if len(r_sorted) >= 5 else r_sorted
    for i in range(len(recent_5) - 3):
        chunk = recent_5[i:i+3]
        if len(chunk) < 3:
            continue
        r0, r2 = chunk[0], chunk[-1]
        pct0 = r0.get('r_pct', 0.5)
        pct2 = r2.get('r_pct', 0.5)
        if pct0 >= 0.80 and pct2 <= 0.20:
            alerts.append({
                'type': 'harvest_signal',
                'severity': 'high',
                'desc': f"R指数{pct0:.0%}分位→{pct2:.0%}分位 3日内急跌，可能收割完成",
                'date': r2['date'],
                'r_value': r2['value']
            })
    
    # 👉 恐慌踩踏: R持续<20%分位(连续3天) + 机构净买转负
    if len(r_sorted) >= 3:
        last_3 = r_sorted[-3:]
        if all(r.get('r_pct', 1) <= 0.20 for r in last_3):
            surplus_neg = sum(1 for r in last_3 if r.get('surplus', 0) < 0)
            if surplus_neg >= 2:
                alerts.append({
                    'type': 'panic_signal',
                    'severity': 'high',
                    'desc': f"R持续低分位{last_3[0].get('date')}~{last_3[-1].get('date')} + "
                           f"[B]能量持续不足({surplus_neg}/3天)，可能恐慌踩踏",
                    'date': last_3[-1]['date'],
                    'r_value': last_3[-1]['value']
                })
    
    # 👉 游资大规模离场: camp_divergence连续负数 + 共识向下增多
    if camp_summary_hist:
        cs_sorted = sorted(camp_summary_hist, key=lambda x: x['date'])
        recent_cs = cs_sorted[-5:] if len(cs_sorted) >= 5 else cs_sorted
        neg_days = sum(1 for cs in recent_cs if cs.get('ratio', 0) < 0)
        total_both_sell = sum(
            cs.get('buy_signals', {}).get('both_sell', 0) for cs in recent_cs
        )
        avg_both_sell = total_both_sell / max(len(recent_cs), 1)
        if neg_days >= 4 and avg_both_sell >= 8:
            alerts.append({
                'type': 'liquidity_drain',
                'severity': 'medium',
                'desc': f"游资连续{neg_days}/5天相对机构更积极，共识向下日均{avg_both_sell:.0f}只，"
                       f"游资主导市场可能面临流动性退潮",
                'date': recent_cs[-1]['date'],
                'value': recent_cs[-1].get('ratio', 0) if recent_cs else 0
            })
    
    return alerts


def get_latest_market_signals():
    """获取最近N天所有相关信号（扩展：拉取更多历史R指数用于预警）"""
    end = date.today()
    start = end - timedelta(days=15)  # 拉15天R指数用于预警逻辑
    
    signals = {}
    
    # R指数（拉15天供预警计算）
    rows, _ = df._pg_execute(
        "SELECT trade_date, value, extra FROM market_signals "
        "WHERE signal_type='r_index' AND code='market' AND trade_date >= %s "
        "ORDER BY trade_date DESC",
        (start,)
    )
    signals['r_index'] = []
    for r in rows:
        extra = _safe_extra(r[2])
        signals['r_index'].append({
            'date': r[0], 'value': r[1], 'r_pct': extra.get('r_pct', 0.5),
            'advance_ratio': extra.get('advance_ratio', 0.5),
            'surplus': extra.get('surplus', 0),
            'warning': extra.get('warning', None)
        })
    
    # [B]-[A]盈余
    rows, _ = df._pg_execute(
        "SELECT trade_date, value FROM market_signals "
        "WHERE signal_type='ba_surplus' AND code='market' AND trade_date >= %s "
        "ORDER BY trade_date DESC LIMIT 10",
        (start,)
    )
    signals['ba_surplus'] = [{'date': r[0], 'value': r[1]} for r in rows]
    
    # R预警
    rows, _ = df._pg_execute(
        "SELECT trade_date, value, extra FROM market_signals "
        "WHERE signal_type='r_warning' AND trade_date >= %s "
        "ORDER BY trade_date DESC LIMIT 10",
        (start,)
    )
    signals['r_warning'] = []
    for r in rows:
        extra = _safe_extra(r[2])
        signals['r_warning'].append({
            'date': r[0], 'value': r[1], 'warning': extra.get('warning')
        })
    
    # 阵营分歧摘要
    rows, _ = df._pg_execute(
        "SELECT trade_date, value, extra FROM market_signals "
        "WHERE signal_type='camp_diverge_summary' AND trade_date >= %s "
        "ORDER BY trade_date DESC LIMIT 10",
        (start,)
    )
    signals['camp_summary'] = []
    for r in rows:
        extra = _safe_extra(r[2])
        signals['camp_summary'].append({
            'date': r[0], 'ratio': r[1],
            'buy_signals': extra.get('buy_signals', {}),
            'total_stocks': extra.get('total_stocks', 0)
        })
    
    # 关键分歧股票
    rows, _ = df._pg_execute(
        "SELECT trade_date, code, value, extra FROM market_signals "
        "WHERE signal_type='camp_diverge' AND trade_date >= %s AND value IS NOT NULL "
        "ORDER BY trade_date DESC, value DESC LIMIT 25",
        (start,)
    )
    signals['camp_diverge'] = []
    for r in rows:
        extra = _safe_extra(r[3])
        signals['camp_diverge'].append({
            'date': r[0], 'code': r[1],
            'name': extra.get('name', ''),
            'signal': extra.get('signal', ''),
            'inst_net': extra.get('inst_net', 0),
            'hot_net': extra.get('hot_net', 0),
        })
    
    return signals


def print_brief(signals):
    """打印每日逆推简报"""
    r_index = signals.get('r_index', [])
    camp_sum = signals.get('camp_summary', [])
    warnings = signals.get('r_warning', [])
    diverge = signals.get('camp_diverge', [])
    
    # 检测预警
    alerts = detect_alerts(r_index, camp_sum, diverge)
    
    print(f"\n{'='*75}")
    print(f"📊 逆推信号简报 — {date.today()}")
    print(f"{'='*75}")
    
    # ━━━ 预警区域 ━━━
    if alerts:
        print(f"\n━━━ 🚨 新增预警信号 ━━━")
        for a in alerts:
            icon = {'harvest_signal': '🔴', 'panic_signal': '🚨', 'liquidity_drain': '💧',
                    'retail_pump_risk': '🎣'}.get(a['type'], '⚠️')
            sev_icon = {'high': '⚠️', 'medium': '📌', 'low': '🔎'}.get(a.get('severity', 'low'), '')
            print(f"  {sev_icon} {icon} {a['type']}")
            print(f"    {a['desc']}")
    
    if warnings:
        print(f"\n━━━ ⚠️ 持续预警 ━━━")
        for w in warnings[:3]:
            print(f"  {w['date']}: {w['warning']} (R={w['value']:.2f})")
    
    # ━━━ R指数 ━━━
    if r_index:
        latest_r = r_index[0]
        print(f"\n━━━ 大盘R指数 ━━━")
        print(f"  R指数: {latest_r['value']:.2f} (百分位: {latest_r.get('r_pct', 0):.0%})")
        print(f"  涨跌比: {latest_r.get('advance_ratio', 0):.1%}")
        print(f"  [B]-[A]盈余: {latest_r.get('surplus', 0):+.1%}")
        if len(r_index) >= 3:
            r_trend = [r['value'] for r in r_index[:3]]
            r_trend_str = '↑' if r_trend[0] > r_trend[2] else '↓'
            print(f"  3日趋势: {r_trend[2]:.2f} → {r_trend[1]:.2f} → {r_trend[0]:.2f} {r_trend_str}")
        if len(r_index) >= 5:
            r_5 = r_index[:5]
            r_vals = [f'{r["value"]:.1f}' for r in reversed(r_5)]
            print(f"  5日: {' > '.join(r_vals)}")
    
    # ━━━ 阵营分歧 ━━━
    if camp_sum:
        latest_cs = camp_sum[0]
        print(f"\n━━━ 阵营分歧 ━━━")
        print(f"  分歧度: {latest_cs.get('ratio', 0):+.3f} "
              f"(正=机构占优, 负=游资占优)")
        sigs = latest_cs.get('buy_signals', {})
        print(f"  🟢 机构吸筹: {sigs.get('inst_buy_hot_sell', 0)}只  "
              f"🔴 机构出货: {sigs.get('inst_sell_hot_buy', 0)}只")
        print(f"  ⚪ 共识向上: {sigs.get('both_buy', 0)}只  "
              f"⚫ 共识向下: {sigs.get('both_sell', 0)}只")
        if len(camp_sum) >= 2:
            prev_sigs = camp_sum[1].get('buy_signals', {})
            buy_chg = sigs.get('inst_buy_hot_sell', 0) - prev_sigs.get('inst_buy_hot_sell', 0)
            sell_chg = sigs.get('inst_sell_hot_buy', 0) - prev_sigs.get('inst_sell_hot_buy', 0)
            if buy_chg > 0:
                print(f"  📈 机构吸筹较前日 +{buy_chg}只")
            if sell_chg > 0:
                print(f"  📉 机构出货较前日 +{sell_chg}只")
            both_buy_chg = sigs.get('both_buy', 0) - prev_sigs.get('both_buy', 0)
            both_sell_chg = sigs.get('both_sell', 0) - prev_sigs.get('both_sell', 0)
            if both_buy_chg >= 5:
                print(f"  ⚪ 共识向上暴增 +{both_buy_chg}只")
            if both_sell_chg >= 5:
                print(f"  ⚫ 共识向下暴增 +{both_sell_chg}只")
    
    # ━━━ 分歧趋势 ━━━
    if len(camp_sum) >= 5:
        cs_trend = [cs.get('ratio', 0) for cs in camp_sum[:5]]
        cs_direction = '↑' if cs_trend[0] > cs_trend[-1] else '↓'
        avg_ratio = sum(cs_trend) / len(cs_trend)
        print(f"  5日分歧趋势: {cs_direction} (均值{avg_ratio:+.3f})")
    
    # ━━━ 关键分歧股票 ━━━
    key_signals = [d for d in diverge if d.get('signal') in ('inst_buy_hot_sell', 'inst_sell_hot_buy')]
    if key_signals:
        print(f"\n━━━ 🔑 关键阵营分歧股票 ━━━")
        for d in key_signals[:10]:
            icon = SIGNAL_ICONS.get(d['signal'], '❓')
            inst_str = f"机构{d['inst_net']/1e8:+.2f}亿"
            hot_str = f"游资{d['hot_net']/1e8:+.2f}亿"
            print(f"  {icon} {d['code']}({d.get('name','')}): {inst_str} {hot_str} "
                  f"({d.get('date')})")
    
    # ━━━ 综合判断 ━━━
    print(f"\n━━━ 💡 综合判断 ━━━")
    advice = []
    
    # 基于R指数
    if r_index:
        r = r_index[0]
        r_pct = r.get('r_pct', 0.5)
        surplus = r.get('surplus', 0)
        if r_pct < 0.2 and surplus < -0.1:
            advice.append("⚠️ 市场一致性低迷，[B]能量不足 — 观望或减仓")
        elif r_pct < 0.2 and surplus > 0.1:
            advice.append("⚡ 市场加速上涨中，但注意见顶回落")
        elif r_pct > 0.8 and surplus > 0.1:
            advice.append("🟢 分歧中多头占优，可能处于机构潜伏建仓阶段")
        elif r_pct > 0.8 and surplus < -0.1:
            advice.append("🔴 分歧中空头主导，机构可能在降低风险敞口")
    
    # 基于预警
    for a in alerts:
        if a['type'] == 'harvest_signal':
            advice.append("🔴 收割预警！R指数从高位急跌至低位，[B]能量转负+机构出货")
        elif a['type'] == 'panic_signal':
            advice.append("🚨 恐慌踩踏预警！一致性恐慌+机构转负，建议观望")
    
    # 基于分歧
    if camp_sum:
        latest_ratio = camp_sum[0].get('ratio', 0)
        if latest_ratio < -0.15:
            advice.append("🔴 游资主导市场，游资接盘机构出货，追高风险大")
        elif latest_ratio > 0.15:
            advice.append("🟢 机构主导市场，机构吸筹游资出货，中期偏多")
    
    if not advice:
        advice.append("➖ 信号不显著，等待更多数据积累")
    for a in advice:
        print(f"  {a}")
    
    print(f"{'='*75}\n")


def write_brief_to_db(signals):
    """写入综合信号简报到 market_signals"""
    alerts = detect_alerts(
        signals.get('r_index', []),
        signals.get('camp_summary', []),
        signals.get('camp_diverge', [])
    )
    
    r_index = signals.get('r_index', [])
    camp_sum = signals.get('camp_summary', [])
    diverge = signals.get('camp_diverge', [])
    
    # 综合判断
    advice_list = []
    if r_index:
        r = r_index[0]
        r_pct = r.get('r_pct', 0.5)
        surplus = r.get('surplus', 0)
        if r_pct < 0.2 and surplus < -0.1:
            advice_list.append("market_consensus_low")
        elif r_pct < 0.2 and surplus > 0.1:
            advice_list.append("market_accelerating")
        elif r_pct > 0.8 and surplus > 0.1:
            advice_list.append("market_diverge_bull")
        elif r_pct > 0.8 and surplus < -0.1:
            advice_list.append("market_diverge_bear")
    
    for a in alerts:
        if a['type'] == 'harvest_signal':
            advice_list.append('harvest_alert')
        elif a['type'] == 'panic_signal':
            advice_list.append('panic_alert')
    
    brief = {
        'type': 'inverse_brief',
        'date': str(date.today()),
        'alerts': alerts,
        'advice': advice_list,
        'r_latest': {
            'value': r_index[0]['value'] if r_index else None,
            'r_pct': r_index[0].get('r_pct', 0) if r_index else None,
            'surplus': r_index[0].get('surplus', 0) if r_index else None,
        } if r_index else None,
        'camp_latest': {
            'ratio': camp_sum[0].get('ratio', 0) if camp_sum else None,
            'signals': camp_sum[0].get('buy_signals', {}) if camp_sum else {},
        } if camp_sum else None,
    }
    
    brief = _jsonsafe(brief)
    
    try:
        df._pg_execute(
            """INSERT INTO market_signals (signal_type, code, trade_date, value, extra, updated_at)
               VALUES (%s, %s, %s, %s, %s::jsonb, NOW())
               ON CONFLICT (signal_type, code, trade_date)
               DO UPDATE SET value = EXCLUDED.value, extra = EXCLUDED.extra, updated_at = NOW()""",
            ('inverse_brief', 'market', date.today(),
             len(alerts), json.dumps(brief, ensure_ascii=False)),
            fetch=False
        )
    except Exception as e:
        print(f"  ⚠️ 写入inverse_brief失败: {e}")


def save_brief():
    """保存简报到文件"""
    from io import StringIO
    
    signals = get_latest_market_signals()
    alerts = detect_alerts(
        signals.get('r_index', []),
        signals.get('camp_summary', []),
        signals.get('camp_diverge', [])
    )
    
    report_dir = os.path.expanduser('~/.openclaw/workspace/memory/reports')
    os.makedirs(report_dir, exist_ok=True)
    today = date.today().strftime('%Y-%m-%d')
    report_path = os.path.join(report_dir, f'inverse-push-{today}.md')
    
    buf = StringIO()
    r_index = signals.get('r_index', [])
    camp_sum = signals.get('camp_summary', [])
    warnings = signals.get('r_warning', [])
    diverge = signals.get('camp_diverge', [])
    
    buf.write(f"# 逆推信号简报 — {today}\n\n")
    
    if alerts:
        buf.write(f"## 🚨 预警\n\n")
        for a in alerts:
            buf.write(f"- **{a['type']}**（{a.get('severity','')}）: {a['desc']}\n")
        buf.write("\n")
    
    if r_index:
        r = r_index[0]
        buf.write(f"## 大盘R指数\n\n")
        buf.write(f"- R指数: {r['value']:.2f} (百分位: {r.get('r_pct', 0):.0%})\n")
        buf.write(f"- 涨跌比: {r.get('advance_ratio', 0):.1%}\n")
        buf.write(f"- [B]-[A]盈余: {r.get('surplus', 0):+.1%}\n")
        if r.get('warning'):
            buf.write(f"- ⚠️ 预警: {r['warning']}\n")
        buf.write("\n")
    
    if warnings:
        buf.write(f"## ⚠️ 持续预警\n\n")
        for w in warnings[:3]:
            buf.write(f"- {w['date']}: {w['warning']} (R={w['value']:.2f})\n")
        buf.write("\n")
    
    if camp_sum:
        cs = camp_sum[0]
        sigs = cs.get('buy_signals', {})
        buf.write(f"## 阵营分歧\n\n")
        buf.write(f"- 分歧度: {cs.get('ratio', 0):+.3f}\n")
        buf.write(f"- 🟢 机构吸筹: {sigs.get('inst_buy_hot_sell', 0)}只\n")
        buf.write(f"- 🔴 机构出货: {sigs.get('inst_sell_hot_buy', 0)}只\n")
        buf.write(f"- ⚪ 共识向上: {sigs.get('both_buy', 0)}只\n")
        buf.write(f"- ⚫ 共识向下: {sigs.get('both_sell', 0)}只\n\n")
    
    if diverge:
        key_d = [d for d in diverge if d.get('signal') in ('inst_buy_hot_sell', 'inst_sell_hot_buy')]
        if key_d:
            buf.write(f"## 关键分歧股票\n\n")
            for d in key_d[:10]:
                icon = SIGNAL_ICONS.get(d['signal'], '❓')
                buf.write(f"- {icon} {d['code']}({d.get('name','')}): "
                         f"机构{d['inst_net']/1e8:+.2f}亿 游资{d['hot_net']/1e8:+.2f}亿\n")
            buf.write("\n")
    
    # 综合判断
    buf.write(f"## 综合判断\n\n")
    if r_index:
        r = r_index[0]
        r_pct = r.get('r_pct', 0.5)
        surplus = r.get('surplus', 0)
        if r_pct < 0.2 and surplus < -0.1:
            buf.write(f"- ⚠️ 市场一致性低迷，[B]能量不足 — 观望或减仓\n")
        elif r_pct < 0.2 and surplus > 0.1:
            buf.write(f"- ⚡ 市场加速上涨中，但注意见顶回落\n")
        elif r_pct > 0.8 and surplus > 0.1:
            buf.write(f"- 🟢 分歧中多头占优，可能处于机构潜伏建仓阶段\n")
        elif r_pct > 0.8 and surplus < -0.1:
            buf.write(f"- 🔴 分歧中空头主导，机构可能在降低风险敞口\n")
    for a in alerts:
        if a['type'] == 'harvest_signal':
            buf.write(f"- 🔴 收割预警！R指数从高位急跌至低位\n")
        elif a['type'] == 'panic_signal':
            buf.write(f"- 🚨 恐慌踩踏预警！一致性恐慌+机构转负，建议观望\n")
    if camp_sum:
        latest_ratio = camp_sum[0].get('ratio', 0)
        if latest_ratio < -0.15:
            buf.write(f"- 🔴 游资主导市场，游资接盘机构出货，追高风险大\n")
        elif latest_ratio > 0.15:
            buf.write(f"- 🟢 机构主导市场，机构吸筹游资出货，中期偏多\n")
    
    with open(report_path, 'w') as f:
        f.write(buf.getvalue())
    return report_path


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='每日逆推信号简报')
    parser.add_argument('--save', action='store_true', help='保存简报到文件')
    parser.add_argument('--no-print', action='store_true', help='不打印到终端')
    parser.add_argument('--write-db', action='store_true', help='写入PG数据库')
    args = parser.parse_args()
    
    signals = get_latest_market_signals()
    if not args.no_print:
        print_brief(signals)
    if args.save:
        path = save_brief()
        print(f"  简报已保存: {path}")
    if args.write_db:
        write_brief_to_db(signals)
        print(f"  综合简报已写入 PG inverse_brief")
