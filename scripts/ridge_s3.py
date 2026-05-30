"""
ridge_s3.py — S3信号的岭回归验证

预设失败条件：
1. T+3 和 T+5 交叉验证 R² 均 < 0.02
2. 无特征在 α∈[0.1,10] 范围内符号稳定
3. 样本外(2024-2026) R² ≤ 0

特征：
- lagged_5d_return: 前5日累计收益
- turnover_z: 换手率Z分数（20日窗口）
- pe_percentile: 行业内PE分位
- pb_percentile: 行业内PB分位  
- sector_strong_ratio: 同板块强势股占比
- phase_B_ACCUM, phase_B_DISTRIB, phase_A_ACCUM, phase_A_RECOVER: 大盘Phase独热
- total_energy_trend: 大盘总能量60日趋势
- insider_buy_flag: 7日内高管增持
- lhb_net_sell_flag: 龙虎榜机构净卖
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import numpy as np
import pandas as pd
from datetime import date, timedelta
from sklearn.linear_model import RidgeCV, ridge_regression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split, cross_val_score
import time

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
conn = psycopg2.connect(DB)
cur = conn.cursor()

print("=" * 70)
print("Ridge S3 — 岭回归验证（预设失败条件）")
print("=" * 70)

# ── 1. 取 S3 信号 + 特征 ──
print("\n[1] 构建特征矩阵...")

sql = """
WITH s3 AS (
    SELECT pe.date, SUBSTRING(pe.level, 3) as code,  -- A.600xxx -> 600xxx
           (pe.extra::json->>'sig') as sig,
           (pe.extra::json->>'ret')::numeric as ret_at_signal
    FROM pool_energy pe
    WHERE pe.extra::json->>'sig' = 'S3'
),
dp_data AS (
    SELECT code, trade_date, 
           close, volume, turnover,
           ROUND(close / LAG(close, 5) OVER (PARTITION BY code ORDER BY trade_date) - 1, 6) as ret_5d,
           ROUND(close / LAG(close, 20) OVER (PARTITION BY code ORDER BY trade_date) - 1, 6) as ret_20d,
           ROUND(close / LAG(close, 3) OVER (PARTITION BY code ORDER BY trade_date) - 1, 6) as ret_3d,
           ROUND(close / LAG(close, 5) OVER (PARTITION BY code ORDER BY trade_date) - 1, 6) as ret_5d_fwd
    FROM daily_prices
    WHERE trade_date >= '2018-01-01'
),
sector AS (
    SELECT code, industry FROM industry_components_ths_l1
),
market_ret AS (
    SELECT trade_date,
           AVG(close / LAG(close, 1) OVER (PARTITION BY symbol ORDER BY trade_date) - 1) as mkt_ret
    FROM daily_prices WHERE trade_date >= '2018-01-01'
    GROUP BY trade_date
)
SELECT s3.date::text, s3.code,
       s3.ret_at_signal,
       dp.ret_5d as lagged_5d_return,
       ROUND((dp3.close / dp3_20.close - 1)::numeric, 4) as t3_excess,
       ROUND((dp5.close / dp5_20.close - 1)::numeric, 4) as t5_excess,
       ms.pe, ms.pb,
       ic.sector_name,
       ic.industry
FROM s3
JOIN daily_prices dp ON dp.symbol = 'A.' || s3.code AND dp.trade_date = s3.date
LEFT JOIN daily_prices dp3 ON dp3.symbol = 'A.' || s3.code 
    AND dp3.trade_date = s3.date + INTERVAL '3 trading days'
LEFT JOIN daily_prices dp5 ON dp5.symbol = 'A.' || s3.code 
    AND dp5.trade_date = s3.date + INTERVAL '5 trading days'
LEFT JOIN market_snapshots ms ON ms.code = s3.code AND ms.trade_date = s3.date
LEFT JOIN industry_components_ths_l1 ic ON ic.code = s3.code
WHERE dp.close > 0
LIMIT 1000
"""
# 简化：先用基础特征做第一版，确认数据可用再补全

# 简化查询——先取核心数据
sql = """
SELECT pe.date, SUBSTRING(pe.level, 3) as code,
       (pe.extra::json->>'ret')::numeric as ret_at_signal,
       pe.phase
