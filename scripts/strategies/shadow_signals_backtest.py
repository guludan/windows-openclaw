#!/usr/bin/env python3
"""上下影线组合信号回测 — 搓揉线/双针探底/长下影洗盘"""
import psycopg2, numpy as np
from datetime import date; from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
conn = psycopg2.connect(DB); cur = conn.cursor()
TODAY = date.today()

cur.execute("SELECT DISTINCT symbol FROM daily_prices WHERE close>0 AND trade_date >= '2022-01-01'")
symbols = [r[0] for r in cur.fetchall()]
import random; random.seed(42)
sample = random.symbols(symbols, min(500, len(symbols)))
# oops typo, just use sample direct

# fix: hardcode first 500
sample = symbols[:500]
print(f"样本: {len(sample)}只", flush=True)

signals = defaultdict(list)

for idx, sym in enumerate(sample):
    if idx % 50 == 0: print(f"  {idx}", flush=True)
    code = sym.split('.')[-1]
    cur.execute("SELECT trade_date,open,high,low,close FROM daily_prices WHERE symbol=%s AND close>0 ORDER BY trade_date", (sym,))
    rows = cur.fetchall()
    if len(rows) < 60: continue
    
    n = len(rows)
    ds = [r[0] for r in rows]
    oo = np.array([float(r[1]) if r[1] else 0 for r in rows], dtype=float)
    hh = np.array([float(r[2]) if r[2] else 0 for r in rows], dtype=float)
    ll = np.array([float(r[3]) if r[3] else 0 for r in rows], dtype=float)
    cc = np.array([float(r[4]) for r in rows], dtype=float)
    
    for i in range(2, n):
        if oo[i] == 0 or oo[i-1] == 0: continue
        
        def shadow(o, h, l, c):
            rng = h - l
            if rng == 0: return (0, 0)
            return ((h - max(o, c)) / rng, (min(o, c) - l) / rng)
        
        u1, lw1 = shadow(oo[i-1], hh[i-1], ll[i-1], cc[i-1])
        u2, lw2 = shadow(oo[i], hh[i], ll[i], cc[i])
        chg1 = (cc[i-1] / cc[i-2] - 1) * 100
        chg2 = (cc[i] / cc[i-1] - 1) * 100
        
        sig = None
        if u1 >= 0.5 and lw1 <= 0.3 and lw2 >= 0.5 and u2 <= 0.3 and abs(chg1)<3 and abs(chg2)<3:
            sig = '搓揉线'
        elif lw1 >= 0.5 and lw2 >= 0.5 and abs(ll[i-1]-ll[i])/max(ll[i-1],ll[i],0.01) < 0.03:
            sig = '双针探底'
        elif chg2 <= -2 and lw2 >= 0.5:
            sig = '长下影洗盘'
        
        if not sig: continue
        fut = cc[i+1:i+21]
        if len(fut) < 3: continue
        e = {'code': code, 'type': sig, 'date': str(ds[i]), 'close': round(float(cc[i]), 2), 'chg': round(float(chg2), 2)}
        for j in [(3,3),(5,5),(10,10),(20,20)]:
            if len(fut) >= j[1]:
                e[f'T+{j[1]}'] = round(float((fut[j[1]-1]/cc[i]-1)*100), 2)
        signals[sig].append(e)

total = sum(len(v) for v in signals.values())
print(f"\n总信号: {total}", flush=True)
if total < 10: print("太少"); conn.close(); exit()

for t, arr in sorted(signals.items()):
    print(f"\n{t}: {len(arr)}次")
    print(f"{'窗':>6} {'均值':>8} {'中位':>8} {'胜率':>6}")
    for h in [3, 5, 10, 20]:
        vals = [s.get(f'T+{h}') for s in arr if f'T+{h}' in s]
        if not vals: continue
        a = np.array(vals)
        print(f"T+{h}: {np.mean(a):>+7.2f}% {np.median(a):>+7.2f}% {np.mean(a>0)*100:>4.0f}%")

conn.close()
