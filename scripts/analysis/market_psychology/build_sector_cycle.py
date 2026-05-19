#!/usr/bin/env python3
"""
板块周期数据库构建 — 31个申万一级行业 2025~2026 每日行情+情绪指标

产出表：sector_daily
  trade_date, industry_name, industry_code,
  return_pct, close, volume,
  up_stocks, down_stocks, limit_up, limit_down,
  avg_return, 总成交额, 相对沪深300收益, 板块内部离散度

用于：周期定位→轮动信号→造势事件识别→回测
"""
import sys, os, logging, json, time
from datetime import date, timedelta
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import data_finance as df

log = logging.getLogger('sector_cycle')

# ============ 1. 构建板块成分股映射 ============

def get_industry_map() -> dict:
    """返回 {industry_name: [stock_code, ...]}"""
    rows, _ = df._pg_execute(
        "SELECT industry_name, stock_code FROM industry_components WHERE level = '一级'")
    mapping = {}
    for r in rows:
        name = r[0]
        raw = r[1]
        # industry_components: '601009.SH' -> daily_prices: 'A.601009'
        code = "A." + raw.split('.')[0]
        if name not in mapping:
            mapping[name] = []
        mapping[name].append(code)
    return mapping


# ============ 2. 计算板块每日行情 ============

def build_sector_daily(industry_map: dict, start_date: str = "2025-01-02", end_date: str = "2026-05-18"):
    """一次性构建全量板块日线"""
    log.info(f"构建板块日线: {start_date} ~ {end_date}")
    
    # 获取沪深300作为基准
    hs300_map = {}
    rows, _ = df._pg_execute(
        "SELECT trade_date::text, close FROM daily_prices WHERE symbol='I.000300' "
        "AND trade_date BETWEEN %s::date AND %s::date ORDER BY trade_date",
        (start_date, end_date))
    for r in rows:
        hs300_map[r[0]] = float(r[1])
    
    # 获取全市场个股日线（一次拉全，避免逐行业查询）
    log.info("拉取全市场个股日线...")
    all_rows, _ = df._pg_execute(
        "SELECT trade_date::text, symbol, close "
        "FROM daily_prices WHERE symbol LIKE 'A.%%' "
        "AND trade_date BETWEEN %s::date AND %s::date ORDER BY trade_date, symbol",
        (start_date, end_date))
    
    # 按股票组织（用于计算涨跌）
    stock_data = {}
    for r in all_rows:
        dt, sym, close = r[0], r[1], float(r[2]) if r[2] else 0
        if sym not in stock_data:
            stock_data[sym] = {}
        stock_data[sym][dt] = close
    
    log.info(f"个股数: {len(stock_data)}, 交易天数: {len(set(d[0] for d in all_rows))}")
    
    # 按日期+行业计算
    # 倒排索引: date -> [market data per stock]
    date_stock_price = {}  # date -> {symbol: close}
    for r in all_rows:
        dt, sym, close = r[0], r[1], float(r[2]) if r[2] else 0
        if dt not in date_stock_price:
            date_stock_price[dt] = {}
        date_stock_price[dt][sym] = close
    
    # 生成板块日线
    sector_daily = []  # [{trade_date, industry, return_pct, ...}]
    
    industry_stocks_map = {}  # cache: industry -> [codes]
    for ind_name, codes in industry_map.items():
        # 确保 codes 只包含在 daily_prices 里存在的
        valid_codes = [c for c in codes if c in stock_data]
        industry_stocks_map[ind_name] = valid_codes
    
    # 对每个日期计算
    dates = sorted(date_stock_price.keys())
    log.info(f"共 {len(dates)} 个交易日, 开始逐日计算板块表现...")
    
    for idx, dt in enumerate(dates):
        if idx % 50 == 0:
            log.info(f"  进度: {idx}/{len(dates)} ({dt})")
        
        today_prices = date_stock_price[dt]
        prev_dt = dates[idx - 1] if idx > 0 else None
        prev_prices = date_stock_price[prev_dt] if prev_dt else {}
        
        hs300 = hs300_map.get(dt, 0)
        hs300_prev = hs300_map.get(prev_dt, 0)
        hs300_return = (hs300 - hs300_prev) / hs300_prev * 100 if hs300_prev > 0 else 0
        
        for ind_name, codes in industry_stocks_map.items():
            # 计算当天板块内所有股票的涨跌
            returns = []
            up, down = 0, 0
            limit_up, limit_down = 0, 0
            valid_count = 0
            
            for code in codes:
                cur_close = today_prices.get(code)
                prev_close = prev_prices.get(code)
                if cur_close and prev_close and prev_close > 0:
                    ret = (cur_close - prev_close) / prev_close * 100
                    returns.append(ret)
                    valid_count += 1
                    if ret > 0:
                        up += 1
                    elif ret < 0:
                        down += 1
                    if ret >= 9.5:
                        limit_up += 1
                    elif ret <= -9.5:
                        limit_down += 1
            
            if valid_count < 3:
                continue  # 板块样本不足
            
            sector_return = round(np.mean(returns), 2)
            sector_median = round(np.median(returns), 2)
            sector_std = round(np.std(returns), 2)  # 板块内部离散度
            sector_relative = round(sector_return - hs300_return, 2)  # 相对沪深300
            
            sector_daily.append({
                'trade_date': dt,
                'industry': ind_name,
                'return_pct': sector_return,
                'median_return': sector_median,
                'std_dev': sector_std,
                'relative_vs_hs300': sector_relative,
                'hs300_return': round(hs300_return, 2),
                'up_stocks': up,
                'down_stocks': down,
                'limit_up': limit_up,
                'limit_down': limit_down,
                'valid_count': valid_count,
            })
    
    log.info(f"板块日线总行数: {len(sector_daily)}")
    return sector_daily


