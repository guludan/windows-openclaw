#!/usr/bin/env python3
"""
北交所(92xxxx)财务数据补拉 — 东财datacenter API

数据源: datacenter.eastmoney.com RPT_F10_FINANCE_MAINFINADATA
走直连（东财datacenter没封WSL IP）
字段映射:
  PARENTNETPROFIT → net_profit
  OPERATE_INCOME_PK → revenue
  BPS → bvps
  RPS → eps? 没有，用 EPSJB
  ROEJQ → roe
  NETCASH_OPERATE_PK → 经营现金流（虽然financial_data没有此列，暂不存）
  TOTAL_EQUITY_PK → 净资产合计
  TOTAL_ASSETS_PK → 总资产

用法: conda tt 环境下 /home/tearsyu/miniconda3/envs/tt/bin/python3 fetch_financial_bj.py
"""
import sys, time, logging, requests, json
import psycopg2
import psycopg2.extras as pge

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fetch_bj")

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

def get_pg():
    return psycopg2.connect(DB)

def get_active_bj_codes():
    """获取北交所活跃股票代码（纯6位数字）"""
    conn = get_pg()
    cur = conn.cursor()
    cur.execute("SELECT code FROM stock_list WHERE code LIKE '920%' AND active = true")
    codes = [r[0] for r in cur.fetchall()]
    conn.close()
    return codes

def get_existing():
    """已有数据的 (symbol, end_date)"""
    conn = get_pg()
    cur = conn.cursor()
    cur.execute("SELECT symbol, MAX(end_date) FROM financial_data GROUP BY symbol")
    existing = {r[0]: str(r[1])[:10] for r in cur.fetchall()}
    conn.close()
    return existing

def parse_q_end_date(report_date):
    """东财report_date '2026-03-31' → quarter_type"""
    month = int(report_date.split('-')[1])
    year = int(report_date.split('-')[0])
    q = (month - 1) // 3 + 1
    return year, q

# 字段映射
FIELD_MAP = {
    'NETPROFIT_ATTR': 'net_profit',  # 原名PARENTNETPROFIT
    'PARENTNETPROFIT': 'net_profit',
    'OPERATE_INCOME_PK': 'revenue',
    'BPS': 'bvps',
    'EPSJB': 'eps',
    'ROEJQ': 'roe',
    'NETCASH_OPERATE_PK': 'cash_flow_oper',
    'TOTAL_EQUITY_PK': 'total_equity',
    'TOTAL_ASSETS_PK': 'total_assets',
}

def fetch_stock_finance(code, report_name='RPT_F10_FINANCE_MAINFINADATA'):
    """拉一只北交所股票的财务数据"""
    url = 'https://datacenter.eastmoney.com/securities/api/data/v1/get'
    params = {
        'reportName': report_name,
        'columns': 'ALL',
        'pageNumber': 1, 'pageSize': 5,
        'sortTypes': -1,
        'sortColumns': 'REPORT_DATE',
        'source': 'HSF10', 'client': 'PC',
        'filter': f'(SECUCODE="{code}.BJ")'
    }
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        j = r.json()
        if j.get('success') and j.get('result') and j['result'].get('data'):
            return j['result']['data']
        return None
    except Exception as e:
        log.warning(f"  {code}: 请求失败 {e}")
        return None

def main():
    codes = get_active_bj_codes()
    log.info(f"北交所活跃股票: {len(codes)} 只")
    
    existing = get_existing()
    # 已有最近end_date的不用补
    # 但北交所全无数据，所以全补
    codes_6 = [c for c in codes if len(c) == 6 and c.startswith('920')]
    # 有些可能以4开头, 过滤
    log.info(f"92xxxx格式: {len(codes_6)} 只")
    
    total_rows = 0
    failed = []
    skipped = 0
    
    for idx, code in enumerate(codes_6):
        if idx > 0 and idx % 20 == 0:
            log.info(f"进度 {idx}/{len(codes_6)}, 已写入 {total_rows} 行")
        
        # 检查是否已有数据（跳过已有最新季度的）
        if code in existing:
            skipped += 1
            continue
        
        data = fetch_stock_finance(code)
        if not data:
            failed.append((code, '无数据'))
            time.sleep(0.3)
            continue
        
        batch = []
        for item in data:
            # report_date = 报表截止日
            report_date = str(item.get('REPORT_DATE', ''))[:10]
            if not report_date:
                continue
            year, q_type = parse_q_end_date(report_date)
            
            net_profit = item.get('PARENTNETPROFIT')
            revenue = item.get('OPERATE_INCOME_PK')
            bps = item.get('BPS')
            eps = item.get('EPSJB')
            roe = item.get('ROEJQ')
            # 去掉None的跳过（至少要有净利或营收）
            if net_profit is None and revenue is None:
                continue
            
            # 处理可能的字符串数值
            try:
                net_profit = float(net_profit) if net_profit else None
            except:
                net_profit = None
            try:
                revenue = float(revenue) if revenue else None
            except:
                revenue = None
            try:
                bps = float(bps) if bps else None
            except:
                bps = None
            try:
                eps = float(eps) if eps else None
            except:
                eps = None
            try:
                roe = float(roe) if roe else None
            except:
                roe = None
            
            batch.append(('A.' + code, report_date, roe, net_profit, revenue, eps, bps, q_type, year))
        
        if not batch:
            failed.append((code, '解析无有效行'))
            time.sleep(0.3)
            continue
        
        # 批量写入
        try:
            conn = get_pg()
            cur = conn.cursor()
            for b in batch:
                cur.execute("""
                    INSERT INTO financial_data 
                    (symbol, end_date, roe, net_profit, revenue, eps, bvps, quarter_type, year)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, end_date) DO UPDATE SET
                    roe=EXCLUDED.roe, net_profit=EXCLUDED.net_profit,
                    revenue=EXCLUDED.revenue, eps=EXCLUDED.eps,
                    bvps=EXCLUDED.bvps
                """, b)
            conn.commit()
            cur.close()
            total_rows += len(batch)
        except Exception as e:
            failed.append((code, f'DB错误: {e}'))
        
        time.sleep(0.5)  # 东财API限速
    
    log.info(f"\n✅ 完成！")
    log.info(f"  累计写入: {total_rows} 行")
    log.info(f"  跳过(已有): {skipped} 只")
    log.info(f"  失败: {len(failed)} 只")
    if failed:
        log.info(f"  失败前10: {failed[:10]}")


if __name__ == '__main__':
    main()
