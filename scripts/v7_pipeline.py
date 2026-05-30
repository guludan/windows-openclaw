"""v7_pipeline.py — V7 完整日更流水线
收盘后跑此脚本完成：日线增量 → 信号检测(含除权过滤) → V7评分 → 选股
"""
import psycopg2, numpy as np, json, time, sys
from datetime import date, timedelta
from collections import defaultdict

sys.path.insert(0,'scripts')
from signal_filter import detect_exright

DB="host=192.168.3.14 dbname=guludan user=guludan password=toto"
TODAY=date(2026,5,27)
t0=time.time()

conn=psycopg2.connect(DB); cur=conn.cursor()

# ===== 步骤0：大盘相空间 =====
cur.execute("SELECT b_pool,a_pool,phase FROM pool_energy WHERE level='market' AND date=%s",(TODAY,))
m=cur.fetchone()
MP=m[2]; ENERGY=float(m[0]+m[1]); SURPLUS=float(m[0]-m[1])
print(f"📊 大盘 Phase={MP} B={m[0]:.3f} A={m[1]:.3f} 盈余={SURPLUS:.4f} ({time.time()-t0:.0f}s)")

# ===== 步骤1：加载所有有 pool_energy 占位的股票日线 =====
print(f"📥 加载日线...")
cur.execute("""
    SELECT SUBSTRING(p.level,8) AS c6, d.trade_date, d.close, d.turnover
    FROM pool_energy p
    JOIN daily_prices d ON d.symbol='A.'||SUBSTRING(p.level,8) AND d.trade_date>='2019-06-01' AND d.trade_date<=%s
    WHERE p.level!='market' AND d.close>0
    ORDER BY c6, d.trade_date
""",(TODAY,))
all_rows=cur.fetchall()
print(f"  {len(all_rows):,}行 ({time.time()-t0:.0f}s)")

# 按股票分组
px_raw=defaultdict(list)
for c6,td,cl,to in all_rows: px_raw[c6].append((td,cl,to))
print(f"  股票: {len(px_raw)}只")

# ===== 步骤2：特征计算 + 除权过滤 + 信号检测 + V7评分 =====
print(f"\n🔍 信号检测 (含除权过滤)...")

# 增减持
cur.execute("SELECT code,change_date,is_increase,is_decrease FROM incr_decr WHERE change_date>=%s AND change_date<=%s",(TODAY-timedelta(days=7),TODAY))
ins=defaultdict(lambda:{'b':0,'s':0})
for c,dt,inc,dec in cur.fetchall():
    if inc: ins[c]['b']=1
    if dec: ins[c]['s']=1

counts={'s3':0,'s2':0,'s1':0,'exright':0,'valid':0}
s3_list=[]; updated=0

for c6,rows in px_raw.items():
    dl=[r[0] for r in rows]
    try: idx=dl.index(TODAY)
    except ValueError: continue
    if idx<25: continue  # 需要20根K线算特征
    
    r=rows[idx]; c0=r[1]; to0=r[2]
    ret=c0/rows[idx-1][1]-1
    
    # ========== 除权除息过滤 (规则1: 极端涨跌) ==========
    if abs(ret) > 0.20:
        counts['exright']+=1
        continue
    
    # 前一日无成交
    if idx>=1:
        prev_to=rows[idx-1][2] or 0
        if prev_to < 0.001 and abs(ret) > 0.15:
            counts['exright']+=1
            continue
    
    # 无量异常
    if to0 is not None and to0>0:
        to20=[rows[idx-k-1][2] for k in range(20) if idx-k-1>=0 and rows[idx-k-1][2] and rows[idx-k-1][2]>0]
        if len(to20)>=5 and np.mean(to20)>0 and abs(ret)>0.15 and to0/np.mean(to20)<0.3:
            counts['exright']+=1
            continue
    
    # ========== 特征 ==========
    r5=rows[idx-5][1] if idx>=5 else None
    ret5=c0/r5-1 if r5 else 0.0
    
    r20=[rows[idx-k][1]/rows[idx-k-1][1]-1 for k in range(1,21)]
    vz2=np.std(r20); vol5=np.mean([abs(rows[idx-k][1]/rows[idx-k-1][1]-1) for k in range(5)])
    vz2=vol5/vz2 if vz2>0 else 0
    
    to20_vals=[rows[idx-k][2] for k in range(1,21) if rows[idx-k][2] is not None and rows[idx-k][2]>0]
    mu=np.mean(to20_vals) if to20_vals else 0; sd=np.std(to20_vals) if to20_vals else 0
    to0_=to0 if to0 is not None else 0
    tz=(to0_-mu)/sd if sd>0 else 0
    
    # ========== 信号检测 ==========
    sts=[]
    if vz2>2.5 and abs(tz)>2.0 and ret<-0.05: sts.append('s3_panic')
    if vz2>2.5 and tz>2.0 and ret>0.05: sts.append('s2_pulse')
    if vz2>3.0 and abs(tz)<1.5: sts.append('s1_surge')
    
    if not sts: continue
    sigs='/'.join(sts)
    counts['valid']+=1
    for s in sts:
        if s=='s3_panic': counts['s3']+=1
        elif s=='s2_pulse': counts['s2']+=1
        else: counts['s1']+=1
    
    # V7评分
    i=ins.get(c6,{'b':0,'s':0})
    cur.execute("SELECT v7_score_s3(%s,%s,%s,%s,%s,0,%s)",(float(ret5),float(tz),ENERGY,i['b'],i['s'],MP))
    v7=cur.fetchone()[0]
    
    extra=json.dumps({'sigs':sigs,'vol_z':round(vz2,2),'v_z':round(tz,2),
        'ret':round(ret,4),'total_pool':round(ENERGY,4),'v7_score':round(float(v7),4)})
    
    cur.execute("UPDATE pool_energy SET b_inject=%s,a_inject=%s,phase=%s,extra=%s WHERE level=%s AND date=%s",
        (m[0],m[1],sigs,extra,'code:A.'+c6,TODAY))
    updated+=1
    if 's3_panic' in sts: s3_list.append((c6,ret,vz2,tz,ret5,float(v7)))

