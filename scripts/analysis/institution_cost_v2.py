#!/usr/bin/env python3
"""
机构持仓成本分析器 v2 (2026-06-03)
============================
修复v1的3个P0/P1问题：
  P0: 多成本区加权 → 避免系统性高估亏损
  P0: 减持窗口验证 → 避免把减持误判为建仓
  P1: 净利润趋势 → 防止基本面恶化标的进入BUY
  P1: 持仓时间维度 → 自救紧迫性评估
  P2: 板块联动分析 → 全行业机构状态
  P2: 行业排除列表 → 手动配置

算法核心：
  1. 找放量横盘区（机构建仓特征）
  2. 验证不是减持窗口（查exec_hold_changes）
  3. 多区加权平均成本
  4. 仓位锁仓度（换手率萎缩）
  5. 净利润趋势（最近两季）
  6. 板块联动（同行业对比）
  7. 综合信号输出

用法:
  python3 institution_cost_v2.py --code 002073          # 单只
  python3 institution_cost_v2.py --v8-candidates        # V8候选池
"""
import sys, os, json, argparse
import psycopg2
import numpy as np
from datetime import date, timedelta

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT_DIR = '/home/tearsyu/.openclaw/workspace/memory/cost_cache'

# 行业排除列表（手动配置）
EXCLUDED_INDUSTRIES = {
    '房地产开发', '房地产服务', '房地产', '房地产Ⅱ', '房地产开发Ⅱ', '房地产服务Ⅱ',
}

# 板块联动需要用的行业分类优先顺序
INDUSTRY_LEVELS = ['industry_components_ths_l1', 'stock_industry']


def get_pg():
    conn = psycopg2.connect(DB)
    conn.autocommit = True
    return conn


def find_institution_zones(closes, volumes, dates, lookback=400):
    """
    扫描放量横盘区（机构建仓候选）
    
    条件：
    - 15~120天连续交易
    - 振幅 < 15%（价格被压制）
    - 量能放大 > 前60日均量的1.3x
    - 量能变异系数 < 0.8（不是偶发脉冲）
    """
    n = len(closes)
    if n < 120:
        return []
    
    zones = []
    zone_ends = []  # 记录已选取的结束位置
    
    # 从最远期向近期扫描
    start_max = n - 15
    start_min = max(0, n - lookback)
    
    candidates = []
    for start in range(start_max, start_min, -5):  # 步长5减少重叠
        for end in range(min(n, start + 15), min(n, start + 120), 3):
            seg_closes = closes[start:end]
            seg_volumes = volumes[start:end]
            seg_dates = dates[start:end]
            days = end - start
            if days < 15:
                continue
            
            price_high = np.max(seg_closes)
            price_low = np.min(seg_closes)
            range_ratio = price_high / price_low if price_low > 0 else 999
            
            if range_ratio > 1.15:
                continue
            
            pre_vol = np.mean(volumes[max(0, start-60):start]) if start >= 60 else np.mean(volumes[:start])
            avg_vol = np.mean(seg_volumes)
            vol_ratio = avg_vol / pre_vol if pre_vol > 0 else 1.0
            
            if vol_ratio < 1.3:
                continue
            
            vol_cv = np.std(seg_volumes) / avg_vol if avg_vol > 0 else 99
            if vol_cv > 0.8:
                continue
            
            # VWAP = 量价加权均价
            total_amount = np.sum(seg_closes * seg_volumes * 100)
            total_shares = np.sum(seg_volumes) * 100
            zone_price = total_amount / total_shares if total_shares > 0 else np.mean(seg_closes)
            
            # 与已选区去重（检查重叠度）
            overlap = False
            for zs, ze in zone_ends:
                if not (dates[end-1] < zs or dates[start] > ze):
                    overlap = True
                    break
            if overlap:
                continue
            
            candidates.append({
                'start': str(seg_dates[0]),
                'end': str(seg_dates[-1]),
                'days': days,
                'cost_price': round(float(zone_price), 2),
                'zone_high': round(float(price_high), 2),
                'zone_low': round(float(price_low), 2),
                'avg_vol': int(avg_vol),
                'vol_ratio': round(float(vol_ratio), 2),
                'vol_cv': round(float(vol_cv), 2),
                'range_ratio': round(float(range_ratio), 3),
            })
            zone_ends.append((dates[start], dates[end-1]))
            break  # 每段最多取一个
    
    # 按时间从近到远排序
    candidates.sort(key=lambda z: z['end'], reverse=True)
    return candidates[:5]


