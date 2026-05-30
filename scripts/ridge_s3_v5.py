"""ridge_s3_v5.py — 超轻量版"""
import psycopg2, numpy as np, pandas as pd, warnings, json, bisect
from datetime import date, timedelta
from collections import defaultdict
from sklearn.linear_model import RidgeCV, ridge_regression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score
warnings.filterwarnings('ignore')

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
conn = psycopg2.connect(DB)
cur = conn.cursor()

print("=" * 70)
print("Ridge S3 v5 — 超轻量")
print("=" * 70)

# 1. 只取 S3 股票的日线（全量，但只取需要的列）
print("\n[1] 拉 S3 股票日线...")
cur.execute("""
    SELECT DISTINCT SUBSTRING(pe.level, 8) as code6
    FROM pool_energy pe WHERE pe.phase = 's3_panic' AND pe.date >= '2020-01-01'
""")
codes = [r[0] for r in cur.fetchall()]
print(f"  {len(codes)} 只股票")

# 每条分 200 只，直接拉全量日线
px_all = []
B = 200
for i in range(0, len(codes), B):
    cset = "','".join(codes[i:i+B])
    cur.execute(f"""
        SELECT SUBSTRING(symbol, 3) as code6, trade_date, close, turnover
        FROM daily_prices
        WHERE SUBSTRING(symbol, 3) IN ('{cset}')
          AND trade_date >= '2019-06-01' AND close > 0
        ORDER BY code6, trade_date
    """)
    px_all.extend(cur.fetchall())

px_by_code = defaultdict(list)
for r in px_all:
    px_by_code[r[0]].append((r[1], r[2], r[3]))
print(f"  日线: {len(px_all)} 行")

# 2. S3 信号
cur.execute("""
    SELECT pe.date, SUBSTRING(pe.level, 8) as code6
    FROM pool_energy pe WHERE pe.phase = 's3_panic' AND pe.date >= '2020-01-01' ORDER BY pe.date
""")
s3_list = cur.fetchall()
print(f"  S3: {len(s3_list)} 条")

# 3. 大盘数据
cur.execute("""
    SELECT date, phase, b_pool, a_pool
    FROM pool_energy WHERE level = 'market' AND date >= '2020-01-01'
""")
mkt_d = {r[0]: {'phase': r[1] or 'NEUTRAL', 'b': r[2] or 0, 'a': r[3] or 0} for r in cur.fetchall()}
print(f"  大盘: {len(mkt_d)} 天")

# 4. 增减持
cur.execute("""
    SELECT code, announce_date, change_type
    FROM incr_decr WHERE change_type IN ('INCREASE','DECREASE') AND announce_date >= '2020-01-01'
""")
ins = defaultdict(list)
for r in cur.fetchall():
    ins[r[0]].append((r[1], r[2]))
print(f"  增减持: {sum(len(v) for v in ins.values())} 条")

conn.close()

# 5. 算特征
print("\n[2] 构建特征...")
def get_idx(plist, d):
    dates = [p[0] for p in plist]
    i = bisect.bisect_left(dates, d)
    return i if (i < len(dates) and dates[i] == d) else None

def v(plist, idx):
    return plist[idx][1] if (idx is not None and 0 <= idx < len(plist)) else None

def t(plist, idx):
    return plist[idx][2] if (idx is not None and 0 <= idx < len(plist)) else None

records = []
for date_sig, code6 in s3_list:
    pl = px_by_code.get(code6, [])
    if not pl: continue
    ix = get_idx(pl, date_sig)
    if ix is None: continue
    
    c0 = v(pl, ix)
    c3 = v(pl, ix+3)
    c5 = v(pl, ix+5)
    c5b = v(pl, ix-5)
    
    y3 = (c3/c0 - 1) if (c3 and c0) else None
    y5 = (c5/c0 - 1) if (c5 and c0) else None
    if not y3 and not y5: continue
    
    lag5 = (c0/c5b - 1) if (c5b and c0) else 0.0
    
    # 换手率 Z
    to0 = t(pl, ix)
    to_hist = [t(pl, ix-k) for k in range(1, 21)]
    to_hist = [v for v in to_hist if v and v > 0]
    if to0 and len(to_hist) >= 5:
        mu, sd = np.mean(to_hist), np.std(to_hist)
        to_z = (to0 - mu) / sd if sd > 0 else 0.0
    else:
        to_z = 0.0
    
    m = mkt_d.get(date_sig, {'phase': 'NEUTRAL', 'b': 0, 'a': 0})
    phase = m['phase']
    energy = float(m['b'] + m['a'])
    
    i_list = ins.get(code6, [])
    buy7 = any(abs((dd-date_sig).days)<=7 and t=='INCREASE' for dd,t in i_list)
    sell7 = any(abs((dd-date_sig).days)<=7 and t=='DECREASE' for dd,t in i_list)
    
    f = {'lagged_5d_return': lag5, 'turnover_z': to_z, 'total_energy': energy,
         'insider_buy': 1 if buy7 else 0, 'insider_sell': 1 if (sell7 and not buy7) else 0,
         'y3': y3, 'y5': y5}
    for ph in ['B_ACCUM','B_DISTRIB','A_ACCUM','A_RECOVER','NEUTRAL','HI_EQ']:
        f[f'p_{ph}'] = 1 if phase == ph else 0
    records.append(f)

print(f"  有效: {len(records)} 条")

# 6. Ridge
df = pd.DataFrame(records)
cols = ['lagged_5d_return','turnover_z','total_energy','insider_buy','insider_sell']
phases = [c for c in df.columns if c.startswith('p_')]
cols += phases
print(f"  特征: {cols}")

for ycol, label in [('y3','T+3'), ('y5','T+5')]:
    dfo = df.dropna(subset=[ycol])
    N = len(dfo)
    if N < 50: continue
    X = dfo[cols].fillna(0).values
    y = dfo[ycol].values
    Xs = StandardScaler().fit_transform(X)
    split = int(N*0.67)
    X_tr, y_tr, X_te, y_te = Xs[:split], y[:split], Xs[split:], y[split:]
    alphas = np.logspace(-3, 3, 50)
    m = RidgeCV(alphas=alphas, cv=5, scoring='r2')
    m.fit(X_tr, y_tr)
    cv = cross_val_score(RidgeCV(alphas=alphas, cv=3), Xs, y, cv=5, scoring='r2')
    
    print(f"\n  {label} (N={N}): α={m.alpha_:.4f}")
    print(f"  训练R²={m.score(X_tr,y_tr):.4f} 测试R²={m.score(X_te,y_te):.4f}")
    print(f"  CV R²={cv.mean():.4f}±{cv.std():.4f}")
    
    ap = np.logspace(-3, 3, 100)
    cp = ridge_regression(Xs, y, ap)
    stable = []
    for i, col in enumerate(cols):
        c = m.coef_[i]
        print(f"    {col:25s}: {c:.6f}")
        seg = cp[i, (ap>=0.1)&(ap<=10)]
        if np.all(seg>0) or np.all(seg<0):
            stable.append(col)
    print(f"  符号稳定: {len(stable)}/{len(cols)}")

print("\n✅ 结果见上方")
