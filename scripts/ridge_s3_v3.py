"""
ridge_s3_v3.py — S3信号的岭回归验证（优化版）
分批拉取避免全表排序
"""
import psycopg2
import numpy as np
import pandas as pd
from datetime import date, timedelta
from sklearn.linear_model import RidgeCV, ridge_regression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score
import json, sys, bisect, warnings
from collections import defaultdict
warnings.filterwarnings('ignore')

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
conn = psycopg2.connect(DB)
cur = conn.cursor()

print("=" * 70)
print("Ridge S3 v3 — 岭回归验证（优化版）")
print("=" * 70)

# ── 1. 只拉涉及的股票 ──
print("\n[1] 确定 S3 涉及的股票...")
cur.execute("""
    SELECT DISTINCT SUBSTRING(pe.level, 8) as code6
    FROM pool_energy pe
    WHERE pe.phase = 's3_panic'
      AND pe.date >= '2020-01-01'
""")
s3_codes = [r[0] for r in cur.fetchall()]
print(f"  S3 涉及的股票: {len(s3_codes)} 只")

# ── 2. 分批拉日线（按股票，走索引） ──
print("\n[2] 分批拉取日线数据...")
px_by_code = {}
total = 0
batch_size = 200
for i in range(0, len(s3_codes), batch_size):
    batch = s3_codes[i:i+batch_size]
    codes_str = "','".join(batch)
    cur.execute(f"""
        SELECT SUBSTRING(symbol, 3) as code6, trade_date, close, turnover
        FROM daily_prices
        WHERE SUBSTRING(symbol, 3) IN ('{codes_str}')
          AND trade_date >= '2019-06-01' AND close > 0
        ORDER BY code6, trade_date
    """)
    rows = cur.fetchall()
    for r in rows:
        code6 = r[0]
        if code6 not in px_by_code:
            px_by_code[code6] = []
        px_by_code[code6].append((r[1], r[2], r[3]))
    total += len(rows)
    if (i // batch_size) % 5 == 0:
        print(f"  {i}/{len(s3_codes)} 股票, {total} 行...")
print(f"  日线总计: {total} 行, {len(px_by_code)} 只股票")

# ── 3. 拉 S3 信号数据 ──
print("\n[3] 拉取 S3 信号...")
cur.execute("""
    SELECT pe.date, SUBSTRING(pe.level, 8) as code6
    FROM pool_energy pe
    WHERE pe.phase = 's3_panic'
      AND pe.date >= '2020-01-01'
    ORDER BY pe.date
""")
s3_signals = cur.fetchall()
print(f"  S3 信号: {len(s3_signals)} 条")

# ── 4. 拉所有辅助数据 ──
print("\n[4] 拉取辅助数据...")

# 大盘 market phase
cur.execute("""
    SELECT date, phase, b_pool, a_pool, b_inject, a_inject
    FROM pool_energy
    WHERE level = 'market' AND date >= '2020-01-01'
    ORDER BY date
""")
mkt_rows = cur.fetchall()
mkt_phase = {r[0]: r[1] for r in mkt_rows}
mkt_energy = {r[0]: {'b_pool': r[2] or 0, 'a_pool': r[3] or 0} for r in mkt_rows}

# 增减持
cur.execute("""
    SELECT code, announce_date, change_type, change_num
    FROM incr_decr
    WHERE change_type IN ('INCREASE', 'DECREASE')
      AND announce_date >= '2020-01-01'
    ORDER BY code, announce_date
""")
insider_by_code = defaultdict(list)
for r in cur.fetchall():
    insider_by_code[r[0]].append((r[1], r[2], r[3] or 0))

print(f"  增减持: {sum(len(v) for v in insider_by_code.values())} 条")

# 龙虎榜
cur.execute("""
    SELECT SUBSTRING(code, 6) as code6, trade_date,
           (extra::json->>'inst_sell_net')::numeric as inst_net
    FROM market_signals
    WHERE signal_type = 'lhb_daily'
""")
lhb_by_code = defaultdict(list)
for r in cur.fetchall():
    lhb_by_code[r[0]].append((r[1], r[2]))
print(f"  龙虎榜: {sum(len(v) for v in lhb_by_code.values())} 条")

conn.close()

# ── 5. 计算特征 ──
print("\n[5] 计算特征矩阵...")

def get_px_idx(plist, d):
    dates = [p[0] for p in plist]
    i = bisect.bisect_left(dates, d)
    if i < len(dates) and dates[i] == d:
        return i
    return None

def get_val(plist, idx):
    if idx is not None and 0 <= idx < len(plist):
        return plist[idx][1]
    return None

def get_to(plist, idx):
    if idx is not None and 0 <= idx < len(plist):
        return plist[idx][2]
    return None

records = []
for date_sig, code6 in s3_signals:
    plist = px_by_code.get(code6, [])
    if not plist:
        continue
    
    idx = get_px_idx(plist, date_sig)
    if idx is None:
        continue
    
    f = {}
    # Target
    px_d = get_val(plist, idx)
    px_3 = get_val(plist, idx+3)
    px_5 = get_val(plist, idx+5)
    f['y3'] = (px_3/px_d - 1) if (px_3 and px_d) else None
    f['y5'] = (px_5/px_d - 1) if (px_5 and px_d) else None
    
    # lagged_5d_return
    px_5b = get_val(plist, idx-5)
    f['lagged_5d_return'] = (px_d/px_5b - 1) if (px_5b and px_d) else 0
    
    # turnover_z
    if idx >= 20:
        tos = [get_to(plist, i) for i in range(idx-20, idx)]
        tos = [v for v in tos if v and v > 0]
        if len(tos) >= 5:
            mu, std = np.mean(tos), np.std(tos)
            cur_to = get_to(plist, idx)
            f['turnover_z'] = (cur_to - mu)/std if (cur_to and std > 0) else 0
        else:
            f['turnover_z'] = 0
    else:
        f['turnover_z'] = 0
    
    # Phase
    phase = mkt_phase.get(date_sig, 'NEUTRAL')
    phase = 'NEUTRAL' if 'NUETRAL' else phase  # typo防
    for ph in ['B_ACCUM', 'B_DISTRIB', 'A_ACCUM', 'A_RECOVER', 'NEUTRAL', 'HI_EQ']:
        f[f'phase_{ph}'] = 1 if phase == ph else 0
    
    # insiders
    ins = insider_by_code.get(code6, [])
    buy7 = any(i[0]-timedelta(3) <= date_sig <= i[0]+timedelta(7) and i[1]=='INCREASE' for i in ins)
    sell7 = any(i[0]-timedelta(3) <= date_sig <= i[0]+timedelta(7) and i[1]=='DECREASE' for i in ins)
    f['insider_buy'] = 1 if buy7 else 0
    f['insider_neutral'] = 0 if (buy7 or sell7) else 1  # 中性
    
    # Energy
    e = mkt_energy.get(date_sig, {})
    f['total_energy'] = e.get('b_pool', 0) + e.get('a_pool', 0)
    
    records.append(f)

print(f"  有效样本: {len(records)}")

# ── 6. DataFrame ──
df = pd.DataFrame(records)
df_y3 = df.dropna(subset=['y3']).copy()
df_y5 = df.dropna(subset=['y5']).copy()

# 筛选最常用的 phase
phases_present = []
for ph in ['B_ACCUM', 'B_DISTRIB', 'A_ACCUM', 'A_RECOVER', 'NEUTRAL', 'HI_EQ']:
    if (df[f'phase_{ph}'] == 1).any():
        phases_present.append(ph)

feat_cols = ['lagged_5d_return', 'turnover_z', 'insider_buy', 'insider_neutral', 'total_energy'] + \
            [f'phase_{p}' for p in phases_present]

print(f"\n[6] 特征: {feat_cols}")
print(f"  T+3 样本: {len(df_y3)}, T+5 样本: {len(df_y5)}")

# ── 7. Ridge ──
def run_ridge(df_in, y_col, label):
    n = len(df_in)
    if n < 50:
        return None
    X = df_in[feat_cols].fillna(0).values
    y = df_in[y_col].values
    
    scaler = StandardScaler()
    X_s = scaler.fit_transform(X)
    
    split = int(n * 0.67)
    X_tr, y_tr = X_s[:split], y[:split]
    X_te, y_te = X_s[split:], y[split:]
    
    alphas = np.logspace(-3, 3, 50)
    model = RidgeCV(alphas=alphas, cv=5, scoring='r2')
    model.fit(X_tr, y_tr)
    
    cv = cross_val_score(RidgeCV(alphas=alphas, cv=3), X_s, y, cv=5, scoring='r2')
    
    print(f"\n  {label} (N={n}):")
    print(f"    最优 α: {model.alpha_:.4f}")
    print(f"    训练 R²: {model.score(X_tr, y_tr):.4f}")
    print(f"    测试 R²: {model.score(X_te, y_te):.4f}")
    print(f"    CV R²: {cv.mean():.4f} ± {cv.std():.4f}")
    print(f"    系数:")
    
    alphas_p = np.logspace(-3, 3, 100)
    cp = ridge_regression(X_s, y, alphas_p)
    stable = []
    for i, col in enumerate(feat_cols):
        c = model.coef_[i]
        print(f"      {col:25s}: {c:.6f}")
        seg = cp[i, (alphas_p >= 0.1) & (alphas_p <= 10)]
        if np.all(seg > 0) or np.all(seg < 0):
            stable.append(col)
    
    print(f"    符号稳定: {len(stable)}/{len(feat_cols)}")
    for s in stable:
        print(f"      ✅ {s}")
    
    return {
        'alpha': model.alpha_,
        'train_r2': model.score(X_tr, y_tr),
        'test_r2': model.score(X_te, y_te),
        'cv_r2': cv.mean(),
        'stable_cnt': len(stable),
        'stable_feats': stable,
        'coefs': {col: float(model.coef_[i]) for i, col in enumerate(feat_cols)}
    }

r3 = run_ridge(df_y3, 'y3', 'T+3')
r5 = run_ridge(df_y5, 'y5', 'T+5')

# ── 8. 判定 ──
print("\n" + "=" * 70)
print("判定 — 预设失败条件")
print("=" * 70)

cv3 = r3['cv_r2'] if r3 else -10
cv5 = r5['cv_r2'] if r5 else -10
st3 = r3['stable_cnt'] if r3 else 0
st5 = r5['stable_cnt'] if r5 else 0
ts3 = r3['test_r2'] if r3 else -10
ts5 = r5['test_r2'] if r5 else -10

f1 = cv3 < 0.02 and cv5 < 0.02
f2 = st3 == 0 and st5 == 0
f3 = ts3 <= 0 and ts5 <= 0

print(f"\n条件1: CV R² < 0.02? T+3={cv3:.4f} T+5={cv5:.4f} → {'⚠️ 失败' if f1 else '✅ 通过'}")
print(f"条件2: 无符号稳定? T+3={st3} T+5={st5} → {'⚠️ 失败' if f2 else '✅ 通过'}")
print(f"条件3: 测试 R² ≤ 0? T+3={ts3:.4f} T+5={ts5:.4f} → {'⚠️ 失败' if f3 else '✅ 通过'}")

if f1 and f2 and f3:
    print("\n➡ V7 = 纯风险预算 + 行为筛选（岭回归路线放弃）")
    print("  模块1: 流动性乘数 | 模块2: Phase仓位约束 | 模块3: 行为独立筛选")
else:
    print("\n✅ V7 纳入岭回归评分模块")

result = {
    't3': r3 if r3 else None,
    't5': r5 if r5 else None,
    'verdict': 'fail' if (f1 and f2 and f3) else 'pass'
}
with open('/tmp/ridge_s3_v3_result.json', 'w') as f:
    json.dump(result, f, indent=2, default=str)
print(f"\n✅ 完成")
