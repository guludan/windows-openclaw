#!/usr/bin/env python3
"""行业板块分析 — 基于申万一级行业分类 + PG 日线

功能：
1. 初始化行业成分股表（一次性下载31个申万一级行业）
2. 计算板块等权涨跌幅
3. 集成到 analysis 框架
"""
import sys, os, time, logging
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import data_finance as df
import requests, pandas as pd
from io import StringIO

log = logging.getLogger('analysis.sectors')


def init_industry_table():
    """创建申万一级行业成分股表（幂等）"""
    df._pg_execute("""
        CREATE TABLE IF NOT EXISTS industry_components (
            industry_code TEXT NOT NULL,
            industry_name TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            level TEXT DEFAULT '一级',
            updated_at TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (industry_code, stock_code)
        )
    """, fetch=False)
    log.info("industry_components 表已就绪")


def download_all_sw_first():
    """下载所有申万一级行业成分股"""
    import akshare as ak
    industry_list = ak.sw_index_first_info()
    total = 0
    failed = 0
    for _, row in industry_list.iterrows():
        code = row['行业代码']
        name = row['行业名称']
        try:
            r = requests.get(
                'https://legulegu.com/stockdata/index-composition?industryCode=' + code,
                headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
            tables = pd.read_html(StringIO(r.text))
            if not tables:
                log.warning('无成分股: %s(%s)', name, code)
                failed += 1
                continue
            cnt = 0
            for _, r2 in tables[0].iterrows():
                df._pg_execute(
                    "INSERT INTO industry_components (industry_code, industry_name, stock_code, stock_name) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (code, name, str(r2['股票代码']).strip(), str(r2['股票简称']).strip()),
                    fetch=False)
                cnt += 1
            total += cnt
            log.info('  %s(%s): %d 只', name, code, cnt)
            time.sleep(0.3)
        except Exception as e:
            log.warning('%s(%s) 失败: %s', name, code, e)
            failed += 1
    log.info('完成: %d 条, %d 个失败', total, failed)


def daily_sector_summary(top_n: int = 10) -> tuple:
    """今日行业板块涨跌幅TOP（等权计算）
    
    Returns:
        (top_list, bottom_list)
        each: {name, code, change_pct, up, down, total}
    """
    # 取最新两个交易日
    rows, _ = df._pg_execute("SELECT max(trade_date) FROM daily_prices WHERE symbol LIKE 'A.%%'")
    if not rows or not rows[0][0]:
        return [], []
    latest = str(rows[0][0])
    
    rows, _ = df._pg_execute(
        "SELECT max(trade_date) FROM daily_prices WHERE symbol LIKE 'A.%%' AND trade_date < %s::date",
        (latest,))
    prev = str(rows[0][0]) if rows and rows[0][0] else latest
    
    # 最新和前一日收盘价
    _prices = {}
    for dt in [latest, prev]:
        rows, _ = df._pg_execute(
            "SELECT symbol, close FROM daily_prices WHERE trade_date = %s::date AND symbol LIKE 'A.%%'",
            (dt,))
        _prices[dt] = {r[0][2:]: float(r[1]) for r in rows if r[1] is not None}
    
    lp, pp = _prices[latest], _prices[prev]
    
    # 板块成分股
    rows, _ = df._pg_execute(
        "SELECT industry_code, industry_name, substring(stock_code from 1 for 6) as code FROM industry_components")
    boards = defaultdict(list)
    for r in rows:
        boards[(r[0], r[1])].append(r[2])
    
    results = []
    for (ic, name), codes in boards.items():
        changes = []
        for code in codes:
            if code in lp and code in pp and pp[code] > 0:
                chg = (lp[code] - pp[code]) / pp[code] * 100
                changes.append(chg)
        if changes:
            avg_chg = sum(changes) / len(changes)
            up = sum(1 for c in changes if c > 0)
            down = sum(1 for c in changes if c < 0)
            results.append({
                'name': name, 'code': ic,
                'change_pct': round(avg_chg, 2),
                'up': up, 'down': down, 'total': len(changes),
            })
    
    results.sort(key=lambda x: x['change_pct'], reverse=True)
    top = results[:top_n]
    bottom = results[-top_n:] if len(results) >= top_n else results
    bottom.sort(key=lambda x: x['change_pct'])
    return top, bottom


def concept_summary(top_n: int = 5) -> tuple:
    """占位：概念板块（需要类似的结构化数据源，暂缺）"""
    return [], []


def sector_daily_chart() -> str:
    """板块涨跌幅文本简报"""
    top, bottom = daily_sector_summary(8)
    lines = ["行业板块涨跌幅 (申万一级):", "=" * 50]
    lines.append("")
    lines.append("涨幅TOP:")
    for s in top:
        lines.append("  %-12s %+.2f%% (涨%d/跌%d/%d只)" % (
            s['name'][:10], s['change_pct'], s['up'], s['down'], s['total']))
    lines.append("")
    lines.append("跌幅TOP:")
    for s in bottom:
        lines.append("  %-12s %+.2f%% (涨%d/跌%d/%d只)" % (
            s['name'][:10], s['change_pct'], s['up'], s['down'], s['total']))
    return "\n".join(lines)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--init', action='store_true', help='初始化+下载成分股')
    parser.add_argument('--calc', action='store_true', help='计算板块涨跌幅')
    args = parser.parse_args()
    
    if args.init:
        init_industry_table()
        download_all_sw_first()
    elif args.calc:
        print(sector_daily_chart())
    else:
        parser.print_help()
