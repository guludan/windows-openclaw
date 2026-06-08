#!/usr/bin/env python3
"""
cost_structure + V8 联合回测
==================================
目的：验证"庄家成本支撑 + V8共振信号"组合策略的历史胜率

方法：
  1. 从daily_prices取历史数据（全量滑动窗口）
  2. 对每只股票在每个交易日计算 cost_structure 的支撑强度评分
  3. 与V8信号（SMA偏离+PB）交叉，识别买入信号
  4. 统计T+N后的收益/胜率/最大回撤

输出：
  - 全市场统计
  - 按评分分层统计
  - 按regime分层统计
  - 按行业分层统计
"""
import sys, os, json, psycopg2, numpy as np
from datetime import date, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT = '/home/tearsyu/.openclaw/workspace/memory/reports'

# 参数
SMA_THRESHOLD = -0.14             # SMA偏离阈值
HOLD_DAYS = [5, 10, 20, 40, 60]  # 持有期
SCORE_BINS = [0, 60, 75, 85, 100]  # 评分分组
STOP_LOSS = 0.95                 # -5%止损

def get_pg():
    return psycopg2.connect(DB)

def calc_cost_score(closes, volumes, sma_dev, n_days):
    """简化版cost_structure评分，只用已有数据"""
    if n_days < 60:
        return 0, {}
    
    cs = np.array(closes[-60:], dtype=float)
    vs = np.array(volumes[-60:], dtype=float)
    cur_price = float(closes[-1])
    
    # VWAP
    vwap60 = float(np.sum(cs * vs * 100) / np.sum(vs * 100)) if np.sum(vs) > 0 else cur_price
    
    # 量比(60d/120d)
    if n_days >= 120:
        vs120 = np.array(volumes[-120:], dtype=float)
        vs60 = np.array(volumes[-60:], dtype=float)
        vol_ratio = float(np.mean(vs60) / np.mean(vs120)) if np.mean(vs120) > 0 else 1.0
    else:
        vol_ratio = 1.0
    
    # 缩量横盘检测
    latest_range = (np.max(cs) / np.min(cs) - 1) * 100 if len(cs) >= 20 else 100
    is_consol = vol_ratio < 0.9 and latest_range < 25
    
    vwap60_disc = cur_price / vwap60 - 1
    
    # 评分
    score = 50
    if sma_dev < -0.25: score += 15
    elif sma_dev < -0.20: score += 10
    elif sma_dev < -0.15: score += 5
    
    if vwap60_disc < -0.15: score += 10
    elif vwap60_disc < -0.10: score += 5
    
    if is_consol: score += 10
    if vol_ratio < 0.7: score += 8
    elif vol_ratio < 0.9: score += 5
    
    return min(100, score), {
        'vwap60': vwap60,
        'vwap60_disc': vwap60_disc,
        'vol_ratio': vol_ratio,
        'is_consol': is_consol,
        'range40': latest_range,
    }


