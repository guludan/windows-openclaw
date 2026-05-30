#!/usr/bin/env python3
"""
pre_ban_patterns.py — T-5~T-1 连板前兆特征提取

分析首板股票在涨停前5天的资金痕迹，对比连板股 vs 一日游股的差异。
四个维度：量价收敛、尾盘抢筹、抗跌性、板块预热。
"""
import psycopg2, numpy as np, time, sys, os, json
from datetime import date, timedelta
from collections import defaultdict

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

def main():
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    t0 = time.time()

    # 1. 加载首板 + 连板标记
    print(f"加载首板... ({time.time()-t0:.0f}s)")
    cur.execute("""
        WITH ranked AS (
            SELECT code, trade_date, close, volume, amplitude, ret_0, est_mv, industry,
                LEAD(trade_date, 1) OVER (PARTITION BY code ORDER BY trade_date) AS next_ban,
                LAG(trade_date, 1) OVER (PARTITION BY code ORDER BY trade_date) AS prev_ban_dt
            FROM daily_limit_up
            WHERE is_limit_up = true
              AND trade_date >= '2024-01-01' AND trade_date <= '2026-05-22'
        ),
        first_bans AS (
            SELECT * FROM ranked
            WHERE prev_ban_dt IS NULL OR (trade_date - prev_ban_dt) > 5
        )
        SELECT code, trade_date, close, volume, amplitude, ret_0, est_mv, industry,
               CASE WHEN next_ban IS NOT NULL AND (next_ban - trade_date) <= 3 THEN 1 ELSE 0 END AS is_chain
        FROM first_bans
        ORDER BY trade_date
    """)
    ban_rows = cur.fetchall()
    chain = [r for r in ban_rows if r[8] == 1]
    onetime = [r for r in ban_rows if r[8] == 0]
    print(f"  首板: {len(ban_rows)}, 连板: {len(chain)}, 一日游: {len(onetime)} ({time.time()-t0:.0f}s)")

    # 2. 预载大盘日线（加速抗跌性判断）
    cur.execute("SELECT trade_date, close, open FROM daily_prices WHERE symbol='A.000300'")
    hs300_map = {r[0]: (r[2], r[1]) for r in cur.fetchall()}  # date -> (open, close)

    # 3. 遍历首板取T-5~T-1特征
    FEATS = {
        'vol_shrink': {'chain': [], 'onetime': []},
        'amp_shrink': {'chain': [], 'onetime': []},
        'tail_strong': {'chain': [], 'onetime': []},
        'anti_drop': {'chain': [], 'onetime': []},
        'sector_heat': {'chain': [], 'onetime': []},
        'gap_to_ma5': {'chain': [], 'onetime': []},
        'cumul_ret': {'chain': [], 'onetime': []},
        'vol_ratio': {'chain': [], 'onetime': []},
    }

    total = len(ban_rows)
    for idx, r in enumerate(ban_rows):
        code, dt, close, vol, amp, ret, mv, ind, is_chain = r
        grp = 'chain' if is_chain else 'onetime'
        
        if idx % 2000 == 0:
            print(f"  [{idx}/{total}] {dt} {code} ({time.time()-t0:.0f}s)")

        # T-10 到 T-1（取足够多）
        cur.execute("""
            SELECT trade_date, close, open, high, low, volume
            FROM daily_prices
            WHERE symbol=%s AND trade_date < %s AND trade_date >= %s
            ORDER BY trade_date DESC LIMIT 15
        """, ('A.'+code, dt, dt - timedelta(days=25)))
        hist = cur.fetchall()
        hist.reverse()  # 最早的在前
        
        if len(hist) < 6:
            continue
        
        def t(n):
            """T-n，t(1)=T-1, t(5)=T-5"""
            idx = -(n+1)
            if abs(idx) > len(hist):
                return None
            return hist[idx]
        
        # ===== 1. 量价收敛度 =====
        # 用量均值和振幅均值
        vols_prev, amps_prev = [], []
        for i in range(2, 6):
            h = t(i)
            if h:
                vols_prev.append(h[5])
                prev_c = h[2] / (1 + (h[2]-h[1])/max(h[1], 0.01)) if h[1] > 0 else h[2]
                amps_prev.append((h[3]-h[4]) / max(prev_c, 0.001))
        
        h1 = t(1)
        if h1:
            v1 = h1[5]
            vol_prev_avg = np.mean(vols_prev) if vols_prev else None
            vol_shrink = v1 / vol_prev_avg if vol_prev_avg and vol_prev_avg > 0 else None
            
            a1 = (h1[3]-h1[4]) / max(h1[2]/(1+(h1[2]-h1[1])/max(h1[1],0.01)), 0.001)
            amp_prev_avg = np.mean(amps_prev) if amps_prev else None
            amp_shrink = a1 / amp_prev_avg if amp_prev_avg and amp_prev_avg > 0 else None
            
            if vol_shrink:
                FEATS['vol_shrink'][grp].append(vol_shrink)
            if amp_shrink:
                FEATS['amp_shrink'][grp].append(amp_shrink)
        
        # ===== 2. 尾盘抢筹 =====
        tail_cnt = 0
        for i in range(1, 4):
            h = t(i)
            if h and h[1] > 0:
                spread = h[3] - h[4]
                if spread > 0:
                    rel_pos = (h[2] - h[4]) / spread  # 收盘在日内区间的位置 (0~1)
                    if rel_pos > 0.7:  # 收盘在上1/3
                        tail_cnt += 1
        FEATS['tail_strong'][grp].append(tail_cnt)
        
        # ===== 3. 抗跌性 =====
        anti_cnt = 0
        for i in range(1, 4):
            h = t(i)
            if h and h[0] in hs300_map:
                hs_open, hs_close = hs300_map[h[0]]
                hs_ret = (hs_close - hs_open) / max(hs_open, 0.01) if hs_open > 0 else 0
                if hs_ret < -0.005:  # 大盘跌 >0.5%
                    stk_ret = (h[2] - h[1]) / max(h[1], 0.01)
                    if stk_ret > hs_ret:  # 个股比大盘强
                        anti_cnt += 1
        FEATS['anti_drop'][grp].append(anti_cnt)
        
        # ===== 4. 板块预热 =====
        sector_heat = 0
        if ind:
            cur.execute("""
                SELECT COUNT(*) FROM daily_limit_up
                WHERE industry=%s AND trade_date >= %s AND trade_date < %s
                  AND is_limit_up = true
            """, (ind, dt - timedelta(days=5), dt))
            sector_heat = cur.fetchone()[0]
        FEATS['sector_heat'][grp].append(sector_heat)
        
        # ===== 额外 =====
        closes = [h[2] for h in hist[-5:]]
        ma5 = np.mean(closes) if len(closes) >= 5 else None
        last_c = hist[-1][2]
        gap5 = (last_c - ma5) / ma5 if ma5 and ma5 > 0 else None
        if gap5 is not None:
            FEATS['gap_to_ma5'][grp].append(gap5)
        
        # 累计涨幅 T-5~T-1
        t5_c = t(5)[2] if t(5) else None
        if t5_c and t5_c > 0:
            cumul = (hist[-1][2] - t5_c) / t5_c
            FEATS['cumul_ret'][grp].append(cumul)
            FEATS['vol_ratio'][grp].append(v1 / np.mean([h2[5] for h2 in hist]))
        
        if idx >= 6000:  # 6000条够统计显著性
            break

    # 4. 输出对比
    labels = {
        'vol_shrink': '① 量收缩(T-1/T-5~2均值)',
        'amp_shrink': '② 振幅收缩(T-1/T-5~2均值)',
        'tail_strong': '③ 尾盘抢筹(T-3~T-1, 收盘上1/3天数)',
        'anti_drop': '④ 抗跌性(大盘跌时个股强天数)',
        'sector_heat': '⑤ 板块预热(近5天同行涨停数)',
        'gap_to_ma5': '⑥ 距MA5偏离',
        'cumul_ret': '⑦ 累计涨幅T-5~T-1',
        'vol_ratio': '⑧ 量比(T-1/T-全窗口均值)',
    }

    print(f"\n{'='*75}")
    print(f"  T-1~T-5 区间特征对比：连板 vs 一日游")
    print(f"{'='*75}")

    for feat, name in labels.items():
        c_arr = np.array(FEATS[feat]['chain'])
        o_arr = np.array(FEATS[feat]['onetime'])
        if len(c_arr) < 10 or len(o_arr) < 10:
            print(f"\n{name}: 样本不足 chain={len(c_arr)} onetime={len(o_arr)}")
            continue
        
        print(f"\n{name}:")
        print(f"  连板({len(c_arr)}):  均值={np.mean(c_arr):.4f} 中位={np.median(c_arr):.4f}")
        print(f"  一日游({len(o_arr)}): 均值={np.mean(o_arr):.4f} 中位={np.median(o_arr):.4f}")
        diff = np.mean(c_arr) - np.mean(o_arr)
        dir_sym = '↑' if diff > 0 else '↓'
        # 简单区分度
        overlap = min(np.mean(c_arr) + np.std(c_arr), np.mean(o_arr) + np.std(o_arr)) - max(np.mean(c_arr) - np.std(c_arr), np.mean(o_arr) - np.std(o_arr))
        sep = "✅ 区分度好" if abs(np.mean(c_arr) - np.mean(o_arr)) > np.std(o_arr) * 0.5 else ("⚠️ 弱区分" if abs(np.mean(c_arr) - np.mean(o_arr)) > np.std(o_arr) * 0.2 else "❌ 无区分")
        print(f"  差异: {diff:+.4f}  {dir_sym}  {sep}")

    conn.close()
    print(f"\n🎉 完成 ({time.time()-t0:.0f}s)")

if __name__ == '__main__':
    main()
