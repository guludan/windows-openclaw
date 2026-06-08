#!/usr/bin/env python3
"""
补跑：广汇物流(600603)、尖峰集团(600668)、大众公用(600635) 的逐日滚动回测
提取 stock_rolling_backtest.py 的核心逻辑，再加嘉化能源(600273)验证。
"""
import sys, psycopg2, numpy as np
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

def rolling_bt(sc, name, conn):
    cur = conn.cursor()
    cur.execute(f"""
        SELECT trade_date, close, open, high, low, volume
        FROM daily_prices WHERE symbol='A.{sc}' AND close > 0
        ORDER BY trade_date
    """)
    rows = cur.fetchall()
    if not rows or len(rows) < 200:
        return None, "数据不足"
    
    dates = [r[0] for r in rows]
    closes = np.array([r[1] for r in rows], dtype=float)
    opens = np.array([r[2] for r in rows], dtype=float)
    highs = np.array([r[3] for r in rows], dtype=float)
    lows = np.array([r[4] for r in rows], dtype=float)
    vols = np.array([r[5] for r in rows], dtype=float)
    n = len(closes)
    
    # Regime数据
    cur2 = conn.cursor()
    cur2.execute("SELECT date, extra FROM pool_energy WHERE level='market' AND extra IS NOT NULL ORDER BY date")
    regime_map = {}
    for td, e in cur2.fetchall():
        if isinstance(e, dict):
            regime_map[td.isoformat()] = {'regime': e.get('regime', 'UNK'), 'sz': e.get('surplus_z', 0)}
    def get_regime(td_str):
        best = None
        for k, v in sorted(regime_map.items()):
            if k <= td_str: best = v
            else: break
        return best or {'regime': 'UNK', 'sz': 0}
    
    signals = []
    for i in range(200, n - 20):
        cur_close = closes[i]
        
        # SMA120（含当天，偏差极微）
        sma120 = np.mean(closes[i-119:i+1])
        sdev = cur_close / max(sma120, 0.01) - 1
        
        if sdev >= -0.10:
            continue
        
        # 庄家阶段
        p5v = np.mean(vols[max(0,i-4):i+1])
        p20v = np.mean(vols[max(0,i-19):i+1])
        vi = p5v / max(p20v, 1)
        
        sh_cnt = 0
        for j in range(max(0, i-9), i+1):
            cr = highs[j] - lows[j]
            if cr > 0 and (highs[j] - max(opens[j], closes[j])) / cr > 0.4:
                sh_cnt += 1
        
        yang = closes[max(0,i-19):i+1] >= opens[max(0,i-19):i+1]
        y20 = np.mean(yang)
        
        if vi > 1.3 and sh_cnt >= 3:
            stage = "出货期"
        elif y20 > 0.55 and 1.0 <= vi <= 1.8:
            stage = "建仓期"
        else:
            stage = "无主力特征"
        
        t3 = closes[i+3]/cur_close - 1 if i+3 < n else None
        t5 = closes[i+5]/cur_close - 1 if i+5 < n else None
        t10 = closes[i+10]/cur_close - 1 if i+10 < n else None
        t20 = closes[i+20]/cur_close - 1 if i+20 < n else None
        
        reg = get_regime(dates[i].isoformat())
        
        signals.append({
            'date': dates[i].isoformat(),
            'close': float(cur_close),
            'sdev': float(sdev),
            'stage': stage,
            'regime': reg['regime'],
            'surplus_z': reg['sz'],
            't3': float(t3) if t3 is not None else None,
            't5': float(t5) if t5 is not None else None,
            't10': float(t10) if t10 is not None else None,
            't20': float(t20) if t20 is not None else None,
        })
    
    return signals, None


def fmt(arr, label):
    if len(arr) < 3:
        return f"  {label}: N/A (n={len(arr)})"
    m = float(np.mean(arr))
    md = float(np.median(arr))
    wr = float(np.mean(arr > 0) * 100)
    sd = float(np.std(arr))
    return f"  {label}: avg={m:+.2%} med={md:+.2%} wr={wr:.0f}% sd={sd:.2%} n={len(arr)}"