def verify_not_reduction(cost_zones, code, cur):
    """
    P0修正：验证放量横盘区不是减持窗口
    
    查exec_hold_changes，看放量横盘期内是否有高管/大股东减持
    如果有，这个区间的放量不是机构建仓，而是减持
    """
    if not cost_zones:
        return cost_zones
    
    verified = []
    for z in cost_zones:
        z_start = z['start']
        z_end = z['end']
        
        # 查这个时间段内的减持记录
        cur.execute("""
            SELECT COUNT(*), COALESCE(SUM(change_shares), 0)
            FROM exec_hold_changes
            WHERE security_code = %s
              AND change_date >= %s AND change_date <= %s
              AND change_shares < 0  -- 负值=减持
        """, (code, z_start, z_end))
        cnt, total_change = cur.fetchone()
        
        if cnt > 0 and total_change < 0:
            # 有减持，标记
            z['reduction_warning'] = True
            z['reduction_deals'] = cnt
            z['reduction_shares'] = int(abs(total_change))
            # 不直接排除，但标记（可能存在混合情况：机构建仓+大股东减持）
            # 仅在减持量极大时排除
            z['reduction_risk'] = 'HIGH' if abs(int(total_change)) > 10000000 else 'MID'
        else:
            z['reduction_warning'] = False
            z['reduction_risk'] = 'NONE'
        
        # 即使有减持也保留，但标记供下游决策
        verified.append(z)
    
    return verified


def calc_weighted_cost(cost_zones):
    """
    P0修正：多成本区加权平均
    
    每个成本区按天数加权：
    综合成本 = Σ(成本 × 持续天数) / Σ(持续天数)
    
    同时计算：
    - 加权成本
    - 最重仓位（天数最多的成本区）
    - 近期仓位（最近3个月内）
    """
    if not cost_zones:
        return None
    
    total_days = sum(z['days'] for z in cost_zones)
    weighted = sum(z['cost_price'] * z['days'] for z in cost_zones) / total_days
    
    # 最重仓（天数最多的区）
    heaviest = max(cost_zones, key=lambda z: z['days'])
    
    # 近期仓位（最近3个月）
    three_months_ago = date.today() - timedelta(days=90)
    recent_zones = [z for z in cost_zones if z['end'] >= str(three_months_ago)]
    recent_cost = sum(z['cost_price'] * z['days'] for z in recent_zones) / sum(z['days'] for z in recent_zones) if recent_zones else None
    
    # 最早的仓位（最长的建仓周期）
    earliest_cost = cost_zones[-1]['cost_price'] if len(cost_zones) >= 2 else weighted
    
    return {
        'weighted_cost': round(weighted, 2),
        'heaviest_cost': round(heaviest['cost_price'], 2),
        'heaviest_days': heaviest['days'],
        'recent_cost': round(recent_cost, 2) if recent_cost else None,
        'earliest_cost': round(earliest_cost, 2),
        'zone_count': len(cost_zones),
    }


def calc_hold_liquidity(closes, volumes):
    """
    筹码锁定度（换手率变化）
    
    ratio < 0.6: locked（锁仓，机构没跑）
    ratio 0.6~1.0: normal（正常换手）
    ratio > 1.2: draining（放量，可能在出逃）
    """
    n = len(closes)
    if n < 60:
        return 0, 'unknown', ''
    
    recent_vol = np.mean(volumes[-20:]) if n >= 20 else np.mean(volumes)
    hist_vol = np.mean(volumes[-120:]) if n >= 120 else np.mean(volumes)
    vol_ratio = recent_vol / hist_vol if hist_vol > 0 else 1.0
    
    if vol_ratio < 0.6:
        detailed = f"量能萎缩到均值{vol_ratio*100:.0f}%，筹码锁定"
        return round(vol_ratio, 2), 'locked', detailed
    elif vol_ratio < 1.2:
        detailed = f"正常换手 ({vol_ratio:.2f}x)"
        return round(vol_ratio, 2), 'normal', detailed
    else:
        detailed = f"放量{vol_ratio:.1f}x，筹码松动"
        return round(vol_ratio, 2), 'draining', detailed


