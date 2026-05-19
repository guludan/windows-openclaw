#!/usr/bin/env python3
"""信号分析 — 两融/龙虎榜/ETF份额/外资ETF"""
import sys, os, logging
from datetime import date, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import data_finance as df

log = logging.getLogger('analysis.signals')


def margin_market_daily() -> str:
    """两融余额日报"""
    rows, _ = df._pg_execute(
        "SELECT signal_type, trade_date::text, round((value/1e8)::numeric) as val_b "
        "FROM market_signals "
        "WHERE signal_type IN ('margin_sh','margin_sz') AND code='TOTAL' "
        "ORDER BY trade_date DESC LIMIT 4"
    )
    lines = ["\U0001f4b0 两融余额"]
    for r in rows:
        label = r[0][7:] + "交易所"
        lines.append("  %s: %s %8.0f亿" % (label, r[1], float(r[2])))
    return "\n".join(lines)


def margin_leaders(days: int = 5, top_n: int = 10) -> tuple:
    """近N天融资净买入/卖出最大的个券"""
    since = (date.today() - timedelta(days=days)).isoformat()
    buy, _ = df._pg_execute(
        "SELECT m.code, s.name, sum(m.change_val) as total "
        "FROM market_signals m LEFT JOIN stock_list s ON s.code = m.code "
        "WHERE m.signal_type='margin_detail' AND m.change_val > 0 "
        "AND m.trade_date >= %s::date "
        "GROUP BY m.code, s.name ORDER BY total DESC LIMIT %s",
        (since, top_n))
    sell, _ = df._pg_execute(
        "SELECT m.code, s.name, sum(abs(m.change_val)) as total "
        "FROM market_signals m LEFT JOIN stock_list s ON s.code = m.code "
        "WHERE m.signal_type='margin_detail' AND m.change_val < 0 "
        "AND m.trade_date >= %s::date "
        "GROUP BY m.code, s.name ORDER BY total DESC LIMIT %s",
        (since, top_n))
    return buy, sell


def lhb_flow(days: int = 7, top_n: int = 10) -> tuple:
    """近N天龙虎榜净买入/卖出TOP"""
    since = (date.today() - timedelta(days=days)).isoformat()
    buy, _ = df._pg_execute(
        "SELECT extra->>'name', round(((extra->>'net')::numeric)/1e8, 2) as net_buy, "
        "round(((extra->>'buy')::numeric)/1e8, 2), trade_date::text "
        "FROM market_signals WHERE signal_type='lhb' "
        "AND (extra->>'net')::numeric > 0 AND trade_date >= %s::date "
        "ORDER BY (extra->>'net')::numeric DESC LIMIT %s",
        (since, top_n))
    sell, _ = df._pg_execute(
        "SELECT extra->>'name', round(((extra->>'net')::numeric)/1e8, 2), "
        "round(((extra->>'buy')::numeric)/1e8, 2), trade_date::text "
        "FROM market_signals WHERE signal_type='lhb' "
        "AND (extra->>'net')::numeric < 0 AND trade_date >= %s::date "
        "ORDER BY (extra->>'net')::numeric LIMIT %s",
        (since, top_n))
    return buy, sell


def etf_share_change(top_n: int = 10) -> list:
    """ETF份额变化最大的（最近两个有数据的交易日对比）"""
    rows, _ = df._pg_execute(
        "SELECT DISTINCT trade_date FROM market_signals "
        "WHERE signal_type='etf_share' ORDER BY trade_date DESC LIMIT 2")
    if len(rows) < 2:
        return []
    d1, d0 = str(rows[0][0]), str(rows[1][0])
    # 用 market_signals 原表，因为 CTE 不继承 extra 列
    rows, _ = df._pg_execute(
        "SELECT code, round((d1.value/1e8)::numeric,2), "
        "round((d0.value/1e8)::numeric,2), "
        "round(((d1.value/d0.value-1)*100)::numeric,2), "
        "d1.extra->>'name' FROM market_signals d1 "
        "JOIN market_signals d0 USING(code) "
        "WHERE d1.signal_type='etf_share' AND d1.trade_date=%s::date "
        "AND d0.signal_type='etf_share' AND d0.trade_date=%s::date "
        "AND d0.value > 0 "
        "ORDER BY abs(d1.value-d0.value) DESC LIMIT %s",
        (d1, d0, top_n))
    return rows


def foreign_etf_snapshot() -> list:
    """海外中国ETF最新行情"""
    rows, _ = df._pg_execute(
        "SELECT extra->>'name', round(value::numeric,2), trade_date::text, code "
        "FROM market_signals WHERE signal_type='foreign_etf' "
        "ORDER BY trade_date DESC, code")
    return rows


def hsgt_summary() -> str:
    """沪深港通（北向/南向）最新信号"""
    rows, _ = df._pg_execute(
        "SELECT signal_type, trade_date::text, round(value::numeric,2), "
        "extra->>'source' as src FROM market_signals "
        "WHERE signal_type IN ('hsgt_north_sh','hsgt_north_sz','hsgt_south_sh','hsgt_south_sz') "
        "AND code='TOTAL' "
        "ORDER BY trade_date DESC LIMIT 8")
    lines = ["\U0001f30d 沪深港通"]
    seen = set()
    for r in rows:
        label = r[0].replace('hsgt_','').replace('_',' ')
        if label in seen:
            continue
        seen.add(label)
        src = "(当日)" if r[3] else ""
        lines.append("  %s: %s 净%+.2f亿 %s" % (label, r[1], r[2], src))
    return "\n".join(lines)


def all_signals_summary() -> str:
    """所有信号综合摘要"""
    parts = [margin_market_daily()]
    parts.append("")
    parts.append(hsgt_summary())
    parts.append("")

    buy, sell = margin_leaders(days=3, top_n=5)
    if buy:
        parts.append("\U0001f4c8 融资净买入TOP5:")
        for r in buy:
            parts.append("  %s %s +%.2f亿" % (r[1] or "?", r[0], float(r[2])/1e8))
    if sell:
        parts.append("\U0001f4c9 融资净偿还TOP5:")
        for r in sell:
            parts.append("  %s %s -%.2f亿" % (r[1] or "?", r[0], float(r[2])/1e8))

    etf = etf_share_change(6)
    if etf:
        parts.append("")
        parts.append("\U0001f4ca ETF份额变化TOP:")
        for r in etf:
            chg = float(r[3])
            icon = "\U0001f4c8" if chg > 0 else "\U0001f4c9"
            parts.append("  %s %-18s %6.1f亿->%6.1f亿 (%+.2f%%)" %
                         (icon, (r[4] or "?")[:16], float(r[2]), float(r[1]), chg))

    buy_lh, sell_lh = lhb_flow(days=3, top_n=5)
    if buy_lh:
        parts.append("")
        parts.append("\U0001f40f 龙虎榜净买入TOP5:")
        for r in buy_lh:
            parts.append("  %s +%.1f亿" % (r[0], float(r[1])))
    if sell_lh:
        parts.append("\U0001f47b 龙虎榜净卖出TOP5:")
        for r in sell_lh:
            parts.append("  %s %.1f亿" % (r[0], float(r[1])))

    return "\n".join(parts)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    print(all_signals_summary())