conn.commit()
print(f"  S3:{counts['s3']} S2:{counts['s2']} S1:{counts['s1']}")
print(f"  更新: {updated} | 除权过滤剔除: {counts['exright']} | 有效: {counts['valid']}")

if s3_list:
    print(f"\n  S3清单:")
    print(f"  {'代码':>8s} {'ret':>8s} {'vz2':>5s} {'tz':>5s} {'r5':>8s} {'V7':>9s}")
    print("  "+"-"*48)
    for c6,r_,vz,tz_,r5,v7 in s3_list[:20]:
        print(f"  {c6:>8s} {float(r_):>+7.2%} {vz:>4.2f} {tz_:>4.1f} {float(r5):>+7.2%} {float(v7):>+8.4f}")

# ===== 步骤3：行业排行 =====
print(f"\n📈 行业排行...")
cur.execute("SELECT stock_code,industry,stock_name FROM industry_components_ths_l1 WHERE industry IS NOT NULL")
ind_map={}
for c,ind,nm in cur.fetchall():
    if c in px_raw: ind_map[c]=(ind.replace(' ',''),nm or '')

ind_s=defaultdict(list)
for c,(ind,nm) in ind_map.items(): 
    # 从 today_data 取今日收益
    cur.execute("SELECT trade_date,close FROM daily_prices WHERE symbol='A.'||%s AND trade_date>=%s AND close>0 ORDER BY trade_date",(c,TODAY-timedelta(days=5)))
    rows=cur.fetchall()
    if rows and rows[-1][0]==TODAY and rows[-1][1]>0:
        c1=rows[-2][1] if len(rows)>=2 else rows[0][1]
        d1=rows[-1][1]/c1-1 if c1>0 else 0
        ind_s[ind].append(d1)

ind_scores=[(ind,float(np.median(stks)),len(stks)) for ind,stks in ind_s.items() if len(stks)>=3]
ind_scores.sort(key=lambda x:-x[1])
print(f"  {'行业':>12s} {'涨幅中位':>8s} {'只数':>5s}")
print("  "+"-"*28)
for ind,d1,n in ind_scores[:8]:
    print(f"  {ind[:12]:>12s} {d1:>+7.2%} {n:>5d}")

# ===== 步骤4：选股（top3行业） =====
print(f"\n🎯 选股...")
for ind,d1,n in ind_scores[:3]:
    if d1<0.003: continue
    picks=[]
    for c in ind_map:
        if ind_map[c][0]!=ind: continue
        cur.execute("SELECT close,turnover FROM daily_prices WHERE symbol='A.'||%s AND trade_date=%s",(c,TODAY))
        px=cur.fetchone()
        if not px: continue
        c0,to0=px
        ret=c0/rows[-2][1]-1
        if ret<0 or ret>0.07: continue
        if to0<0.5: continue
        picks.append((ret,c,ind_map[c][1] or ''))
    picks.sort(key=lambda x:-x[0])
    if picks:
        print(f"\n  {ind}:")
        for r,c,nm in picks[:3]:
            print(f"    {c:>8s} {nm[:8]:>8s} {r:>+6.2%}")

print(f"\n⏱️ 总耗时: {time.time()-t0:.0f}s")
conn.close()
