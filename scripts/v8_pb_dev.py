"""PB 偏离度均值回归验证
PB = Price / BVPS (每股净资产)
BVPS 季度更新，价格每日变化 → PB 每日偏离度
假设: PB 显著低于历史中位 → 均值回归
"""
import psycopg2, numpy as np
from collections import defaultdict

conn = psycopg2.connect("host=192.168.3.14 dbname=guludan user=guludan password=toto", options="-c statement_timeout=0")
cur = conn.cursor()

# 1) 拉 BVPS 数据 (2024+)
cur.execute("""
    SELECT symbol as c6, end_date, bvps
    FROM financial_data 
    WHERE bvps IS NOT NULL AND bvps > 0 AND end_date >= '2023-01-01'
    ORDER BY c6, end_date
""")
all_bvps = {}
for c6, ed, bvps in cur.fetchall():
    if c6 not in all_bvps: all_bvps[c6] = {}
    all_bvps[c6][ed] = float(bvps)

codes_with_bvps = [c for c in all_bvps if len(all_bvps[c]) >= 4]
print(f"有BVPS的股票: {len(codes_with_bvps)} 只")

# 2) 限300只
sample = codes_with_bvps[:300]
print(f"样本: {len(sample)} 只")

results = []

for si, c6 in enumerate(sample):
    if si % 50 == 0 and si > 0:
        print(f"  ...{si}/{len(sample)} 事件:{len(results)}")
    if len(results) >= 3000:
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
    
    stock_bvps = sorted(all_bvps[c6].items())  # [(end_date, bvps), ...]
    
    # 对每个交易日找当时最新的 BVPS
    bvps_per_day = np.full(n, np.nan)
    bvps_date_idx = 0
    
    for i, dt in enumerate(dates):
        # 找到最新的可用 BVPS (报告期 <= 交易日)
        while bvps_date_idx < len(stock_bvps) and stock_bvps[bvps_date_idx][0] <= dt:
            bvps_per_day[i] = stock_bvps[bvps_date_idx][1]
            bvps_date_idx += 1
        if bvps_date_idx > 0:
            # 用最近已知的bvps填后续(直到下一期报告)
            bvps_per_day[i] = stock_bvps[bvps_date_idx-1][1]
    
    valid = np.where(~np.isnan(bvps_per_day))[0]
    if len(valid) < 130:
        continue
    
    for idx_pos in range(120, len(valid)):
        i = valid[idx_pos]
        pb = closes[i] / bvps_per_day[i]
        
        if pb > 30 or pb < 0.3:  # 筛异常
            continue
        
        # 120日中位PB
        hist_pb = []
        for j in valid[max(0, idx_pos-120):idx_pos-5]:
            pj = closes[j] / bvps_per_day[j]
            if 0.3 < pj < 30:
                hist_pb.append(pj)
        if len(hist_pb) < 30:
            continue
        
        pb_med = np.median(hist_pb)
        pb_dev = (pb - pb_med) / pb_med  # 负 = 折价
        
        ret = closes[i] / closes[i-1] - 1
        
        # 条件: PB偏离度 < -10% (比PE放宽，PB更稳定)
        if pb_dev >= -0.10:
            continue
        
        f5 = closes[i+5]/closes[i]-1 if i+5 < n else None
        f10 = closes[i+10]/closes[i]-1 if i+10 < n else None
        f20 = closes[i+20]/closes[i]-1 if i+20 < n else None
        
        if f5 is not None:
            results.append({
                'c6': c6, 'date': dates[i],
                'pb': pb, 'pb_med': pb_med, 'pb_dev': pb_dev,
                'ret': ret,
                'f5': f5, 'f10': f10, 'f20': f20
            })

print(f"\nPB偏离<-10%事件: {len(results)}")

if len(results) < 20:
    print("事件不足")
    exit()

# 全量
for label, key in [('T+5','f5'),('T+10','f10'),('T+20','f20')]:
    vals = [r[key] for r in results if r[key] is not None]
    rs = np.array(vals)
    print(f"  {label} (N={len(vals)}): 均值{np.mean(rs):+.2%} 中位{np.median(rs):+.2%} 胜率{np.mean(rs>0):.1%} 夏普{np.mean(rs)/max(np.std(rs),1e-6)*np.sqrt(252/5):.2f}")

# PB偏离度分层
print(f"\nPB偏离度分层 T+5:")
for lo, hi, lb in [(-1.0,-0.35,'<-35%'), (-0.35,-0.25,'-35%~-25%'), (-0.25,-0.18,'-25%~-18%'), (-0.18,-0.10,'-18%~-10%')]:
    sel = [r for r in results if lo < r['pb_dev'] <= hi]
    if len(sel) < 3: continue
    rs = np.array([r['f5'] for r in sel])
    print(f"  {lb:>10s}: {len(rs):>4d}条 均值{np.mean(rs):+.2%} 中位{np.median(rs):+.2%} 胜率{np.mean(rs>0):.1%}")

# 按年
print(f"\n按年:")
by_y = defaultdict(list)
for r in results:
    by_y[r['date'].year].append(r['f5'])
for y in sorted(by_y.keys()):
    rs = np.array(by_y[y])
    print(f"  {y}: {len(rs):>4d}条 均值{np.mean(rs):+.2%} 中位{np.median(rs):+.2%} 胜率{np.mean(rs>0):.1%}")

# 加条件: 当日也大跌
print(f"\n联合条件: PB偏离 + 当日大跌:")
for dev_thresh in [-0.15, -0.20, -0.25]:
    for ret_thresh in [-0.03, -0.05, -0.07]:
        sel = [r for r in results if r['pb_dev'] < dev_thresh and r['ret'] < ret_thresh]
        if len(sel) < 5: continue
        rs = np.array([r['f5'] for r in sel])
        print(f"  PB<{dev_thresh:.0%}+日跌<{ret_thresh:.0%}: {len(rs):>4d}条 均值{np.mean(rs):+.2%} 胜率{np.mean(rs>0):.1%}")

conn.close()
