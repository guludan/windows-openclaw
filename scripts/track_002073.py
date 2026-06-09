#!/usr/bin/env python3
"""
软控股份(002073) 每日监控脚本
收盘后运行，输出特征到 D:/data/a-stock/reports/选股跟踪/
"""
import sys, os, json
sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
from strategies.v8_strategy import V8Strategy
import psycopg2, numpy as np
from datetime import date, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
today = date.today()
sc = '002073'
name = '软控股份'
industry = '专用设备'

conn = psycopg2.connect(DB)
cur = conn.cursor()

# 日线（2019年起）
cur.execute("""
    SELECT trade_date, close, open, high, low, volume, amount
    FROM daily_prices WHERE symbol='A.{}' AND trade_date >= '2019-01-01' AND close > 0
    ORDER BY trade_date
""".format(sc))
rows = cur.fetchall()

dates = [r[0] for r in rows]
closes = np.array([float(r[1]) for r in rows])
opens = np.array([float(r[2]) for r in rows])
highs = np.array([float(r[3]) for r in rows])
lows = np.array([float(r[4]) for r in rows])
volumes = np.array([float(r[5]) for r in rows])
amounts = np.array([float(r[6]) if r[6] else 0 for r in rows])
n = len(closes)

# SMA120
cumsum = np.cumsum(closes)
sma120 = np.full(n, np.nan)
for i in range(119, n):
    sma120[i] = cumsum[i]/120 if i == 119 else (cumsum[i]-cumsum[i-120])/120
sma_dev = closes/sma120 - 1

# SMA斜率20d
sma_slope = np.full(n, np.nan)
sma_slope[20:] = np.where(sma120[:-20] > 0, sma120[20:]/sma120[:-20]-1, np.nan)

# 量比(20d)
vol_ma20 = np.full(n, np.nan)
if n >= 20:
    vol_cum = np.cumsum(volumes)
    vol_ma20[19] = vol_cum[19]/20
    vol_ma20[20:] = (vol_cum[20:] - vol_cum[:n-20])/20
vol_ratio = np.where(vol_ma20 > 0, volumes/np.maximum(vol_ma20, 1), np.nan)

# 额比
amt_ma20 = np.full(n, np.nan)
if n >= 20:
    amt_cum = np.cumsum(amounts)
    amt_ma20[19] = amt_cum[19]/20
    amt_ma20[20:] = (amt_cum[20:] - amt_cum[:n-20])/20
amt_ratio = np.where(amt_ma20 > 0, amounts/np.maximum(amt_ma20, 1), np.nan)

# 日涨跌
r1d = np.full(n, np.nan); r1d[1:] = closes[1:]/closes[:-1]-1

# 振幅(20d)
amp20d = np.full(n, np.nan)
for i in range(19, n):
    vals = [(highs[j]-lows[j])/opens[j] for j in range(i-19, i+1) if opens[j] > 0]
    amp20d[i] = np.mean(vals) if vals else 0

# 振幅趋势(前20 vs 再前20)
amp_trend = np.full(n, np.nan)
for i in range(39, n):
    cur20 = [(highs[j]-lows[j])/opens[j] for j in range(i-19, i+1) if opens[j] > 0]
    prev20 = [(highs[j]-lows[j])/opens[j] for j in range(i-39, i-19) if opens[j] > 0]
    c = np.mean(cur20) if cur20 else 0
    p = np.mean(prev20) if prev20 else 0.001
    amp_trend[i] = c/p - 1

# 连跌天数
conseq_dn = np.zeros(n, dtype=int)
for i in range(1, n):
    if not np.isnan(r1d[i]) and r1d[i] < 0:
        conseq_dn[i] = conseq_dn[i-1] + 1

# 前5日涨幅
ret5 = np.full(n, np.nan)
if n >= 6: ret5[5:] = closes[5:]/closes[:n-5]-1

# 累计跌幅(连跌期内)
total_decline = np.zeros(n)
for i in range(1, n):
    if conseq_dn[i] > 0:
        total_decline[i] = np.prod(1+r1d[i-conseq_dn[i]+1:i+1]) - 1

# 状态判断
price_state = np.full(n, '', dtype=object)
for i in range(n):
    if sma_slope[i] is not None and not np.isnan(sma_slope[i]):
        if sma_slope[i] > 0.005:
            price_state[i] = '向上'
        elif sma_slope[i] < -0.005:
            price_state[i] = '加速跌'
        else:
            price_state[i] = '横盘'

# V8通过条件检查
v8_sma_mask = sma_dev < -0.20

