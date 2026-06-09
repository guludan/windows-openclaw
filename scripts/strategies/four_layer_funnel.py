#!/usr/bin/env python3
"""
四层漏斗扫描器 — 熊市底部识别

第一层: V8超跌筛选 (SMA<-15%, PB<1.0 + Layer 0排雷)
第二层: 主力活跃度过滤 (换手>2%, 放量脉冲, 快速修复)
第三层: 熵法成本分析 (庄家真实成本, 当前价vs成本, 支撑定性)
第四层: 个股逐日滚动回测 (T+20胜率)

输出: 最终交易清单
"""
import sys, json
sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import numpy as np
from datetime import date, datetime, timedelta
from collections import defaultdict, OrderedDict
from strategies.active_filter import compute_active_score, DB, THRESHOLD
from strategies.active_filter import compute_cost_levels_entropy

def get_v8_candidates(conn, target_date, sma_thresh=-15, pb_max=1.0):
    """第一层: V8超跌筛选 + Layer 0排雷"""
    cur = conn.cursor()
    
    # 获取所有有日线的股票+最近收盘+SMA120
    cur.execute("""
        SELECT d.symbol, d.close, d.trade_date, d.volume, d.turnover, d.amount
        FROM daily_prices d
        JOIN stock_list sl ON d.symbol = 'A.' || sl.code
        WHERE d.trade_date = (
            SELECT MAX(trade_date) FROM daily_prices 
            WHERE symbol=d.symbol AND trade_date <= %s AND close>0
        )
        AND sl.active = true
    """, (target_date,))
    
    rows = cur.fetchall()
    candidates = []
    
    for r in rows:
        sym = r[0]
        code = sym[2:] if sym.startswith('A.') else sym
        close = float(r[1])
        
        # 取SMA120
        cur.execute("""
            SELECT AVG(close) FROM (
                SELECT close FROM daily_prices 
                WHERE symbol=%s AND trade_date<%s AND close>0
                ORDER BY trade_date DESC LIMIT 120
            ) t
        """, (sym, target_date))  # < 不含当天，避免前视偏差
        sma120 = cur.fetchone()[0]
        if not sma120 or sma120 == 0:
            continue
        sma_dev = (close / float(sma120) - 1) * 100
        
        # PB
        cur.execute("""
            SELECT close / NULLIF(bvps, 0) 
            FROM financial_data fd
            JOIN daily_prices d ON d.symbol=fd.symbol
            WHERE fd.symbol=%s AND bvps>0
            ORDER BY fd.end_date DESC LIMIT 1
        """, (sym,))
        pb_r = cur.fetchone()
        pb = float(pb_r[0]) if pb_r and pb_r[0] else 999
        
        if sma_dev < sma_thresh and pb < pb_max:
            candidates.append({
                'code': code, 'sym': sym, 'close': close,
                'sma_dev': round(sma_dev, 1), 'pb': round(pb, 2),
                'volume': float(r[3]) if r[3] else 0,
                'turnover': float(r[4]) if len(r) > 4 and r[4] and r[4] != 'None' else 0,
                'amount': float(r[5]) if len(r) > 5 and r[5] and r[5] != 'None' else 0,
            })
    
    return sorted(candidates, key=lambda x: x['sma_dev'])


def layer2_active_filter(conn, cand, target_date):
    """第二层: 主力活跃度过滤"""
    score = compute_active_score(conn, cand['sym'], target_date, lookback=60)
    if score is None:
        return None
    return {
        **cand,
        'active_score': score['score'],
        'active_pass': score['pass'],
        'elements': score['elements'],
    }


def layer3_entropy_analysis(conn, cand, target_date):
    """第三层: 熵法成本分析"""
    ea = compute_cost_levels_entropy(conn, cand['sym'], target_date, lookback=100)
    if ea is None or ea.get('true_cost_vwap') is None:
        return None
    
    tc = ea['true_cost_vwap']
    cur_price = cand['close']
    deviation = (cur_price / tc - 1) * 100
    
    return {
        **cand,
        'entropy': ea,
        'true_cost': round(tc, 2),
        'deviation_pct': round(deviation, 2),
        'low_safe': round(ea.get('low_safe_zone', 0), 2) if ea.get('low_safe_zone') else None,
        'accum_zones': ea.get('accumulation_zones', []),
        'entropy_z': ea.get('entropy_stats', {}).get('current_z', 0),
    }


