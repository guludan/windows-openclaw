#!/usr/bin/env python3
"""
补跑庄家行为数据 + 最终评级表

庄家行为检测逻辑（沿用 stock_rolling_backtest.py）:
  - 出货期: 近5日均量/近20日均量 > 1.3 + 上影线≥3次/10天
  - 建仓期: 阳线占比>55% + 量比1.0~1.8且无上影线特征
  - 无主力特征: 以上都不满足

输出到 memory/cost_deviation/final_ratings_{date}.json
"""
import psycopg2, numpy as np, json, os, sys
from datetime import date
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT = '/home/tearsyu/.openclaw/workspace/memory/cost_deviation'


def get_stage_data(code, conn):
    """拉最近60天庄家行为 + 净利润趋势 + 筹码状态"""
    cur = conn.cursor()
    cur.execute(f"""
        SELECT trade_date, close, open, high, low, volume
        FROM daily_prices WHERE symbol='A.{code}' AND close > 0
        ORDER BY trade_date DESC LIMIT 60
    """)
    rows = cur.fetchall()
    if not rows or len(rows) < 30:
        return None
    rows.reverse()
    
    closes = np.array([float(r[1]) for r in rows], dtype=float)
    opens = np.array([float(r[2]) for r in rows], dtype=float)
    highs = np.array([float(r[3]) for r in rows], dtype=float)
    lows = np.array([float(r[4]) for r in rows], dtype=float)
    vols = np.array([float(r[5]) if r[5] else 0 for r in rows], dtype=float)
    n = len(rows)
    
    # 庄家行为（最新）
    p5v = np.mean(vols[-5:]) if n >= 5 else 0
    p20v = np.mean(vols[-20:]) if n >= 20 else 1
    vi = p5v / max(p20v, 1)
    
    sh_cnt = sum(
        1 for j in range(max(0, n-10), n)
        if (highs[j]-lows[j]) > 0 and (highs[j]-max(opens[j],closes[j]))/(highs[j]-lows[j]) > 0.4
    )
    
    yang = closes[-20:] >= opens[-20:] if n >= 20 else closes >= opens
    y20 = np.mean(yang)
    
    if vi > 1.3 and sh_cnt >= 3:
        stage = "出货期"
    elif y20 > 0.55 and 1.0 <= vi <= 1.8:
        stage = "建仓期"
    else:
        stage = "无主力特征"
    
    # 筹码状态（近10/60日换手比）
    if n >= 60:
        near_vol = np.mean(vols[-10:])
        hist_vol = np.mean(vols[-60:-10])
        lock_ratio = near_vol / max(hist_vol, 1)
        if lock_ratio < 0.6:
            hold_state = "锁仓"
        elif lock_ratio < 0.85:
            hold_state = "正常"
        elif lock_ratio < 1.2:
            hold_state = "轻度活跃"
        else:
            hold_state = "出逃"
    else:
        hold_state = "未知"
    
    cur.execute(f"""
        SELECT net_profit, net_profit_yoy, end_date FROM financial_data
        WHERE symbol='A.{code}' AND net_profit IS NOT NULL
        ORDER BY end_date DESC LIMIT 1
    """)
    fin_r = cur.fetchone()
    profit_trend = "未知"
    if fin_r:
        np_val, yoy, ed = fin_r
        if yoy and yoy < -30:
            profit_trend = f"恶化(-{abs(yoy):.0f}%)" if yoy < 0 else f"改善(+{yoy:.0f}%)"
        elif yoy and yoy > 0:
            profit_trend = f"改善(+{yoy:.0f}%)"
        else:
            profit_trend = f"持平({float(np_val)/1e8:.1f}亿)"
    
    # SMA偏离
    cur.execute(f"""
        SELECT trade_date, close FROM daily_prices WHERE symbol='A.{code}' AND close > 0
        ORDER BY trade_date DESC LIMIT 120
    """)
    hist_r = cur.fetchall()
    hist_c = np.array([float(r[1]) for r in hist_r], dtype=float)
    sma120 = np.mean(hist_c)  # 含当天
    sdev = float(closes[-1]) / max(sma120, 0.01) - 1
    
    return {
        'stage': stage,
        'hold_state': hold_state,
        'lock_ratio': round(float(lock_ratio), 2) if n >= 60 else None,
        'profit_trend': profit_trend,
        'sdev': round(float(sdev*100), 1),
        'close': round(float(closes[-1]), 2),
        'date': str(rows[-1][0]),
    }


