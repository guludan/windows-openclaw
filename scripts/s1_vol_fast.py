"""s1_vol_fast.py — S1 波动率预测（SQL JOIN 直拉）"""
import numpy as np, pandas as pd, warnings, time, psycopg2
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score
warnings.filterwarnings("ignore")
t0 = time.time()

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
print("S1 波动率预测 — SQL 直拉版", flush=True)

conn = psycopg2.connect(DB); cur = conn.cursor()
# 直接用 SQL 算特征和波动率目标
cur.execute("""
    WITH s1 AS (
        SELECT DISTINCT SUBSTRING(level,8) as code6, date as sig_date
        FROM pool_energy WHERE phase='s1_surge' AND date>='2022-01-01'
    ),
    px AS (
        SELECT SUBSTRING(symbol,3) as code6, trade_date, close, turnover,
               ROW_NUMBER() OVER (PARTITION BY SUBSTRING(symbol,3) ORDER BY trade_date) as rn
        FROM daily_prices
        WHERE trade_date>='2021-06-01' AND close>0 AND trade_date<='2026-05-27'
    ),
    mp AS (
        SELECT date, phase, b_pool, a_pool FROM pool_energy WHERE level='market'
    ),
    raw AS (
        SELECT s1.sig_date, s1.code6,
               p0.close as c0, p_1.close as c_1, p_3.close as c_3, p_5.close as c_5,
               p0.turnover as to0,
               p1.close as c1, p2.close as c2, p3.close as c3, p4.close as c4, p5.close as c5,
               mp.phase as mkt_phase, mp.b_pool, mp.a_pool
        FROM s1
        JOIN px p0 ON p0.code6=s1.code6 AND p0.trade_date=s1.sig_date
        LEFT JOIN px p_1 ON p_1.code6=s1.code6 AND p_1.rn=p0.rn-1
        LEFT JOIN px p_3 ON p_3.code6=s1.code6 AND p_3.rn=p0.rn-3
        LEFT JOIN px p_5 ON p_5.code6=s1.code6 AND p_5.rn=p0.rn-5
        LEFT JOIN px p1 ON p1.code6=s1.code6 AND p1.rn=p0.rn+1
        LEFT JOIN px p2 ON p2.code6=s1.code6 AND p2.rn=p0.rn+2
        LEFT JOIN px p3 ON p3.code6=s1.code6 AND p3.rn=p0.rn+3
        LEFT JOIN px p4 ON p4.code6=s1.code6 AND p4.rn=p0.rn+4
        LEFT JOIN px p5 ON p5.code6=s1.code6 AND p5.rn=p0.rn+5
        LEFT JOIN mp ON mp.date=s1.sig_date
    )
    SELECT * FROM raw
""")
rows = cur.fetchall()
colnames = [d[0] for d in cur.description]
print(f"SQL 直拉: {len(rows)} 行 ({time.time()-t0:.0f}s)", flush=True)

# 增减持
cur.execute("SELECT code,change_date,is_increase,is_decrease FROM incr_decr WHERE change_date>='2022-01-01'")
ins = {}
for r in cur.fetchall():
    key = (r[0], r[1]); t = None
    if r[2]: t=1
    elif r[3]: t=0
    if t is not None: ins[key]=t

# 换手率历史（用另一 SQL 算）
# 取 S1 股票的日均换手率
cur.execute("""
    SELECT SUBSTRING(symbol,3) as code6,
           AVG(turnover) as avg_to, STDDEV(turnover) as std_to
    FROM daily_prices WHERE trade_date>='2022-01-01' AND close>0 AND turnover>0
    GROUP BY 1
""")
to_stats = {r[0]: (r[1], r[2] or 1) for r in cur.fetchall()}
conn.close()

# 构造特征矩阵
recs = []
for r in rows:
    d = dict(zip(colnames, r))
    c0=d["c0"]; c_1=d["c_1"]; c_3=d["c_3"]; c_5=d["c_5"]
    to0=d["to0"]; bph=d["mkt_phase"] or "NEUTRAL"; bp=d["b_pool"] or 0; ap=d["a_pool"] or 0
    
    # 收益率
    r5d = [d[f"c{k}"] for k in [1,2,3,4,5]]
    r5d = [x/c0-1 for x in r5d if x and c0]
    if len(r5d) < 3: continue
    rv5 = np.std(r5d) * np.sqrt(252)
    
    lag5 = (c0/c_5-1) if(c_5 and c0) else 0.0
    tstat = to_stats.get(d["code6"], (None,None))
    tz = 0.0
    if to0 and tstat[0] and tstat[1] and tstat[1]>0:
        tz = (to0-tstat[0])/tstat[1]
    
    s = d["sig_date"]
    il = ins.get((d["code6"], s), None)
    ins_buy=1 if il==1 else 0; ins_sell=1 if il==0 else 0
    
    f = {"l5r":lag5, "tz":tz, "en":float(bp+ap),
         "ib":ins_buy, "is_":ins_sell, "rv5":rv5}
    for p in ["B_ACCUM","B_DISTRIB","A_ACCUM","A_RECOVER","NEUTRAL","HI_EQ"]:
        f["p_"+p] = 1 if bph==p else 0
    recs.append(f)

df = pd.DataFrame(recs); print(f"有效: {len(df)} ({time.time()-t0:.0f}s)", flush=True)

COLS = ["l5r","tz","en","ib","is_"]
X=df[COLS].fillna(0).values; y=df["rv5"].values
Xs=StandardScaler().fit_transform(X)
sp=int(len(df)*0.67)
a_ext=np.logspace(-3,5,100)
m=RidgeCV(alphas=a_ext,cv=5,scoring="r2").fit(Xs[:sp],y[:sp])
cv=cross_val_score(RidgeCV(alphas=a_ext,cv=3),Xs,y,cv=5,scoring="r2")
print(f"\nS1 波动率预测 (N={len(df)}):")
print(f"  alpha={m.alpha_:.2f}")
print(f"  训练R2={m.score(Xs[:sp],y[:sp]):.4f} 测试R2={m.score(Xs[sp:],y[sp:]):.4f}")
print(f"  CV R2={cv.mean():+.4f}+-{cv.std():.4f}")
print(f"  RV5分布: P50={np.median(y):.4f} P90={np.percentile(y,90):.4f}")
for i,c in enumerate(COLS): print(f"    {c:>20s}: {m.coef_[i]:.6f}")
print(f"\n{'✅' if cv.mean()>=0.02 else '❌'} 方向一: S1{'可' if cv.mean()>=0.02 else '不可'}预测波动率")
print(f"耗时: {time.time()-t0:.0f}s")