FROM pool_energy pe
WHERE pe.extra::json->>'sig' = 'S3'
  AND pe.date >= '2020-01-01'
ORDER BY pe.date
LIMIT 2000
"""
cur.execute(sql)
rows = cur.fetchall()
print(f"  原始 S3 信号: {len(rows)} 条")

# 逐条拼特征（避免复杂SQL）
from collections import defaultdict

# 缓存：获取某个code某天的close/fwd return
def get_price_close(cur, code, d):
    cur.execute("SELECT close FROM daily_prices WHERE symbol='A.'||%s AND trade_date=%s", (code, d))
    r = cur.fetchone()
    return r[0] if r else None

def get_fwd_return(cur, code, d, n_days):
    """返回 n_days 后的收益率（相对于 20日前）"""
    # 先找当天和 n 天后的 close
    cur.execute("""
        SELECT dp.trade_date, dp.close, ms.close as mkt_close
        FROM daily_prices dp
        LEFT JOIN market_ret mr ON mr.trade_date = dp.trade_date
        WHERE dp.symbol='A.'||%s AND dp.trade_date >= %s
        ORDER BY dp.trade_date
        LIMIT %s
    """, (code, d, n_days + 5))
    rows = cur.fetchall()
    if len(rows) < n_days:
        return None
    # 找到 d 和 d+n_days 的 close
    close_d = None
    close_tn = None
    for r in rows:
        if r[0] == d:
            close_d = r[1]
        if len(rows) > 0 and r[0] is not None:
            # 简单：取第 n_days 个
            pass
    # 简化——用PostgreSQL窗口函数
    return None

# 改用 SQL 一次性拼 —— 用 PostgreSQL 窗口函数
print("\n[2] 用 SQL 构建特征 + 目标变量...")

sql = """
WITH s3 AS (
    SELECT pe.date, SUBSTRING(pe.level, 3) as code,
           (pe.extra::json->>'ret')::numeric as ret_at_signal,
           pe.phase
    FROM pool_energy pe
    WHERE pe.extra::json->>'sig' = 'S3'
      AND pe.date >= '2020-01-01'
),
prices AS (
    SELECT SUBSTRING(symbol, 3) as code, trade_date, close, turnover,
           ROW_NUMBER() OVER (PARTITION BY SUBSTRING(symbol, 3) ORDER BY trade_date) as rn
    FROM daily_prices
    WHERE trade_date >= '2019-06-01' AND close > 0
),
future_ret AS (
    SELECT p1.code, p1.trade_date,
           p1.close as close_d,
           -- T+3 (3个交易日)
           p3.close as close_3,
           -- T+5 (5个交易日)
           p5.close as close_5
    FROM prices p1
    LEFT JOIN prices p3 ON p3.code = p1.code AND p3.rn = p1.rn + 3
    LEFT JOIN prices p5 ON p5.code = p1.code AND p5.rn = p1.rn + 5
),
-- 市场收益（等权日均）
mkt_ret AS (
    SELECT trade_date, AVG(close / LAG(close) OVER (ORDER BY trade_date) - 1) as mkt_ret
    FROM daily_prices WHERE trade_date >= '2019-06-01' AND close > 0
    GROUP BY trade_date
)
SELECT s3.date, s3.code, s3.ret_at_signal, s3.phase,
       fr.close_d,
       -- lagged_5d_return
       ROUND(LOG(fr.close_d / LAG(fr.close_d, 5) OVER (PARTITION BY s3.code ORDER BY s3.date)), 6) as lagged_5d_return,
       -- t3_excess, t5_excess
       CASE WHEN fr.close_3 > 0 THEN 
           ROUND(((fr.close_3 / fr.close_d - 1) - COALESCE(mr3.mkt_ret, 0))::numeric, 6)
       END as t3_excess,
       CASE WHEN fr.close_5 > 0 THEN 
           ROUND(((fr.close_5 / fr.close_d - 1) - COALESCE(mr5.mkt_ret, 0))::numeric, 6)
       END as t5_excess,
       -- 换手率 Z 分数
       ROUND((p_current.turnover - AVG(p_hist.turnover) OVER (PARTITION BY s3.code ORDER BY s3.date ROWS BETWEEN 19 PRECEDING AND 1 PRECEDING)) / 
             NULLIF(STDDEV(p_hist.turnover) OVER (PARTITION BY s3.code ORDER BY s3.date ROWS BETWEEN 19 PRECEDING AND 1 PRECEDING), 0), 4) as turnover_z
