#!/usr/bin/env python3
"""
庄家行为全市场扫描 v3
- 个股级历史回测（非全局平均）
- T+3/T+5/T+10/T+20 四个持有期
- 出货期主力被套自救逻辑
- 大盘白马优先排序
"""

import sys, os, json, time
import numpy as np
import psycopg2
from collections import defaultdict
from datetime import date, datetime, timedelta

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
MIN_FLOAT_MV = 10
MIN_HISTORY = 250  # 需要至少250根日线做个股历史回测
CACHE_EXPIRY = 10  # 个股历史信号缓存保留天数

# ========== 辅助计算 ==========

def calc_sma(series, n):
    return np.mean(series[-n:]) if len(series) >= n else np.mean(series)

def calc_vol_ratio(vols, short_n=10, long_n=60):
    short = np.mean(vols[-short_n:])
    long = np.mean(vols[-long_n:]) if len(vols) >= long_n else np.mean(vols)
    return short / max(long, 1)

def calc_shadow_count(highs, lows, opens, closes, lookback=10):
    cnt = 0
    for j in range(max(0, len(closes)-lookback), len(closes)):
        cr = highs[j] - lows[j]
        if cr <= 0:
            continue
        us = highs[j] - max(opens[j], closes[j])
        if us / cr > 0.4:
            cnt += 1
    return cnt

def detect_stage(closes, opens, highs, lows, vols):
    """检测当前庄家阶段，返回(stage, stage_severity)"""
    idx = len(closes) - 1
    if idx < 20:
        return "无主力特征", 0
    
    n = len(closes)
    yang = closes >= opens
    
    # 基础指标
    yang_20 = np.mean(yang[n-20:])
    yang_5 = np.sum(yang[n-5:])
    vol_10 = np.mean(vols[n-10:])
    vol_60 = np.mean(vols[max(0,n-60):n])
    vol_ratio_10_60 = vol_10 / max(vol_60, 1)
    vol_20 = np.mean(vols[n-20:])
    vol_ratio_today = vols[idx] / max(vol_20, 1)
    amp_20 = np.mean([(highs[j]-lows[j])/max(opens[j],0.01) for j in range(n-20, n)])
    shadow_count = calc_shadow_count(highs, lows, opens, closes, 10)
    r1d = closes[idx] / closes[idx-1] - 1 if idx > 0 else 0
    cr = highs[idx] - lows[idx]
    us = highs[idx] - max(opens[idx], closes[idx])
    ls = min(opens[idx], closes[idx]) - lows[idx]
    shadow_ratio = us / max(cr, 0.001)
    high_60 = np.max(highs[max(0,n-60):n])
    
    # 连跌
    conseq_dn = 0
    for k in range(idx, max(0, idx-5), -1):
        d = closes[k]/closes[k-1]-1 if k > 0 else 0
        if d < 0:
            conseq_dn += 1
        else:
            break
    
    past_5_vol = np.mean(vols[max(0, n-5):n])
    past_20_vol = np.mean(vols[max(0, n-20):n])
    vol_increase = past_5_vol / max(past_20_vol, 1)
    
    # 出货期（最高优先级）
    if vol_increase > 1.3 and shadow_count >= 3:
        return "出货期", 4
    
    # 试盘期
    if cr > 0 and shadow_ratio > 0.5 and us > ls and closes[idx] < opens[idx] and abs(r1d) < 0.05 and vol_ratio_today > 1.5 and us/max(closes[idx],0.01) > 0.03:
        return "试盘期", 3
    
    # 拉升期
    if yang_5 >= 3 and vol_ratio_10_60 > 1.1 and r1d > 0 and r1d < 0.105 and amp_20 > 0.035 and highs[idx] > high_60 * 0.95:
        return "拉升期", 2
    
    # 建仓期
    if yang_20 > 0.55 and 1.0 <= vol_ratio_10_60 <= 1.8 and amp_20 < 0.05:
        return "建仓期", 1
    
    # 洗盘期
    if conseq_dn >= 2 and r1d < -0.03 and vol_ratio_today < 0.7:
        return "洗盘期", 0
    
    return "无主力特征", 0

def v8_tier(sma_dev):
    if sma_dev < -0.30:
        return "S3深度", 1.4
    elif sma_dev < -0.25:
        return "S2重仓", 1.3
    elif sma_dev < -0.20:
        return "S1中度", 1.15
    elif sma_dev < -0.15:
        return "V8标准", 1.0
    elif sma_dev < -0.10:
        return "V8轻仓", 0.85
    return None, 0

STAGE_MULT = {'出货期': 1.4, '试盘期': 1.1, '建仓期': 1.0, '拉升期': 0.5, '洗盘期': 0.3, '无主力特征': 1.0}

# ========== 个股历史回测 ==========

