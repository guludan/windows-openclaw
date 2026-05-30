"""
ridge_s3.py — S3信号的岭回归验证（v2）

特征矩阵（重新构建）：
- lagged_5d_return: 前5日累计收益 ✓
- turnover_z: 换手率Z分数（20日窗口） ✓
- pe_percentile: 行业内PE分位
- pb_percentile: 行业内PB分位
- sector_strong_ratio: 同板块强势股占比
- phase_*: 大盘相空间独热 ✓
- total_energy_trend: 大盘总能量60日趋势
- insider_buy_flag: 7日内高管增持
- lhb_net_sell_flag: 龙虎榜机构净卖

预设失败条件：
1. T+3 和 T+5 交叉验证 R² 均 < 0.02
2. 无特征在 α∈[0.1,10] 范围内符号稳定
3. 样本外(2024-2026) R² ≤ 0
"""
import psycopg2
import numpy as np
import pandas as pd
from datetime import datetime, date, timedelta
from sklearn.linear_model import RidgeCV, ridge_regression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score
import json, os, sys, warnings
warnings.filterwarnings('ignore')

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
conn = psycopg2.connect(DB)
cur = conn.cursor()

print("=" * 70)
print("Ridge S3 v2 — 岭回归验证")
print("=" * 70)

# ── 1. 一次性拉全量数据 ──
print("\n[1] 拉取全量数据...")

# S3 信号 + 价格
cur.execute("""
    SELECT pe.date, SUBSTRING(pe.level, 8) as code6,
           dp.close
    FROM pool_energy pe
    JOIN daily_prices dp ON dp.symbol = 'A.' || SUBSTRING(pe.level, 8)
        AND dp.trade_date = pe.date
    WHERE pe.phase = 's3_panic'
      AND pe.date >= '2020-01-01'
      AND dp.close > 0
    ORDER BY pe.date
""")
s3 = cur.fetchall()
print(f"  S3 信号: {len(s3)} 条")

# 拉日线全量（回填换手率版）
cur.execute("""
    SELECT SUBSTRING(symbol, 3) as code6, trade_date, close, turnover
    FROM daily_prices
    WHERE trade_date >= '2019-06-01' AND close > 0
      AND SUBSTRING(symbol, 1, 2) = 'A.'
    ORDER BY code6, trade_date
""")
all_px = cur.fetchall()
print(f"  日线全量: {len(all_px)} 条")

# 拉大盘 pool_energy（market 级别：phase + energy）
cur.execute("""
    SELECT date, phase, b_pool, a_pool, b_inject, a_inject
    FROM pool_energy
    WHERE level = 'market'
      AND date >= '2020-01-01'
    ORDER BY date
""")
market_pe = cur.fetchall()
print(f"  大盘PE: {len(market_pe)} 条")

# 拉行业内PE/PB分位（market_snapshots 最新7天的 pe/pb）
# 由于 market_snapshots.pe/pb 只有最新7天，改从 financial_data 的 eps_clean 推算
cur.execute("""
    SELECT symbol, end_date, eps_clean, roe
    FROM financial_data
    WHERE eps_clean IS NOT NULL AND eps_clean > 0
      AND end_date >= '2019-12-31'
    ORDER BY symbol, end_date
""")
fd = cur.fetchall()
print(f"  财务数据: {len(fd)} 条")

# 行业成分
cur.execute("""
    SELECT code, industry, sector_name
    FROM industry_components_ths_l1
""")
ic = cur.fetchall()
code_to_industry = {r[0]: r[2] or 'other' for r in ic}
print(f"  行业成分: {len(ic)} 条")

# 高管增减持
cur.execute("""
    SELECT code, announce_date, change_type, change_num
    FROM incr_decr
    WHERE change_type IN ('INCREASE', 'DECREASE')
      AND announce_date >= '2020-01-01'
    ORDER BY code, announce_date
""")
insider = cur.fetchall()
print(f"  增减持: {len(insider)} 条")

# 龙虎榜
cur.execute("""
    SELECT SUBSTRING(code, 6) as code6, trade_date,
           (extra::json->>'inst_sell_net')::numeric as inst_net
    FROM market_signals
    WHERE signal_type = 'lhb_daily'
    LIMIT 5000
""")
lhb = cur.fetchall()
print(f"  龙虎榜: {len(lhb)} 条")

conn.close()