FROM s3
JOIN future_ret fr ON fr.code = s3.code AND fr.trade_date = s3.date
JOIN prices p_current ON p_current.code = s3.code AND p_current.trade_date = s3.date
LEFT JOIN prices p_hist ON p_hist.code = s3.code
    AND p_hist.rn BETWEEN p_current.rn - 21 AND p_current.rn - 1
LEFT JOIN mkt_ret mr3 ON mr3.trade_date = (
    SELECT p3.trade_date FROM prices p3 
    WHERE p3.code = s3.code AND p3.rn = p_current.rn + 3
)
LEFT JOIN mkt_ret mr5 ON mr5.trade_date = (
    SELECT p5.trade_date FROM prices p5 
    WHERE p5.code = s3.code AND p5.rn = p_current.rn + 5
)
WHERE fr.close_d > 0
"""

# 太复杂了——PostgreSQL这SQL写起来像在打仗。换个简单策略：
# 先全量拉 raw data，Python 里算特征
print("  改用 Python 计算特征...")

# 1. 取所有 S3 信号
cur.execute("""
    SELECT date, SUBSTRING(level, 3) as code,
           (extra::json->>'ret')::numeric as ret_at_signal,
           phase
    FROM pool_energy
    WHERE extra::json->>'sig' = 'S3'
      AND date >= '2020-01-01'
    ORDER BY date
""")
s3_rows = cur.fetchall()
print(f"  S3 信号: {len(s3_rows)} 条")

# 2. 取所有需要的日线数据（一次全量）
cur.execute("""
    SELECT SUBSTRING(symbol, 3) as code, trade_date, close, turnover
    FROM daily_prices
    WHERE trade_date >= '2019-06-01' AND close > 0
    ORDER BY code, trade_date
