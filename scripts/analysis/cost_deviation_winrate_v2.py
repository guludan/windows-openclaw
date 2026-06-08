#!/usr/bin/env python3
"""
全市场成本偏离vs胜率 (2026-06-03)
=============================
直接从daily_prices扫描全市场V8信号，
标注每条的机构成本偏离度（无BVPS时用SMA120近似），
等样本分桶统计胜率。

更快的方式：直接用PG算，不逐只Python循环。
"""
import psycopg2, numpy as np, json, os, sys
from datetime import date, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT_DIR = '/home/tearsyu/.openclaw/workspace/memory/cost_deviation'


def find_institution_zones_batch(closes, volumes, start_idx, window=400):
    """
    从start_idx向后找放量横盘区
    只返回在start_idx之前结束的成本区
    
    返回: (weighted_cost or None)
    """
    if start_idx < 120:
        return None
    
    n = start_idx + 1
    closes_seg = closes[:n]
    volumes_seg = volumes[:n]
    dates_seg = None  # 不需要date
    
    # 快速扫描
    best_cost = None
    best_days = 0
    
    # 从最近往前扫
    scan_start = max(0, n - window)
    for s in range(n - 15, scan_start, -5):
        for e in range(min(n, s + 15), min(n, s + 60), 3):
            seg_c = closes_seg[s:e]
            seg_v = volumes_seg[s:e]
            days = e - s
            if days < 15: continue
            
            pr_high = np.max(seg_c)
            pr_low = np.min(seg_c)
            if pr_low <= 0 or pr_high / pr_low > 1.15: continue
            
            pre_vol = np.mean(volumes_seg[max(0, s-60):s]) if s >= 60 else np.mean(volumes_seg[:s])
            avg_v = np.mean(seg_v)
            vr = avg_v / pre_vol if pre_vol > 0 else 1.0
            if vr < 1.3: continue
            
            vol_cv = np.std(seg_v) / avg_v if avg_v > 0 else 99
            if vol_cv > 0.8: continue
            
            total_amt = np.sum(seg_c * seg_v * 100)
            total_sh = np.sum(seg_v) * 100
            cost = total_amt / total_sh if total_sh > 0 else np.mean(seg_c)
            
            # 记录最长建仓期的成本
            if days > best_days:
                best_days = days
                best_cost = float(cost)
            break  # 每个起点只取第一次满足
    
    return best_cost if best_days >= 15 else None


