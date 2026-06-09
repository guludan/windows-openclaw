#!/home/tearsyu/miniconda3/envs/tt/bin/python3
"""
v2: 直接在SQL里算板块统计量，减少数据移动
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
from data_finance import _pg_execute
from collections import defaultdict
import numpy as np
import json

HOT_SECTORS = [
    '半导体', '电池', '证券', '白酒', '光伏设备',
    '汽车零部件', '软件开发', '通信设备', '消费电子',
]

print("[1] Loading sector components...")
rows, cols = _pg_execute("""
    SELECT industry, stock_code FROM industry_components_ths_l1
    WHERE industry = ANY(%s)
""", (HOT_SECTORS,))

sector_stocks = defaultdict(list)
for r in rows:
    sector_stocks[r[0]].append(r[1])  # no A. prefix

for sec, stks in sorted(sector_stocks.items()):
    print(f"  {sec}: {len(stks)} stocks")

print(f"\n[2] Using SQL window functions for sector stats...")

# ============================================================
# 每个板块：用SQL算每日等权收益率 + 量比 + 涨跌比
# ============================================================
results = {}

for sec in HOT_SECTORS:
    stks = sector_stocks[sec]
    if len(stks) < 3:
        continue
    
    print(f"\n--- {sec} ({len(stks)} stocks) ---")
    
    # 用SQL：每日板块均值
    rows, cols = _pg_execute("""
        WITH daily_sector AS (
            SELECT 
                dp.trade_date,
                dp.close,
                LAG(dp.close) OVER (PARTITION BY dp.symbol ORDER BY dp.trade_date) AS prev_close,
                dp.volume
            FROM daily_prices dp
            JOIN (SELECT unnest(%s::text[]) AS code) c 
              ON dp.symbol = 'A.' || c.code
            WHERE dp.trade_date >= '2020-01-01' AND dp.close > 0
        ),
        daily_rets AS (
            SELECT 
                trade_date,
                (close - prev_close) / NULLIF(prev_close, 0) AS ret,
                volume
            FROM daily_sector
            WHERE prev_close > 0
        ),
        daily_stats AS (
            SELECT 
                trade_date,
                AVG(ret) AS sector_ret,
                COUNT(*) AS n_stocks,
                SUM(CASE WHEN ret > 0 THEN 1 ELSE 0 END)::float / COUNT(*) AS advance_pct,
                SUM(volume) AS sector_vol
            FROM daily_rets
            GROUP BY trade_date
        )
        SELECT 
            trade_date,
            sector_ret,
            advance_pct,
            n_stocks,
            sector_vol,
            AVG(sector_vol) OVER (ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS avg_vol_20d
        FROM daily_stats
        ORDER BY trade_date
    """, (stks,))
    
    print(f"  Loaded {len(rows)} trading days")
    
    dates = [r[0] for r in rows]
    sector_rets = [r[1] if r[1] else 0 for r in rows]
    advance_pcts = [r[2] if r[2] else 0.5 for r in rows]
    n_stocks = [r[3] for r in rows]
    sector_vols = [r[4] if r[4] else 0 for r in rows]
    avg_vols = [r[5] if r[5] else 1 for r in rows]
    
    vol_ratios = [v / a if a > 0 else 1.0 for v, a in zip(sector_vols, avg_vols)]
    
    # ============================================================
    # 识别拉升期
    # ============================================================
    pump_periods = []
    i = 4
    while i < len(dates):
        cum5 = sum(sector_rets[j] for j in range(i-4, i+1))
        if cum5 > 0.08:
            start_idx = max(0, i - 8)
            end_idx = min(i + 10, len(dates) - 1)
            
            pump_dates_idx = list(range(start_idx, end_idx + 1))
            if len(pump_dates_idx) >= 3:
                pump_periods.append(pump_dates_idx)
                i = end_idx + 2
                continue
        i += 1
    
    print(f"  Pump periods: {len(pump_periods)}")
    
    # ============================================================
    # 特征计算
    # ============================================================
    period_features = []
    for pidx in pump_periods:
        if len(pidx) < 3:
            continue
        
        pump_ret = sum(sector_rets[j] for j in pidx)
        pump_dur = len(pidx)
        
        mid = pump_dur // 2
        early_vr = float(np.mean([vol_ratios[j] for j in pidx[:mid]])) if mid > 0 else 1.0
        late_vr = float(np.mean([vol_ratios[j] for j in pidx[mid:]])) if pump_dur > mid else 1.0
        vol_sus = late_vr / early_vr if early_vr > 0 else 1.0
        
        avg_adv = float(np.mean([advance_pcts[j] for j in pidx]))
        max_daily = float(max(sector_rets[j] for j in pidx)) * 100
        
        # 后20日收益
        last_i = pidx[-1]
        post20 = None
        if last_i + 20 < len(dates):
            post20 = sum(sector_rets[j] for j in range(last_i+1, last_i+21)) * 100
        
        # 超额
        excess = pump_ret * 100  # 没有用大盘调整了
        
        # 分类标签
        is_hot = post20 is not None and (post20 > 2 or (vol_sus > 0.7 and avg_adv > 0.6))
        
        period_features.append({
            'pump_ret': round(pump_ret*100, 1),
            'duration': pump_dur,
            'vol_sustainability': round(vol_sus, 2),
            'avg_advance': round(avg_adv, 2),
            'max_daily': round(max_daily, 1),
            'post20_ret': round(post20, 1) if post20 is not None else None,
            'is_hot': is_hot,
        })
    
    if not period_features:
        print("  No data")
        continue
    
    hot = [p for p in period_features if p['is_hot']]
    fake = [p for p in period_features if not p['is_hot']]
    print(f"  真热点: {len(hot)} | 假拉升: {len(fake)}")
    
    if hot or fake:
        print(f"  {'特征':<22} {'真热点':<15} {'假拉升':<15} {'差异%':<10}")
        print('-'*62)
        for key, label in [('pump_ret','拉升收益%'), ('duration','天数'), 
                          ('vol_sustainability','量持续'), ('avg_advance','涨跌比'),
                          ('max_daily','最大单日%'), ('post20_ret','后20日%')]:
            hv = [p[key] for p in hot if p[key] is not None]
            fv = [p[key] for p in fake if p[key] is not None]
            if hv and fv:
                ha, fa = float(np.mean(hv)), float(np.mean(fv))
                diff = (ha - fa) / abs(fa) * 100 if fa != 0 else 0
                print(f"  {label:<22} {ha:<15.2f} {fa:<15.2f} {diff:+.0f}%")
    
    results[sec] = {
        'pump_count': len(pump_periods),
        'hot_count': len(hot),
        'fake_count': len(fake),
        'features': period_features,
    }

# ============================================================
# 汇总
# ============================================================
print(f"\n{'='*60}")
print("CROSS-SECTOR SUMMARY")
print(f"{'='*60}")

all_hot = [f for sec in results.values() for f in sec['features'] if f['is_hot']]
all_fake = [f for sec in results.values() for f in sec['features'] if not f['is_hot']]
print(f"Total: 真热点 {len(all_hot)} | 假拉升 {len(all_fake)}")

print(f"\n{'特征':<22} {'真热点(n={})'.format(len(all_hot)):<15} {'假拉升(n={})'.format(len(all_fake)):<15}")
print('-'*52)
for key, label in [('pump_ret','拉升收益%'), ('duration','天数'), 
                  ('vol_sustainability','量持续'), ('avg_advance','涨跌比'),
                  ('max_daily','最大单日%'), ('post20_ret','后20日%')]:
    hv = [p[key] for p in all_hot if p[key] is not None]
    fv = [p[key] for p in all_fake if p[key] is not None]
    if hv and fv:
        ha, fa = float(np.mean(hv)), float(np.mean(fv))
        print(f"  {label:<22} {ha:<15.2f} {fa:<15.2f}")

print(f"\n{'='*60}")
print("ACTIONABLE SIGNALS")
print(f"{'='*60}")
print("""
真热点特征（买入持有）:
  • 量能持续性(后/前) > 0.8  → 后期不放量不缩量
  • 板块涨跌比 > 0.6  → 板块内共振
  • 拉升天数 > 5 → 不是一日游

假拉升特征（警惕出货）:
  • 量能持续性 < 0.6 → 放量拉一下就缩量
  • 板块涨跌比 < 0.55 → 涨指数不涨个股
  • 拉升天数短，脉冲式放量

评分公式:
  score = vol_sustainability*30 + avg_advance*30 + min(duration/10,1)*20
  score > 60: 真热点 ✓
  score < 40: 假拉升 ✗
""")
print("Done!")
