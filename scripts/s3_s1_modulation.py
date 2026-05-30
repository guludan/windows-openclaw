"""s3_s1_modulation.py — 方向四：S1_active_nearby 对 S3 岭回归的影响"""
import numpy as np, pandas as pd, warnings, time, psycopg2, bisect
from collections import defaultdict
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score
warnings.filterwarnings("ignore")
t0 = time.time()

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

conn = psycopg2.connect(DB); cur = conn.cursor()
cur.execute("SELECT DISTINCT SUBSTRING(level,8) FROM pool_energy WHERE phase IN ('s1_surge','s3_panic') AND date>='2020-01-01'")
codes = [r[0] for r in cur.fetchall()]; print(f"股票: {len(codes)}")

B=200; all_px=[]
for i in range(0,len(codes),B):
    cset="','".join(codes[i:i+B])
    cur.execute(f"""SELECT SUBSTRING(symbol,3),trade_date,close,turnover FROM daily_prices 
        WHERE SUBSTRING(symbol,3) IN ('{cset}') AND trade_date>='2022-01-01' AND close>0 ORDER BY 1,2""")
    all_px.extend(cur.fetchall())
print(f"日线: {len(all_px)}")

cur.execute("SELECT date,phase,b_pool,a_pool FROM pool_energy WHERE level='market' AND date>='2020-01-01'")
mk={}
for r in cur.fetchall():
    p=r[1] or "NEUTRAL"; mk[r[0]]={"ph":p,"b":r[2] or 0,"a":r[3] or 0}
cur.execute("SELECT code,change_date,is_increase,is_decrease FROM incr_decr WHERE change_date>='2020-01-01'")
ins=defaultdict(list)
for r in cur.fetchall():
    if r[2]: ins[r[0]].append((r[1],"INC"))
    elif r[3]: ins[r[0]].append((r[1],"DEC"))

# S1 信号哈希表：(code6, date) → True
cur.execute("SELECT date,SUBSTRING(level,8) FROM pool_energy WHERE phase='s1_surge' AND date>='2020-01-01'")
s1_lookup = set((r[1],r[0]) for r in cur.fetchall())
print(f"S1: {len(s1_lookup)}")

# S3 信号
cur.execute("""SELECT pe.date,SUBSTRING(pe.level,8),mp.phase FROM pool_energy pe 
    LEFT JOIN pool_energy mp ON mp.level='market' AND mp.date=pe.date
    WHERE pe.phase='s3_panic' AND pe.date>='2020-01-01' ORDER BY pe.date""")
s3sigs=cur.fetchall(); conn.close(); print(f"S3: {len(s3sigs)}")

px_code=defaultdict(list)
for r in all_px: px_code[r[0]].append((r[1],r[2],r[3]))

PHASES=["B_ACCUM","B_DISTRIB","A_ACCUM","A_RECOVER","NEUTRAL","HI_EQ"]
recs=[]
for ds,c6,mkt_ph in s3sigs:
    pl=px_code.get(c6,[]); 
    if not pl: continue
    dl=[p[0] for p in pl]; ix=bisect.bisect_left(dl,ds)
    if ix>=len(dl) or dl[ix]!=ds: continue
    c0=pl[ix][1]; c3=pl[ix+3][1] if ix+3<len(pl) else None; c5=pl[ix+5][1] if ix+5<len(pl) else None
    if c3 is None and c5 is None: continue
    y3=(c3/c0-1) if(c3 and c0)else None; y5=(c5/c0-1) if(c5 and c0)else None
    lag5=(c0/pl[ix-5][1]-1) if(ix>=5 and pl[ix-5][1]) else 0.0
    to0=pl[ix][2]; tos=[pl[ix-k][2] for k in range(1,21) if ix-k>=0 and pl[ix-k][2] and pl[ix-k][2]>0]
    tz=0.0
    if to0 and len(tos)>=5: mu,sd=np.mean(tos),np.std(tos); tz=(to0-mu)/sd if sd>0 else 0.0
    m=mk.get(ds,{"ph":"NEUTRAL","b":0,"a":0})
    il=ins.get(c6,[])
    buy7=any(abs((dd-ds).days)<=7 and t=="INC" for dd,t in il)
    sell7=any(abs((dd-ds).days)<=7 and t=="DEC" for dd,t in il)
    en=float(m["b"]+m["a"])
    
    # S1_active_nearby：前 3 天是否有 S1
    s1_3d = 0
    for k in [-3,-2,-1,0]:
        dk = dl[ix+k] if 0<=ix+k<len(dl) else None
        if dk and (c6, dk) in s1_lookup:
            s1_3d = 1
            break
    
    f={"l5r":lag5,"tz":tz,"en":en,"ib":1 if buy7 else 0,"is_":1 if(sell7 and not buy7) else 0,
       "y3":y3,"y5":y5}
    for p in PHASES: f["p_"+p]=1 if m["ph"]==p else 0
    f["s1_nearby"] = s1_3d
    recs.append(f)

df=pd.DataFrame(recs); print(f"有效: {len(df)}")
base_cols=["l5r","tz","en","ib","is_"]+["p_"+p for p in PHASES]

for yc,label in [("y3","T+3"),("y5","T+5")]:
    dfo=df.dropna(subset=[yc]); N=len(dfo)
    X_base=dfo[base_cols].fillna(0).values; X_full=dfo[base_cols+["s1_nearby"]].fillna(0).values; y=dfo[yc].values
    
    # Baseline（无 s1_nearby）
    Xs=StandardScaler().fit_transform(X_base)
    sp=int(N*0.67); a_ext=np.logspace(-3,5,100)
    m0=RidgeCV(alphas=a_ext,cv=5,scoring="r2").fit(Xs[:sp],y[:sp])
    cv0=cross_val_score(RidgeCV(alphas=a_ext,cv=3),Xs,y,cv=5,scoring="r2")
    
    # 加 s1_nearby
    Xsf=StandardScaler().fit_transform(X_full)
    m1=RidgeCV(alphas=a_ext,cv=5,scoring="r2").fit(Xsf[:sp],y[:sp])
    cv1=cross_val_score(RidgeCV(alphas=a_ext,cv=3),Xsf,y,cv=5,scoring="r2")
    
    # s1_nearby 系数
    idx = len(base_cols)  # s1_nearby 是最后一列
    s1_coef = m1.coef_[idx]
    
    print(f"\n{label} (N={N}):")
    print(f"  Baseline: CV R2={cv0.mean():+.4f}")
    print(f"  +s1_nearby: CV R2={cv1.mean():+.4f}")
    print(f"  s1_nearby系数={s1_coef:.6f} (其它系数:")
    for i,c in enumerate(base_cols): 
        print(f"    {c:>20s}: {m1.coef_[i]:.6f}")
    
    # 符号稳定性
    stable=True
    for alp in np.logspace(-1,1,20):
        rr=RidgeCV(alphas=[alp],cv=3).fit(Xsf,y)
        if rr.coef_[idx]*s1_coef < 0: stable=False; break
    print(f"  s1_nearby符号稳定: {'✅' if stable else '❌'}")
    print(f"  R2变化: {cv1.mean()-cv0.mean():+.4f}")

print(f"\n耗时: {time.time()-t0:.0f}s")
