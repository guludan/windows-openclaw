"""s1_threshold_scan_v2.py — 多阈值 S1 岭回归扫描"""
import numpy as np, pandas as pd, warnings, time, psycopg2, bisect
from collections import defaultdict
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score
warnings.filterwarnings("ignore")
t0 = time.time()

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

# ── 加载日线（S1 全部股票） ──
conn = psycopg2.connect(DB); cur = conn.cursor()
cur.execute("SELECT DISTINCT SUBSTRING(level,8) FROM pool_energy WHERE phase='s1_surge' AND date>='2020-01-01'")
codes = [r[0] for r in cur.fetchall()]; print(f"股票: {len(codes)}", flush=True)

B=200; all_px=[]
for i in range(0,len(codes),B):
    cset="','".join(codes[i:i+B])
    cur.execute(f"""SELECT SUBSTRING(symbol,3),trade_date,close,turnover FROM daily_prices 
        WHERE SUBSTRING(symbol,3) IN ('{cset}') AND trade_date>='2019-06-01' AND close>0 ORDER BY 1,2""")
    all_px.extend(cur.fetchall())
print(f"日线: {len(all_px)}", flush=True)

# 辅助数据
cur.execute("SELECT date,phase,b_pool,a_pool FROM pool_energy WHERE level='market' AND date>='2020-01-01'")
mk={}
for r in cur.fetchall():
    p=r[1] or "NEUTRAL"; mk[r[0]]={"ph":p,"b":r[2] or 0,"a":r[3] or 0}

cur.execute("SELECT code,change_date,is_increase,is_decrease FROM incr_decr WHERE change_date>='2020-01-01'")
ins=defaultdict(list)
for r in cur.fetchall():
    if r[2]: ins[r[0]].append((r[1],"INC"))
    elif r[3]: ins[r[0]].append((r[1],"DEC"))

# S1 全量 + vol_z
cur.execute("""SELECT pe.date,SUBSTRING(pe.level,8),(pe.extra->>'vol_z')::numeric,mp.phase 
    FROM pool_energy pe LEFT JOIN pool_energy mp ON mp.level='market' AND mp.date=pe.date
    WHERE pe.phase='s1_surge' AND pe.date>='2020-01-01' ORDER BY pe.date""")
s1_all=cur.fetchall(); conn.close()

px_code=defaultdict(list)
for r in all_px: px_code[r[0]].append((r[1],r[2],r[3]))
cols=["l5r","tz","en","ib","is_"]

def run(vz_thr):
    """对 vol_z >= thr 的 S1 子样本跑岭回归"""
    recs=[]
    for ds,c6,vol_act,mkt_ph in s1_all:
        if vol_act is None or float(vol_act) < vz_thr: continue
        pl=px_code.get(c6,[]); 
        if not pl: continue
        dl=[p[0] for p in pl]; ix=bisect.bisect_left(dl,ds)
        if ix>=len(dl) or dl[ix]!=ds: continue
        c0=pl[ix][1]; c3=pl[ix+3][1] if ix+3<len(pl) else None; c5=pl[ix+5][1] if ix+5<len(pl) else None
        if c3 is None and c5 is None: continue
        y3=(c3/c0-1) if(c3 and c0) else None; y5=(c5/c0-1) if(c5 and c0) else None
        lag5=(c0/pl[ix-5][1]-1) if(ix>=5 and pl[ix-5][1]) else 0.0
        to0=pl[ix][2]; tos=[pl[ix-k][2] for k in range(1,21) if ix-k>=0 and pl[ix-k][2] and pl[ix-k][2]>0]
        tz=0.0; 
        if to0 and len(tos)>=5: mu,sd=np.mean(tos),np.std(tos); tz=(to0-mu)/sd if sd>0 else 0.0
        m=mk.get(ds,{"ph":"NEUTRAL","b":0,"a":0})
        il=ins.get(c6,[])
        buy7=any(abs((dd-ds).days)<=7 and t=="INC" for dd,t in il)
        sell7=any(abs((dd-ds).days)<=7 and t=="DEC" for dd,t in il)
        en=float(m["b"]+m["a"])
        f={"l5r":lag5,"tz":tz,"en":en,"ib":1 if buy7 else 0,"is_":1 if(sell7 and not buy7) else 0,
           "y3":y3,"y5":y5,"phase_mkt":m["ph"]}
        recs.append(f)
    
    df=pd.DataFrame(recs); N=len(df)
    results={"thr":vz_thr,"N":N}
    for yc,lb in [("y3","T+3"),("y5","T+5")]:
        dfo=df.dropna(subset=[yc]); n=len(dfo)
        if n<50: results[lb]={"n":n,"cv_r2":None,"test_r2":None,"alpha":None}; continue
        X=dfo[cols].fillna(0).values; y=dfo[yc].values
        Xs=StandardScaler().fit_transform(X)
        sp=int(n*0.67); X_tr,y_tr,X_te,y_te = Xs[:sp],y[:sp],Xs[sp:],y[sp:]
        a_ext=np.logspace(-3,5,100)
        m=RidgeCV(alphas=a_ext,cv=5,scoring="r2").fit(Xs,y)
        cv=cross_val_score(RidgeCV(alphas=a_ext,cv=3),Xs,y,cv=5,scoring="r2")
        results[lb]={"n":n,"cv_r2":float(cv.mean()),"test_r2":float(m.score(X_te,y_te)),"alpha":float(m.alpha_)}
    return results, recs

