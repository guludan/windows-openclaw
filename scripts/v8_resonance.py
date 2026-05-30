"""V8 共振策略: 价格超跌(SMA120偏离) + 价值低估(PB偏离)

条件:
  Layer1 (价格超跌): SMA120偏离度 < -15%
  Layer2 (价值确认): PB偏离度 < -10% (净资产折价)

同时满足 → 买入统计 T+N 收益
"""
import psycopg2, numpy as np
from collections import defaultdict

conn = psycopg2.connect("host=192.168.3.14 dbname=guludan user=guludan password=toto", options="-c statement_timeout=0")
cur = conn.cursor()

# BVPS
cur.execute("SELECT symbol c6, end_date, bvps FROM financial_data WHERE bvps>0 AND end_date>='2023-01-01' ORDER BY c6, end_date")
all_bvps = {}
for c6, ed, bv in cur.fetchall():
    if c6 not in all_bvps: all_bvps[c6] = {}
    all_bvps[c6][ed] = float(bv)

codes_with_bvps = [c for c in all_bvps if len(all_bvps[c]) >= 4]
print(f"有BVPS: {len(codes_with_bvps)} 只")

# 拉日线 + 计算 SMA120 偏离 + PB 偏离, 检测共振
sample = codes_with_bvps[:500]  # 500只覆盖够了
print(f"样本: {len(sample)} 只")

results_sma = []      # 只SMA偏离
results_resonance = [] # 共振 (SMA+PB)
results_pb_only = []   # 只PB偏离

for si, c6 in enumerate(sample):
    if si % 100 == 0 and si > 0:
        print(f"  ...{si}/{len(sample)} SMA:{len(results_sma)} 共振:{len(results_resonance)}")
    if len(results_resonance) >= 1000:
        print("  共振达到1000条上限")
        break
    
    cur.execute("""
        SELECT trade_date, close FROM daily_prices 
        WHERE symbol='A.'||%s AND trade_date>='2024-01-01' AND close>0
        ORDER BY trade_date
    """, (c6,))
    rows = cur.fetchall()
    if len(rows) < 200:
        continue
    
    dates = [r[0] for r in rows]
    closes = np.array([float(r[1]) for r in rows])
    n = len(closes)
    
    # SMA120
    sma120 = np.zeros(n)
    for i in range(119, n):
        sma120[i] = np.mean(closes[i-119:i+1])
    sma_dev = closes / sma120 - 1
    
    # BVPS 映射
    stock_bvps = sorted(all_bvps[c6].items())
    bvps_day = np.full(n, np.nan)
    bi = 0
    for i, dt in enumerate(dates):
        while bi < len(stock_bvps) and stock_bvps[bi][0] <= dt:
            bvps_day[i] = stock_bvps[bi][1]
            bi += 1
        if bi > 0:
            bvps_day[i] = stock_bvps[bi-1][1]
    
    valid = np.where(~np.isnan(bvps_day))[0]
    if len(valid) < 130:
        continue
    
    for vi in range(120, len(valid)):
        i = valid[vi]
        
        # PB
        pb = closes[i] / bvps_day[i]
        if pb > 30 or pb < 0.3:
            continue
        
        # PB 120日中位
        hist_pb = []
        for j in valid[max(0, vi-120):vi-5]:
            pj = closes[j] / bvps_day[j]
            if 0.3 < pj < 30:
                hist_pb.append(pj)
        if len(hist_pb) < 30:
            continue
        pb_med = np.median(hist_pb)
        pb_dev = (pb - pb_med) / pb_med
        
        # SMA偏离
        sd = sma_dev[i]
        
        ret = closes[i] / closes[i-1] - 1
        
        # 仅SMA < -15%
        if sd < -0.15:
            f5 = closes[i+5]/closes[i]-1 if i+5 < n else None
            f10 = closes[i+10]/closes[i]-1 if i+10 < n else None
            f20 = closes[i+20]/closes[i]-1 if i+20 < n else None
            if f5 is not None:
                results_sma.append({
                    'c6': c6, 'date': dates[i],
                    'sd': sd, 'pb_dev': pb_dev, 'ret': ret,
                    'f5': f5, 'f10': f10, 'f20': f20
                })
        
        # 共振: SMA < -15% AND PB < -10%
        if sd < -0.15 and pb_dev < -0.10:
            f5 = closes[i+5]/closes[i]-1 if i+5 < n else None
            f10 = closes[i+10]/closes[i]-1 if i+10 < n else None
            f20 = closes[i+20]/closes[i]-1 if i+20 < n else None
            if f5 is not None:
                results_resonance.append({
                    'c6': c6, 'date': dates[i],
                    'sd': sd, 'pb_dev': pb_dev, 'pb': pb, 'pb_med': pb_med,
                    'ret': ret, 'f5': f5, 'f10': f10, 'f20': f20
                })
        
        # 仅PB < -15% (不加SMA条件)
        if pb_dev < -0.15:
            f5 = closes[i+5]/closes[i]-1 if i+5 < n else None
            if f5 is not None:
                results_pb_only.append({
                    'c6': c6, 'date': dates[i],
                    'sd': sd, 'pb_dev': pb_dev, 'ret': ret, 'f5': f5
                })

