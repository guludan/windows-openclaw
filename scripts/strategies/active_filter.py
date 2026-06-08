#!/usr/bin/env python3
"""
主力活跃度前置过滤器 + 庄家成本分析 + 支撑位/托底质量评估

第一层 — 活跃度筛选（V8前置门）:
1. 量级门槛: 日均换手>2% 且 日均成交额>3000万
2. 放量脉冲: 60d内至少1次单日量 > 20日均量×2.5
3. 快速修复: 放量下跌后5d内回到放量前90%以上的次数>0

第二层 — 庄家成本推算:
- 底部缩量区间VWAP（量比<0.7的交易日）作为庄家建仓成本
- 放量脉冲仅用于区分派发型vs建仓型，不直接用其价格算成本

第三层 — 支撑位/压力位识别 + 托底类型判断:
- 底部是否逐步抬高来区分庄家护盘 vs 散户自发
- 成交量加权价格区间（Volume Profile）
- 触底反弹力度 vs 触碰次数

用法:
  python3 active_filter.py [--code 600635] [--date YYYY-MM-DD]
  python3 active_filter.py --scan
  python3 active_filter.py --backtest
"""
import sys, json, argparse
sys.path.insert(0, '.')
import psycopg2
import numpy as np
from datetime import date, datetime, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

# 阈值
THRESHOLD = {
    'turnover_avg': 2.0,       # 日均换手率% > 2%
    'amount_avg': 3000 * 1e4,  # 日均成交额 > 3000万
    'volume_ratio': 2.5,       # 单日量 > 20日均量×2.5
    'min_pulses': 1,           # 最少脉冲次数
    'repair_days': 5,          # 修复窗口
    'repair_ratio': 0.90,      # 修复到放量前90%
    'lookback': 60,            # 回看天数
}


