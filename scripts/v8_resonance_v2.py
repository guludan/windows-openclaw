"""V8 共振回测 v2: 深度分层 + 按熊市拆分
补全仅PB分支 和 深度共振分层
"""
import psycopg2, numpy as np
from collections import defaultdict
import pickle, os

cache_path = './.v8_cache.pkl'

if os.path.exists(cache_path):
    print("加载缓存...")
    with open(cache_path, 'rb') as f:
        cache = pickle.load(f)
    results_sma = cache['sma']
    results_resonance = cache['resonance']
    results_sma_deep = cache['sma_deep']
    results_resonance_deep = cache['resonance_deep']
else:
    conn = psycopg2.connect("host=192.168.3.14 dbname=guludan user=guludan password=toto", options="-c statement_timeout=0")
    cur = conn.cursor()
    
    cur.execute("SELECT symbol c6, end_date, bvps FROM financial_data WHERE bvps>0 AND end_date>='2023-01-01' ORDER BY c6, end_date")
    all_bvps = {}
    for c6, ed, bv in cur.fetchall():
        if c6 not in all_bvps: all_bvps[c6] = {}
        all_bvps[c6][ed] = float(bv)
    codes_with_bvps = [c for c in all_bvps if len(all_bvps[c]) >= 4]
    print(f"有BVPS: {len(codes_with_bvps)} 只")
    
    sample = codes_with_bvps[:500]
    print(f"样本: {len(sample)} 只")
    
    results_sma = []
    results_resonance = []
    results_sma_deep = []    # SMA<-30%
    results_resonance_deep = []  # SMA<-25% + PB<-15%
    
    for si, c6 in enumerate(sample):
        if si % 100 == 0 and si > 0:
            print(f"  ...{si}/{len(sample)} 共振深度:{len(results_resonance_deep)}")
        if len(results_resonance_deep) >= 800:
            print("  深度共振达到800上限")
            break
        
        cur.execute("""
            SELECT trade_date, close FROM daily_prices 
            WHERE symbol='A.'||%s AND trade_date>='2024-01-01' AND close>0
            ORDER BY trade_date
        """, (c6,))
        rows = cur.fetchall()
        if len(rows) < 200: continue
        
        dates = [r[0] for r in rows]
        closes = np.array([float(r[1]) for r in rows])
        n = len(closes)
        
        sma120 = np.array([np.mean(closes[max(0,i-119):i+1]) for i in range(n)])
        # fix warn
        sma120[:119] = np.nan
        sma_dev = closes / sma120 - 1
        
        stock_bvps = sorted(all_bvps[c6].items())
        bvps_day = np.full(n, np.nan)
        bi = 0
        for i, dt in enumerate(dates):
            while bi < len(stock_bvps) and stock_bvps[bi][0] <= dt:
                bvps_day[i] = stock_bvps[bi][1]
                bi += 1
            if bi > 0: bvps_day[i] = stock_bvps[bi-1][1]
        
        valid = np.where(~np.isnan(bvps_day))[0]
        if len(valid) < 130: continue
        
        for vi in range(120, len(valid)):
            i = valid[vi]
            if np.isnan(sma_dev[i]): continue
            sd = sma_dev[i]
            
            pb = closes[i] / bvps_day[i]
            if pb > 30 or pb < 0.3: continue
            
            hist_pb = []
            for j in valid[max(0, vi-120):vi-5]:
                pj = closes[j] / bvps_day[j]
                if 0.3 < pj < 30: hist_pb.append(pj)
            if len(hist_pb) < 30: continue
            pb_dev = (pb - np.median(hist_pb)) / np.median(hist_pb)
            
            ret = closes[i] / closes[i-1] - 1
            
            # Collect
            f5 = closes[i+5]/closes[i]-1 if i+5 < n else None
            f10 = closes[i+10]/closes[i]-1 if i+10 < n else None
            f20 = closes[i+20]/closes[i]-1 if i+20 < n else None
            if f5 is None: continue
            
            entry = {'c6':c6,'date':dates[i],'sd':sd,'pb_dev':pb_dev,'ret':ret,'f5':f5,'f10':f10,'f20':f20}
            
            if sd < -0.15:
                results_sma.append(entry)
            if sd < -0.30:
                results_sma_deep.append(entry)
            if sd < -0.15 and pb_dev < -0.10:
                results_resonance.append(entry)
            if sd < -0.25 and pb_dev < -0.15:
                results_resonance_deep.append(entry)
    
    # 缓存
    cache = {'sma': results_sma, 'resonance': results_resonance,
             'sma_deep': results_sma_deep, 'resonance_deep': results_resonance_deep}
    with open(cache_path, 'wb') as f: pickle.dump(cache, f)
    conn.close()

