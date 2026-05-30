"""S3 超额收益 — Python侧每日均值baseline"""
import psycopg2, numpy as np
from collections import defaultdict
from datetime import timedelta

conn = psycopg2.connect("host=192.168.3.14 dbname=guludan user=guludan password=toto")
cur = conn.cursor()

# 大盘phase
cur.execute("SELECT date, phase FROM pool_energy WHERE level='market' ORDER BY date")
mkt_phase = {r[0]: r[1] or 'NEUTRAL' for r in cur.fetchall()}

# S3信号
cur.execute("SELECT trade_date, ret5d, ret20d, v7_score FROM backtest_signals WHERE signal_type='s3_panic' AND ret5d IS NOT NULL ORDER BY trade_date")
signals = [(r[0], float(r[1]), float(r[2] or 0), float(r[3])) for r in cur.fetchall()]
print(f"S3信号: {len(signals)}")

# 全A每日均值: 从 backtest_signals 按全部信号取 AVG(ret5d) per date
# 实际上这是"信号发生日全A的信号强度均值"，不是完美的全A基准
# 但 backtest_signals 只记录了有信号的日子...
# 更好的：直接从 daily_prices 取每日所有股票的 5日收益均值
# 用优化版本
print("计算全A日均T+5收益...")

# 拉所有股票全量日线 2020+ (有close即可)
cur.execute("""
    SELECT trade_date, AVG(close5/close-1)::double precision
    FROM (
        SELECT symbol, trade_date, close,
               LEAD(close, 5) OVER(PARTITION BY symbol ORDER BY trade_date) AS close5
        FROM daily_prices
        WHERE trade_date >= '2019-01-01' AND close > 0
    ) sub
    WHERE close5 IS NOT NULL
    GROUP BY trade_date
    ORDER BY trade_date
""")
daily_avg = {r[0]: float(r[1]) for r in cur.fetchall()}
print(f"日均T+5基准: {len(daily_avg)} 天")

# 按phase统计
ps = defaultdict(lambda: {'n':0,'a5':[],'e5':[],'a20':[],'e20':[]})
for dt, ret5, ret20, v7 in signals:
    ph = mkt_phase.get(dt, 'NEUTRAL')
    i5 = daily_avg.get(dt)
    if i5 is None: continue
    d = ps[ph]
    d['n']+=1; d['a5'].append(ret5); d['e5'].append(ret5-i5)

total_n = sum(d['n'] for d in ps.values())
print(f"有基准: {total_n}/{len(signals)}")

print(f"\n{'='*65}")
print(f"  S3 超额收益 T+5 (vs 全A等权)")
print(f"{'='*65}")
print(f"  {'Phase':>15s} {'N':>5s} {'S3绝对':>8s} {'全A':>8s} {'超额':>8s} {'α胜率':>7s}")
for ph in ['B_ACCUM','A_RECOVER','NEUTRAL','B_DISTRIB','A_ACCUM','HI_EQ']:
    d = ps.get(ph)
    if not d or d['n'] < 3: continue
    e5 = np.array(d['e5'])
    i5_avg = np.mean(d['i5']) if d.get('i5') else np.mean([daily_avg.get(0,0)]) 
    # 实际上要用这个phase的指数均值
    bm5 = np.mean([daily_avg.get(s[0],0) for s in signals if mkt_phase.get(s[0],'NEUTRAL')==ph])
    print(f"  {ph:>15s} {d['n']:>5d} {np.mean(d['a5']):>+7.2%} {bm5:>+7.2%} {np.mean(e5):>+7.2%} {np.mean(e5>0):>6.1%}")

# V7分层超额
print(f"\n{'='*65}")
print(f"  V7评分 Top40% vs Bottom60% 超额T+5")
print(f"{'='*65}")
for ph in ['B_ACCUM','A_RECOVER','NEUTRAL','B_DISTRIB','A_ACCUM']:
    d = ps.get(ph)
    if not d or d['n'] < 10: continue
    sigs = [(dt, ret5, daily_avg.get(dt,0), v7) 
            for dt, ret5, ret20, v7 in signals 
            if mkt_phase.get(dt,'NEUTRAL')==ph and daily_avg.get(dt) is not None]
    sigs.sort(key=lambda x: -x[3])
    cutoff = int(len(sigs) * 0.4) or 1
    top_ex = [s[1]-s[2] for s in sigs[:cutoff]]
    bot_ex = [s[1]-s[2] for s in sigs[cutoff:]]
    print(f"  {ph:>15s} (N={len(sigs)}): Top40% {np.mean(top_ex):+.2%}  Bot60% {np.mean(bot_ex):+.2%}  差 {np.mean(top_ex)-np.mean(bot_ex):+.2%}")

# NEUTRAL β拆解
print(f"\n{'='*65}")
ear = {'a5':[],'i5':[],'n':0}; lat = {'a5':[],'i5':[],'n':0}
for dt, ret5, ret20, v7 in signals:
    if mkt_phase.get(dt,'') != 'NEUTRAL': continue
    i5 = daily_avg.get(dt); 
    if i5 is None: continue
    if dt.year < 2024:
        ear['a5'].append(ret5); ear['i5'].append(i5); ear['n']+=1
    else:
        lat['a5'].append(ret5); lat['i5'].append(i5); lat['n']+=1
for lb, nd in [('2020-2023',ear),('2024-2026',lat)]:
    if nd['n']<3: continue
    print(f"  NEUTRAL {lb} (N={nd['n']}): S3={np.mean(nd['a5']):+.2%} 全A={np.mean(nd['i5']):+.2%} β={np.mean(nd['i5'])/max(abs(np.mean(nd['a5'])),1e-6)*100:.0f}%")

# 被挡
print(f"\n{'='*65}")
for ph in ['A_ACCUM','B_DISTRIB']:
    d=ps.get(ph)
    if not d or d['n']<3: continue
    e5=np.array(d['e5'])
    print(f"  被挡 {ph:>12s} (N={d['n']}): 超额 {np.mean(e5):+.2%} α胜率 {np.mean(e5>0):.1%}")

conn.close()
