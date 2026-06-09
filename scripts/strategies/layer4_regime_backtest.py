#!/usr/bin/env python3
"""
分层L4回测 — 按Regime拆分T+20胜率

用法:
  python3 layer4_regime_backtest.py [--codes 600635,600273,002073]
  python3 layer4_regime_backtest.py --scan
"""
import sys
sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, json
import numpy as np
from datetime import date, timedelta
from collections import defaultdict
from strategies.active_filter import DB

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

def load_regime_map(conn):
    """从pool_energy加载regime_map {date: regime}"""
    cur = conn.cursor()
    cur.execute("""
        SELECT date, extra->>'regime' FROM pool_energy
        WHERE level='market' AND extra ? 'regime'
        ORDER BY date
    """)
    return {r[0]: r[1] for r in cur.fetchall()}


def _dedup_signals(dates, closes, regime_map, min_gap=5):
    """
    信号去重：同一轮下跌中连续触发(间隔<min_gap天)只保留第一次。
    返回去重后的(date, close, regime, ret20)列表
    """
    # 先找所有原始信号
    raw = []
    for i in range(120, len(closes)):
        c = closes[i]
        sma120 = np.mean(closes[i-120:i])
        if c < sma120 * 0.85:
            end = min(i + 20, len(closes) - 1)
            if end - i >= 20:
                ret20 = (closes[end] / c - 1) * 100
                raw.append({
                    'date': dates[i],
                    'close': float(c),
                    'sma120': float(sma120),
                    'dev': (c/sma120 - 1) * 100,
                    'ret20': float(ret20),
                    'idx': i,
                })
    if not raw:
        return []
    
    # 去重：间隔>=min_gap交易日的才视为独立信号
    # 用日期索引的差值判断间距
    deduped = [raw[0]]
    for s in raw[1:]:
        idx_gap = s['idx'] - deduped[-1]['idx']
        if idx_gap >= min_gap:
            deduped.append(s)
    
    return deduped


def calc_regime_winrate(conn, sym, target_date, regime_map, dedup=True):
    """
    对个股逐日滚动回测，按regime分组统计胜率
    参数:
        dedup: True时启用信号去重(同一轮下跌连续触发只保留第一次)
    返回: regime维度胜率、通用胜率
    """
    cur = conn.cursor()
    
    start = target_date - timedelta(days=365*5)  # 5年数据
    
    cur.execute("""
        SELECT trade_date, close FROM daily_prices
        WHERE symbol=%s AND trade_date BETWEEN %s AND %s
          AND close>0 AND volume>0
        ORDER BY trade_date
    """, (sym, start, target_date - timedelta(days=20)))
    rows = cur.fetchall()
    if len(rows) < 140:
        return None, None, None
    
    dates = [r[0] for r in rows]
    closes = np.array([float(r[1]) for r in rows])
    
    # 获取信号（可选去重）
    if dedup:
        deduped = _dedup_signals(dates, closes, regime_map, min_gap=5)
    else:
        # 不过滤，保留全部原始信号
        raw_all = []
        for i in range(120, len(closes)):
            c = closes[i]
            sma120 = np.mean(closes[i-120:i])
            if c < sma120 * 0.85:
                end = min(i + 20, len(closes) - 1)
                if end - i >= 20:
                    ret20 = (closes[end] / c - 1) * 100
                    raw_all.append({
                        'date': dates[i],
                        'close': float(c),
                        'sma120': float(sma120),
                        'dev': (c/sma120 - 1) * 100,
                        'ret20': float(ret20),
                    })
        deduped = raw_all
    
    # 按regime分组
    trades_by_regime = defaultdict(list)
    trades_all = []
    
    for s in deduped:
        ret20 = s['ret20']
        regime = regime_map.get(s['date'], 'UNKNOWN')
        trades_by_regime[regime].append(ret20)
        trades_all.append(ret20)
    
    result = {}
    
    # 通用胜率
    if trades_all:
        ta = np.array(trades_all)
        result['ALL'] = {
            'n': len(trades_all),
            'winrate': round(np.mean(ta > 0) * 100, 1),
            'mean': round(np.mean(ta), 2),
            'median': round(np.median(ta), 2),
        }
    
    # 按regime分组
    for regime, trade_list in sorted(trades_by_regime.items()):
        if len(trade_list) < 3:  # 去重后样本更少，阈值降到3
            continue
        tr = np.array(trade_list)
        result[regime] = {
            'n': len(trade_list),
            'winrate': round(np.mean(tr > 0) * 100, 1),
            'mean': round(np.mean(tr), 2),
            'median': round(np.median(tr), 2),
        }
    
    return result, len(trades_all), trades_all


