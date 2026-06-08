#!/usr/bin/env python3
"""
筹码成本分析器 — 庄家成本反推 + 支撑强度评分
第一版 (2026-06-03)
===============
功能:
  1. 计算个股的成交量加权均价(VWAP) — 近20/60/120/250日
  2. 识别缩量横盘段 — 庄家吸筹特征
  3. 估算庄家成本区间
  4. 支撑强度评分(0-100)
  5. 输出JSON缓存供V8仪表盘/position_tracker调用

用法:
  python3 cost_structure.py                          # 大众公用示范
  python3 cost_structure.py --code 600635             # 指定单只
  python3 cost_structure.py --scan                    # 全市场(慢)
  python3 cost_structure.py --v8-candidates           # 只跑V8候选池
"""
import sys, os, json, argparse
import psycopg2
import numpy as np
from datetime import date, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT_DIR = '/home/tearsyu/.openclaw/workspace/memory/cost_cache'


def get_pg(autocommit=True):
    conn = psycopg2.connect(DB)
    conn.autocommit = autocommit
    return conn


def calc_vwap(closes, volumes, amounts):
    """计算VWAP（成交量加权均价）及各种统计"""
    n = len(closes)
    if n == 0:
        return {}
    result = {}
    
    # amount列经常缺失，用close*volume*100估算成交额
    # volume单位是"手"(1手=100股)，close是元/股
    # VWAP = sum(成交额) / sum(成交量_股) = sum(close*volume*100) / sum(volume*100)
    amount_est = closes * volumes * 100.0
    vol_in_shares = volumes * 100.0  # 手→股
    
    # 优先使用真实amount，如果大多数有效
    has_amount = np.sum(amounts > 0) > n * 0.5
    if has_amount:
        amt_series = amounts
    else:
        amt_series = amount_est
    
    # 全周期VWAP
    total_shares = np.sum(vol_in_shares)
    if total_shares > 0:
        result['vwap_total'] = float(np.sum(amt_series) / total_shares)
    
    # 各周期VWAP
    for period_name, days in [('20d',20), ('60d',60), ('120d',120), ('250d',250)]:
        if n >= days:
            seg_s = vol_in_shares[-days:]
            seg_a = amt_series[-days:]
            total_s = np.sum(seg_s)
            result[f'vwap_{period_name}'] = float(np.sum(seg_a) / total_s) if total_s > 0 else None
        else:
            result[f'vwap_{period_name}'] = None
    
    # 均价 (简单算术平均)
    for period_name, days in [('20d',20), ('60d',60), ('120d',120), ('250d',250)]:
        if n >= days:
            result[f'avg_{period_name}'] = float(np.mean(closes[-days:]))
        else:
            result[f'avg_{period_name}'] = None
    
    # 当前价相对各周期VWAP的折溢价
    cur = float(closes[-1]) if len(closes) > 0 else None
    if cur:
        for k in ['vwap_20d','vwap_60d','vwap_120d','vwap_250d','vwap_total']:
            if k in result and result[k] and result[k] > 0:
                result[f'{k}_discount'] = cur / result[k] - 1
            else:
                result[f'{k}_discount'] = None
    
    return result