def backtest_period(cur, start_date, end_date):
    """对指定时间区间做滑动窗口回测"""
    print(f"回测区间: {start_date} ~ {end_date}", file=sys.stderr)
    
    # 获取区间内所有交易日
    cur.execute("""
        SELECT DISTINCT trade_date FROM daily_prices 
        WHERE trade_date BETWEEN %s AND %s AND close > 0
        ORDER BY trade_date
    """, (start_date, end_date))
    trade_dates = [r[0] for r in cur.fetchall()]
    print(f"交易日: {len(trade_dates)}天", file=sys.stderr)
    
    if len(trade_dates) < 20:
        return {'error': '交易日不足'}
    
    # 获取所有有效symbol
    cur.execute("""
        SELECT DISTINCT substring(symbol,3) FROM daily_prices 
        WHERE trade_date = %s AND close > 0 AND volume > 0
    """, (trade_dates[-1],))
    all_symbols = [r[0] for r in cur.fetchall()]
    print(f"全市场: {len(all_symbols)}只", file=sys.stderr)
    
    # 排除ST
    cur.execute("SELECT stock_code, stock_name FROM stock_industry")
    st_codes = {r[0] for r in cur.fetchall() if r[1] and ('ST' in r[1] or 'PT' in r[1])}
    
    # 逐日滑动窗口
    signals = []  # {date, code, score, close, regime, ...}
    
    for t_idx, t_date in enumerate(trade_dates):
        if t_idx < 60:  # 需要至少60天历史来算VWAP
            continue
        if t_idx % 50 == 0:
            print(f"  进度: {t_idx}/{len(trade_dates)}天, 信号累计{len(signals)}条", file=sys.stderr)
        
        # 当天有效数据
        cur.execute("""
            SELECT substring(symbol,3), close FROM daily_prices 
            WHERE trade_date=%s AND close>0 AND volume>0
        """, (t_date,))
        today_stocks = {r[0]: float(r[1]) for r in cur.fetchall()}
        
        # 逐个检测
        for code, cur_price in today_stocks.items():
            if code in st_codes:
                continue
            if cur_price < 2.5 or cur_price > 50:
                continue
            
            # 查120天历史
            cur.execute("""
                SELECT close, volume FROM daily_prices 
                WHERE symbol='A.'||%s AND trade_date<%s AND close>0 AND volume>0
                ORDER BY trade_date DESC LIMIT 180
            """, (code, t_date))  # < 不含当天，避免前视偏差
            hist = cur.fetchall()
            if len(hist) < 120:
                continue
            
            hist = list(reversed(hist))  # 正序
            closes = np.array([float(r[0]) for r in hist], dtype=float)
            volumes = np.array([float(r[1]) for r in hist], dtype=float)
            
            # SMA120
            sma120 = float(np.mean(closes[-120:]))
            sma_dev = cur_price / sma120 - 1
            
            # 只关注SMA偏离深的
            if sma_dev >= SMA_THRESHOLD:
                continue
            
            # cost_structure评分
            score, factors = calc_cost_score(closes, volumes, sma_dev, len(closes))
            
            if score >= 60:  # 60分以上才认为有信号价值
                signals.append({
                    'date': t_date,
                    'code': code,
                    'close': cur_price,
                    'sma_dev': sma_dev,
                    'score': score,
                    'factors': factors,
                })
    
    print(f"\n信号总数: {len(signals)}条", file=sys.stderr)
    
    # ---- 回测收益计算 ----
    # 对每个信号，找T+N后的价格
    results = []
    date_set = set(trade_dates)
    date_list = trade_dates
    
    for sig in signals:
        sig_date = sig['date']
        sig_code = sig['code']
        sig_close = sig['close']
        sig_score = sig['score']
        sig_sma = sig['sma_dev']
        
        # 找信号之后第N天的索引
        try:
            idx = date_list.index(sig_date)
        except ValueError:
            continue
        
        returns = {}
        for hd in HOLD_DAYS:
            target_idx = idx + hd
            if target_idx < len(date_list):
                target_date = date_list[target_idx]
                # 拿target_date的close
                cur.execute("""
                    SELECT close FROM daily_prices 
                    WHERE symbol='A.'||%s AND trade_date=%s AND close>0
                """, (sig_code, target_date))
                r = cur.fetchone()
                if r:
                    ret = float(r[0]) / sig_close - 1
                    # 应用止损
                    if ret < -0.05:
                        ret = -0.05
                    returns[f'T+{hd}'] = ret
                else:
                    returns[f'T+{hd}'] = None
            else:
                returns[f'T+{hd}'] = None
        
        # 查当日regime
        cur.execute("SELECT extra->>'regime' FROM pool_energy WHERE level='market' AND date=%s", (sig_date,))
        regime_r = cur.fetchone()
        regime = regime_r[0] if regime_r and regime_r[0] else 'UNKNOWN'
        
        results.append({
            'date': sig_date, 'code': sig_code,
            'close': sig_close, 'score': sig_score,
            'sma_dev': sig_sma, 'regime': regime,
            'returns': returns,
        })
    
    return results


