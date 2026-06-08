#!/usr/bin/env python3
"""
成本偏离vs胜率分析 (2026-06-03)
========================
对个股的逐日滚动回测信号，标注每条的机构成本偏离度，
然后用等样本分桶统计每档的T+胜率。

用法:
  python3 cost_deviation_winrate.py --code 002073  # 软控
  python3 cost_deviation_winrate.py --code 600273  # 嘉化
  python3 cost_deviation_winrate.py --v8-candidates  # 全候选(RUN IN SCREEN)
"""
import sys, os, json, argparse
import psycopg2
import numpy as np
from datetime import date, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT_DIR = '/home/tearsyu/.openclaw/workspace/memory/cost_deviation'

# 拷贝机构成本区算法
def find_institution_zones(closes, volumes, dates, lookback=400):
    """找放量横盘区（机构建仓候选）"""
    n = len(closes)
    if n < 120:
        return []
    zones = []
    zone_ends = []
    start_max = n - 15
    start_min = max(0, n - lookback)
    
    for start in range(start_max, start_min, -5):
        for end in range(min(n, start + 15), min(n, start + 120), 3):
            seg_closes = closes[start:end]
            seg_volumes = volumes[start:end]
            seg_dates = dates[start:end]
            days = end - start
            if days < 15: continue
            
            price_high = np.max(seg_closes)
            price_low = np.min(seg_closes)
            if price_low <= 0 or price_high / price_low > 1.15: continue
            
            pre_vol = np.mean(volumes[max(0, start-60):start]) if start >= 60 else np.mean(volumes[:start])
            avg_vol = np.mean(seg_volumes)
            vol_ratio = avg_vol / pre_vol if pre_vol > 0 else 1.0
            if vol_ratio < 1.3: continue
            vol_cv = np.std(seg_volumes) / avg_vol if avg_vol > 0 else 99
            if vol_cv > 0.8: continue
            
            total_amount = np.sum(seg_closes * seg_volumes * 100)
            total_shares = np.sum(seg_volumes) * 100
            zone_price = total_amount / total_shares if total_shares > 0 else np.mean(seg_closes)
            
            overlap = False
            for zs, ze in zone_ends:
                if not (dates[end-1] < zs or dates[start] > ze):
                    overlap = True; break
            if overlap: continue
            
            zones.append({
                'start': str(seg_dates[0]), 'end': str(seg_dates[-1]), 'days': days,
                'cost_price': round(float(zone_price), 2),
            })
            zone_ends.append((dates[start], dates[end-1]))
            break
    
    zones.sort(key=lambda z: z['end'], reverse=True)
    return zones[:5]


def calc_weighted_cost_from_zones(zones, as_of_date=None):
    """计算截止某日期的加权成本（只用在该日期之前结束的成本区）"""
    if not zones:
        return None, []
    
    # 如果给定了as_of_date，只取在该日期之前的成本区
    if as_of_date:
        as_of_str = as_of_date.isoformat() if hasattr(as_of_date, 'isoformat') else str(as_of_date)
        # 对每只信号，只使用end<=信号触发日期的成本区
        # zones已经按时间排序（近到远），过滤
        pass
    
    # 加权平均（按天数）
    total_days = sum(z['days'] for z in zones)
    if total_days == 0:
        return None, zones
    weighted = sum(z['cost_price'] * z['days'] for z in zones) / total_days
    return round(weighted, 2), zones


