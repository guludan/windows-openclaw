#!/home/tearsyu/miniconda3/envs/tt/bin/python3
"""
热门板块大跌后反弹统计 + 见顶特征研究
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
from data_finance import _pg_execute
import json
from datetime import date, timedelta
from collections import defaultdict
import numpy as np

HOT_SECTORS = [
    '半导体', '电池', '证券', '白酒', '光伏设备',
    '汽车零部件', '软件开发', '通信设备', '消费电子',
    '医疗服务', '军工装备', '化学制品'
]

def calc_sector_returns(start='2020-01-01', end='2026-06-09'):
    print("[1] Load sector components...")
    rows, cols = _pg_execute("""
        SELECT industry, stock_code FROM industry_components_ths_l1
        WHERE industry = ANY(%s)
    """, (HOT_SECTORS,))
    
    sector_stocks = defaultdict(list)
    for r in rows:
        sector_stocks[r[0]].append('A.' + r[1])
    
    for sec, stks in sorted(sector_stocks.items()):
        print(f"  {sec}: {len(stks)} stocks")
    
    all_stocks = [s for stks in sector_stocks.values() for s in stks]
    
    print(f"\n[2] Load price data {start}~{end} ({len(all_stocks)} stocks)...")
    rows, cols = _pg_execute("""
        SELECT symbol, trade_date, close, volume, amount
        FROM daily_prices
        WHERE symbol = ANY(%s)
          AND trade_date >= %s AND trade_date <= %s
          AND close > 0
        ORDER BY trade_date, symbol
    """, (all_stocks, start, end))
    print(f"  Loaded {len(rows)} rows")
    
    # Organize
    stock_prices = defaultdict(lambda: defaultdict(float))
    stock_volumes = defaultdict(lambda: defaultdict(float))
    
    for r in rows:
        stock_prices[r[0]][r[1]] = r[2]
        stock_volumes[r[0]][r[1]] = r[3] if r[3] else 0
    
    # Calc sector equal-weight returns
    sector_returns = {}
    sector_vol_ratio = {}
    
    for sec in HOT_SECTORS:
        stks = [s for s in sector_stocks[sec] if s in stock_prices]
        all_dates = sorted(set(d for s in stks for d in stock_prices[s].keys()))
        
        rets = {}
        vratio = {}
        
        for i, dt in enumerate(all_dates):
            today_closes = [stock_prices[s].get(dt, 0) for s in stks]
            today_closes = [c for c in today_closes if c > 0]
            if i == 0 or len(today_closes) == 0:
                continue
            
            prev_dt = all_dates[i-1]
            prev_closes = [stock_prices[s].get(prev_dt, 0) for s in stks]
            prev_closes = [c for c in prev_closes if c > 0]
            if len(prev_closes) == 0:
                continue
            
            indiv_rets = [(tc - pc) / pc for tc, pc in zip(today_closes, prev_closes) if pc > 0]
            if indiv_rets:
                rets[dt] = float(np.mean(indiv_rets))
            
            # Volume ratio
            today_vol = sum(stock_volumes[s].get(dt, 0) for s in stks)
            lookback = all_dates[max(0, i-20):i]
            if lookback:
                avg_vol = float(np.mean([sum(stock_volumes[s].get(ld, 0) for s in stks) for ld in lookback]))
                vratio[dt] = today_vol / avg_vol if avg_vol > 0 else 1.0
        
        sector_returns[sec] = rets
        sector_vol_ratio[sec] = vratio
        print(f"  {sec}: {len(rets)} trading days")
    
    return sector_returns, sector_vol_ratio, sector_stocks


def analyze_crash_events(sector_returns, sector_vol_ratio):
    print(f"\n{'='*70}")
    print("PART 1: Hot Sector Crash Rebound Statistics")
    print(f"{'='*70}")
    
    all_results = []
    
    for sec in HOT_SECTORS[:6]:
        rets = sector_returns[sec]
        dates = sorted(rets.keys())
        print(f"\n--- {sec} ---")
        
        # Identify crash events
        crash_events = []
        for dt in dates:
            if rets[dt] < -0.03:
                crash_events.append((dt, 'single_day', rets[dt]))
        
        for i in range(2, len(dates)):
            cum3 = sum(rets[dates[j]] for j in range(i-2, i+1))
            if cum3 < -0.06:
                dt = dates[i]
                if not any(abs((dt - ce[0]).days) <= 2 for ce in crash_events):
                    crash_events.append((dt, 'cum3', cum3))
        
        print(f"  Crash events: {len(crash_events)}")
        
        for lookahead in [5, 10, 20, 40]:
            fwd_rets, wins = [], 0
            for crash_dt, ctype, crash_ret in crash_events:
                try:
                    idx = dates.index(crash_dt)
                except ValueError:
                    continue
                if idx + lookahead >= len(dates):
                    continue
                cum_ret = sum(rets[dates[j]] for j in range(idx+1, idx+lookahead+1))
                fwd_rets.append(cum_ret)
                if cum_ret > 0:
                    wins += 1
            if fwd_rets:
                wr = wins/len(fwd_rets)*100
                avg = np.mean(fwd_rets)*100
                med = np.median(fwd_rets)*100
                print(f"  T+{lookahead}: WR={wr:.0f}% avg={avg:+.1f}% med={med:+.1f}% (n={len(fwd_rets)})")
                all_results.append({'sector': sec, 'window': f'T+{lookahead}', 'win_rate': round(wr,1), 'avg_ret': round(avg,2), 'med_ret': round(med,2), 'n': len(fwd_rets)})
    return all_results


def analyze_peak_features(sector_returns, sector_vol_ratio):
    print(f"\n{'='*70}")
    print("PART 2: Top Pattern Analysis")
    print(f"{'='*70}")
    
    all_patterns = []
    
    for sec in HOT_SECTORS[:6]:
        rets = sector_returns[sec]
        vratio_dict = sector_vol_ratio.get(sec, {})
        dates = sorted(rets.keys())
        
        cum20 = {}
        for i in range(19, len(dates)):
            cum20[dates[i]] = sum(rets[dates[j]] for j in range(i-19, i+1))
        
        peaks = []
        for i in range(40, len(dates)):
            dt = dates[i]
            cur = cum20.get(dt, 0)
            window = [cum20.get(dates[j], 0) for j in range(i-10, i+11) if 0 <= j < len(dates)]
            if len(window) > 0 and cur >= max(window) and cur > 0.15:
                peaks.append((dt, cur, i))
        
        print(f"\n--- {sec}: {len(peaks)} peaks ---")
        
        for peak_dt, peak_val, peak_idx in peaks[:15]:
            if peak_idx + 20 >= len(dates):
                continue
            
            pre_features = []
            for j in range(max(0, peak_idx-9), peak_idx+1):
                dt = dates[j]
                pre_features.append({
                    'date': str(dt),
                    'ret': rets[dt],
                    'vol_ratio': vratio_dict.get(dt, 1.0),
                })
            
            post_rets = sum(rets[dates[j]] for j in range(peak_idx+1, min(peak_idx+21, len(dates))))
            avg_vr_before = float(np.mean([f['vol_ratio'] for f in pre_features[-5:]])) if pre_features else 1.0
            last_ret = pre_features[-1]['ret'] if pre_features else 0
            big_up_days = sum(1 for f in pre_features if f['ret'] > 0.03 and f['vol_ratio'] > 1.5)
            
            all_patterns.append({
                'sector': sec, 'peak_date': str(peak_dt),
                'peak_20d_cum': round(peak_val*100, 1),
                'post_20d_ret': round(post_rets*100, 1),
                'avg_vol_ratio_5d': round(avg_vr_before, 2),
                'last_day_ret': round(last_ret*100, 2),
                'big_up_vol_days_10d': big_up_days,
            })
    
    if not all_patterns:
        print("  No peaks found")
        return []
    
    print(f"\n  Total top events: {len(all_patterns)}")
    worse = [p for p in all_patterns if p['post_20d_ret'] < -5]
    flat = [p for p in all_patterns if -5 <= p['post_20d_ret'] <= 2]
    recover = [p for p in all_patterns if p['post_20d_ret'] > 2]
    print(f"  20d after peak: crash>5% {len(worse)} | flat {len(flat)} | recover {len(recover)}")
    
    print(f"\n  Feature comparison (avg):")
    for label, group in [('crash>5%', worse), ('flat', flat), ('recover', recover)]:
        if not group:
            continue
        print(f"    {label}(n={len(group)}): "
              f"vol_ratio_5d={np.mean([p['avg_vol_ratio_5d'] for p in group]):.2f} "
              f"last_ret={np.mean([p['last_day_ret'] for p in group]):+.1f}% "
              f"big_up_days={np.mean([p['big_up_vol_days_10d'] for p in group]):.1f} "
              f"peak_cum={np.mean([p['peak_20d_cum'] for p in group]):.1f}%")
    
    return all_patterns


def analyze_dump_patterns(sector_returns, sector_vol_ratio, sector_stocks):
    print(f"\n{'='*70}")
    print("PART 3: Dump Pattern Analysis")
    print(f"{'='*70}")
    
    for sec in HOT_SECTORS[:4]:
        rets = sector_returns[sec]
        vratio_dict = sector_vol_ratio.get(sec, {})
        dates = sorted(rets.keys())
        print(f"\n--- {sec} ---")
        
        # Pattern A: Stagnation with high volume -> future
        pattern_a = []
        for i in range(10, len(dates)-5):
            dt = dates[i]
            vr = vratio_dict.get(dt, 1.0)
            if vr > 1.5:
                pre5 = sum(rets[dates[j]] for j in range(i-4, i+1))
                if -0.03 < pre5 < 0.03:
                    fwd5 = sum(rets[dates[j]] for j in range(i+1, i+6))
                    pattern_a.append((str(dt), pre5*100, vr, fwd5*100))
        
        if pattern_a:
            fwd5s = [p[3] for p in pattern_a]
            win_a = sum(1 for p in pattern_a if p[3] > 0)
            print(f"  Pattern A(stagnation+high vol -> next5d): n={len(pattern_a)} WR={win_a/len(pattern_a)*100:.0f}% avg={np.mean(fwd5s):+.1f}%")
            worst = sorted(pattern_a, key=lambda x: x[3])[:3]
            print(f"    Worst 3: {[(w[0], round(w[3],1)) for w in worst]}")
        
        # Pattern B: 3 consecutive down days with volume -> future
        pattern_b = []
        for i in range(5, len(dates)-5):
            last3 = [rets[dates[j]] for j in range(i-2, i+1)]
            vr3 = [vratio_dict.get(dates[j], 1.0) for j in range(i-2, i+1)]
            if all(r < 0 for r in last3) and any(v > 1.3 for v in vr3):
                fwd5 = sum(rets[dates[j]] for j in range(i+1, i+6))
                pattern_b.append((str(dates[i]), sum(last3)*100, fwd5*100))
        
        if pattern_b:
            fwd5s = [p[2] for p in pattern_b]
            win_b = sum(1 for p in pattern_b if p[2] > 0)
            print(f"  Pattern B(3d dn+vol -> next5d): n={len(pattern_b)} WR={win_b/len(pattern_b)*100:.0f}% avg={np.mean(fwd5s):+.1f}%")


if __name__ == '__main__':
    sector_returns, sector_vol_ratio, sector_stocks = calc_sector_returns()
    results = analyze_crash_events(sector_returns, sector_vol_ratio)
    peak_results = analyze_peak_features(sector_returns, sector_vol_ratio)
    analyze_dump_patterns(sector_returns, sector_vol_ratio, sector_stocks)
    
    print(f"\n{'='*70}")
    print("SUMMARY TABLE: Crash Rebound")
    print(f"{'='*70}")
    print(f"{'Sector':<12} {'Window':<8} {'WR':<8} {'Avg%':<8} {'Med%':<8} {'n':<8}")
    print('-'*60)
    for r in results:
        print(f"{r['sector']:<12} {r['window']:<8} {r['win_rate']:<7.0f}% {r['avg_ret']:<+7.1f} {r['med_ret']:<+7.1f} {r['n']:<8}")
    print("\nDone!")