def calc_netprofit_trend(code, cur):
    """
    P1修正：净利润趋势判断
    取最近季度的净利润，判断基本面方向
    返回: trend, detail
    """
    cur.execute("""
        SELECT end_date, net_profit, net_profit_yoy, revenue, revenue_yoy
        FROM financial_data
        WHERE symbol = 'A.'||%s AND net_profit IS NOT NULL
        ORDER BY end_date DESC LIMIT 4
    """, (code,))
    rows = cur.fetchall()
    
    if not rows:
        return 'unknown', '无财务数据'
    
    q1 = rows[0]
    np1 = float(q1[1]) if q1[1] else 0
    np_yoy1 = float(q1[2]) if q1[2] else 0
    
    detail_parts = ['盈利' if np1 > 0 else '亏损']
    
    if len(rows) >= 2:
        q2 = rows[1]
        np2 = float(q2[1]) if q2[1] else 0
        np_chg = (np1 - np2) / abs(np2) if np2 != 0 else 0
        
        if np_chg > 0.1:
            detail_parts.append('环比改善')
            trend = 'improving'
        elif np_chg > -0.1:
            detail_parts.append('环比持平')
            trend = 'stable'
        else:
            detail_parts.append('环比恶化')
            trend = 'declining'
    else:
        if np_yoy1 > 0:
            trend = 'improving'
        elif np_yoy1 < 0:
            trend = 'declining'
        else:
            trend = 'stable'
    
    if np_yoy1 > 0:
        detail_parts.append(f"同比+{np_yoy1:.0f}%")
    elif np_yoy1 < 0:
        detail_parts.append(f"同比{np_yoy1:.0f}%")
    else:
        detail_parts.append('同比持平')
    
    return trend, ' '.join(detail_parts)


def calc_sector_cost(code, cur, target_date):
    """
    P2：板块联动分析
    
    找到股票所属行业，计算同行业其他股票的机构平均亏损和筹码状态
    
    返回：
      - sector_avg_loss: 行业平均机构亏损
      - sector_lock_ratio: 行业锁仓比例
      - sector_count: 行业样本数
    """
    # 找行业
    cur.execute("""
        SELECT industry FROM industry_components_ths_l1
        WHERE stock_code=%s
    """, (code,))
    row = cur.fetchone()
    if not row:
        return None
    
    industry = row[0]
    
    # 找同行业其他股票
    cur.execute("""
        SELECT stock_code FROM industry_components_ths_l1
        WHERE industry=%s AND stock_code!=%s
    """, (industry, code))
    sector_codes = [r[0] for r in cur.fetchall()]
    
    if len(sector_codes) < 5:
        return None
    
    # 算行业机构亏损均值（简化：用SMA偏离近似）
    losses, locks = [], []
    sample = sector_codes[:50]  # 最多采50只
    
    for sc in sample:
        symbol = 'A.' + sc
        cur.execute("""
            SELECT trade_date, close, volume FROM daily_prices
            WHERE symbol=%s AND trade_date <= %s AND close > 0
            ORDER BY trade_date
        """, (symbol, target_date))
        rows = cur.fetchall()
        if len(rows) < 120:
            continue
        
        closes = np.array([float(r[1]) for r in rows[-120:]])
        volumes = np.array([float(r[2]) if r[2] else 0 for r in rows[-120:]])
        
        # SMA120偏离近似机构亏损
        sma120 = np.mean(closes)
        cur_price = closes[-1]
        loss = cur_price / sma120 - 1
        losses.append(loss)
        
        # 筹码锁定
        recent = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
        hist = np.mean(volumes)
        vr = recent / hist if hist > 0 else 1
        locks.append('locked' if vr < 0.6 else 'normal')
    
    if not losses:
        return None
    
    avg_loss = np.mean(losses)
    lock_count = sum(1 for l in locks if l == 'locked')
    lock_ratio = lock_count / len(locks) if locks else 0
    
    return {
        'industry': industry,
        'avg_loss': round(float(avg_loss * 100), 1),  # 百分数
        'lock_ratio': round(float(lock_ratio), 2),
        'sample_count': len(losses),
    }


def check_excluded_industry(code, cur):
    """检查是否在行业排除列表中"""
    for tbl, col in [('industry_components_ths_l1', 'industry'), ('stock_industry', 'industry')]:
        cur.execute(f"SELECT {col} FROM {tbl} WHERE stock_code=%s", (code,))
        row = cur.fetchone()
        if row and row[0] in EXCLUDED_INDUSTRIES:
            return True, row[0]
    return False, None