def compute_cost_levels_entropy(conn, symbol, target_date, lookback=100):
    """
    自适应熵法推算庄家真实建仓成本（v3 核心算法）
    
    原理:
    - 庄家建仓时量价关系从无序(散户盘)变为有序(主力盘)
    - 价格-成交量联合分布的香农熵在吸筹区显著下降
    - 熵值越低 → 量平价稳 → 越可能是主力吸筹区
    - 3σ异常检测识别"熵值异常低"的核心吸筹时间窗
    
    返回:
    - true_cost: 核心建仓成本（庄家真实持仓成本）
    - low_safe_zone: 低吸安全区（历史地量区）
    - entropy_series: 熵值序列（用于诊断）
    - accumulation_zones: 识别出的吸筹区段
    """
    cur = conn.cursor()
    
    cur.execute("""
        SELECT trade_date, close, volume::numeric/1e4, high, low, COALESCE(amount::numeric/1e8,0)
        FROM daily_prices
        WHERE symbol=%s AND trade_date<=%s AND close>0 AND volume>0
        ORDER BY trade_date DESC
        LIMIT %s
    """, (symbol, target_date, lookback + 40))
    rows = cur.fetchall()
    n = len(rows)
    if n < 60:
        return None
    
    rows.reverse()
    dates = np.array([r[0] for r in rows])
    closes = np.array([float(r[1]) for r in rows])
    volumes_wan = np.array([float(r[2]) for r in rows])
    highs = np.array([float(r[3]) for r in rows])
    lows = np.array([float(r[4]) for r in rows])
    amounts_yi = np.array([float(r[5]) for r in rows])
    
    # --- Step 1: 滑窗VWAP + 窗口内价量概率密度估计 ---
    WINDOW = 20  # 滑窗大小（交易日）
    entropy_series = []
    window_vwaps = []
    
    for i in range(WINDOW, n):
        chunk_close = closes[i-WINDOW:i]
        chunk_vol = volumes_wan[i-WINDOW:i]
        chunk_high = highs[i-WINDOW:i]
        chunk_low = lows[i-WINDOW:i]
        
        # 窗口内每日VWAP ≈ (high+low)/2
        chunk_vwap = (chunk_high + chunk_low) / 2
        window_vwaps.append(chunk_vwap[-1])  # 存窗口最后一个VWAP
        
        # --- 信息熵: 价格-成交量联合分布 ---
        # 把价格分成10个分箱，量做归一化后作为概率权重
        price_bins = np.linspace(chunk_close.min(), chunk_close.max(), 10)
        if price_bins[-1] == price_bins[0]:
            price_bins = np.linspace(price_bins[0] * 0.99, price_bins[0] * 1.01, 10)
        
        total_vol = chunk_vol.sum()
        if total_vol == 0:
            entropy_series.append(0)
            continue
        
        probs = []
        for j in range(len(price_bins) - 1):
            lo, hi = price_bins[j], price_bins[j+1]
            mask = (chunk_vwap >= lo) & (chunk_vwap < hi)
            vol_in_bin = chunk_vol[mask].sum()
            probs.append(vol_in_bin / total_vol)
        
        probs = np.array(probs)
        probs = probs[probs > 0]  # 去掉0概率
        if len(probs) == 0:
            entropy_series.append(0)
            continue
        
        # 香农熵
        h = -np.sum(probs * np.log2(probs))
        # 归一化到[0,1]: 最大熵≈log2(10)=3.32
        h_norm = h / np.log2(len(price_bins) - 1) if len(price_bins) > 1 else 0
        entropy_series.append(h_norm)
    
    entropy_series = np.array(entropy_series)
    window_vwaps = np.array(window_vwaps)
    entropy_dates = dates[WINDOW:]
    
    # --- Step 2: 3σ异常检测 → 识别吸筹区 ---
    mu_e = np.mean(entropy_series)
    sigma_e = np.std(entropy_series)
    if sigma_e < 0.01:
        sigma_e = 0.01  # 防除零
    
    z_scores = (entropy_series - mu_e) / sigma_e
    
    # 熵值异常低 = z-score < -1.5（放宽到1.5σ，更敏感）
    accum_mask = z_scores < -1.5
    
    # 合并连续段
    accum_zones = []
    in_zone = False
    zone_start = 0
    for idx in range(len(accum_mask)):
        if accum_mask[idx] and not in_zone:
            zone_start = idx
            in_zone = True
        elif not accum_mask[idx] and in_zone:
            if idx - zone_start >= 3:  # 至少连续3天
                accum_zones.append((int(zone_start), int(idx)))
            in_zone = False
    if in_zone and len(accum_mask) - zone_start >= 3:
        accum_zones.append((int(zone_start), len(accum_mask)))
    
    # --- Step 3: 从吸筹区推算建仓成本 ---
    accum_prices = []
    accum_volumes = []
    accum_dates = []
    for zs, ze in accum_zones:
        for idx in range(zs, ze):
            original_idx = WINDOW + idx  # 对应原始数据索引
            if original_idx < n:
                accum_prices.append(window_vwaps[idx])
                accum_volumes.append(volumes_wan[original_idx])
                accum_dates.append(dates[original_idx])
    
    true_cost = None
    if accum_prices:
        total_v = sum(accum_volumes)
        if total_v > 0:
            true_cost = sum(p * v for p, v in zip(accum_prices, accum_volumes)) / total_v
    
    # --- Step 4: 低吸安全区（历史极低量区 = 散户绝望割肉区） ---
    entire_vol_mean = np.mean(volumes_wan)
    entire_vol_std = np.std(volumes_wan)
    if entire_vol_std < 1:
        entire_vol_std = 1
    extreme_low_vol = entire_vol_mean - 1.5 * entire_vol_std
    if extreme_low_vol < entire_vol_mean * 0.15:
        extreme_low_vol = entire_vol_mean * 0.15
    
    low_vol_days = []
    for i in range(WINDOW, n):
        if volumes_wan[i] < extreme_low_vol and volumes_wan[i] > 0:
            avg_p = (highs[i] + lows[i]) / 2
            low_vol_days.append((avg_p, volumes_wan[i], dates[i]))
    
    low_safe_zone = None
    low_safe_zone_vol = None
    if low_vol_days:
        sorted_lv = sorted(low_vol_days, key=lambda x: x[1])
        top_lv = sorted_lv[:min(10, len(sorted_lv))]
        total_lv = sum(v for _, v, _ in top_lv)
        if total_lv > 0:
            low_safe_zone = sum(p * v for p, v, _ in top_lv) / total_lv
            low_safe_zone_vol = sum(v for _, v, _ in top_lv) / len(top_lv) if top_lv else 0
    
    if low_safe_zone is None:
        recent_lows = []
        for i in range(n - 20, n):
            if volumes_wan[i] > 0:
                recent_lows.append(((highs[i] + lows[i]) / 2, volumes_wan[i], dates[i], lows[i]))
        if recent_lows:
            sorted_rl = sorted(recent_lows, key=lambda x: x[1])
            top_rl = sorted_rl[:min(5, len(sorted_rl))]
            total_rl = sum(v for _, v, _, _ in top_rl)
            if total_rl > 0:
                low_safe_zone = sum(p * v for p, v, _, _ in top_rl) / total_rl
                low_safe_zone_vol = sum(v for _, v, _, _ in top_rl) / len(top_rl) if top_rl else 0
    
    # --- Step 5: 熵值汇总信息 ---
    recent_entropy = list(zip(entropy_dates[-20:], entropy_series[-20:].round(3), z_scores[-20:].round(2)))
    
    return {
        'true_cost_vwap': round(true_cost, 2) if true_cost else None,
        'low_safe_zone': round(low_safe_zone, 2) if low_safe_zone else None,
        'low_safe_zone_vol_avg': round(low_safe_zone_vol, 0) if low_safe_zone_vol else None,
        'entropy_stats': {
            'mean': round(mu_e, 3),
            'std': round(sigma_e, 3),
            'min': round(entropy_series.min(), 3),
            'min_date': str(entropy_dates[np.argmin(entropy_series)]) if len(entropy_series) > 0 else None,
            'current': round(entropy_series[-1], 3),
            'current_z': round(z_scores[-1], 2),
        },
        'accumulation_zones': [
            {'start': str(entropy_dates[zs]), 'end': str(entropy_dates[ze-1]) if ze < len(entropy_dates) else str(entropy_dates[-1]),
             'days': ze - zs}
            for zs, ze in accum_zones
        ],
        'recent_entropy': recent_entropy,
    }



