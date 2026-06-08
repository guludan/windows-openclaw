#!/usr/bin/env python3
"""
护盘模式检测 — 每日全市场扫描
新增: 大跌接筹模式 (跌>2%但卖/买比<0.5 = 机构硬接)
"""
import psycopg2, numpy as np, json, os
from datetime import date

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
conn = psycopg2.connect(DB)
cur = conn.cursor()

TODAY = date.today()
OUTDIR = '/home/tearsyu/.openclaw/workspace/memory/reports'

# 获取今日market_snapshots数据
cur.execute("""
    SELECT ms.symbol, ms.close, ms.volume, ms.amount, ms.high, ms.low, 
           ms.buy_vol, ms.sell_vol, ms.buy_amount, ms.sell_amount,
           dp.pre_close
    FROM market_snapshots ms
    JOIN daily_prices dp ON dp.symbol = ms.symbol AND dp.trade_date = ms.trade_date
    WHERE ms.trade_date = %s
""", (TODAY,))
rows = cur.fetchall()

# 取大盘今日涨幅
cur.execute("SELECT close FROM daily_prices WHERE symbol='A.000001' AND trade_date=%s", (TODAY,))
sh_r = cur.fetchone()
sh_close = float(sh_r[0]) if sh_r else None

results = []

for r in rows:
    sym, close, vol, amt, high, low, buy_v, sell_v, buy_a, sell_a, pre_close = r
    code = sym.split('.')[-1] if '.' in sym else sym
    if not all([close, pre_close, buy_v, sell_v]): continue
    close = float(close)
    pre = float(pre_close)
    chg = (close / pre - 1) * 100
    
    ratio = sell_v / max(buy_v, 1)
    vol_f = float(vol) if vol else 0
    
    # ① 缩量抗跌模式
    if chg > -1.0 and chg > -1.5:  # 跌幅小于1%
        mode1 = True
    else:
        mode1 = False
    
    # ② 大跌接筹模式（新的）
    mode2 = chg <= -2.0 and ratio < 0.5
    
    # ③ 放量护盘
    mode3 = chg > -2.0 and ratio < 0.8 and vol_f > 0
    
    score = 0
    reasons = []
    if mode2:
        score += 3
        reasons.append(f"大跌接筹(+3)")
    if mode1:
        score += 2
        reasons.append(f"缩量抗跌(+2)")
    if mode3:
        score += 1
        reasons.append(f"放量护盘(+1)")
    
    if score >= 2:
        results.append({
            'code': code, 'score': score, 'chg': round(chg, 2),
            'ratio': round(ratio, 2), 'type': '大跌接筹' if mode2 else ('缩量抗跌' if mode1 else '放量护盘'),
            'close': round(close, 2), 'vol': int(vol_f), 'reasons': ';'.join(reasons)
        })

results.sort(key=lambda x: x['score'], reverse=True)
top30 = results[:30]

print(f"\n{'='*60}")
print(f"  护盘模式全市场扫描 — {TODAY}")
print(f"{'='*60}")
print(f"  总扫描: {len(rows)}只 | 护盘信号: {len(results)}只")
print(f"  大盘今日: {sh_close:.2f}")

print(f"\n  各模式分布:")
mode1_n = sum(1 for r in results if '缩量抗跌' in r['type'])
mode2_n = sum(1 for r in results if '大跌接筹' in r['type'])
print(f"  缩量抗跌: {mode1_n} | 大跌接筹: {mode2_n}")

print(f"\n  大跌接筹 TOP 10 (跌>2%但买盘碾压):")
print(f"  {'代码':>8} {'得分':>4} {'涨跌':>6} {'卖/买':>6} {'收盘':>6} {'类型'}")
print(f"  {'-'*45}")
for r in [x for x in results if '大跌接筹' in x['type']][:10]:
    print(f"  {r['code']:>8} {r['score']:>4} {r['chg']:>+5.1f}% {r['ratio']:>4.1f}x {r['close']:>6.2f} {r['type']}")

# 持仓交叉验证
print(f"\n  持仓交叉验证:")
holdings = {'600273': '嘉化能源', '002073': '软控股份', '600635': '大众公用'}
for hc, hn in holdings.items():
    for r in results:
        if r['code'] == hc:
            print(f"  {hn}({hc}): {r['type']}, 得分{r['score']}, 卖/买{r['ratio']:.2f}x")
            break
    else:
        print(f"  {hn}({hc}): ❌ 未识别")

# 保存结果
out = os.path.join(OUTDIR, f'support_scan_{TODAY}.json')
with open(out, 'w') as f:
    json.dump({
        'date': str(TODAY),
        'total': len(rows),
        'signals': len(results),
        'mode2_count': mode2_n,
        'mode2': [{'code': r['code'], 'chg': r['chg'], 'ratio': r['ratio']} for r in results if '大跌接筹' in r['type']],
        'top10': [r['code'] for r in results[:10]]
    }, f, ensure_ascii=False)
print(f"\n  保存: {out}")

conn.close()
PYEOF