def get_rolling_signals(code, cur, target_date=None):
    """从PG读取逐日滚动回测结果（没有就现场跑）"""
    if target_date is None:
        cur.execute("SELECT MAX(trade_date) FROM daily_prices")
        target_date = cur.fetchone()[0]
    
    # 检查是否有缓存结果
    # 若没有，现场计算
    symbol = 'A.' + code
    
    # 拉全量日线
    cur.execute("""
        SELECT trade_date, close, open, high, low, volume
        FROM daily_prices WHERE symbol=%s AND close > 0
        ORDER BY trade_date
    """, (symbol,))
    rows = cur.fetchall()
    if not rows or len(rows) < 250:
        return None, f"数据不足({len(rows)}天)"
    
    dates = [r[0] for r in rows]
    closes = np.array([float(r[1]) for r in rows], dtype=float)
    volumes = np.array([float(r[4]) if r[4] else 0 for r in rows], dtype=float)
    n = len(closes)
    
    # 拉regime
    cur2 = cur.connection.cursor()
    cur2.execute("SELECT date, extra FROM pool_energy WHERE level='market' AND extra IS NOT NULL ORDER BY date")
    regime_map = {}
    for td, e in cur2.fetchall():
        if isinstance(e, dict):
            regime_map[td.isoformat()] = {'regime': e.get('regime', 'UNK'), 'sz': e.get('surplus_z', 0)}
    def get_regime(td_str):
        best = None
        for k, v in sorted(regime_map.items()):
            if k <= td_str: best = v
            else: break
        return best or {'regime': 'UNK', 'sz': 0}
    
    # 逐日滚动回测（简化版，只算V8条件）
    signals = []
    min_lookback = 250
    
    for i in range(min_lookback, n):
        cut_date = dates[i]
        cur_close = float(closes[i])
        
        # V8入场条件
        # SMA120 & 偏离
        seg120 = closes[i-119:i+1]
        seg60 = closes[i-59:i+1]
        sma120 = np.mean(seg120)
        sma60 = np.mean(seg60)
        sdev = cur_close / sma120 - 1
        
        # PB (从financial_data读)
        cur.execute("SELECT bvps FROM financial_data WHERE symbol=%s AND end_date<=%s AND bvps>0 ORDER BY end_date DESC LIMIT 1", (symbol, cut_date))
        bvps_r = cur.fetchone()
        bvps = float(bvps_r[0]) if bvps_r else 0
        pb = cur_close / bvps if bvps > 0 else 999
        
        # 行业密度（最新）
        cur.execute("SELECT bk_density_level FROM industry_components_ths_l1 WHERE stock_code=%s", (code,))
        dens_r = cur.fetchone()
        density = dens_r[0] if dens_r else 'NORMAL'
        
        # V8条件
        tier = None
        if sdev < -0.14 and pb < 2.0:
            if density == 'RARE':
                tier = 3
            elif density == 'NORMAL' and sdev < -0.18:
                tier = 2
            elif density == 'DENSE' and sdev < -0.25:
                tier = 1
        
        if not tier:
            continue
        
        # 量能过滤
        if i >= 60:
            avg_vol_60 = np.mean(volumes[i-59:i+1])
            avg_vol_pre60 = np.mean(volumes[i-119:i-59])
            vol_ratio = avg_vol_60 / avg_vol_pre60 if avg_vol_pre60 > 0 else 1
            if vol_ratio < 0.5:
                continue
        
        # 检查未来收益
        def get_future_ret(days_ahead):
            j = i + days_ahead
            if j < n:
                return float(closes[j] / cur_close - 1)
            return None
        
        reg = get_regime(cut_date.isoformat())
        
        # — 关键步骤：算截至当日的机构成本 —
        # 只用截止到i(当天)的日线数据，模拟当时可获取的信息
        past_closes = closes[:i+1]
        past_volumes = volumes[:i+1]
        past_dates = dates[:i+1]
        zones = find_institution_zones(past_closes, past_volumes, past_dates, lookback=400)
        if zones:
            # 只取在cut_date之前结束的成本区
            valid_zones = [z for z in zones if z['end'] <= str(cut_date)]
            if valid_zones:
                total_d = sum(z['days'] for z in valid_zones)
                if total_d > 0:
                    weighted_cost = sum(z['cost_price'] * z['days'] for z in valid_zones) / total_d
                else:
                    weighted_cost = None
            else:
                weighted_cost = None
        else:
            weighted_cost = None
        
        # 偏离度（相对机构成本 vs SMA120双记录）
        cost_dev = cur_close / weighted_cost - 1 if weighted_cost and weighted_cost > 0 else None
        
        t3 = get_future_ret(3)
        t5 = get_future_ret(5)
        t10 = get_future_ret(10)
        t20 = get_future_ret(20)
        
        signals.append({
            'date': str(cut_date),
            'close': round(float(cur_close), 2),
            'sdev': round(float(sdev*100), 1),
            'pb': round(float(pb), 2),
            'density': density,
            'tier': tier,
            'regime': reg['regime'],
            'sz': round(reg['sz'], 1),
            'weighted_cost': round(float(weighted_cost), 2) if weighted_cost else None,
            'cost_dev': round(float(cost_dev*100), 1) if cost_dev is not None else None,
            't3': round(float(t3*100), 1) if t3 is not None else None,
            't5': round(float(t5*100), 1) if t5 is not None else None,
            't10': round(float(t10*100), 1) if t10 is not None else None,
            't20': round(float(t20*100), 1) if t20 is not None else None,
        })
    
    return signals, None