# ── 2. 数据结构化 ──
print("\n[2] 构建特征矩阵...")

# 日线：code6 -> [(date, close, turnover), ...]
import bisect
from collections import OrderedDict, defaultdict

px_by_code = defaultdict(list)
for r in all_px:
    px_by_code[r[0]].append((r[1], r[2], r[3]))
print(f"  股票数: {len(px_by_code)}")

# 大盘 phase 按日期索引
mkt_phase = {r[0]: r[1] for r in market_pe}
mkt_energy = {r[0]: {'b_pool': r[2] or 0, 'a_pool': r[3] or 0, 'b_inject': r[4] or 0, 'a_inject': r[5] or 0} for r in market_pe}

# 财务 eps TTM：code6 -> [(end_date, ttm_eps)]
fd_by_code = defaultdict(list)
for r in fd:
    fd_by_code[r[0]].append((r[1], r[2], r[3]))

# 增减持
insider_by_code = defaultdict(list)
for r in insider:
    insider_by_code[r[0]].append((r[1], r[2], r[3]))

# 龙虎榜
lhb_by_code = defaultdict(list)
for r in lhb:
    lhb_by_code[r[0]].append((r[1], r[2]))

# ── 3. 特征计算函数 ──
def get_px_idx(plist, d):
    """二分查找日期在 plist 中的索引"""
    dates = [p[0] for p in plist]
    i = bisect.bisect_left(dates, d)
    if i < len(dates) and dates[i] == d:
        return i
    return None

def get_px_at(plist, idx):
    if idx is not None and 0 <= idx < len(plist):
        return plist[idx][1]  # close
    return None

def get_to_at(plist, idx):
    if idx is not None and 0 <= idx < len(plist):
        return plist[idx][2]  # turnover
    return None

def get_ttm_eps(fd_list, d):
    """获取最近 end_date 的 eps_clean × 4 (作为 ttm)"""
    if not fd_list:
        return None
    best = None
    for r in fd_list:
        if r[0] <= d:
            best = r
    if best:
        # eps_clean 是累计值（Q1/Q2/Q3/Q4逐年累加），但直接用年报的最好
        return best[1]  # 直接返回 eps_clean
    return None

def compute_features(date_sig, code6, plist):
    """计算所有特征"""
    idx = get_px_idx(plist, date_sig)
    if idx is None:
        return None
    
    feats = {}
    prices = plist
    
    # lagged_5d_return
    px_d = get_px_at(prices, idx)
    px_5 = get_px_at(prices, idx - 5)
    feats['lagged_5d_return'] = (px_d / px_5 - 1) if (px_d and px_5 and px_5 > 0) else None
    
    # turnover_z
    if idx >= 20:
        to_vals = [get_to_at(prices, i) for i in range(idx - 20, idx)]
        to_vals = [v for v in to_vals if v is not None and v > 0]
        if len(to_vals) >= 10:
            to_mean = np.mean(to_vals)
            to_std = np.std(to_vals)
            to_current = get_to_at(prices, idx)
            if to_current and to_std > 0:
                feats['turnover_z'] = (to_current - to_mean) / to_std
            else:
                feats['turnover_z'] = None
        else:
            feats['turnover_z'] = None
    else:
        feats['turnover_z'] = None
    
    # T+3, T+5 forward returns
    px_3 = get_px_at(prices, idx + 3)
    px_5 = get_px_at(prices, idx + 5)
    
    ret_3 = (px_3 / px_d - 1) if (px_3 and px_d) else None
    ret_5 = (px_5 / px_d - 1) if (px_5 and px_d) else None
    
    feats['y3'] = ret_3
    feats['y5'] = ret_5
    
    # T+3, T+5 市场超额（简化：用大盘涨跌均值）
    # 这里跳过精确 market excess，先跑基线
    feats['y3_raw'] = ret_3
    feats['y5_raw'] = ret_5
    
    # Phase 独热
    phase = mkt_phase.get(date_sig, 'NEUTRAL')
    for ph in ['B_ACCUM', 'B_DISTRIB', 'A_ACCUM', 'A_RECOVER', 'NEUTRAL', 'HI_EQ']:
        feats[f'phase_{ph}'] = 1 if phase == ph else 0
    
    # 高管增持标志
    insiders = insider_by_code.get(code6, [])
    has_buy = False
    has_sell = False
    for ins in insiders:
        if ins[0] - timedelta(days=7) <= date_sig <= ins[0] + timedelta(days=3):
            if ins[1] == 'INCREASE':
                has_buy = True
            if ins[1] == 'DECREASE':
                has_sell = True
    feats['insider_buy_flag'] = 1 if has_buy else 0
    feats['insider_sell_flag'] = 1 if (has_sell and not has_buy) else 0
    
    # 大盘总能量趋势（60日均线）
    # 用最近的 60 个交易日的大盘 b_pool 均值
    # 简化为当前的大盘能量
    energy = mkt_energy.get(date_sig, {})
    feats['total_energy'] = (energy.get('b_pool', 0) + energy.get('a_pool', 0)) / 2 if energy else 0
    
    return feats

