#!/usr/bin/env python3
# preflight-ok: true
"""
fetch_exec_hold_daily.py — 增量更新exec_hold_changes表（东财datacenter）
每日收盘后运行，拉取当日及此前未入库的数据
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, psycopg2.extras
import requests, time, json
from datetime import datetime, date

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
BASE_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://data.eastmoney.com/executive/',
}
PAGE_SIZE = 500

def main():
    conn = psycopg2.connect(DB)
    cur = conn.cursor()

    # 取已有最新日期，拉该日期之后的数据
    cur.execute("SELECT MAX(change_date) FROM exec_hold_changes")
    max_date = cur.fetchone()[0]
    print(f"当前已有数据截至: {max_date}")

    # 按升序取比现有最大日期更新的数据
    total_inserted = 0
    params = {
        'reportName': 'RPT_EXECUTIVE_HOLD_DETAILS',
        'columns': 'ALL',
        'pageNumber': 1,
        'pageSize': PAGE_SIZE,
        'source': 'WEB',
        'client': 'WEB',
        'sortTypes': -1,
        'sortColumns': 'CHANGE_DATE',
    }

    # 先取第一页获取最新日期
    resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    j = resp.json()
    total = j['result']['count']
    print(f"API总条数: {total}")

    # 降序拉取，从第一页起全部尝试插入（有ON CONFLICT DO NOTHING）
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    inserted_total = 0

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
            cd = (r.get('CHANGE_DATE', '') or '')[:10]
            cs = r.get('CHANGE_SHARES')
            if cs is not None:
                try: cs = int(str(cs).replace(',', ''))
                except: cs = None
            ap = r.get('AVERAGE_PRICE')
            if ap is not None and str(ap).strip():
                try: ap = float(ap)
                except: ap = None
            else: ap = None

            values.append((
                r.get('SECURITY_CODE', ''), r.get('SECURITY_NAME', ''),
                r.get('PERSON_NAME', ''), r.get('POSITION_NAME', ''),
                cd, cs, ap, r.get('CHANGE_AMOUNT'),
                r.get('CHANGE_REASON', ''), r.get('CHANGE_RATIO'),
                r.get('CHANGE_AFTER_HOLDNUM'), r.get('HOLD_TYPE', ''),
                r.get('DSE_PERSON_NAME', ''), r.get('PERSON_DSE_RELATION', ''),
                r.get('BEGIN_HOLD_NUM'), r.get('END_HOLD_NUM'),
            ))

        try:
            psycopg2.extras.execute_values(
                cur,
                '''INSERT INTO exec_hold_changes 
                    (security_code, security_name, person_name, position_name,
                     change_date, change_shares, average_price, change_amount,
                     change_reason, change_ratio, change_after_holdnum,
                     hold_type, dse_person_name, person_dse_relation,
                     begin_hold_num, end_hold_num)
                   VALUES %s
                   ON CONFLICT (security_code, change_date, person_name, change_shares, change_reason) 
                   DO NOTHING''',
                values,
                template='(%s,%s,%s,%s,%s::date,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'
            )
            p_inserted = cur.rowcount
            inserted_total += p_inserted
            conn.commit()
        except Exception as e:
            print(f"  DB error page {page}: {e}")
            conn.rollback()

        if page % 20 == 0:
            print(f"  第{page}/{total_pages}: 本批入库{inserted_total}条")

        time.sleep(1.5)

    cur.execute("SELECT COUNT(*), MIN(change_date), MAX(change_date) FROM exec_hold_changes")
    cnt, mn, mx = cur.fetchone()
    print(f"\n增量完成: 入库{inserted_total}条新数据")
    print(f"exec_hold_changes: {cnt}条 ({mn} ~ {mx})")
    cur.close()
    conn.close()

if __name__ == '__main__':
    main()
