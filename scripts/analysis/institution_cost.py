#!/usr/bin/env python3
"""
机构持仓成本分析器 (2026-06-03)
=========================
核心思路：机构建仓 = 放量横盘期
- 放量：日均成交 > 前60日均量 × 1.3
- 横盘：区间振幅 < 15%
- 建仓成本 = 放量横盘期的 VWAP（量价加权均价）
- 机构被套深度 = (成本区均价 - 当前价) / 成本区均价
- 筹码锁定度 = 近期换手率 / 历史换手率（缩量=筹码还在）

用法:
  python3 institution_cost.py --code 002073          # 单只分析
  python3 institution_cost.py --v8-candidates         # 扫V8候选池
  python3 institution_cost.py --code 600782 --include-signals  # 含交易建议
"""
import sys, os, json, argparse
import psycopg2
import numpy as np
from datetime import date, timedelta

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT_DIR = '/home/tearsyu/.openclaw/workspace/memory/cost_cache'


def get_pg():
    conn = psycopg2.connect(DB)
    conn.autocommit = True
    return conn


def find_institution_cost_zones(closes, volumes, dates, lookback=250):
    """
    核心算法：找机构建仓成本区
    
    机构建仓特征（区别于游资/散户）：
    - 时间够长（至少15天以上，机构不是一天建完的）
    - 价格横盘（振幅<15%，机构不希望建仓期价格抬升）
    - 成交量放大（持续有买入，日均量>前60日均量×1.3）
    - 区间内量能稳定（变异系数小，不是偶发脉冲）
    
    返回：候选成本区列表，按"放量强度 × 持续时间"排序
    """
    n = len(closes)
    if n < 120:
        return []
    
    zones = []
    
    # 全周期6-60日均量（用于放量判断）
    vol_ma60 = np.convolve(volumes, np.ones(60)/60, mode='same')
    # 前60日均量（基准）
    
    # 滑动窗口扫描：从最近250天往前扫
    min_win = 15
    max_win = 120
    
    for start in range(max(0, n - lookback - max_win), n - min_win):
        # 跳过波动太大的时段
        for end in range(min(n, start + min_win), min(n, start + max_win)):
            seg_vol = volumes[start:end]
            seg_close = closes[start:end]
            seg_dates = dates[start:end]
            days = end - start
            
            # 条件1：时间够长
            if days < 15:
                continue
            
            # 条件2：价格横盘（振幅<15%）
            price_high = np.max(seg_close)
            price_low = np.min(seg_close)
            if price_low <= 0 or price_high / price_low > 1.15:
                # 振幅太大，不是横盘建仓
                # 但要容忍单边建仓（机构拉高建仓）：看价量相关性
                continue
            
            # 条件3：放量（日均量 > 前60日均量×1.3）
            pre_vol = np.mean(volumes[max(0, start-60):start]) if start >= 60 else np.mean(volumes[:start])
            avg_vol = np.mean(seg_vol)
            if pre_vol > 0 and avg_vol / pre_vol < 1.3:
                continue  # 不够放量，不是机构在建仓
            
            # 条件4：量能稳定（变异系数 < 0.8，不是偶发脉冲）
            vol_cv = np.std(seg_vol) / avg_vol if avg_vol > 0 else 99
            if vol_cv > 0.8:
                continue
            
            # 条件5：区间内量价负相关（越跌越买=吸筹特征）
            # 用Spearman rank 近似
            if days >= 20:
                price_ranks = np.argsort(seg_close)
                vol_ranks = np.argsort(seg_vol)
                # 如果量在低价位更大 → 负相关
                # 简单做法：看前半段价格vs后半段价格
                mid = days // 2
                first_half_price = np.mean(seg_close[:mid])
                second_half_price = np.mean(seg_close[mid:])
                # 如果价格轻微波动但量不降，说明在吸收
                # 不强制负相关，放宽条件
            
            # 计算成本区 = VWAP
            # volume是手(1手=100股)，乘以100转成股
            total_vol_shares = np.sum(seg_vol) * 100
            if total_vol_shares > 0:
                # 成交额 = close × vol × 100 (amount列不可靠)
                total_amount = np.sum(seg_close * seg_vol * 100)
                zone_price = total_amount / total_vol_shares
            else:
                zone_price = np.mean(seg_close)
            
            # 放量强度
            vol_ratio = avg_vol / pre_vol if pre_vol > 0 else 1.0
            
            zones.append({
                'start': str(seg_dates[0]),
                'end': str(seg_dates[-1]),
                'days': days,
                'cost_price': round(float(zone_price), 2),
                'zone_high': round(float(price_high), 2),
                'zone_low': round(float(price_low), 2),
                'avg_vol': int(avg_vol),
                'vol_ratio': round(float(vol_ratio), 2),
                'vol_cv': round(float(vol_cv), 2),
                'score': round(days * vol_ratio / (1 + vol_cv), 1),  # 质量评分
            })
            
            # 跳过这段（避免重叠区间）
            break  # 只取最早满足的end，然后继续移动start
    
    # 去重并排序：按质量评分从高到低
    zones.sort(key=lambda z: z['score'], reverse=True)
    
    # 合并重叠区（保留质量最高的）
    merged = []
    for z in zones:
        z_start = date.fromisoformat(z['start'])
        z_end = date.fromisoformat(z['end'])
        # 检查是否与已选区重叠
        overlapping = False
        for mz in merged:
            mz_start = date.fromisoformat(mz['start'])
            mz_end = date.fromisoformat(mz['end'])
            if not (z_end < mz_start or z_start > mz_end):
                overlapping = True
                break
        if not overlapping:
            merged.append(z)
    
    return merged[:5]  # 最多返回5个非重叠区


