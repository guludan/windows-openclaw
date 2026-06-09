#!/home/tearsyu/miniconda3/envs/tt/bin/python3
"""
真热点 vs 假拉升识别

核心思路：拉升阶段的资金行为会留下痕迹。
- 真热点：拉升时有持续的机构资金流入/跟风盘/板块内共振
- 假拉升：脉冲式放量、板块内部分化严重、拉完就缩量

指标组：
1. 拉升斜率 vs 量能持续性
2. 板块内涨跌比（共振度）
3. 拉升前后量比变化（脉冲 vs 持续）
4. 与大盘的相关性（真热点独立走强，假拉升跟着大盘）
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
from data_finance import _pg_execute
from collections import defaultdict
import numpy as np
import json

HOT_SECTORS = {
    '半导体': {'type': 'tech'},
    '电池': {'type': 'tech'},
    '证券': {'type': 'finance'},
    '白酒': {'type': 'consume'},
    '光伏设备': {'type': 'manufacture'},
    '汽车零部件': {'type': 'manufacture'},
    '软件开发': {'type': 'tech'},
    '通信设备': {'type': 'tech'},
    '消费电子': {'type': 'tech'},
    '医疗服务': {'type': 'health'},
    '军工装备': {'type': 'manufacture'},
    '化学制品': {'type': 'manufacture'},
}

def calc_sector_data(start='2020-01-01', end='2026-06-09'):
    print("[1] Loading sector components and price data...")
    
    rows, cols = _pg_execute("""
        SELECT industry, stock_code FROM industry_components_ths_l1
        WHERE industry = ANY(%s)
    """, (list(HOT_SECTORS.keys()),))
    
    sector_stocks = defaultdict(list)
    for r in rows:
        sector_stocks[r[0]].append('A.' + r[1])
    
    # Get index data for comparison (上证000001, 创业板399006)
    idx_rows, _ = _pg_execute("""
        SELECT symbol, trade_date, close, volume
        FROM daily_prices
        WHERE symbol IN ('A.000001', 'A.399006')
          AND trade_date >= %s AND trade_date <= %s
          AND close > 0
        ORDER BY trade_date, symbol
    """, (start, end))
    
    sh_idx = {}  # date -> close
    cy_idx = {}
    for r in idx_rows:
        if r[0] == 'A.000001':
            sh_idx[r[1]] = r[2]
        else:
            cy_idx[r[1]] = r[2]
    
    all_stocks = [s for stks in sector_stocks.values() for s in stks]
    
    rows, cols = _pg_execute("""
        SELECT symbol, trade_date, close, volume, amount
        FROM daily_prices
        WHERE symbol = ANY(%s)
          AND trade_date >= %s AND trade_date <= %s
          AND close > 0
        ORDER BY trade_date, symbol
    """, (all_stocks, start, end))
    print(f"  Loaded {len(rows)} rows for {len(all_stocks)} stocks")
    
    stock_prices = defaultdict(lambda: defaultdict(float))
    stock_volumes = defaultdict(lambda: defaultdict(float))
    
    for r in rows:
        stock_prices[r[0]][r[1]] = r[2]
        stock_volumes[r[0]][r[1]] = r[3] if r[3] else 0
    
    return sector_stocks, stock_prices, stock_volumes, sh_idx, cy_idx


def analyze_pump_patterns(sector_stocks, stock_prices, stock_volumes, sh_idx, cy_idx):
    """
    核心分析：识别每一段"拉升期"，计算特征，判断是真热点还是假拉升。
    
    拉升期定义：板块5日累计涨幅 > 8%
    """
    print(f"\n{'='*70}")
    print("HOT vs FAKE: Pump Pattern Analysis")
    print(f"{'='*70}")
    
    results = {}
    
    for sec in sorted(HOT_SECTORS.keys()):
        stks = [s for s in sector_stocks[sec] if s in stock_prices]
        if len(stks) < 3:
            continue
        
        all_dates = sorted(set(d for s in stks for d in stock_prices[s].keys()))
        
        # 每日板块等权收益率 + 个股明细
        daily_returns = {}  # date -> float (sector return)
        daily_vol_ratios = {}  # date -> float
        daily_stock_rets = {}  # date -> {symbol: ret}
        daily_advance_pct = {}  # date -> % of stocks that went up
        
        for i, dt in enumerate(all_dates):
            closes = {s: stock_prices[s].get(dt, 0) for s in stks}
            closes = {k: v for k, v in closes.items() if v > 0}
            
            if i == 0 or len(closes) < 3:
                continue
            
            prev_dt = all_dates[i-1]
            prev_closes = {s: stock_prices[s].get(prev_dt, 0) for s in closes.keys()}
            prev_closes = {k: v for k, v in prev_closes.items() if v > 0}
            
            if len(prev_closes) < 3:
                continue
            
            # 个股收益率
            indiv_rets = {}
            for sym in closes:
                if sym in prev_closes and prev_closes[sym] > 0:
                    indiv_rets[sym] = (closes[sym] - prev_closes[sym]) / prev_closes[sym]
            
            if not indiv_rets:
                continue
            
            daily_returns[dt] = float(np.mean(list(indiv_rets.values())))
            daily_stock_rets[dt] = indiv_rets
            
            up_count = sum(1 for v in indiv_rets.values() if v > 0)
            daily_advance_pct[dt] = up_count / len(indiv_rets) if indiv_rets else 0.5
            
            # 量比
            today_vol = sum(stock_volumes[s].get(dt, 0) for s in stks)
            lookback = all_dates[max(0, i-20):i]
            if lookback:
                avg_vol = float(np.mean([sum(stock_volumes[s].get(ld, 0) for s in stks) for ld in lookback]))
                daily_vol_ratios[dt] = today_vol / avg_vol if avg_vol > 0 else 1.0
            else:
                daily_vol_ratios[dt] = 1.0
        
        dates = sorted(daily_returns.keys())
        
        # ============================================================
        # 识别拉升期：5日滚动涨幅 > 8%
        # ============================================================
        pump_periods = []
        i = 0
        while i < len(dates):
            dt = dates[i]
            if i < 4:
                i += 1
                continue
            
            cum5 = sum(daily_returns[dates[j]] for j in range(i-4, i+1))
            if cum5 > 0.08:
                # 找到拉升起点（往前找涨幅开始的日期）
                start_idx = i - 4
                for j in range(i-4, 0, -1):
                    if sum(daily_returns[dates[k]] for k in range(j, i+1)) < 0.02:
                        start_idx = j + 1
                        break
                
                # 找结束点
                end_idx = min(i + 10, len(dates) - 1)
                for j in range(i, min(i + 15, len(dates))):
                    cum_from_start = sum(daily_returns[dates[k]] for k in range(start_idx, j+1))
                    if cum_from_start < max(0.05, cum5 * 0.3):  # 回撤超过70%
                        end_idx = j - 1
                        break
                
                pump_dates = dates[start_idx:end_idx+1]
                if len(pump_dates) >= 3:
                    pump_periods.append(pump_dates)
                    i = end_idx + 1
                    continue
            i += 1
        
        print(f"\n--- {sec} ({HOT_SECTORS[sec]['type']}, {len(stks)} stocks) ---")
        print(f"  拉升期数量: {len(pump_periods)}")
        
        period_features = []
        for pump_dates in pump_periods:
            if len(pump_dates) < 3:
                continue
            
            # 拉升阶段收益率
            pump_ret = sum(daily_returns[d] for d in pump_dates)
            pump_duration = len(pump_dates)
            avg_daily_ret = pump_ret / pump_duration if pump_duration > 0 else 0
            
            # 量能持续性: 拉升后期的量比 / 拉升前期的量比
            mid = pump_duration // 2
            early_vr = np.mean([daily_vol_ratios.get(d, 1.0) for d in pump_dates[:mid]]) if mid > 0 else 1.0
            late_vr = np.mean([daily_vol_ratios.get(d, 1.0) for d in pump_dates[mid:]]) if pump_duration > mid else 1.0
            vol_sustainability = late_vr / early_vr if early_vr > 0 else 1.0
            
            # 板块内涨跌比（共振度）
            advance_ratios = [daily_advance_pct.get(d, 0.5) for d in pump_dates]
            avg_advance = np.mean(advance_ratios)
            
            # 与大盘相关性（取上证）
            sh_ret = 0
            sh_corr_dates = [d for d in pump_dates if d in sh_idx]
            if len(sh_corr_dates) >= 3:
                sh_returns = []
                sec_returns = []
                for k in range(1, len(sh_corr_dates)):
                    dt = sh_corr_dates[k]
                    prev_dt = sh_corr_dates[k-1]
                    sh_r = (sh_idx[dt] - sh_idx[prev_dt]) / sh_idx[prev_dt]
                    sec_r = daily_returns.get(dt, 0)
                    sh_returns.append(sh_r)
                    sec_returns.append(sec_r)
                if len(sh_returns) >= 3:
                    sh_ret = float(np.mean(sh_returns) * pump_duration * 100)  # 换算成同期收益
            
            # 最大单日涨幅（脉冲力度）
            max_daily = max(daily_returns.get(d, 0) for d in pump_dates) * 100
            
            # 拉升后20日表现（验证是真热点还是假拉升）
            last_dt = pump_dates[-1]
            last_idx = dates.index(last_dt) if last_dt in dates else -1
            post20_ret = None
            if last_idx >= 0 and last_idx + 20 < len(dates):
                post20_ret = sum(daily_returns[dates[j]] for j in range(last_idx+1, min(last_idx+21, len(dates)))) * 100
            
            # 拉升后是否跌回起点
            fwd_ret_peak_to_end = None
            if last_idx >= 0:
                peak_val = max(sum(daily_returns[dates[k]] for k in range(0, j+1)) 
                              for j in range(len(pump_dates)))
                final_val = sum(daily_returns[dates[k]] for k in range(0, last_idx+1))
                if last_idx + 60 < len(dates):
                    end_val = sum(daily_returns[dates[k]] for k in range(0, last_idx+61))
                    fwd_ret_peak_to_end = (end_val - peak_val) * 100
            
            is_hot = False
            if post20_ret is not None:
                is_hot = post20_ret > 2 or (vol_sustainability > 0.7 and avg_advance > 0.6)
            
            period_features.append({
                'pump_ret': round(pump_ret*100, 1),
                'duration': pump_duration,
                'avg_daily_ret': round(avg_daily_ret*100, 2),
                'vol_sustainability': round(vol_sustainability, 2),
                'avg_advance': round(avg_advance, 2),
                'max_daily': round(max_daily, 1),
                'post20_ret': round(post20_ret, 1) if post20_ret is not None else None,
                'sh_corr_ret': round(sh_ret, 1),
                'excess_ret': round(pump_ret*100 - sh_ret, 1) if sh_ret is not None else None,
                'is_hot': is_hot,
            })
        
        if not period_features:
            print("  无足够拉升期数据")
            continue
        
        # 分类统计
        hot = [p for p in period_features if p['is_hot']]
        fake = [p for p in period_features if not p['is_hot']]
        
        print(f"  真热点: {len(hot)}次 | 假拉升: {len(fake)}次")
        
        # 真热点 vs 假拉升的特征对比
        if hot or fake:
            print(f"\n  {'特征':<22} {'真热点':<20} {'假拉升':<20} {'判别力':<10}")
            print('-'*72)
            
            features_to_compare = [
                ('pump_ret', '拉升收益率%'),
                ('duration', '拉升天数'),
                ('vol_sustainability', '量能持续性(后/前)'),
                ('avg_advance', '板块涨跌比'),
                ('max_daily', '最大单日%'),
                ('post20_ret', '后20日%'),
                ('excess_ret', '超额大盘%'),
            ]
            
            for key, label in features_to_compare:
                hot_vals = [p[key] for p in hot if p[key] is not None]
                fake_vals = [p[key] for p in fake if p[key] is not None]
                if hot_vals and fake_vals:
                    hot_avg = np.mean(hot_vals)
                    fake_avg = np.mean(fake_vals)
                    # 简单的判别力：差异大小
                    diff = abs(hot_avg - fake_avg) / (np.std(hot_vals) + np.std(fake_vals) + 0.01)
                    print(f"  {label:<22} {hot_avg:<20.2f} {fake_avg:<20.2f} {'✓' if diff > 1 else '~' if diff > 0.5 else '✗':<10}")
                elif hot_vals:
                    print(f"  {label:<22} {np.mean(hot_vals):<20.2f} {'N/A':<20}")
                elif fake_vals:
                    print(f"  {label:<22} {'N/A':<20} {np.mean(fake_vals):<20.2f}")
        
        results[sec] = {
            'type': HOT_SECTORS[sec]['type'],
            'pump_count': len(pump_periods),
            'hot_count': len(hot),
            'fake_count': len(fake),
            'features': period_features,
        }
    
    # ============================================================
    # 跨板块总结
    # ============================================================
    print(f"\n{'='*70}")
    print("CROSS-SECTOR SUMMARY: 真热点 vs 假拉升特征对比")
    print(f"{'='*70}")
    
    all_hot = []
    all_fake = []
    for sec, data in results.items():
        for f in data['features']:
            if f['is_hot']:
                all_hot.append({**f, 'sector': sec, 'type': data['type']})
            else:
                all_fake.append({**f, 'sector': sec, 'type': data['type']})
    
    print(f"\n总计: 真热点 {len(all_hot)}次 | 假拉升 {len(all_fake)}次")
    
    print(f"\n{'特征':<22} {'真热点(n={})'.format(len(all_hot)):<20} {'假拉升(n={})'.format(len(all_fake)):<20} {'差异':<10}")
    print('-'*72)
    
    for key, label in features_to_compare:
        hot_vals = [p[key] for p in all_hot if p[key] is not None]
        fake_vals = [p[key] for p in all_fake if p[key] is not None]
        if hot_vals and fake_vals:
            hot_avg = np.mean(hot_vals)
            fake_avg = np.mean(fake_vals)
            diff_pct = (hot_avg - fake_avg) / abs(fake_avg) * 100 if fake_avg != 0 else 0
            print(f"  {label:<22} {hot_avg:<20.2f} {fake_avg:<20.2f} {diff_pct:+.0f}%")
    
    # 板块类型分析
    print(f"\n{'='*70}")
    print("BY SECTOR TYPE")
    print(f"{'='*70}")
    for stype in ['tech', 'manufacture', 'finance', 'consume', 'health']:
        type_hot = [p for p in all_hot if p['type'] == stype]
        type_fake = [p for p in all_fake if p['type'] == stype]
        if type_hot or type_fake:
            print(f"\n{stype.upper()}: 真热点{len(type_hot)} | 假拉升{len(type_fake)}")
            if type_hot:
                print(f"  真热点: vol_sus={np.mean([p['vol_sustainability'] for p in type_hot]):.2f} "
                      f"adv={np.mean([p['avg_advance'] for p in type_hot]):.2f} "
                      f"max_daily={np.mean([p['max_daily'] for p in type_hot]):.1f}%")
            if type_fake:
                print(f"  假拉升: vol_sus={np.mean([p['vol_sustainability'] for p in type_fake]):.2f} "
                      f"adv={np.mean([p['avg_advance'] for p in type_fake]):.2f} "
                      f"max_daily={np.mean([p['max_daily'] for p in type_fake]):.1f}%")
    
    # 导出完整JSON供后续参考
    with open('/tmp/hot_vs_fake_results.json', 'w') as f:
        json.dump({
            'summary': {
                'total_hot': len(all_hot),
                'total_fake': len(all_fake),
            },
            'features': {k: {
                'hot_avg': round(float(np.mean([p[k] for p in all_hot if p[k] is not None])), 2) if [p[k] for p in all_hot if p[k] is not None] else None,
                'fake_avg': round(float(np.mean([p[k] for p in all_fake if p[k] is not None])), 2) if [p[k] for p in all_fake if p[k] is not None] else None,
            } for k, _ in features_to_compare},
        }, f, indent=2)
    
    print(f"\n{'='*70}")
    print("ACTIONABLE SIGNALS: 如何判断当前拉升是真热点还是假拉升")
    print(f"{'='*70}")
    
    print("""
真热点的特征（买入持有）:
1. 量能持续性 > 0.8（后期量不萎缩）
2. 板块涨跌比 > 0.6（超过60%的股票在涨，板块内共振）
3. 超额大盘 > 5%（独立于大盘走强）
4. 拉升天数 > 5（不是一日游）

假拉升的特征（警惕出货）:
1. 量能持续性 < 0.6（后期缩量严重）
2. 板块涨跌比 < 0.55（指数涨但个股不跟）
3. 脉冲式放量（单日量比>2x，次日就缩）
4. 超额大盘 < 3%（跟着大盘涨，不是独立行情）

综合评分法:
  score = vol_sustainability * 30 + avg_advance * 30 + min(excess_ret/10, 1) * 20 + min(duration/10, 1) * 20
  score > 60: 真热点 ✓
  score < 40: 假拉升/出货 ✗
  score 40-60: 需要更多确认
""")
    
    print("Done!")


if __name__ == '__main__':
    sector_stocks, stock_prices, stock_volumes, sh_idx, cy_idx = calc_sector_data()
    analyze_pump_patterns(sector_stocks, stock_prices, stock_volumes, sh_idx, cy_idx)