def run():
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # 1. 拉行业密度
    cur.execute("SELECT stock_code, bk_density_level FROM industry_components_ths_l1")
    density_map = {r[0]: r[1] for r in cur.fetchall()}
    print(f"行业密度: {len(density_map)}只", file=sys.stderr)
    
    # 2. 拉日线
    print("拉日线...", file=sys.stderr)
    cur.execute("""
        SELECT substring(symbol, 3), trade_date, close, volume 
        FROM daily_prices 
        WHERE trade_date >= '2020-01-01' AND close > 0
        ORDER BY symbol, trade_date
    """)
    all_rows = cur.fetchall()
    print(f"日线: {len(all_rows)}条", file=sys.stderr)
    
    # 按股票分组
    price_data = defaultdict(list)
    for code, td, c, v in all_rows:
        if c > 0:
            price_data[code].append((td, float(c), int(v) if v else 0))
    
    # 3. 只选有密度的 + 日线>=500天
    candidates = {}
    for code, rows in price_data.items():
        if len(rows) >= 500 and code in density_map:
            candidates[code] = rows
    print(f"候选: {len(candidates)}只", file=sys.stderr)
    
    # 4. 扫描信号
    all_signals = []
    count = 0
    for code, rows in candidates.items():
        count += 1
        if count % 500 == 0:
            print(f"  扫描 {count}/{len(candidates)} -> {len(all_signals)}信号", file=sys.stderr)
        
        dates = [r[0] for r in rows]
        closes = np.array([r[1] for r in rows], dtype=float)
        volumes = np.array([r[2] for r in rows], dtype=float)
        n = len(closes)
        
        density = density_map[code]
        
        for i in range(250, n):
            cur_close = float(closes[i])
            cut_date = dates[i]
            
            # SMA120
            sma120 = float(np.mean(closes[i-119:i+1]))
            sdev = cur_close / sma120 - 1
            
            # V8条件
            if sdev >= -0.14:
                continue
            
            tier = None
            if density == 'RARE' and sdev < -0.14:
                tier = 3
            elif density == 'NORMAL' and sdev < -0.18:
                tier = 2
            elif density == 'DENSE' and sdev < -0.25:
                tier = 1
            
            if not tier:
                continue
            
            # 量能过滤
            if i >= 60:
                avg60 = np.mean(volumes[i-59:i+1])
                avg_pre = np.mean(volumes[i-119:i-59])
                v_ratio = avg60 / avg_pre if avg_pre > 0 else 1
                if v_ratio < 0.5: continue
            
            # 机构成本
            cost = find_institution_zones_batch(closes, volumes, i)
            
            if cost and cost > 0:
                cost_dev = cur_close / cost - 1
            else:
                cost_dev = None  # 无机构成本区，跳过
            
            if cost_dev is None:
                continue
            
            # T+20
            t20_idx = i + 20
            if t20_idx < n:
                t20_ret = float(closes[t20_idx] / cur_close - 1)
            else:
                t20_ret = None
            
            if t20_ret is None:
                continue
            
            all_signals.append({
                'code': code,
                'date': str(cut_date),
                'close': round(float(cur_close), 2),
                'sdev': round(float(sdev*100), 1),
                'cost_dev': round(float(cost_dev*100), 1),
                't20': round(float(t20_ret*100), 1),
            })
    
    cur.close()
    conn.close()
    
    print(f"\n总信号: {len(all_signals)}", file=sys.stderr)
    
    # 5. 等样本分桶
    if len(all_signals) < 100:
        print("信号太少!", file=sys.stderr)
        return
    
    cost_devs = np.array([s['cost_dev'] for s in all_signals], dtype=float)
    t20s = np.array([s['t20'] for s in all_signals], dtype=float)
    
    n_bins = 12
    sorted_idx = np.argsort(cost_devs)
    bin_size = len(sorted_idx) // n_bins
    
    bins = []
    for bi in range(n_bins):
        start = bi * bin_size
        end = (bi + 1) * bin_size if bi < n_bins - 1 else len(sorted_idx)
        if start >= end: continue
        
        idxs = sorted_idx[start:end]
        b_cd = cost_devs[idxs]
        b_t20 = t20s[idxs]
        
        bins.append({
            'bin': bi + 1,
            'n': len(idxs),
            'cost_min': round(float(np.min(b_cd)), 1),
            'cost_max': round(float(np.max(b_cd)), 1),
            'cost_med': round(float(np.median(b_cd)), 1),
            't20_wr': round(float(np.mean(b_t20 > 0) * 100), 1),
            't20_avg': round(float(np.mean(b_t20)), 1),
            't20_med': round(float(np.median(b_t20)), 1),
            't20_extreme': round(float(np.mean(b_t20 < -15) * 100), 1),
        })
    
    # 6. 打印
    print(f"\n{'='*80}")
    print(f"  全市场机构成本偏离 vs T+20胜率 ({len(all_signals)}条信号)")
    print(f"{'='*80}")
    print(f"\n{'桶':>3} {'偏离区间':>14} {'中值':>7} {'T+20胜率':>8} {'T+20均值':>8} {'极端亏>15%':>10} {'样本':>6}")
    print(f"  {'-'*60}")
    for b in bins:
        print(f"  {b['bin']:>3} {b['cost_min']:>+7.1f}%~{b['cost_max']:>+5.1f}% {b['cost_med']:>+6.1f}% {b['t20_wr']:>7.1f}% {b['t20_avg']:>+7.1f}% {b['t20_extreme']:>9.1f}% {b['n']:>6}")
    
    # 7. 仓位映射
    print(f"\n→→→ 仓位映射规则:")
    print(f"{'偏离中值':>8} {'胜率':>6} {'极端亏':>8} {'仓位':>6}")
    for b in bins:
        wr = b['t20_wr']
        el = b['t20_extreme']
        if wr >= 70 and el < 3:
            w = 1.2
        elif wr >= 65 and el < 5:
            w = 1.0
        elif wr >= 60 and el < 8:
            w = 0.7
        elif wr >= 55:
            w = 0.4
        elif wr >= 50 and el < 12:
            w = 0.2
        else:
            w = 0.0
        print(f"  {b['cost_med']:>+6.1f}% {b['t20_wr']:>5.1f}% {b['t20_extreme']:>7.1f}% {w:>5.1f}x")
    
    # 8. 保存
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f'winrate_curve_{date.today()}.json')
    with open(out, 'w') as f:
        json.dump({'signal_count': len(all_signals), 'bins': bins}, f, ensure_ascii=False, indent=1)
    print(f"\n保存: {out}", file=sys.stderr)


if __name__ == '__main__':
    run()
