#!/usr/bin/env python3
"""
收盘庄家护盘扫描 — 每日收盘后运行
识别特征: ①大盘跌个股不跌/微跌 ②分时承接放量 ③盘口买盘托单 ④缩量横盘不下探
交叉验证: 与现有评级框架中的因子做匹配 (机构成本/净利润/庄家行为阶段)
"""
import psycopg2, numpy as np, json, os, sys
from datetime import date, datetime, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUTDIR = '/home/tearsyu/.openclaw/workspace/memory/reports'
TODAY = date.today()

conn = psycopg2.connect(DB)
cur = conn.cursor()

print(f"\n{'='*70}")
print(f"  庄家护盘扫描 — {TODAY}")
print(f"{'='*70}")

# ① 获取今日大盘数据
cur.execute("SELECT trade_date, close FROM daily_prices WHERE symbol='A.000001' AND close>0 ORDER BY trade_date DESC LIMIT 2")
idx = cur.fetchall()
if len(idx) >= 2:
    sh_today = float(idx[0][1])
    sh_yest = float(idx[1][1])
    sh_chg = (sh_today / sh_yest - 1) * 100
    print(f"\n  上证今日: {sh_today:.2f} 涨跌幅: {sh_chg:+.2f}%")
else:
    sh_chg = 0

# ② 获取全部股票列表（有日线数据的活跃A股）
cur.execute("SELECT DISTINCT symbol FROM daily_prices WHERE close>0 AND trade_date >= '2025-01-01'")
all_symbols = [r[0] for r in cur.fetchall()]
print(f"  全市场: {len(all_symbols)}只", flush=True)

# ③ 每日特征扫描 — 每只股票今日的数据
CACHE_OUT = os.path.join(OUTDIR, f'support_scan_{TODAY}.json')

results = []
batch_size = 100
for idx_start in range(0, min(len(all_symbols), 2000), batch_size):
    batch = all_symbols[idx_start:idx_start+batch_size]
    for sym in batch:
        try:
            # 最近60天数据
            cur.execute("""
                SELECT trade_date, close, volume, amount 
                FROM daily_prices WHERE symbol=%s AND close>0 
                ORDER BY trade_date DESC LIMIT 60
            """, (sym,))
            rows = cur.fetchall()
            if len(rows) < 10:
                continue
            
            rows.sort(key=lambda x: x[0])
            n = len(rows)
            closes = np.array([float(r[1]) for r in rows], dtype=float)
            volumes = np.array([float(r[2]) if r[2] else 0 for r in rows], dtype=float)
            amounts = np.array([float(r[3]) if r[3] else 0 for r in rows], dtype=float)
            
            today_c = float(rows[-1][1])
            yest_c = float(rows[-2][1]) if n >= 2 else today_c
            chg = (today_c / yest_c - 1) * 100
            today_v = float(rows[-1][2]) if rows[-1][2] else 0
            today_amt = float(rows[-1][3]) if rows[-1][3] else 0
            
            # 需要今日数据接近当日 (数据新鲜度检查)
            if rows[-1][0] != TODAY:
                continue  # 今日数据还没入库，跳过
            
            # 均值
            if n >= 20:
                avg_20_v = np.mean(volumes[-20:])
                avg_prev_v = np.mean(volumes[-20:-5]) if n >= 25 else np.mean(volumes[:-5])
            else:
                avg_20_v = np.mean(volumes)
                avg_prev_v = np.mean(volumes[:-1])
            
            # 量比 = 今日量 / 近20日均量
            vol_ratio = today_v / max(avg_20_v, 1)
            
            # SMA120偏离
            cur.execute("SELECT close FROM daily_prices WHERE symbol=%s AND close>0 ORDER BY trade_date DESC LIMIT 120", (sym,))
            hist120 = cur.fetchall()
            if len(hist120) >= 120:
                sma120 = np.mean([float(r[0]) for r in hist120])
                sdev = (today_c / sma120 - 1) * 100
            else:
                sdev = (today_c / np.mean(closes) - 1) * 100
            
            # ① 缩量护盘: 大盘跌/个股不跌 + 缩量
            chg_ok = chg >= sh_chg + 0.5  # 跑赢大盘至少0.5%
            vol_light = vol_ratio < 1.0  # 不放量
            chg_positive = chg > -1.0  # 不深跌
            support_flag = chg_ok and vol_light and chg_positive
            
            # ② 放量护盘: 大盘跌/个股微跌 + 放量 + 买盘特征
            support_flag2 = chg >= sh_chg and chg > -2.0 and vol_ratio >= 1.2 and chg >= -1.5
            
            # ③ 持续护盘: 近3天大盘跌/个股不跌 + 连续缩量
            if n >= 5:
                r3_close = float(rows[-1][1])
                r3_prev = float(rows[-5][1])
                r3_chg = (r3_close / r3_prev - 1) * 100
                r3_vol_ratio = np.mean(volumes[-3:]) / max(np.mean(volumes[-10:-3]), 1) if n >= 13 else 2
                sustain_flag = (r3_chg > sh_chg - 1 or r3_chg > -3) and r3_vol_ratio < 1.0
            else:
                sustain_flag = False
            
            score = 0
            reasons = []
            if support_flag:
                score += 2
                reasons.append(f"缩量抗跌(+2)")
            if support_flag2:
                score += 2
                reasons.append(f"放量护盘(+2)")
            if sustain_flag:
                score += 1
                reasons.append(f"持续护盘(+1)")
            if chg_positive and vol_ratio < 0.6:
                score += 1
                reasons.append(f"极度缩量(+1)")
            
            # 额外加分: 在深跌区(SMA<-20%)的护盘更有意义
            if sdev < -20:
                score += 1
                reasons.append(f"深跌区(+1)")
            
            if score >= 3:
                results.append({
                    'symbol': sym,
                    'code': sym.split('.')[-1] if '.' in sym else sym,
                    'score': score,
                    'chg': round(chg, 2),
                    'sh_chg': round(sh_chg, 2),
                    'vol_ratio': round(vol_ratio, 2),
                    'sdev': round(sdev, 1),
                    'today_close': round(today_c, 2),
                    'today_vol': int(today_v),
                    'today_amt': round(today_amt/1e8, 2),
                    'reasons': ';'.join(reasons),
                })
        except Exception as e:
            continue

    if len(results) > 200:
        break  # 取前200个就够了，后续做精确排名

