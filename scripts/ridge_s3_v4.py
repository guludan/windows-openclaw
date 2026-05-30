"""ridge_s3_v4.py — S3 岭回归（直联版）"""
import psycopg2, numpy as np, warnings, json, bisect
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
print("Ridge S3 v4 — 直联版")
print("=" * 70)

# 全部用 SQL 一次性把特征算好，Python 只跑 Ridge
print("\n[1] 构建 SQL 特征视图...")

# 一次性查所有特征
sql = """
WITH s3 AS (
    SELECT DISTINCT pe.date, SUBSTRING(pe.level, 8) as code6
    FROM pool_energy pe
    WHERE pe.phase = 's3_panic' AND pe.date >= '2020-01-01'
),
prices AS (
    SELECT SUBSTRING(symbol, 3) as code6, trade_date, close, turnover,
           ROW_NUMBER() OVER (PARTITION BY SUBSTRING(symbol, 3) ORDER BY trade_date) as rn
    FROM daily_prices
    WHERE trade_date >= '2019-06-01' AND close > 0
),
px_with_rn AS (
    SELECT p.*
    FROM prices p
    JOIN s3 ON s3.code6 = p.code6
),
-- 所有 S3 信号的目标收益率
s3_features AS (
    SELECT 
        s3.date, s3.code6,
        p0.close as close_0,
        p3.close as close_3,
        p5.close as close_5,
        p5b.close as close_5b,
        p0.turnover as to_0,
        p20.turnover as to_20,
        -- phase
        mp.phase,
        mp.b_pool, mp.a_pool
    FROM s3
    JOIN px_with_rn p0 ON p0.code6 = s3.code6 AND p0.trade_date = s3.date
    LEFT JOIN px_with_rn p3 ON p3.code6 = s3.code6 AND p3.rn = p0.rn + 3
    LEFT JOIN px_with_rn p5 ON p5.code6 = s3.code6 AND p5.rn = p0.rn + 5
    LEFT JOIN px_with_rn p5b ON p5b.code6 = s3.code6 AND p5b.rn = p0.rn - 5
    LEFT JOIN px_with_rn p20 ON p20.code6 = s3.code6 AND p20.rn = p0.rn - 1
    LEFT JOIN pool_energy mp ON mp.level = 'market' AND mp.date = s3.date
)
SELECT * FROM s3_features ORDER BY date
"""
cur.execute(sql)
rows = cur.fetchall()
print(f"  特征行: {len(rows)}")

# 拉换手率历史（为前 20 日均值准备）—— 单独的查询
print("\n[2] 补充换手率历史...")
code_list = list(set(r[1] for r in rows))
print(f"  涉及股票: {len(code_list)}")