def backtest_stock(closes, highs, lows, opens, vols, sma_dev_threshold=-0.10, min_samples=5):
    """
    对一只股票进行历史V8信号回测
    返回: {sma_dev区间: {T+3/T+5/T+10/T+20: (收益均值, 胜率, 样本数)}}
    """
    n = len(closes)
    if n < 120:
        return {}
    
    # 定义sma_dev回测区间
    thresholds = [(0, 'V8轻仓', -0.10, -0.05),
                  (1, 'V8标准', -0.15, -0.10),
                  (2, 'S1中度', -0.20, -0.15),
                  (3, 'S2重仓', -0.25, -0.20),
                  (4, 'S3深度', -1.0, -0.25)]
    
    # 收集每根K线的庄家阶段
    stages = []
    for i in range(119, n-20):  # 需要120根历史+20天未来
        cuts = closes[max(0,i-119):i+1]
        if len(cuts) < 60:
            continue
        sma120 = np.mean(cuts)
        sdev = closes[i] / max(sma120, 0.01) - 1
        
        # 检测当前阶段
        if i >= 119:
            st, _ = detect_stage(closes[:i+1], opens[:i+1], highs[:i+1], lows[:i+1], vols[:i+1])
        else:
            st = "无主力特征"
        
        stages.append((i, sdev, st))
    
    # 按区间汇总收益
    results = defaultdict(lambda: defaultdict(list))
    
    for i, sdev, stage in stages:
        if sdev >= -0.10:
            continue
        
        # 找对应的阈值区间
        for _, name, lo, hi in thresholds:
            if lo <= sdev < hi:
                tier = name
                break
        else:
            tier = "V8轻仓"
        
        for lookahead in [3, 5, 10, 20]:
            if i + lookahead >= n:
                continue
            ret = closes[i+lookahead] / closes[i] - 1
            results[tier][f'T+{lookahead}'].append(ret)
            # 细分按阶段
            results[f'{tier}_{stage}'][f'T+{lookahead}'].append(ret)
    
    # 汇总统计
    stats = {}
    for key, periods in results.items():
        stats[key] = {}
        for period, returns in periods.items():
            if len(returns) < min_samples:
                stats[key][period] = (0, 0, 0, len(returns))
                continue
            arr = np.array(returns)
            pct_50 = np.percentile(arr, 50)
            pct_25 = np.percentile(arr, 25)
            pct_75 = np.percentile(arr, 75)
            mean_ret = np.mean(arr)
            wr = np.mean(arr > 0) * 100
            stats[key][period] = (mean_ret, wr, len(returns), len(returns))
    
    return stats

# ========== 主函数 ==========

