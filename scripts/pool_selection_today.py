"""V7 风控选股 — 2026-05-27"""
import psycopg2, numpy as np, json, time
from datetime import date
from collections import defaultdict

conn=psycopg2.connect("host=192.168.3.14 dbname=guludan user=guludan password=toto")
cur=conn.cursor(); today=date(2026,5,27); t0=time.time()

# 1) 大盘
cur.execute("SELECT b_pool,a_pool,phase FROM pool_energy WHERE level='market' AND date=%s",(today,))
m=cur.fetchone()
print(f"大盘 Phase={m[2]} B={m[0]:.3f} A={m[1]:.3f} 风险预算=0.4x\n")

# 2) 日线
cur.execute("""
    SELECT SUBSTRING(symbol,3),close,turnover,(close/lag(close)OVER w-1)as d1,
           (close/lag(close,5)OVER w-1)as d5,(close/lag(close,20)OVER w-1)as d20
    FROM daily_prices WHERE trade_date=%s AND close>0 AND turnover IS NOT NULL
    WINDOW w AS(PARTITION BY symbol ORDER BY trade_date)
""",(today,))
px={r[0]:{'c':r[1],'to':r[2],'d1':r[3],'d5':r[4],'d20':r[5]} for r in cur.fetchall()}
print(f"日线: {len(px)} 只")

# 3) 行业
cur.execute("SELECT stock_code,industry,stock_name FROM industry_components_ths_l1 WHERE industry IS NOT NULL")
ind_map={}
for c,ind,nm in cur.fetchall():
    if c in px and ind: ind_map[c]=(ind.replace(' ',''),nm or '')

# 4) 行业评分
ind_s=defaultdict(list)
for c,(ind,nm) in ind_map.items(): ind_s[ind].append(px[c])

ind_scores=[]
for ind,stks in ind_s.items():
    n=len(stks)
    if n<3: continue
    d1a=[s['d1'] for s in stks if s['d1'] is not None]
    d5a=[s['d5'] for s in stks if s['d5'] is not None]
    d20a=[s['d20'] for s in stks if s['d20'] is not None]
    if not d1a or not d5a or not d20a: continue
    d1=np.median(d1a); d5=np.median(d5a); d20=np.median(d20a)
    score=d1*0.1+d5*0.3+d20*0.6
    ind_scores.append((ind,score,d1,d5 or 0,d20 or 0,n))

ind_scores.sort(key=lambda x:-x[1])
print(f"\n{'行业':>12s} {'评分':>8s} {'1d':>8s} {'5d':>8s} {'20d':>8s} {'只':>4s}")
print("-"*50)
for ind,s,d1,d5,d20,n in ind_scores[:10]:
    print(f"  {ind[:12]:>12s} {s:>+8.3f} {d1:>+7.2%} {d5:>+7.2%} {d20:>+8.2%} {n:>4d}")

# 5) 选股 (TOP3 行业, 0%<d1<6%, d5>0, d20>0, 换手>0.3%)
print(f"\n{'代码':>8s} {'名称':>8s} {'行业':>10s} {'收盘':>6s} {'1d':>7s} {'5d':>7s} {'20d':>7s} {'换手':>6s} {'评级':>6s}")
print("-"*66)

for ind,s,d1m,d5m,d20m,n in ind_scores[:3]:
    picks=[]
    for c in ind_map:
        if ind_map[c][0]==ind and c in px:
            p=px[c]
            if p['d1'] and p['d5'] and p['d20'] and 0<p['d1']<0.06 and p['d5']>0 and p['d20']>0 and p['to'] and p['to']>0.3:
                scores=[p['d1']*0.2+p['d5']*0.3+p['d20']*0.5]
                if p['d1']>0 and p['d1']<0.03: scores[0]+=0.02  # 温和上涨加分
                picks.append((scores[0],c,ind_map[c][1],p))
    picks.sort(key=lambda x:-x[0])
    for scr,c,nm,p in picks[:3]:
        med = np.median([x[0] for x in picks]) if picks else 0
        r = "推荐" if scr > med else "关注"
        print(f"  {c:>8s} {nm[:8]:>8s} {ind[:10]:>10s} {p['c']:>5.2f} {p['d1']:>+6.2%} {p['d5']:>+6.2%} {p['d20']:>+6.2%} {p['to']:>5.2f}% {r}")

# 6) 信号
print(f"\n信号股:")
cur.execute("""SELECT SUBSTRING(pe.level,8),pe.phase,pe.extra::text FROM pool_energy pe
    WHERE pe.date=%s AND pe.level LIKE 'code:A.%%' AND pe.extra->>'sigs'!='no_signal'""",(today,))
for r in cur.fetchall():
    ex=json.loads(r[2]) if r[2] else {}
    p=px.get(r[0],{})
    print(f"  {r[0]} {r[1]:>15s} ret={ex.get('ret',0):>+8.2%} vol_z={ex.get('vol_z',0):>5.2f} 收盘={p.get('c','?')}")

print(f"\n耗时: {time.time()-t0:.0f}s")
conn.close()
