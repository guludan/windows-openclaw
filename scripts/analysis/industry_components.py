#!/usr/bin/env python3
"""下载申万一级行业成分股到 PG + 计算板块涨跌幅"""
import sys, os, time, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import data_finance as df
import requests, pandas as pd
from io import StringIO

log = logging.getLogger('industry_components')


def init_table():
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


def fetch_all_sw_first():
    import akshare as ak
    industry_list = ak.sw_index_first_info()
    total_saved = 0
    total_failed = 0
    for _, row in industry_list.iterrows():
        code = row['行业代码']
        name = row['行业名称']
        try:
            url = 'https://legulegu.com/stockdata/index-composition?industryCode=' + code
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
            tables = pd.read_html(StringIO(r.text))
            if not tables:
                log.warning('无成分股: %s(%s)', name, code)
                total_failed += 1
                continue
            count = 0
            for _, row2 in tables[0].iterrows():
                df._pg_execute(
                    "INSERT INTO industry_components (industry_code, industry_name, stock_code, stock_name) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (code, name, str(row2['股票代码']).strip(), str(row2['股票简称']).strip()),
                    fetch=False)
                count += 1
            total_saved += count
            log.info('  %s(%s): %d 只', name, code, count)
            time.sleep(0.3)
        except Exception as e:
            log.warning('%s(%s) 失败: %s', name, code, e)
            total_failed += 1
    log.info('完成: %d 条, %d 个失败', total_saved, total_failed)
    return total_saved, total_failed


def calc_board_snapshot():
    """基于 industry_components + daily_prices 算板块等权涨跌幅
    
    匹配逻辑：industry_components.stock_code = '000019.SZ' 
    → 取前6位 = '000019'
    → daily_prices.symbol = 'A.000019'
    """
    rows, _ = df._pg_execute("SELECT max(trade_date) FROM daily_prices WHERE symbol LIKE 'A.%%'")
    latest_date = str(rows[0][0]) if rows and rows[0][0] else None
    if not latest_date:
        return []
    
    # 用 latest_date 的前一个交易日做涨幅计算
    sql = """
    WITH daily AS (
        SELECT substring(symbol from 3 for 6) as code, close, trade_date
        FROM daily_prices
        WHERE trade_date = %s::date AND symbol LIKE 'A.%%'
    ),
    prev AS (
        SELECT substring(symbol from 3 for 6) as code, close
        FROM daily_prices
        WHERE trade_date = (%s::date - interval '1 day')::date AND symbol LIKE 'A.%%'
    ),
    changes AS (
        SELECT d.code,
            CASE WHEN p.close IS NOT NULL AND p.close > 0
                THEN (d.close - p.close) / p.close * 100
                ELSE 0 END AS chg
        FROM daily d LEFT JOIN prev p ON d.code = p.code
    )
    SELECT ic.industry_code, ic.industry_name,
           round(avg(coalesce(ch.chg, 0))::numeric, 2) as avg_chg,
           count(*) as total,
           sum(case when ch.chg > 0 then 1 else 0 end) as up,
           sum(case when ch.chg < 0 then 1 else 0 end) as down
    FROM industry_components ic
    LEFT JOIN changes ch ON substring(ic.stock_code from 1 for 6) = ch.code
    GROUP BY ic.industry_code, ic.industry_name
    ORDER BY avg_chg DESC
    """
    rows, _ = df._pg_execute(sql, (latest_date, latest_date))
    return [{
        'industry_code': r[0],
        'industry_name': r[1],
        'change_pct': float(r[2]),
        'total': int(r[3]),
        'up': int(r[4]),
        'down': int(r[5]),
    } for r in rows]


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--init', action='store_true')
    parser.add_argument('--calc', action='store_true')
    args = parser.parse_args()
    
    if args.init:
        init_table()
        fetch_all_sw_first()
    elif args.calc:
        results = calc_board_snapshot()
        if results:
            print("行业板块涨跌幅 (申万一级, 最新交易日):")
            print("-" * 50)
            for r in results[:8]:
                bar = '+' if r['change_pct'] > 0 else '-'
                print("  %s %-12s %+.2f%% (涨%d/跌%d/%d只)" % (
                    bar, r['industry_name'][:10], r['change_pct'],
                    r['up'], r['down'], r['total']))
            print("  ...")
            for r in results[-8:]:
                bar = '+' if r['change_pct'] > 0 else '-'
                print("  %s %-12s %+.2f%% (涨%d/跌%d/%d只)" % (
                    bar, r['industry_name'][:10], r['change_pct'],
                    r['up'], r['down'], r['total']))
        else:
            print("无数据")
    else:
        parser.print_help()