def calc_signal(cost_info, current_price, sma_dev, hold_state, profit_trend, hold_duration, sector_info, is_excluded):
    """
    综合信号计算
    
    因子：
    1. 机构亏损深度（多成本区加权） max 30
    2. 筹码锁定度 max 20
    3. 净利润趋势 max 20
    4. 持仓时间（被套时间） max 15
    5. 板块联动 max 15
    
    总分0~100
    """
    score = 30  # 基础分
    factors = {}
    
    if is_excluded:
        return 0, 'EXCLUDED', 0, {'reason': '行业排除'}
    
    # ===== 1. 亏损深度 (max 30) =====
    if cost_info and cost_info['weighted_cost'] > 0:
        loss = (current_price - cost_info['weighted_cost']) / cost_info['weighted_cost']
    elif cost_info:
        loss = sma_dev if sma_dev else 0
    else:
        loss = sma_dev if sma_dev else 0
    
    factors['loss'] = round(float(loss * 100), 1)
    
    if loss < -0.35:
        score += 30
        factors['loss_score'] = 30
    elif loss < -0.25:
        score += 25
        factors['loss_score'] = 25
    elif loss < -0.18:
        score += 20
        factors['loss_score'] = 20
    elif loss < -0.10:
        score += 10
        factors['loss_score'] = 10
    else:
        factors['loss_score'] = 0
    
    # ===== 2. 筹码锁定度 (max 20) =====
    hold_ratio, hold_status, hold_detail = hold_state
    factors['hold_status'] = hold_status
    
    if hold_status == 'locked':
        score += 20
        factors['lock_score'] = 20
    elif hold_status == 'normal':
        score += 10
        factors['lock_score'] = 10
    elif hold_status == 'draining':
        score -= 10  # 出逃扣分
        factors['lock_score'] = -10
    
    # ===== 3. 净利润趋势 (max 20) =====
    trend, trend_detail = profit_trend
    factors['profit_trend'] = trend
    
    if loss is not None and loss < -0.30:
        # 亏超30%时净利润恶化 → 降级
        if trend == 'declining':
            score -= 10  # 基本面恶化，机构自救能力下降
            factors['profit_score'] = -10
        elif trend == 'improving':
            score += 15
            factors['profit_score'] = 15
        elif trend == 'stable':
            score += 10
            factors['profit_score'] = 10
        else:
            factors['profit_score'] = 0
    else:
        # 亏损不深时，净利润趋势影响小
        if trend == 'improving':
            score += 5
            factors['profit_score'] = 5
        elif trend == 'declining':
            score -= 5
            factors['profit_score'] = -5
        else:
            factors['profit_score'] = 0
    
    # ===== 4. 持仓时间 (max 15) =====
    # 被套越久，自救紧迫性越高
    factors['hold_duration'] = hold_duration
    
    if hold_duration >= 365:
        score += 15  # 被套1年以上→非常迫切
        factors['duration_score'] = 15
    elif hold_duration >= 180:
        score += 10
        factors['duration_score'] = 10
    elif hold_duration >= 90:
        score += 5
        factors['duration_score'] = 5
    else:
        factors['duration_score'] = 0
    
    # ===== 5. 板块联动 (max 15) =====
    if sector_info:
        avg_loss = sector_info['avg_loss']
        lock_ratio = sector_info['lock_ratio']
        
        if avg_loss < -20 and lock_ratio > 0.3:
            score += 15  # 行业全面被套+锁仓→系统性底部
            factors['sector_score'] = 15
        elif avg_loss < -15:
            score += 8
            factors['sector_score'] = 8
        elif avg_loss < -10:
            score += 5
            factors['sector_score'] = 5
        else:
            factors['sector_score'] = 0
    else:
        factors['sector_score'] = 0
    
    # ===== 信号判定 =====
    signal = 'WATCH'
    
    if score >= 75 and loss < -0.20 and hold_status != 'draining' and trend != 'declining':
        signal = 'BUY'
    elif score >= 55:
        signal = 'HOLD'
    elif score <= 30:
        signal = 'AVOID'
    
    target_up = 0
    if cost_info and cost_info['weighted_cost'] > 0 and current_price > 0:
        target_up = (cost_info['weighted_cost'] / current_price - 1) * 100
    
    return score, signal, round(target_up, 1), factors


