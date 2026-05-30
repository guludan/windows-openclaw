#!/usr/bin/env python3
"""
fetch_industry_daily.py — 批量拉取同花顺一级行业（90个）指数日线到PG

数据源：akshare stock_board_industry_index_ths
用途：货币周期-行业轮动分析
"""
import sys, os, time, logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fetch_industry")

sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/scripts"))
import data_finance as df
import akshare as ak

def get_industry_list():
    """获取同花顺行业列表（90个板块）"""
    df_ak = ak.stock_board_industry_name_ths()
    names = df_ak["name"].tolist()
    log.info(f"同花顺行业板块: {len(names)} 个")
    return names

def create_industry_table():
    """确保industry_daily表存在"""
    conn = df._get_pg()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS industry_daily (
            trade_date DATE NOT NULL,
            industry VARCHAR(50) NOT NULL,
            open DOUBLE PRECISION,
            high DOUBLE PRECISION,
            low DOUBLE PRECISION,
            close DOUBLE PRECISION,
            volume BIGINT,
            amount DOUBLE PRECISION,
            PRIMARY KEY (trade_date, industry)
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    log.info("industry_daily 表已就绪")

def fetch_and_store(industry, start="20150101", end="20260531"):
    """拉取单行业指数日线并写入"""
    try:
        df_ak = ak.stock_board_industry_index_ths(symbol=industry, start_date=start, end_date=end)
        if df_ak.empty:
            return 0
    except Exception as e:
        log.warning(f"  {industry} 拉取失败: {e}")
        return 0
    
    import psycopg2.extras as pge
    conn = df._get_pg()
    cur = conn.cursor()
    
    rows = []
    for _, r in df_ak.iterrows():
        try:
            d = str(r["日期"])[:10]
            rows.append((
                d, industry,
                float(r["开盘价"]), float(r["最高价"]), float(r["最低价"]), float(r["收盘价"]),
                int(r["成交量"]), float(r["成交额"])
            ))
        except:
            continue
    
    # 清旧数据
    cur.execute("DELETE FROM industry_daily WHERE industry=%s", (industry,))
    conn.commit()
    
    # 批量插入
    pge.execute_values(cur,
        "INSERT INTO industry_daily (trade_date, industry, open, high, low, close, volume, amount) "
        "VALUES %s ON CONFLICT DO NOTHING",
        rows, page_size=500
    )
    conn.commit()
    cur.close()
    conn.close()
    return len(rows)

def main():
    create_industry_table()
    
    industries = get_industry_list()
    log.info(f"📡 开始拉取 {len(industries)} 个行业指数日线 (2015-2026)")
    
    total = 0
    for i, ind in enumerate(industries):
        c = fetch_and_store(ind)
        total += c
        log.info(f"  [{i+1}/{len(industries)}] {ind}: {c} 条")
        time.sleep(2)  # 礼貌间隔
    
    log.info(f"\n✅ 行业指数拉取完成，共写入 {total} 条")

if __name__ == "__main__":
    main()
