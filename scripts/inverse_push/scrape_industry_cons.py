#!/usr/bin/env python3
"""
东方财富行业成分股采集 v6 — Playwright fetch API
利用Playwright浏览器的网络栈（不受WSL防火墙限制）直连东方财富API
"""
import os, sys, json, time, re, asyncio
from datetime import date

sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
import data_finance as df

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("❌ 安装: pip install playwright && playwright install chromium")
    sys.exit(1)

API_CONFIG = {
    'ut': '8dec03ba335b81bf4ebdf7b29ec27d15',
}


def collect_all_bk(page):
    """用Playwright在浏览器里发fetch获取行业BK代码列表"""
    result = page.evaluate("""
        async () => {
            const params = new URLSearchParams({
                pn: '1', pz: '500', po: '1', np: '1',
                ut: '8dec03ba335b81bf4ebdf7b29ec27d15',
                fltt: '2', invt: '2', fid: 'f3',
                fs: 'm:90+t:2',
                fields: 'f12,f14'
            });
            const url = 'https://push2.eastmoney.com/api/qt/clist/get?' + params.toString();
            const resp = await fetch(url, {
                headers: {
                    'User-Agent': 'Mozilla/5.0',
                    'Referer': 'https://data.eastmoney.com/'
                }
            });
            const data = await resp.json();
            const items = data.data.diff || [];
            return items.map(item => [item.f12, item.f14]);
        }
    """)
    return result


def fetch_cons(page, bk_code):
    """用Playwright浏览器发fetch获取行业成分股"""
    result = page.evaluate(f"""
        async () => {{
            const params = new URLSearchParams({{
                pn: '1', pz: '500', po: '1', np: '1',
                ut: '8dec03ba335b81bf4ebdf7b29ec27d15',
                fltt: '2', invt: '2', fid: 'f62',
                fs: 'b:{bk_code}',
                fields: 'f12,f14'
            }});
            const url = 'https://push2.eastmoney.com/api/qt/clist/get?' + params.toString();
            const resp = await fetch(url, {{
                headers: {{'Referer': 'https://data.eastmoney.com/bkzj/{bk_code}.html'}}
            }});
            const data = await resp.json();
            const items = data.data.diff || [];
            return items.map(item => [item.f12 || '', item.f14 || '']);
        }}
    """)
    return [(code.zfill(6), name) for code, name in result]