def analyze_stock(code, cur, target_date=None):
    """完整分析"""
    if target_date is None:
        cur.execute("SELECT MAX(trade_date) FROM daily_prices")
        target_date = cur.fetchone()[0]
    if target_date is None:
        target_date = date.today()
    
    symbol = 'A.' + code
    
    # 检查排除行业
    is_excluded, excl_industry = check_excluded_industry(code, cur)
    
    # 拉日线
    cur.execute("""
        SELECT trade_date, close, volume FROM daily_prices
        WHERE symbol=%s AND trade_date <= %s AND close > 0
        ORDER BY trade_date
    """, (symbol, target_date))
    rows = cur.fetchall()
    
    if len(rows) < 120:
        return {'code': code, 'error': f'数据不足({len(rows)}天)'}
    
    dates = [r[0] for r in rows]
    closes = np.array([float(r[1]) for r in rows], dtype=float)
    volumes = np.array([float(r[2]) if r[2] else 0 for r in rows], dtype=float)
    
    n = len(closes)
    current_price = float(closes[-1])
    sma120 = float(np.mean(closes[-120:]))
    sma_dev = current_price / sma120 - 1
    
    # 1. 找成本区
    cost_zones = find_institution_zones(closes, volumes, dates)
    
    # 2. 验证减持窗口
    cost_zones = verify_not_reduction(cost_zones, code, cur)
    
    # 3. 加权成本
    cost_info = calc_weighted_cost(cost_zones)
    
    # 4. 筹码锁定度
    hold_state = calc_hold_liquidity(closes, volumes)
    
    # 5. 净利润趋势
    profit_trend = calc_netprofit_trend(code, cur)
    
    # 6. 持仓时间（从最早的建仓区到现在的天数）
    hold_duration = 0
    if cost_zones:
        earliest_end = cost_zones[-1]['end']
        hold_start = date.fromisoformat(earliest_end) if isinstance(earliest_end, str) else earliest_end
        hold_duration = (target_date - hold_start).days if isinstance(target_date, date) else 0
    
    # 7. 板块联动
    sector_info = calc_sector_cost(code, cur, target_date)
    
    # 8. 综合信号
    score, signal, target_up, factors = calc_signal(
        cost_info, current_price, sma_dev, hold_state,
        profit_trend, hold_duration, sector_info, is_excluded)
    
    return {
        'code': code,
        'target_date': str(target_date) if isinstance(target_date, date) else str(target_date),
        'n_days': n,
        'current_price': current_price,
        'sma120': round(float(sma120), 2),
        'sma_dev': round(float(sma_dev*100), 1),
        'cost_zones': cost_zones[:3] if cost_zones else [],
        'cost_info': cost_info,
        'hold_state': hold_state[1],
        'hold_detail': hold_state[2],
        'profit_trend': profit_trend,
        'hold_duration_days': hold_duration,
        'sector_info': sector_info,
        'is_excluded': is_excluded,
        'excluded_industry': excl_industry,
        'score': score,
        'signal': signal,
        'target_up_pct': target_up,
        'factors': factors,
        'effective_loss': factors.get('loss', 0),
    }


def print_report(r, stock_name=''):
    """输出分析报告（MD格式）"""
    if 'error' in r:
        print(f"\n### ❌ {r.get('code','?')} — {r['error']}")
        return
    
    code = r['code']
    sig_emoji = {'BUY':'✅','HOLD':'🔄','WATCH':'👁️','AVOID':'⛔','EXCLUDED':'⛔'}
    
    print(f"\n---")
    print(f"### {r['code']} {stock_name}  |  {sig_emoji.get(r['signal'],'❓')} {r['signal']} | 评分 {r['score']}/100")
    print(f"**现价**: {r['current_price']:.2f}  |  **SMA120**: {r['sma120']:.2f}  |  **偏离**: {r['sma_dev']:+.1f}%  |  **有效亏损**: {r['effective_loss']:+.1f}%")
    
    if r['is_excluded']:
        print(f"**⛔ 行业排除**: {r['excluded_industry']}")
        return
    
    cz = r['cost_zones']
    if cz:
        print(f"\n**成本区** ({len(cz)}个, 加权={r['cost_info']['weighted_cost']}):")
        for i, z in enumerate(cz):
            warn = " ⚠️减持窗口" if z.get('reduction_warning') else ""
            print(f"  - #{i+1}: {z['start']}~{z['end']} ({z['days']}天) 成本{z['cost_price']:.2f} [{z['zone_low']:.2f}~{z['zone_high']:.2f}] 放量{z['vol_ratio']:.1f}x{warn}")
    else:
        # 无成本区：用SMA120做基准
        print(f"\n**成本区**: 未检测到明显放量横盘区，使用SMA120基准")
    
    print(f"\n  - **筹码**: {r['hold_detail']}")
    print(f"  - **净利润**: {r['profit_trend'][1]}")
    print(f"  - **持仓时间**: ~{r['hold_duration_days']}天")
    
    if r['sector_info']:
        s = r['sector_info']
        print(f"  - **板块** ({s['industry']}): 行业均损{s['avg_loss']:+.1f}% 锁仓率{s['lock_ratio']:.0%} ({s['sample_count']}只)")
    
    print(f"\n**信号**: {r['signal']} | 目标**+{r['target_up_pct']:.1f}%**")


