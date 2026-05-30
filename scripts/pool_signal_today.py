"""pool_signal_today.py v2 — 基于成交金额(amount)的 S1/S2/S3 信号检测"""
import psycopg2, json, time, numpy as np
from datetime import date, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
today = date(2026,5,27)
t0 = time.time()

conn = psycopg2.connect(DB); cur = conn.cursor()
cur.execute("SELECT DISTINCT level FROM pool_energy WHERE level LIKE 'code:A.%%' AND date=%s",(today,))
codes = [r[0].replace('code:A.','') for r in cur.fetchall()]
print(f"占位: {len(codes)} 只 ({time.time()-t0:.1f}s)")

# 分批拉日线（用 amount 代替 turnover）
B=200; all_px=[]
for i in range(0,len(codes),B):
    cset="','".join(codes[i:i+B])
    cur.execute(f"""SELECT SUBSTRING(symbol,3),trade_date,close,amount FROM daily_prices
        WHERE SUBSTRING(symbol,3) IN ('{cset}') AND trade_date>='2026-04-17' AND trade_date<='{today}' AND close>0 ORDER BY 1,2""")
    all_px.extend(cur.fetchall())
print(f"日线: {len(all_px)} 行 ({time.time()-t0:.1f}s)")

# 大盘
cur.execute("SELECT date,b_pool,a_pool,phase FROM pool_energy WHERE level='market' ORDER BY date")
mkt={r[0]:{'b':r[1],'a':r[2],'ph':r[3] or 'NEUTRAL'} for r in cur.fetchall()}

px=defaultdict(list)
for c6,dt,cl,am in all_px: px[c6].append((dt,cl,am or 0))

updated=0; stats={'s1_surge':0,'s2_pulse':0,'s3_panic':0}
for c6 in codes:
    pl=px.get(c6,[])
    if len(pl)<25: continue
    dl=[p[0] for p in pl]
    try: idx=dl.index(today)
    except ValueError: continue
    if idx<5: continue
    
    c0=pl[idx][1]; c1=pl[idx-1][1] if idx>=1 else None
    ret=(c0/c1-1) if c1 else 0.0
    
    # 用 amount（成交金额）做 vol_z
    am_today=p=pl[idx][2]
    am20=[pl[idx-k][2] for k in range(1,21) if idx-k>=0 and pl[idx-k][2]>0]
    vz=0.0
    if p and len(am20)>=5: mu,sd=np.mean(am20),np.std(am20); vz=(p-mu)/sd if sd>0 else 0.0
    
    # 波动率vol_z（近5日 vs 20日收益率波动）
    r20=list(filter(None,[pl[idx-k][1]/pl[idx-k-1][1]-1 for k in range(1,21) if idx-k>=0 and pl[idx-k-1][1]]))
    vol20=np.std(r20) if len(r20)>=10 else 0.01
    r5=list(filter(None,[abs(pl[idx-k][1]/pl[idx-k-1][1]-1) for k in range(5) if idx-k>=0 and pl[idx-k-1][1]]))
    vol5=np.mean(r5) if r5 else 0.01
    vz2=vol5/vol20 if vol20>0 else 0
    
    sigs='no_signal'; sts=[]
    if vz2>2.5 and vz>2.0 and ret>0.05: sts.append('s2_pulse')
    if vz2>2.5 and vz>2.0 and ret<-0.05: sts.append('s3_panic')
    if vz2>3.0 and abs(vz)<1.5: sts.append('s1_surge')
    
    if sts:
        sigs='/'.join(sts)
        m=mkt.get(today,{'b':0,'a':0,'ph':'NEUTRAL'})
        extra=json.dumps({
            'sigs':sigs,'vol_z':round(float(vz2),2),'v_z':round(float(vz),2),
            'ret':round(float(ret),4),'total_pool':round(float(m['b']+m['a']),4)})
        cur.execute("""UPDATE pool_energy SET b_inject=%s,a_inject=%s,phase=%s,extra=%s
            WHERE level=%s AND date=%s""",(m['b'],m['a'],m['ph'],extra,'code:A.'+c6,today))
        for s in sts: stats[s]+=1
        updated+=1
    
    if updated%100==0 and updated>0: print(f"  已更新{updated} ({time.time()-t0:.1f}s)",end='\r',flush=True)

conn.commit()
print(f"\n完成 ({time.time()-t0:.0f}s)")
print(f"  股票: {len(codes)} 信号更新: {updated}")
for k,v in stats.items(): print(f"  {k}: {v}")

# V7评分
print(f"\nV7 评分:")
cur.execute("""SELECT SUBSTRING(pe.level,8),pe.phase,pe.extra::text FROM pool_energy pe
    WHERE pe.date=%s AND pe.level LIKE 'code:A.%%'
    AND pe.extra->>'sigs' IN ('s1_surge','s2_pulse','s3_panic','s2_pulse/s1_surge','s3_panic/s1_surge')
    ORDER BY pe.phase""",(today,))
rows=cur.fetchall()
if rows:
    print(f"  {'代码':>8s} {'信号':12s} {'vz':>6s} {'ret':>7s} {'V7评分':>8s}")
    for r in rows:
        c6,ph,ext=r
        try: ex=json.loads(ext)
        except: continue
        vz=ex.get('vol_z',0); rt=ex.get('ret',0)
        s3_s=0
        if 's3_panic' in ph:
            cur.execute("SELECT v7_score_s3(%s,%s,%s,0,0,0,%s)",(0,vz,0,mkt.get(today,{}).get('ph','NEUTRAL')))
            s3_s=cur.fetchone()[0]
        s2_s=0
        if 's2_pulse' in ph and mkt.get(today,{}).get('ph')=='B_DISTRIB':
            cur.execute("SELECT v7_score_s2_bdistrib(%s,%s,%s,0,0)",(0,vz,0))
            s2_s=cur.fetchone()[0]
        print(f"  {c6:>8s} {ph:12s} {vz:>6.2f} {rt:>+6.2%} {s3_s+s2_s:>+8.4f}")
else:
    print("  今日无信号触发")
    
conn.close()
