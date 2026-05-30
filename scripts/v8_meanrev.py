"""均值回归验证 v1: 股价偏离120日SMA
Proxy for "偏离基准" — 用价格结构代替PE
如果价格偏离SMA也能发现均值回归效应，
说明"基准回归"这个现象本身存在，值得用EPS算更精准的PE偏离度
"""
import psycopg2, numpy as np
from collections import defaultdict
from datetime import timedelta

conn = psycopg2.connect("host=192.168.3.14 dbname=guludan user=guludan password=toto", options="-c statement_timeout=0")
cur = conn.cursor()

# 拉一部分大中盘股票全量日线
# 取 2019年后有2500天以上的（上市早的）
cur.execute("""
    SELECT SUBSTRING(symbol,3) as c6, MIN(trade_date), MAX(trade_date), COUNT(*)
    FROM daily_prices 
    WHERE close>0 AND turnover>0 AND trade_date>='2015-01-01'
    GROUP BY symbol 
    HAVING COUNT(*) > 2000  -- 至少8年
    ORDER BY COUNT(*) DESC
    LIMIT 800  -- 800只有代表性
""")
stocks = [r[0] for r in cur.fetchall()]
print(f"股票: {len(stocks)} 只")

results = []

for si, c6 in enumerate(stocks):
    if si % 100 == 0 and si > 0:
        print(f"  ...{si}/{len(stocks)} 事件:{len(results)}")
    if len(results) > 2000:
        print("  已达2000条上限")
        break
    
    cur.execute("""
        SELECT trade_date, close FROM daily_prices 
        WHERE symbol='A.'||%s AND trade_date>='2015-01-01' AND close>0
        ORDER BY trade_date
    """, (c6,))
    rows = cur.fetchall()
    if len(rows) < 200:
        continue
    
    dates = [r[0] for r in rows]
    closes = np.array([float(r[1]) for r in rows])
    n = len(closes)
    
    # 120日SMA
    sma120 = np.zeros(n)
    for i in range(119, n):
        sma120[i] = np.mean(closes[i-119:i+1])
    
    for i in range(140, n):  # 需要至少120日SMA + 前20日格局
        # 偏离度: (close / sma120 - 1)
        dev = closes[i] / sma120[i] - 1
        
        # 当日涨跌
        ret = closes[i] / closes[i-1] - 1
        
        # 条件: 大幅低于SMA + 当日大跌 → 超卖
        if dev < -0.15 and ret < -0.05:
            # 未来收益
            f5 = closes[i+5] / closes[i] - 1 if i+5 < n else None
            f10 = closes[i+10] / closes[i] - 1 if i+10 < n else None
            f20 = closes[i+20] / closes[i] - 1 if i+20 < n else None
            
            if f5 is not None:
                results.append({
                    'c6': c6, 'date': dates[i],
                    'dev': dev, 'ret': ret,
                    'f5': f5, 'f10': f10, 'f20': f20,
                    'price': float(closes[i])
                })

print(f"\n偏离度<-15%+大跌>5%事件: {len(results)}")

if len(results) < 10:
    print("事件太少，检查条件")
    exit()

# 全量统计
rets5 = np.array([r['f5'] for r in results])
rets10 = np.array([r['f10'] for r in results if r['f10'] is not None])
rets20 = np.array([r['f20'] for r in results if r['f20'] is not None])

print(f"\n{'='*65}")
print(f"  价格偏离SMA120 → 均值回归")
print(f"{'='*65}")
for label, rs in [('T+5', rets5), ('T+10', rets10), ('T+20', rets20)]:
    print(f"  {label} (N={len(rs):>4d}): 均值{np.mean(rs):+.2%} 中位{np.median(rs):+.2%} 胜率{np.mean(rs>0):.1%} 夏普{np.mean(rs)/max(np.std(rs),1e-6)*np.sqrt(252/5):.2f}")

# 按偏离度分层
print(f"\n{'='*65}")
print(f"  偏离度分层 (T+5):")
print(f"{'='*65}")
print(f"  {'偏离区间':>12s} {'N':>5s} {'均值':>8s} {'中位':>8s} {'胜率':>6s}")
for lo, hi, lb in [(-1.0, -0.30, '<-30%'), (-0.30, -0.25, '-30%~-25%'), (-0.25, -0.20, '-25%~-20%'), (-0.20, -0.15, '-20%~-15%'), (-0.15, 0, '-15%~0%')]:
    sel = [r for r in results if lo < r['dev'] <= hi]
    if len(sel) < 3: continue
    rs = np.array([r['f5'] for r in sel])
    print(f"  {lb:>12s} {len(rs):>5d} {np.mean(rs):>+7.2%} {np.median(rs):>+7.2%} {np.mean(rs>0):>5.1%}")

# 按年
print(f"\n{'='*65}")
print(f"  按年分布:")
print(f"{'='*65}")
by_year = defaultdict(list)
for r in results:
    by_year[r['date'].year].append(r['f5'])
for y in sorted(by_year.keys()):
    rs = np.array(by_year[y])
    print(f"  {y}: {len(rs):>4d}条  均值{np.mean(rs):+.2%}  中位{np.median(rs):+.2%}  胜率{np.mean(rs>0):.1%}")

# 多层条件筛选
print(f"\n{'='*65}")
print(f"  综合条件 (深度偏离+大跌幅度):")
print(f"{'='*65}")
for dev_thresh in [-0.2, -0.25, -0.3]:
    for ret_thresh in [-0.05, -0.07, -0.10]:
        sel = [r for r in results if r['dev'] < dev_thresh and r['ret'] < ret_thresh]
        if len(sel) < 5: continue
        rs = np.array([r['f5'] for r in sel])
        print(f"  偏<{dev_thresh:.0%}+大跌<{ret_thresh:.0%}: {len(rs):>4d}条  T+5均值{np.mean(rs):+.2%} 胜率{np.mean(rs>0):.1%}")

conn.close()
print(f"\n✅")
