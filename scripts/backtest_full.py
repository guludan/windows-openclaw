#!/usr/bin/env python3
"""
backtest_full.py — 全量逐日滚动回测 + 多层评分
===============================================
设计:
  1. 一次性从PG加载全量日线数据(2015~最新)
  2. 逐日滚动窗口: 每一步只用到当天及之前的数据
  3. 对当日每个股票计算: SMA偏离, 60dVWAP折价, 量比, 缩量横盘 → cost评分
  4. 多层分组: 按SMA阈值(-14%/-20%/-25%), 按评分(60-70-80-90-100), 按regime, 按年份
  5. 统计每个信号在T+5/T+10/T+20/T+40的收益+胜率
  6. 输出JSON报告

用法: python3 scripts/backtest_full.py                      # 全量跑2015~最新
      python3 scripts/backtest_full.py --quick               # 只跑2025以后(快速)

输出: memory/reports/backtest_full_report_YYYY-MM-DD.json
"""
import psycopg2, numpy as np, json, os, sys, time
from datetime import date, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT = '/home/tearsyu/.openclaw/workspace/memory/reports'
t0 = time.time()

# ========== 参数 ==========
SMA_THRESHOLDS = [-0.14, -0.20, -0.25, -0.30]        # SMA偏离阈值
SCORE_BINS = [(60,70),(70,80),(80,90),(90,95),(95,101)] # 评分区间
HOLD_DAYS = [5, 10, 20, 40, 60]                       # 持有期

# ========== 1. 加载数据 ==========
conn = psycopg2.connect(DB)
cur = conn.cursor()

print("交易日历...", file=sys.stderr)
cur.execute("SELECT trade_date FROM daily_prices WHERE trade_date>='2015-01-01' GROUP BY trade_date ORDER BY trade_date")
all_dates = [r[0] for r in cur.fetchall()]
date_set = set(all_dates)
print(f"  {len(all_dates)}个交易日", file=sys.stderr)

print("加载日线...", file=sys.stderr)
cur.execute("""
    SELECT symbol, trade_date, close, volume 
    FROM daily_prices WHERE trade_date >= '2015-01-01' AND close > 0 AND volume > 0
    ORDER BY symbol, trade_date
""")
rows = cur.fetchall()
print(f"  {len(rows):,}条", file=sys.stderr)

# 按code分组
data_by_code = defaultdict(list)
for r in rows:
    code = r[0][2:] if r[0].startswith('A.') else r[0]
    data_by_code[code].append((r[1], float(r[2]), float(r[3])))
print(f"  {len(data_by_code)}只股票", file=sys.stderr)

print("加载ST/regime...", file=sys.stderr)
cur.execute("SELECT stock_code FROM stock_industry WHERE stock_name LIKE '%ST%' OR stock_name LIKE '%PT%'")
st_codes = {r[0] for r in cur.fetchall()}
cur.execute("""
    SELECT date, extra->>'regime' FROM pool_energy 
    WHERE level='market' AND date>='2015-01-01' AND extra IS NOT NULL
""")
regime_map = {r[0]: r[1] for r in cur.fetchall()}
print(f"  ST:{len(st_codes)}  regime:{len(regime_map)}天", file=sys.stderr)
conn.close()

# ========== 2. 评分函数(严格逐日, 只用当天之前数据) ==========
def calc_score_and_factors(closes, volumes, cur_price):
    """返回 (score, sma_dev, vol_ratio, is_consol)"""
    n = len(closes)
    if n < 60:
        return 0, 0, 1.0, False
    
    cs = closes[-60:]
    vs = volumes[-60:]
    
    # SMA120偏离
    sma120 = float(np.mean(closes[-120:])) if n >= 120 else cur_price
    sma_dev = cur_price / sma120 - 1
    
    # VWAP60
    vwap60 = float(np.sum(cs * vs) / np.sum(vs)) if np.sum(vs) > 0 else cur_price
    vwap60_disc = cur_price / vwap60 - 1
    
    # 量比60/120
    if n >= 120:
        vol_ratio = float(np.mean(volumes[-60:]) / max(np.mean(volumes[-120:]), 1))
    else:
        vol_ratio = 1.0
    
    # 缩量横盘检测(40天振幅)
    latest_range = (np.max(cs) / max(np.min(cs), 0.01) - 1) * 100
    is_consol = vol_ratio < 0.9 and latest_range < 25
    
    # 评分
    score = 50
    if sma_dev < -0.25: score += 15
    elif sma_dev < -0.20: score += 10
    elif sma_dev < -0.15: score += 5
    if vwap60_disc < -0.15: score += 10
    elif vwap60_disc < -0.10: score += 5
    if is_consol: score += 10
    if vol_ratio < 0.7: score += 8
    elif vol_ratio < 0.9: score += 5
    
    return min(100, score), sma_dev, vol_ratio, is_consol