def calc_hold_liquidity(closes, volumes):
    """
    判断筹码是否还在机构手里
    
    核心：缩量到极致 = 没人卖了 = 机构没出货
    放量 = 有人在买卖，可能是机构在出货
    
    返回：
      - 筹码锁定度（0~1，越高越好）
      - 状态：'locked'(锁仓) / 'normal'(正常) / 'draining'(出逃)
    """
    n = len(closes)
    if n < 60:
        return 0, 'unknown'
    
    # 近期换手率 vs 历史换手率
    recent_vol = np.mean(volumes[-20:]) if n >= 20 else np.mean(volumes)
    hist_vol = np.mean(volumes[-120:]) if n >= 120 else np.mean(volumes)
    
    vol_ratio = recent_vol / hist_vol if hist_vol > 0 else 1.0
    
    # 锁仓 = 近期量能萎缩到历史均值60%以下
    if vol_ratio < 0.6:
        return round(vol_ratio, 2), 'locked'
    elif vol_ratio < 0.85:
        return round(vol_ratio, 2), 'normal'
    elif vol_ratio < 1.2:
        return round(vol_ratio, 2), 'normal'
    else:
        return round(vol_ratio, 2), 'draining'


def calc_institution_signal(cost_zones, current_price, sma_dev, hold_state):
    """
    综合信号：机构被套深度 + 筹码状态 → 交易信号
    
    输出：
      - motive: 自救动机 (0~100)
      - signal: BUY / HOLD / WATCH / AVOID
      - target_up: 目标涨幅（到成本区）
    """
    if not cost_zones:
        return 0, 'INSUFFICIENT_DATA', 0, {}
    
    best = cost_zones[0]
    zone_price = best['cost_price']
    
    # 机构亏损深度
    loss_pct = (current_price - zone_price) / zone_price if zone_price > 0 else 0
    
    # 筹码锁定度
    lock_ratio = hold_state[0]
    lock_status = hold_state[1]
    
    # ===== 动机评分 =====
    motive = 0
    
    # 1. 亏损深度贡献 (max 40分)
    if loss_pct < -0.30:
        motive += 40  # 亏超30%，必自救
    elif loss_pct < -0.20:
        motive += 30
    elif loss_pct < -0.10:
        motive += 20
    elif loss_pct < -0.05:
        motive += 10
    
    # 2. 筹码锁定贡献 (max 30分)
    if lock_status == 'locked':
        motive += 30  # 锁仓 = 机构没逃，有自救能力
    elif lock_status == 'normal':
        motive += 15
    elif lock_status == 'draining':
        motive -= 15  # 在出货 = 自救动机弱
    
    # 3. SMA超跌贡献 (max 20分)
    if sma_dev is not None and sma_dev < 0:
        if sma_dev < -0.25:
            motive += 20
        elif sma_dev < -0.20:
            motive += 15
        elif sma_dev < -0.15:
            motive += 10
        elif sma_dev < -0.10:
            motive += 5
    
    # 4. 建仓时间权重 (max 10分)
    if best['days'] >= 60:
        motive += 10  # 建了2个月以上，不舍得放弃
    elif best['days'] >= 30:
        motive += 5
    
    # ===== 信号 =====
    if motive >= 60 and loss_pct < -0.15 and lock_status == 'locked':
        signal = 'BUY'
    elif motive >= 40 and loss_pct < -0.10:
        signal = 'HOLD'
    elif motive >= 20:
        signal = 'WATCH'
    else:
        signal = 'AVOID'
    
    target_up = zone_price / current_price - 1 if current_price > 0 else 0
    
    factors = {
        'loss_pct': loss_pct,
        'hold_state': lock_status,
        'motive_score': motive,
    }
    
    return motive, signal, target_up, factors


