"""
V6 分段回测 v2：全拉到 Python 内存算 T+20
2020-01 ~ 2026-04
"""
import psycopg2, json, numpy as np, sys, os, time
from datetime import date, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.v6_multi_factor import V6MultiFactor, _INDUSTRY_CACHE

conn = psycopg2.connect(host='192.168.3.14', dbname='guludan', user='guludan', password='toto')
cur = conn.cursor()
mf = V6MultiFactor(conn)

# 1. 大盘 phase
print("加载大盘相空间...")
t0 = time.time()
cur.execute("SELECT date, phase FROM pool_energy WHERE level='market' AND date >= '2020-01-01' ORDER BY date")
phase_map = {r[0]: r[1] or 'NEUTRAL' for r in cur.fetchall()}
print(f"  {len(phase_map)} 天, {time.time()-t0:.1f}s")

def get_phase(d):
    return phase_map.get(d, 'NEUTRAL')

# 2. 加载信号
print("加载 S1/S2/S3 信号...")
t0 = time.time()
cur.execute("""
    SELECT e.level, e.date, e.extra
    FROM pool_energy e
    WHERE e.level LIKE 'code:A.%%'
      AND e.date >= '2020-01-01' AND e.date <= '2026-04-22'
      AND (e.extra->>'sigs' LIKE '%s1%' OR e.extra->>'sigs' LIKE '%s2%' OR e.extra->>'sigs' LIKE '%s3%')
    ORDER BY e.date, e.level
""")
raw = cur.fetchall()
all_signals = []
for r in raw:
    extra = json.loads(r[2]) if isinstance(r[2], str) else r[2]
    all_signals.append({'level': r[0], 'date': r[1], 'extra': extra})
print(f"  {len(all_signals)} 条, {time.time()-t0:.1f}s")

s1 = [s for s in all_signals if 's1' in s['extra'].get('sigs', '')]
s2 = [s for s in all_signals if 's2' in s['extra'].get('sigs', '')]
s3 = [s for s in all_signals if 's3' in s['extra'].get('sigs', '')]
print(f"  S1: {len(s1)}, S2: {len(s2)}, S3: {len(s3)}")

# 3. 行业映射
ind_map = mf.fetch_industry_map()

# 4. 大盘 phase 缓存
all_dates = sorted(set(s['date'] for s in all_signals))
mf.preload_market_phases(all_dates)

# 5. 行业缓存
_INDUSTRY_CACHE.clear()
mf.precalc_all_industry_excess(all_signals, ind_map)

# === 6. 全拉到 Python 算 T+20 ===
print("\n加载日线数据到内存...")
t0 = time.time()
all_codes = sorted(set(s['level'][7:] for s in all_signals))
all_syms = ['A.' + c for c in all_codes]
sym_str = "','".join(all_syms)
cur.execute(f"""
    SELECT symbol, trade_date, close
    FROM daily_prices
    WHERE symbol IN ('{sym_str}')
      AND trade_date BETWEEN '2020-01-01' AND '2026-05-25'
      AND close > 0
    ORDER BY symbol, trade_date
""")
rows = cur.fetchall()
print(f"  日线: {len(rows)} 行 ({len(all_codes)} 只, {time.time()-t0:.0f}s)")

# 索引
price_idx = defaultdict(list)
for sym, dt, close in rows:
    price_idx[sym].append((dt, close))

# 算 T+20
print("  Python 端查 T+20...", end="", flush=True)
t0 = time.time()
ret_20_cache = {}
skipped = 0
for sig in all_signals:
    code = sig['level'][7:]
    sym = 'A.' + code
    dt = sig['date']
    key = (sym, dt)
    if key in ret_20_cache:
        continue
    
    series = price_idx.get(sym, [])
    if not series:
        skipped += 1
        continue
    
    # 二分找 dt
    import bisect
    dates = [d for d, _ in series]
    i = bisect.bisect_left(dates, dt)
    if i >= len(dates) or dates[i] != dt:
        skipped += 1
        continue
    
    c0 = series[i][1]
    if c0 <= 0:
        skipped += 1
        continue
    
    if i + 20 < len(series):
        c20 = series[i + 20][1]
        if c20 > 0:
            ret_20_cache[key] = c20 / c0 - 1
        else:
            skipped += 1
    else:
        skipped += 1

print(f" {len(ret_20_cache)} 条, 跳过 {skipped} ({time.time()-t0:.1f}s)")

# === 7. 分段回测 ===
PHASES = ['B_ACCUM', 'B_DISTRIB', 'NEUTRAL', 'A_RECOVER', 'A_ACCUM', 'HI_EQ']

def run_backtest(signals, classify_fn, label):
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    
    phase_data = defaultdict(list)
    total_skipped = 0
    
    for sig in signals:
        code = sig['level'][7:]
        sym = 'A.' + code
        dt = sig['date']
        extra = sig['extra']
        
        ret_20 = ret_20_cache.get((sym, dt))
        if ret_20 is None:
            total_skipped += 1
            continue
        
        cls = classify_fn(code, dt, extra, ind_map)
        phase_data[get_phase(dt)].append({
            'code': code, 'date': dt, 'ret_20': ret_20,
            'type': cls['type'], 'score': cls['score'],
        })
    
    if total_skipped:
        print(f"  跳过(无T+20): {total_skipped}")
    
    for ph in PHASES:
        grp = phase_data.get(ph, [])
        if not grp:
            continue
        rets = np.array([r['ret_20'] for r in grp])
        print(f"\n  {ph:<12} (n={len(grp):>7d}): 基线={np.mean(rets):+7.2%}  胜率={np.mean(rets>0):.0%}")
        
        types = sorted(set(r['type'] for r in grp))
        for typ in types:
            tg = [r for r in grp if r['type'] == typ]
            if len(tg) < 5:
                continue
            tr = np.array([r['ret_20'] for r in tg])
            print(f"    {typ}级 (n={len(tg):>7d}): {np.mean(tr):+7.2%}  胜率={np.mean(tr>0):.0%}")
        
        buys = [r for r in grp if r['score'] >= 0.6]
        passes = [r for r in grp if r['score'] < 0.6]
        if buys:
            br = np.array([r['ret_20'] for r in buys])
            print(f"    买入(score>=0.6, n={len(buys):>5d}): {np.mean(br):+7.2%}  胜率={np.mean(br>0):.0%}")
        if passes:
            pr = np.array([r['ret_20'] for r in passes])
            print(f"    排除(score<0.6, n={len(passes):>5d}): {np.mean(pr):+7.2%}  胜率={np.mean(pr>0):.0%}")


run_backtest(s1, mf.classify_s1, "S1 分段回测 (信息泄露 vs 噪声)")
sys.stdout.flush()
run_backtest(s2, mf.classify_s2, "S2 分段回测 (相空间方向判定)")
sys.stdout.flush()
run_backtest(s3, mf.classify, "S3 分段回测 (post_vol + 信源分类)")
sys.stdout.flush()

conn.close()