def analyze(sigs, sc, name):
    print(f"\n{'='*80}")
    print(f"【{sc} {name}】逐日滚动回测 | 信号{len(sigs)}个")
    print(f"{'='*80}")
    
    if len(sigs) < 10:
        print("  信号太少")
        return
    
    t20s = np.array([s['t20'] for s in sigs if s['t20'] is not None])
    t5s = np.array([s['t5'] for s in sigs if s['t5'] is not None])
    t10s = np.array([s['t10'] for s in sigs if s['t10'] is not None])
    
    print(f"  总体: T+5 {fmt_ext(t5s)}  T+10 {fmt_ext(t10s)}  T+20 {fmt_ext(t20s)}")
    
    # 按偏离
    for dl, dlo, dhi in [('S3<-25%', -1, -0.25), ('S2-20~-25%', -0.25, -0.20),
                          ('S1-15~-20%', -0.20, -0.15), ('V8-10~-15%', -0.15, -0.10)]:
        sub = [s for s in sigs if dlo <= s['sdev'] < dhi]
        if len(sub) >= 5:
            t20 = [s['t20'] for s in sub if s['t20'] is not None]
            print(f"  [{dl} n={len(sub)}] T+20: {fmt_ext(t20)}")
    
    # 按Regime
    for r in ['BEAR', 'BULL', 'SIDEWAYS']:
        sub = [s for s in sigs if s['regime'] == r]
        if len(sub) >= 10:
            t20 = [s['t20'] for s in sub if s['t20'] is not None]
            print(f"  [{r:<10} n={len(sub)}] T+20: {fmt_ext(t20)}")
    
    # 按阶段
    for st in ['出货期', '建仓期', '无主力特征']:
        sub = [s for s in sigs if s['stage'] == st]
        if len(sub) >= 10:
            t20 = [s['t20'] for s in sub if s['t20'] is not None]
            print(f"  [{st:<8} n={len(sub)}] T+20: {fmt_ext(t20)}")
    
    # BEAR极空+深跌
    bear_deep = [s for s in sigs if s['regime'] == 'BEAR' and s['surplus_z'] < -1.5 and s['sdev'] < -0.15]
    if len(bear_deep) >= 5:
        t20 = [s['t20'] for s in bear_deep if s['t20'] is not None]
        t5 = [s['t5'] for s in bear_deep if s['t5'] is not None]
        t10 = [s['t10'] for s in bear_deep if s['t10'] is not None]
        neg20 = sum(1 for v in t20 if v < -0.15)
        gt10 = sum(1 for v in t20 if v > 0.10)
        print(f"  [熊市极空+深跌 n={len(bear_deep)}]")
        print(f"    T+5 {fmt_ext(t5)}")
        print(f"    T+10 {fmt_ext(t10)}")
        print(f"    T+20 {fmt_ext(t20)}")
        print(f"    T+20亏损>15%: {neg20}次({neg20/len(bear_deep)*100:.0f}%) 盈利>10%: {gt10}次({gt10/len(bear_deep)*100:.0f}%)")
    
    # BEAR深跌（不限surplus_z）
    bear = [s for s in sigs if s['regime'] == 'BEAR' and s['sdev'] < -0.15]
    if len(bear) >= 5:
        t20 = [s['t20'] for s in bear if s['t20'] is not None]
        neg15 = sum(1 for v in t20 if v < -0.15)
        print(f"  [BEAR深跌< -15% n={len(bear)}] T+20: {fmt_ext(t20)} 亏损>15%: {neg15}/{len(bear)}({neg15/len(bear)*100:.0f}%)")
    
    # 近5次
    print(f"\n  --- 最近5次信号 ---")
    for s in sigs[-5:]:
        print(f"  {s['date'][5:]}: sdev={s['sdev']:+.1%} stg={s['stage'][:2]} "
              f"T+3v={s['t3'] if s['t3'] is not None else 'N':>6} "
              f"T+5v={s['t5'] if s['t5'] is not None else 'N':>6} "
              f"T+10={s['t10'] if s['t10'] is not None else 'N':>6} "
              f"T+20={s['t20'] if s['t20'] is not None else 'N':>6}")

    # 当前信号
    ls = sigs[-1]
    print(f"\n  当前({ls['date']}): close={ls['close']:.2f} sdev={ls['sdev']:+.2%} stage={ls['stage']} regime={ls['regime']} sz={ls['surplus_z']:.2f}")


def fmt_ext(arr):
    arr = np.array(arr)
    m = np.mean(arr)
    md = np.median(arr)
    wr = np.mean(arr > 0) * 100
    sd = np.std(arr)
    return f"avg={float(m):+.2%} med={float(md):+.2%} wr={float(wr):.0f}% sd={float(sd):.2%} n={len(arr)}"


if __name__ == '__main__':
    conn = psycopg2.connect(DB)
    
    targets = [
        # 今天需要补的三只
        ('600603', '广汇物流'),
        ('600668', '尖峰集团'),
        ('600635', '大众公用'),
        # 嘉化验证
        ('600273', '嘉化能源'),
    ]
    
    for sc, name in targets:
        sigs, err = rolling_bt(sc, name, conn)
        if err:
            print(f"\n{sc} {name}: ❌ {err}")
        else:
            analyze(sigs, sc, name)
    
    conn.close()
