"""
V6 分段回测：按大盘相空间分段独立跑 S1/S2/S3
2020-2026 全量
"""
import psycopg2, json, numpy as np, sys, os, time
from datetime import date, datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.v6_multi_factor import V6MultiFactor, _INDUSTRY_CACHE

conn = psycopg2.connect(host='192.168.3.14', dbname='guludan', user='guludan', password='toto')
cur = conn.cursor()
mf = V6MultiFactor(conn)

# 1. 加载大盘 phase
print("加载大盘相空间...")
t0 = time.time()
cur.execute("""
    SELECT date, phase FROM pool_energy
    WHERE level = 'market' AND date >= '2020-01-01'
    ORDER BY date
""")
phase_map = {}  # date -> phase
for d, ph in cur.fetchall():
    phase_map[d] = ph or 'NEUTRAL'
print(f"  {len(phase_map)} 天, {time.time()-t0:.1f}s")

# phase 的 tag 方式：取最近 5 天的多数 phase
def get_phase_smoothed(d, days=5):
    """最近 days 天的多数 phase"""
    from collections import Counter
    # 先找这个日期前后
    candidates = []
    # 顺延最多10天向前
    dates_sorted = sorted(phase_map.keys())
    for candidate_d in dates_sorted:
        if candidate_d <= d and d - candidate_d <= timedelta(days=days*2):
            # 不在该月的一定范围内
            pass
    
    # 简单：直接取当天的 phase
    return phase_map.get(d, 'NEUTRAL')

from datetime import timedelta

def get_phase(d):
    return phase_map.get(d, 'NEUTRAL')

# 2. 加载信号
print("加载 S1/S2/S3 信号 (2020-01 ~ 2026-04)...")
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

# 按信号类型分
s1 = [s for s in all_signals if 's1' in s['extra'].get('sigs', '')]
s2 = [s for s in all_signals if 's2' in s['extra'].get('sigs', '')]
s3 = [s for s in all_signals if 's3' in s['extra'].get('sigs', '')]
print(f"  S1: {len(s1)}, S2: {len(s2)}, S3: {len(s3)}")

# 3. 行业映射（一次加载，永久缓存）
ind_map = mf.fetch_industry_map()

# 4. 预加载所有信号日期的大盘 phase
all_dates = sorted(set(s['date'] for s in all_signals))
mf.preload_market_phases(all_dates)

# 5. 按 phase 分组跑回测
PHASES = ['B_ACCUM', 'B_DISTRIB', 'NEUTRAL', 'A_RECOVER', 'A_ACCUM', 'HI_EQ']

def fast_ret_20(cur, sym, dt):
    """快速查 T+20 收益"""
    c = cur.execute("SELECT close FROM daily_prices WHERE symbol=%s AND trade_date=%s", (sym, dt))
    r0 = cur.fetchone()
    if not r0:
        return None
    cur.execute("""
        SELECT close FROM daily_prices
        WHERE symbol=%s AND trade_date > %s
        ORDER BY trade_date OFFSET 19 LIMIT 1
    """, (sym, dt))
    r1 = cur.fetchone()
    if not r1:
        return None
    return r1[0] / r0[0] - 1

def backtest_by_phase(signals, classify_fn, label):
    """按 phase 分段回测"""
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    
    # 按 phase 分组
    phase_signals = defaultdict(list)
    for sig in signals:
        ph = get_phase(sig['date'])
        phase_signals[ph].append(sig)
    
    # T+20 缓存
    ret_cache = {}
    def get_ret(sym, dt):
        key = (sym, dt)
        if key not in ret_cache:
            cur.execute("SELECT close FROM daily_prices WHERE symbol=%s AND trade_date=%s", (sym, dt))
            r0 = cur.fetchone()
            if not r0:
                ret_cache[key] = None
            else:
                cur.execute("""
                    SELECT close FROM daily_prices
                    WHERE symbol=%s AND trade_date > %s
                    ORDER BY trade_date OFFSET 19 LIMIT 1
                """, (sym, dt))
                r1 = cur.fetchone()
                ret_cache[key] = r1[0] / r0[0] - 1 if r1 else None
        return ret_cache[key]
    
    results = {}
    for ph in PHASES:
        sigs = phase_signals.get(ph, [])
        if len(sigs) < 10:
            continue
        
        ph_results = []
        for sig in sigs:
            code = sig['level'][7:]
            sym = 'A.' + code
            dt = sig['date']
            extra = sig['extra']
            
            ret_20 = get_ret(sym, dt)
            if ret_20 is None:
                continue
            
            cls = classify_fn(code, dt, extra, ind_map)
            ph_results.append({
                'code': code, 'date': dt,
                'ret_20': ret_20,
                'type': cls['type'],
                'score': cls['score'],
                'phase': get_phase(dt),
            })
        
        if ph_results:
            results[ph] = ph_results
    
    # 打印
    for ph in PHASES:
        if ph not in results:
            continue
        grp = results[ph]
        rets = np.array([r['ret_20'] for r in grp])
        print(f"\n  {ph:<12} (n={len(grp):>6d}): 基线={np.mean(rets):+7.2%}  胜率={np.mean(rets>0):.0%}")
        
        # 按 type 分层
        types = sorted(set(r['type'] for r in grp))
        for typ in types:
            tg = [r for r in grp if r['type'] == typ]
            if len(tg) < 5:
                continue
            tr = np.array([r['ret_20'] for r in tg])
            print(f"    {typ}级 (n={len(tg):>6d}): {np.mean(tr):+7.2%}  胜率={np.mean(tr>0):.0%}")
        
        # 买入/排除组
        buys = [r for r in grp if r['score'] >= 0.6]
        passes = [r for r in grp if r['score'] < 0.6]
        if buys:
            br = np.array([r['ret_20'] for r in buys])
            print(f"    买入(score>=0.6, n={len(buys):>4d}): {np.mean(br):+7.2%}  胜率={np.mean(br>0):.0%}")
        if passes:
            pr = np.array([r['ret_20'] for r in passes])
            print(f"    排除(score<0.6, n={len(passes):>4d}): {np.mean(pr):+7.2%}  胜率={np.mean(pr>0):.0%}")
    
    return results


# 预缓存行业（全量一次，不要每次清）
_INDUSTRY_CACHE.clear()

# S1
print("\n预加载行业缓存（S1/S2/S3 共享）...")
mf.precalc_all_industry_excess(all_signals, ind_map)
sys.stdout.flush()

s1_results = backtest_by_phase(s1, mf.classify_s1, "S1 分段回测 (信息泄露 vs 噪声)")
sys.stdout.flush()
s2_results = backtest_by_phase(s2, mf.classify_s2, "S2 分段回测 (相空间方向判定)")
sys.stdout.flush()
s3_results = backtest_by_phase(s3, mf.classify, "S3 分段回测 (post_vol + 信源分类)")
sys.stdout.flush()

conn.close()