def find_consolidation_periods(closes, volumes, dates, min_days=15, vol_cv_thresh=0.5, chg_thresh=0.04):
    """
    识别缩量横盘段（庄家吸筹特征）
    条件:
      - 连续>=min_days天
      - 成交量变异系数(CV) < vol_cv_thresh (量能稳定)
      - 最大绝对涨跌幅 < chg_thresh (价格横盘)
    """
    n = len(closes)
    if n < min_days:
        return []
    
    periods = []
    i = 0
    while i <= n - min_days:
        # 滑动窗口找符合条件的连续段
        seg_v = volumes[i:i+min_days]
        seg_c = closes[i:i+min_days]
        
        # 检查量能稳定和价格横盘
        avg_v = np.mean(seg_v)
        if avg_v > 0:
            cv = np.std(seg_v) / avg_v
            # 最大涨跌幅
            max_chg = max([abs(seg_c[j]/seg_c[j-1]-1) for j in range(1, len(seg_c))] + [0])
            
            if cv < vol_cv_thresh and max_chg < chg_thresh:
                # 尝试扩展窗口
                j = i + min_days
                while j < n:
                    extended_v = volumes[i:j+1]
                    extended_c = closes[i:j+1]
                    avg_v2 = np.mean(extended_v)
                    if avg_v2 > 0:
                        cv2 = np.std(extended_v) / avg_v2
                        max_chg2 = max([abs(extended_c[k]/extended_c[k-1]-1) for k in range(1, len(extended_c))] + [0])
                        if cv2 < vol_cv_thresh * 1.2 and max_chg2 < chg_thresh * 1.2:
                            j += 1
                            continue
                    break
                
                avg_price = float(np.mean([closes[k] for k in range(i, j)]))
                mid_vol = float(avg_v / 1e4)
                periods.append({
                    'start': str(dates[i]),
                    'end': str(dates[j-1]),
                    'days': j - i,
                    'avg_price': round(avg_price, 2),
                    'avg_vol_wan': round(mid_vol, 0),
                    'vol_cv': round(cv, 2),
                    'max_chg': round(max_chg, 4),
                })
                i = j
                continue
        i += 1
    
    return periods


def estimate_holder_costs(periods, vwap_data, current_price, sma120):
    """
    估算各类资金的成本区间
    根据缩量横盘段的均价 + VWAP数据推导
    """
    if not periods:
        return {}
    
    result = {}
    
    # 庄家成本：最长的缩量横盘段均价
    # (庄家以月为单位建仓，不是几天)
    long_periods = [p for p in periods if p['days'] >= 40]
    if long_periods:
        # 按天数加权平均
        total_days = sum(p['days'] for p in long_periods)
        weighted_cost = sum(p['avg_price'] * p['days'] for p in long_periods) / total_days
        result['zhuangjia_cost'] = round(weighted_cost, 2)
        result['zhuangjia_days'] = total_days
    elif periods:
        # 最长的一段
        best = max(periods, key=lambda p: p['days'])
        result['zhuangjia_cost'] = best['avg_price']
        result['zhuangjia_days'] = best['days']
    
    if 'zhuangjia_cost' in result:
        zj = result['zhuangjia_cost']
        result['zhuangjia_profit'] = round(current_price / zj - 1, 4)
        # 庄家承受力: 从当前价跌到庄家成本的百分比
        result['zhuangjia_cushion'] = round(zj / current_price - 1, 4)
    
    # 游资/机构成本：近似用近60日VWAP（中短期资金）
    vwap_60 = vwap_data.get('vwap_60d')
    if vwap_60:
        result['youzi_cost'] = round(vwap_60, 2)
        result['youzi_profit'] = round(current_price / vwap_60 - 1, 4)
    
    # 机构成本：近120日VWAP
    vwap_120 = vwap_data.get('vwap_120d')
    if vwap_120:
        result['jigou_cost'] = round(vwap_120, 2)
        result['jigou_profit'] = round(current_price / vwap_120 - 1, 4)
    
    # SMA120基准
    result['sma120'] = round(float(sma120), 2) if sma120 else None
    
    return result