def scan(check_date=None):
    if check_date is None:
        check_date = date.today().isoformat()
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 庄家行为扫描 v3 开始 | {check_date}")
    print()
    
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # 获取regime
    cur.execute("SELECT extra FROM pool_energy WHERE level='market' AND date <= %s AND extra IS NOT NULL ORDER BY date DESC LIMIT 1", (check_date,))
    r = cur.fetchone()
    e = r[0] if r and isinstance(r[0], dict) else {}
    regime = e.get('regime', 'SIDEWAYS')
    surplus_z = e.get('surplus_z', 0)
    print(f"市场状态: {regime} | surplus_z={surplus_z:.3f}")
    
    # 全市场股票
    cur.execute("""
        SELECT s.code, s.name, m.pb, m.float_mv, m.pe, m.price, m.total_mv
        FROM stock_list s
        JOIN market_snapshots m ON s.code = m.code
        WHERE m.trade_date = (SELECT max(trade_date) FROM market_snapshots)
        AND m.float_mv >= %s AND m.pb > 0
        AND (s.code LIKE '6%%' OR s.code LIKE '0%%' OR s.code LIKE '3%%')
        AND s.code NOT LIKE '9%%'
    """, (MIN_FLOAT_MV,))
    stocks_raw = cur.fetchall()
    stocks = {r[0]: {'name':r[1],'pb':float(r[2]),'float_mv':float(r[3]),'pe':r[4],'close':float(r[5]),'total_mv':float(r[6])} for r in stocks_raw}
    print(f"全市场: {len(stocks)}只股票")
    
    # 批量拉取日线（加速）
    print("拉取日线数据...")
    batch_size = 100
    all_codes = list(stocks.keys())
    price_data = {}
    t0 = time.time()
    
    for batch_start in range(0, len(all_codes), batch_size):
        batch = all_codes[batch_start:batch_start+batch_size]
        symbols = [f"A.{c}" for c in batch]
        placeholders = ','.join(['%s'] * len(symbols))
        cur.execute(f"""
            SELECT symbol, trade_date, close, open, high, low, volume
            FROM daily_prices WHERE symbol IN ({placeholders}) AND close > 0
            AND trade_date >= '2018-01-01' AND trade_date <= %s
            ORDER BY symbol, trade_date
        """, symbols + [check_date])
        
        for sym, td, c, o, h, l, v in cur.fetchall():
            code = sym[2:]  # 去掉 A. 前缀
            if code not in price_data:
                price_data[code] = {'closes': [], 'opens': [], 'highs': [], 'lows': [], 'vols': []}
            price_data[code]['closes'].append(float(c))
            price_data[code]['opens'].append(float(o))
            price_data[code]['highs'].append(float(h))
            price_data[code]['lows'].append(float(l))
            price_data[code]['vols'].append(float(v))
        
        if (batch_start // batch_size) % 5 == 0:
            print(f"  拉取 {batch_start}/{len(all_codes)}... ({time.time()-t0:.1f}s)")
    
    print(f"日线拉取完成，耗时 {time.time()-t0:.1f}s")
    conn.close()
    
    # ========== 分析 ==========
    candidates = []
    total_v8 = 0
    stage_counts = defaultdict(int)
    
    for sc, info in stocks.items():
        pd = price_data.get(sc)
        if not pd or len(pd['closes']) < MIN_HISTORY:
            continue
        
        closes = np.array(pd['closes'])
        opens = np.array(pd['opens'])
        highs = np.array(pd['highs'])
        lows = np.array(pd['lows'])
        vols = np.array(pd['vols'])
        n = len(closes)
        
        # sma_dev
        sma120 = np.mean(closes[-120:])
        sma_dev = closes[-1] / max(sma120, 0.01) - 1
        
        tier, tier_w = v8_tier(sma_dev)
        if tier is None:
            continue  # 非V8
        
        total_v8 += 1
        
        # 当前庄家阶段
        stage, severity = detect_stage(closes, opens, highs, lows, vols)
        stage_counts[stage] += 1
        
        # 个股历史回测
        bt = backtest_stock(closes, highs, lows, opens, vols)
        
        # 获取该股在当前V8层级和历史阶段下的统计
        bt_key = f'{tier}_{stage}'
        bt_generic = tier
        
        def get_bt_stats(bt, key, period):
            stats = bt.get(key, {})
            if period in stats and stats[period][2] >= 5:
                return stats[period]
            stats2 = bt.get(bt_generic, {})
            if period in stats2 and stats2[period][2] >= 5:
                return stats2[period]
            return None
        
        def get_ret(ret_tuple):
            return ret_tuple[0] if ret_tuple else 0
        def get_wr(ret_tuple):
            return ret_tuple[1] if ret_tuple else 0
        def get_n(ret_tuple):
            return ret_tuple[2] if ret_tuple else 0
        
        t3 = get_bt_stats(bt, bt_key, 'T+3')
        t5 = get_bt_stats(bt, bt_key, 'T+5')
        t10 = get_bt_stats(bt, bt_key, 'T+10')
        t20 = get_bt_stats(bt, bt_key, 'T+20')
        
        stage_mult = STAGE_MULT.get(stage, 1.0)
        
        candidates.append({
            'code': sc,
            'name': info['name'],
            'close': closes[-1],
            'pb': info['pb'],
            'pe': info['pe'],
            'float_mv': info['float_mv'],
            'total_mv': info['total_mv'],
            'sma_dev': sma_dev,
            'v8_tier': tier,
            'stage': stage,
            'stage_mult': stage_mult,
            't3_ret': get_ret(t3),
            't3_wr': get_wr(t3),
            't3_n': get_n(t3),
            't5_ret': get_ret(t5),
            't5_wr': get_wr(t5),
            't5_n': get_n(t5),
            't10_ret': get_ret(t10),
            't10_wr': get_wr(t10),
            't10_n': get_n(t10),
            't20_ret': get_ret(t20),
            't20_wr': get_wr(t20),
            't20_n': get_n(t20),
            'bt_key': bt_key,
        })
    
    # ========== 排序 ==========
    tier_order = {'S3深度': 0, 'S2重仓': 1, 'S1中度': 2, 'V8标准': 3, 'V8轻仓': 4}
    stage_priority = {'出货期': 0, '试盘期': 1, '建仓期': 2, '洗盘期': 3, '无主力特征': 4}
    
    def sort_key(c):
        tr = tier_order.get(c['v8_tier'], 9)
        sp = stage_priority.get(c['stage'], 9)
        t20_ret = c['t20_ret']
        t20_wr = c['t20_wr']
        mv = c['total_mv']
        return (sp, tr, -t20_ret, -t20_wr, -mv)
    
    candidates.sort(key=sort_key)
    
    # ========== 输出 ==========
    print(f"\nV8信号: {total_v8}只 | 检测完毕: {len(candidates)}只候选")
    print(f"阶段分布: {', '.join([f'{s}:{c}' for s,c in sorted(stage_counts.items())])}")
    
    # 输出到文件
    today_str = check_date
    output_dir = f'/mnt/d/data/a-stock/reports/{today_str}'
    os.makedirs(output_dir, exist_ok=True)
    
    lines = []
    
    def pln(s=""):
        print(s)
        lines.append(s)
    
    pln(f"庄家行为扫描 v3 | {today_str} | {regime} surplus_z={surplus_z:.2f}")
    pln(f"V8信号: {total_v8}只 | 候选: {len(candidates)}只")
    pln(f"")

    # 各阶段输出
    for stage_name in ['出货期', '试盘期', '建仓期', '洗盘期', '无主力特征']:
        stage_cands = [c for c in candidates if c['stage'] == stage_name]
        if not stage_cands:
            continue
        
        tier_counts = defaultdict(int)
        for c in stage_cands:
            tier_counts[c['v8_tier']] += 1
        tier_str = ', '.join([f'{t}:{n}' for t,n in sorted(tier_counts.items(), key=lambda x: tier_order.get(x[0], 9))])
        
        pln(f"{'='*100}")
        pln(f"【{stage_name}】共 {len(stage_cands)}只 | {tier_str}")
        pln(f"{'='*100}")
        
        if stage_name in ['出货期', '试盘期', '建仓期']:
            # 只输出深跌的（S1+以上）
            display = [c for c in stage_cands if c['v8_tier'] in ['S3深度', 'S2重仓', 'S1中度']]
            if not display:
                # 加V8标准
                display = [c for c in stage_cands if c['v8_tier'] in ['V8标准']]
                if not display:
                    display = stage_cands[:10]  # 兜底
            else:
                # 按市值降序
                display.sort(key=lambda x: x['total_mv'], reverse=True)
                # 最多20只
                display = display[:30]
        else:
            display = stage_cands[:20]
        
        if not display:
            pln("  无")
            continue
        
        pln(f"{'代码':>8} {'名称':<10} {'V8':>8} {'sma_dev':>9} {'PB':>5} {'市值(亿)':>8} "
            f"{'T+3':>10} {'T+5':>10} {'T+10':>10} {'T+20':>10}")
        pln("-"*100)
        
        for c in display:
            def fmt_period(ret, wr, n):
                if n < 5:
                    return f'{"":>8}'
                return f'{ret:>+6.2%} ({wr:.0f}%/{n})'
            
            t3s = fmt_period(c['t3_ret'], c['t3_wr'], c['t3_n'])
            t5s = fmt_period(c['t5_ret'], c['t5_wr'], c['t5_n'])
            t10s = fmt_period(c['t10_ret'], c['t10_wr'], c['t10_n'])
            t20s = fmt_period(c['t20_ret'], c['t20_wr'], c['t20_n'])
            
            pln(f"{c['code']:>8} {c['name'][:8]:<10} {c['v8_tier']:>8} {c['sma_dev']:>+9.2%} "
                f"{c['pb']:>5.2f} {c['total_mv']:>8.0f}亿 {t3s} {t5s} {t10s} {t20s}")
        
        pln()
    
    # ========== 持仓专属 ==========
    hold_codes = ['002073', '600273']
    pln(f"{'='*60}")
    pln(f"【当前持仓】")
    pln(f"{'='*60}")
    for sc in hold_codes:
        c = next((c for c in candidates if c['code'] == sc), None)
        if not c:
            # 尝试从全V8列表找
            pln(f"{sc}: {'<'*10} 不在V8候选池中")
            continue
        def ff(ret, wr, n):
            return f'{ret:>+6.2%} (wr{wr:.0f}%, n={n})' if n >= 5 else f'{ret:>+6.2%} (样本{n})'
        pln(f"{c['code']} {c['name']} | {c['v8_tier']} {c['stage']} sma_dev={c['sma_dev']:.2%} PB={c['pb']:.2f}")
        pln(f"  T+3: {ff(c['t3_ret'], c['t3_wr'], c['t3_n'])}")
        pln(f"  T+5: {ff(c['t5_ret'], c['t5_wr'], c['t5_n'])}")
        pln(f"  T+10: {ff(c['t10_ret'], c['t10_wr'], c['t10_n'])}")
        pln(f"  T+20: {ff(c['t20_ret'], c['t20_wr'], c['t20_n'])}")
        pln()
    
    # ========== 保存 ==========
    report_path = f'{output_dir}/庄家行为监控_v3_{today_str}.md'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    print(f"\n报告已保存: {report_path}")
    print(f"总耗时: {time.time()-t0:.1f}s")
    
    return candidates

if __name__ == '__main__':
    import sys
    check_date = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    candidates = scan(check_date)