# ── 4. 构建特征矩阵 ──
records = []
for r in s3:
    date_sig, code6, close = r
    plist = px_by_code.get(code6, [])
    if not plist:
        continue
    feats = compute_features(date_sig, code6, plist)
    if feats is None:
        continue
    if feats['y3'] is None and feats['y5'] is None:
        continue
    feats['date'] = date_sig
    feats['code'] = code6
    records.append(feats)

print(f"\n[3] 有效样本: {len(records)}")

# Phase 分布
from collections import Counter
phase_counts = Counter(r.get('phase_B_ACCUM', 0) for r in records)
phases_present = []
for ph in ['B_ACCUM', 'B_DISTRIB', 'A_ACCUM', 'A_RECOVER', 'NEUTRAL', 'HI_EQ']:
    cnt = sum(1 for r in records if r.get(f'phase_{ph}', 0) == 1)
    if cnt > 0:
        phases_present.append(ph)
        print(f"  Phase {ph}: {cnt} 条")

print(f"  Y3 有值: {sum(1 for r in records if r['y3'] is not None)}")
print(f"  Y5 有值: {sum(1 for r in records if r['y5'] is not None)}")

# ── 5. 构建 DataFrame ──
df = pd.DataFrame(records)

feature_cols = ['lagged_5d_return', 'turnover_z', 
                'insider_buy_flag', 'insider_sell_flag', 'total_energy'] + \
               [f'phase_{ph}' for ph in phases_present]
feature_cols = [c for c in feature_cols if c in df.columns]

# 剔除特征全为 NaN 的行
df_features = df[feature_cols].copy()
df_features = df_features.fillna(0)  # NaN 替换为 0

print(f"\n[4] 特征统计:")
for col in feature_cols:
    nz = (df_features[col] != 0).sum()
    print(f"  {col:25s}: 非零 {nz:>5d}/{len(df_features)}")

# ── 6. 跑 Ridge ──
print("\n" + "=" * 70)
print("岭回归结果")
print("=" * 70)

def run_ridge_on_y(df_in, y_col, label):
    """运行 RidgeCV + 正则化路径"""
    valid = df_in.dropna(subset=[y_col])
    if len(valid) < 50:
        print(f"\n  {label}: 样本不足 ({len(valid)})")
        return None
    
    X = valid[feature_cols].fillna(0).values
    y = valid[y_col].values
    
    # 标准化
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    # 时间序分割：2/3 训练, 1/3 测试
    n = len(X_scaled)
    split = int(n * 0.67)
    X_train, y_train = X_scaled[:split], y[:split]
    X_test, y_test = X_scaled[split:], y[split:]
    
    print(f"\n  {label}: N={n} 训练={len(X_train)} 测试={len(X_test)}")
    
    # RidgeCV
    alphas = np.logspace(-3, 3, 50)
    model = RidgeCV(alphas=alphas, cv=5, scoring='r2')
    model.fit(X_train, y_train)
    
    train_r2 = model.score(X_train, y_train)
    test_r2 = model.score(X_test, y_test)
    
    # 全量 5折 CV
    cv_scores = cross_val_score(RidgeCV(alphas=alphas, cv=3, scoring='r2'), 
                                X_scaled, y, cv=5, scoring='r2')
    
    print(f"    最优 α: {model.alpha_:.4f}")
    print(f"    训练 R²: {train_r2:.4f}")
    print(f"    测试 R²: {test_r2:.4f}")
    print(f"    交叉验证 R² (5折): {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")
    
    print(f"    系数:")
    for name, coef in zip(feature_cols, model.coef_):
        print(f"      {name:25s}: {coef:.6f}")
    
    # 正则化路径——符号稳定性
    alphas_path = np.logspace(-3, 3, 100)
    coef_paths = ridge_regression(X_scaled, y, alphas_path)
    
    stable_mask = (alphas_path >= 0.1) & (alphas_path <= 10)
    stable_coefs = coef_paths[:, stable_mask]
    
    stable_features = []
    for i, name in enumerate(feature_cols):
        series = stable_coefs[i]
        if np.all(series > 0) or np.all(series < 0):
            stable_features.append(name)
    
    print(f"    α∈[0.1,10] 符号稳定: {len(stable_features)}/{len(feature_cols)}")
    for f in stable_features:
        print(f"      ✅ {f}")
    if not stable_features:
        print(f"      ❌ 无符号稳定特征")
    
    return {
        'alpha': model.alpha_,
        'train_r2': train_r2,
        'test_r2': test_r2,
        'cv_r2_mean': cv_scores.mean(),
        'cv_r2_std': cv_scores.std(),
        'coefs': {name: float(coef) for name, coef in zip(feature_cols, model.coef_)},
        'stable_cnt': len(stable_features),
        'stable_features': stable_features,
        'n': n
    }