def compute_cost_levels(conn, symbol, target_date, lookback=120):
    """
    推算庄家成本位 + 支撑压力位 + 托底质量评估
    
    修正(2026-06-05):
    - 庄家成本用底部缩量区间VWAP，而非放量脉冲法（庄家建仓不惊动市场）
    - 放量脉冲仅用于区分建仓型 vs 派发型
    - 支撑位增加"底部逐步抬高"验证，区分庄家护盘 vs 散户自发
    - 新增自适应熵法(compute_cost_levels_entropy)作为v3核心
    """
    cur = conn.cursor()
    
    # 取 lookback 天数据
    cur.execute("""
        SELECT trade_date, close, volume::numeric/1e4, COALESCE(turnover,0), high, low, COALESCE(amount::numeric/1e8,0)
        FROM daily_prices
        WHERE symbol=%s AND trade_date<=%s AND close>0 AND volume>0
        ORDER BY trade_date DESC
        LIMIT %s
    """, (symbol, target_date, lookback + 20))
    rows = cur.fetchall()
    if len(rows) < 30:
        return None
    
    rows.reverse()
    dates = [r[0] for r in rows]
    closes = np.array([float(r[1]) for r in rows])
    volumes_wan = np.array([float(r[2]) for r in rows])
    turnovert = np.array([r[3] if r[3] else 0 for r in rows])
    highs = np.array([float(r[4]) for r in rows])
    lows = np.array([float(r[5]) for r in rows])
    amounts_yi = np.array([float(r[6]) for r in rows])
    n = len(rows)
    
    # ===== 自适应熵法成本分析 =====
    entropy_result = compute_cost_levels_entropy(conn, symbol, target_date, lookback=100)
    
    # ===== 后续继续原有缩量VWAP分析作为补充 =====
    cumvol = np.cumsum(volumes_wan)
    ma20_vol = np.full(n, np.nan)
    ma20_vol[19] = cumvol[19]/20
    ma20_vol[20:] = (cumvol[20:] - cumvol[:n-20]) / 20
    
    recent_n = n
    lookback_days = min(80, n)
    start_idx = n - lookback_days if n > lookback_days else 20
    
    silent_days = []
    for i in range(max(20, start_idx), n):
        if not np.isnan(ma20_vol[i]) and volumes_wan[i] < ma20_vol[i] * 0.7:
            avg_price = (highs[i] + lows[i]) / 2
            silent_days.append({
                'date': dates[i],
                'price': avg_price,
                'volume': volumes_wan[i],
                'vol_ratio': float(volumes_wan[i] / ma20_vol[i]),
                'close': float(closes[i]),
            })
    
    cost_vwap_full = None
    cost_vwap_recent = None
    cost_days_count = 0
    cost_recent_count = 0
    if silent_days:
        sorted_silent = sorted(silent_days, key=lambda x: x['vol_ratio'])
        top_silent = sorted_silent[:min(30, len(sorted_silent))]
        weighted_sum = sum(d['price'] * d['volume'] for d in top_silent)
        total_vol = sum(d['volume'] for d in top_silent)
        if total_vol > 0:
            cost_vwap_full = weighted_sum / total_vol
            cost_days_count = len(top_silent)
        
        recent_silent = [d for d in silent_days if d['date'] >= dates[start_idx]]
        if recent_silent:
            sorted_recent = sorted(recent_silent, key=lambda x: x['vol_ratio'])
            top_recent = sorted_recent[:min(20, len(sorted_recent))]
            r_sum = sum(d['price'] * d['volume'] for d in top_recent)
            r_vol = sum(d['volume'] for d in top_recent)
            if r_vol > 0:
                cost_vwap_recent = r_sum / r_vol
                cost_recent_count = len(top_recent)
    
    # --- 放量脉冲分析（仅用于区分建仓/派发，不用于算成本） ---
    pulse_events = []
    for i in range(20, n):
        if not np.isnan(ma20_vol[i]) and volumes_wan[i] > ma20_vol[i] * 2.0:
            avg_price = (highs[i] + lows[i]) / 2
            post_prices = closes[i+1:min(i+11, n)]
            if len(post_prices) >= 5:
                post_avg = np.mean(post_prices)
                pulse_type = 'DISTRIBUTE' if post_avg < closes[i] * 0.98 else 'ACCUMULATE'
            else:
                pulse_type = 'PENDING'
            
            pulse_events.append({
                'date': str(dates[i]),
                'price': float(closes[i]),
                'avg_price': float(avg_price),
                'volume_wan': float(volumes_wan[i]),
                'vol_ratio': float(volumes_wan[i] / ma20_vol[i]),
                'amount_yi': float(amounts_yi[i]),
                'type': pulse_type,
            })
    
    # --- 支撑位/压力位 Volume Profile 分析 ---
    # 用最近60个交易日，按价格区间统计成交量
    recent_n = min(60, n)
    price_bins = defaultdict(float)
    for i in range(n - recent_n, n):
        # 每根K线的成交量按比例分配到高-低区间
        if highs[i] > lows[i]:
            # 粗略：分配一半量到阳线区域，一半到阴线区域
            avg_p = (highs[i] + lows[i]) / 2
            bin_key = round(avg_p * 2) / 2  # 0.5元粒度
            price_bins[bin_key] += volumes_wan[i]
    
    # 找出量最大的价格区间
    sorted_bins = sorted(price_bins.items(), key=lambda x: x[1], reverse=True)
    high_vol_bins = [pb[0] for pb in sorted_bins[:5]]  # 量最大的5个区间
    
    # --- 触底反弹分析 ---
    support_events = []
    for i in range(20, n - 3):
        if (closes[i] <= closes[i-1] and closes[i] <= closes[i-2] and
            closes[i] <= closes[i+1] and closes[i] <= closes[i+2]):
            max_rebound = max(closes[i+1:i+6])
            rebound_pct = (max_rebound / closes[i] - 1) * 100
            vol_ratio = volumes_wan[i] / ma20_vol[i] if not np.isnan(ma20_vol[i]) else 1
            support_events.append({
                'date': str(dates[i]),
                'idx': i,
                'low': float(closes[i]),
                'close_before': float(closes[i-1]) if i > 0 else float(closes[i]),
                'rebound': float(rebound_pct),
                'vol_ratio': float(vol_ratio),
                'volume': float(volumes_wan[i]),
            })
    
    # 按价格区间聚合支撑位
    support_bins = defaultdict(list)
    for e in support_events:
        bk = round(e['low'] * 2) / 2
        support_bins[bk].append(e)
    
    support_levels = []
    for bk in sorted(support_bins.keys()):
        evts = sorted(support_bins[bk], key=lambda x: x['date'])
        avg_rebound = np.mean([e['rebound'] for e in evts])
        avg_vr = np.mean([e['vol_ratio'] for e in evts])
        touch_count = len(evts)
        
        # 真托底: 缩量触底(vol_ratio<1.2) + 有效反弹(>2%)
        genuine_count = sum(1 for e in evts if e['vol_ratio'] < 1.2 and e['rebound'] > 2)
        
        # 检查底部是否抬高: 看该价位最近3次触底的低点趋势
        bottom_trend = 'FLAT'
        if touch_count >= 3:
            lows_seq = [e['low'] for e in evts[-3:]]
            if lows_seq[2] > lows_seq[1] > lows_seq[0]:
                bottom_trend = 'RISING'  # 底部抬高 → 庄家护盘
            elif lows_seq[2] < lows_seq[1] < lows_seq[0]:
                bottom_trend = 'FALLING'  # 底部下移 → 散户自发/假托底
        elif touch_count >= 2:
            lows_seq = [e['low'] for e in evts[-2:]]
            if lows_seq[1] > lows_seq[0]:
                bottom_trend = 'RISING'
            elif lows_seq[1] < lows_seq[0]:
                bottom_trend = 'FALLING'
        
        # 综合判断支撑类型
        if touch_count >= 2 and bottom_trend == 'RISING':
            support_type = 'ZHUANGJIA_PROTECT'  # 庄家护盘
            quality = 'STRONG'
        elif genuine_count >= touch_count * 0.6 and touch_count >= 2:
            support_type = 'RETAIL_NATURAL'  # 散户自发但有效
            quality = 'MODERATE'
        elif touch_count >= 2 and bottom_trend == 'FALLING':
            support_type = 'RETAIL_WEAKENING'  # 散户自发但被消耗
            quality = 'WEAK'
        else:
            support_type = 'NOISE'
            quality = 'WEAK'
        
        support_levels.append({
            'price': bk,
            'touch_count': touch_count,
            'avg_rebound_pct': round(avg_rebound, 1),
            'avg_vol_ratio': round(avg_vr, 2),
            'genuine_count': genuine_count,
            'bottom_trend': bottom_trend,
            'support_type': support_type,
            'quality': quality,
        })
    
    # --- 压力位分析 ---
    peak_events = []
    for i in range(20, n - 3):
        if (closes[i] >= closes[i-1] and closes[i] >= closes[i-2] and
            closes[i] >= closes[i+1] and closes[i] >= closes[i+2]):
            min_future = min(closes[i+1:i+4])
            pullback = (closes[i] - min_future) / closes[i] * 100
            vol_ratio = volumes_wan[i] / ma20_vol[i] if not np.isnan(ma20_vol[i]) else 1
            peak_events.append({
                'date': str(dates[i]),
                'peak': float(closes[i]),
                'pullback': float(pullback),
                'vol_ratio': float(vol_ratio),
            })
    
    peak_bins = defaultdict(list)
    for e in peak_events:
        bk = round(e['peak'] * 2) / 2
        peak_bins[bk].append(e)
    
    resistance_levels = []
    for bk in sorted(peak_bins.keys()):
        evts = peak_bins[bk]
        avg_pullback = np.mean([e['pullback'] for e in evts])
        avg_vr = np.mean([e['vol_ratio'] for e in evts])
        touch_count = len(evts)
        resistance_levels.append({
            'price': bk,
            'touch_count': touch_count,
            'avg_pullback_pct': round(avg_pullback, 1),
            'avg_vol_ratio': round(avg_vr, 2),
            'strength': 'STRONG' if avg_pullback > 5 and touch_count >= 3 else 'WEAK',
        })
    
    # --- 当前价格 vs 成本位 ---
    current_price = closes[-1]
    current_deviation = None
    current_deviation_recent = None
    if cost_vwap_full:
        current_deviation = (current_price / cost_vwap_full - 1) * 100
    if cost_vwap_recent:
        current_deviation_recent = (current_price / cost_vwap_recent - 1) * 100
    
    # 找出当前价格附近最近的支撑位和压力位
    nearest_support = None
    nearest_resistance = None
    for sl in support_levels:
        if sl['price'] < current_price:
            if nearest_support is None or sl['price'] > nearest_support['price']:
                nearest_support = sl
    for rl in resistance_levels:
        if rl['price'] > current_price:
            if nearest_resistance is None or rl['price'] < nearest_resistance['price']:
                nearest_resistance = rl
    
    # 汇总已识别的脉冲的建仓/派发统计
    distribute_count = sum(1 for p in pulse_events if p['type'] == 'DISTRIBUTE')
    accumulate_count = sum(1 for p in pulse_events if p['type'] == 'ACCUMULATE')
    
    # 熵法诊断：当前熵值位置
    entropy_current = None
    entropy_z = None
    entropy_min_value = None
    entropy_min_date = None
    if entropy_result and entropy_result.get('entropy_stats'):
        es = entropy_result['entropy_stats']
        entropy_current = es['current']
        entropy_z = es['current_z']
        entropy_min_value = es['min']
        # 找最小熵对应的日期
        if entropy_result.get('recent_entropy'):
            for d, h_val, z_val in entropy_result['recent_entropy']:
                if h_val == es['min']:
                    entropy_min_date = str(d)
                    break
    
    result = {
        'cost_analysis_v2': {
            'build_cost_full_vwap': round(cost_vwap_full, 2) if cost_vwap_full else None,
            'build_cost_recent_vwap': round(cost_vwap_recent, 2) if cost_vwap_recent else None,
            'silent_days_used_full': cost_days_count,
            'silent_days_used_recent': cost_recent_count,
            'silent_days_total': len(silent_days),
        },
        'entropy_analysis': entropy_result,
        'pulse_analysis': {
            'total_pulses': len(pulse_events),
            'distribute_count': distribute_count,
            'accumulate_count': accumulate_count,
            'pulse_details': pulse_events[-5:],
        },
        'current_price': float(current_price),
        'current_vs_build_cost': round(current_deviation, 2) if current_deviation else None,
        'entropy_current': entropy_current,
        'entropy_z': entropy_z,
        'entropy_min': {'value': entropy_min_value, 'date': entropy_min_date},
        'support_levels': support_levels,
        'resistance_levels': resistance_levels,
        'nearest_support': nearest_support,
        'nearest_resistance': nearest_resistance,
        'current_bin': round(current_price * 2) / 2,
    }
    
    return result