# ── 阈值扫描（含 方向一：cum_return 约束） ──
# 对 vol_z>=3.0 的全量样本再计算 cum_return[-3:]
# 需要额外拉 3 天前的价格
print("\n扫描 vol_z 阈值 + 方向一致性...", flush=True)
thrs = [3.0, 3.5, 4.0, 4.5, 5.0, 6.0]
print(f"{'阈值':>6s} {'N全量':>7s} {'T+3_CV':>8s} {'T+3_测试':>8s} {'T+5_CV':>8s} {'T+5_测试':>8s} {'状态'}", flush=True)

# 也跑方向一：cum_return[-3:] > 3% + 方向一致
# 先跑全量阈值扫描（不含方向约束）
for thr in thrs:
    res,_ = run(thr)
    if res["T+3"]["cv_r2"] is None:
        print(f"{thr:>6.1f} {res['N']:>7d} {'-':>8s} {'-':>8s} {'-':>8s} {'-':>8s} {'跳过'}", flush=True)
        continue
    t3_cv=res["T+3"]["cv_r2"]; t3_test=res["T+3"]["test_r2"]
    t5_cv=res["T+5"]["cv_r2"]; t5_test=res["T+5"]["test_r2"]
    passed = t3_cv>=0.02 or t5_cv>=0.02
    print(f"{thr:>6.1f} {res['N']:>7d} {t3_cv:>+.4f} {t3_test:>+.4f} {t5_cv:>+.4f} {t5_test:>+.4f} {'✅' if passed else '❌'}", flush=True)

# ── 方向一：cum_return[-3:] > 3% + 方向一致 ──
print(f"\n方向一: cum_return[-3:] > 3% + sign一致", flush=True)
# 需要重做：改 feature 提取加入前 3 天收益
res_cr, recs_cr = run(3.0)
df_cr = pd.DataFrame(recs_cr)

# 加 cum_return[-3:] 需要回 DB 或从 px_code 算
# px_code 里有日线，可以算
print(f"  计算前3天收益...", flush=True)
cr_list=[]
for ds,c6 in [(r["date"] if "date" in r else None, r.get("code6")) for r in recs_cr]:
    pass  # 重新遍历

# 重新从 s1_all 遍历算 cr
recs_cr2=[]
for ds,c6,vol_act,mkt_ph in s1_all:
    if vol_act is None or float(vol_act) < 3.0: continue
    pl=px_code.get(c6,[])
    if not pl: continue
    dl=[p[0] for p in pl]; ix=bisect.bisect_left(dl,ds)
    if ix>=len(dl) or dl[ix]!=ds: continue
    
    # 前 3 天 cum_return
    c_3 = pl[ix-3][1] if ix>=3 else None
    c0 = pl[ix][1]
    cr3 = (c0/c_3-1) if(c_3 and c0) else None
    cr1d = (c0/pl[ix-1][1]-1) if(ix>=1 and pl[ix-1][1]) else None
    
    if cr3 is None or abs(cr3) < 0.03: continue
    if cr1d and (cr3*cr1d < 0): continue  # 方向翻转
    
    # 同之前特征提取
    c3=pl[ix+3][1] if ix+3<len(pl) else None; c5=pl[ix+5][1] if ix+5<len(pl) else None
    if c3 is None and c5 is None: continue
    y3=(c3/c0-1) if(c3 and c0) else None; y5=(c5/c0-1) if(c5 and c0) else None
    lag5=(c0/pl[ix-5][1]-1) if(ix>=5 and pl[ix-5][1]) else 0.0
    to0=pl[ix][2]; tos=[pl[ix-k][2] for k in range(1,21) if ix-k>=0 and pl[ix-k][2] and pl[ix-k][2]>0]
    tz=0.0
    if to0 and len(tos)>=5: mu,sd=np.mean(tos),np.std(tos); tz=(to0-mu)/sd if sd>0 else 0.0
    m=mk.get(ds,{"ph":"NEUTRAL","b":0,"a":0})
    il=ins.get(c6,[])
    buy7=any(abs((dd-ds).days)<=7 and t=="INC" for dd,t in il)
    sell7=any(abs((dd-ds).days)<=7 and t=="DEC" for dd,t in il)
    en=float(m["b"]+m["a"])
    f={"l5r":lag5,"tz":tz,"en":en,"ib":1 if buy7 else 0,"is_":1 if(sell7 and not buy7) else 0,
       "y3":y3,"y5":y5}
    recs_cr2.append(f)

df2=pd.DataFrame(recs_cr2); print(f"  符合方向条件: {len(df2)}", flush=True)
for yc,lb in [("y3","T+3"),("y5","T+5")]:
    dfo=df2.dropna(subset=[yc]); n=len(dfo)
    if n<50: print(f"  {lb}: 样本不足 {n}", flush=True); continue
    X=dfo[cols].fillna(0).values; y=dfo[yc].values
    Xs=StandardScaler().fit_transform(X)
    sp=int(n*0.67); X_tr,y_tr,X_te,y_te = Xs[:sp],y[:sp],Xs[sp:],y[sp:]
    a_ext=np.logspace(-3,5,100)
    m=RidgeCV(alphas=a_ext,cv=5,scoring="r2").fit(Xs,y)
    cv=cross_val_score(RidgeCV(alphas=a_ext,cv=3),Xs,y,cv=5,scoring="r2")
    print(f"  {lb} (N={n}): alpha={m.alpha_:.2f} CV R2={cv.mean():+.4f} 测试={m.score(X_te,y_te):+.4f}", flush=True)

print(f"\n总耗时: {time.time()-t0:.0f}s", flush=True)
