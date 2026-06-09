#!/usr/bin/env python3
"""
主力活跃度过滤 — 回测验证
抽样近1年所有V8触发信号，对比通过/不通过过滤的T+20收益
"""
import sys
sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
from strategies.active_filter import compute_active_score
import psycopg2
import numpy as np
from datetime import date, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
conn = psycopg2.connect(DB)
cur = conn.cursor()

start = date(2025, 6, 1)
end = date(2026, 6, 4)

print(f"回测区间: {start} ~ {end}")
print("=" * 70)

# 策略1: 选取所有V8候选信号（SMA偏差<-20% 且 PB<1.0）
# 从financial_data取BVPS，配合daily_prices计算SMA120
# 但全量计算太慢，直接用更高效的SQL筛选

# 先取所有有BVPS的股票的最近SMA120
# 从daily_prices取120日均价
# 简化: 只计算每个股票在回测期间每个触发日的情况
# 为了效率，我们逐月采样

print("构建V8候选池...")
cur.execute("""
    SELECT symbol, trade_date, close
    FROM daily_prices
    WHERE trade_date >= %s AND trade_date <= %s
      AND close > 0 AND volume > 0
    ORDER BY symbol, trade_date
""", (start, end))
rows = cur.fetchall()
print(f"总数据行: {len(rows)}")

# 按股票分组
stock_data = defaultdict(list)
for r in rows:
    stock_data[r[0]].append((r[1], r[2]))
print(f"总股票数: {len(stock_data)}")

# 取BVPS
cur.execute("""
    SELECT DISTINCT ON (symbol) symbol, bvps
    FROM financial_data
    WHERE bvps IS NOT NULL AND bvps > 0
    ORDER BY symbol, end_date DESC
""")
bvps_map = {r[0]: r[1] for r in cur.fetchall()}
print(f"有BVPS的股票: {len(bvps_map)}")

# 筛选条件: symbol在bvps_map中
symbols_with_bvps = set(bvps_map.keys())
# 只取A.前缀的
valid_symbols = {s for s in stock_data if s in bvps_map and s.startswith('A.')}
print(f"有BVPS+日线的: {len(valid_symbols)}")

# 对每个股票计算触发日
v8_signals = []
count = 0
for sym in valid_symbols:
    count += 1
    if count % 500 == 0:
        print(f"  处理: {count}/{len(valid_symbols)}")
    
    data = stock_data[sym]
    closes = np.array([r[1] for r in data])
    dates = [r[0] for r in data]
    bvps = bvps_map[sym]
    
    n = len(closes)
    if n < 130:
        continue
    
    # SMA120
    cumsum = np.cumsum(closes)
    sma120 = np.full(n, np.nan)
    sma120[119] = cumsum[119] / 120
    sma120[120:] = (cumsum[120:] - cumsum[:n-120]) / 120
    
    sma_dev = closes / sma120 - 1
    pb = closes / bvps
    
    # 找触发日
    for i in range(119, n):
        if sma_dev[i] < -0.20 and pb[i] < 1.0:
            d = dates[i]
            if d < start or d > end:
                continue
            v8_signals.append((sym, d, float(closes[i]), float(sma_dev[i]), float(pb[i])))

print(f"V8触发信号总数: {len(v8_signals)}")

# 对每个信号计算active_score和T+20收益
passed = []  # (ret20, score)
failed = []
pass_count = 0
fail_count = 0

print("\n计算active_score和T+20收益...")
for idx, (sym, d, close, sma_d, pb_v) in enumerate(v8_signals):
    if idx % 200 == 0:
        print(f"  信号: {idx}/{len(v8_signals)}")
    
    # active_score
    score = compute_active_score(conn, sym, d)
    if score is None:
        continue
    
    # T+20收益
    cur.execute("""
        SELECT close FROM daily_prices
        WHERE symbol=%s AND trade_date>%s AND close>0
        ORDER BY trade_date LIMIT 20
    """, (sym, d))
    future = [float(r[0]) for r in cur.fetchall()]
    if len(future) < 20:
        continue
    
    ret20 = (future[-1] / close - 1) * 100
    
    if score['pass']:
        passed.append((ret20, score['score']))
        pass_count += 1
    else:
        failed.append((ret20, score['score']))
        fail_count += 1

print("\n" + "=" * 70)
print("回测结果")
print("=" * 70)

if passed:
    p_ret = [r[0] for r in passed]
    p_win = sum(1 for r in p_ret if r > 0)
    print(f"\n✅ 通过主力过滤 ({len(passed)}次)")
    print(f"  T+20平均收益: {np.mean(p_ret):+.2f}%")
    print(f"  T+20中位收益: {np.median(p_ret):+.2f}%")
    print(f"  胜率: {p_win/len(p_ret)*100:.1f}%")
    print(f"  最大收益: {max(p_ret):+.2f}%")
    print(f"  最大亏损: {min(p_ret):+.2f}%")

if failed:
    f_ret = [r[0] for r in failed]
    f_win = sum(1 for r in f_ret if r > 0)
    print(f"\n❌ 未通过主力过滤 ({len(failed)}次)")
    print(f"  T+20平均收益: {np.mean(f_ret):+.2f}%")
    print(f"  T+20中位收益: {np.median(f_ret):+.2f}%")
    print(f"  胜率: {f_win/len(f_ret)*100:.1f}%")
    print(f"  最大收益: {max(f_ret):+.2f}%")
    print(f"  最大亏损: {min(f_ret):+.2f}%")

if passed and failed:
    # 统计显著性
    from scipy import stats
    t_stat, p_val = stats.ttest_ind(p_ret, f_ret, alternative='greater')
    print(f"\n统计检验(t-test, passed>failed): t={t_stat:.3f}, p={p_val:.4f}")

print("\n" + "=" * 70)
print(f"通过率: {pass_count/(pass_count+fail_count)*100:.1f}%")

conn.close()
