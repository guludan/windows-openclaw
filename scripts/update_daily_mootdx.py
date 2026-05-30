#!/usr/bin/env python3
"""
update_daily_mootdx.py — 用 mootdx 统一拉取全市场个股日线到 PG

数据源：mootdx TCP 协议（通达信服务器 7709，不封 IP）
每日增量更新，支持指定日期范围补拉。

用法：
  python3 update_daily_mootdx.py                    # 最近5个交易日增量更新
  python3 update_daily_mootdx.py --start 2026-05-19 --end 2026-05-22  # 指定范围回填
"""
import sys, os, time, logging, argparse
from datetime import datetime, date, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("update_daily_mootdx")

sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/scripts"))
import data_finance as df
from mootdx.quotes import Quotes

client = Quotes.factory(market='std')

# 北交所/沪深 市场代码
def _market(code):
    if code.startswith("8") or code.startswith("4") or code.startswith("92"):
        return 2  # 北交所
    if code.startswith("6"):
        return 1  # 上海
    return 0  # 深圳

def get_stock_list():
    """获取全市场股票列表（含沪深+北交所），返回 sorted list"""
    conn = df._get_pg()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT symbol FROM daily_prices WHERE symbol LIKE 'A.%%' ORDER BY symbol")
    stocks = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return stocks

def update_daily(start_date=None, end_date=None, batch_commit=500):
    """
    用 mootdx 补拉个股日线
    - 默认最近5个交易日
    - 只插入新数据，不覆盖已有
    """
    if start_date is None:
        start_date = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
    if end_date is None:
        end_date = date.today().strftime("%Y-%m-%d")
    
    target_start = datetime.strptime(start_date, "%Y-%m-%d").date()
    target_end = datetime.strptime(end_date, "%Y-%m-%d").date()
    
    stocks = get_stock_list()
    log.info(f"全市场股票: {len(stocks)}只, 补拉 {start_date} ~ {end_date}")
    
    conn = df._get_pg()
    cur = conn.cursor()
    
    total = 0
    errors = 0
    missing_stocks = []
    
    for i, sym in enumerate(stocks):
        code = sym.replace("A.", "")
        
        try:
            # offset=5 只取最近几天，避免全量拉取
            klines = client.bars(symbol=code, category=4, offset=10)
            if klines is None or len(klines) == 0:
                errors += 1
                missing_stocks.append(sym)
                continue
            
            for _, row in klines.iterrows():
                td = row['datetime']
                if isinstance(td, str):
                    td = datetime.strptime(td[:10], "%Y-%m-%d").date()
                elif hasattr(td, 'date'):
                    td = td.date()
                
                if td < target_start or td > target_end:
                    continue
                
                # mootdx字段:
                # vol - 成交量(手), amount - 成交额(元)
                cur.execute(
                    "INSERT INTO daily_prices (symbol, trade_date, open, high, low, close, volume, amount, source) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (sym, td,
                     float(row['open']), float(row['high']), float(row['low']),
                     float(row['close']),
                     int(row['vol']), float(row['amount']),
                     'mootdx')
                )
                total += cur.rowcount
            
            if (i + 1) % batch_commit == 0:
                conn.commit()
                log.info(f"  进度 {i+1}/{len(stocks)} ({total}行)")
                
        except Exception as e:
            errors += 1
            if errors <= 3:
                log.warning(f"  {sym}: {e}")
    
    conn.commit()
    
    # 最终统计
    cur.execute("SELECT MAX(trade_date) FROM daily_prices WHERE symbol LIKE 'A.%%'")
    latest = cur.fetchone()[0]
    cur.execute("SELECT trade_date, COUNT(DISTINCT symbol) FROM daily_prices WHERE symbol LIKE 'A.%%' AND trade_date >= %s GROUP BY trade_date ORDER BY trade_date", (start_date,))
    
    log.info(f"=== 完成 ===")
    log.info(f"新增 {total} 行, 失败 {errors} 只")
    log.info(f"个股最新日期: {latest}")
    for r in cur.fetchall():
        log.info(f"  {r[0]}: {r[1]}只")
    
    if missing_stocks:
        log.warning(f"mootdx无数据的股票({len(missing_stocks)}只, 示例): {missing_stocks[:10]}")
    
    cur.close()
    conn.close()
    return total

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="mootdx个股日线更新")
    parser.add_argument("--start", type=str, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=str, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--batch", type=int, default=500, help="每N只commit一次")
    args = parser.parse_args()
    update_daily(args.start, args.end, args.batch)
