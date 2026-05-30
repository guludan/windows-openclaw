#!/usr/bin/env python3
"""
R指数 & [B]/[A]酸碱浓度 v3
直接从daily_prices(只取A.前缀股票)计算全市场截面指标
"""
import os, sys, json, argparse
from datetime import datetime, timedelta, date
import numpy as np
sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
import data_finance as df


def compute_pct_change(td, trade_dates_pool=None):
    """计算某日所有A.股票涨跌幅（用close/前close）"""
    # 找上一个交易日
    if trade_dates_pool:
        idx = trade_dates_pool.index(td)
        prev = trade_dates_pool[idx-1] if idx > 0 else td - timedelta(days=1)
    else:
        # 从数据库找上一个交易日
        rows, _ = df._pg_execute(
            "SELECT MAX(trade_date) FROM daily_prices WHERE trade_date < %s AND symbol LIKE 'A.%%'",
            (td,)
        )
        prev = rows[0][0] if rows and rows[0][0] else td - timedelta(days=1)
    
    rows, _ = df._pg_execute("""
        SELECT c.symbol, 
               c.close / NULLIF(p.close, 0) - 1 AS chg_pct,
               c.volume,
               c.close
        FROM daily_prices c
        JOIN daily_prices p ON c.symbol = p.symbol AND p.trade_date = %s
        WHERE c.trade_date = %s AND c.symbol LIKE 'A.%%' 
          AND c.close > 0 AND c.volume > 0
          AND p.close > 0 AND p.volume > 0
    """, (prev, td))
    return rows