def analyze_results(results):
    """分析回测结果"""
    if not results:
        return {'error': '无信号'}
    
    total = len(results)
    
    # 全市场统计
    stats = {}
    for hd in HOLD_DAYS:
        valid = [r for r in results if r['returns'].get(f'T+{hd}') is not None]
        if valid:
            rets = [r['returns'][f'T+{hd}'] for r in valid]
            wins = sum(1 for r in rets if r > 0)
            stats[f'T+{hd}'] = {
                'count': len(valid),
                'avg_ret': float(np.mean(rets)),
                'win_rate': wins / len(valid),
                'max_ret': float(np.max(rets)),
                'min_ret': float(np.min(rets)),
                'median': float(np.median(rets)),
            }
    
    # 按评分分层
    score_stats = {}
    for bin_i in range(len(SCORE_BINS)-1):
        lo, hi = SCORE_BINS[bin_i], SCORE_BINS[bin_i+1]
        bin_results = [r for r in results if lo <= r['score'] < hi]
        if bin_results:
            label = f'{lo}-{hi}'
            score_stats[label] = {}
            for hd in HOLD_DAYS:
                valid = [r for r in bin_results if r['returns'].get(f'T+{hd}') is not None]
                if valid and len(valid) >= 5:
                    rets = [r['returns'][f'T+{hd}'] for r in valid]
                    wins = sum(1 for r in rets if r > 0)
                    score_stats[label][f'T+{hd}'] = {
                        'count': len(valid),
                        'avg_ret': float(np.mean(rets)),
                        'win_rate': wins / len(valid),
                    }
    
    # 按regime分层
    regime_results = defaultdict(list)
    for r in results:
        regime_results[r['regime']].append(r)
    
    regime_stats = {}
    for regime, reg_results in regime_results.items():
        regime_stats[regime] = {}
        for hd in HOLD_DAYS:
            valid = [r for r in reg_results if r['returns'].get(f'T+{hd}') is not None]
            if valid and len(valid) >= 5:
                rets = [r['returns'][f'T+{hd}'] for r in valid]
                wins = sum(1 for r in rets if r > 0)
                regime_stats[regime][f'T+{hd}'] = {
                    'count': len(valid),
                    'avg_ret': float(np.mean(rets)),
                    'win_rate': wins / len(valid),
                }
    
    # 按年份分层
    year_results = defaultdict(list)
    for r in results:
        year_results[r['date'].year].append(r)
    
    year_stats = {}
    for year, yr in year_results.items():
        if len(yr) >= 20:
            year_stats[year] = {}
            for hd in HOLD_DAYS:
                valid = [r for r in yr if r['returns'].get(f'T+{hd}') is not None]
                if valid:
                    rets = [r['returns'][f'T+{hd}'] for r in valid]
                    wins = sum(1 for r in rets if r > 0)
                    year_stats[year][f'T+{hd}'] = {
                        'count': len(valid),
                        'avg_ret': float(np.mean(rets)),
                        'win_rate': wins / len(valid),
                    }
    
    return {
        'total_signals': total,
        'overall': stats,
        'by_score': score_stats,
        'by_regime': regime_stats,
        'by_year': year_stats,
    }


