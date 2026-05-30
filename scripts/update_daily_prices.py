#!/usr/bin/env python3
"""
日线增量更新（腾讯批量接口，~35秒全量）
用法：python3 update_daily_prices.py
"""
import sys, time, requests
from datetime import date

sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import data_finance as df

INSERT_SQL = """INSERT INTO daily_prices
    (symbol, trade_date, open, high, low, close, volume, amount)
    VALUES %s
    ON CONFLICT (symbol, trade_date) DO UPDATE SET
        open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
        close=EXCLUDED.close, volume=EXCLUDED.volume,
        amount=EXCLUDED.amount, updated_at=NOW()"""

def fetch_quotes(codes, batch_size=45):
    """腾讯批量行情"""
    all_data = {}
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        bulk = ','.join(f"sh{c}" if c.startswith('6') or c.startswith('9') else f"sz{c}" for c in batch)
        try:
            r = requests.get(f"https://qt.gtimg.cn/q={bulk}", timeout=8,
                           headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                for line in r.text.strip().split('\n'):
                    parts = line.split('~')
                    if len(parts) < 40: continue
                    raw = parts[0]
                    for pfx in ['v_','sh','sz','SH','SZ']: raw = raw.replace(pfx, '')
                    raw = raw.split('=')[0] if '=' in raw else raw
                    c = raw.strip()[:6]
                    all_data[c] = (float(parts[5]) if parts[5] else 0,  # open
                                   float(parts[3]) if parts[3] else 0,  # close
                                   float(parts[33]) if len(parts)>33 and parts[33] else 0,  # high
                                   float(parts[34]) if len(parts)>34 and parts[34] else 0,  # low
                                   float(parts[6]) if parts[6] else 0,  # volume(手)
                                   float(parts[37]) if len(parts)>37 and parts[37] else 0)  # amount(元)
        except:
            pass
    return all_data


def update_today():
    """更新今日日线"""
    rows,_ = df._pg_execute("SELECT code FROM stock_list WHERE active=true ORDER BY code")
    codes = [r[0] for r in rows]
    
    ds = date.today().strftime('%Y-%m-%d')
    print(f"📡 {ds} ({len(codes)}只)")
    
    t0 = time.time()
    data = fetch_quotes(codes)
    msgs = []
    
    rows_batch = [(c, ds, d[0], d[2], d[3], d[1], int(d[4]), d[5]) for c, d in data.items() if d[1] > 0]
    
    for i in range(0, len(rows_batch), 500):
        df._pg_executemany(INSERT_SQL, rows_batch[i:i+500])
    
    elapsed = time.time() - t0
    print(f"  ✅ {len(rows_batch)}条 ({elapsed:.0f}s)")
    return len(rows_batch)


if __name__ == '__main__':
    update_today()