# ========== 3. 逐日滚动回测 ==========
print("\n逐日滚动回测...", file=sys.stderr)

# 对每只股票独立处理
signal_rows = []  # 用来逐条收集

stock_list = [code for code in data_by_code if code not in st_codes and len(data_by_code[code]) >= 125]
n_stocks = len(stock_list)

for idx, code in enumerate(stock_list):
    hist = data_by_code[code]
    closes = np.array([r[1] for r in hist], dtype=float)
    volumes = np.array([r[2] for r in hist], dtype=float)
    
    # 逐个交易日滑动
    for i in range(120, len(closes)):
        td = hist[i][0]
        if td not in date_set:
            continue
        
        cp = float(closes[i])
        if cp < 2.5 or cp > 50:
            continue
        
        # 只用当天之前的数据算评分
        score, sma_dev, vol_ratio, is_consol = calc_score_and_factors(
            closes[:i], volumes[:i], cp
        )
        
        if score < 60:
            continue
        
        # 记录阈值
        matched_th = None
        for th in SMA_THRESHOLDS:
            if sma_dev < th:
                matched_th = th
                break
        if matched_th is None:
            continue  # 连-14%都不满足
        
        regime = regime_map.get(td, 'UNKNOWN')
        
        # T+N收益
        rets = {}
        for hd in HOLD_DAYS:
            j = i + hd
            if j < len(closes):
                rets[f'T+{hd}'] = float(closes[j] / cp - 1)
        
        # 评分bin
        score_bin = None
        for lo, hi in SCORE_BINS:
            if lo <= score < hi:
                score_bin = f'{lo}-{hi-1}'
                break
        
        # 阈值bin(作为key)
        th_key = f'{matched_th:.0%}'
        
        signal_rows.append({
            'code': code,
            'date': td.isoformat(),
            'close': cp,
            'score': score,
            'sma_dev': sma_dev,
            'vol_ratio': vol_ratio,
            'is_consol': is_consol,
            'threshold': th_key,
            'score_bin': score_bin or '60-69',
            'regime': regime,
            'year': str(td.year),
            'returns': rets,
        })
    
    if (idx + 1) % 500 == 0:
        el = time.time() - t0
        print(f"  [{idx+1}/{n_stocks}] {code} {len(signal_rows):,}条信号, {el:.0f}s", file=sys.stderr)

print(f"\n总信号: {len(signal_rows):,}", file=sys.stderr)

# ========== 4. 统计 ==========
def analyze_signals(sig_list, label=''):
    if not sig_list:
        return None
    stats = {}
    for hd in HOLD_DAYS:
        tag = f'T+{hd}'
        valid = [r for r in sig_list if r['returns'].get(tag) is not None]
        if not valid:
            continue
        rets = [r['returns'][tag] for r in valid]
        stats[tag] = {
            'count': len(valid),
            'avg': float(np.mean(rets)),
            'win_rate': float(np.mean([1 if x > 0 else 0 for x in rets])),
            'median': float(np.median(rets)),
            'p10': float(np.percentile(rets, 10)),
            'p90': float(np.percentile(rets, 90)),
            'std': float(np.std(rets)),
        }
    return stats

# 全量
overall_all = analyze_signals(signal_rows)