# 结果
print(f"\n{'='*65}")
print(f"  共振策略回测 (2024-2026)")
print(f"{'='*65}")

for tag, rl in [('仅SMA<-15%', results_sma), ('共振: SMA<-15%+PB<-10%', results_resonance), ('仅PB<-15%', results_pb_only)]:
    print(f"\n  {tag} (N={len(rl)}):")
    if len(rl) < 5:
        print("    样本不足")
        continue
    for label, key in [('T+5','f5'),('T+10','f10'),('T+20','f20')]:
        vals = [r[key] for r in rl if r[key] is not None]
        if not vals: continue
        rs = np.array(vals)
        ir = np.mean(rs)/max(np.std(rs),1e-6)*np.sqrt(252/5)
        print(f"    {label}: {len(vals):>4d}条  均值{np.mean(rs):+.2%}  中位{np.median(rs):+.2%}  胜率{np.mean(rs>0):.1%}  夏普{ir:.2f}")

# 共振策略的偏离度分层
if len(results_resonance) >= 50:
    print(f"\n{'='*65}")
    print(f"  共振策略: 按当日跌幅分层 (T+5)")
    print(f"{'='*65}")
    print(f"  {'当日跌幅':>12s} {'N':>5s} {'均值':>8s} {'中位':>8s} {'胜率':>6s}")
    for ret_thresh in [-0.03, -0.05, -0.07, -0.10]:
        sel = [r for r in results_resonance if r['ret'] < ret_thresh]
        if len(sel) < 5: continue
        rs = np.array([r['f5'] for r in sel])
        print(f"  {ret_thresh:>+.0%}以下 {len(rs):>5d} {np.mean(rs):>+7.2%} {np.median(rs):>+7.2%} {np.mean(rs>0):>5.1%}")

    # 按年
    print(f"\n  按年分布:")
    by_y = defaultdict(list)
    for r in results_resonance:
        by_y[r['date'].year].append(r['f5'])
    for y in sorted(by_y.keys()):
        rs = np.array(by_y[y])
        print(f"    {y}: {len(rs):>4d}条  均值{np.mean(rs):+.2%}  中位{np.median(rs):+.2%}  胜率{np.mean(rs>0):.1%}")

    # 系数加严: 更深偏离
    print(f"\n  更严格的共振条件 T+5:")
    for sma_t in [-0.18, -0.20, -0.25]:
        for pb_t in [-0.12, -0.15, -0.20]:
            sel = [r for r in results_resonance if r['sd'] < sma_t and r['pb_dev'] < pb_t]
            if len(sel) < 5: continue
            rs = np.array([r['f5'] for r in sel])
            print(f"    SMA<{sma_t:.0%}+PB<{pb_t:.0%}: {len(rs):>4d}条  均值{np.mean(rs):+.2%}  胜率{np.mean(rs>0):.1%}")

conn.close()
print(f"\n✅ 完成")