def compute_active_score(conn, symbol, target_date, lookback=60):
    """
    计算一只股票的主力活跃度三要素
    返回 dict 或 None (数据不足)
    """
    cur = conn.cursor()
    
    # 取最近 lookback 个交易日（含target_date）
    cur.execute("""
        SELECT trade_date, close, volume, turnover, amount
        FROM daily_prices
        WHERE symbol=%s AND trade_date<=%s AND close>0 AND volume>0
        ORDER BY trade_date DESC
        LIMIT %s
    """, (symbol, target_date, lookback + 120))  # 多取点给SMA20
    rows = cur.fetchall()
    if len(rows) < 20:
        return None
    
    # 按日期正向排序
    rows.reverse()
    
    closes = np.array([float(r[1]) for r in rows])
    volumes = np.array([float(r[2]) for r in rows])
    turnovert = np.array([r[3] if r[3] else 0 for r in rows])
    amounts = np.array([float(r[4]) if r[4] else 0 for r in rows])
    dates = [r[0] for r in rows]
    n = len(rows)
    
    # --- 要素1: 量级门槛 ---
    # 只看最近 lookback 天
    lookback_actual = min(lookback, n)
    recent_vol = volumes[-lookback_actual:]
    recent_amt = amounts[-lookback_actual:]
    recent_turn = turnovert[-lookback_actual:]
    
    avg_turnover = float(np.mean(recent_turn))
    avg_amount = float(np.mean(recent_amt))
    
    level_pass = (avg_turnover > THRESHOLD['turnover_avg'] and 
                  avg_amount > THRESHOLD['amount_avg'])
    
    # --- 要素2: 放量脉冲 ---
    # 计算20日移动平均量，找单日量 > 20日均量×2.5
    pulse_count = 0
    pulse_dates = []
    if n >= 20:
        cumvol = np.cumsum(volumes)
        ma20_vol = np.full(n, np.nan)
        ma20_vol[19] = cumvol[19] / 20
        ma20_vol[20:] = (cumvol[20:] - cumvol[:n-20]) / 20
        
        for i in range(20, n):
            if volumes[i] > ma20_vol[i] * THRESHOLD['volume_ratio']:
                pulse_count += 1
                pulse_dates.append((dates[i], float(volumes[i] / ma20_vol[i])))
    
    pulse_pass = pulse_count >= THRESHOLD['min_pulses']
    
    # --- 要素3: 快速修复能力 ---
    # 放量下跌后5d内价格修复到放量前90%以上
    repair_count = 0
    repair_details = []
    if n >= 25:  # 需要足够数据
        cumvol = np.cumsum(volumes)
        ma20_vol = np.full(n, np.nan)
        ma20_vol[19] = cumvol[19] / 20
        ma20_vol[20:] = (cumvol[20:] - cumvol[:n-20]) / 20
        
        for i in range(20, min(n - 5, lookback_actual + 20)):
            # 放量下跌: 单日量 > 2倍均量 且 当日跌
            if volumes[i] > ma20_vol[i] * 2.0 and (i == 0 or closes[i] < closes[i-1]):
                pre_close = closes[i-1] if i > 0 else closes[i]
                drop_pct = (closes[i] / pre_close - 1) * 100
                
                # 检查后续5天是否修复到 pre_close 的 90% 以上
                repair_window = min(5, n - i - 1)
                if repair_window <= 0:
                    continue
                    
                max_future = max(closes[i+1:i+1+repair_window])
                if max_future >= pre_close * THRESHOLD['repair_ratio']:
                    repair_count += 1
                    repair_details.append({
                        'date': str(dates[i]),
                        'drop': round(drop_pct, 2),
                        'low': float(closes[i]),
                        'pre_close': float(pre_close),
                        'repair_to': float(pre_close * THRESHOLD['repair_ratio']),
                        'max_future': float(max_future),
                        'repair_days': int(np.argmax(closes[i+1:i+1+repair_window]) + 1)
                    })
    
    repair_pass = repair_count > 0
    
    # --- 综合评分 ---
    score = 0
    if level_pass: score += 40
    if pulse_pass: score += 30
    if repair_pass: score += 30
    
    # --- 汇总 ---
    # 额外：计算庄家成本分析
    cost_info = compute_cost_levels(conn, symbol, target_date, lookback=120)
    
    result = {
        'symbol': symbol,
        'date': str(target_date),
        'lookback': lookback_actual,
        'elements': {
            'level': {
                'pass': level_pass,
                'avg_turnover': round(avg_turnover, 2),
                'avg_amount_wan': round(avg_amount / 1e4, 0),
            },
            'pulse': {
                'pass': pulse_pass,
                'count': pulse_count,
                'threshold_ratio': THRESHOLD['volume_ratio'],
                'pulses': pulse_dates[-5:] if pulse_dates else [],
            },
            'repair': {
                'pass': repair_pass,
                'count': repair_count,
                'details': repair_details[-3:],
            }
        },
        'score': score,
        'pass': score >= 40,
        'cost_analysis': cost_info,
    }
    
    return result