def get_bt_summary(code, conn):
    """从逐日滚动回测结果拉汇总统计"""
    cur = conn.cursor()
    cur.execute(f"""
        SELECT trade_date, close, open, high, low, volume
        FROM daily_prices WHERE symbol='A.{code}' AND close > 0
        ORDER BY trade_date
    """)
    rows = cur.fetchall()
    if not rows or len(rows) < 250:
        return None
    
    dates_a = [r[0] for r in rows]
    closes_a = np.array([float(r[1]) for r in rows], dtype=float)
    opens_a = np.array([float(r[2]) for r in rows], dtype=float)
    highs_a = np.array([float(r[3]) for r in rows], dtype=float)
    lows_a = np.array([float(r[4]) for r in rows], dtype=float)
    vols_a = np.array([float(r[5]) if r[5] else 0 for r in rows], dtype=float)
    n = len(closes_a)
    
    # Regime
    cur2 = conn.cursor()
    cur2.execute("SELECT date, extra FROM pool_energy WHERE level='market' AND extra IS NOT NULL ORDER BY date")
    regime_map = {}
    for td, e in cur2.fetchall():
        if isinstance(e, dict):
            regime_map[td.isoformat()] = {'regime': e.get('regime', 'UNK'), 'sz': e.get('surplus_z', 0)}
    def get_reg(td_str):
        best = None
        for k, v in sorted(regime_map.items()):
            if k <= td_str: best = v
            else: break
        return best or {'regime': 'UNK', 'sz': 0}
    
    all_sigs = []
    for i in range(200, n - 20):
        cur_close = closes_a[i]
        sma120 = np.mean(closes_a[i-119:i+1])
        sdev = cur_close / max(sma120, 0.01) - 1
        if sdev >= -0.10:
            continue
        
        t20 = closes_a[i+20]/cur_close - 1 if i+20 < n else None
        reg = get_reg(dates_a[i].isoformat())
        
        all_sigs.append({
            'sdev': float(sdev),
            'regime': reg['regime'],
            'sz': reg['sz'],
            't20': float(t20) if t20 is not None else None,
        })
    
    if len(all_sigs) < 10:
        return None
    
    t20_all = np.array([s['t20'] for s in all_sigs if s['t20'] is not None])
    bear_deep = [s for s in all_sigs if s['regime'] == 'BEAR' and s['sdev'] < -0.15 and s['t20'] is not None]
    bear_extreme = [s for s in all_sigs if s['regime'] == 'BEAR' and s['sz'] < -1.5 and s['sdev'] < -0.15 and s['t20'] is not None]
    
    result = {
        'overall_wr': round(float(np.mean(t20_all>0)*100), 1),
        'overall_avg': round(float(np.mean(t20_all)*100), 2),
        'overall_n': len(t20_all),
        'bear_deep_wr': round(float(np.mean([s['t20']>0 for s in bear_deep])*100), 1) if bear_deep else None,
        'bear_deep_avg': round(float(np.mean([s['t20'] for s in bear_deep])*100), 2) if bear_deep else None,
        'bear_deep_n': len(bear_deep),
        'bear_extreme_wr': round(float(np.mean([s['t20']>0 for s in bear_extreme])*100), 1) if bear_extreme else None,
        'bear_extreme_avg': round(float(np.mean([s['t20'] for s in bear_extreme])*100), 2) if bear_extreme else None,
        'bear_extreme_n': len(bear_extreme),
    }
    return result


def calc_grade(bt, stage, profit_trend, hold_state):
    """按共识框架评级"""
    if not bt or bt['overall_n'] < 30:
        return 'D', 0, '数据不足'
    
    wr = bt['overall_wr']
    bear_wr = bt['bear_deep_wr'] or bt['overall_wr']
    
    # 基础等级
    if wr >= 80:
        base = 'A'
    elif wr >= 65:
        base = 'B'
    elif wr >= 55:
        base = 'C'
    else:
        base = 'D'
    
    # 加分
    up = 0
    reasons = []
    
    # BEAR环境加分（Tier 2）
    if bear_wr and bear_wr >= wr + 5:
        up += 1
        reasons.append(f'BEAR+1档({bear_wr:.0f}%)')
    
    # 庄家行为加分
    if '出货' in (stage or ''):
        up += 1
        reasons.append('出货期+1')
    elif '建仓' in (stage or ''):
        up += 0.5
        reasons.append('建仓期+0.5')
    
    # 扣分
    down = 0
    if profit_trend and '恶化' in profit_trend:
        down += 1
        reasons.append(f'净利-1({profit_trend})')
    
    if '出逃' in (hold_state or ''):
        down += 1
        reasons.append('出逃-1')
    
    # 最终等级
    level_map = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
    base_num = level_map[base]
    final_num = base_num + down - up
    final_num = max(0, min(3, final_num))
    
    rev_map = {0: 'A', 1: 'B', 2: 'C', 3: 'D'}
    final = rev_map[final_num]
    
    # 仓位
    if final == 'A':
        pos = 1.0
    elif final == 'B':
        pos = 0.6
    elif final == 'C':
        pos = 0.3
    else:
        pos = 0
    
    return final, pos, ', '.join(reasons)