def layer4_backtest(conn, cand, target_date):
    """第四层: 逐日滚动回测"""
    cur = conn.cursor()
    code = cand['code']
    sym = cand['sym']
    
    # 获取历史所有可交易日期价格，用numpy直接算，避免SQL回测慢
    start_date = target_date - timedelta(days=365*3)
    cur.execute("""
        SELECT trade_date, close
        FROM daily_prices
        WHERE symbol=%s AND trade_date BETWEEN %s AND %s
          AND close>0 AND volume>0
        ORDER BY trade_date
    """, (sym, start_date, target_date - timedelta(days=20)))
    rows = cur.fetchall()
    if len(rows) < 140:
        return None
    
    closes_arr = np.array([float(r[1]) for r in rows])
    
    trades = []
    for i in range(120, len(rows)):
        c = closes_arr[i]
        sma120 = np.mean(closes_arr[i-120:i])
        if c < sma120 * 0.85:
            end_idx = min(i + 20, len(closes_arr) - 1)
            if end_idx - i >= 20:
                ret20 = (closes_arr[end_idx] / c - 1) * 100
                trades.append(ret20)
    
    if len(trades) < 10:
        return None
    
    trades = np.array(trades)
    win_rate = np.mean(trades > 0) * 100
    avg_ret = np.mean(trades)
    med_ret = np.median(trades)
    max_loss = trades.min()
    
    return {
        **cand,
        'backtest_count': len(trades),
        't20_winrate': round(win_rate, 1),
        't20_avg': round(avg_ret, 2),
        't20_median': round(med_ret, 2),
        't20_max_loss': round(max_loss, 2),
        't20_grade': 'A' if win_rate >= 65 and max_loss > -15 else 'B' if win_rate >= 55 else 'C',
    }


def run_full_pipeline(target_date=None):
    """运行完整四层漏斗"""
    if target_date is None:
        target_date = date.today()
    
    conn = psycopg2.connect(DB)
    print(f"\n{'='*60}")
    print(f"四层漏斗扫描 {target_date}")
    print(f"{'='*60}\n")
    
    # === 第一层: V8超跌 ===
    print("【第一层】V8超跌筛选 (SMA<-15%, PB<1.0) ...")
    v8_cands = get_v8_candidates(conn, target_date)
    print(f"  通过: {len(v8_cands)} 只\n")
    
    # === 第二层: 主力活跃度 ===
    print("【第二层】主力活跃度过滤 ...")
    l2_passed = []
    for cand in v8_cands[:200]:  # 限制
        r = layer2_active_filter(conn, cand, target_date)
        if r and r['active_pass']:
            l2_passed.append(r)
    print(f"  通过: {len(l2_passed)} / {len(v8_cands)}\n")
    
    # 修正: L2不通过的不排除，仅降档标记
    l2_all = []
    for cand in l2_passed:
        cand['_l2_pass'] = True
        l2_all.append(cand)
    # 也把L2未通过的加入
    checked = set(c['sym'] for c in l2_passed)
    for cand in v8_cands[:200]:
        if cand['sym'] in checked:
            continue
        r = layer2_active_filter(conn, cand, target_date)
        if r and not r['active_pass']:
            r['_l2_pass'] = False
            l2_all.append(r)
    print(f"  通过: {len(l2_passed)} | 未通过(降档): {len(l2_all)-len(l2_passed)} / {len(v8_cands)}\n")
    
    # === 第三层: 熵法成本 ===
    print("【第三层】熵法成本分析 ...")
    l3_passed = []
    for cand in l2_all:
        r = layer3_entropy_analysis(conn, cand, target_date)
        if r and r['deviation_pct'] < 0:  # 跌破成本
            l3_passed.append(r)
    print(f"  当前价跌破庄家成本: {len(l3_passed)} / {len(l2_all)}\n")
    
    if not l3_passed:
        print("  没有跌破成本的标的，停止")
        conn.close()
        return
    
    # === 第四层: 回测验证 ===
    print("【第四层】逐日滚动回测 ...")
    final = []
    for cand in l3_passed:
        r = layer4_backtest(conn, cand, target_date)
        if r:
            final.append(r)
    print(f"  有足够历史数据: {len(final)} / {len(l3_passed)}\n")
    
    # === 输出结果 ===
    print(f"\n{'='*60}")
    print(f"四层漏斗最终结果")
    print(f"{'='*60}")
    
    # 按庄家被套深度排序（跌破成本越深越好）
    final.sort(key=lambda x: x['deviation_pct'])
    
    print(f"\n{'代码':<10} {'名称':<10} {'现价':>6} {'SMA偏离':>8} {'PB':>6} {'主力分':>5} {'L2':>4} {'庄家成本':>8} {'成本差':>7} {'防线':>6} {'T+20胜率':>8} {'评级':>5}")
    print('-' * 85)
    
    names = {'600635': '大众公用', '600273': '嘉化能源', '002073': '软控股份', '000912':'泸天化', '300284':'苏交科', '000850':'华茂股份', '600810':'神马股份', '000797':'中国武夷', '601366':'利群股份', '002187':'广百股份', '002614':'奥佳华'}
    for f in final[:15]:
        name = names.get(f['code'], f['code'])
        cost = f.get('true_cost', 0)
        low_safe = f.get('low_safe', 0)
        l2_tag = '✅' if f.get('_l2_pass', True) else '⚠️'
        if low_safe:
            low_safe = f'{low_safe:.2f}'
        else:
            low_safe = '-'
        print(f"{f['code']:<10} {name:<10} {f['close']:>6.2f} {f.get('sma_dev',0):>7.1f}% {f.get('pb',0):>5.1f} {f.get('active_score',0):>4d} {l2_tag:>4} {cost:>8.2f} {f.get('deviation_pct',0):>6.1f}% {low_safe:>6} {f.get('t20_winrate',0):>7.1f}% {f.get('t20_grade','-'):>5}")
    
    print(f"\n{'='*60}")
    print(f"交易建议清单")
    print(f"{'='*60}")
    
    # 按 T+20胜率 + 跌破成本深度 排序
    final.sort(key=lambda x: (-x.get('t20_winrate', 0), x.get('deviation_pct', 0)))
    
    for f in final[:20]:
        code = f['code']
        name = names.get(code, code)
        grade = f.get('t20_grade', 'C')
        l2_ok = f.get('_l2_pass', True)
        
        deviation = f.get('deviation_pct', 0)
        ent = f.get('entropy', {})
        low_safe = f.get('low_safe', 0)
        cur_price = f['close']
        cost_val = f.get('true_cost', '-')
        
        # L2未通过的降一档
        if not l2_ok:
            if grade == 'A': grade = 'B'
            elif grade == 'B': grade = 'C'
        
        if grade in ('A', 'B') and low_safe and cur_price <= low_safe * 1.05:
            action = "加仓 🟢"
            reason = f"贴近防线{low_safe}，洗盘尾声" + (" (L2弱)" if not l2_ok else "")
        elif grade in ('A', 'B') and deviation < -5:
            action = "关注 📌"
            reason = f"低于成本{abs(deviation):.0f}%，等待缩量" + (" (L2弱)" if not l2_ok else "")
        elif grade == 'C':
            action = "移除 ❌"
            reason = "回测胜率偏低" + (" (L2未通过降档)" if not l2_ok else "")
        else:
            action = "观望 ⏳"
            reason = "-"
        
        print(f"\n{code:<6} {name:<10}")
        print(f"  庄家状态: {'洗盘' if action in ('加仓','关注') else '待定'}")
        print(f"  成本区: {cost_val}  当前价:{cur_price:.2f}  跌破:{deviation:.1f}%")
        print(f"  防线: {low_safe if low_safe else '-'}")
        print(f"  操作: {action} — {reason}")
        print(f"  止损: {low_safe if low_safe else '-'}")
        print(f"  止盈: {round(cost_val, 2) if cost_val != '-' else '-'}")
    
    conn.close()
    return final


