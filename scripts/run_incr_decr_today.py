#!/usr/bin/env python3
"""仅拉取最新增减持数据 -- 上交所全量接口不支持按日期过滤，改用全量扫描+去重"""
import sys, os
for k in ['http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY']:
    os.environ.pop(k, None)
sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, psycopg2.extras
import requests, time
from datetime import datetime, date, timedelta

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto connect_timeout=10'
SSE_URL = 'https://query.sse.com.cn/commonQuery.do'
HEADERS = {'Referer':'https://www.sse.com.cn/',
           'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
BATCH_SIZE = 2000

def parse_cd(s):
    s = s.strip()
    for f in ('%Y%m%d','%Y-%m-%d','%Y/%m/%d'):
        try: return datetime.strptime(s,f).date()
        except: pass
    raise ValueError

# 获取当前数据库最新日期
conn = psycopg2.connect(DB)
cur = conn.cursor()
cur.execute("SELECT MAX(change_date) FROM incr_decr")
max_db_date = cur.fetchone()[0] or date(2000,1,1)
cur.close()
conn.close()
print(f"数据库最新数据日期: {max_db_date}")

# 分页拉取全量数据
print("拉取全量接口数据...")
total_ins = total_skp = 0
latest_data = {}  # (code, date, person, num) -> row

# 用大pageSize，每次拉一页就行（接口不改分页）
conn = psycopg2.connect(DB)
conn.set_session(autocommit=False)
cur = conn.cursor()

# 先从MAX(d)日期开始，往前扫到有数据的地方
target_date = date.today()
# 最近5个交易日
all_new = []

for pg in range(1, 20):  # 最多20页
    try:
        r = requests.get(SSE_URL, params={
            'sqlId':'COMMON_SSE_XXPL_CXJL_SSGSGFBDQK_S',
            'pageHelp.pageSize':5000,'pageHelp.pageNo':pg,
            'isPagination':'true',
        }, headers=HEADERS, timeout=30)
        data = r.json()
    except Exception as e:
        print(f'  [ERR] p{pg}: {e}')
        break

    rows = data.get('result', [])
    if not rows:
        print(f'  p{pg} empty, done')
        break
    
    # 解析并只保留 >= max_db_date 的数据
    vals = []
    for row in rows:
        cds = row.get('CHANGE_DATE','').strip()
        if not cds: continue
        try: cd = parse_cd(cds)
        except: continue
        if cd < max_db_date:
            # 因为按日期排序（最新的在前），到了旧数据就停止
            pass  # 先收集，后面判断
        try: cn = int(row.get('CHANGE_NUM',0) or 0)
        except: continue
        if cn == 0: continue
        vals.append((cd, row.get('COMPANY_CODE',''), row.get('COMPANY_ABBR',''),
                     row.get('PERSON_NAME','') or '', row.get('POSITION_NAME','') or '',
                     cn, float(row.get('CHANGE_PRICE',0) or 0),
                     row.get('CHANGE_REASON',''), cn>0, cn<0, abs(cn)))

    if not vals:
        print(f'  p{pg}: no valid records')
        continue

    # 检查这条页是否全是 <= max_db_date 的旧数据
    max_in_page = max(v[0] for v in vals)
    min_in_page = min(v[0] for v in vals)
    print(f'  p{pg}: {len(vals)} valid, date range {min_in_page} ~ {max_in_page}')
    
    all_new.extend(v for v in vals if v[0] > max_db_date)
    
    # 如果这一页的最小日期 < max_db_date，基本都扫完了
    if min_in_page <= max_db_date:
        print(f'  遇到旧数据区，停止分页')
        break
    
    time.sleep(0.3)

# 去重后入库
print(f'\n待入库新数据: {len(all_new)} 条')
if all_new:
    for t in range(0, len(all_new), BATCH_SIZE):
        chunk = all_new[t:t+BATCH_SIZE]
        # 用临时表
        cur.execute("""
            CREATE TEMP TABLE IF NOT EXISTS tmp_incr_decr (
                code varchar, name varchar, person varchar, position varchar,
                change_date date, change_num bigint, change_price numeric,
                change_reason varchar, is_increase bool, is_decrease bool, abs_change bigint
            ) ON COMMIT DELETE ROWS
        """)
        cur.execute("TRUNCATE tmp_incr_decr")
        psycopg2.extras.execute_values(cur,
            'INSERT INTO tmp_incr_decr VALUES %s', chunk,
            template='(%s,%s,%s,%s,%s::date,%s,%s,%s,%s,%s,%s)')
        cur.execute("""
            INSERT INTO incr_decr (code,name,person,position,change_date,
                change_num,change_price,change_reason,is_increase,is_decrease,abs_change)
            SELECT t.* FROM tmp_incr_decr t
            WHERE NOT EXISTS (
                SELECT 1 FROM incr_decr d
                WHERE d.code=t.code AND d.change_date=t.change_date
                  AND COALESCE(d.person,'')=COALESCE(t.person,'')
                  AND d.change_num=t.change_num)
            ON CONFLICT (code,change_date,change_num) DO NOTHING""")
        total_ins += cur.rowcount
        total_skp += len(chunk) - cur.rowcount
        conn.commit()
        print(f'  批次 {t//BATCH_SIZE+1}: {cur.rowcount} 入库')

cur.execute("SELECT count(*), min(change_date), max(change_date) FROM incr_decr")
c, mn, mx = cur.fetchone()
print(f'\n=== 汇总 ===')
print(f'本次入库 {total_ins} 条, 跳过 {total_skp} 条(已存在)')
print(f'incr_decr 表总计: {c}条 ({mn} ~ {mx})')
if total_ins > 0:
    cur.execute("SELECT * FROM incr_decr WHERE change_date = (SELECT MAX(change_date) FROM incr_decr) LIMIT 5")
    for row in cur.fetchall():
        print(f'  最新: {row}')

cur.close()
conn.close()
