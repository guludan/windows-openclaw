#!/usr/bin/env python3
"""
build_ths_l1_components.py — 同花顺一级行业成分股映射（分批+超时保护+重试）

每只股票查同花顺 basic.10jqka.com.cn/{code}/field.html 提取88xxxx行业代码。
随机间隔0.3~1.0s，每200条commit，每500只刷新PG连接。
超时15s自动跳过，避免卡死。
"""
import urllib.request, re, time, logging, sys, os, random, psycopg2

LOG_FILE = os.path.expanduser("~/.openclaw/workspace/memory/reports/ths_map.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, mode='a'), logging.StreamHandler()]
)
log = logging.getLogger("ths_l1")

sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/scripts"))
import data_finance as df
import akshare as ak
from psycopg2.extras import execute_values

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

class ThsFetcher:
    def __init__(self):
        self.opener = urllib.request.build_opener()
    
    def fetch(self, code, code_map, retry=2):
        url = f"http://basic.10jqka.com.cn/{code}/field.html"
        for attempt in range(retry):
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            try:
                resp = self.opener.open(req, timeout=15)
                html = resp.read().decode('gbk', errors='replace')
                for c in set(re.findall(r'(88\d{4})', html)):
                    if code_map.get(c):
                        return code_map[c]
                return None
            except Exception:
                if attempt < retry - 1:
                    time.sleep(1)
                continue
        return None

def get_stock_list():
    conn = df._get_pg()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT symbol FROM daily_prices WHERE symbol LIKE 'A.%%' ORDER BY symbol")
    stocks = [r[0].replace("A.", "") for r in cur.fetchall()]
    cur.close(); conn.close()
    return stocks

def main():
    stocks = get_stock_list()
    log.info(f"股票池: {len(stocks)}只")
    
    # 行业映射
    code_map = {row['code']: row['name'] for _, row in ak.stock_board_industry_name_ths().iterrows()}
    
    # 初始化表
    conn = df._get_pg(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM industry_components_ths_l1")
    cnt = cur.fetchone()[0]
    if cnt == 0:
        log.info("表空，重建")
        cur.execute("DROP TABLE IF EXISTS industry_components_ths_l1")
        cur.execute("""
            CREATE TABLE industry_components_ths_l1 (
                industry VARCHAR(50) NOT NULL,
                stock_code VARCHAR(20) NOT NULL,
                PRIMARY KEY (industry, stock_code)
            )
        """)
        conn.commit()
    cur.execute("SELECT stock_code FROM industry_components_ths_l1")
    done = set(r[0] for r in cur.fetchall())
    cur.close(); conn.close()
    log.info(f"已有{len(done)}只, 还需{len(stocks)-len(done)}只")
    
    fetcher = ThsFetcher()
    remaining = [s for s in stocks if s not in done]
    added, skipped, errors = 0, 0, 0
    batch = []
    industry_counts = {}
    
    conn = df._get_pg(); cur = conn.cursor()
    try:
        for i, code in enumerate(remaining):
            industry = fetcher.fetch(code, code_map)
            if industry:
                batch.append((industry, code))
                industry_counts[industry] = industry_counts.get(industry, 0) + 1
                added += 1
            else:
                skipped += 1
            
            if len(batch) >= 200:
                execute_values(cur, "INSERT INTO industry_components_ths_l1 (industry, stock_code) VALUES %s ON CONFLICT DO NOTHING", batch, page_size=200)
                conn.commit()
                batch = []
            
            if (i + 1) % 500 == 0:
                if batch:
                    execute_values(cur, "INSERT INTO industry_components_ths_l1 (industry, stock_code) VALUES %s ON CONFLICT DO NOTHING", batch, page_size=200)
                    conn.commit(); batch = []
                cur.close(); conn.close()
                conn = df._get_pg(); cur = conn.cursor()
                log.info(f"  进度 {i+1}/{len(remaining)} (已+{added}, {skipped}跳过)")
            
            time.sleep(random.uniform(0.3, 0.8))
        
        if batch:
            execute_values(cur, "INSERT INTO industry_components_ths_l1 (industry, stock_code) VALUES %s ON CONFLICT DO NOTHING", batch, page_size=200)
            conn.commit()
    except Exception as e:
        log.error(f"异常中断: {e}")
        if batch:
            try: execute_values(cur, "INSERT INTO industry_components_ths_l1 (industry, stock_code) VALUES %s ON CONFLICT DO NOTHING", batch, page_size=200); conn.commit()
            except: pass
    finally:
        try: conn.commit(); cur.close(); conn.close()
        except: pass
    
    log.info(f"=== 完成 ===")
    log.info(f"新增: {added}, 已有: {len(done)}, 无行业: {skipped}, 错误: {errors}")
    for n, c in sorted(industry_counts.items(), key=lambda x:-x[1])[:40]:
        log.info(f"  {n}: {c}")
    
    conn = df._get_pg(); cur = conn.cursor()
    cur.execute("SELECT COUNT(DISTINCT industry), COUNT(*) FROM industry_components_ths_l1")
    r = cur.fetchone()
    log.info(f"最终: {r[0]}行业, {r[1]}条")
    conn.close()

if __name__ == "__main__":
    main()