# Phase
cur.execute("SELECT date, phase FROM pool_energy WHERE level='market' ORDER BY date")
pm = {r[0]: r[1] for r in cur.fetchall()}
cur.execute("SELECT date, extra->>'sz_5', extra->>'sz_20', extra->>'surplus_z', extra->>'regime', extra->>'accel' FROM pool_energy WHERE level='market' AND extra IS NOT NULL ORDER BY date")
regime_info = {}
for r in cur.fetchall():
    try:
        regime_info[r[0]] = {
            'sz5': float(r[1]) if r[1] else None,
            'sz20': float(r[2]) if r[2] else None,
            'sz': float(r[3]) if r[3] else None,
            'regime': r[4],
            'accel': float(r[5]) if r[5] else None,
        }
    except: pass

conn.close()

# ========== 输出 ==========
out = []
out.append(f"# 软控股份(002073) 日线监控 | {today}")
out.append(f"")
out.append(f"## 近期特征")
hdr = f"{'日期':>12} {'收盘':>7} {'涨跌':>8} {'SMA120':>9} {'SMA偏离':>9} {'SMA斜率':>9} {'量比':>6} {'额比':>6} {'振幅':>7} {'振幅趋势':>8} {'连跌天':>6} {'总跌幅':>8} {'前5日':>8} {'V8?':>4}"
out.append(hdr)
out.append("-" * len(hdr))

for i in range(max(0, n-30), n):
    d = dates[i]
    v8flag = '✅' if (not np.isnan(v8_sma_mask[i]) and v8_sma_mask[i]) else ''
    s = sma_slope[i] if not np.isnan(sma_slope[i]) else 0
    vr = vol_ratio[i] if not np.isnan(vol_ratio[i]) else 0
    ar = amt_ratio[i] if not np.isnan(amt_ratio[i]) else 0
    ampl = amp20d[i] if not np.isnan(amp20d[i]) else 0
    at = amp_trend[i] if not np.isnan(amp_trend[i]) else 0
    td = total_decline[i] if not np.isnan(total_decline[i]) else 0
    r5 = ret5[i] if not np.isnan(ret5[i]) else 0
    r1 = r1d[i] if not np.isnan(r1d[i]) else 0
    cd = conseq_dn[i]
    sd = sma_dev[i] if not np.isnan(sma_dev[i]) else 0
    sma = sma120[i] if not np.isnan(sma120[i]) else 0
    out.append(f"{d} {closes[i]:>7.2f} {r1:>+8.2%} {sma:>9.2f} {sd:>+9.2%} {s:>+9.2%} {vr:>6.2f}x {ar:>6.2f}x {ampl:>7.2%} {at:>+8.2%} {cd:>4d}/{td:>+7.2%}  {r5:>+8.2%} {v8flag:>4}")

# 今日市场状态
ri = regime_info.get(today) or regime_info.get(date(2026,6,1))
if ri:
    out.append(f"\n## 市场状态 ({today})")
    out.append(f"- Phase: {pm.get(today, '?')}")
    out.append(f"- Regime: {ri.get('regime','?')}")
    out.append(f"- surplus_z: {ri.get('sz','?'):.3f} (sz5={ri.get('sz5','?'):.3f}  sz20={ri.get('sz20','?'):.3f})")
    out.append(f"- 加速度: {ri.get('accel','?'):.3f}")

# 最新状态
out.append(f"\n## 当前状态")
out.append(f"- SMA偏离: {sma_dev[-1]:+.2%} (需< -20%)")
out.append(f"- SMA斜率: {sma_slope[-1]:+.2%}")
out.append(f"- V8触发: {'✅ 已触发' if v8_sma_mask[-1] else '❌ 未触发'}")
out.append(f"- 连跌天数: {conseq_dn[-1]}d")
out.append(f"- 振幅趋势: {amp_trend[-1]:+.2%}")

# V8仪表盘交叉参考
out.append(f"\n## 交叉参考")
out.append(f"- V8仪表盘: D:/data/a-stock/reports/{today}/v8_dashboard_{today}.txt")
out.append(f"- 选股推荐: D:/data/a-stock/reports/{today}/选股推荐_{today}.md")

# 写入
report_dir = f"/home/tearsyu/.openclaw/workspace/memory/reports"
os.makedirs(report_dir, exist_ok=True)
report_path = f"{report_dir}/track_002073_{today}.md"
with open(report_path, 'w') as f:
    f.write('\n'.join(out))

# 同时写D盘
disk_dir = f"/mnt/d/data/a-stock/reports/{today}"
os.makedirs(disk_dir, exist_ok=True)
disk_path = f"{disk_dir}/track_002073_{today}.md"
with open(disk_path, 'w') as f:
    f.write('\n'.join(out))

print(f"已写入 {disk_path}")
print('\n'.join(out[-10:]))
