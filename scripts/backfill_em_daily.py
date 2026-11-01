#!/usr/bin/env python3
"""
东财日K回补 — 用http://push2his.eastmoney.com
用法: python3 backfill_em_daily.py --start 2026-06-27 --end 2026-09-10
口径验证: fqt=0 与 mootdx 完全一致(哈三联9/11: 11.64/72458)
"""
import argparse
import json
import subprocess
import time
import psycopg2
from datetime import date

PG = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
URL = "http://push2his.eastmoney.com/api/qt/stock/kline/get"

def curl_get(params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    out = subprocess.run(["curl", "-s", "-m", "8", "-A", "Mozilla/5.0", f"{URL}?{qs}"],
                         capture_output=True, text=True, timeout=12)
    return json.loads(out.stdout) if out.stdout else {}

def secid_of(code):
    if code.startswith(("60", "68", "11", "5")):
        return "1." + code  # 沪
    return "0." + code      # 深

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    args = ap.parse_args()
    beg = args.start.replace("-", "")
    end = args.end.replace("-", "")

    conn = psycopg2.connect(PG)
    cur = conn.cursor()
    cur.execute("SELECT code FROM stock_list WHERE name NOT LIKE '%%ST%%'")
    # 用库里已有的A股全集（含退市过的也补，保持完整）
    cur.execute("""
        SELECT DISTINCT SUBSTRING(symbol FROM 3) FROM daily_prices
        WHERE symbol LIKE 'A.%%'
    """)
    codes = [r[0] for r in cur.fetchall()]
    print(f"回补 {len(codes)} 只: {args.start} ~ {args.end}")

    total, errors = 0, 0
    t0 = time.time()
    for i, code in enumerate(codes):
        secid = secid_of(code)
        try:
            params = {"secid": secid, "klt": "101", "fqt": "0",
                      "beg": beg, "end": end,
                      "fields1": "f1,f2,f3", "fields2": "f51,f52,f53,f54,f55,f56,f57"}
            d = curl_get(params)
            klines = d.get("data", {}).get("klines", [])
            for k in klines:
                parts = k.split(",")
                td, o, c, h, l, v, a = parts[0], float(parts[1]), float(parts[2]), \
                    float(parts[3]), float(parts[4]), int(float(parts[5])), float(parts[6])
                cur.execute(
                    "INSERT INTO daily_prices (symbol, trade_date, open, high, low, close, volume, amount, source) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    ("A." + code, td, o, h, l, c, v, a, "eastmoney"))
                total += cur.rowcount
        except Exception:
            errors += 1
            time.sleep(1)
        if (i + 1) % 200 == 0:
            conn.commit()
            el = time.time() - t0
            print(f"  [{i+1}/{len(codes)}] 写入{total}条 错{errors} 耗时{el:.0f}s", flush=True)
        if (i + 1) % 500 == 0:
            time.sleep(20)  # 每500只休息20s，防封
        time.sleep(1.5)
    conn.commit()
    print(f"完成: {total}条, 错误{errors}, 耗时{(time.time()-t0)/60:.1f}分钟")
    conn.close()

if __name__ == "__main__":
    main()
