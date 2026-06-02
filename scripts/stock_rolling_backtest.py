#!/usr/bin/env python3
"""
逐日滚动个股独立回测 — 无前视偏差
站在每天的收盘价，只用当天之前的数据判断是否触发V8条件
"""
import sys, psycopg2, numpy as np, json
from collections import defaultdict
from datetime import date, datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

def rolling_stock_bt(sc, name, conn):
    cur = conn.cursor()
    
    # 全量日线
    cur.execute(f"""
        SELECT trade_date, close, open, high, low, volume
        FROM daily_prices WHERE symbol='A.{sc}' AND close > 0
        ORDER BY trade_date
    """)
    rows = cur.fetchall()
    if not rows or len(rows) < 200:
        return None
    
    dates = [r[0] for r in rows]
    closes = np.array([r[1] for r in rows], dtype=float)
    opens = np.array([r[2] for r in rows], dtype=float)
    highs = np.array([r[3] for r in rows], dtype=float)
    lows = np.array([r[4] for r in rows], dtype=float)
    vols = np.array([r[5] for r in rows], dtype=float)
    n = len(closes)
    
    # 拉regime数据
    cur2 = conn.cursor()
    cur2.execute("SELECT date, extra FROM pool_energy WHERE level='market' AND extra IS NOT NULL ORDER BY date")
    regime_map = {}
    for td, e in cur2.fetchall():
        if isinstance(e, dict):
            regime_map[td.isoformat()] = {'regime': e.get('regime', 'UNK'), 'sz': e.get('surplus_z', 0)}
    def get_latest_regime(td_str):
        best = None
        for k, v in sorted(regime_map.items()):
            if k <= td_str:
                best = v
            else:
                break
        return best or {'regime': 'UNK', 'sz': 0}
    
    # 逐日滚动遍历
    signals = []
    
    for i in range(200, n - 20):  # 200日启动，预留-20%所需的时间
        # === 站在第i天（收盘价），只用[i-119:i+1]的数据 ===
        lookback_start = max(0, i - 119)
        lookback = closes[lookback_start:i+1]
        if len(lookback) < 60:
            continue
        
        sma120 = np.mean(lookback)
        cur_close = closes[i]
        sdev = cur_close / max(sma120, 0.01) - 1
        
        if sdev >= -0.10:
            continue  # 非V8
        
        # V8层级
        tier = "V8轻仓"
        if sdev < -0.30: tier = "S3深度"
        elif sdev < -0.25: tier = "S2重仓"
        elif sdev < -0.20: tier = "S1中度"
        elif sdev < -0.15: tier = "V8标准"
        
        # 当前波动率（使用到i为止的数据）
        p_hist = closes[max(0,i-59):i+1]
        hist_vol = np.std(p_hist) / max(np.mean(p_hist), 0.01) * 100
        p_short = closes[max(0,i-9):i+1]
        short_vol = np.std(p_short) / max(np.mean(p_short), 0.01) * 100
        
        # 庄家阶段（使用到i为止的数据）
        p5v = np.mean(vols[max(0,i-4):i+1])
        p20v = np.mean(vols[max(0,i-19):i+1])
        vi = p5v / max(p20v, 1)
        
        # 上影线（最近10天）
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
        
        # 未来收益（无前视偏差——只用i+3/i+5/i+10/i+20的真实价格）
        t3 = closes[i+3]/cur_close - 1 if i+3 < n else None
        t5 = closes[i+5]/cur_close - 1 if i+5 < n else None
        t10 = closes[i+10]/cur_close - 1 if i+10 < n else None
        t20 = closes[i+20]/cur_close - 1 if i+20 < n else None
        
        reg = get_latest_regime(dates[i].isoformat())
        
        signals.append({
            'date': dates[i].isoformat(),
            'close': cur_close,
            'sdev': sdev,
            'tier': tier,
            'stage': stage,
            'hist_vol': hist_vol,
            'short_vol': short_vol,
            'regime': reg['regime'],
            'surplus_z': reg['sz'],
            't3': t3, 't5': t5, 't10': t10, 't20': t20,
        })
    
    return signals, dates[-1], closes[-1], sdev, n

def fmt(arr, label):
    if len(arr) < 3:
        return f"  {label}: N/A (n={len(arr)})"
    m = np.mean(arr)
    md = np.median(arr)
    wr = np.mean(arr > 0) * 100
    sd = np.std(arr)
    return f"  {label}: avg={m:+.2%} med={md:+.2%} wr={wr:.0f}% sd={sd:.2%} n={len(arr)}"