r3 = run_ridge_on_y(df, 'y3', 'T+3')
r5 = run_ridge_on_y(df, 'y5', 'T+5')

# ── 7. 判定 ──
print("\n" + "=" * 70)
print("判定 — 预设失败条件")
print("=" * 70)

cv3 = r3['cv_r2_mean'] if r3 else -10
cv5 = r5['cv_r2_mean'] if r5 else -10
stable3 = r3['stable_cnt'] if r3 else 0
stable5 = r5['stable_cnt'] if r5 else 0
test3 = r3['test_r2'] if r3 else -10
test5 = r5['test_r2'] if r5 else -10

fail1 = cv3 < 0.02 and cv5 < 0.02
fail2 = stable3 == 0 and stable5 == 0
fail3 = test3 <= 0 and test5 <= 0

print(f"\n条件1: CV R² < 0.02?")
print(f"  T+3: {cv3:.4f} {'❌<0.02' if cv3 < 0.02 else '✅>=0.02'}")
print(f"  T+5: {cv5:.4f} {'❌<0.02' if cv5 < 0.02 else '✅>=0.02'}")
print(f"  → {'✅ 失败' if fail1 else '❌ 通过'}")

print(f"\n条件2: 无符号稳定特征?")
print(f"  T+3: {stable3}, T+5: {stable5}")
print(f"  → {'✅ 失败' if fail2 else '❌ 通过'}")

print(f"\n条件3: 测试 R² ≤ 0?")
print(f"  T+3: {test3:.4f}, T+5: {test5:.4f}")
print(f"  → {'✅ 失败' if fail3 else '❌ 通过'}")

if fail1 and fail2 and fail3:
    print("\n⎧⎨⎩ 三条全部失败")
    print("V7 = 纯风险预算 + 行为筛选")
    print("  模块1: 流动性乘数（总能量60日均线降仓）")
    print("  模块2: Phase 仓位约束（只限制上限）")
    print("  模块3: 行为信号独立筛选（增持/龙虎榜）")
else:
    print("\n✅ 岭回归路线通过！V7 纳入评分模块")

# 保存结果
result = {
    't3': {'alpha': r3['alpha'], 'cv_r2': r3['cv_r2_mean'], 'test_r2': r3['test_r2'], 
           'stable_cnt': r3['stable_cnt'], 'stable_feats': r3['stable_features']} if r3 else None,
    't5': {'alpha': r5['alpha'], 'cv_r2': r5['cv_r2_mean'], 'test_r2': r5['test_r2'],
           'stable_cnt': r5['stable_cnt'], 'stable_feats': r5['stable_features']} if r5 else None,
    'verdict': 'fail' if (fail1 and fail2 and fail3) else 'pass',
    'n_samples': len(records),
    'features': feature_cols,
    'fail_reason': {
        'cv_r2_too_low': fail1,
        'no_stable_feature': fail2,
        'test_r2_bad': fail3
    }
}
with open('/tmp/ridge_s3_v2_result.json', 'w') as f:
    json.dump(result, f, indent=2, default=str)
print(f"\n结果已保存")

print("\n✅ 完成")