# 拉每只股票的全量换手率（2020 起，只取有信号的）
to_hist = defaultdict(list)
for i in range(0, len(code_list), 100):
    batch = code_list[i:i+100]
    codes = "','".join(batch)
    cur.execute(f"""
        SELECT SUBSTRING(symbol, 3) as code6, trade_date, turnover
        FROM daily_prices
        WHERE SUBSTRING(symbol, 3) IN ('{codes}')
          AND trade_date >= '2019-06-01' AND close > 0 AND turnover > 0
        ORDER BY code6, trade_date
    """)
    for r in cur.fetchall():
        to_hist[r[0]].append((r[1], r[2]))
    if (i // 100) % 5 == 0:
        print(f"  {i}/{len(code_list)}...")

# 增减持
cur.execute("""
    SELECT code, announce_date, change_type
    FROM incr_decr
    WHERE change_type IN ('INCREASE', 'DECREASE')
      AND announce_date >= '2020-01-01'
    ORDER BY code, announce_date
""")
ins = defaultdict(list)
for r in cur.fetchall():
    ins[r[0]].append((r[1], r[2]))

conn.close()

# 换手率索引
to_idx = {}
for code6, plist in to_hist.items():
    to_idx[code6] = {d: v for d, v in plist}

# 前一日的换手率（取换手率的近 20 日均值）
def get_to_ma(code6, d):
    dkey = code6
    h = to_hist.get(code6, [])
    if not h:
        return None, None
    vals = []
    for dd, vv in h:
        if dd < d:
            vals.append(vv)
        else:
            break
    if len(vals) >= 5:
        return np.mean(vals), np.std(vals)
    return None, None

print(f"\n[3] 构建特征矩阵...")
records = []
for r in rows:
    date_sig, code6, c0, c3, c5, c5b, to_0_, to_20_, phase, bp, ap = r
    
    y3 = (c3/c0 - 1) if (c3 and c0) else None
    y5 = (c5/c0 - 1) if (c5 and c0) else None
    if c5b and c0:
        lag5 = c0/c5b - 1
    else:
        lag5 = 0
    
    # turnover_z
    to_mean, to_std = get_to_ma(code6, date_sig)
    if to_mean and to_std and to_std > 0 and to_0_:
        to_z = (to_0_ - to_mean) / to_std
    else:
        to_z = 0
    
    f = {
        'lagged_5d_return': lag5,
        'turnover_z': to_z if isinstance(to_z, float) else float(to_z) if to_z else 0,
        'total_energy': float(bp or 0) + float(ap or 0),
    }
    phase = phase or 'NEUTRAL'
    for ph in ['B_ACCUM','B_DISTRIB','A_ACCUM','A_RECOVER','NEUTRAL','HI_EQ']:
        f[f'phase_{ph}'] = 1 if phase == ph else 0
    
    ins_list = ins.get(code6, [])
    buy7 = any(dd-timedelta(3)<=date_sig<=dd+timedelta(7) and t=='INCREASE' for dd,t in ins_list)
    sell7 = any(dd-timedelta(3)<=date_sig<=dd+timedelta(7) and t=='DECREASE' for dd,t in ins_list)
    f['insider_buy'] = 1 if buy7 else 0
    f['insider_sell'] = 1 if (sell7 and not buy7) else 0
    f['insider_none'] = 1 if not (buy7 or sell7) else 0
    
    f['y3'] = y3
    f['y5'] = y5
    records.append(f)

df = __import__('pandas').DataFrame(records)
print(f"  有效: {len(df)} 条")

feat_cols = ['lagged_5d_return','turnover_z','total_energy','insider_buy','insider_sell','insider_none']
phases_present = []
for ph in ['B_ACCUM','B_DISTRIB','A_ACCUM','A_RECOVER','NEUTRAL','HI_EQ']:
    if (df[f'phase_{ph}']==1).any():
        phases_present.append(ph)
feat_cols += [f'phase_{p}' for p in phases_present]

df_y3 = df.dropna(subset=['y3'])
df_y5 = df.dropna(subset=['y5'])
print(f"  T+3: {len(df_y3)}, T+5: {len(df_y5)}")

def run_ridge(df_in, ycol, label):
    N = len(df_in)
    if N < 50: return None
    X = df_in[feat_cols].fillna(0).values
    y = df_in[ycol].values
    Xs = StandardScaler().fit_transform(X)
    
    split = int(N * 0.67)
    X_tr, y_tr = Xs[:split], y[:split]
    X_te, y_te = Xs[split:], y[split:]
    
    alphas = np.logspace(-3, 3, 50)
    m = RidgeCV(alphas=alphas, cv=5, scoring='r2')
    m.fit(X_tr, y_tr)
    cv = cross_val_score(RidgeCV(alphas=alphas, cv=3), Xs, y, cv=5, scoring='r2')
    
    print(f"\n  {label} (N={N}):")
    print(f"    α={m.alpha_:.4f} train_R²={m.score(X_tr,y_tr):.4f} test_R²={m.score(X_te,y_te):.4f} CV_R²={cv.mean():.4f}±{cv.std():.4f}")
    print(f"    系数:")
    
    ap = np.logspace(-3, 3, 100)
    cp = ridge_regression(Xs, y, ap)
    stable = []
    for i, col in enumerate(feat_cols):
        c = m.coef_[i]
        print(f"      {col:25s}: {c:.6f}")
        seg = cp[i, (ap>=0.1)&(ap<=10)]
        if np.all(seg>0) or np.all(seg<0):
            stable.append(col)
    print(f"    符号稳定: {len(stable)}/{len(feat_cols)}")
    return {'alpha': m.alpha_, 'train_r2': m.score(X_tr,y_tr), 'test_r2': m.score(X_te,y_te),
            'cv_r2': cv.mean(), 'stable_cnt': len(stable), 'stable_feats': stable}

r3 = run_ridge(df_y3, 'y3', 'T+3')
r5 = run_ridge(df_y5, 'y5', 'T+5')

# 判定
print("\n" + "=" * 70)
print("判定")
print("=" * 70)
c3 = r3['cv_r2'] if r3 else -10; c5 = r5['cv_r2'] if r5 else -10
s3 = r3['stable_cnt'] if r3 else 0; s5 = r5['stable_cnt'] if r5 else 0
t3 = r3['test_r2'] if r3 else -10; t5 = r5['test_r2'] if r5 else -10
f1 = c3<0.02 and c5<0.02; f2 = s3==0 and s5==0; f3 = t3<=0 and t5<=0

print(f"条件1: CV R² < 0.02? T+3={c3:.4f} T+5={c5:.4f} → {'⚠️失败' if f1 else '✅通过'}")
print(f"条件2: 无符号稳定? T+3={s3} T+5={s5} → {'⚠️失败' if f2 else '✅通过'}")
print(f"条件3: 测试 R² ≤ 0? T+3={t3:.4f} T+5={t5:.4f} → {'⚠️失败' if f3 else '✅通过'}")
if f1 and f2 and f3:
    print("\n➡ V7 = 纯风险预算 + 行为筛选")
else:
    print("\n✅ V7 纳入岭回归评分模块")

json.dump({'t3':r3,'t5':r5,'verdict':'fail' if (f1&f2&f3) else 'pass'}, 
          open('/tmp/ridge_s3_result.json','w'))
print(f"\n✅ 完成")
