#!/usr/bin/env python3
"""
qualifier_backtest.py — signal_qualifier 全量回测

对 pool_energy 中所有 S3 信号跑信号定性分类器，统计各类别的 T+20 收益表现。
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, psycopg2.extras
import numpy as np
import pandas as pd
import json, time
from datetime import datetime, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

# 复现 SignalQualifier 的核心逻辑，但批量优化为单次 SQL
def batch_compute_volume_pattern(conn, codes, dates):
    """
    批量读取量价数据，避免逐股逐次查PG
    返回 {code: {date: info}}
    """
    if not codes:
        return {}
    
    cur = conn.cursor()
    results = {}
    
    # 按股票分组查
    for code, d_list in codes.items():
        d_min = min(d_list) - timedelta(days=250)
        d_max = max(d_list) + timedelta(days=60)
        
        cur.execute("""
            SELECT trade_date, close, volume, amount, high, low, open
            FROM daily_prices 
            WHERE symbol = %s AND trade_date BETWEEN %s AND %s
            ORDER BY trade_date
        """, ('A.'+code, d_min, d_max))
        rows = cur.fetchall()
        
        if len(rows) < 60:
            for d in d_list:
                results.setdefault(code, {})[d] = None
            continue
        
        df = pd.DataFrame(rows, columns=['trade_date','close','volume','amount','high','low','open'])
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df.set_index('trade_date', inplace=True)
        df['log_ret'] = np.log(df['close'] / df['close'].shift(1)).clip(-0.11, 0.11)
        df['vol_20d'] = df['log_ret'].rolling(20).std() * np.sqrt(252)
        
        for d in d_list:
            # 定位 idx
            if d in df.index:
                idx = df.index.get_loc(d)
            else:
                mask = df.index <= pd.Timestamp(d)
                if not mask.any():
                    results.setdefault(code, {})[d] = None
                    continue
                idx = mask.sum() - 1
            
            if isinstance(idx, slice):
                idx = idx.stop - 1
            if idx < 60 or idx + 20 >= len(df):
                results.setdefault(code, {})[d] = None
                continue
            
            # 规则1: pre_vol
            vol_5_20 = df['volume'].iloc[idx-20:idx-5].mean()
            vol_20_60 = df['volume'].iloc[idx-60:idx-20].mean()
            pre_vol = vol_5_20 / max(vol_20_60, 1)
            
            # 规则2: 日内承接
            hl = df['high'].iloc[idx]
            ll = df['low'].iloc[idx]
            op = df['open'].iloc[idx]
            cl = df['close'].iloc[idx]
            if hl > ll:
                close_pos = (cl - ll) / (hl - ll)
            else:
                close_pos = 0.5
            up_shadow = hl - max(op, cl)
            down_shadow = min(op, cl) - ll
            resistance = up_shadow / max(down_shadow, 0.001)
            has_cj = close_pos > 0.35 or resistance > 0.3
            
            # 规则3: post_vol
            vol_d0 = df['volume'].iloc[idx]
            vol_d1_d3 = df['volume'].iloc[idx+1:idx+4].mean()
            post_vol = vol_d1_d3 / max(vol_d0, 1)
            future_low = df['low'].iloc[idx+1:idx+6].min()
            breach_low = future_low < ll
            
            results.setdefault(code, {})[d] = {
                'pre_vol_ratio': round(pre_vol, 2),
                'close_pos': round(close_pos, 2),
                'resistance': round(resistance, 2),
                'has_cj': has_cj,
                'post_vol_ratio': round(post_vol, 2),
                'breach_low': breach_low,
            }
    
    cur.close()
    return results


def classifiy_s3(incr_idx, lhb_idx, vol_info, code, d):
    """
    完整分类决策逻辑（与 signal_qualifier.py 一致）
    """
    # 龙虎榜
    on_lhb = (code in lhb_idx and d in lhb_idx[code])
    if on_lhb:
        return {'type': 'LHB', 'score': 0.0}
    
    # 增减持
    incr_7d = decr_7d = incr_14d = 0
    for delta in range(1, 8):
        dd = d - timedelta(days=delta)
        if code in incr_idx and dd in incr_idx[code]:
            incr_7d += incr_idx[code][dd]['total_incr']
            decr_7d += incr_idx[code][dd]['total_decr']
    for delta in range(1, 15):
        dd = d - timedelta(days=delta)
        if code in incr_idx and dd in incr_idx[code]:
            incr_14d += incr_idx[code][dd]['total_incr']
    
    if incr_7d > 0 and decr_7d == 0:
        return {'type': 'INC_H' if incr_7d >= 5000 else 'INC_M',
                'score': 2.0 if incr_7d >= 5000 else 1.5}
    if decr_7d > incr_7d:
        return {'type': 'DECR', 'score': 0.5}
    if incr_14d > 0 and decr_7d == 0:
        return {'type': 'INC_W', 'score': 1.3}
    
    if vol_info is None:
        return {'type': 'UNK', 'score': 1.0}
    
    pv = vol_info['pre_vol_ratio']
    has_cj = vol_info['has_cj']
    pv_vol = vol_info['post_vol_ratio']
    
    # 修正规则 v2 (基于post_vol主轴的统计发现):
    # post_vol > 0.8 (放量持续) → 收益最好但仓位已打满，不做boost
    # post_vol < 0.5 (迅速萎缩) → 趋势延续向下的概率高，抑制
    if pv_vol < 0.5:
        return {'type': 'A_STARVE', 'score': 0.0}
    if pv_vol > 0.8:
        return {'type': 'A_HOLD', 'score': 1.0}
    return {'type': 'C_MID', 'score': 0.7}


def main():
    t0 = time.time()
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    print("=" * 60)
    print("signal_qualifier 全量回测", datetime.now().strftime('%Y-%m-%d %H:%M'))
    print("=" * 60)
    
    # 1) 加载 S3 信号 + 收益
    print("\n[1] 加载 S3 信号...")
    cur.execute("""
        SELECT e.level, e.date, 
               p0.close as close_0, p20.close as close_20
        FROM pool_energy e
        LEFT JOIN LATERAL (
            SELECT close FROM daily_prices
            WHERE symbol = substring(e.level, 6) AND trade_date = e.date
            LIMIT 1
        ) p0 ON true
        LEFT JOIN LATERAL (
            SELECT close FROM daily_prices dp
            WHERE dp.symbol = substring(e.level, 6) AND dp.trade_date > e.date
            ORDER BY dp.trade_date OFFSET 19 LIMIT 1
        ) p20 ON true
        WHERE e.level LIKE 'code:%%'
          AND (e.extra->>'sigs') LIKE '%s3%'
          AND e.date >= '2020-01-01'
          AND p0.close IS NOT NULL AND p20.close IS NOT NULL
        ORDER BY e.date
    """)
    rows = cur.fetchall()
    print(f"  {len(rows)} 条 S3 信号（有 T+20 收益）")
    
    if not rows:
        print("  无信号，退出")
        return
    
    # 2) 加载辅助数据索引
    print("\n[2] 加载辅助数据索引...")
    cur.execute("""
        SELECT code, change_date, 
               COALESCE(SUM(abs_change) FILTER (WHERE is_increase), 0) as total_incr,
               COALESCE(SUM(abs_change) FILTER (WHERE is_decrease), 0) as total_decr
        FROM incr_decr
        GROUP BY code, change_date
    """)
    incr_idx = defaultdict(dict)
    for r in cur.fetchall():
        incr_idx[r[0]][r[1]] = {'total_incr': r[2], 'total_decr': r[3]}
    print(f"  增减持: {sum(len(v) for v in incr_idx.values())} 条记录")
    
    cur.execute("""
        SELECT code, trade_date
        FROM csi_lhb_detail
        GROUP BY code, trade_date
    """)
    lhb_idx = defaultdict(dict)
    for r in cur.fetchall():
        lhb_idx[r[0]][r[1]] = True
    print(f"  龙虎榜: {sum(len(v) for v in lhb_idx.values())} 条记录")
    
    # 3) 批量查询量价数据
    print("\n[3] 批量加载量价数据...")
    code_dates = defaultdict(set)
    for r in rows:
        code = r[0][7:]
        d = r[1]
        if hasattr(d, 'date'): d = d.date()
        code_dates[code].add(d)
    
    vol_info = batch_compute_volume_pattern(conn, 
        {code: list(dates) for code, dates in code_dates.items()},
        None)
    print(f"  已计算 {sum(len(v) for v in vol_info.values())} 条量价特征")
    
    # 4) 分类 + 回测
    print("\n[4] 分类回测...")
    results = defaultdict(list)
    qualifier_dist = defaultdict(list)  # type -> [(ret_20, score)]
    
    # 特征分析 bucket
    all_feats = []
    
    for r in rows:
        code = r[0][7:]
        d = r[1]
        if hasattr(d, 'date'): d = d.date()
        close_0 = r[2]
        close_20 = r[3]
        ret_20 = close_20 / close_0 - 1
        
        info = vol_info.get(code, {}).get(d, None)
        cls = classifiy_s3(incr_idx, lhb_idx, info, code, d)
        
        qualifier_dist[cls['type']].append(cls['score'])
        results[cls['type']].append(ret_20)
        
        if info is not None and cls['type'] not in ('INC_H','INC_M','INC_W','DECR','LHB','UNK'):
            all_feats.append({
                'type': cls['type'],
                'ret_20': ret_20,
                'pre_vol': info['pre_vol_ratio'],
                'has_cj': info['has_cj'],
                'post_vol': info['post_vol_ratio'],
                'breach_low': info['breach_low'],
            })
    
    # 4.5) 特征诊断（A/B/C 类内部特征分布）
    if all_feats:
        print(f"\n{'='*70}")
        print("量价微观结构特征诊断（非一手信号 S3）:")
        adf = pd.DataFrame(all_feats)
        
        print(f"\n{'类型':<8} {'n':<6} {'pre_vol中位':<12} {'has_cj比例':<12} {'post_vol中位':<14} {'breach_low%':<12} {'T+20均值':<10}")
        print("-"*70)
        for typ in ['A_STARVE','C_MID','A_HOLD']:
            grp = adf[adf['type']==typ]
            if len(grp) < 3: continue
            print(f"{typ:<8} {len(grp):<6} {grp['pre_vol'].median():<12.2f} {grp['has_cj'].mean():<12.1%} {grp['post_vol'].median():<14.2f} {grp['breach_low'].mean():<12.1%} {grp['ret_20'].mean():+8.2%}")
        
        print(f"\n各特征与收益的相关性:")
        print(f"  pre_vol × ret_20: ρ={adf['pre_vol'].corr(adf['ret_20']):+.3f}")
        print(f"  post_vol × ret_20: ρ={adf['post_vol'].corr(adf['ret_20']):+.3f}")
    
    # 5) 输出
    print(f"\n{'='*70}")
    print(f"{'类型':<10} {'次数':<8} {'T+20均值':<10} {'T+20中位':<10} {'胜率':<8} {'注入乘数':<10}")
    print(f"{'-'*70}")
    
    # 按 score 排序输出
    ordered = sorted(results.keys(), key=lambda k: -np.mean(results[k]))
    
    all_r20 = []
    for typ in ordered:
        grp = results[typ]
        mean_r = np.mean(grp)
        med_r = np.median(grp)
        win_r = sum(1 for r in grp if r > 0) / len(grp)
        avg_s = np.mean(qualifier_dist[typ])
        all_r20.extend(grp)
        print(f"{typ:<10} {len(grp):<8} {mean_r:+8.2%}  {med_r:+8.2%}  {win_r:<7.1%}  {avg_s:.2f}")
    
    # 基线
    if all_r20:
        print(f"{'-'*70}")
        print(f"{'基线':<10} {len(all_r20):<8} {np.mean(all_r20):+8.2%}  {np.median(all_r20):+8.2%}  {sum(1 for r in all_r20 if r>0)/len(all_r20):<7.1%}  {'—':<10}")
    
    # 6) 加权组合收益（按 score 加权 vs 均衡）
    print(f"\n{'='*70}")
    print("策略对比（全量 S3）:")
    
    # 原始 baseline: 全量平均
    baseline_r = np.mean(all_r20)
    
    # score 加权: 每笔按 score 线性叠加
    weighted_r = 0
    raw_idx = 0
    for typ in ordered:
        for ret in results[typ]:
            score = qualifier_dist[typ][raw_idx]
            weighted_r += ret * score
            raw_idx += 1
        raw_idx = 0  # reset per type
    # 正确做法: 逐笔加权
    all_scores = []
    all_s3_ret = []
    for typ in ordered:
        for i, ret in enumerate(results[typ]):
            all_scores.append(qualifier_dist[typ][i] if len(qualifier_dist[typ]) > i else 0.7)
            all_s3_ret.append(ret)
    
    if all_scores:
        weighted_ret = np.average(all_s3_ret, weights=all_scores)
    else:
        weighted_ret = 0
    
    # 高信心组（score >= 1.5）
    high_ret = [r for i, r in enumerate(all_s3_ret) if all_scores[i] >= 1.5]
    high_win = sum(1 for r in high_ret if r > 0) / len(high_ret) if high_ret else 0
    
    print(f"{'策略':<20} {'次数':<8} {'T+20均值':<10} {'胜率':<8}")
    print(f"{'-'*50}")
    print(f"{'基线(全量)':<20} {len(all_s3_ret):<8} {baseline_r:+8.2%}  {sum(1 for r in all_s3_ret if r>0)/len(all_s3_ret):<7.1%}")
    print(f"{'注入乘数加权':<20} {len(all_s3_ret):<8} {weighted_ret:+8.2%}  {'—':<8}")
    if high_ret:
        print(f"{'高信心组(s>=1.5)':<20} {len(high_ret):<8} {np.mean(high_ret):+8.2%}  {high_win:<7.1%}")
    
    conn.close()
    elapsed = time.time() - t0
    print(f"\n耗时: {elapsed:.0f}s")


if __name__ == '__main__':
    main()
