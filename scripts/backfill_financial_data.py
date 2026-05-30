#!/usr/bin/env python3
# preflight-ok: true
"""
backfill_financial_data.py v2 — 全量拉取近4个季度财务数据

策略：东财API不支持OR filter，改为全量拉取全部数据（无filter），
按REPORT_DATE降序，只拉取2025-09-30后的数据（代码中过滤日期）。

第一批拉500页（250K条），覆盖2025Q3~2026Q1的所有财报。
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, psycopg2.extras
import requests, time
from datetime import date, datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
BASE_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://datacenter.eastmoney.com/',
}
PAGE_SIZE = 500
MIN_DATE = '2025-09-30'  # 从2025Q3开始

def main():
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # 获取已有记录
    cur.execute("SELECT symbol, end_date FROM financial_data")
    existing = {(r[0], str(r[1])) for r in cur.fetchall()}
    print(f"financial_data已有{len(existing):,}条记录")
    
    # 一次性获取API总记录数和页数
    params = {
        'reportName': 'RPT_F10_FINANCE_MAINFINADATA',
        'columns': 'SECURITY_CODE,REPORT_DATE,PARENTNETPROFIT,PARENTNETPROFITTZ,TOTALOPERATEREVE,TOTALOPERATEREVETZ,EPSJB,BPS,ROEJQ',
        'pageNumber': 1,
        'pageSize': 500,
        'source': 'WEB', 'client': 'WEB',
        'sortTypes': -1,
        'sortColumns': 'REPORT_DATE',
    }
    resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    j = resp.json()
    total = j['result']['count']
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    print(f"API总记录: {total:,}条, {total_pages}页")
    print(f"将拉取全量数据（代码中按日期 {MIN_DATE} 过滤）")
    
    inserted = 0
    skipped = 0
    
    for page in range(1, total_pages + 1):
        params['pageNumber'] = page
        
        for attempt in range(3):
            try:
                resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
                j = resp.json()
                if j.get('success'):
                    data = j.get('result', {}).get('data', [])
                    break
                else:
                    time.sleep(3)
                    data = []
            except Exception as e:
                time.sleep(3)
                data = []
        
        if not data:
            continue
        
        values = []
        for r in data:
            rd = (r.get('REPORT_DATE', '') or '')[:10]
            # 按日期过滤——因为降序排列，一旦到MIN_DATE之前就停止本页
            if rd < MIN_DATE:
                date_reached = True
                continue
            
            code = r.get('SECURITY_CODE', '')
            if not code or not rd:
                continue
            
            symbol = 'A.' + code
            
            # 跳过已有记录
            if (symbol, rd) in existing:
                skipped += 1
                continue
            
            profit = r.get('PARENTNETPROFIT')
            profit_yoy = r.get('PARENTNETPROFITTZ')
            revenue = r.get('TOTALOPERATEREVE')
            revenue_yoy = r.get('TOTALOPERATEREVETZ')
            eps = r.get('EPSJB')
            bvps = r.get('BPS')
            roe = r.get('ROEJQ')
            
            values.append((
                symbol, rd,
                float(roe) if roe is not None else None,
                float(profit) if profit is not None else None,
                float(profit_yoy) if profit_yoy is not None else None,
                float(revenue) if revenue is not None else None,
                float(revenue_yoy) if revenue_yoy is not None else None,
                float(eps) if eps is not None else None,
                float(bvps) if bvps is not None else None,
            ))
        
        if values:
            try:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO financial_data 
                       (symbol, end_date, roe, net_profit, net_profit_yoy, revenue, revenue_yoy, eps, bvps, updated_at)
                       VALUES %s
                       ON CONFLICT (symbol, end_date) DO UPDATE SET
                           roe=EXCLUDED.roe,
                           net_profit=EXCLUDED.net_profit,
                           net_profit_yoy=EXCLUDED.net_profit_yoy,
                           revenue=EXCLUDED.revenue,
                           revenue_yoy=EXCLUDED.revenue_yoy,
                           eps=EXCLUDED.eps,
                           bvps=EXCLUDED.bvps,
                           updated_at=NOW()""",
                    values,
                    template='(%s, %s::date, %s, %s, %s, %s, %s, %s, %s, NOW())'
                )
                inserted += cur.rowcount
                conn.commit()
            except Exception as e:
                print(f"  DB error page {page}: {e}")
                conn.rollback()
        
        if page % 20 == 0:
            cur.execute("SELECT COUNT(*) FROM financial_data")
            cnt = cur.fetchone()[0]
            print(f"  第{page:>4}/{total_pages}页  本批{len(values)}条  跳过{skipped}  总量{cnt:,}", flush=True)
        
        # 提前终止条件：成功获取数据且所有记录均早于MIN_DATE
        if data and all((r.get('REPORT_DATE', '') or '')[:10] < MIN_DATE for r in data):
            print(f"  第{page}页所有记录<{MIN_DATE}，提前结束")
            break
        
        time.sleep(1.2)
    
    cur.execute("SELECT COUNT(*), MIN(end_date), MAX(end_date) FROM financial_data")
    cnt, mn, mx = cur.fetchone()
    print(f"\n✅ 完成！financial_data: {cnt:,}条 ({mn} ~ {mx})")
    print(f"本次新增: {inserted}条 | 跳过已有: {skipped}条")
    cur.close()
    conn.close()

if __name__ == '__main__':
    t0 = time.time()
    main()
    print(f"耗时 {time.time()-t0:.0f}s")