def calc_support_score(cost_data, vwap_data, current_price, sma_dev):
    """
    支撑强度评分 (0-100)
    """
    if not cost_data:
        return 0, {}
    
    score = 50  # 基准分
    factors = {}
    
    # 1. 庄家成本折价 (max 25分)
    zj_profit = cost_data.get('zhuangjia_profit')
    if zj_profit is not None:
        if zj_profit < 0:
            # 当前价低于庄家成本 = 庄家浮亏 = 极其安全但罕见
            score += 25
            factors['zhuangjia_discount'] = 25
        elif zj_profit < 0.5:
            # 庄家浮盈<50% = 相对安全
            factor = 25 * (1 - zj_profit/0.5)
            score += max(0, factor)
            factors['zhuangjia_discount'] = round(max(0, factor), 1)
        else:
            # 庄家浮盈>50% = 他有空间继续砸
            score -= 5
            factors['zhuangjia_discount'] = -5
    
    # 2. VWAP偏离 (max 20分)
    vwap_disc = vwap_data.get('vwap_60d_discount')
    if vwap_disc is not None:
        if vwap_disc < 0:
            # 低于60日均价 = 有支撑
            factor = min(20, -vwap_disc * 100)
            score += max(0, factor)
            factors['vwap_discount'] = round(max(0, factor), 1)
        else:
            score -= 5
            factors['vwap_discount'] = -5
    
    # 3. 缩量横盘总天数 (max 20分)
    zj_days = cost_data.get('zhuangjia_days', 0)
    if zj_days >= 100:
        score += 20
        factors['consolidation_days'] = 20
    elif zj_days >= 60:
        score += 15
        factors['consolidation_days'] = 15
    elif zj_days >= 30:
        score += 10
        factors['consolidation_days'] = 10
    elif zj_days >= 15:
        score += 5
        factors['consolidation_days'] = 5
    
    # 4. SMA偏离深度 (max 20分)
    if sma_dev is not None:
        if sma_dev < -0.30:
            score += 20
            factors['sma_deviation'] = 20
        elif sma_dev < -0.25:
            score += 15
            factors['sma_deviation'] = 15
        elif sma_dev < -0.20:
            score += 10
            factors['sma_deviation'] = 10
        elif sma_dev < -0.15:
            score += 5
            factors['sma_deviation'] = 5
    
    # 5. 游资浮亏惩罚 (max -15分)
    yz_profit = cost_data.get('youzi_profit')
    if yz_profit is not None and yz_profit < -0.20:
        # 游资亏损超20% → 有割肉出逃风险
        score -= 10
        factors['youzi_panic'] = -10
    
    return min(100, max(0, score)), factors


def analyze_stock(code, cur, target_date=None):
    """分析单只股票的筹码成本结构"""
    if target_date is None:
        cur.execute("SELECT MAX(trade_date) FROM daily_prices")
        target_date = cur.fetchone()[0]
    
    # 取历史日线
    symbol = 'A.' + code
    cur.execute("""
        SELECT trade_date, close, volume, amount
        FROM daily_prices WHERE symbol=%s
        AND trade_date <= %s AND close > 0
        ORDER BY trade_date
    """, (symbol, target_date))
    rows = cur.fetchall()
    
    if len(rows) < 20:
        return {'code': code, 'error': '数据不足', 'n_days': len(rows)}
    
    dates = [r[0] for r in rows]
    closes = np.array([float(r[1]) for r in rows], dtype=float)
    volumes = np.array([float(r[2]) if r[2] else 0 for r in rows], dtype=float)
    amounts = np.array([float(r[3]) if r[3] else 0 for r in rows], dtype=float)
    
    n = len(closes)
    cur_price = float(closes[-1])
    
    # SMA120
    if n >= 120:
        sma120 = float(np.mean(closes[-120:]))
        sma_dev = cur_price / sma120 - 1
    else:
        sma120 = None
        sma_dev = None
    
    # 1. VWAP计算
    vwap_data = calc_vwap(closes, volumes, amounts)
    
    # 2. 缩量横盘段
    periods = find_consolidation_periods(closes, volumes, dates)
    
    # 3. 庄家成本估算
    cost_data = estimate_holder_costs(periods, vwap_data, cur_price, sma120)
    
    # 4. 支撑强度评分
    score, factors = calc_support_score(cost_data, vwap_data, cur_price, sma_dev)
    
    # 5. 汇总
    result = {
        'code': code,
        'target_date': str(target_date),
        'n_days': n,
        'current_price': cur_price,
        'sma120': sma120,
        'sma_dev': sma_dev,
        'sma_dev_pct': f"{sma_dev:+.2%}" if sma_dev else None,
        'vwap': vwap_data,
        'consolidation_periods': len(periods),
        'cost_estimate': cost_data,
        'support_score': score,
        'score_factors': factors,
    }
    
    # 6. 释义
    if score >= 80:
        result['verdict'] = 'RED 强支撑 — 庄家成本明显低于现价，缩量横盘充分'
    elif score >= 65:
        result['verdict'] = 'ORANGE 中等支撑 — 有庄家成本锚定，但安全边际一般'
    elif score >= 50:
        result['verdict'] = 'YELLOW 偏弱支撑 — 庄家成本偏高或横盘不足'
    else:
        result['verdict'] = 'WHITE 基本面支撑为主，无明显庄家成本锚定'
    
    return result


