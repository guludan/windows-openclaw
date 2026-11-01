#!/usr/bin/env python3
"""
腾讯日K回补 — web.ifzq.gtimg.cn fqkline (不复权)
用法: python3 backfill_tx_daily.py --start 2026-06-27 --end 2026-09-10
口径: 9/10哈三联11.95 与东财/mootdx一致
字段: [date, open, close, high, low, volume] 无amount
"""
import argparse
import json
import subprocess
import time
import psycopg2

PG = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

def curl_get(param):
    out = subprocess.run(["curl", "-s", "-m", "8", "-A", "Mozilla/5.0",
                          f"{URL}?param={param}"],
                         capture_output=True, text=True, timeout=12)
    return json.loads(out.stdout) if out.stdout else {}

def mkt_of(code):
    return "sh" if code.startswith(("6", "9")) else "sz"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    args = ap.parse_args()

    conn = psycopg2.connect(PG)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT SUBSTRING(symbol FROM 3) FROM daily_prices
        WHERE symbol LIKE 'A.%'
    """)
    codes = [r[0] for r in cur.fetchall()]
    # 跳过北交所(8/4开头, 腾讯前缀不同)
    codes = [c for c in codes if c.startswith(("6", "0", "3"))]
    print(f"回补 {len(codes)} 只: {args.start} ~ {args.end}")

    total, errors, empty = 0, 0, 0
    t0 = time.time()
    for i, code in enumerate(codes):
        sym = mkt_of(code) + code
        try:
            param = f"{sym},day,{args.start},{args.end},60,"
            d = curl_get(param)
            dd = d.get("data", {}).get(sym, {})
            klines = dd.get("day") or dd.get("qfqday") or []
            if not klines:
                empty += 1
            for row in klines:
                td, o, c, h, l, v = row[0], float(row[1]), float(row[2]), \
                    float(row[3]), float(row[4]), int(float(row[5]))
                cur.execute(
                    "INSERT INTO daily_prices (symbol, trade_date, open, high, low, close, volume, amount, source) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    ("A." + code, td, o, h, l, c, v, 0, "tencent"))
                total += cur.rowcount
        except Exception:
            errors += 1
            time.sleep(2)
        if (i + 1) % 200 == 0:
            conn.commit()
            el = time.time() - t0
            print(f"  [{i+1}/{len(codes)}] 写{total} 错{errors} 空{empty} {el:.0f}s", flush=True)
        if (i + 1) % 500 == 0:
            time.sleep(15)
        time.sleep(1.5)
    conn.commit()
    print(f"完成: {total}条 错{errors} 空{empty} 耗时{(time.time()-t0)/60:.1f}分钟")
    conn.close()

if __name__ == "__main__":
    main()