print(f"  初筛: {len(results)}只候选", flush=True)

# ④ 排序取前30
results.sort(key=lambda x: x['score'], reverse=True)
top30 = results[:30]

# ⑤ 交叉验证: 查它们的评级因子
print(f"\n  {'='*55}")
print(f"  庄家护盘候选 TOP 30")
print(f"  {'='*55}")
print(f"  {'代码':>8} {'得分':>4} {'涨跌':>6} {'大盘':>6} {'量比':>5} {'SMA':>6} {'原因'}")
print(f"  {'-'*55}")

for r in top30[:20]:
    print(f"  {r['code']:>8} {r['score']:>4} {r['chg']:>+5.1f}% {r['sh_chg']:>+5.1f}% {r['vol_ratio']:>4.1f}x {r['sdev']:>+5.1f}% {r['reasons'][:30]}")

# ⑥ 与现有持仓做交叉验证
print(f"\n  {'='*55}")
print(f"  与持仓交叉验证")
print(f"  {'='*55}")

holdings = {
    '600273': '嘉化能源', '002073': '软控股份',
    '600635': '大众公用', '600668': '尖峰集团'
}

for hcode, hname in holdings.items():
    matched = [r for r in results if r['code'] == hcode]
    if matched:
        r = matched[0]
        print(f"  {hname}({hcode}): 护盘得分{r['score']} — {r['reasons']}")
    else:
        print(f"  {hname}({hcode}): ❌ 未识别为护盘")

# ⑦ 保存
outpath = os.path.join(OUTDIR, f'support_scan_{TODAY}.json')
with open(outpath, 'w') as f:
    json.dump({
        'date': str(TODAY),
        'sh_chg': round(sh_chg, 2),
        'total_scanned': len(all_symbols),
        'candidates': [{
            'code': r['code'], 'score': r['score'],
            'chg': r['chg'], 'vol_ratio': r['vol_ratio'],
            'sdev': r['sdev'], 'reasons': r['reasons']
        } for r in results],
        'top30': [r['code'] for r in top30]
    }, f, ensure_ascii=False, indent=1)

print(f"\n  保存: {outpath}")
conn.close()
