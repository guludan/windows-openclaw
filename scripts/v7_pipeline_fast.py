"""v7_pipeline_fast.py — 快速版V7日更管线
不JOIN pool_energy，直接从 daily_prices + market_snapshots 算信号
"""
import psycopg2, numpy as np, json, time
from datetime import date, timedelta
from collections import defaultdict

DB="host=192.168.3.14 dbname=guludan user=guludan password=toto"
TODAY=date(2026,5,27); YESTERDAY=TODAY-timedelta(days=1)
LOOKBACK=date(2026,4,1)
conn=psycopg2.connect(DB); cur=conn.cursor(); t0=time.time()

# ===== 0) 大盘相空间 =====
cur.execute("SELECT b_pool,a_pool,phase FROM pool_energy WHERE level='market' AND date=%s",(TODAY,))
m=cur.fetchone()
if not m: print("无大盘数据"); exit()
MP=m[2]; ENERGY=float(m[0]+m[1]); SURPLUS=float(m[0]-m[1])

# ===== 1) 今日日线 =====
cur.execute("""
    SELECT SUBSTRING(symbol,3) AS c6, close, turnover, volume, amount
    FROM daily_prices WHERE trade_date=%s AND close>0
""",(TODAY,))
today_px={r[0]:{'c':r[1],'to':r[2],'vol':r[3],'amt':r[4]} for r in cur.fetchall()}
print(f"今日日线: {len(today_px)}只 ({time.time()-t0:.0f}s)")