def generate_summary(signals, code, name=''):
    """等样本分桶 + 胜率统计"""
    if not signals or len(signals) < 30:
        return None
    
    # 只取有cost_dev的信号
    valid = [s for s in signals if s['cost_dev'] is not None and s['t20'] is not None]
    if len(valid) < 20:
        return None
    
    cost_devs = np.array([s['cost_dev'] for s in valid], dtype=float)
    t20s = np.array([s['t20'] for s in valid], dtype=float)
    
    # ===== 等样本分桶（按cost_dev排序后N等分）=====
    n_bins = 10
    if len(valid) < n_bins * 5:
        n_bins = max(5, len(valid) // 5)
    
    sorted_idx = np.argsort(cost_devs)
    bin_size = len(sorted_idx) // n_bins
    
    bins = []
    for bi in range(n_bins):
        start = bi * bin_size
        end = (bi + 1) * bin_size if bi < n_bins - 1 else len(sorted_idx)
        if start >= end:
            continue
        
        idxs = sorted_idx[start:end]
        b_cost_devs = cost_devs[idxs]
        b_t20s = t20s[idxs]
        
        cost_min = np.min(b_cost_devs)
        cost_max = np.max(b_cost_devs)
        cost_med = np.median(b_cost_devs)
        wr = np.mean(b_t20s > 0) * 100
        avg_ret = np.mean(b_t20s)
        med_ret = np.median(b_t20s)
        extreme_loss = np.mean(b_t20s < -15) * 100
        
        # 也收集t5/t10
        t5s = np.array([s['t5'] for s in [valid[i] for i in idxs] if s['t5'] is not None])
        t10s = np.array([s['t10'] for s in [valid[i] for i in idxs] if s['t10'] is not None])
        
        bins.append({
            'bin': bi + 1,
            'n': len(idxs),
            'cost_dev_min': round(float(cost_min), 1),
            'cost_dev_max': round(float(cost_max), 1),
            'cost_dev_med': round(float(cost_med), 1),
            't20_winrate': round(float(wr), 1),
            't20_avg_ret': round(float(avg_ret*100), 1),
            't20_med_ret': round(float(med_ret*100), 1),
            't20_extreme_loss_pct': round(float(extreme_loss), 1),
            't5_winrate': round(float(np.mean(t5s > 0)*100), 1) if len(t5s) > 0 else None,
            't10_winrate': round(float(np.mean(t10s > 0)*100), 1) if len(t10s) > 0 else None,
        })
    
    # 按regime分层
    regime_groups = defaultdict(list)
    for s in valid:
        regime_groups[s['regime']].append(s)
    
    regime_stats = {}
    for regime, vs in regime_groups.items():
        if len(vs) < 10:
            continue
        t20s_r = np.array([s['t20'] for s in vs if s['t20'] is not None])
        regime_stats[regime] = {
            'n': len(t20s_r),
            't20_winrate': round(float(np.mean(t20s_r > 0)*100), 1),
            't20_avg_ret': round(float(np.mean(t20s_r)*100), 1),
        }
    
    return {
        'code': code,
        'name': name,
        'total_signals': len(valid),
        'n_bins': n_bins,
        'bins': bins,
        'regime_stats': regime_stats,
        'overall_t20_winrate': round(float(np.mean(t20s > 0)*100), 1),
        'overall_t20_avg': round(float(np.mean(t20s)*100), 1),
    }


def print_report(summary):
    """打印分析报告"""
    if not summary:
        print("数据不足")
        return
    
    s = summary
    print(f"\n{'='*80}")
    print(f"  {s['code']} {s['name']} — 机构成本偏离 vs 胜率曲线")
    print(f"  信号总数: {s['total_signals']} | 总体T+20胜率: {s['overall_t20_winrate']}%")
    print(f"{'='*80}")
    
    print(f"\n  等样本分桶 ({s['n_bins']}桶, ~{s['total_signals']//s['n_bins']}/桶):")
    print(f"  {'桶':>3} {'偏离区间':>12} {'偏离中值':>8} {'T+20胜率':>8} {'T+5胜率':>8} {'T+10胜率':>8} {'T+20均值':>8} {'极端亏>15%':>10}")
    print(f"  {'-'*70}")
    for b in s['bins']:
        print(f"  {b['bin']:>3} {b['cost_dev_min']:>+.1f}~{b['cost_dev_max']:>+.1f}% {b['cost_dev_med']:>+7.1f}% {b['t20_winrate']:>7.1f}% {b.get('t5_winrate','  N/A'):>8} {b.get('t10_winrate','  N/A'):>8} {b['t20_avg_ret']:>+7.1f}% {b['t20_extreme_loss_pct']:>9.1f}%")
    
    print(f"\n  Regime分层:")
    for regime, st in sorted(s['regime_stats'].items(), key=lambda x: x[1]['n'], reverse=True):
        print(f"    {regime:<15s} n={st['n']:>4} T+20胜率={st['t20_winrate']:>5.1f}% 均值={st['t20_avg_ret']:>+6.1f}%")
    
    # 最佳区间
    best = max(s['bins'], key=lambda b: b['t20_winrate'])
    worst = min(s['bins'], key=lambda b: b['t20_winrate'])
    print(f"\n  最佳入场区间: {best['cost_dev_min']:+.1f}%~{best['cost_dev_max']:+.1f}% (胜率{best['t20_winrate']:.1f}%)")
    print(f"  最差入场区间: {worst['cost_dev_min']:+.1f}%~{worst['cost_dev_max']:+.1f}% (胜率{worst['t20_winrate']:.1f}%)")


def print_compact(signals, code, name=''):
    """紧凑表格输出"""
    summary = generate_summary(signals, code, name)
    print_report(summary)
    
    # 仓位映射建议
    if summary and summary['bins']:
        print(f"\n  →→→ 仓位映射规则（基准仓位=1.0，由逐日滚动回测校准）:")
        for b in summary['bins']:
            wr = b['t20_winrate']
            el = b['t20_extreme_loss_pct']
            
            if wr >= 70 and el < 3:
                weight = 1.2
            elif wr >= 65 and el < 5:
                weight = 1.0
            elif wr >= 60 and el < 8:
                weight = 0.7
            elif wr >= 55:
                weight = 0.4
            else:
                weight = 0.0
            
            print(f"     成本偏离 [{b['cost_dev_min']:+.1f}%~{b['cost_dev_max']:+.1f}%] 胜率{wr:.0f}% 极端亏损{el:.1f}% → 仓位{weight:.1f}x")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='成本偏离vs胜率分析')
    parser.add_argument('--code', type=str, default='002073')
    parser.add_argument('--save', action='store_true')
    args = parser.parse_args()
    
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    cur.execute("SELECT MAX(trade_date) FROM daily_prices")
    td = cur.fetchone()[0]
    
    cur.execute("SELECT stock_name FROM stock_industry WHERE stock_code=%s", (args.code,))
    nr = cur.fetchone()
    name = nr[0] if nr else ''
    
    print(f"\n正在跑 {args.code} {name} 的逐日滚动回测（含机构成本偏离度）...", file=sys.stderr)
    signals, err = get_rolling_signals(args.code, cur, td)
    
    if err:
        print(f"❌ {err}")
    elif signals:
        print(f"✅ 获取 {len(signals)} 条信号，其中有cost_dev的 {sum(1 for s in signals if s['cost_dev'] is not None)} 条", file=sys.stderr)
        print_compact(signals, args.code, name)
        
        if args.save:
            if not os.path.exists(OUT_DIR):
                os.makedirs(OUT_DIR, exist_ok=True)
            path = os.path.join(OUT_DIR, f'{args.code}_{td}.json')
            with open(path, 'w') as f:
                json.dump({
                    'code': args.code,
                    'name': name,
                    'signals': signals,
                }, f, ensure_ascii=False, indent=1)
            print(f"\n缓存: {path}", file=sys.stderr)
    else:
        print("❌ 未触发信号")
    
    conn.close()
