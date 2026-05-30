#!/usr/bin/env python3
"""
构建同花顺行业成分股映射表
用东方财富API拉成分股，匹配到同花顺行业名
"""
import sys, os, time, json, re
import numpy as np
import requests
sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
import data_finance as df
from datetime import date

S = requests.Session()
S.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://data.eastmoney.com/',
})

def get_bk_list():
    """获取东方财富所有行业板块的BK代码和名称"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        'pn': '1', 'pz': '500', 'po': '1',
        'np': '1', 'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
        'fltt': '2', 'invt': '2', 'fid': 'f3',
        'fs': 'm:90+t:2',
        'fields': 'f12,f14'
    }
    r = S.get(url, params=params, timeout=10)
    data = r.json()
    result = []
    for item in data.get('data', {}).get('diff', []):
        result.append((item['f12'], item['f14']))
    return result  # [(BK_code, name), ...]

def fetch_cons(bk_code):
    """拉东方财富板块成分股"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        'pn': '1', 'pz': '500', 'po': '1',
        'np': '1', 'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
        'fltt': '2', 'invt': '2', 'fid': 'f3',
        'fs': f'b:{bk_code}+f:!50',
        'fields': 'f12,f14',
    }
    r = S.get(url, params=params, timeout=10)
    data = r.json()
    codes = []
    for item in data.get('data', {}).get('diff', []):
        code = item.get('f12', '')
        name = item.get('f14', '')
        if code:
            codes.append((code, name))
    return codes

def main():
    today = date.today()
    today_str = today.strftime('%Y-%m-%d')
    
    # 1. 东方财富行业列表
    print("📡 获取东方财富行业列表...")
    bk_list = get_bk_list()
    print(f"  东方财富行业: {len(bk_list)}个")
    
    # 2. 同花顺行业列表
    import akshare as ak
    ths_df = ak.stock_board_industry_name_ths()
    ths_names = set(ths_df['name'].tolist())
    print(f"  同花顺行业: {len(ths_names)}个")
    
    # 3. 匹配：东方财富名 → 同花顺名 模糊匹配
    # 先用精确匹配，再用子串匹配
    bk_to_ths = {}  # bk_name → ths_name
    for bk_code, bk_name in bk_list:
        if bk_name in ths_names:
            bk_to_ths[bk_name] = bk_name
        else:
            # 子串匹配
            matched = [t for t in ths_names if bk_name in t or t in bk_name]
            if matched:
                # 选最长的
                bk_to_ths[bk_name] = max(matched, key=len)
    
    # 统计
    exact_match = sum(1 for n in bk_list if n[1] in ths_names)
    fuzzy_match = len(bk_to_ths) - exact_match
    unmatched_bk = [n for n in bk_list if n[1] not in bk_to_ths]
    unmatched_ths = ths_names - set(bk_to_ths.values())
    
    print(f"\n🎯 行业名匹配:")
    print(f"  精确匹配: {exact_match}")
    print(f"  模糊匹配: {fuzzy_match}")
    print(f"  总匹配: {len(bk_to_ths)}")
    print(f"  未匹配东方财富: {len(unmatched_bk)}")
    if unmatched_bk: print(f"    例: {unmatched_bk[:5]}")
    print(f"  未匹配同花顺: {len(unmatched_ths)}")
    if unmatched_ths: print(f"    例: {sorted(unmatched_ths)[:5]}")
    
    # 4. 对匹配上的行业，拉成分股
    print(f"\n📡 拉成分股 ({len(bk_to_ths)}个行业)...")
    import psycopg2.extras as pge
    
    conn = df._get_pg()
    cur = conn.cursor()
    
    # 重建表（加industry_code字段）
    cur.execute("DROP TABLE IF EXISTS industry_components_ths")
    cur.execute("""
        CREATE TABLE industry_components_ths (
            industry_code VARCHAR(20) NOT NULL,
            industry_name VARCHAR(50) NOT NULL,
            stock_code VARCHAR(10) NOT NULL,
            stock_name VARCHAR(50),
            trade_date DATE NOT NULL,
            PRIMARY KEY (industry_code, stock_code)
        )
    """)
    conn.commit()
    
    total = 0
    success = 0
    
    for bk_code, bk_name in bk_list:
        ths_name = bk_to_ths.get(bk_name)
        if not ths_name:
            continue
        
        cons = fetch_cons(bk_code)
        if not cons:
            continue
        
        rows = []
        for sc, sn in cons:
            sc_padded = str(sc).zfill(6)
            rows.append((bk_code, ths_name, sc_padded, sn, today_str))
        
        if rows:
            pge.execute_values(cur,
                "INSERT INTO industry_components_ths (industry_code, industry_name, stock_code, stock_name, trade_date) "
                "VALUES %s ON CONFLICT (industry_code, stock_code) DO UPDATE SET stock_name=EXCLUDED.stock_name",
                rows, page_size=500
            )
            conn.commit()
            total += len(rows)
            success += 1
        
        if (success) % 10 == 0:
            print(f"  进度 {success}/{len(bk_to_ths)} 行业 ({total}条)")
        
        time.sleep(0.3)
    
    cur.close()
    conn.close()
    
    print(f"\n✅ 共写入 {total} 条, {success}/{len(bk_to_ths)} 个行业")
    
    # 统计
    r,_ = df._pg_execute("SELECT industry_name, COUNT(*) FROM industry_components_ths GROUP BY industry_name ORDER BY industry_name")
    for rr in r:
        print(f"  {rr[0]}: {rr[1]}只")

if __name__ == '__main__':
    main()