def analyze_stock(code, cur, target_date=None):
    """分析单只股票的机构成本"""
    if target_date is None:
        cur.execute("SELECT MAX(trade_date) FROM daily_prices")
        target_date = cur.fetchone()[0]
    
    symbol = 'A.' + code
    cur.execute("""
        SELECT trade_date, close, volume, amount
        FROM daily_prices WHERE symbol=%s
        AND trade_date <= %s AND close > 0
        ORDER BY trade_date
    """, (symbol, target_date))
    rows = cur.fetchall()
    
    if len(rows) < 120:
        return {'code': code, 'error': f'数据不足({len(rows)}/120天)', 'n_days': len(rows)}
    
    dates = [r[0] for r in rows]
    closes = np.array([float(r[1]) for r in rows], dtype=float)
    volumes = np.array([float(r[2]) if r[2] else 0 for r in rows], dtype=float)
    
    n = len(closes)
    current_price = float(closes[-1])
    
    # SMA120
    sma120 = float(np.mean(closes[-120:])) if n >= 120 else None
    sma_dev = current_price / sma120 - 1 if sma120 else None
    
    # ===== 核心分析 =====
    
    # 1. 机构成本区
    cost_zones = find_institution_cost_zones(closes, volumes, dates)
    
    # 2. 筹码锁定度
    hold_state = calc_hold_liquidity(closes, volumes)
    
    # 3. 综合信号
    motive, signal, target_up, factors = calc_institution_signal(
        cost_zones, current_price, sma_dev, hold_state
    )
    
    # 4. VWAP参考
    vwap_60 = float(np.sum(closes[-60:] * volumes[-60:] * 100) / np.sum(volumes[-60:] * 100)) if n >= 60 else 0
    vwap_120 = float(np.sum(closes[-120:] * volumes[-120:] * 100) / np.sum(volumes[-120:] * 100)) if n >= 120 else 0
    vwap_250 = float(np.sum(closes[-250:] * volumes[-250:] * 100) / np.sum(volumes[-250:] * 100)) if n >= 250 else 0
    
    return {
        'code': code,
        'target_date': str(target_date),
        'n_days': n,
        'current_price': current_price,
        'sma120': sma120,
        'sma_dev': sma_dev,
        'vwap_60d': round(float(vwap_60), 2),
        'vwap_120d': round(float(vwap_120), 2),
        'vwap_250d': round(float(vwap_250), 2),
        'cost_zones': cost_zones,
        'hold_state': hold_state[1],
        'hold_ratio': hold_state[0],
        'motive_score': motive,
        'signal': signal,
        'target_up_pct': round(float(target_up * 100), 1),
        'factors': factors,
    }


def print_report(result, stock_name=''):
    """输出分析报告"""
    r = result
    if 'error' in r:
        print(f"\n=== {r.get('code','?')} {stock_name} — ❌ {r['error']} ===")
        return
    
    print(f"\n{'='*72}")
    print(f"  {r['code']} {stock_name}  —  机构持仓成本分析")
    print(f"  数据日期: {r['target_date']}  |  样本: {r['n_days']}天")
    print(f"{'='*72}")
    
    # 行情
    print(f"\n  📊 行情")
    print(f"  现价: {r['current_price']:.2f}  |  SMA120: {r['sma120']:.2f}  |  偏离: {r['sma_dev']:+.2%}")
    print(f"  VWAP: 60d={r['vwap_60d']:.2f}  120d={r['vwap_120d']:.2f}  250d={r['vwap_250d']:.2f}")
    
    # 成本区
    cz = r['cost_zones']
    print(f"\n  🏛️  机构成本区 ({len(cz)}个)")
    if cz:
        for i, z in enumerate(cz):
            loss = (r['current_price'] - z['cost_price']) / z['cost_price']
            print(f"   ├ #{i+1}: {z['start']}~{z['end']} ({z['days']}天)")
            print(f"   │  成本: {z['cost_price']:.2f}  振幅: [{z['zone_low']:.2f}~{z['zone_high']:.2f}]")
            print(f"   │  放量比: {z['vol_ratio']:.2f}x  量CV: {z['vol_cv']:.2f}")
            print(f"   │  机构亏损: {loss:+.2%}  (现价{z['cost_price']-r['current_price']:.2f}元之下)")
    
    # 筹码状态
    print(f"\n  🔒 筹码状态")
    hs = r['hold_state']
    hr = r['hold_ratio']
    if hs == 'locked':
        print(f"  锁仓 ({hr:.2f}x) — 量能萎缩到均值{hr*100:.0f}%，机构没出货")
    elif hs == 'draining':
        print(f"  ⚠️ 出逃 ({hr:.2f}x) — 近期放量，机构可能在出货")
    else:
        print(f"  正常换手 ({hr:.2f}x)")
    
    # 信号
    print(f"\n  🚦 交易信号")
    sig = r['signal']
    if sig == 'BUY':
        print(f"  ✅ BUY — 机构被套{abs(r['factors']['loss_pct'])*100:.0f}%+锁仓+超跌")
    elif sig == 'HOLD':
        print(f"  🔄 HOLD — 机构被套但信号不够强")
    elif sig == 'WATCH':
        print(f"  👁️  WATCH — 继续观察")
    else:
        print(f"  ⛔ {sig}")
    
    print(f"  自救动机评分: {r['motive_score']}/100")
    print(f"  目标涨幅(到成本区): +{r['target_up_pct']:.1f}%")
    print(f"{'='*72}\n")