def main():
    print(f"{'='*60}")
    print(f"📡 东方财富行业成分股采集 v6（Playwright fetch）")
    print(f"{'='*60}\n")
    
    # 同花顺行业映射
    import akshare as ak
    ths_df = ak.stock_board_industry_name_ths()
    ths_names = set(ths_df['name'].tolist())
    print(f"📡 同花顺行业: {len(ths_names)}个\n")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        page = ctx.new_page()
        page.goto("https://data.eastmoney.com/", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        
        # ━━ Step 1: BK行业列表 ━━
        print("📡 获取东方财富行业列表...")
        bk_items = collect_all_bk(page)
        print(f"  ✅ {len(bk_items)}个行业")
        
        if not bk_items:
            print("❌ 无法获取行业列表")
            browser.close()
            return
        
        for bk, name in bk_items[:5]:
            print(f"    {bk} {name}")
        
        # 名匹配
        bk_to_ths = {}
        for bk, bn in bk_items:
            if bn in ths_names:
                bk_to_ths[bk] = bn
            else:
                matched = [t for t in ths_names if bn in t or t in bn]
                bk_to_ths[bk] = max(matched, key=len) if matched else bn
        
        matched_count = sum(1 for v in bk_to_ths.values() if v in ths_names)
        print(f"  🎯 同花顺匹配: {matched_count}/{len(bk_items)}\n")
        
        # ━━ Step 2: 拉成分股 ━━
        print(f"📡 拉成分股 ({len(bk_items)}个)...")
        all_records = []
        success = 0
        failed = []
        
        for idx, (bk_code, bk_name) in enumerate(bk_items):
            industry_name = bk_to_ths.get(bk_code, bk_name)
            
            try:
                stocks = fetch_cons(page, bk_code)
                
                if stocks:
                    all_records.append((bk_code, industry_name, stocks))
                    success += 1
                    flag = 'M' if industry_name in ths_names else ' '
                    print(f"  [{idx+1}/{len(bk_items)}] {flag} {industry_name:14s} ({bk_code}): {len(stocks):3d}只 ✅")
                else:
                    failed.append(bk_code)
                    print(f"  [{idx+1}/{len(bk_items)}] {industry_name}: 0只 ❌")
            
            except Exception as e:
                failed.append(bk_code)
                print(f"  [{idx+1}/{len(bk_items)}] {industry_name}: {str(e)[:60]} ❌")
            
            # 每30个换页面防内存泄漏
            if idx > 0 and idx % 30 == 0:
                page.close()
                page = ctx.new_page()
                page.goto("https://data.eastmoney.com/", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(1000)
            
            time.sleep(0.5)
        
        page.close()
        browser.close()
    
    print(f"\n📊 成功{success}/{len(bk_items)}, 共{sum(len(r[2]) for r in all_records)}条")
    if failed:
        print(f"  失败({len(failed)}): {failed[:10]}{'...' if len(failed)>10 else ''}")
    
    if not all_records:
        print("❌ 无数据")
        return
    
    # ━━ Step 3: 写入PG ━━
    print("\n💾 写入PG...")
    import psycopg2.extras as pge
    today_str = str(date.today())
    
    conn = df._get_pg()
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS industry_components_ths")
    cur.execute("""
        CREATE TABLE industry_components_ths (
            industry_code VARCHAR(10) NOT NULL,
            industry_name VARCHAR(50) NOT NULL,
            stock_code VARCHAR(10) NOT NULL,
            stock_name VARCHAR(50),
            trade_date DATE NOT NULL,
            PRIMARY KEY (industry_code, stock_code)
        )
    """)
    conn.commit()
    
    total = 0
    for bk_code, industry_name, stocks in all_records:
        rows = [(bk_code, industry_name, sc, sn, today_str) for sc, sn in stocks]
        pge.execute_values(cur,
            "INSERT INTO industry_components_ths (industry_code, industry_name, stock_code, stock_name, trade_date) VALUES %s ON CONFLICT (industry_code, stock_code) DO UPDATE SET stock_name=EXCLUDED.stock_name",
            rows, page_size=500
        )
        conn.commit()
        total += len(rows)
    
    cur.close()
    conn.close()
    print(f"✅ PG: {total}条, {len(all_records)}个行业")
    
    # CSV
    csv_path = f"/tmp/industry_components_ths_{today_str}.csv"
    with open(csv_path, 'w', encoding='utf-8-sig') as f:
        f.write("industry_code,industry_name,stock_code,stock_name,trade_date\n")
        for bk_code, industry_name, stocks in all_records:
            for sc, sn in stocks:
                f.write(f"{bk_code},{industry_name},{sc},{sn},{today_str}\n")
    print(f"✅ CSV: {csv_path}")
    
    # 匹配统计
    r,_ = df._pg_execute("SELECT DISTINCT industry_name FROM industry_components_ths ORDER BY industry_name")
    em_names = set(x[0] for x in r)
    r2,_ = df._pg_execute("SELECT DISTINCT industry FROM industry_daily ORDER BY industry")
    ths_db = set(x[0] for x in r2)
    matched = ths_db & em_names
    unmatched = ths_db - em_names
    print(f"\n🎯 同花顺匹配: {len(matched)}/{len(ths_db)}")
    if unmatched:
        print(f"  未匹配({len(unmatched)}): {sorted(unmatched)}")
    
    print(f"\n✅ 完成!")


if __name__ == '__main__':
    main()
