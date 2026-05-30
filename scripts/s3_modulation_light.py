"""s3_modulation_light.py — 方向四：S3 + s1_nearby 调制验证（轻量版）"""
import numpy as np, pandas as pd, warnings, time, psycopg2, bisect
from collections import defaultdict
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score
warnings.filterwarnings("ignore")
t0 = time.time()

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

conn = psycopg2.connect(DB); cur = conn.cursor()

# S3 股票（用了小数据集——先取 S3 涉及的 2657 只）
cur.execute("SELECT DISTINCT SUBSTRING(level,8) FROM pool_energy WHERE phase='s3_panic' AND date>='2020-01-01'")
codes = [r[0] for r in cur.fetchall()]; print(f"S3股票: {len(codes)}")

# S1 信号哈希（全量，不依赖日线）
cur.execute("SELECT date,SUBSTRING(level,8) FROM pool_energy WHERE phase='s1_surge' AND date>='2020-01-01'")
s1_set = set((r[1], r[0]) for r in cur.fetchall())
print(f"S1信号: {len(s1_set)}")

# S3 信号（只拉 S3 数据）
cur.execute("""SELECT pe.date,SUBSTRING(pe.level,8),mp.b_pool,mp.a_pool,mp.phase
    FROM pool_energy pe
    LEFT JOIN pool_energy mp ON mp.level='market' AND mp.date=pe.date
    WHERE pe.phase='s3_panic' AND pe.date>='2020-01-01' ORDER BY pe.date""")
s3 = cur.fetchall(); print(f"S3: {len(s3)}")

# 只需要 S3 股票的日线，只看 close（不查换手率）
B=200; all_px=[]
for i in range(0,len(codes),B):
    cset="','".join(codes[i:i+B])
    cur.execute(f"SELECT SUBSTRING(symbol,3),trade_date,close FROM daily_prices WHERE SUBSTRING(symbol,3) IN ('{cset}') AND trade_date>='2019-06-01' AND close>0 ORDER BY 1,2")
    all_px.extend(cur.fetchall())
print(f"日线: {len(all_px)}")

cur.execute("SELECT code,change_date,is_increase,is_decrease FROM incr_decr WHERE change_date>='2020-01-01'")
ins=defaultdict(list)
for r in cur.fetchall():
    if r[2]: ins[r[0]].append((r[1],"INC"))
    elif r[3]: ins[r[0]].append((r[1],"DEC"))
conn.close()

px_code=defaultdict(list)
for r in all_px: px_code[r[0]].append((r[1],r[2]))  # (date, close)

PHASES=["B_ACCUM","B_DISTRIB","A_ACCUM","A_RECOVER","NEUTRAL","HI_EQ"]
recs=[]
for ds,c6,bp,ap,phase in s3:
    pl=px_code.get(c6,[]); 
    if not pl: continue
    dl=[p[0] for p in pl]; ix=bisect.bisect_left(dl,ds)
    if ix>=len(dl) or dl[ix]!=ds: continue
    c0=pl[ix][1]; c3=pl[ix+3][1] if ix+3<len(pl) else None; c5=pl[ix+5][1] if ix+5<len(pl) else None
    if c3 is None and c5 is None: continue
    y3=(c3/c0-1) if(c3 and c0)else None; y5=(c5/c0-1) if(c5 and c0)else None
    lag5=(c0/pl[ix-5][1]-1) if(ix>=5 and pl[ix-5][1]) else 0.0
    
    en=float((bp or 0)+(ap or 0))
    il=ins.get(c6,[])
    buy7=any(abs((dd-ds).days)<=7 and t=="INC" for dd,t in il)
    sell7=any(abs((dd-ds).days)<=7 and t=="DEC" for dd,t in il)
    ph=phase or "NEUTRAL"
    
    # s1_nearby: S3 触发前 5 天有无 S1
    s1_near = 0
    for k in range(-5, 0):
        dk = dl[ix+k] if 0<=ix+k<len(dl) else None
        if dk and (c6, dk) in s1_set: s1_near=1; break
    
    f={"l5r":lag5,"en":en,"ib":1 if buy7 else 0,"is_":1 if(sell7 and not buy7) else 0,
       "s1_near":s1_near,"y3":y3,"y5":y5}
    for p in PHASES: f["p_"+p]=1 if ph==p else 0
    recs.append(f)

df=pd.DataFrame(recs); print(f"有效: {len(df)} ({time.time()-t0:.0f}s)")

# 看 s1_nearby 统计
cnt_s1 = df["s1_near"].sum()
print(f"S3中s1_nearby=1: {cnt_s1} ({cnt_s1/len(df)*100:.1f}%)")

base=["l5r","en","ib","is_"]+["p_"+p for p in PHASES]

for yc,label in [("y3","T+3"),("y5","T+5")]:
    dfo=df.dropna(subset=[yc]); N=len(dfo)
    Xb=dfo[base].fillna(0).values
    Xf=dfo[base+["s1_near"]].fillna(0).values
    y=dfo[yc].values
    
    a_ext=np.logspace(-3,5,100)
    sp=int(N*0.67)
    Xbs=StandardScaler().fit_transform(Xb); Xfs=StandardScaler().fit_transform(Xf)
    m0=RidgeCV(alphas=a_ext,cv=5,scoring="r2").fit(Xbs[:sp],y[:sp])
    cv0=cross_val_score(RidgeCV(alphas=a_ext,cv=3),Xbs,y,cv=5,scoring="r2")
    m1=RidgeCV(alphas=a_ext,cv=5,scoring="r2").fit(Xfs[:sp],y[:sp])
    cv1=cross_val_score(RidgeCV(alphas=a_ext,cv=3),Xfs,y,cv=5,scoring="r2")
    
    s1_c = m1.coef_[len(base)]
    # 符号稳定
    stb=True
    for a in np.logspace(-1,1,20):
        rr=RidgeCV(alphas=[a],cv=3).fit(Xfs,y)
        if rr.coef_[len(base)]*s1_c < 0: stb=False; break
    
    print(f"\n{label} (N={N}):")
    print(f"  Baseline CV R2={cv0.mean():+.4f} +s1_near CV R2={cv1.mean():+.4f}")
    print(f"  s1_near系数={s1_c:.6f} 符号稳定={'✅' if stb else '❌'}")
    print(f"  R2变化={cv1.mean()-cv0.mean():+.4f}")

print(f"\n④ S1调制: {'✅ 通过' if any((df['y3'].notna().sum()>0) and ... ) else '结果见上方'}")
print(f"耗时: {time.time()-t0:.0f}s")