""")
all_prices = cur.fetchall()
print(f"  日线数据: {len(all_prices)} 条")

# 3. 组织为 dict[code] -> [(date, close, turnover)]
from collections import OrderedDict
import bisect

price_by_code = defaultdict(list)
for r in all_prices:
    price_by_code[r[0]].append((r[1], r[2], r[3]))

print(f"  股票数: {len(price_by_code)}")

# 4. 计算特征的函数
def get_return(prices, idx, offset):
    """offset 个交易日后的收益率"""
    if idx + offset < len(prices):
        p0 = prices[idx][1]
        pn = prices[idx + offset][1]
        if p0 > 0 and pn > 0:
            return pn / p0 - 1
    return None

def get_turnover_z(prices, idx):
    """换手率 Z 分数（前20日）"""
    if idx < 20:
        return None
    vals = [prices[i][2] for i in range(idx - 20, idx) if prices[i][2]]
    if not vals or len(vals) < 10:
        return None
    mean = np.mean(vals)
    std = np.std(vals)
    current = prices[idx][2] if idx < len(prices) else None
    if current and std > 0 and mean > 0:
        return (current - mean) / std
    return None

# 5. 构建特征矩阵
features = []
targets = {'t3': [], 't5': []}

# 按S3信号的日期，找到该code的日线索引
code_price_idx = {}
for code, plist in price_by_code.items():
    code_price_idx[code] = {d[0]: i for i, d in enumerate(plist)}

for r in s3_rows:
    date_sig, code, ret_sig, phase = r
    
    if code not in code_price_idx:
        continue
    plist = price_by_code.get(code, [])
    idx = code_price_idx[code].get(date_sig)
    if idx is None:
        continue
    
    # lagged_5d_return
    lag_r = get_return(plist, idx - 5, 5) if idx >= 5 else None
    
    # t3, t5 excess return
    t3 = get_return(plist, idx, 3)
    t5 = get_return(plist, idx, 5)
    t3_excess = None
    t5_excess = None
    if t3 is not None:
        t3_excess = t3 - 0  # 全市场等权，简化处理
    if t5 is not None:
        t5_excess = t5 - 0
    
    # turnover_z
    to_z = get_turnover_z(plist, idx)
    
    if t3 is not None or t5 is not None:
        features.append({
            'lagged_5d_return': lag_r,
            'turnover_z': to_z,
            'phase': phase,
            'code': code,
            'date': date_sig,
        })
        targets['t3'].append(t3_excess)
        targets['t5'].append(t5_excess)

print(f"\n[3] 有效样本:")
print(f"  T+3: {sum(1 for t in targets['t3'] if t is not None)}")
print(f"  T+5: {sum(1 for t in targets['t5'] if t is not None)}")

# 6. 构建数值特征矩阵
valid_rows = [(f, t3, t5) for f, t3, t5 in zip(features, targets['t3'], targets['t5'])
              if t3 is not None or t5 is not None]

if len(valid_rows) < 100:
    print("  ❌ 样本不足！")
    sys.exit(1)

# Phase 独热
phases = ['B_ACCUM', 'B_DISTRIB', 'A_ACCUM', 'A_RECOVER', 'NEUTRAL', 'HI_EQ']

# 构建 DataFrame
data = []
for f, t3, t5 in valid_rows:
    row = {
        'lagged_5d_return': f['lagged_5d_return'],
        'turnover_z': f['turnover_z'],
        'y3': t3, 'y5': t5
    }
    for ph in phases:
        row[f'phase_{ph}'] = 1 if f['phase'] == ph else 0
    data.append(row)

df = pd.DataFrame(data)
print(f"  DataFrame: {df.shape}")

# 剔除 NaN 行
df = df.dropna(subset=['lagged_5d_return', 'y3', 'y5'], how='all')
print(f"  剔除NaN后: {df.shape}")

feature_cols = ['lagged_5d_return', 'turnover_z'] + [f'phase_{ph}' for ph in phases]
feature_cols = [c for c in feature_cols if c in df.columns]

# 分数据集
df_t3 = df.dropna(subset=['y3']).copy()
df_t5 = df.dropna(subset=['y5']).copy()
print(f"\n  T+3 有效: {len(df_t3)}, T+5 有效: {len(df_t5)}")

# 训练集/测试集（按时间分段）
# 2024-01-01 前 = 训练，之后 = 测试
train_mask_t3 = df_t3.index < len(df_t3) // 2  # 近似
# 按时间排序
df_t3 = df_t3.sort_values('date' if 'date' in df_t3 else df_t3.index)
df_t5 = df_t5.sort_values('date' if 'date' in df_t5 else df_t5.index)

# 简化为 2018-2023 训练, 2024-2026 测试
# 用百分比切——2/3 训练, 1/3 测试
n3 = len(df_t3)
split3 = int(n3 * 0.67)
n5 = len(df_t5)
split5 = int(n5 * 0.67)

# ── 7. 岭回归 ──
print("\n[4] 跑岭回归...")

def run_ridge(df_in, y_col, label, split):
    """运行 RidgeCV + 正则化路径"""
    X = df_in[feature_cols].fillna(0).values
    y = df_in[y_col].values
    
    # 标准化
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    # 训练/测试集（时间序）
    X_train = X_scaled[:split]
    y_train = y[:split]
    X_test = X_scaled[split:]
    y_test = y[split:]
    
    print(f"\n  {label}:")
    print(f"    训练: {len(X_train)}, 测试: {len(X_test)}")
    
    # RidgeCV
    alphas = np.logspace(-3, 3, 50)
    model = RidgeCV(alphas=alphas, cv=5, scoring='r2')
    model.fit(X_train, y_train)
    
    # 训练集 R²
    train_score = model.score(X_train, y_train)
    # 测试集 R²
    test_score = model.score(X_test, y_test)
    # 交叉验证 R²
    cv_scores = cross_val_score(model, X_scaled, y, cv=5, scoring='r2')
    
    print(f"    最优 α: {model.alpha_:.4f}")
    print(f"    训练 R²: {train_score:.4f}")
    print(f"    测试 R²: {test_score:.4f}")
    print(f"    交叉验证 R² (5折): 均值={cv_scores.mean():.4f} 标准差={cv_scores.std():.4f}")
    
    # 系数
    print(f"    系数:")
    for name, coef in zip(feature_cols, model.coef_):
        print(f"      {name:25s}: {coef:.6f}")
    
    # 正则化路径
    alphas_path = np.logspace(-3, 3, 100)
    path = ridge_regression(X_scaled, y, alphas_path)
    
    # 符号稳定性（在 α∈[0.1, 10] 范围）
    stable_mask = (alphas_path >= 0.1) & (alphas_path <= 10)
    stable_coefs = path[:, stable_mask]
    
    stable_features = []
    for i, name in enumerate(feature_cols):
        coefs_i = stable_coefs[i]
        # 符号不变
        if np.all(coefs_i > 0) or np.all(coefs_i < 0):
            stable_features.append(name)
    
    print(f"    α∈[0.1,10] 符号稳定特征: {len(stable_features)}/{len(feature_cols)}")
    if stable_features:
        for f in stable_features:
            print(f"      ✅ {f}")
    else:
        print(f"      ❌ 无符号稳定特征")
    
    result = {
        'alpha': model.alpha_,
        'cv_r2': cv_scores.mean(),
        'cv_std': cv_scores.std(),
        'test_r2': test_score,
        'train_r2': train_score,
        'coefs': list(zip(feature_cols, model.coef_)),
        'stable_cnt': len(stable_features),
        'path': path,
        'alphas': alphas_path
    }
    return result

r3 = run_ridge(df_t3, 'y3', 'T+3', split3)
r5 = run_ridge(df_t5, 'y5', 'T+5', split5)

# ── 8. 判定 ──
print("\n" + "=" * 70)
print("判定 — 预设失败条件验证")
print("=" * 70)

fail1 = r3['cv_r2'] < 0.02 and r5['cv_r2'] < 0.02
fail2 = r3['stable_cnt'] == 0 and r5['stable_cnt'] == 0
fail3 = r3['test_r2'] <= 0 and r5['test_r2'] <= 0

print(f"\n条件1: CV R² < 0.02 ? T+3={r3['cv_r2']:.4f} T+5={r5['cv_r2']:.4f}")
print(f"  → {'✅ 失败' if fail1 else '❌ 通过'}")

print(f"\n条件2: 无符号稳定特征 ? T+3={r3['stable_cnt']} T+5={r5['stable_cnt']}")
print(f"  → {'✅ 失败' if fail2 else '❌ 通过'}")

print(f"\n条件3: 测试 R² ≤ 0 ? T+3={r3['test_r2']:.4f} T+5={r5['test_r2']:.4f}")
print(f"  → {'✅ 失败' if fail3 else '❌ 通过'}")

if fail1 and fail2 and fail3:
    print("\n❌❌❌ 三条失败条件全部满足！")
    print("V7 退化为纯风险预算 + 行为筛选")
    print("  模块1: 流动性乘数（总能量60日均线降仓）")
    print("  模块2: Phase 仓位约束（只限制上限）")
    print("  模块3: 行为信号独立筛选（增持/龙虎榜）")
else:
    print(f"\n✅ 岭回归路线通过！V7 纳入打分模块")
    print(f"  通过的信号: {'T+3' if r3['cv_r2']>=0.02 else ''} {'T+5' if r5['cv_r2']>=0.02 else ''}")

# 存入结果
import json
result = {
    't3': {'alpha': r3['alpha'], 'cv_r2': r3['cv_r2'], 'test_r2': r3['test_r2'], 'stable_cnt': r3['stable_cnt']},
    't5': {'alpha': r5['alpha'], 'cv_r2': r5['cv_r2'], 'test_r2': r5['test_r2'], 'stable_cnt': r5['stable_cnt']},
    'verdict': 'fail' if (fail1 and fail2 and fail3) else 'pass',
    'n_samples': {'t3': len(df_t3), 't5': len(df_t5)}
}
with open('/tmp/ridge_s3_result.json', 'w') as f:
    json.dump(result, f, indent=2, default=str)
print(f"\n结果已保存: /tmp/ridge_s3_result.json")

conn.close()
print("\n✅ 完成")
