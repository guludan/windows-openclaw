#!/usr/bin/env python3
"""
2026年4月 S3 样本外验证 — 用已经完成 T+20 的最新数据验证分类器
"""
import psycopg2, json, numpy as np, pandas as pd
from datetime import date, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

def main():
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    print("=" * 60)
    print("样本外验证：2026年4月S3 × signal_qualifier")
    print("=" * 60)
    
    # 1) 加载 S3 信号 + T+20 收益
    cur.execute("""
        SELECT e.level, e.date, e.extra,
               p0.close as close_0, p20.close as close_20
        FROM pool_energy e
        LEFT JOIN LATERAL (
            SELECT close FROM daily_prices
            WHERE symbol = substring(e.level, 6) AND trade_date = e.date LIMIT 1
        ) p0 ON true
        LEFT JOIN LATERAL (
            SELECT close FROM daily_prices dp
            WHERE dp.symbol = substring(e.level, 6) AND dp.trade_date > e.date
            ORDER BY dp.trade_date OFFSET 19 LIMIT 1
        ) p20 ON true
        WHERE e.level LIKE 'code:A.%%'
          AND position('s3' in e.extra->>'sigs') > 0
          AND e.date BETWEEN '2026-04-01' AND '2026-04-22'
          AND p0.close IS NOT NULL AND p20.close IS NOT NULL
        ORDER BY e.date
    """)
    s3_rows = cur.fetchall()
    print(f"[1] S3 信号(含T+20): {len(s3_rows)} 条\n")
    
    if not s3_rows:
        print("  无数据，退出")
        return
    
    # 2) 辅助数据
    incr_dict = defaultdict(dict)
    cur.execute("""
        SELECT code, change_date, 
               SUM(abs_change) FILTER (WHERE is_increase) as incr,
               SUM(abs_change) FILTER (WHERE is_decrease) as decr
        FROM incr_decr
        GROUP BY code, change_date
    """)
    for r in cur.fetchall():
        incr_dict[r[0]][r[1]] = {'incr': r[2] or 0, 'decr': r[3] or 0}
    
    lhb_dict = defaultdict(set)
    cur.execute("SELECT code, trade_date FROM csi_lhb_detail GROUP BY code, trade_date")
    for r in cur.fetchall():
        lhb_dict[r[0]].add(r[1])
    
    # 3) 逐条分类
    results = []
    
    for r in s3_rows:
        level, dt, extra_json = r[0], r[1], r[2]
        close_0, close_20 = r[3], r[4]
        ret_20 = close_20 / close_0 - 1
        code = level[7:]  # 'code:A.xxxxx' → 'xxxxx'
        extra = extra_json  # 已经是 dict
        
        # --- 龙虎榜 ---
        if code in lhb_dict and dt in lhb_dict[code]:
            results.append({'type': 'LHB', 'score': 0.0, 'ret_20': ret_20, 'reason': '龙虎榜'})
            continue
        
        # --- 增减持 ---
        incr7 = decr7 = incr14 = 0
        for d in range(1, 8):
            dd = dt - timedelta(days=d)
            if code in incr_dict and dd in incr_dict[code]:
                incr7 += incr_dict[code][dd]['incr']
                decr7 += incr_dict[code][dd]['decr']
        for d in range(8, 15):
            dd = dt - timedelta(days=d)
            if code in incr_dict and dd in incr_dict[code]:
                incr14 += incr_dict[code][dd]['incr']
        
        if incr7 > 0 and decr7 == 0:
            score = 2.0 if incr7 >= 5000 else 1.5
            typ = 'INC_H' if incr7 >= 5000 else 'INC_M'
            results.append({'type': typ, 'score': score, 'ret_20': ret_20, 'reason': f'增持{incr7}'})
            continue
        if decr7 > incr7:
            results.append({'type': 'DECR', 'score': 0.5, 'ret_20': ret_20, 'reason': f'减持{decr7}'})
            continue
        if incr14 > 0 and decr7 == 0:
            results.append({'type': 'INC_W', 'score': 1.3, 'ret_20': ret_20, 'reason': f'14d增持{incr14}'})
            continue
        
        # --- 量价微观 ---
        sym = 'A.' + code
        cur2 = conn.cursor()
        cur2.execute("""
            SELECT trade_date, volume, high, low, open, close
            FROM daily_prices 
            WHERE symbol = %s AND trade_date BETWEEN %s AND %s
            ORDER BY trade_date
        """, (sym, dt - timedelta(days=250), dt + timedelta(days=60)))
        rows2 = cur2.fetchall()
        cur2.close()
        
        if len(rows2) < 80:
            results.append({'type': 'UNK', 'score': 1.0, 'ret_20': ret_20, 'reason': '数据不足'})
            continue
        
        df = pd.DataFrame(rows2, columns=['trade_date','volume','high','low','open','close'])
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df.set_index('trade_date', inplace=True)
        
        if dt in df.index:
            idx = df.index.get_loc(dt)
        else:
            mask = df.index <= pd.Timestamp(dt)
            if not mask.any():
                results.append({'type': 'UNK', 'score': 1.0, 'ret_20': ret_20, 'reason': '日期不匹配'})
                continue
            idx = mask.sum() - 1
        if isinstance(idx, slice): idx = idx.stop - 1
        if idx < 60 or idx + 20 >= len(df):
            results.append({'type': 'UNK', 'score': 1.0, 'ret_20': ret_20, 'reason': '数据不足'})
            continue
        
        post_vol = df['volume'].iloc[idx+1:idx+4].mean() / max(df['volume'].iloc[idx], 1)
        
        if post_vol > 0.8:
            results.append({'type': 'A_HOLD', 'score': 1.0, 'ret_20': ret_20, 'reason': f'post_vol={post_vol:.2f}>0.8'})
        elif post_vol < 0.5:
            results.append({'type': 'A_STARVE', 'score': 0.2, 'ret_20': ret_20, 'reason': f'post_vol={post_vol:.2f}<0.5'})
        else:
            results.append({'type': 'C_MID', 'score': 0.7, 'ret_20': ret_20, 'reason': f'post_vol={post_vol:.2f}居中'})
    
    # 4) 输出
    df_r = pd.DataFrame(results)
    print(f"\n{'类型':<10} {'次数':<6} {'T+20均值':<10} {'胜率':<8} {'score':<6} {'注入加权':<10}")
    print("-" * 60)
    
    all_ret = []
    for typ in ['INC_H','INC_M','A_HOLD','C_MID','A_STARVE','DECR','INC_W','UNK','LHB']:
        grp = df_r[df_r['type'] == typ]
        if len(grp) < 1: continue
        mean_r = grp['ret_20'].mean()
        win = (grp['ret_20'] > 0).mean()
        score = grp['score'].iloc[0]
        weighted = mean_r * score
        all_ret.extend(grp['ret_20'].tolist())
        print(f"{typ:<10} {len(grp):<6} {mean_r:+8.2%}  {win:<7.1%}  {score:<6.1f} {weighted:+8.2%}")
    
    if all_ret:
        print("-" * 60)
        m = np.mean(all_ret)
        print(f"{'基线(全量)':<10} {len(all_ret):<6} {m:+8.2%}  {(np.array(all_ret)>0).mean():<7.1%}")

    # 加权组合
    weighted_all = (df_r['ret_20'] * df_r['score']).sum() / df_r['score'].sum()
    print(f"\n注入乘数加权: +{weighted_all*100:.2f}%")
    print(f"vs 基线: +{np.mean(all_ret)*100:.2f}%")
    print(f"提升: +{(weighted_all - np.mean(all_ret))*100:.2f}%\n")
    
    # 逐条打印
    print("详细清单:")
    print(f"{'代码':<10} {'日期':<12} {'类型':<10} {'score':<6} {'T+20收益':<10} {'原因'}")
    print("-" * 70)
    for i, row in df_r.iterrows():
        code = s3_rows[i][0][7:]
        dt = s3_rows[i][1]
        print(f"A.{code:<7} {dt!s:<12} {row['type']:<10} {row['score']:<6.1f} {row['ret_20']:+8.2%}  {row['reason']}")
    
    conn.close()

if __name__ == '__main__':
    main()
