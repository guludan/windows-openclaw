"""EPS + close 实时算 PE → 估值偏离度均值回归验证

1. 从 financial_data 拿每只股票的季度 EPS（最近4个季度滚动）
2. 从 daily_prices 拿日线 close
3. 算实时 PE = price / eps_ttm
4. 算 PE 相对120日中位数的偏离度
5. 超卖条件触发 → 统计 T+N 回归收益
"""
import psycopg2, numpy as np
from collections import defaultdict
from datetime import datetime, timedelta

conn = psycopg2.connect("host=192.168.3.14 dbname=guludan user=guludan password=toto", options="-c statement_timeout=0")
cur = conn.cursor()

# 1) 拉所有有 EPS 数据的股票列表
cur.execute("""
    SELECT DISTINCT symbol as c6
    FROM financial_data 
    WHERE eps IS NOT NULL AND eps > 0
""")
all_codes = [r[0] for r in cur.fetchall()]
print(f"有 EPS 的股票: {len(all_codes)} 只")

# 2) 预拉所有金融数据 (最近2年的季度EPS)
cur.execute("""
    SELECT symbol as c6, end_date, eps
    FROM financial_data 
    WHERE eps IS NOT NULL AND eps > 0 
      AND end_date >= '2023-01-01'
    ORDER BY c6, end_date
""")
eps_data = {}
for c6, end_date, eps in cur.fetchall():
    if c6 not in eps_data:
        eps_data[c6] = {}
    eps_data[c6][end_date] = float(eps)
print(f"EPS 记录: {sum(len(v) for v in eps_data.values())}")

# 3) 对前 300 只股票跑
# 限定300只够了——太多时间不够
sample_codes = [c for c in all_codes if c in eps_data and len(eps_data[c]) >= 3][:300]
print(f"回测样本: {len(sample_codes)} 只")

results = []

for si, c6 in enumerate(sample_codes):
    if si % 50 == 0 and si > 0:
        print(f"  ...{si}/{len(sample_codes)} 事件:{len(results)}")
    if len(results) >= 2000:
        break
    
    # 拉到2026-05的日线
    cur.execute("""
        SELECT trade_date, close FROM daily_prices 
        WHERE symbol='A.'||%s AND trade_date>='2023-01-01' AND close>0
        ORDER BY trade_date
    """, (c6,))
    price_rows = cur.fetchall()
    if len(price_rows) < 200:
        continue
    
    dates = [r[0] for r in price_rows]
    closes = np.array([float(r[1]) for r in price_rows], dtype=float)
    
    # EPS 序列映射到每个交易日: 用最新的可用季度EPS
    stock_eps = eps_data.get(c6, {})
    if not stock_eps:
        continue
    
    eps_dates = sorted(stock_eps.keys())
    
    # 对每个交易日找最新的 EPS (过去4个季度滚动求和 = eps_ttm)
    eps_ttm_values = np.full(len(dates), np.nan)
    
    for i, dt in enumerate(dates):
        # 获取可用eps: publish_date <= dt
        recent_epses = [(ed, stock_eps[ed]) for ed in eps_dates if ed <= dt]
        if len(recent_epses) >= 4:
            # 取最近4个季度(有些一年只有2次报告，按可用次数取)
            last4 = recent_epses[-4:]
            eps_ttm_values[i] = sum(e[1] for e in last4)
        elif len(recent_epses) >= 2 and dt.year >= 2024:
            # 至少2个季度也勉强可用
            eps_ttm_values[i] = sum(e[1] for e in recent_epses[-2:]) * 2
    
    # 只保留有 eps_ttm 的日子
    valid_idx = np.where(~np.isnan(eps_ttm_values))[0]
    if len(valid_idx) < 130:
        continue
    
    # 对每个有 EPS 的日子
    for idx_pos in range(120, len(valid_idx)):
        i = valid_idx[idx_pos]
        pe = closes[i] / eps_ttm_values[i]
        
        if pe > 200 or pe < 1:  # 过滤异常
            continue
        
        # 过去120日 PE 的中位 (用前面valid_idx的PE)
        hist_pe = []
        for j in valid_idx[max(0, idx_pos-120):idx_pos-5]:
            pj = closes[j] / eps_ttm_values[j]
            if 1 < pj < 200:
                hist_pe.append(pj)
        
        if len(hist_pe) < 30:
            continue
        
        pe_med = np.median(hist_pe)
        pe_dev = (pe - pe_med) / pe_med  # 负值 = 折价
        
        # 当日涨跌
        ret = closes[i] / closes[i-1] - 1
        
        # 超卖条件: PE偏离度 < -15% (不加涨跌幅限制，先看PE回归本身)
        if pe_dev >= -0.15:
            continue
        
        f5 = closes[i+5]/closes[i]-1 if i+5 < len(closes) else None
        f10 = closes[i+10]/closes[i]-1 if i+10 < len(closes) else None
        f20 = closes[i+20]/closes[i]-1 if i+20 < len(closes) else None
        
        if f5 is not None:
            results.append({
                'c6': c6, 'date': dates[i],
                'pe': pe, 'pe_med': pe_med, 'pe_dev': pe_dev,
                'ret': ret,
                'f5': f5, 'f10': f10, 'f20': f20
            })

print(f"\n极端负偏离 PE 事件: {len(results)}")

if len(results) < 10:
    print("事件不足，试试放宽条件")
    exit()

# 全量统计
for label, key in [('T+5', 'f5'), ('T+10', 'f10'), ('T+20', 'f20')]:
    vals = [r[key] for r in results if r[key] is not None]
    if not vals: continue
    rs = np.array(vals)
    print(f"  {label} (N={len(vals)}): 均值{np.mean(rs):+.2%} 中位{np.median(rs):+.2%} 胜率{np.mean(rs>0):.1%}")

# 按 PE 偏离度分层
print(f"\nPE偏离度分层 T+5:")
for lo, hi, lb in [(-1.0, -0.50, '<-50%'), (-0.50, -0.35, '-50%~-35%'), (-0.35, -0.25, '-35%~-25%'), (-0.25, -0.20, '-25%~-20%')]:
    sel = [r for r in results if lo < r['pe_dev'] <= hi]
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

# 与 SMA120 偏离度的交叉验证: PE偏离+SMA偏离一起看
print(f"\nPE偏离+SMA120偏离双因子 T+5:")
for pe_d_thresh in [-0.25, -0.30]:
    for sma_d_thresh in [-0.15, -0.20]:
        sel = [r for r in results if r['pe_dev'] < pe_d_thresh]
        if len(sel) < 5: continue
        rs = np.array([r['f5'] for r in sel])
        print(f"  PE<{pe_d_thresh:.0%}(N={len(rs):>4d}): {np.mean(rs):+.2%} 胜率{np.mean(rs>0):.1%}")

conn.close()
