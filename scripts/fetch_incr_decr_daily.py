#!/usr/bin/env python3
"""
fetch_incr_decr_daily.py — 每日增量抓取上交所董监高增减持

分页拉取全量数据，临时表 + NOT EXISTS 去重。
加入 max_pages 防护避免接口异常无限循环。
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, psycopg2.extras
import requests, time
from datetime import datetime, date

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
SSE_URL = 'https://query.sse.com.cn/commonQuery.do'
HEADERS = {
    'Referer': 'https://www.sse.com.cn/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}

BATCH_SIZE = 2000
MAX_PAGES = 15  # 安全上限（接口返回67647条，约14页）

def parse_change_date(s):
    s = s.strip()
    for fmt in ('%Y%m%d', '%Y-%m-%d', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"cannot parse: {s}")

def fetch_daily_increment():
    conn = psycopg2.connect(DB)
    conn.set_session(autocommit=False)
    cur = conn.cursor()
    
    today = date.today()
    total_inserted = 0
    total_skipped = 0
    
    # 准备临时表
    cur.execute("""
        CREATE TEMP TABLE IF NOT EXISTS tmp_incr_decr (
            code varchar, name varchar, person varchar, position varchar,
            change_date date, change_num bigint, change_price numeric,
            change_reason varchar, is_increase bool, is_decrease bool, abs_change bigint
        ) ON COMMIT DELETE ROWS
    """)
    conn.commit()
    
    api_date_str = today.strftime('%Y-%m-%d')
    print(f"  分页拉取 {api_date_str} ...")
    
    max_pages = MAX_PAGES
    consecutive_empty = 0
    last_page_count = 0
    last_five_same = 0  # 连续相同数据量的页数
    
    for page in range(1, max_pages + 1):
        params = {
            'sqlId': 'COMMON_SSE_XXPL_CXJL_SSGSGFBDQK_S',
            'pageHelp.pageSize': 5000,
            'pageHelp.pageNo': page,
            'isPagination': 'true',
            'startDate': api_date_str,
            'endDate': api_date_str,
        }
        try:
            resp = requests.get(SSE_URL, params=params, headers=HEADERS, timeout=60)
            data = resp.json()
        except Exception as e:
            print(f"  [ERROR] page {page}: {e}")
            break
        
        rows = data.get('result', [])
        help_info = data.get('pageHelp', {})
        total_api = help_info.get('total', 0)
        
        if page == 1:
            print(f"  接口总数: {total_api} 条")
            # 估算合理页数
            reasonable_pages = (total_api // 5000) + 2
            max_pages = min(max_pages, reasonable_pages)
        
        if not rows:
            print(f"  第{page}页 空，结束")
            break
        
        # 检测相同数据量的重复页（接口bug）
        if len(rows) == last_page_count and last_page_count > 0:
            last_five_same += 1
            if last_five_same >= 5:
                print(f"  第{page}页: 连续5页数据量相同，终止(接口bug)")
                break
        else:
            last_five_same = 0
        last_page_count = len(rows)
        
        # 解析
        values = []
        for r in rows:
            cds = r.get('CHANGE_DATE', '').strip()
            if not cds:
                continue
            try:
                cn = int(r.get('CHANGE_NUM', 0) or 0)
            except (ValueError, TypeError):
                continue
            if cn == 0:
                continue
            try:
                cd = parse_change_date(cds)
            except ValueError:
                continue
            if cd >= today:
                continue
            
            values.append((
                r.get('COMPANY_CODE', ''),
                r.get('COMPANY_ABBR', ''),
                r.get('PERSON_NAME', ''),
                r.get('POSITION_NAME', ''),
                cd, cn,
                float(r.get('CHANGE_PRICE', 0) or 0),
                r.get('CHANGE_REASON', ''),
                cn > 0, cn < 0, abs(cn),
            ))
        
        if not values:
            consecutive_empty += 1
            if consecutive_empty >= 3:
                print(f"  第{page}页: 连续3页无有效数据，结束")
                break
            continue
        consecutive_empty = 0
        
        # 分块写入临时表→正式表
        inserted = 0
        for i in range(0, len(values), BATCH_SIZE):
            chunk = values[i:i+BATCH_SIZE]
            cur.execute("TRUNCATE tmp_incr_decr")
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO tmp_incr_decr 
                    (code, name, person, position, change_date,
                     change_num, change_price, change_reason,
                     is_increase, is_decrease, abs_change)
                   VALUES %s""",
                chunk,
                template='(%s,%s,%s,%s,%s::date,%s,%s,%s,%s,%s,%s)'
            )
            cur.execute("""
                INSERT INTO incr_decr 
                    (code, name, person, position, change_date,
                     change_num, change_price, change_reason,
                     is_increase, is_decrease, abs_change)
                SELECT t.* FROM tmp_incr_decr t
                WHERE NOT EXISTS (
                    SELECT 1 FROM incr_decr d 
                    WHERE d.code = t.code 
                      AND d.change_date = t.change_date 
                      AND COALESCE(d.person,'') = COALESCE(t.person,'')
                      AND d.change_num = t.change_num
                )
                ON CONFLICT (code, change_date, change_num) DO NOTHING
            """)
            inserted += cur.rowcount
            total_skipped += len(chunk) - cur.rowcount
            conn.commit()
        
        total_inserted += inserted
        
        # 如果某页全部已存在，快速结束
        if inserted == 0 and len(values) == len(values):
            # 但如果是从第1页就开始跳过，继续
            pass
        
        print(f"  第{page}页 ({len(values)}有效 → 入库{inserted})")
        
        # 如果上一页+这一页都没有新数据，且已过3页，提前结束
        if page >= 3 and inserted == 0:
            # 检查之前是否一直没新数据
            pass  # 交给数据量重复检测来处理
        
        time.sleep(0.3)
    
    cur.execute("SELECT COUNT(*), MIN(change_date), MAX(change_date) FROM incr_decr")
    cnt, mn, mx = cur.fetchone()
    
    print(f"\n  入库 {total_inserted} 条, 跳过 {total_skipped} 条(已存在)")
    print(f"  incr_decr 表总计: {cnt}条 ({mn} ~ {mx})")
    
    cur.close()
    conn.close()
    return total_inserted

if __name__ == '__main__':
    print(f"【增减持每日抓取】{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    fetch_daily_increment()
    print("完成")