def compute_market_r_and_ba(trade_dates):
    if not trade_dates:
        return []
    
    ds_str = trade_dates[0].strftime('%Y-%m-%d')
    de_str = trade_dates[-1].strftime('%Y-%m-%d')
    
    # 先用第一个交易日往前找60天算历史分位数基准
    hist_start = (trade_dates[0] - timedelta(days=90)).strftime('%Y-%m-%d')
    
    # 预计算所有所需交易日的截面统计
    # 先用LAG计算涨跌幅
    all_stats = {}
    bar = max(1, len(trade_dates) // 10)
    
    for i, td in enumerate(trade_dates):
        stocks = compute_pct_change(td, trade_dates)
        if not stocks:
            continue
        
        chgs = np.array([r[1] for r in stocks if r[1] is not None and not np.isnan(r[1]) and not np.isinf(r[1])])
        volumes = np.array([r[2] for r in stocks if r[2] is not None and r[2] > 0])
        
        if len(chgs) < 100:
            continue
        
        adv_ratio = float(np.mean(chgs > 0))
        cs_vol = float(np.std(chgs))
        avg_chg = float(np.mean(chgs))
        median_chg = float(np.median(chgs))
        sbull = float(np.mean(chgs > 0.02))  # 涨>2%
        sbear = float(np.mean(chgs < -0.02)) # 跌>2%
        avg_volume = float(np.mean(volumes)) if len(volumes) > 0 else 0
        
        all_stats[td] = {
            'n': len(chgs),
            'cs_vol': cs_vol,
            'advance_ratio': adv_ratio,
            'avg_chg': avg_chg,
            'median_chg': median_chg,
            'strong_bull': sbull,
            'strong_bear': sbear,
            'avg_volume': avg_volume,
        }
        
        if (i+1) % bar == 0 or i == len(trade_dates)-1:
            print(f"  [{i+1}/{len(trade_dates)}] {td}: {len(chgs):<5}只, 涨跌比={adv_ratio:.2%}, σ={cs_vol*100:.2f}%", end='')
        if 'r_index' in locals():
            print(f" → R指数≈{r_index:.2f}")
        else:
            print()
    
    if not all_stats:
        return []
    
    # 用历史数据算分位数基准
    all_vols = np.array([v['cs_vol'] for v in all_stats.values()])
    all_advs = np.array([v['advance_ratio'] for v in all_stats.values()])
    
    p20_vol, p80_vol = np.percentile(all_vols, [20, 80])
    p20_adv, p80_adv = np.percentile(all_advs, [20, 80])
    
    results = []
    for td in sorted(all_stats.keys()):
        d = all_stats[td]
        cs_vol = d['cs_vol']
        adv_ratio = d['advance_ratio']
        
        # R指数分量
        vol_norm = max(0.01, min(0.99, (cs_vol - p20_vol) / max(p80_vol - p20_vol, 0.001)))
        adv_diverge = abs(adv_ratio - 0.5) * 2
        
        # R指数
        r_inv = vol_norm * 0.5 + adv_diverge * 0.5
        r_index = round(1.0 / max(r_inv, 0.1), 4)
        
        # [B]和[A]
        pos_mom = max(0, d['avg_chg'])
        neg_mom = max(0, -d['avg_chg'])
        b_conc = adv_ratio * (1 + pos_mom * 3)
        a_conc = (1 - adv_ratio) * (1 + neg_mom * 3)
        total = b_conc + a_conc
        b_norm = round(b_conc / total, 4) if total > 0 else 0.5
        a_norm = round(1.0 - b_norm, 4)
        surplus = round(b_norm - a_norm, 4)
        
        # 中和进度 = 今日成交量 vs 近期均值（简化用）
        neutralization = round(min(3.0, d['avg_volume'] / 1e7), 4)
        
        extra = {
            'vol_norm': round(vol_norm, 4),
            'adv_diverge': round(adv_diverge, 4),
            'cs_vol': round(cs_vol, 4),
            'advance_ratio': round(adv_ratio, 4),
            'avg_chg': round(d['avg_chg'], 4),
            'median_chg': round(d['median_chg'], 4),
            'strong_bull': round(d['strong_bull'], 4),
            'strong_bear': round(d['strong_bear'], 4),
            'stock_count': d['n'],
            'neutralization': neutralization,
        }
        
        try:
            df._pg_execute(
                """INSERT INTO market_signals (signal_type, code, trade_date, value, extra, updated_at)
                   VALUES (%s, %s, %s, %s, %s::jsonb, NOW())
                   ON CONFLICT (signal_type, code, trade_date)
                   DO UPDATE SET value = EXCLUDED.value, extra = EXCLUDED.extra, updated_at = NOW()""",
                ('r_index', 'market', td, float(r_index),
                 json.dumps({k: float(v) if isinstance(v, (np.floating,)) else v for k, v in {**extra, 'type': 'r_index'}.items()}, ensure_ascii=False)),
                fetch=False
            )
            df._pg_execute(
                """INSERT INTO market_signals (signal_type, code, trade_date, value, extra, updated_at)
                   VALUES (%s, %s, %s, %s, %s::jsonb, NOW())
                   ON CONFLICT (signal_type, code, trade_date)
                   DO UPDATE SET value = EXCLUDED.value, extra = EXCLUDED.extra, updated_at = NOW()""",
                ('ba_surplus', 'market', td, float(surplus),
                 json.dumps({k: float(v) if isinstance(v, (np.floating,)) else v for k, v in {**extra, 'type': 'ba_surplus', 'b_conc': b_norm, 'a_conc': a_norm, 'neutralization': neutralization}.items()}, ensure_ascii=False)),
                fetch=False
            )
        except Exception as e:
            print(f"  写入失败 {td}: {e}")
            continue
        
        results.append({
            'date': td, 'r_index': r_index, 'b_conc': b_norm, 'a_conc': a_norm,
            'surplus': surplus, 'advance_ratio': adv_ratio, 'neutralization': neutralization
        })
    
    return results


def print_summary(results):
    if not results:
        return
    print(f"\n{'='*80}")
    print(f"📊  R指数 & 酸碱浓度 — 最近{len(results)}天")
    print(f"{'='*80}")
    print(f"{'日期':<12} {'R指数':<8} {'[B]':<8} {'[A]':<8} {'B-A盈余':<10} {'涨跌比':<8} {'截面σ':<8}")
    print(f"{'-'*60}")
    for r in results[-20:]:
        print(f"{str(r['date']):<12} {r['r_index']:<8.2f} {r['b_conc']:<8.2%} {r['a_conc']:<8.2%} {r['surplus']:<+10.2%} {r['advance_ratio']:<8.2%}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=120)
    parser.add_argument('--end-date', type=str, default=None)
    parser.add_argument('--start-date', type=str, default=None)
    args = parser.parse_args()
    
    if args.start_date:
        start_date = datetime.strptime(args.start_date, '%Y%m%d').date()
        end_date = datetime.strptime(args.end_date, '%Y%m%d').date() if args.end_date else date.today()
    else:
        end_date = datetime.strptime(args.end_date, '%Y%m%d').date() if args.end_date else date.today()
        start_date = end_date - timedelta(days=args.days)
    
    print(f"📡 全市场 R指数 & 酸碱浓度 v3 (daily_prices)")
    print(f"   R = 1/(vol_norm×0.5 + diverge×0.5)")
    print(f"   [B] = 涨跌比×(1+正涨跌×3)")
    print(f"   范围: {start_date} ~ {end_date}")
    
    # 获取交易日
    rows, _ = df._pg_execute(
        "SELECT DISTINCT trade_date FROM daily_prices WHERE trade_date >= %s AND trade_date <= %s AND symbol LIKE 'A.%%' ORDER BY trade_date",
        (start_date, end_date)
    )
    trade_dates = [r[0] for r in rows]
    print(f"  交易日: {len(trade_dates)} 天")
    
    results = compute_market_r_and_ba(trade_dates)
    print_summary(results)
    print(f"\n✅ 完成, 共 {len(results)} 个交易日")