if __name__ == '__main__':
    target = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    
    # 如果指定了--codes则只分析指定股票
    if '--codes' in sys.argv:
        idx = sys.argv.index('--codes')
        codes = sys.argv[idx+1:idx+4]
        conn = psycopg2.connect(DB)
        for code in codes:
            print(f"\n{'='*60}")
            print(f"单只分析: {code}")
            print(f"{'='*60}")
            cur = conn.cursor()
            cur.execute("""
                SELECT close FROM daily_prices
                WHERE symbol='A.'||%s AND trade_date<=%s AND close>0
                ORDER BY trade_date DESC LIMIT 1
            """, (code, target))
            r = cur.fetchone()
            if not r:
                print(f"  无数据")
                continue
            cand = {'code': code, 'sym': f'A.{code}', 'close': float(r[0])}
            
            # 第二层
            l2 = layer2_active_filter(conn, cand, target)
            if not l2:
                print(f"  ❌ 第二层: 数据不足")
                continue
            print(f"  第二层: {'✅' if l2['active_pass'] else '❌'} 评分{l2['active_score']}")
            
            # 第三层
            l3 = layer3_entropy_analysis(conn, cand, target)
            if not l3:
                print(f"  ❌ 第三层: 熵法无法计算")
                continue
            ea = l3['entropy']
            print(f"  第三层:")
            print(f"    庄家成本: {l3['true_cost']}")
            print(f"    当前价: {l3['close']:.2f}")
            print(f"    跌破成本: {l3['deviation_pct']:.1f}%")
            print(f"    低吸安全区: {l3.get('low_safe', 'N/A')}")
            print(f"    当前熵z: {ea.get('entropy_stats', {}).get('current_z', 0):.2f}")
            print(f"    吸筹区: {ea.get('accumulation_zones', [])}")
            
            # 第四层
            l4 = layer4_backtest(conn, l3, target)
            if l4:
                print(f"  第四层:")
                print(f"    T+20胜率: {l4['t20_winrate']}%")
                print(f"    T+20均值: {l4['t20_avg']:+.2f}%")
                print(f"    T+20最大亏损: {l4['t20_max_loss']:+.2f}%")
                print(f"    评级: {l4['t20_grade']}")
                print(f"    样本量: {l4['backtest_count']}")
            
            # 支撑定性（从active_filter的cost_analysis获取完整）
            score = compute_active_score(conn, cand['sym'], target, lookback=60)
            if score and score.get('cost_analysis'):
                ca = score['cost_analysis']
                for sl in ca.get('support_levels', []):
                    if abs(sl['price'] - l3['close']) < 0.5:
                        trend = {'RISING':'↑','FALLING':'↓','FLAT':'→'}
                        print(f"    近端支撑: {sl['price']} {sl['support_type']} 底部{trend.get(sl['bottom_trend'],'→')}")
            print()
            
        conn.close()
        sys.exit(0)
    
    run_full_pipeline(target)
