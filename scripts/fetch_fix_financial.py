#!/usr/bin/env python3
"""
财务数据全量修复 - 东财datacenter RPT_F10_FINANCE_MAINFINADATA

问题：baostock/mootdx写入的老数据净利全错（99%的小数值）
方案：用东财datacenter API批量拉取全部股票的历史财务数据覆盖

用法: /usr/bin/python3 fetch_fix_financial.py
"""
import sys, time, logging, requests, psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fix_fin")

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

FIELD_MAP = {
    'PARENTNETPROFIT': 'net_profit',
    'OPERATE_INCOME_PK': 'revenue',
    'BPS': 'bvps',
    'EPSJB': 'eps',
    'ROEJQ': 'roe',
}

def get_pg():
    return psycopg2.connect(DB)

def get_all_codes():
    conn = get_pg()
    cur = conn.cursor()
    # 只取那些所有季度净利都<1元的（数据有问题的）
    # 或北交所（新数据）但缺少历史季度的
    cur.execute("""
        SELECT DISTINCT symbol FROM (
            SELECT symbol, 
                   COUNT(*) FILTER (WHERE net_profit IS NOT NULL AND ABS(net_profit) < 1) as tiny_q,
                   COUNT(*) as total_q
            FROM financial_data
            GROUP BY symbol
        ) sub
        WHERE tiny_q > total_q * 0.5
    """)
    codes = sorted(set(r[0] if not r[0].startswith('A.') or len(r[0]) < 10 else r[0][2:] for r in cur.fetchall()))
    codes = [c for c in codes if c.isdigit() and len(c) == 6]
    conn.close()
    log.info(f"需要修复的股票(有错误数据的): {len(codes)} 只")
    return codes

def get_market_suffix(code):
    """获取东财SECUCODE后缀"""
    if code.startswith('6') or code.startswith('9'):
        return '.SH'
    elif code.startswith('0') or code.startswith('3') or code.startswith('2'):
        return '.SZ'
    elif code.startswith('920'):
        return '.BJ'
    elif code.startswith('8'):
        return '.BJ'  # 北交所老代码格式
    elif code.startswith('4'):
        return '.BJ'
    else:
        return '.SZ'

def fetch_stock(code, report_name='RPT_F10_FINANCE_MAINFINADATA'):
    """拉一只股票的全部历史财务数据"""
    suffix = get_market_suffix(code)
    url = 'https://datacenter.eastmoney.com/securities/api/data/v1/get'
    params = {
        'reportName': report_name,
        'columns': 'ALL',
        'pageNumber': 1, 'pageSize': 30,
        'sortTypes': -1,
        'sortColumns': 'REPORT_DATE',
        'source': 'HSF10', 'client': 'PC',
        'filter': f'(SECUCODE="{code}{suffix}")',
    }
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        j = r.json()
        if j.get('success') and j['result'] and j['result'].get('data'):
            return j['result']['data']
        return None
    except Exception as e:
        log.warning(f"  {code}: 请求失败 {e}")
        return None

def main():
    codes = get_all_codes()
    total_rows = 0
    failed = []
    skipped = 0
    
    for idx, code in enumerate(codes):
        if idx > 0 and idx % 50 == 0:
            log.info(f"进度 {idx}/{len(codes)}, 已写入 {total_rows} 行, 失败 {len(failed)}")
        
        data = fetch_stock(code)
        if not data:
            failed.append(code)
            # 别打太频繁
            time.sleep(0.3)
            continue
        
        batch = []
        for item in data:
            report_date = str(item.get('REPORT_DATE', ''))[:10]
            if not report_date:
                continue
            
            year, month = int(report_date[:4]), int(report_date[5:7])
            q_type = (month - 1) // 3 + 1
            
            np_val = item.get('PARENTNETPROFIT')
            rev_val = item.get('OPERATE_INCOME_PK')
            bps_val = item.get('BPS')
            eps_val = item.get('EPSJB')
            roe_val = item.get('ROEJQ')
            bvps_val = item.get('BPS')
            
            if np_val is None and rev_val is None:
                continue
            
            # 转float
            try:
                np_val = float(np_val) if np_val is not None else None
            except: np_val = None
            try:
                rev_val = float(rev_val) if rev_val is not None else None
            except: rev_val = None
            try:
                roe_val = float(roe_val) if roe_val is not None else None
            except: roe_val = None
            try:
                eps_val = float(eps_val) if eps_val is not None else None
            except: eps_val = None
            try:
                bvps_val = float(bvps_val) if bvps_val is not None else None
            except: bvps_val = None
            
            batch.append(('A.' + code, report_date, roe_val, np_val, rev_val, eps_val, bvps_val, q_type, year))
        
        if not batch:
            failed.append(code)
            time.sleep(0.3)
            continue
        
        # 批量Upsert
        try:
            conn = get_pg()
            cur = conn.cursor()
            for b in batch:
                cur.execute("""
                    INSERT INTO financial_data (symbol, end_date, roe, net_profit, revenue, eps, bvps, quarter_type, year)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, end_date) DO UPDATE SET
                        roe=EXCLUDED.roe,
                        net_profit=EXCLUDED.net_profit,
                        revenue=EXCLUDED.revenue,
                        eps=EXCLUDED.eps,
                        bvps=EXCLUDED.bvps
                """, b)
            conn.commit()
            cur.close()
            conn.close()
            total_rows += len(batch)
        except Exception as e:
            failed.append(f"{code}(DB:{e})")
        
        time.sleep(0.2)  # 限速
    
    log.info(f"\n✅ 修复完成！")
    log.info(f"  累计写入/更新: {total_rows} 行（{len(codes)} 只股票）")
    log.info(f"  失败: {len(failed)} 只")
    if failed:
        log.info(f"  失败前20: {failed[:20]}")
    
    # 修完后验证
    log.info(f"\n=== 修复后验证 ===")
    conn = get_pg()
    cur = conn.cursor()
    for q in ['2026-03-31', '2025-12-31', '2025-06-30', '2024-12-31', '2024-06-30', '2023-12-31']:
        cur.execute("""
            SELECT COUNT(*) as total,
                   COUNT(*) FILTER (WHERE net_profit IS NOT NULL AND ABS(net_profit) < 1) as tiny
            FROM financial_data WHERE end_date = %s
        """, (q,))
        r = cur.fetchone()
        tiny_pct = r[1]/r[0]*100 if r[0] > 0 else 0
        log.info(f"  {q}: {r[1]}/{r[0]}条错误 = {tiny_pct:.1f}%")
    conn.close()


if __name__ == '__main__':
    main()