# ============ 3. 写入数据库 ============

def create_sector_daily_table():
    """创建或替换板块日线表"""
    try:
        df._pg_execute("SELECT 1 FROM sector_daily LIMIT 1", fetch=True)
        log.info("sector_daily 表已存在")
    except:
        df._pg_execute("""
            CREATE TABLE sector_daily (
                trade_date date NOT NULL,
                industry varchar(50) NOT NULL,
                return_pct double precision,
                median_return double precision,
                std_dev double precision,
                relative_vs_hs300 double precision,
                hs300_return double precision,
                up_stocks integer,
                down_stocks integer,
                limit_up integer,
                limit_down integer,
                valid_count integer,
                PRIMARY KEY (trade_date, industry)
            )
        """, fetch=False)
        log.info("sector_daily 表已创建")


def write_sector_daily(data: list):
    """逐行写入，numpy 类型转原生 Python"""
    count = 0
    sql = ("INSERT INTO sector_daily "
           "(trade_date, industry, return_pct, median_return, std_dev, "
           "relative_vs_hs300, hs300_return, up_stocks, down_stocks, "
           "limit_up, limit_down, valid_count) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
           "ON CONFLICT (trade_date, industry) DO UPDATE SET "
           "return_pct=EXCLUDED.return_pct, median_return=EXCLUDED.median_return, "
           "std_dev=EXCLUDED.std_dev, relative_vs_hs300=EXCLUDED.relative_vs_hs300, "
           "hs300_return=EXCLUDED.hs300_return, up_stocks=EXCLUDED.up_stocks, "
           "down_stocks=EXCLUDED.down_stocks, limit_up=EXCLUDED.limit_up, "
           "limit_down=EXCLUDED.limit_down, valid_count=EXCLUDED.valid_count")
    for i in range(0, len(data), 50):
        batch = data[i:i+50]
        for d in batch:
            vals = (
                d['trade_date'], d['industry'],
                float(d['return_pct']), float(d['median_return']), float(d['std_dev']),
                float(d['relative_vs_hs300'] or 0), float(d['hs300_return'] or 0),
                int(d['up_stocks']), int(d['down_stocks']),
                int(d['limit_up']), int(d['limit_down']),
                int(d['valid_count']),
            )
            df._pg_execute(sql, vals, fetch=False)
            count += 1
        if i % 1000 == 0:
            log.info(f"  写入进度: {count}/{len(data)}")
    
    log.info(f"写入完成: {count} 行")


def run():
    """主流程"""
    log.info("=== 板块周期数据库构建 ===")
    
    create_sector_daily_table()
    
    industry_map = get_industry_map()
    log.info(f"行业数: {len(industry_map)}")
    for k, v in list(industry_map.items())[:3]:
        log.info(f"  {k}: {len(v)} 只成分股")
    
    data = build_sector_daily(industry_map)
    
    write_sector_daily(data)
    
    # 验证
    rows, _ = df._pg_execute(
        "SELECT count(*), min(trade_date), max(trade_date), count(DISTINCT industry) "
        "FROM sector_daily")
    log.info(f"验证: {rows[0][0]} 行, {rows[0][3]} 行业, {rows[0][1]}~{rows[0][2]}")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    run()
