#!/usr/bin/env python3
"""V8 高波动周期型候选分析

从V8+Layer0候选池中筛选高波动周期股：
1. 波动率（日收益率年化标准差）> 50%
2. 净利>0
3. 净利率>0（可配置阈值）
4. 诚信排雷（跳过处罚记录）
5. 扣非排雷（扣非为负降级/剔除）

输出分层：Tier1 / Tier2 / 观察 / 排除

用法: /usr/bin/python3 v8_cyclical.py [--threshold 0.05]
  --threshold: 最低波动率阈值（默认0.5=50%年化波动）
"""
import psycopg2, math, sys
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
VERBOSE = False

def get_v8_candidates():
    """获取V8 Layer 0通过候选（代码+行业+价格+SMA+PB+密度）"""
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    # 用V8+净利>0
    cur.execute("""
        SELECT v.code, v.stock_name, v.industry, v.density,
               round(v.sma_dev::numeric*100,2) as sma_pct,
               round(v.pb::numeric,3) as pb,
               round(v.close::numeric,2) as price,
               round(v.sma120::numeric,2) as sma120
        FROM mv_v8_candidates v
        JOIN financial_data f ON f.symbol = 'A.' || v.code AND f.end_date = '2026-03-31'
        WHERE v.sma_dev < -0.2 AND v.pb < 1.0
          AND f.net_profit > 0
        ORDER BY v.sma_dev
    """)
    signals = []
    for r in cur.fetchall():
        signals.append({
            'code': r[0], 'name': r[1] or '', 'industry': r[2] or '',
            'density': r[3] or '', 'sma_pct': float(r[4]),
            'pb': float(r[5]), 'price': float(r[6]), 'sma120': float(r[7]),
        })
    conn.close()
    return signals


def get_price_data(codes):
    """获取候选股票日线数据"""
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    placeholders = ','.join(['%s'] * len(codes))
    cur.execute(f"""
        SELECT substring(symbol, 3) as code, trade_date, close
        FROM daily_prices
        WHERE substring(symbol, 3) IN ({placeholders})
          AND trade_date >= '2023-01-01'
        ORDER BY code, trade_date
    """, codes)
    stock_prices = defaultdict(list)
    for r in cur.fetchall():
        stock_prices[r[0]].append((str(r[1])[:10], float(r[2])))
    conn.close()
    return stock_prices


def get_financial(codes):
    """获取财务数据和扣非净利润"""
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    placeholders = ','.join(['%s'] * len(codes))
    
    # 净利+营收+ROE
    cur.execute(f"""
        SELECT DISTINCT ON (symbol) symbol, net_profit, revenue, roe, eps, bvps
        FROM financial_data
        WHERE symbol IN ({placeholders}) AND end_date = '2026-03-31'
        ORDER BY symbol, end_date DESC
    """, codes)
    fin = {}
    for r in cur.fetchall():
        np_val = float(r[1]) if r[1] else 0
        rev_val = float(r[2]) if r[2] else 0
        fin[r[0]] = {
            'np': np_val,
            'rev': rev_val,
            'margin': np_val/rev_val*100 if rev_val and rev_val > 0 else None,
            'roe': float(r[3]) if r[3] else None,
            'eps': float(r[4]) if r[4] else None,
            'bvps': float(r[5]) if r[5] else None,
        }
    conn.close()
    return fin


def calc_volatility(prices, max_days=500):
    """计算年化波动率、振幅、脉冲系数"""
    if len(prices) < 200:
        return None
    
    prices.sort(key=lambda x: x[0])
    closes = [p[1] for p in prices[-max_days:]]
    
    # 日收益率
    rets = [(closes[i+1]-closes[i])/closes[i] for i in range(len(closes)-1)]
    if not rets:
        return None
    
    avg_ret = sum(rets)/len(rets)
    daily_vol = math.sqrt(sum((r-avg_ret)**2 for r in rets) / len(rets))
    annual_vol = daily_vol * math.sqrt(252)
    
    # 最大单日涨跌幅
    max_up = max(rets) * 100
    max_down = min(rets) * 100
    
    # 120日振幅
    lookback = min(120, len(closes))
    high = max(closes[-lookback:])
    low = min(closes[-lookback:])
    amplitude = (high - low) / low * 100
    
    # 月度波动脉冲系数
    monthly_vols = defaultdict(list)
    for i in range(len(rets)):
        # 只有recent_days对应的prices部分
        if len(prices) > max_days:
            start_idx = len(prices) - max_days
        else:
            start_idx = 0
        ym = prices[start_idx + i][0][:7]
        monthly_vols[ym].append(rets[i])
    
    monthly_vol_values = []
    for ym, rlist in monthly_vols.items():
        if len(rlist) >= 5:
            a = sum(rlist)/len(rlist)
            v = math.sqrt(sum((r-a)**2 for r in rlist)/len(rlist))
            monthly_vol_values.append(v)
    
    if monthly_vol_values:
        avg_mv = sum(monthly_vol_values)/len(monthly_vol_values)
        pulse_ratio = max(monthly_vol_values) / avg_mv if avg_mv > 0 else 1
    else:
        pulse_ratio = 1
    
    # 最近20日涨幅
    recent_ret = (closes[-1] - closes[-min(20, len(closes))]) / closes[-min(20, len(closes))] * 100
    
    return {
        'annual_vol': annual_vol,
        'daily_vol_pct': daily_vol * 100,
        'max_up': max_up, 'max_down': max_down,
        'amplitude': amplitude,
        'pulse_ratio': pulse_ratio,
        'recent_20d': recent_ret,
    }


