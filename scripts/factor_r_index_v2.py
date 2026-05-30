#!/usr/bin/env python3
"""
R指数 & [B]/[A]酸碱浓度计算脚本 v2
直接用market_snapshots表（含截面涨跌幅/换手率/振幅等现成数据）

核心指标：
1. R指数（心理距离）— 截面波动率倒数 × 换手率倒数 × 一致性偏离度
2. [B]浓度（多头能量）— 涨跌幅为正的比例 × 正动量强度
3. [A]浓度（空头能量）— 涨跌幅为负的比例 × 负动量强度
4. 中和进度 — 成交量比

输出到：market_signals (signal_type='r_index', 'ba_surplus')
"""
import os, sys, json, argparse
from datetime import datetime, timedelta, date
sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
import data_finance as df
import numpy as np


def compute_market_r_and_ba(trade_dates):
    """批量计算每日R指数和酸碱浓度"""
    
    if not trade_dates:
        return []
    
    start_ds = trade_dates[0].strftime('%Y-%m-%d')
    start_prev = (trade_dates[0] - timedelta(days=60)).strftime('%Y-%m-%d')
    
    # 获取market_snapshots的截面数据
    rows, cols = df._pg_execute("""
        SELECT trade_date,
               COUNT(*) AS stock_count,
               STDDEV(change_pct) AS cs_vol,          -- 截面波动率
               AVG(turnover) AS avg_turnover,           -- 平均换手率
               AVG(amplitude) AS avg_amplitude,         -- 平均振幅
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY change_pct) AS median_pct,  -- 中位涨跌
               SUM(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*), 0) AS advance_ratio,
               SUM(CASE WHEN change_pct > 2 THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*), 0) AS strong_bull_ratio,
               SUM(CASE WHEN change_pct < -2 THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*), 0) AS strong_bear_ratio,
               AVG(change_pct) AS avg_pct
        FROM market_snapshots
        WHERE trade_date >= %s AND trade_date <= %s
          AND change_pct IS NOT NULL AND turnover IS NOT NULL
        GROUP BY trade_date
        ORDER BY trade_date
    """, (start_ds, trade_dates[-1].strftime('%Y-%m-%d')))
    
    if not rows:
        print("  market_snapshots无数据")
        return []
    
    # 转为dict by trade_date
    cs_data = {}
    for r in rows:
        td = r[0]
        cs_data[td] = {
            'stock_count': r[1], 'cs_vol': r[2], 'avg_turnover': r[3],
            'avg_amplitude': r[4], 'median_pct': r[5], 'advance_ratio': r[6],
            'strong_bull': r[7], 'strong_bear': r[8], 'avg_pct': r[9]
        }
    
    # 获取近60日均换手率（用于换手率比率）
    hist_rows, _ = df._pg_execute("""
        SELECT trade_date, AVG(turnover) AS avg_turn,
               STDDEV(change_pct) AS cs_vol,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY change_pct) AS median_pct
        FROM market_snapshots
        WHERE trade_date >= %s AND trade_date < %s
          AND change_pct IS NOT NULL AND turnover IS NOT NULL
        GROUP BY trade_date
        ORDER BY trade_date
    """, (start_prev, start_ds))
    
    hist_turn = np.array([r[1] for r in hist_rows if r[1] is not None])
    hist_csvol = np.array([r[2] for r in hist_rows if r[2] is not None])
    
    avg_hist_turn = np.mean(hist_turn) if len(hist_turn) > 0 else 1.0
    p20_turn = np.percentile(hist_turn, 20) if len(hist_turn) > 0 else 0.5
    p80_turn = np.percentile(hist_turn, 80) if len(hist_turn) > 0 else 2.0
    
    p20_vol = np.percentile(hist_csvol, 20) if len(hist_csvol) > 0 else 1.0
    p80_vol = np.percentile(hist_csvol, 80) if len(hist_csvol) > 0 else 3.0
    
    results = []
    for td in trade_dates:
        if td not in cs_data:
            continue
        
        d = cs_data[td]
        cs_vol = d['cs_vol'] if d['cs_vol'] else 1.0
        avg_turn = d['avg_turnover'] if d['avg_turnover'] else 0.5
        adv_ratio = d['advance_ratio'] if d['advance_ratio'] else 0.5
        sbull = d['strong_bull'] if d['strong_bull'] else 0.1
        sbear = d['strong_bear'] if d['strong_bear'] else 0.1
        
        # R指数分量
        vol_norm = max(0.01, min(0.99, (cs_vol - p20_vol) / max(p80_vol - p20_vol, 0.01)))
        turn_norm = max(0.01, min(0.99, (avg_turn - p20_turn) / max(p80_turn - p20_turn, 0.01)))
        adv_diverge = abs(adv_ratio - 0.5) * 2
        
        # R指数 = 1 / (高波动×高换手×强单边)
        r_inv = vol_norm * 0.35 + turn_norm * 0.25 + adv_diverge * 0.4
        r_index = round(1.0 / max(r_inv, 0.1), 4)
        
        # [B]和[A]浓度
        pos_mom = max(0, d['avg_pct'] or 0)
        neg_mom = max(0, -(d['avg_pct'] or 0))
        
        b_conc = adv_ratio * (1 + pos_mom * 2)
        a_conc = (1 - adv_ratio) * (1 + neg_mom * 2)
        
        total = b_conc + a_conc
        b_norm = round(b_conc / total, 4) if total > 0 else 0.5
        a_norm = round(1.0 - b_norm, 4)
        surplus = round(b_norm - a_norm, 4)
        
        # 中和进度（换手率相对历史均值）
        neutralization = round(min(3.0, avg_turn / max(avg_hist_turn, 0.1)), 4)
        
        extra = {
            'vol_norm': round(vol_norm, 4),
            'turn_norm': round(turn_norm, 4),
            'adv_diverge': round(adv_diverge, 4),
            'cs_vol': round(cs_vol, 4),
            'advance_ratio': round(adv_ratio, 4),
            'avg_pct': round(d['avg_pct'], 4) if d['avg_pct'] else 0,
            'strong_bull': round(sbull, 4),
            'strong_bear': round(sbear, 4),
            'median_pct': round(d['median_pct'], 4) if d['median_pct'] else 0,
            'stock_count': d['stock_count'],
            'neutralization': neutralization,
        }
        
        # 写入R指数
        try:
            df._pg_execute(
                """INSERT INTO market_signals (signal_type, code, trade_date, value, extra, updated_at)
                   VALUES (%s, %s, %s, %s, %s::jsonb, NOW())
                   ON CONFLICT (signal_type, code, trade_date)
                   DO UPDATE SET value = EXCLUDED.value, extra = EXCLUDED.extra, updated_at = NOW()""",
                ('r_index', 'market', td, r_index,
                 json.dumps({**extra, 'type': 'r_index'}, ensure_ascii=False)),
                fetch=False
            )
            
            # 写入酸碱盈余
            df._pg_execute(
                """INSERT INTO market_signals (signal_type, code, trade_date, value, extra, updated_at)
                   VALUES (%s, %s, %s, %s, %s::jsonb, NOW())
                   ON CONFLICT (signal_type, code, trade_date)
                   DO UPDATE SET value = EXCLUDED.value, extra = EXCLUDED.extra, updated_at = NOW()""",
                ('ba_surplus', 'market', td, surplus,
                 json.dumps({**extra, 'type': 'ba_surplus', 'b_conc': b_norm, 'a_conc': a_norm, 'neutralization': neutralization}, ensure_ascii=False)),
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
    print(f"{'日期':<12} {'R指数':<8} {'[B]':<8} {'[A]':<8} {'B-A盈余':<10} {'涨跌比':<8} {'中和进度':<10}")
    print(f"{'-'*60}")
    for r in results[-20:]:
        print(f"{str(r['date']):<12} {r['r_index']:<8.2f} {r['b_conc']:<8.2%} {r['a_conc']:<8.2%} {r['surplus']:<+10.2%} {r['advance_ratio']:<8.2%} {r['neutralization']:<10.2f}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=60)
    parser.add_argument('--end-date', type=str, default=None)
    args = parser.parse_args()
    
    end_date = datetime.strptime(args.end_date, '%Y%m%d').date() if args.end_date else date.today()
    start_date = end_date - timedelta(days=args.days)
    
    print(f"📡 全市场 R指数 & 酸碱浓度")
    print(f"   方法: R = 1/(vol_norm×0.35 + turn_norm×0.25 + diverge×0.4)")
    print(f"   方法: [B] = 涨跌比×(1+σ+×2), [A] = (1-涨跌比)×(1+σ-×2)")
    print(f"   范围: {start_date} ~ {end_date}")
    
    rows, _ = df._pg_execute(
        "SELECT DISTINCT trade_date FROM market_snapshots WHERE trade_date >= %s AND trade_date <= %s ORDER BY trade_date",
        (start_date, end_date)
    )
    trade_dates = [r[0] for r in rows]
    print(f"  交易日: {len(trade_dates)} 天")
    
    results = compute_market_r_and_ba(trade_dates)
    
    print_summary(results)
    print(f"\n✅ 完成, 共 {len(results)} 个交易日")