def layer0_check(conn, sym):
    """简易Layer 0财务核查"""
    cur = conn.cursor()
    codes = {'A.000912': '泸天化', 'A.300284': '苏交科', 'A.000850': '华茂股份',
             'A.600810': '神马股份', 'A.002073': '软控股份', 'A.600635': '大众公用',
             'A.600273': '嘉化能源', 'A.600797': '中国武夷', 'A.601366': '利群股份'}
    
    code = sym[2:]
    name = codes.get(sym, code)
    
    # 取2026Q1和最近年报数据
    cur.execute("""
        SELECT end_date, net_profit, revenue
        FROM financial_data
        WHERE symbol=%s AND net_profit IS NOT NULL
        ORDER BY end_date DESC LIMIT 4
    """, (sym,))
    rows = cur.fetchall()
    
    layer0 = '数据不足'
    details = []
    
    if rows:
        profits = [(str(r[0])[:10], r[1]/1e8, r[2]/1e8 if r[2] else None) for r in rows]
        for pd, np_, rev in profits[:2]:
            details.append(f'{pd}: 净利{np_:+.2f}亿' + (f' 营收{rev:.1f}亿' if rev else ''))
        
        # 判断
        if rows[0][1] and rows[0][1] > 0:
            layer0 = '✅ 最新盈利'
        elif rows[0][1] and rows[0][1] < 0:
            layer0 = '⚠️ 最新亏损'
        if len(rows) >= 4 and all(r[1] > 0 for r in rows[:4]):
            layer0 = '✅ 连续4期盈利'
        elif len(rows) >= 2 and rows[0][1] > 0 and rows[1][1] > 0:
            layer0 = '✅ 近2期均盈利'
    
    return name, layer0, details


def main():
    target = date(2026, 6, 5)
    
    # 指定股票
    codes = ['000912', '300284', '000850', '600810', '002073', '600635', '600273']
    
    conn = psycopg2.connect(DB)
    regime_map = load_regime_map(conn)
    
    print(f"{'='*95}")
    print(f"分层L4回测 (SMA<-15%通用条件，含Regime维度)")
    print(f"{'='*95}")
    
    header = f"{'代码':<8} {'名称':<10} {'条件':<10} {'n':>5} {'胜率':>7} {'均值':>7} {'中位':>7} {'Layer 0':>20}"
    print(header)
    print('-' * 95)
    
    for code in codes:
        sym = f'A.{code}'
        name, layer0, details = layer0_check(conn, sym)
        
        result, n_total, trades_all = calc_regime_winrate(conn, sym, target, regime_map, dedup=True)
        if result is None or 'ALL' not in result:
            print(f"{code:<8} {name:<10} {'数据不足':<10} {'-':>5} {'-':>7} {'-':>7} {'-':>7} {'-':>20}")
            continue
        
        all_r = result['ALL']
        
        # 通用胜率行
        print(f"{code:<8} {name:<10} {'通用(去重)':<10} {all_r['n']:>5d} {all_r['winrate']:>6.1f}% {all_r['mean']:>+6.2f}% {all_r['median']:>+6.2f}% {layer0:>20}")
        
        # Layer 0详情
        for d in details:
            print(f"{'':<18} 📋 {d}")
        
        # Regime分层行
        for regime in ['BEAR', 'SIDEWAYS', 'BULL', 'BOTTOM_WARNING', 'TOP_WARNING']:
            if regime in result:
                r = result[regime]
                label = f'{regime}极空' if regime == 'BEAR' else regime
                print(f"{'':<18} {label:<10} {r['n']:>5d} {r['winrate']:>6.1f}% {r['mean']:>+6.2f}% {r['median']:>+6.2f}%")
        
        print()
    
    # 保存报告
    print(f"\n{'='*95}")
    print(f"最终评级表 (按BEAR极空区胜率 + 通用胜率 综合排序)")
    print(f"{'='*95}")
    
    rows_out = []
    for code in codes:
        sym = f'A.{code}'
        result, n_total, _ = calc_regime_winrate(conn, sym, target, regime_map, dedup=True)
        if result is None:
            continue
        name, layer0, _ = layer0_check(conn, sym)
        
        all_r = result.get('ALL', {})
        bear_r = result.get('BEAR', {})
        
        bear_win = bear_r.get('winrate', 0)
        all_win = all_r.get('winrate', 0)
        all_n = all_r.get('n', 0)
        
        # 评级（去重后）
        effective_win = max(all_win, bear_win)
        
        if effective_win >= 70 and layer0.startswith('✅'):
            grade = 'A' if all_n >= 15 else 'B (样本偏少)'
        elif effective_win >= 55 and layer0.startswith('✅'):
            grade = 'B'
        elif layer0.startswith('⚠️'):
            grade = 'C (财务预警)'
        else:
            grade = 'C'
        
        grade_emoji = '🟢' if grade.startswith('A') else '🟡' if grade.startswith('B') else '🔴'
        
        rows_out.append((bear_win, all_win, code, name, all_n, all_r.get('winrate',0), 
                        bear_r.get('winrate',0), bear_r.get('n',0),
                        bear_r.get('mean',0), all_r.get('mean',0), layer0, grade, grade_emoji))
    
    # 按BEAR区胜率排序
    rows_out.sort(key=lambda x: (-x[0], -x[1]))
    
    print(f"{'代码':<8} {'名称':<10} {'n':>5} {'通用胜率':>8} {'通用均值':>8} {'BEAR胜率':>8} {'BEAR_n':>6} {'BEAR均值':>8} {'评级':>18}")
    print('-' * 95)
    for r in rows_out:
        print(f"{r[2]:<8} {r[3]:<10} {r[4]:>5d} {r[5]:>7.1f}% {r[9]:>+7.2f}% {r[6]:>7.1f}% {r[7]:>5d} {r[8]:>+7.2f}% {r[12]:>2s} {r[11]:<15}")
        print(f"{'':<18} Layer 0: {r[10]:>20}")
    
    conn.close()


if __name__ == '__main__':
    main()
