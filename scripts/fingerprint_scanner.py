#!/usr/bin/env python3
"""
指纹提取+规避模型 — 用已知的"机构控盘票"特征，扫描全市场排除同类
"""

import sys; sys.path.insert(0,'scripts')
import psycopg2, urllib.request, json
from datetime import datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

# 已知控盘票模版（从大众公用+嘉化能源提取）
KNOWN_BAD_TEMPLATE = {
    # 大众公用指纹（控盘特征显著）
    '600635': {
        # 价格特征
        'price_range': (4.77, 6.42),  # 近60天价格区间
        'range_width': 1.65,  # 25.6%
        'close_to_20d_low': True,  # 目前接近60天低点
        
        # 量特征
        'vol_avg_60d': 612149,
        'vol_today_ratio': 0.5,  # 今天量只有均值的0.5倍
        'vol_spike_days': [  # 放量日（机构对倒特征）
            '2026-05-26',  # 236万，140万（最高）
            '2026-05-27',  # 223万
            '2026-06-03',  # 104万
        ],
        
        # K线特征
        'candle_pattern': '有序下跌',  # 每天0.5-2%，从6.42一路到4.77
        'has_shadows': True,  # 有下影线
        'has_tail_drop': True,  # 尾盘杀跌特征
        
        # 波动特征
        'return_std': 2.60,  # 日收益率标准差
        '60d_return': -21.8,  # 60天跌21.8%
    }
}

def scan_market():
    """扫全市场找相同指纹的票"""
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # 所有股价>2元<100元的活跃A股
    cur.execute("""
        SELECT d.symbol, s.name, d.close
        FROM daily_prices d
        JOIN stock_list s ON s.code = SUBSTRING(d.symbol FROM 3)
        WHERE d.trade_date = '2026-06-12'
        AND d.close > 2 AND d.close < 100
        ORDER BY d.symbol
    """)
    stocks = cur.fetchall()
    print(f"扫描{len(stocks)}只...")
    
    suspicious = []
    for sym, name, close in stocks:
        code = sym[2:]
        
        # 查近60天
        cur.execute(f"""
            SELECT trade_date, close, volume
            FROM daily_prices WHERE symbol='{sym}'
            ORDER BY trade_date DESC LIMIT 60
        """)
        rows = cur.fetchall()
        if len(rows) < 30:
            continue
        
        closes = [float(r[1]) for r in rows]
        vols = [float(r[2]) for r in rows]
        dates = [str(r[0]) for r in rows]
        
        # 时间区间跌幅
        pct_60d = (closes[0] - closes[-1]) / closes[-1] * 100
        
        # 波动率
        import statistics
        rets = [(closes[i]-closes[i+1])/closes[i+1]*100 for i in range(len(closes)-1)]
        vola = statistics.stdev(rets)
        
        # 量比
        avg_vol = sum(vols)/len(vols)
        vol_ratio = vols[0] / avg_vol if avg_vol > 0 else 0
        
        # 均价
        avg_close = sum(closes)/len(closes)
        
        # 特征分（与大众公用模版匹配度）
        score = 0
        reasons = []
        
        # 1. 跌幅区间特征：60天跌15%-30%
        if -30 < pct_60d < -15:
            score += 3
            reasons.append(f"60d跌{pct_60d:.0f}%")
        
        # 2. 有序下跌特征：波动率适中(1.5-3.5)，不是暴涨暴跌
        if 1.5 < vola < 3.5:
            score += 2
            reasons.append(f"波动率{vola:.1f}")
        
        # 3. 缩量特征：今日量比<0.8
        if vol_ratio < 0.8:
            score += 2
            reasons.append(f"缩量{vol_ratio:.1f}x")
        
        # 4. 接近60天低点
        if closes[0] <= sum(sorted(closes)[:5])/5 * 1.05:  # 接近5个最低点的均值
            score += 2
            reasons.append("近60天低区")
        
        # 5. 低PE但不是ST
        cur.execute(f"SELECT pe FROM stock_list WHERE code='{code}'")
        pe_r = cur.fetchone()
        pe = float(pe_r[0]) if pe_r and pe_r[0] else 0
        if 5 < pe < 30:
            score += 1
            reasons.append(f"PE{pe:.0f}")
        
        # 6. 区间内价差幅度15-30%
        price_range = max(closes) - min(closes)
        range_pct = price_range / min(closes) * 100 if min(closes) > 0 else 0
        if 15 < range_pct < 35:
            score += 1
            reasons.append(f"区间幅度{range_pct:.0f}%")
        
        # 综合判断：得分>=9的可疑
        if score >= 9:
            suspicious.append((code, name, close, pct_60d, vola, vol_ratio, score, pe, ' '.join(reasons)))
    
    suspicious.sort(key=lambda x: x[6], reverse=True)
    
    print(f"\n=== 疑似控盘票TOP 30（特征分排序，越高越像大众公用）===")
    print(f"{'代码':<8} {'名称':<10} {'现价':<8} {'60d%':<8} {'波动':<6} {'量比':<6} {'得分':<5} {'PE':<6} {'原因'}")
    print("-"*90)
    for s in suspicious[:30]:
        print(f"{s[0]:<8} {s[1]:<10} {s[2]:<8.2f} {s[3]:+7.1f}%{s[4]:<8.2f} {s[5]:<6.1f}x {s[6]:<5} {s[7]:<6.0f} {s[8]}")
    
    conn.close()
    return suspicious

if __name__ == '__main__':
    bad = scan_market()
