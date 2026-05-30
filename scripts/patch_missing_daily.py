#!/usr/bin/env python3
"""
补拉缺失日线（腾讯fqkline，单线程无sleep，只拉缺失日期）
用法: python3 patch_missing_daily.py 2026-05-19 2026-05-20
"""
import sys, time, requests, os
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scripts'))
import data_finance as df

INSERT_SQL = """INSERT INTO daily_prices
    (symbol, trade_date, open, high, low, close, volume)
    VALUES %s
    ON CONFLICT (symbol, trade_date) DO UPDATE SET
        open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
        close=EXCLUDED.close, volume=EXCLUDED.volume, updated_at=NOW()"""

def fetch_one(code, ds):
    """腾讯fqkline获取单只单日数据（前复权）"""
    bulk = f"sh{code}" if code.startswith('6') or code.startswith('9') else f"sz{code}"
    try:
        r = requests.get(
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={bulk},day,{ds},{ds},640,qfq",
            timeout=6, headers={"User-Agent": "Mozilla/5.0"}
        )
        data = r.json()
        if data.get('code') != 0: return None
        inner = data.get('data', {}).get(bulk, {})
        raw = inner.get('day') or inner.get('qfqday')
        if raw and raw[0] and raw[0][1]:
            return (code, ds, float(raw[0][1]), float(raw[0][3]), float(raw[0][4]), float(raw[0][2]))
    except:
        pass
    return None


rows,_ = df._pg_execute("SELECT code FROM stock_list WHERE active=true ORDER BY code")
codes = [r[0] for r in rows]

target_dates = sys.argv[1:] if len(sys.argv) > 1 else []
if not target_dates:
    # 自动检测：找出交易量严重不足的最近日期
    r2,_ = df._pg_execute("""
        SELECT trade_date FROM daily_prices 
        WHERE trade_date >= (SELECT MAX(trade_date) FROM daily_prices) - 7
        GROUP BY trade_date
        HAVING COUNT(*) < 5000
        ORDER BY trade_date
    """)
    target_dates = [r[0].strftime('%Y-%m-%d') for r in r2]
    # 也检查有0条的交易日（但daily_prices里没记录）
    print(f"自动检测缺失: {target_dates}")

if not target_dates:
    print("没有缺失日期")
    sys.exit(0)

print(f"📡 补拉 {target_dates}")
print(f"   股票: {len(codes)}只")

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

for ds in target_dates:
    # 跳过周日（非交易日）
    t0 = time.time()
    all_rows = []
    done = 0
    
    for code in codes:
        bulk = f"sh{code}" if code.startswith('6') or code.startswith('9') else f"sz{code}"
        try:
            r = session.get(
                f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={bulk},day,{ds},{ds},640,qfq",
                timeout=6
            )
            data = r.json()
            if data.get('code') == 0:
                inner = data.get('data', {}).get(bulk, {})
                raw = inner.get('day') or inner.get('qfqday')
                if raw and raw[0] and raw[0][1]:
                    row = raw[0]
                    all_rows.append((code, float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])))
        except:
            pass
        
        done += 1
        if done % 1000 == 0:
            got = (done / len(codes)) * (100 - done/(done or 1)) + 50
            print(f"  {ds} 进度 {done}/{len(codes)} ({len(all_rows)}只) {time.time()-t0:.0f}s", end='\r')
    
    print(f"\n  {ds}: 拉取 {len(all_rows)}只, {time.time()-t0:.1f}s")
    
    if all_rows:
        batch = [(r[0], ds, r[1], r[3], r[4], r[2], int(r[5])) for r in all_rows]
        for i in range(0, len(batch), 500):
            df._pg_executemany(INSERT_SQL, batch[i:i+500])
        
        r,_ = df._pg_execute('SELECT COUNT(*) FROM daily_prices WHERE trade_date=%s', (ds,))
        print(f"  ✅ {ds}: {r[0][0]}条")

# 更新大盘R指数
print(f"\n📥 更新大盘R指数...")
os.system(f'{sys.executable} scripts/inverse_push/r_index_v3.py --mode market')

print(f"\n✅ 完成")