def full_scan(cur, target_date=None):
    """全市场扫描"""
    if target_date is None:
        cur.execute("SELECT MAX(trade_date) FROM daily_prices")
        target_date = cur.fetchone()[0]
    
    # 从V8候选物化视图读
    cur.execute("""
        SELECT code FROM mv_v8_candidates 
        WHERE trade_date = %s
    """, (target_date,))
    codes = [r[0] for r in cur.fetchall()]
    
    if not codes:
        # 回退：从daily_prices读当日所有symbol
        cur.execute("""
            SELECT DISTINCT substring(symbol, 3) FROM daily_prices
            WHERE trade_date = %s AND close > 0 AND symbol LIKE 'A.%%'
        """, (target_date,))
        codes = [r[0] for r in cur.fetchall()]
    
    results = []
    for code in codes:
        r = analyze_stock(code, cur, target_date)
        if 'error' not in r:
            results.append(r)
        if len(results) % 500 == 0:
            print(f'  扫描进度: {len(results)}/{len(codes)}', file=sys.stderr)
    
    return results


def save_cache(results, filename):
    """保存到JSON缓存"""
    if not os.path.exists(OUT_DIR):
        os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, filename)
    # 结果按支撑强度排序再存
    results.sort(key=lambda r: r.get('support_score', 0), reverse=True)
    with open(path, 'w') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f'缓存保存至: {path} ({len(results)}只)', file=sys.stderr)
    return path