def print_ratings(ratings):
    """打印评级表"""
    print(f"\n{'='*90}")
    print(f"  最终评级表 — {date.today()}")
    print(f"{'='*90}")
    print(f"\n  {'股票':>8} {'代码':>8} {'T+20wr':>7} {'BEAR深跌':>8} {'庄家':>8} {'筹码':>8} {'净利':>12} {'偏离':>6} {'等级':>4} {'仓位':>5} {'理由'}")
    print(f"  {'-'*95}")
    
    for r in ratings:
        s = r['stage'] or '?'
        h = r['hold_state'] or '?'
        p = r['profit_trend'] or '?'
        sd = r['sdev']
        sig = '✅' if r['grade'] in ('A','B') else '⚠️'
        grade_str = f"{sig}{r['grade']}"
        print(f"  {r['name']:>8} {r['code']:>8} {r['bt']['overall_wr']:>5.0f}% {str(r['bt']['bear_deep_wr'] or 'N'):>6}s "
              f"{s:>8} {h:>8} {p:>12} {sd:>+5.1f}% {grade_str:>4} {r['pos']:>4.1f}x {r['reason']}")
    
    print(f"\n  --- 操作指南 ---")
    print(f"  A级: 仓位1.0x | P1卖1/3, P2卖1/3, 剩余等A_RECOVER脉冲")
    print(f"  B级: 仓位0.6x | P1卖1/2, P2清仓")
    print(f"  C级: 仓位0.3x | 仅BEAR极空可入场, P1卖1/2, P2清仓")
    print(f"  D级: 仓位0    | 不参与")
    print(f"\n  {'='*50}")
    print(f"  当前持仓建议")
    print(f"  {'='*50}")
    for r in ratings:
        if r['grade'] in ('A','B'):
            op = "加至满仓" if r['grade'] == 'A' else "维持当前"
            print(f"  {r['name']}({r['code']}): {r['grade']}级 {r['pos']}x → {op}")
        else:
            print(f"  {r['name']}({r['code']}): {r['grade']}级 → 等条件触发")


if __name__ == '__main__':
    conn = psycopg2.connect(DB)
    
    targets = [
        ('嘉化能源', '600273'),
        ('软控股份', '002073'),
        ('广汇物流', '600603'),
        ('尖峰集团', '600668'),
        ('大众公用', '600635'),
    ]
    
    # 拉庄家数据 + 逐日回测汇总
    ratings = []
    for name, code in targets:
        print(f"  {name}...", file=sys.stderr)
        bt = get_bt_summary(code, conn)
        sg = get_stage_data(code, conn)
        
        if not bt:
            print(f"    ❌ BT数据不足", file=sys.stderr)
            continue
        
        grade, pos, reason = calc_grade(
            bt, sg['stage'] if sg else None,
            sg['profit_trend'] if sg else None,
            sg['hold_state'] if sg else None,
        )
        
        ratings.append({
            'name': name,
            'code': code,
            'bt': bt,
            'stage': sg['stage'] if sg else None,
            'hold_state': sg['hold_state'] if sg else None,
            'profit_trend': sg['profit_trend'] if sg else None,
            'sdev': sg['sdev'] if sg else None,
            'close': sg['close'] if sg else None,
            'lock_ratio': sg['lock_ratio'] if sg else None,
            'grade': grade,
            'pos': pos,
            'reason': reason,
        })
        print(f"    ✅ {grade}级 {pos}x", file=sys.stderr)
    
    conn.close()
    
    # 打印
    print_ratings(ratings)
    
    # 保存
    os.makedirs(OUT, exist_ok=True)
    outpath = os.path.join(OUT, f'final_ratings_{date.today()}.json')
    # 序列化时处理numpy类型
    class NpEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.floating, np.integer)):
                return float(obj)
            if isinstance(obj, np.bool_):
                return bool(obj)
            return super().default(obj)
    
    with open(outpath, 'w') as f:
        json.dump(ratings, f, cls=NpEncoder, ensure_ascii=False, indent=1)
    print(f"\n保存: {outpath}", file=sys.stderr)
