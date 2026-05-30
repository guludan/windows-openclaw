#!/usr/bin/env python3
# preflight-ok: true
"""
fetch_cashflow.py — 拉取经营/投资/筹资现金流（东财datacenter）
存入 financial_cashflow 表
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, psycopg2.extras
import requests, time, json
from datetime import datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
BASE_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://datacenter.eastmoney.com/',
}
PAGE_SIZE = 500

def main():
    conn = psycopg2.connect(DB)
    cur = conn.cursor()

    # 创建表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS financial_cashflow (
            id SERIAL PRIMARY KEY,
            symbol VARCHAR(20),
            end_date DATE,
            netcash_operate NUMERIC(20,2),     -- 经营活动现金流净额
            netcash_invest NUMERIC(20,2),       -- 投资活动现金流净额
            netcash_finance NUMERIC(20,2),      -- 筹资活动现金流净额
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (symbol, end_date)
        )
    """)
    conn.commit()
    print("表 financial_cashflow 就绪")

    # 获取总页数
    params = {
        'reportName': 'RPT_F10_FINANCE_MAINFINADATA',
        'columns': 'SECURITY_CODE,REPORT_DATE,NETCASH_OPERATE_PK,NETCASH_INVEST_PK,NETCASH_FINANCE_PK',
        'pageNumber': 1,
        'pageSize': PAGE_SIZE,
        'source': 'WEB',
        'client': 'WEB',
        'sortTypes': -1,
        'sortColumns': 'REPORT_DATE',
    }
    resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    j = resp.json()
    if not j.get('success'):
        print("API错误:", j.get('message'))
        return
    total = j['result']['count']
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    print(f"总记录: {total}条, {total_pages}页（将在代码中过滤有现金流的记录）")

    inserted = 0
    for page in range(1, total_pages + 1):
        params['pageNumber'] = page
        for attempt in range(3):
            try:
                resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
                j = resp.json()
                data = j.get('result', {}).get('data', [])
                break
            except Exception as e:
                print(f"  page {page} attempt {attempt+1}: {e}")
                time.sleep(3)
                data = []

        if not data:
            continue

        values = []
        for r in data:
            code = r.get('SECURITY_CODE', '')
            rd = (r.get('REPORT_DATE', '') or '')[:10]
            op = r.get('NETCASH_OPERATE_PK')
            iv = r.get('NETCASH_INVEST_PK')
            fn = r.get('NETCASH_FINANCE_PK')
            
            if not code or not rd or op is None:
                continue
            
            try:
                op_f = float(op)
                iv_f = float(iv) if iv is not None else None
                fn_f = float(fn) if fn is not None else None
            except (ValueError, TypeError):
                continue
            
            symbol = 'A.' + code
            values.append((symbol, rd, op_f, iv_f, fn_f))

        if not values:
            continue

        try:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO financial_cashflow (symbol, end_date, netcash_operate, netcash_invest, netcash_finance)
                   VALUES %s
                   ON CONFLICT (symbol, end_date) DO UPDATE SET
                       netcash_operate=EXCLUDED.netcash_operate,
                       netcash_invest=EXCLUDED.netcash_invest,
                       netcash_finance=EXCLUDED.netcash_finance,
                       updated_at=NOW()""",
                values,
                template='(%s, %s::date, %s, %s, %s)'
            )
            inserted += cur.rowcount
            conn.commit()
        except Exception as e:
            print(f"  DB error page {page}: {e}")
            conn.rollback()

        if page % 10 == 0:
            cur.execute("SELECT COUNT(*) FROM financial_cashflow")
            print(f"  第{page}/{total_pages}页: 入库{inserted}, 总量{cur.fetchone()[0]}")

        time.sleep(1.2)

    cur.execute("SELECT COUNT(*), MIN(end_date), MAX(end_date) FROM financial_cashflow")
    cnt, mn, mx = cur.fetchone()
    print(f"\n完成！financial_cashflow: {cnt}条 ({mn} ~ {mx})")
    cur.close()
    conn.close()

if __name__ == '__main__':
    t0 = time.time()
    main()
    print(f"耗时 {time.time()-t0:.0f}s")
