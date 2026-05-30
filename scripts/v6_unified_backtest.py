"""
V6 统一回测：S1/S2/S3 三条线同步验证
2026-04 样本外 + 全量历史诊断

用法：
  conda activate tt
  python3 scripts/v6_unified_backtest.py [--mode full] [--start 2024-01-01] [--end 2026-04-22]
"""

import psycopg2
import json
import numpy as np
from datetime import date, timedelta
from collections import defaultdict
import sys, os, argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.v6_multi_factor import V6MultiFactor, _INDUSTRY_CACHE


PG_POOL = None
def get_conn():
    global PG_POOL
    if PG_POOL is None:
        PG_POOL = psycopg2.connect(host='192.168.3.14', dbname='guludan', user='guludan', password='toto')
    return PG_POOL


def fetch_signals_bulk(conn, level_like, sig_keys, start_date, end_date):
    """
    批量拉信号，避免每个信号一条 SQL
    sig_keys: 要检查的 sig 字段中的 key，如 ['s1', 's2', 's3']
    """
    cur = conn.cursor()
    sig_conditions = []
    for sk in sig_keys:
        sig_conditions.append(f"e.extra->>'sigs' LIKE '%{sk}%'")
    where_sig = ' OR '.join(sig_conditions)
    
    cur.execute(f"""
        SELECT e.level, e.date, e.extra
        FROM pool_energy e
        WHERE e.level LIKE 'code:A.%%'
          AND ({where_sig})
          AND e.date BETWEEN '{start_date}'::date AND '{end_date}'::date
        ORDER BY e.date, e.level
    """)
    
    rows = cur.fetchall()
    signals = []
    for r in rows:
        extra = json.loads(r[2]) if isinstance(r[2], str) else r[2]
        signals.append({'level': r[0], 'date': r[1], 'extra': extra})
    return signals


def backtest_signal(mf, signals, ind_map, classify_fn, label):
    """
    统一回测引擎
    classify_fn(code, date, extra, ind_map) -> {'type': str, 'score': float, ...}
    """
    if not signals:
        return None
    
    # 预计算行业超额
    mf.precalc_all_industry_excess(signals, ind_map)
    
    results = []
    for sig in signals:
        code = sig['level'][7:]
        dt = sig['date']
        extra = sig['extra']
        
        # T+20 收益
        sym = 'A.' + code
        mf.cur.execute("""
            SELECT close FROM daily_prices WHERE symbol = %s AND trade_date = %s
        """, (sym, dt))
        c0 = mf.cur.fetchone()
        if not c0:
            continue
        
        mf.cur.execute("""
            SELECT close FROM daily_prices
            WHERE symbol = %s AND trade_date > %s
            ORDER BY trade_date OFFSET 19 LIMIT 1
        """, (sym, dt))
        t20 = mf.cur.fetchone()
        if not t20:
            continue
        t20_ret = t20[0] / c0[0] - 1
        
        # 分类
        cls = classify_fn(code, dt, extra, ind_map)
        
        results.append({
            'code': code, 'date': dt, 'ret_20': t20_ret,
            'type': cls['type'], 'score': cls['score'],
            'reason': cls.get('reason', ''),
        })
    
    return results


def print_backtest_report(results, title):
    """打印回测报告"""
    print(f"\n{'='*65}")
    print(f"  {title}")
    print(f"{'='*65}")
    print(f"  总信号: {len(results)} 条")
    
    # 按 type 分组
    types = sorted(set(r['type'] for r in results))
    for typ in types:
        grp = [r for r in results if r['type'] == typ]
        rets = np.array([r['ret_20'] for r in grp])
        if len(grp) < 3:
            continue
        win_rate = np.mean(rets > 0)
        print(f"    {typ}级 (n={len(grp):>5d}): T+20={np.mean(rets):+7.2%}  胜率={win_rate:.0%}")
        # 按 score 分层
        scores = sorted(set(r['score'] for r in grp))
        for sc in scores:
            sg = [r for r in grp if r['score'] == sc]
            s_rets = np.array([r['ret_20'] for r in sg])
            print(f"       score={sc:.1f} (n={len(sg):>3d}): {np.mean(s_rets):+7.2%}  胜率={np.mean(s_rets>0):.0%}")
    
    # score 加权 vs 基线
    base_ret = np.mean([r['ret_20'] for r in results])
    weighted = np.mean([r['ret_20'] * max(r['score'], 0.1) for r in results])
    print(f"    基线均值: {base_ret:+7.2%}")
    print(f"    加权均值: {weighted:+7.2%}")
    
    # score > 0.5 的买入组
    buys = [r for r in results if r['score'] >= 0.6]
    passes = [r for r in results if r['score'] < 0.6]
    if buys:
        b_rets = np.array([r['ret_20'] for r in buys])
        print(f"    买入(score>=0.6, n={len(buys):>3d}): {np.mean(b_rets):+7.2%}  胜率={np.mean(b_rets>0):.0%}")
    if passes:
        p_rets = np.array([r['ret_20'] for r in passes])
        print(f"    排除(score<0.6, n={len(passes):>3d}): {np.mean(p_rets):+7.2%}  胜率={np.mean(p_rets>0):.0%}")


def run_s1_s2_full_backtest(start, end):
    """全量 S1/S2 回测"""
    conn = get_conn()
    mf = V6MultiFactor(conn)
    
    print(f"\n加载信号: {start} ~ {end}")
    
    # S1
    s1_signals = fetch_signals_bulk(conn, 'code:A.', ['s1'], start, end)
    print(f"  S1: {len(s1_signals)} 条")
    
    # S2
    s2_signals = fetch_signals_bulk(conn, 'code:A.', ['s2'], start, end)
    print(f"  S2: {len(s2_signals)} 条")
    
    # 行业映射
    ind_map = mf.fetch_industry_map()
    
    # 预加载大盘相空间（S2 需要）
    all_dates = sorted(set(
        [s['date'] for s in s1_signals] + 
        [s['date'] for s in s2_signals]
    ))
    mf.preload_market_phases(all_dates)
    
    # 分信号跑
    _INDUSTRY_CACHE.clear()
    s1_results = backtest_signal(mf, s1_signals, ind_map, mf.classify_s1, "S1")
    print_backtest_report(s1_results, "S1 分类回测（波动暴增 → 信息泄露/噪声拆分）")
    
    _INDUSTRY_CACHE.clear()
    s2_results = backtest_signal(mf, s2_signals, ind_map, mf.classify_s2, "S2")
    print_backtest_report(s2_results, "S2 分类回测（放量大涨 → 相空间方向判定）")
    
    # 层叠结果：S1+A/B级 的 S2 交叉
    sys.stdout.flush()
    print(f"\n{'='*65}")
    print("  S1 × S2 交叉分析")
    print(f"{'='*65}")
    
    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default='sample', choices=['sample', 'full'])
    parser.add_argument('--start', default='2026-04-01')
    parser.add_argument('--end', default='2026-04-22')
    args = parser.parse_args()
    
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    
    if args.mode == 'sample':
        print("样本外验证模式 (S1/S2 快速验证)")
        print(f"  日期范围: {start} ~ {end}")
        print("\n  首次运行需要加载行业缓存，约 2 秒")
        print("  之后 S1/S2/S3 共享缓存，零额外开销")
    
    run_s1_s2_full_backtest(start, end)


if __name__ == '__main__':
    main()