def print_report(result, stock_name=''):
    """打印单只股票分析报告"""
    r = result
    if 'error' in r:
        print(f"\n=== {r.get('code','?')} {stock_name} — ❌ {r['error']} ===")
        return
    
    print(f"\n{'='*70}")
    print(f"  {r['code']} {stock_name}  —  筹码成本分析报告")
    print(f"  数据日期: {r['target_date']}  |  样本天数: {r['n_days']}")
    print(f"{'='*70}")
    
    print(f"\n  📊 行情")
    print(f"  ├  现价: {r['current_price']:.2f}")
    print(f"  ├  SMA120: {r['sma120']:.2f}  |  偏离: {r.get('sma_dev_pct','?')}")
    print(f"  └  支撑强度评分: **{r['support_score']:.0f}/100** — {r['verdict']}")
    
    print(f"\n  📈 成交量加权均价(VWAP)")
    for k in ['20d','60d','120d','250d']:
        vwap = r['vwap'].get(f'vwap_{k}')
        disc = r['vwap'].get(f'vwap_{k}_discount')
        if vwap:
            print(f"  ├  {k:>4s} VWAP: {vwap:.2f}  |  折价: {disc:+.2%}")
        else:
            print(f"  ├  {k:>4s} VWAP: {'—':>8s}")
    
    print(f"\n  🏛️  庄家行为分析")
    print(f"  ├  缩量横盘段: {r.get('consolidation_periods',0)}个")
    
    ce = r.get('cost_estimate', {})
    if ce.get('zhuangjia_cost'):
        print(f"  ├  🔴 庄家估算成本: {ce['zhuangjia_cost']:.2f}")
        print(f"  ├     浮盈: {ce.get('zhuangjia_profit',0):+.2%}")
        print(f"  ├    承受力: 庄家能承受跌到{ce['zhuangjia_cost']:.2f}")
        print(f"  ├    建仓天数: ~{ce.get('zhuangjia_days','?')}天")
    if ce.get('youzi_cost'):
        print(f"  ├  🟡 游资估算成本: {ce['youzi_cost']:.2f} (近60日VWAP)")
        print(f"  ├     浮亏: {ce.get('youzi_profit',0):+.2%}")
    if ce.get('jigou_cost'):
        print(f"  ├  🟠 机构估算成本: {ce['jigou_cost']:.2f} (近120日VWAP)")
        print(f"  ├     浮亏: {ce.get('jigou_profit',0):+.2%}")
    
    print(f"\n  📋 评分明细")
    for k, v in r.get('score_factors', {}).items():
        prefix = '+' if v >= 0 else ''
        print(f"  ├  {k}: {prefix}{v:.1f}")
    
    print(f"\n{'='*70}\n")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='筹码成本分析器')
    parser.add_argument('--code', type=str, default='600635', help='股票代码')
    parser.add_argument('--scan', action='store_true', help='全市场扫描')
    parser.add_argument('--v8-candidates', action='store_true', help='只扫V8候选池')
    parser.add_argument('--save', action='store_true', help='保存缓存')
    parser.add_argument('--sort-by', type=str, default='score', choices=['score','sma','vwap'], help='排序方式')
    args = parser.parse_args()
    
    conn = get_pg()
    cur = conn.cursor()
    
    # 最新交易日期
    cur.execute("SELECT MAX(trade_date) FROM daily_prices")
    target = cur.fetchone()[0]
    
    if args.scan or args.v8_candidates:
        if args.v8_candidates:
            print(f'扫描V8候选池...', file=sys.stderr)
        else:
            print(f'全市场扫描...', file=sys.stderr)
        results = full_scan(cur, target)
        
        if args.save:
            fname = f'cost_structure_{target}.json'
            path = save_cache(results, fname)
            print(f'\n缓存文件: {path}')
        
        # 打印TOP10
        results.sort(key=lambda r: r.get('support_score', 0), reverse=True)
        print(f'\n=== 支撑强度 TOP 20 ===')
        print(f'{"#":<5}{"代码":>8} {"现价":>7} {"SMA偏离":>9} {"评分":>5} {"庄家成本":>9} {"庄家浮盈":>9} {"横盘段":>6}')
        print(f'{"─"*60}')
        for i, r in enumerate(results[:20]):
            ce = r.get('cost_estimate', {})
            zj_cost = ce.get('zhuangjia_cost', 0)
            zj_profit = ce.get('zhuangjia_profit', 0)
            cp = r.get('current_price', 0)
            dev = r.get('sma_dev_pct', '?')
            n_cons = r.get('consolidation_periods', 0)
            print(f'{i+1:<5}{r["code"]:>8} {cp:>7.2f} {dev:>9} {r["support_score"]:>5.0f} {zj_cost:>9} {zj_profit:>+9.2%} {n_cons:>6}')
    else:
        # 单只分析
        # 查名称
        cur.execute("SELECT stock_name FROM stock_industry WHERE stock_code=%s", (args.code,))
        name_r = cur.fetchone()
        stock_name = name_r[0] if name_r else ''
        
        result = analyze_stock(args.code, cur, target)
        print_report(result, stock_name)
        
        if args.save:
            fname = f'cost_structure_{args.code}_{target}.json'
            save_cache([result], fname)
    
    conn.close()
