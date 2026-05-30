#!/usr/bin/env python3
"""
fetch_exec_hold_all.py — 从东财datacenter拉取全量董监高持股变动数据（沪深京）

数据源: RPT_EXECUTIVE_HOLD_DETAILS
覆盖: 2004~2026, 沪深京全部
总量: ~168K条
写入: exec_hold_changes 表
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, psycopg2.extras
import requests, time, json
from datetime import datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
BASE_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://data.eastmoney.com/executive/',
}
PAGE_SIZE = 500
BATCH_SIZE = 1000

def create_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS exec_hold_changes (
            id SERIAL PRIMARY KEY,
            security_code VARCHAR(10),
            security_name VARCHAR(50),
            person_name VARCHAR(50),
            position_name VARCHAR(200),
            change_date DATE,
            change_shares BIGINT,
            average_price NUMERIC(12,2),
            change_amount NUMERIC(16,2),
            change_reason VARCHAR(100),
            change_ratio NUMERIC(12,6),
            change_after_holdnum BIGINT,
            hold_type VARCHAR(20),
            dse_person_name VARCHAR(50),
            person_dse_relation VARCHAR(50),
            begin_hold_num BIGINT,
            end_hold_num BIGINT,
            source VARCHAR(10) DEFAULT 'EM',
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (security_code, change_date, person_name, change_shares, change_reason)
        )
    """)
    conn.commit()
    print("表 exec_hold_changes 已就绪")

def fetch_page(page_no):
    """拉取单页数据"""
    params = {
        'reportName': 'RPT_EXECUTIVE_HOLD_DETAILS',
        'columns': 'ALL',
        'pageNumber': page_no,
        'pageSize': PAGE_SIZE,
        'source': 'WEB',
        'client': 'WEB',
        'sortTypes': -1,
        'sortColumns': 'CHANGE_DATE',
    }
    for attempt in range(3):
        try:
            resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                j = resp.json()
                if j.get('success'):
                    return j.get('result', {}).get('data', [])
                else:
                    print(f"  [API Error] page {page_no}: {j.get('message', 'unknown')}")
            else:
                print(f"  [HTTP {resp.status_code}] page {page_no}")
        except Exception as e:
            print(f"  [Fetch Error] page {page_no}: {e}")
        time.sleep(3)  # 重试前等待
    return []

def run_full_fetch():
    conn = psycopg2.connect(DB)
    conn.set_session(autocommit=False)
    cur = conn.cursor()
    
    create_table(conn)
    
    # 第一页获取总条数和页数
    print("获取第一页...")
    data = fetch_page(1)
    if not data:
        print("无法获取数据，退出")
        return
    
    # 重新请求获取total（first page只返回500条，但result里有count）
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
    resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    j = resp.json()
    total = j.get('result', {}).get('count', 0)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    print(f"总条数: {total}, 总页数: {total_pages}")
    
    total_inserted = 0
    total_skipped = 0
    
    for page in range(1, total_pages + 1):
        # 第1页已有数据，不用再请求
        if page == 1:
            rows = data
        else:
            rows = fetch_page(page)
            time.sleep(1)  # 防止请求过快
        
        if not rows:
            print(f"  第{page}页 空，继续")
            continue
        
        # 转换为数据库记录
        values = []
        for r in rows:
            try:
                cd = r.get('CHANGE_DATE', '')
                if cd:
                    cd = cd[:10]  # '2026-05-29 00:00:00' -> '2026-05-29'
                
                cs = r.get('CHANGE_SHARES')
                if cs is not None:
                    cs = int(float(str(cs).replace(',', '')))
                
                ap = r.get('AVERAGE_PRICE')
                if ap is not None and str(ap).strip():
                    ap = float(ap)
                else:
                    ap = None
                
                ca = r.get('CHANGE_AMOUNT')
                if ca is not None and str(ca).strip():
                    ca = float(ca)
                else:
                    ca = None
                
                cr = r.get('CHANGE_RATIO')
                if cr is not None and str(cr).strip():
                    cr = float(cr)
                else:
                    cr = None
                
                cah = r.get('CHANGE_AFTER_HOLDNUM')
                if cah is not None and str(cah).strip():
                    cah = int(float(str(cah).replace(',', '')))
                else:
                    cah = None
                
                bhn = r.get('BEGIN_HOLD_NUM')
                if bhn is not None and str(bhn).strip():
                    bhn = int(float(str(bhn).replace(',', '')))
                else:
                    bhn = None
                
                ehn = r.get('END_HOLD_NUM')
                if ehn is not None and str(ehn).strip():
                    ehn = int(float(str(ehn).replace(',', '')))
                else:
                    ehn = None
                
                values.append((
                    r.get('SECURITY_CODE', ''),
                    r.get('SECURITY_NAME', ''),
                    r.get('PERSON_NAME', ''),
                    r.get('POSITION_NAME', ''),
                    cd,
                    cs,
                    ap,
                    ca,
                    r.get('CHANGE_REASON', ''),
                    cr,
                    cah,
                    r.get('HOLD_TYPE', ''),
                    r.get('DSE_PERSON_NAME', ''),
                    r.get('PERSON_DSE_RELATION', ''),
                    bhn,
                    ehn,
                ))
            except (ValueError, TypeError) as e:
                print(f"  [Parse Error] {r.get('SECURITY_CODE','')}: {e}")
                continue
        
        # 写入数据库
        inserted = 0
        skipped = 0
        temp_schema = ','.join([
            'security_code', 'security_name', 'person_name', 'position_name',
            'change_date', 'change_shares', 'average_price', 'change_amount',
            'change_reason', 'change_ratio', 'change_after_holdnum',
            'hold_type', 'dse_person_name', 'person_dse_relation',
            'begin_hold_num', 'end_hold_num'
        ])
        
        for i in range(0, len(values), BATCH_SIZE):
            chunk = values[i:i+BATCH_SIZE]
            try:
                psycopg2.extras.execute_values(
                    cur,
                    f"""INSERT INTO exec_hold_changes 
                        ({temp_schema})
                       VALUES %s
                       ON CONFLICT (security_code, change_date, person_name, change_shares, change_reason) 
                       DO NOTHING""",
                    chunk,
                    template='(%s,%s,%s,%s,%s::date,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'
                )
                inserted += cur.rowcount
                skipped += len(chunk) - cur.rowcount
            except Exception as e:
                print(f"  [DB Error] batch {i//BATCH_SIZE}: {e}")
                conn.rollback()
                continue
            
            if (i // BATCH_SIZE) % 5 == 0:
                conn.commit()
        
        conn.commit()
        total_inserted += inserted
        total_skipped += skipped
        
        print(f"  第{page}/{total_pages}页 ({len(rows)}条 → 入库{inserted}, 跳过{skipped})")
    
    # 最终统计
    cur.execute("SELECT COUNT(*), MIN(change_date), MAX(change_date) FROM exec_hold_changes")
    cnt, mn, mx = cur.fetchone()
    
    print(f"\n{'='*50}")
    print(f"  入库总计: {total_inserted} 条")
    print(f"  跳过总计: {total_skipped} 条(重复)")
    print(f"  exec_hold_changes 总: {cnt}条 ({mn} ~ {mx})")
    
    cur.close()
    conn.close()
    return total_inserted

if __name__ == '__main__':
    t0 = time.time()
    print(f"【全量董监高持股变动拉取】{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    run_full_fetch()
    dt = time.time() - t0
    print(f"完成，耗时 {dt:.1f}s")