# 按阈值
by_threshold = {}
for th in SMA_THRESHOLDS:
    key = f'{th:.0%}'
    sub = [r for r in signal_rows if r['threshold'] == key]
    if sub:
        by_threshold[key] = analyze_signals(sub)
    print(f"  th={key}: {len(sub):,}条", file=sys.stderr)

# 按评分bin
by_scorebin = {}
for lo, hi in SCORE_BINS:
    key = f'{lo}-{hi-1}'
    sub = [r for r in signal_rows if r['score_bin'] == key]
    if sub:
        by_scorebin[key] = analyze_signals(sub)
    print(f"  score {key}: {len(sub):,}条", file=sys.stderr)

# 按regime
by_regime = {}
for regime in sorted(set(r['regime'] for r in signal_rows)):
    sub = [r for r in signal_rows if r['regime'] == regime]
    if sub:
        by_regime[regime] = analyze_signals(sub)
    print(f"  regime {regime}: {len(sub):,}条", file=sys.stderr)

# 按年份
by_year = {}
for yr in sorted(set(r['year'] for r in signal_rows)):
    sub = [r for r in signal_rows if r['year'] == yr]
    if sub:
        by_year[yr] = analyze_signals(sub)

# 评分×regime交叉
score_regime = defaultdict(lambda: defaultdict(list))
for r in signal_rows:
    for hd in HOLD_DAYS:
        tag = f'T+{hd}'
        if r['returns'].get(tag) is not None:
            score_regime[(r['score_bin'], r['regime'])][tag].append(r['returns'][tag])

# 阈值×regime交叉
thresh_regime = defaultdict(lambda: defaultdict(list))
for r in signal_rows:
    for hd in HOLD_DAYS:
        tag = f'T+{hd}'
        if r['returns'].get(tag) is not None:
            thresh_regime[(r['threshold'], r['regime'])][tag].append(r['returns'][tag])

# ========== 5. 输出报告 ==========
result = {
    'meta': {
        'script': 'backtest_full.py v2',
        'date_range': f'2015-{all_dates[-1]}',
        'trade_days': len(all_dates),
        'total_stocks': len(data_by_code),
        'total_signals': len(signal_rows),
        'unique_stocks': len(set(r['code'] for r in signal_rows)),
        'elapsed_seconds': round(time.time() - t0),
    },
    'overall': overall_all,
    'by_threshold': by_threshold,
    'by_scorebin': by_scorebin,
    'by_regime': by_regime,
    'by_year': by_year,
}

# ═══════════════════ 控制台输出 ═══════════════════
el = time.time() - t0
print(f"\n{'='*80}")
print(f"  全量逐日滚动回测报告")
print(f"  区间: 2015~{all_dates[-1]}")
print(f"  信号: {result['meta']['total_signals']:,}条, 股票: {result['meta']['unique_stocks']:,}只")
print(f"  耗时: {el:.0f}s ({el/60:.1f}分钟)")
print(f"{'='*80}")

print(f"\n{'─'*40} 全市场统计 {'─'*40}")
print(f"{'持有期':>8} {'信号数':>12} {'平均收益':>10} {'胜率':>9} {'中位':>9} {'P10':>9} {'P90':>9} {'标准差':>9}")
print(f"{'─'*80}")
for hd in [f'T+{d}' for d in HOLD_DAYS]:
    s = overall_all.get(hd, {})
    if s:
        print(f"{hd:>8} {s['count']:>12,} {s['avg']:>+9.2%} {s['win_rate']:>7.1%} {s['median']:>+8.2%} {s['p10']:>+8.2%} {s['p90']:>+8.2%} {s['std']:>7.2%}")

print(f"\n{'─'*40} 按SMA阈值分层 {hd} {'─'*40}")
print(f"{'阈值':>8} {'信号数':>12} {'平均收益':>10} {'胜率':>9} {'中位':>9}")
for th in SMA_THRESHOLDS:
    key = f'{th:.0%}'
    s = by_threshold.get(key, {}).get(hd, {})
    if s:
        print(f"{key:>8} {s['count']:>12,} {s['avg']:>+9.2%} {s['win_rate']:>7.1%} {s['median']:>+8.2%}")