def scan_v8_candidates(cur, target_date=None):
    """扫V8候选池"""
    if target_date is None:
        cur.execute("SELECT MAX(trade_date) FROM daily_prices")
        target_date = cur.fetchone()[0]
    
    cur.execute("""
        SELECT code, stock_name FROM mv_v8_candidates 
        WHERE trade_date = %s ORDER BY code
    """, (target_date,))
    candidates = cur.fetchall()
    
    if not candidates:
        cur.execute("SELECT MAX(trade_date) FROM mv_v8_candidates")
        latest = cur.fetchone()[0]
        cur.execute("SELECT code, stock_name FROM mv_v8_candidates WHERE trade_date=%s", (latest,))
        candidates = cur.fetchall()
    
    print(f"\n扫描 {len(candidates)} 只V8候选...\n", file=sys.stderr)
    
    results = []
    for code, name in candidates:
        r = analyze_stock(code, cur, target_date)
        if 'error' not in r:
            r['stock_name'] = name
            results.append(r)
        if len(results) % 100 == 0:
            print(f"  进度: {len(results)}/{len(candidates)}", file=sys.stderr)
    
    return results


def save_results(results, filename):
    """保存JSON"""
    if not os.path.exists(OUT_DIR):
        os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, filename)
    results.sort(key=lambda r: r.get('motive_score', 0), reverse=True)
    with open(path, 'w') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f'缓存: {path} ({len(results)}只)', file=sys.stderr)
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='机构持仓成本分析')
    parser.add_argument('--code', type=str, default='002073', help='股票6位代码')
    parser.add_argument('--v8-candidates', action='store_true', help='扫V8候选池')
    parser.add_argument('--save', action='store_true', help='保存JSON')
    parser.add_argument('--include-signals', action='store_true', help='输出交易建议')
    args = parser.parse_args()
    
    conn = get_pg()
    cur = conn.cursor()
    
    cur.execute("SELECT MAX(trade_date) FROM daily_prices")
    target = cur.fetchone()[0]
    today = date.today()
    if target is None:
        target = today
    
    if args.v8_candidates:
        results = scan_v8_candidates(cur, target)
        if args.save:
            fname = f'institution_cost_{target}.json'
            save_results(results, fname)
        
        # 打印TOP信号
        results.sort(key=lambda r: r.get('motive_score', 0), reverse=True)
        print(f"\n{'='*100}")
        print(f"  {'#':<4} {'代码':>8} {'名称':<10} {'评分':>5} {'信号':<10} {'目标%':>7} {'成本区':>10} {'现价':>7} {'筹码':<8} {'亏损':>8}")
        print(f"{'─'*100}")
        for i, r in enumerate(results[:30]):
            cost_str = str(r['cost_zones'][0]['cost_price']) if r.get('cost_zones') else 'N/A'
            loss = r['factors'].get('loss_pct', 0) * 100 if r.get('factors') else 0
            print(f"  {i+1:<4} {r['code']:>8} {r.get('stock_name',''):<10} {r['motive_score']:>5} {r['signal']:<10} +{r['target_up_pct']:<5.1f}% {cost_str:>10} {r['current_price']:<7.2f} {r['hold_state']:<8} {loss:>+7.1f}%")
    else:
        # 单只
        cur.execute("SELECT stock_name FROM stock_industry WHERE stock_code=%s", (args.code,))
        name_r = cur.fetchone()
        stock_name = name_r[0] if name_r else ''
        
        result = analyze_stock(args.code, cur, target)
        print_report(result, stock_name)
        
        if args.save:
            fname = f'institution_cost_{args.code}_{target}.json'
            save_results([result], fname)
    
    conn.close()
