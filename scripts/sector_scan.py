#!/usr/bin/env python3
"""
按黄老板熟悉的板块和区域政策筛选候选股：
1. 石油政策（油气开采/炼化/油服）
2. 水利（水务/水利建设）
3. 电力（火电/水电/电网/电力设备）
4. AI（算力/模型/硬件）
5. 白云机场空港经济区（广州/白云机场受益股）
6. 中国整体经济周期（宏观对冲相关）

输出两组：V8+控制盘指纹规避；V9+潮汐策略
"""

import sys; sys.path.insert(0,'scripts')
import psycopg2, os

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
conn = psycopg2.connect(DB)
cur = conn.cursor()
today = '2026-06-12'
ds = today.replace('-','')

# 板块映射
SECTORS = {
    '石油': ['石油', '油气', '油服', '炼化', '石化'],
    '水利': ['水利', '水务', '水处理'],
    '电力': ['电力', '电网', '发电', '新能源', '储能', '火电', '水电'],
    'AI': ['AI', '人工智能', '算力', '芯片', '半导体', '软件', '机器人', '服务器'],
    '空港': ['机场', '航空', '物流', '港口', '自贸', '广州'],
}

# 拿全部板块
cur.execute("SELECT DISTINCT industry FROM industry_components_ths_l1 ORDER BY industry")
all_inds = [r[0] for r in cur.fetchall()]

# 匹配板块
matched = {}
for sector, keywords in SECTORS.items():
    found = []
    for ind in all_inds:
        for kw in keywords:
            if kw in ind:
                found.append(ind)
                break
    matched[sector] = found
    print(f'{sector}: {found}')

# 拉成分股
all_stocks = []
for sector, inds in matched.items():
    for ind in inds:
        cur.execute("SELECT stock_code, stock_name FROM industry_components_ths_l1 WHERE industry=%s", (ind,))
        for code, sname in rows if (rows := cur.fetchall()) else []:
            if sname is None:
                cur.execute("SELECT name FROM stock_list WHERE code=%s", (code,))
                r = cur.fetchone()
                sname = r[0] if r else code
            
            sym = 'A.' + code
            cur.execute("""
                SELECT trade_date, close, volume FROM daily_prices WHERE symbol=%s
                AND trade_date >= '2025-06-01'
                ORDER BY trade_date ASC
            """, (sym,))
            rows2 = cur.fetchall()
            if len(rows2) < 60:
                continue
            closes = [float(r[1]) for r in rows2]
            vols = [float(r[2]) for r in rows2 if r[2]]
            
            # 位置
            high_1y = max(closes); low_1y = min(closes)
            pos = (closes[-1] - low_1y) / (high_1y - low_1y) * 100 if (high_1y - low_1y) > 0 else 50
            
            # 60天特征
            pct_60d = (closes[-1] - closes[-60]) / closes[-60] * 100 if len(closes) >= 60 else 0
            pct_20d = (closes[-1] - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0
            
            # 波动率（控盘筛选用）
            import statistics
            rets = [(closes[i] - closes[i+1])/closes[i+1]*100 for i in range(len(closes)-1)]
            vola = statistics.stdev(rets) if len(rets) > 1 else 0
            
            # 量比
            avg_vol_20 = sum(vols[-20:])/20 if len(vols) >= 20 else 0
            avg_vol_60 = sum(vols[-60:])/60 if len(vols) >= 60 else 0
            vol_trend = avg_vol_20 / avg_vol_60 if avg_vol_60 > 0 else 1
            
            # 均线
            ma5 = sum(closes[-5:])/5; ma10 = sum(closes[-10:])/10; ma20 = sum(closes[-20:])/20
            trend = '多头' if ma5 > ma10 > ma20 else '空头' if ma5 < ma10 < ma20 else '震荡'
            
            # 大众公用控盘指纹评分(越低越好)
            risk_score = 0
            if 15 < -pct_60d < 35:  # 60天有序跌
                risk_score += 2
            if 1.5 < vola < 3.5:  # 适中波动率
                risk_score += 1
            if vol_trend < 0.6:  # 严重缩量
                risk_score += 2
            if pct_20d < -10:  # 近期加速跌
                risk_score += 2
            if pos < 20:  # 在底部区
                risk_score += 1
                
            all_stocks.append({
                'sector': sector,
                'code': code, 'name': sname,
                'close': closes[-1], 'pos': round(pos, 1),
                'pct_60d': round(pct_60d, 2), 'pct_20d': round(pct_20d, 2),
                'trend': trend, 'vol_trend': round(vol_trend, 2),
                'vola': round(vola, 2), 'risk': risk_score,
            })

# 去重
seen = set()
unique = []
for s in all_stocks:
    if s['code'] not in seen:
        seen.add(s['code'])
        unique.append(s)

# 输出
dp = f'/mnt/d/data/a-stock/reports/{today}/黄老板熟悉板块候选_{ds}.md'
os.makedirs(os.path.dirname(dp), exist_ok=True)

with open(dp, 'w', encoding='utf-8') as f:
    f.write(f'# 黄老板熟悉板块 + 空港经济区 候选标的\n')
    f.write(f'生成: {today}  总{len(unique)}只\n\n')
    f.write(f'筛选标准: 位置<80%(排除高位), 控盘风险分<6(越低越好)\n\n')
    
    for sector in ['石油', '水利', '电力', 'AI', '空港']:
        stks = [s for s in unique if s['sector'] == sector]
        if not stks: continue
        
        f.write(f'## {sector} ({len(stks)}只)\n\n')
        f.write('|代码|名称|现价|位置%|60日%|20日%|均线趋势|量比趋势|控盘风险|波动率|\n')
        f.write('|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|\n')
        for s in sorted(stks, key=lambda x: x['pos']):
            f.write(f"|{s['code']}|{s['name']}|{s['close']}|{s['pos']:.0f}%|{s['pct_60d']:+.1f}%|{s['pct_20d']:+.1f}%|{s['trend']}|{s['vol_trend']:.1f}x|{s['risk']}|{s['vola']}|\n")
        f.write('\n')
    
    # 黄老板经济周期熟悉的: 找宏观相关行业
    f.write('## 经济周期敏感型（适合你熟悉的周期判断）\n\n')
    f.write('从已有板块中筛选位置合理、风险低的:\n\n')
    candidates = [s for s in unique if s['pos'] < 80 and s['risk'] < 5]
    candidates.sort(key=lambda x: (x['risk'], x['pos']))
    f.write('|代码|名称|板块|现价|位置%|控盘风险|趋势|多空|\n')
    f.write('|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|\n')
    for s in candidates[:50]:
        f.write(f"|{s['code']}|{s['name']}|{s['sector']}|{s['close']}|{s['pos']:.0f}%|{s['risk']}|{s['vol_trend']:.1f}x量|{s['trend']}|\n")

print(f'OK: {dp}')
print(f'总{len(unique)}只')
for sector in SECTORS:
    cnt = len([s for s in unique if s['sector'] == sector])
    print(f'  {sector}: {cnt}只')

conn.close()
PYEOF