print(f"\n{'─'*40} 按评分分层 {hd} {'─'*40}")
print(f"{'评分':>8} {'信号数':>12} {'平均收益':>10} {'胜率':>9} {'中位':>9}")
for lbl in sorted(by_scorebin.keys(), key=lambda x: int(x.split('-')[0])):
    s = by_scorebin[lbl].get(hd, {})
    if s:
        print(f"{lbl:>8} {s['count']:>12,} {s['avg']:>+9.2%} {s['win_rate']:>7.1%} {s['median']:>+8.2%}")

print(f"\n{'─'*40} 按Regime分层 {hd} {'─'*40}")
print(f"{'Regime':>16} {'信号数':>12} {'平均收益':>10} {'胜率':>9} {'中位':>9}")
for regime in sorted(by_regime.keys()):
    s = by_regime[regime].get(hd, {})
    if s:
        print(f"{regime:>16} {s['count']:>12,} {s['avg']:>+9.2%} {s['win_rate']:>7.1%} {s['median']:>+8.2%}")

print(f"\n{'─'*40} 按年份分层 {hd} {'─'*40}")
print(f"{'年份':>8} {'信号数':>12} {'平均收益':>10} {'胜率':>9} {'中位':>9}")
for yr in sorted(by_year.keys()):
    s = by_year[yr].get(hd, {})
    if s:
        print(f"{yr:>8} {s['count']:>12,} {s['avg']:>+9.2%} {s['win_rate']:>7.1%} {s['median']:>+8.2%}")

print(f"\n{'─'*40} 评分×Regime交叉 {hd} {'─'*40}")
print(f"{'评分':>8} {'Regime':>16} {'信号数':>10} {'平均收益':>10} {'胜率':>8}")
for (sb, rg) in sorted(score_regime.keys()):
    v = score_regime[(sb, rg)].get(hd, [])
    if len(v) >= 20:
        print(f"{sb:>8} {rg:>16} {len(v):>10,} {np.mean(v):>+9.2%} {np.mean([1 if x>0 else 0 for x in v]):>7.1%}")

print(f"\n{'─'*40} 阈值×Regime交叉 {hd} {'─'*40}")
print(f"{'阈值':>8} {'Regime':>16} {'信号数':>10} {'平均收益':>10} {'胜率':>8}")
for (th, rg) in sorted(thresh_regime.keys()):
    v = thresh_regime[(th, rg)].get(hd, [])
    if len(v) >= 20:
        print(f"{th:>8} {rg:>16} {len(v):>10,} {np.mean(v):>+9.2%} {np.mean([1 if x>0 else 0 for x in v]):>7.1%}")

# ========== 6. 结论 ==========
best_hd = max(HOLD_DAYS, key=lambda d: overall_all.get(f'T+{d}', {}).get('win_rate', 0))
print(f"\n{'─'*80}")
print(f"结论:")
print(f"  - 最佳持有期: T+{best_hd} ({overall_all[f'T+{best_hd}']['avg']:+.2%}, {overall_all[f'T+{best_hd}']['win_rate']:.1%})")
best_th = max(SMA_THRESHOLDS, key=lambda t: by_threshold.get(f'{t:.0%}', {}).get(f'T+{best_hd}', {}).get('avg', -999))
print(f"  - 最佳阈值: {best_th:.0%} ({by_threshold[f'{best_th:.0%}'][f'T+{best_hd}']['avg']:+.2%})")
best_sb = max(by_scorebin.keys(), key=lambda k: by_scorebin[k].get(f'T+{best_hd}', {}).get('avg', -999))
print(f"  - 最佳评分段: {best_sb} ({by_scorebin[best_sb][f'T+{best_hd}']['avg']:+.2%}, {by_scorebin[best_sb][f'T+{best_hd}']['win_rate']:.1%})")
print(f"{'─'*80}")

# ========== 7. 保存 ==========
os.makedirs(OUT, exist_ok=True)
fname = f'backtest_full_report_{date.today().isoformat()}.json'
fpath = os.path.join(OUT, fname)
with open(fpath, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=1, default=str)
print(f"\n报告保存: {fpath}")
print(f"{'='*80}")