# 输出
def print_stats(label, rl):
    if len(rl) < 3: 
        print(f"\n  {label}: 样本不足 ({len(rl)})")
        return
    print(f"\n  {label} (N={len(rl)}):")
    for lk, ky in [('T+5','f5'),('T+10','f10'),('T+20','f20')]:
        vals = [r[ky] for r in rl if r[ky] is not None]
        if not vals: continue
        rs = np.array(vals)
        ir = np.mean(rs)/max(np.std(rs),1e-6)*np.sqrt(252/5)
        print(f"    {lk}: {len(vals):>4d}条  均值{np.mean(rs):+.2%}  中位{np.median(rs):+.2%}  胜率{np.mean(rs>0):.1%}  夏普{ir:.2f}")

print(f"\n{'='*70}")
print(f"  V8 共振策略 — 深度分层对比")
print(f"{'='*70}")
for tag, rl in [('仅SMA<-15%', results_sma), ('仅SMA<-30%', results_sma_deep),
                ('共振 SMA<-15%+PB<-10%', results_resonance),
                ('深度共振 SMA<-25%+PB<-15%', results_resonance_deep)]:
    print_stats(tag, rl)

# 共振按年
if len(results_resonance) >= 50:
    print(f"\n{'='*70}")
    print(f"  共振策略(SMA<-15%+PB<-10%) 按年:")
    print(f"{'='*70}")
    by_y = defaultdict(list)
    for r in results_resonance:
        by_y[r['date'].year].append(r['f5'])
    for y in sorted(by_y.keys()):
        rs = np.array(by_y[y])
        print(f"  {y}: {len(rs):>4d}条  均值{np.mean(rs):+.2%}  中位{np.median(rs):+.2%}  胜率{np.mean(rs>0):.1%}")

if len(results_resonance_deep) >= 50:
    print(f"\n  深度共振(SMA<-25%+PB<-15%) 按年:")
    by_y = defaultdict(list)
    for r in results_resonance_deep:
        by_y[r['date'].year].append(r['f5'])
    for y in sorted(by_y.keys()):
        rs = np.array(by_y[y])
        print(f"  {y}: {len(rs):>4d}条  均值{np.mean(rs):+.2%}  中位{np.median(rs):+.2%}  胜率{np.mean(rs>0):.1%}")

# 共振内按当日跌幅分层
if len(results_resonance) >= 50:
    print(f"\n{'='*70}")
    print(f"  共振策略: 当日跌幅分层 (T+5)")
    print(f"{'='*70}")
    for rt in [-0.03, -0.05, -0.07]:
        sel = [r for r in results_resonance if r['ret'] < rt]
        if len(sel) < 5: continue
        rs = np.array([r['f5'] for r in sel])
        print(f"  日跌<{rt:.0%}: {len(rs):>4d}条  均值{np.mean(rs):+.2%}  胜率{np.mean(rs>0):.1%}")

# 联合更严条件
print(f"\n{'='*70}")
print(f"  更严共振条件 (T+5):")
print(f"{'='*70}")
pool = results_resonance
for sma_t in [-0.18, -0.20, -0.25]:
    for pb_t in [-0.12, -0.15, -0.18]:
        for rt_t in [None, -0.03, -0.05]:
            sel = [r for r in pool if r['sd'] < sma_t and r['pb_dev'] < pb_t]
            if rt_t: sel = [r for r in sel if r['ret'] < rt_t]
            if len(sel) < 5: continue
            rs = np.array([r['f5'] for r in sel])
            tag = f"SMA<{sma_t:.0%}+PB<{pb_t:.0%}"
            if rt_t: tag += f"+日跌<{rt_t:.0%}"
            print(f"  {tag:>30s}: {len(rs):>4d}条  均值{np.mean(rs):+.2%}  胜率{np.mean(rs>0):.1%}")

print(f"\n✅")
