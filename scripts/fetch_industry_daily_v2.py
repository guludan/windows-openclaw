#!/usr/bin/env python3
"""
fetch_industry_daily_v2.py — 东财 push2 行业板块日线（替换 akshare）

数据源：东财 push2.eastmoney.com
- 行业列表: m:90+t:2 (东财行业板块，~110个)
- 单行业K线: push2his.eastmoney.com/api/qt/stock/kline/get

优势：零鉴权、HTTP、不封IP、无第三方依赖
结构兼容 industry_daily 现有表

用法：
  python3 fetch_industry_daily_v2.py           # 补最新1年
  python3 fetch_industry_daily_v2.py --full    # 全量
"""
import sys, os, time, re, json, logging
from datetime import datetime, date, timedelta
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fetch_ind_v2")

sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/scripts"))
import data_finance as df

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
HEADERS = {
    "User-Agent": UA,
    "Referer": "https://quote.eastmoney.com/",
    "Origin": "https://quote.eastmoney.com",
}

def get_industry_list_em() -> list[dict]:
    """
    东财 push2 获取行业板块列表。
    返回: [{code: "BKXXXX", name: "半导体"}, ...]
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "200", "po": "1", "np": "1",
        "fltt": "2", "invt": "2",
        "fs": "m:90+t:2",
        "fields": "f12,f14",  # f12=code, f14=name
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        d = r.json()
        items = d.get("data", {}).get("diff", [])
        industries = [{"code": it.get("f12", ""), "name": it.get("f14", "")} for it in items if it.get("f12")]
        log.info(f"东财行业板块: {len(industries)} 个")
        return industries
    except Exception as e:
        log.error(f"获取行业列表失败: {e}")
        return []

def get_industry_kline(code: str, start_date: str, end_date: str) -> list[dict]:
    """
    东财 push2his 获取单行业K线
    secid = 90.BKXXXX (东财行业板块规则)
    返回: [{date, open, close, high, low, volume, amount}, ...]
    """
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": f"90.{code}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",        # 日线
        "fqt": "1",          # 前复权
        "beg": start_date,
        "end": end_date,
        "lmt": "3000",
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        d = r.json()
        klines = d.get("data", {}).get("klines", [])
    except Exception as e:
        log.warning(f"  {code} K线请求失败: {e}")
        return []
    
    rows = []
    for line in klines:
        parts = line.split(",")
        if len(parts) >= 11:
            try:
                rows.append({
                    "date": parts[0],
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]),
                    "amount": float(parts[6]),
                })
            except:
                continue
    return rows

def create_table():
    """确保industry_daily表存在（兼容旧表结构+新字段）"""
    conn = df._get_pg()
    cur = conn.cursor()
    # 基础表结构（兼容旧字段）
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
    # 加新字段（东财push2独有的涨跌家数）
    for col, typ in [
        ("up_count", "INTEGER"),
        ("down_count", "INTEGER"),
        ("leader", "VARCHAR(50)"),
        ("leader_change", "DOUBLE PRECISION"),
    ]:
        cur.execute(f"""
            DO $$ BEGIN
                ALTER TABLE industry_daily ADD COLUMN IF NOT EXISTS {col} {typ};
            EXCEPTION WHEN duplicate_column THEN NULL;
            END $$;
        """)
    conn.commit()
    cur.close()
    conn.close()
    log.info("industry_daily 表已就绪(含新字段)")

def main():
    create_table()
    full_mode = '--full' in sys.argv
    
    # 时间范围
    today = date.today()
    if full_mode:
        start_date = "20150101"
    else:
        start_date = (today - timedelta(days=365)).strftime("%Y%m%d")
    end_date = today.strftime("%Y%m%d")
    log.info(f"采集范围: {start_date} ~ {end_date} (full={full_mode})")
    
    industries = get_industry_list_em()
    if not industries:
        log.error("无行业列表，退出")
        return
    
    total = 0
    import psycopg2.extras as pge
    
    for i, ind in enumerate(industries):
        code = ind["code"]
        name = ind["name"]
        
        klines = get_industry_kline(code, start_date, end_date)
        if not klines:
            log.info(f"  [{i+1}/{len(industries)}] {name}({code}): 无数据")
            continue
        
        import base64
        try:
            conn = df._get_pg()
            cur = conn.cursor()
            
            # 清旧数据（这个industry+范围内的老数据）
            cur.execute(
                "DELETE FROM industry_daily WHERE industry=%s AND trade_date >= %s",
                (name, start_date[:4] + "-" + start_date[4:6] + "-" + start_date[6:])
            )
            
            rows = []
            for k in klines:
                rows.append((
                    k["date"], name,
                    k["open"], k["high"], k["low"], k["close"],
                    int(k["volume"]) if k["volume"] else 0,
                    k["amount"],
                ))
            
            pge.execute_values(cur,
                "INSERT INTO industry_daily (trade_date, industry, open, high, low, close, volume, amount) "
                "VALUES %s ON CONFLICT DO NOTHING",
                rows, page_size=500
            )
            conn.commit()
            cur.close()
            total += len(rows)
            log.info(f"  [{i+1}/{len(industries)}] {name}({code}): {len(rows)} 条")
        except Exception as e:
            log.warning(f"  [{i+1}/{len(industries)}] {name} 写入失败: {e}")
        
        time.sleep(0.5)  # 适度限速
    
    log.info(f"\n✅ 完成！{total} 条行业指数写入 industry_daily")


if __name__ == "__main__":
    main()
