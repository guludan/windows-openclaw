"""s1_vol_prediction.py — 方向一：S1 预测 T+5 已实现波动率"""
import numpy as np, pandas as pd, warnings, time, psycopg2, bisect
from collections import defaultdict
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score
warnings.filterwarnings("ignore")
t0 = time.time()

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
PHASES = ["B_ACCUM","B_DISTRIB","A_ACCUM","A_RECOVER","NEUTRAL","HI_EQ"]
COLS = ["l5r","tz","en","ib","is_"]

conn = psycopg2.connect(DB); cur = conn.cursor()
cur.execute("SELECT DISTINCT SUBSTRING(level,8) FROM pool_energy WHERE phase='s1_surge' AND date>='2020-01-01'")
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

cur.execute("""SELECT pe.date,SUBSTRING(pe.level,8),mp.phase FROM pool_energy pe 
    LEFT JOIN pool_energy mp ON mp.level='market' AND mp.date=pe.date
    WHERE pe.phase='s1_surge' AND pe.date>='2020-01-01' ORDER BY pe.date""")
sigs=cur.fetchall(); conn.close()

px_code=defaultdict(list)
for r in all_px: px_code[r[0]].append((r[1],r[2],r[3]))

# 构建特征 + 目标变量 = T+5 已实现波动率
recs=[]
for ds,c6,mkt_ph in sigs:
    pl=px_code.get(c6,[]); 
    if not pl: continue
    dl=[p[0] for p in pl]; ix=bisect.bisect_left(dl,ds)
    if ix>=len(dl) or dl[ix]!=ds: continue
    
    # 特征（同 S1）
    c0=pl[ix][1]
    lag5=(c0/pl[ix-5][1]-1) if(ix>=5 and pl[ix-5][1]) else 0.0
    to0=pl[ix][2]; 
    tos=[pl[ix-k][2] for k in range(1,21) if ix-k>=0 and pl[ix-k][2] and pl[ix-k][2]>0]
    tz=0.0
    if to0 and len(tos)>=5: mu,sd=np.mean(tos),np.std(tos); tz=(to0-mu)/sd if sd>0 else 0.0
    m=mk.get(ds,{"ph":"NEUTRAL","b":0,"a":0})
    il=ins.get(c6,[])
    buy7=any(abs((dd-ds).days)<=7 and t=="INC" for dd,t in il)
    sell7=any(abs((dd-ds).days)<=7 and t=="DEC" for dd,t in il)
    en=float(m["b"]+m["a"])
    
    # 目标：T+1 ~ T+5 的日收益率标准差（年化）
    ret5=[]
    for k in range(1,7):
        pk=pl[ix+k][1] if ix+k<len(pl) else None
        if pk and c0: ret5.append(pk/c0-1)
    if len(ret5)<3: continue
    rv5=np.std(ret5) * np.sqrt(252)  # 年化
    
    f={"l5r":lag5,"tz":tz,"en":en,"ib":1 if buy7 else 0,"is_":1 if(sell7 and not buy7) else 0,
       "rv5":rv5}
    recs.append(f)

df=pd.DataFrame(recs); print(f"有效: {len(df)}")

# 岭回归
X=df[COLS].fillna(0).values; y=df["rv5"].values
Xs=StandardScaler().fit_transform(X)
sp=int(len(df)*0.67); X_tr,y_tr,X_te,y_te = Xs[:sp],y[:sp],Xs[sp:],y[sp:]
a_ext=np.logspace(-3,5,100)
m=RidgeCV(alphas=a_ext,cv=5,scoring="r2").fit(Xs,y)

# 注意：RidgeCV 的 scoring 不返回最好的 CV R²，手动算
cv=cross_val_score(RidgeCV(alphas=a_ext,cv=3),Xs,y,cv=5,scoring="r2")

print(f"\nS1 波动率预测 (N={len(df)}):")
print(f"  alpha={m.alpha_:.2f}")
print(f"  训练R2={m.score(X_tr,y_tr):.4f} 测试R2={m.score(X_te,y_te):.4f}")
print(f"  CV R2={cv.mean():+.4f}+-{cv.std():.4f}")
print(f"  年化波动率: P50={np.median(y):.4f} P90={np.percentile(y,90):.4f}")
print(f"  系数:")
for i,c in enumerate(COLS): print(f"    {c:>20s}: {m.coef_[i]:.6f}")

# 判定
if cv.mean() >= 0.02:
    print(f"\n✅ 方向一通过! S1 可预测波动率 (CV R2={cv.mean():.4f}) → 进风控模块")
else:
    print(f"\n❌ 方向一未通过 (CV R2={cv.mean():.4f}) → 方向一关闭")

print(f"\n耗时: {time.time()-t0:.0f}s")