def filter_v8_candidates(conn, target_date, v8_signals=None):
    """
    对V8信号做主力活跃度过滤
    v8_signals: 已有的V8信号列表，如果为None则从V8策略获取
    """
    results = {}
    
    if v8_signals is None:
        # 从daily_prices获取超跌候选
        from strategies.v8_strategy import V8Strategy
        v8 = V8Strategy()
        # 简化版: 直接查SMA偏离和PB
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT ON (d.symbol) d.symbol, d.close, d.trade_date
            FROM daily_prices d
            JOIN (
                SELECT symbol, MAX(trade_date) as max_date
                FROM daily_prices WHERE trade_date<=%s AND close>0
                GROUP BY symbol
            ) l ON d.symbol=l.symbol AND d.trade_date=l.max_date
            WHERE d.close>0 AND d.trade_date<=%s AND d.trade_date>=%s
        """, (target_date, target_date, target_date - timedelta(days=5)))
        
        symbols_to_check = []
        for r in cur.fetchall():
            symbols_to_check.append(r[0])
    else:
        symbols_to_check = ['A.' + s.get('code', '') for s in v8_signals]
    
    # 批量计算
    passing = []
    failing = []
    
    for sym in symbols_to_check[:200]:  # 限制数量
        code = sym[2:] if sym.startswith('A.') else sym
        result = compute_active_score(conn, sym, target_date)
        if result is None:
            continue
        results[code] = result
        if result['pass']:
            passing.append(code)
        else:
            failing.append(code)
    
    return {
        'date': str(target_date),
        'total': len(results),
        'passing': passing,
        'failing': failing,
        'details': results,
    }


def backtest(conn, start_date, end_date):
    """
    回测主力活跃度与V8信号结合的效果
    """
    print(f"回测区间: {start_date} ~ {end_date}")
    print("=" * 60)
    
    cur = conn.cursor()
    
    # 获取区间内所有的V8入场信号
    cur.execute("""
        SELECT DISTINCT ON (d.symbol, d.trade_date) 
               d.symbol, d.trade_date, d.close
        FROM daily_prices d
        WHERE d.trade_date BETWEEN %s AND %s
          AND d.close > 0
        ORDER BY d.symbol, d.trade_date
    """, (start_date, end_date))
    rows = cur.fetchall()
    
    # 取SMA120和PB
    print(f"全量V8候选: {len(rows)} 条")
    
    # 对每个信号计算active_score
    passed_signals = 0
    failed_signals = 0
    symbol_cache = {}
    
    for sym, d, close in rows:
        code = sym[2:]
        cache_key = f"{sym}_{d}"
        
        score = compute_active_score(conn, sym, d, lookback=60)
        if score is None:
            continue
        
        # 也计算T+20收益
        cur.execute("""
            SELECT close FROM daily_prices
            WHERE symbol=%s AND trade_date>%s AND close>0
            ORDER BY trade_date LIMIT 20
        """, (sym, d))
        future_closes = [float(r[0]) for r in cur.fetchall()]
        ret20 = (future_closes[-1] / close - 1) * 100 if len(future_closes) >= 20 else None
        
        if score['pass']:
            passed_signals += 1
        else:
            failed_signals += 1
    
    print(f"通过主力过滤: {passed_signals}")
    print(f"未通过: {failed_signals}")
    print(f"通过率: {passed_signals/(passed_signals+failed_signals+1)*100:.1f}%")
    
    # 还应该在每个信号点回看T+20收益，对比通过/不通过的差异
    # 但这个数据量会很大，先看看throughput
    print(f"\n待完成: 逐信号T+20收益对比回测")
    
    return {'passed': passed_signals, 'failed': failed_signals}


def analyze_code(code, target_date=None):
    """分析单只股票的完整报告"""
    if target_date is None:
        target_date = date.today()
    sym = f"A.{code}"
    
    conn = psycopg2.connect(DB)
    score = compute_active_score(conn, sym, target_date)
    
    if score is None:
        print(f"{code}: 数据不足")
        return
    
    status = "✅ 通过" if score['pass'] else "❌ 未通过"
    print(f"\n{'='*60}")
    print(f"{code} 主力行为分析 ({status}) 综合评分:{score['score']}")
    print(f"{'='*60}")
    
    el = score['elements']
    print(f"\n【第一层 — 活跃度筛选】")
    print(f"  量级: {'✅' if el['level']['pass'] else '❌'} 日均换手{el['level']['avg_turnover']}% 日均额{el['level']['avg_amount_wan']:.0f}万")
    print(f"  脉冲: {'✅' if el['pulse']['pass'] else '❌'} {el['pulse']['count']}次放量")
    print(f"  修复: {'✅' if el['repair']['pass'] else '❌'} {el['repair']['count']}次修复")
    
    ca = score.get('cost_analysis')
    if ca:
        ca2 = ca.get('cost_analysis_v2')
        ea = ca.get('entropy_analysis')
        pulse = ca.get('pulse_analysis', {})
        
        print(f"\n【第二层 — 自适应熵法庄家成本（v3 核心算法）】")
        if ea:
            tc = ea.get('true_cost_vwap')
            lsz = ea.get('low_safe_zone')
            lsz_vol = ea.get('low_safe_zone_vol_avg')
            es = ea.get('entropy_stats', {})
            
            if tc:
                print(f"✦ 建仓成本(熵法): {tc}元 (核心筹码区)")
            if lsz:
                print(f"✦ 低吸安全区(地量): {lsz}元 (地量均{lsz_vol:.0f}万手)" if lsz_vol else f"✦ 低吸安全区(地量): {lsz}元")
            if es:
                hmin = es.get('min')
                hmin_d = es.get('min_date','?')
                print(f"✦ 当前熵值: {es['current']} (z={es['current_z']}) | 最小熵: {hmin} ({hmin_d})")
            if tc and ca.get('current_price'):
                dev = (ca['current_price'] / tc - 1) * 100
                print(f"✦ 当前价距建仓成本: {dev:+.2f}%")
            
            print(f"\n  识别出的吸筹区段:")
            if ea.get('accumulation_zones'):
                for az in ea['accumulation_zones']:
                    print(f"  📥 {az['start']} ~ {az['end']} ({az['days']}d)  ← 熵值异常低的建仓窗")
            else:
                print(f"  未识别出明显吸筹区")
        
        print(f"\n【缩量区VWAP（对照）】")
        if ca2:
            full = ca2.get('build_cost_full_vwap')
            recent = ca2.get('build_cost_recent_vwap')
            if full:
                print(f"  全量缩量VWAP(120d): {full} (基于{ca2['silent_days_used_full']}个缩量日)")
            if recent:
                print(f"  近60d缩量VWAP: {recent} (基于{ca2['silent_days_used_recent']}个缩量日)")
        d_recent = None
        if ca2 and ca2.get('build_cost_recent_vwap'):
            d_recent = (ca['current_price'] / ca2['build_cost_recent_vwap'] - 1) * 100
        d_full = ca.get('current_vs_build_cost')
        if d_full:
            print(f"  距全量成本: {d_full:+.2f}% | " + (f"距近60d: {d_recent:+.2f}%" if d_recent else ""))
        
        print(f"\n  放量脉冲分类(验证):")
        print(f"  总脉冲:{pulse.get('total_pulses',0)}次 派发型:{pulse.get('distribute_count',0)}次 建仓型:{pulse.get('accumulate_count',0)}次")
        if pulse.get('pulse_details'):
            for p in pulse['pulse_details'][-3:]:
                icon = '📤' if p['type'] == 'DISTRIBUTE' else '📥' if p['type'] == 'ACCUMULATE' else '⏳'
                print(f"  {icon} {p['date']} 收{p['price']:.2f} 量{p['volume_wan']:.0f}万(×{p['vol_ratio']:.1f}) 类型:{p['type']}")
        
        print(f"\n【第三层 — 支撑/压力位 + 托底类型】")
        print(f"  支撑位:")
        for sl in ca['support_levels']:
            icons = {'ZHUANGJIA_PROTECT': '🟢', 'RETAIL_NATURAL': '🟡', 'RETAIL_WEAKENING': '🔴', 'NOISE': '⚪'}
            icon = icons.get(sl['support_type'], '⚪')
            trend = {'RISING': '↑', 'FALLING': '↓', 'FLAT': '→'}
            print(f"  {icon} {sl['price']:>5.1f}元 触底{sl['touch_count']}次 反弹{sl['avg_rebound_pct']:+.1f}% 底部{trend.get(sl['bottom_trend'],'→')} {sl['support_type']}")
        
        print(f"  压力位:")
        for rl in ca['resistance_levels']:
            icon = '🔴' if rl['strength'] == 'STRONG' else '🟡'
            print(f"  {icon} {rl['price']:>5.1f}元 触顶{rl['touch_count']}次 回调{rl['avg_pullback_pct']:.1f}% 量比{rl['avg_vol_ratio']:.2f}")
        
        print(f"\n【综合判断】")
        cur_price = ca['current_price']
        ns = ca.get('nearest_support')
        nr = ca.get('nearest_resistance')
        
        # 参考熵法判断
        ea_tc = ea.get('true_cost_vwap') if ea else None
        ea_lsz = ea.get('low_safe_zone') if ea else None
        
        if ea_tc and cur_price <= ea_tc * 0.95:
            print(f"  💡 当前{cur_price:.2f}已跌破熵法成本{ea_tc}，处于'洗盘接筹'位置")
            if ea_lsz:
                print(f"  💡 低吸安全区{ea_lsz}，若缩量触及可试仓，止损{ea_lsz}")
        elif ea_tc:
            print(f"  当前{cur_price:.2f}在熵法成本{ea_tc}下方")
        
        if ns and nr:
            print(f"  技术区间: {ns['price']}支撑(↓消耗中)~{nr['price']}压力")
        
        if ea and ea.get('entropy_stats', {}).get('current_z', 0) < -1.0:
            print(f"  📊 当前熵值偏低(z={ea['entropy_stats']['current_z']})，仍在吸筹/整理区间")
        print(f"  价格区间: {ca['current_bin']:.1f}元")
    
    conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='主力行为分析')
    parser.add_argument('--code', help='查询单个股票')
    parser.add_argument('--date', help='目标日期 (默认今天)')
    parser.add_argument('--backtest', action='store_true', help='回测模式')
    parser.add_argument('--start', help='回测起始日期')
    parser.add_argument('--end', help='回测结束日期')
    parser.add_argument('--scan', action='store_true', help='扫描全市场')
    args = parser.parse_args()
    
    target = date.fromisoformat(args.date) if args.date else date.today()
    
    if args.code:
        analyze_code(args.code, target)
    elif args.backtest:
        conn = psycopg2.connect(DB)
        start = date.fromisoformat(args.start) if args.start else target - timedelta(days=365)
        end = date.fromisoformat(args.end) if args.end else target
        backtest(conn, start, end)
        conn.close()
    elif args.scan:
        conn = psycopg2.connect(DB)
        result = filter_v8_candidates(conn, target)
        print(f"\n=== 主力活跃度扫描结果 ({target}) ===")
        print(f"总检查: {result['total']} 只")
        print(f"通过: {len(result['passing'])} 只")
        print(f"未通过: {len(result['failing'])} 只")
        conn.close()
    else:
        # 默认：分析软控和大众公用
        for code in ['600635', '002073']:
            analyze_code(code, target)