# ===== 2) 历史日线（只拉有今天的股票） =====
codes=list(today_px.keys())
print(f"加载历史特征 ({len(codes)}只)...")
px_raw=defaultdict(list)
BATCH=500
for ii in range(0,len(codes),BATCH):
    batch=codes[ii:ii+BATCH]
    placeholders=','.join([f"'{c}'" for c in batch])
    cur.execute(f"""
        SELECT SUBSTRING(symbol,3), trade_date, close, turnover
        FROM daily_prices
        WHERE SUBSTRING(symbol,3) IN ({placeholders})
          AND trade_date>='{LOOKBACK}' AND trade_date<='{TODAY}'
          AND close>0
        ORDER BY trade_date
    """)
    for r in cur.fetchall():
        px_raw[r[0]].append((r[1],r[2],r[3]))
    if (ii//BATCH+1)%5==0: print(f"  ...{ii}/{len(codes)}")
print(f"  完成 ({time.time()-t0:.0f}s)")

# ===== 3) 信号检测+除权过滤 =====
# 强除权过滤: 涨幅>20%直接踢
thresh=0.20  # 主板>20%

# 增减持
cur.execute("SELECT code,change_date,is_increase,is_decrease FROM incr_decr WHERE change_date>=%s AND change_date<=%s",(TODAY-timedelta(days=7),TODAY))
ins=defaultdict(lambda:{'b':0,'s':0})
for c,dt,inc,dec in cur.fetchall():
    if inc: ins[c]['b']=1
    if dec: ins[c]['s']=1

signals=[]  # (c6, sig_type, dict)
valid_total=0; exright_total=0; scan_total=0

for c6,px in today_px.items():
    rows=px_raw.get(c6,[])
    dl=[r[0] for r in rows]
    try: idx=dl.index(TODAY)
    except ValueError: continue
    if idx<20: continue
    
    r0=rows[idx]
    c0=r0[1]; to0=r0[2] or 0
    ret=c0/rows[idx-1][1]-1
    scan_total+=1
    
    # === 除权过滤 ===
    if abs(ret) > thresh: exright_total+=1; continue
    
    # 特征
    r5=c0/rows[idx-5][1]-1 if idx>=5 else 0
    r20=[rows[idx-k][1]/rows[idx-k-1][1]-1 for k in range(1,21)]
    vz2=np.std(r20)
    vol5=np.mean([abs(rows[idx-k][1]/rows[idx-k-1][1]-1) for k in range(5)])
    vz2=vol5/vz2 if vz2>0 else 0
    
    to20=[rows[idx-k][2] for k in range(1,21) if rows[idx-k][2] and rows[idx-k][2]>0]
    mu=np.mean(to20) if to20 else 0; sd=np.std(to20) if to20 else 0
    tz=(to0-mu)/sd if sd>0 else 0
    
    # 信号检测
    sts=[]
    if vz2>2.5 and abs(tz)>2.0 and ret<-0.05: sts.append('s3_panic')
    if vz2>2.5 and tz>2.0 and ret>0.05: sts.append('s2_pulse')
    if vz2>3.0 and abs(tz)<1.5: sts.append('s1_surge')
    
    if not sts: continue
    valid_total+=1
    sigs='/'.join(sts)
    
    i=ins.get(c6,{'b':0,'s':0})
    cur.execute("SELECT v7_score_s3(%s,%s,%s,%s,%s,0,%s)",(float(r5),float(tz),ENERGY,i['b'],i['s'],MP))
    v7=cur.fetchone()[0]
    
    signals.append({
        'c6':c6,'c':c0,'to':to0,'ret':ret,'r5':r5,
        'vz2':vz2,'tz':tz,'sigs':sigs,'v7':float(v7)
    })

print(f"\n扫描: {scan_total} | 除权过滤: {exright_total} | 信号: {valid_total}")
by_sig=defaultdict(int)
for s in signals:
    for t in s['sigs'].split('/'): by_sig[t]+=1
print(f"  S3:{by_sig.get('s3_panic',0)} S2:{by_sig.get('s2_pulse',0)} S1:{by_sig.get('s1_surge',0)}")

# 写入 pool_energy
updated=0
for s in signals:
    extra=json.dumps({k:round(float(v),4) if isinstance(v,(int,float)) else v for k,v in s.items() if k!='c6'})
    cur.execute("UPDATE pool_energy SET b_inject=%s,a_inject=%s,phase=%s,extra=%s WHERE level=%s AND date=%s",
        (m[0],m[1],s['sigs'],extra,'code:A.'+s['c6'],TODAY))
    updated+=1
conn.commit()
print(f"  pool_energy写入: {updated}")

# S3 清单
s3s=[s for s in signals if 's3_panic' in s['sigs']]
if s3s:
    print(f"\nS3清单:")
    for s in s3s:
        print(f"  {s['c6']}: ret={s['ret']:+.2%} vz2={s['vz2']:.2f} tz={s['tz']:.1f} V7={s['v7']:+.4f}")

# ===== 4) 行业排行 =====
cur.execute("SELECT stock_code,industry FROM industry_components_ths_l1 WHERE industry IS NOT NULL")
inds={}
for c,ind in cur.fetchall():
    # 直接从 today_px 拿 ret
    px_=today_px.get(c)
    if px_ is None: continue
    # 需要历史数据算 ret
    rows=px_raw.get(c,[])
    if len(rows)<2: continue
    try: idx_=[r[0] for r in rows].index(TODAY)
    except ValueError: continue
    if idx_<1: continue
    ret_=rows[idx_][1]/rows[idx_-1][1]-1
    inds[c]=(ind.replace(' ',''),ret_)

ind_s=defaultdict(list)
for c,(ind,ret_) in inds.items(): ind_s[ind].append(ret_)
ind_scores=[(ind,float(np.median(stks)),len(stks)) for ind,stks in ind_s.items() if len(stks)>=3]
ind_scores.sort(key=lambda x:-x[1])
print(f"\n行业:")
for ind,d1,n in ind_scores[:8]:
    print(f"  {ind[:12]:>12s} {d1:>+7.2%} {n:>5d}")

# 选股
print(f"\n选股:")
for ind,d1,n in ind_scores[:3]:
    if d1<0.003: continue
    picks=[]
    for c,(ind_,ret_) in inds.items():
        if ind_!=ind or ret_<0 or ret_>0.07: continue
        px_=today_px.get(c);
        if not px_ or not px_['to'] or px_['to']<0.5: continue
        picks.append((ret_,c))
    picks.sort(key=lambda x:-x[0])
    if picks:
        print(f"  {ind}:")
        for r_,c_ in picks[:3]:
            print(f"    {c_}: {r_:+.2%}")

print(f"\n⏱️ {time.time()-t0:.0f}s")
conn.close()