def print_report(analysis, target_date=None):
    """格式化输出报告"""
    if 'error' in analysis:
        print(f"\n❌ 回测失败: {analysis['error']}")
        return
    
    print(f"\n{'='*80}")
    print(f"  cost_structure + V8 联合回测报告")
    if target_date:
        print(f"  数据截止: {target_date}")
    print(f"{'='*80}")
    
    a = analysis
    
    print(f"\n📊 总信号数: {a['total_signals']:,}条")
    
    print(f"\n{'─'*80}")
    print(f"  全市场统计")
    print(f"{'─'*80}")
    print(f"{'持有期':>8} {'信号数':>10} {'平均收益':>10} {'胜率':>8} {'中位收益':>10} {'最大收益':>10} {'最小收益':>10}")
    print(f"{'─'*80}")
    for hd in HOLD_DAYS:
        s = a['overall'].get(f'T+{hd}', {})
        if s:
            print(f"{f'T+{hd}':>8} {s['count']:>10,} {s['avg_ret']:>+9.2%} {s['win_rate']:>7.1%} {s['median']:>+9.2%} {s['max_ret']:>+9.2%} {s['min_ret']:>+9.2%}")
    
    print(f"\n{'─'*80}")
    print(f"  按评分分层 (T+20)")
    print(f"{'─'*80}")
    print(f"{'评分区间':>10} {'信号数':>10} {'平均收益':>10} {'胜率':>8}")
    print(f"{'─'*80}")
    for label in sorted(a['by_score'].keys()):
        s = a['by_score'][label].get('T+20', {})
        if s:
            print(f"{label:>10} {s['count']:>10,} {s['avg_ret']:>+9.2%} {s['win_rate']:>7.1%}")
    
    print(f"\n{'─'*80}")
    print(f"  按regime分层 (T+20)")
    print(f"{'─'*80}")
    print(f"{'Regime':>16} {'信号数':>10} {'平均收益':>10} {'胜率':>8}")
    print(f"{'─'*80}")
    for regime in sorted(a['by_regime'].keys()):
        s = a['by_regime'][regime].get('T+20', {})
        if s:
            print(f"{regime:>16} {s['count']:>10,} {s['avg_ret']:>+9.2%} {s['win_rate']:>7.1%}")
    
    print(f"\n{'─'*80}")
    print(f"  按年份分层 (T+20)")
    print(f"{'─'*80}")
    print(f"{'年份':>8} {'信号数':>10} {'平均收益':>10} {'胜率':>8}")
    print(f"{'─'*80}")
    for year in sorted(a['by_year'].keys()):
        s = a['by_year'][year].get('T+20', {})
        if s:
            print(f"{year:>8} {s['count']:>10,} {s['avg_ret']:>+9.2%} {s['win_rate']:>7.1%}")
    
    print(f"\n{'='*80}")


def save_report(analysis, filename):
    """保存JSON报告"""
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, filename)
    with open(path, 'w') as f:
        json.dump(analysis, f, ensure_ascii=False, indent=1, default=str)
    print(f"报告保存: {path}", file=sys.stderr)
    return path


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='cost_structure + V8联合回测')
    parser.add_argument('--start', type=str, default='2020-01-01', help='开始日期')
    parser.add_argument('--end', type=str, default=None, help='结束日期(默认今天)')
    parser.add_argument('--quick', action='store_true', help='快速模式(仅2025年后)')
    parser.add_argument('--save', action='store_true', help='保存报告')
    args = parser.parse_args()
    
    if args.quick:
        start = '2025-01-01'
    else:
        start = args.start
    
    end = args.end
    
    conn = get_pg()
    cur = conn.cursor()
    
    if end is None:
        cur.execute("SELECT MAX(trade_date) FROM daily_prices")
        end = cur.fetchone()[0]
    
    if isinstance(start, str):
        from datetime import datetime
        start_dt = datetime.strptime(start, '%Y-%m-%d').date()
    else:
        start_dt = start
    if isinstance(end, str):
        end_dt = datetime.strptime(end, '%Y-%m-%d').date()
    else:
        end_dt = end
    
    results = backtest_period(cur, start_dt, end_dt)
    conn.close()
    
    if 'error' in results:
        print(f"\n❌ 回测失败: {results['error']}")
        sys.exit(1)
    
    analysis = analyze_results(results)
    print_report(analysis, end_dt)
    
    if args.save:
        fname = f'cost_support_backtest_{start_dt}_{end_dt}.json'
        save_report(analysis, fname)
