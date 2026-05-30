#!/usr/bin/env python3
"""慢速批量拉同花顺个股->行业映射。每次新建连接, 间隔1.5-2.5s。""" 
import urllib.request, re, time, random, psycopg2, akshare as ak, os, logging
from psycopg2.extras import execute_values

logfile = os.path.expanduser("~/.openclaw/workspace/memory/reports/ths_map_slow.log")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(logfile), logging.StreamHandler()])
log = logging.getLogger("slow_ths")

PG = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

code_map = {row["code"]: row["name"] for _, row in ak.stock_board_industry_name_ths().iterrows()}
log.info(f"行业映射: {len(code_map)}个")

conn = psycopg2.connect(PG); cur = conn.cursor()
cur.execute("SELECT stock_code FROM industry_components_ths_l1")
done = set(r[0] for r in cur.fetchall())
log.info(f"已有{len(done)}只")

cur.execute("SELECT DISTINCT symbol FROM daily_prices WHERE symbol LIKE 'A.%'")
all_stocks = [r[0].replace("A.", "") for r in cur.fetchall()]
cur.close(); conn.close()
pending = [s for s in all_stocks if s not in done]
random.shuffle(pending)
log.info(f"需查{len(pending)}只, 总{len(all_stocks)}只")

conn = psycopg2.connect(PG); cur = conn.cursor()
batch = []; added = 0; errors = 0; start = time.time()

for i, code in enumerate(pending):
    url = f"http://basic.10jqka.com.cn/{code}/field.html"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        resp = urllib.request.urlopen(req, timeout=20)
        html = resp.read().decode("gbk", errors="replace")
        found = None
        for c in set(re.findall(r"(88\d{4})", html)):
            if code_map.get(c):
                found = code_map[c]
                break
        if found:
            batch.append((found, code))
            added += 1
    except Exception:
        errors += 1

    if len(batch) >= 200:
        execute_values(cur, "INSERT INTO industry_components_ths_l1 (industry, stock_code) VALUES %s ON CONFLICT DO NOTHING", batch, page_size=200)
        conn.commit(); batch = []

    if (i+1) % 300 == 0:
        el = (time.time()-start)/60
        log.info(f"进度:{i+1}/{len(pending)} +{added} 误{errors} {added/el:.0f}/分")

    time.sleep(random.uniform(1.5, 2.5))

if batch:
    execute_values(cur, "INSERT INTO industry_components_ths_l1 (industry, stock_code) VALUES %s ON CONFLICT DO NOTHING", batch, page_size=200)
    conn.commit()
cur.execute("SELECT COUNT(DISTINCT industry), COUNT(*) FROM industry_components_ths_l1")
r = cur.fetchone()
log.info(f"结束: {r[0]}行业, {r[1]}条, {(time.time()-start)/60:.1f}分")
conn.close()