def scan_v8(cur, target_date=None):
    """扫V8候选池"""
    if target_date is None:
        cur.execute("SELECT MAX(trade_date) FROM daily_prices")
        target_date = cur.fetchone()[0]
    
    cur.execute("SELECT code FROM mv_v8_candidates WHERE trade_date=%s ORDER BY code", (target_date,))
    codes = [r[0] for r in cur.fetchall()]
    
    if not codes:
        cur.execute("SELECT MAX(trade_date) FROM mv_v8_candidates")
        latest = cur.fetchone()[0]
        cur.execute("SELECT code FROM mv_v8_candidates WHERE trade_date=%s", (latest,))
        codes = [r[0] for r in cur.fetchall()]
    
    print(f"\n扫描 {len(codes)} 只V8候选...", file=sys.stderr)
    
    results = []
    for code in codes:
        r = analyze_stock(code, cur, target_date)
        if 'error' not in r:
            cur.execute("SELECT stock_name FROM stock_industry WHERE stock_code=%s", (code,))
            nr = cur.fetchone()
            r['stock_name'] = nr[0] if nr else ''
            results.append(r)
        if len(results) % 200 == 0:
            print(f"  {len(results)}/{len(codes)}", file=sys.stderr)
    
    return results


def save_json(results, fname):
    if not os.path.exists(OUT_DIR):
        os.makedirs(OUT_DIR, exist_ok=True)
    results.sort(key=lambda r: r.get('score', 0), reverse=True)
    path = os.path.join(OUT_DIR, fname)
    with open(path, 'w') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"缓存: {path} ({len(results)}只)", file=sys.stderr)
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='机构持仓成本分析v2')
    parser.add_argument('--code', type=str, default='002073')
    parser.add_argument('--v8-candidates', action='store_true')
    parser.add_argument('--save', action='store_true')
    args = parser.parse_args()
    
    conn = get_pg()
    cur = conn.cursor()
    
    cur.execute("SELECT MAX(trade_date) FROM daily_prices")
    td = cur.fetchone()[0] or date.today()
    target = td if isinstance(td, date) else date.fromisoformat(str(td))
    
    if args.v8_candidates:
        results = scan_v8(cur, target)
        if args.save:
            save_json(results, f'institution_cost_v2_{target}.json')
        
        # TOP30表格
        results.sort(key=lambda r: r.get('score', 0), reverse=True)
        print(f"\n## V8候选机构成本分析 ({target})")
        print(f"\n| # | 代码 | 名称 | 评分 | 信号 | 目标% | 成本区 | 现价 | 亏损% | 筹码 | 净利润 | 持仓时间 |")
        print(f"|---|------|------|------|------|-------|--------|------|-------|------|--------|----------|")
        for i, r in enumerate(results[:30]):
            cz = r.get('cost_zones', [])
            cost_str = f"{r['cost_info']['weighted_cost']:.2f}" if r.get('cost_info') else 'N/A'
            name = r.get('stock_name', '')[:8]
            pt = r['profit_trend'][0].replace('improving','↑').replace('declining','↓').replace('stable','→').replace('unknown','?')
            duration = f"{r['hold_duration_days']}d" if r['hold_duration_days'] else '?'
            excl = "🚫" if r['is_excluded'] else ""
            print(f"| {i+1} | {r['code']}{excl} | {name} | {r['score']} | {r['signal']} | +{r['target_up_pct']:.1f}% | {cost_str} | {r['current_price']:.2f} | {r['effective_loss']:+.1f}% | {r['hold_state']} | {pt} | {duration} |")
    else:
        # 单只分析
        cur.execute("SELECT stock_name FROM stock_industry WHERE stock_code=%s", (args.code,))
        nr = cur.fetchone()
        name = nr[0] if nr else ''
        r = analyze_stock(args.code, cur, target)
        print_report(r, name)
        
        if args.save:
            save_json([r], f'institution_cost_v2_{args.code}_{target}.json')
    
    conn.close()