def analyze(sigs, sc, name):
    """对信号做多维度统计"""
    print(f"\n{'='*80}")
    print(f"【{sc} {name}】逐日滚动回测 | 信号{len(sigs)}个")
    print(f"{'='*80}")
    
    if len(sigs) < 10:
        print("  信号太少，分析不可靠")
        return
    
    # 1. 总体
    t20s = np.array([s['t20'] for s in sigs if s['t20'] is not None])
    print(f"  全部信号T+20: avg={np.mean(t20s):+.2%} wr={np.mean(t20s>0)*100:.0f}% n={len(t20s)}")
    
    # 2. 按偏离深度
    depths = [('S3超深', -100, -0.25), ('S2深跌', -0.25, -0.20), 
              ('S1中度', -0.20, -0.15), ('V8标准', -0.15, -0.10)]
    for dl, dlo, dhi in depths:
        sub = [s for s in sigs if dlo <= s['sdev'] < dhi]
        if len(sub) >= 5:
            t20 = np.array([s['t20'] for s in sub if s['t20'] is not None])
            t5 = np.array([s['t5'] for s in sub if s['t5'] is not None])
            print(f"  [{dl} n={len(sub)}] T+5:{np.mean(t5):+.2%}({np.mean(t5>0)*100:.0f}%) T+20:{np.mean(t20):+.2%}({np.mean(t20>0)*100:.0f}%)")
    
    # 3. 按Regime
    for r in ['BEAR', 'BULL', 'SIDEWAYS']:
        sub = [s for s in sigs if s['regime'] == r]
        if len(sub) >= 10:
            t20 = np.array([s['t20'] for s in sub if s['t20'] is not None])
            print(f"  [{r} n={len(sub)}] T+20: avg={np.mean(t20):+.2%} wr={np.mean(t20>0)*100:.0f}%")
    
    # 4. 按阶段
    for st in ['出货期', '建仓期', '无主力特征']:
        sub = [s for s in sigs if s['stage'] == st]
        if len(sub) >= 10:
            t20 = np.array([s['t20'] for s in sub if s['t20'] is not None])
            print(f"  [{st} n={len(sub)}] T+20: avg={np.mean(t20):+.2%} wr={np.mean(t20>0)*100:.0f}%")
    
    # 5. 核心问题：熊市、极空区、深跌  → 真实T+20分布
    bear_deep = [s for s in sigs if s['regime'] in ('BEAR',) and s['surplus_z'] < -1.5 and s['sdev'] < -0.15]
    if len(bear_deep) >= 5:
        t20 = np.array([s['t20'] for s in bear_deep if s['t20'] is not None])
        t5 = np.array([s['t5'] for s in bear_deep if s['t5'] is not None])
        print(f"  [熊市极空+深跌 n={len(bear_deep)}]")
        print(f"    T+5:  avg={np.mean(t5):+.2%} med={np.median(t5):+.2%} wr={np.mean(t5>0)*100:.0f}% sd={np.std(t5):.2%}")
        print(f"    T+10: avg={np.mean(t10_arr:=np.array([s['t10'] for s in bear_deep if s['t10'] is not None])):+.2%} wr={np.mean(t10_arr>0)*100:.0f}%")
        print(f"    T+20: avg={np.mean(t20):+.2%} med={np.median(t20):+.2%} wr={np.mean(t20>0)*100:.0f}% sd={np.std(t20):.2%}")
        
        # 列出极端情况
        neg20 = sum(1 for s in bear_deep if s['t20'] is not None and s['t20'] < -0.15)
        gt10 = sum(1 for s in bear_deep if s['t20'] is not None and s['t20'] > 0.10)
        print(f"    T+20亏损>15%: {neg20}次({neg20/len(bear_deep)*100:.0f}%) 盈利>10%: {gt10}次({gt10/len(bear_deep)*100:.0f}%)")
    
    # 6. 按sma_dev+Regime交叉
    print(f"\n  --- 偏离深度 × 市场状态(T+20) ---")
    for dl, dlo, dhi in depths:
        sub = [s for s in sigs if dlo <= s['sdev'] < dhi]
        if len(sub) < 5:
            continue
        for r in ['BEAR', 'SIDEWAYS', 'BULL']:
            ss = [s for s in sub if s['regime'] == r]
            if len(ss) >= 5:
                t20 = np.array([s['t20'] for s in ss if s['t20'] is not None])
                print(f"  {dl}+{r}: avg={np.mean(t20):+.2%} wr={np.mean(t20>0)*100:.0f}% n={len(ss)}")
    
    # 7. 近5次信号的实际收益
    print(f"\n  --- 最近5次信号的T+N收益 ---")
    for s in sigs[-5:]:
        print(f"  {s['date'][5:]}: sdev={s['sdev']:+.1%} stg={s['stage'][:2]} "
              f"T+3={s['t3'] if s['t3'] is not None else 'N/A'} "
              f"T+5={s['t5'] if s['t5'] is not None else 'N/A'} "
              f"T+10={s['t10'] if s['t10'] is not None else 'N/A'} "
              f"T+20={s['t20'] if s['t20'] is not None else 'N/A'}")
    
    # 8. 当前信号
    ls = sigs[-1]
    print(f"\n  当前({ls['date']}): close={ls['close']:.2f} sdev={ls['sdev']:+.2%} tier={ls['tier']} stage={ls['stage']} regime={ls['regime']} surplus_z={ls['surplus_z']:.2f}")

# ========== 跑 ==========
conn = psycopg2.connect(DB)

# P0
for sc, name in [('002073', '软控股份'), ('600273', '嘉化能源')]:
    r = rolling_stock_bt(sc, name, conn)
    if r:
        sigs, last_dt, last_cls, last_sdev, total_n = r
        analyze(sigs, sc, name)

# P1
for sc, name in [('601888', '中国中免'), ('601633', '长城汽车'),
                  ('600838', '上海九百'), ('600635', '大众公用'),
                  ('605188', '国光连锁'), ('600729', '重百集团'),
                  ('600702', '舍得酒业'), ('605339', '南侨食品'),
                  ('688121', '卓然股份'),
                  ]:
    r = rolling_stock_bt(sc, name, conn)
    if r:
        sigs, _, _, _, _ = r
        analyze(sigs[:600], sc, name)  # 控制输出长度

conn.close()