def check_penalty(code):
    """诚信排雷：检查是否有信披处罚记录（占位，待爬虫数据源）"""
    # 手动记录已知
    PENALTY_BLACKLIST = {
        '002716': '湖南白银',   # 2024年信披违法被证监会处罚
        '002969': '嘉美包装',   # 庄股嫌疑，1100%振幅无基本面支撑
    }
    return code in PENALTY_BLACKLIST


def get_reject_reason(code):
    """获取排除原因"""
    reasons = {
        '002716': '历史信披违法被证监会处罚',
        '002969': '庄股嫌疑（1100%振幅无基本面支撑）',
    }
    return reasons.get(code, '')


def main():
    vol_threshold = 0.5  # 默认50%年化波动
    if '--threshold' in sys.argv:
        idx = sys.argv.index('--threshold')
        vol_threshold = float(sys.argv[idx+1])
    
    signals = get_v8_candidates()
    codes = [s['code'] for s in signals]
    print(f"V8+Layer0候选: {len(codes)}只")
    
    # 拉数据
    stock_prices = get_price_data(codes)
    fin_data = get_financial(codes)
    code_map = {s['code']: s for s in signals}
    
    # 分析每只
    all_results = []
    
    for code in codes:
        prices = stock_prices.get(code)
        if not prices:
            continue
        
        vol = calc_volatility(prices)
        if not vol or vol['annual_vol'] < vol_threshold:
            continue
        
        fin = fin_data.get(code, {})
        margin = fin.get('margin')
        np_val = fin.get('np', 0)
        rev_val = fin.get('rev', 0)
        
        # 评分
        vol_score = vol['annual_vol'] * (vol['amplitude']/100) * vol['pulse_ratio']
        
        all_results.append({
            **code_map[code],
            **vol,
            'margin': margin,
            'np': np_val,
            'rev': rev_val,
            'vol_score': vol_score,
            'roe': fin.get('roe'),
            'bvps': fin.get('bvps'),
        })
    
    all_results.sort(key=lambda x: -x['vol_score'])
    
    # 分层
    tier1, tier2, excluded, observed = [], [], [], []
    
    for r in all_results:
        code = r['code']
        
        # 排雷过滤
        if check_penalty(code):
            r['reject_reason'] = get_reject_reason(code) or '列入排除名单'
            excluded.append(r)
            continue
        
        # 净利率判定
        margin = r['margin']
        if margin is not None and margin > 5:
            tier1.append(r)
        elif margin is not None and margin > 0:
            tier2.append(r)
        else:
            observed.append(r)
    
    # 输出报告
    print(f"\n{'='*80}")
    print(f"  V8 高波动周期型候选报告")
    print(f"  (年化波动率≥{vol_threshold*100:.0f}% + 净利>0)")
    print(f"{'='*80}")
    
    print(f"\n📊 波动筛选结果")
    print(f"   总候选: {len(all_results)}只")
    print(f"   Tier 1 (高波动+高净利率>5%): {len(tier1)}只")
    print(f"   Tier 2 (高波动+微利<5%): {len(tier2)}只")
    print(f"   观察 (无净利率数据): {len(observed)}只")
    print(f"   排除 (诚信污点): {len(excluded)}只")
    
    if tier1:
        print(f"\n🥇 Tier 1 — 高波动 + 净利率>5%")
        print(f"  {'代码':>8} {'名称':<12} {'行业':<14} {'密度':<6} {'SMA%':>7} {'PB':>5} {'波动':>6} {'振幅':>6} {'净利率':>7} {'净利(万)':>8} {'近期20d':>8}")
        print(f"  {'-'*90}")
        for r in tier1[:15]:
            print(f"  {r['code']:>8} {r['name'][:12]:<12} {r['industry'][:12]:<14} {r['density']:<6} {r['sma_pct']:>+6.2f}% {r['pb']:>4.2f} "
                  f"{r['annual_vol']*100:>5.1f}% {r['amplitude']:>5.0f}% {r['margin']:>6.1f}% {r['np']/1e4:>8.0f} {r['recent_20d']:>+7.2f}%")
    
    if tier2:
        print(f"\n🥈 Tier 2 — 高波动 + 微利(净利率<5%)")
        print(f"  {'代码':>8} {'名称':<12} {'行业':<14} {'密度':<6} {'SMA%':>7} {'PB':>5} {'波动':>6} {'振幅':>6} {'净利率':>7} {'净利(万)':>8} {'近期20d':>8}")
        print(f"  {'-'*90}")
        for r in tier2[:10]:
            print(f"  {r['code']:>8} {r['name'][:12]:<12} {r['industry'][:12]:<14} {r['density']:<6} {r['sma_pct']:>+6.2f}% {r['pb']:>4.2f} "
                  f"{r['annual_vol']*100:>5.1f}% {r['amplitude']:>5.0f}% {r['margin']:>6.1f}% {r['np']/1e4:>8.0f} {r['recent_20d']:>+7.2f}%")
    
    if excluded:
        print(f"\n🚫 排除（诚信污点）")
        for r in excluded:
            print(f"  {r['code']:>8} {r['name'][:12]:<12} → {r['reject_reason']}")
    
    if observed:
        print(f"\n👀 观察（等待数据补充）")
        for r in observed[:5]:
            print(f"  {r['code']:>8} {r['name'][:12]:<12} 波动{r['annual_vol']*100:.1f}%")


if __name__ == '__main__':
    main()
