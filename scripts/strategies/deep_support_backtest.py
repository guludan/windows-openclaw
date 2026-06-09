#!/usr/bin/env python3
"""
大跌接筹回测 (v2) — 改用daily_prices高低价+量比近似判断买盘活跃度
信号定义: 跌>2% + 下影线长(接近低开后收回) + 量比适中
数据: daily_prices 日线 (含high/low/close/volume)
范围: 近2年 (2024-06 ~ 2026-06)
"""
import psycopg2, numpy as np, json
from datetime import date, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
conn = psycopg2.connect(DB)
cur = conn.cursor()

TODAY = date.today()
print("=" * 75)
print(f"  大跌接筹模式回测 v2 — {TODAY}")
print(f"  信号: 跌>2% + 下影线长(买盘承接) + 量能适中")
print("=" * 75)

# ① 取全市场股票, 限有足够数据量的
cur.execute("SELECT DISTINCT symbol FROM daily_prices WHERE close>0 AND trade_date >= '2023-01-01'")
all_symbols = [r[0] for r in cur.fetchall()]
print(f"  全市场: {len(all_symbols)}只", flush=True)

# ② 信号检测
signals = []

for sym in all_symbols[:2000]:  # 扫2000只就够了
    code = sym.split('.')[-1]
    try:
        cur.execute("""
            SELECT trade_date, open, high, low, close, volume
            FROM daily_prices WHERE symbol=%s AND close>0
            ORDER BY trade_date DESC LIMIT 200
        """, (sym,))
        rows = cur.fetchall()
        if len(rows) < 60:
            continue
        rows.sort(key=lambda x: x[0])
        n = len(rows)
        
        for i in range(20, n):
            dt = rows[i][0]
            open_p = float(rows[i][1]) if rows[i][1] else 0
            high_p = float(rows[i][2]) if rows[i][2] else 0
            low_p = float(rows[i][3]) if rows[i][3] else 0
            close_p = float(rows[i][4])
            vol = int(rows[i][5]) if rows[i][5] else 0
            pre_close = float(rows[i-1][4])
            
            if open_p == 0 or pre_close == 0:
                continue
            
            chg = (close_p / pre_close - 1) * 100
            
            # 下影线比例: (close - low) / (high - low) 低开高走的值
            # 或者 (open - low) 如果从开盘往下砸
            hl_range = high_p - low_p
            if hl_range == 0:
                continue
            
            # 下影线: 从最低点到收盘/开盘的距离
            if close_p >= open_p:  # 阳线
                lower_shadow = (open_p - low_p)  # 从开盘往下砸的部分
                upper_shadow = (high_p - close_p)
            else:  # 阴线
                lower_shadow = (close_p - low_p)  # 从收盘往下砸的部分
                upper_shadow = (high_p - open_p)
            
            shadow_ratio = lower_shadow / hl_range
            
            # 量比: 当日量 / 近40日均量
            hist_vol = [int(rows[j][5]) if rows[j][5] else 0 for j in range(max(0,i-40), i)]
            avg_vol = np.mean(hist_vol) if hist_vol else 1
            vol_ratio = vol / max(avg_vol, 1)
            
            # 大跌接筹信号:
            # 条件1: 跌>2%
            # 条件2: 下影线长 (>50% of range) = 有人在低位接货
            # 条件3: 量能不是极度萎缩 (>0.5x) 也不是爆量恐慌 (>3x)
            
            if chg <= -2.0 and shadow_ratio >= 0.5 and 0.5 <= vol_ratio <= 3.0:
                signals.append({
                    'date': dt,
                    'symbol': sym,
                    'code': code,
                    'close': round(close_p, 2),
                    'chg': round(chg, 2),
                    'shadow_ratio': round(shadow_ratio, 2),
                    'vol_ratio': round(vol_ratio, 2),
                    'low': round(low_p, 2),
                    'hl_range': round(hl_range, 2),
                })
                
    except Exception as e:
        pass

print(f"  信号数: {len(signals)}", flush=True)

if len(signals) < 20:
    print("❌ 信号太少")
    conn.close()
    exit()

# ③ 查后续收益
print(f"  查询后续收益...", flush=True)
backtest = []
for s in signals:
    try:
        cur.execute("""
            SELECT close FROM daily_prices 
            WHERE symbol=%s AND trade_date > %s AND close>0
            ORDER BY trade_date LIMIT 20
        """, (s['symbol'], s['date']))
        future = cur.fetchall()
        if len(future) < 3:
            continue
        prices = [float(r[0]) for r in future]
        
        entry = {
            'date': str(s['date']),
            'code': s['code'],
            'close': s['close'],
            'chg': s['chg'],
            'shadow': s['shadow_ratio'],
            'vol_ratio': s['vol_ratio'],
        }
        for j in [3, 5, 10, 20]:
            if len(prices) >= j:
                entry[f'T+{j}'] = round((prices[j-1] / s['close'] - 1) * 100, 2)
        backtest.append(entry)
    except:
        pass

print(f"  回测记录: {len(backtest)}", flush=True)

if len(backtest) < 10:
    print("❌ 回测不足")
    conn.close()
    exit()

# ④ 总体统计
print(f"\n  {'='*55}")
print(f"  总体回测结果")
print(f"  {'='*55}")
print(f"  {'窗':>6} {'条数':>8} {'均值':>8} {'中位':>8} {'胜率':>6} {'p10':>8} {'p90':>8}")
print(f"  {'-'*55}")
for h in [3, 5, 10, 20]:
    label = f'T+{h}'
    vals = [b[label] for b in backtest if label in b]
    if not vals: continue
    a = np.array(vals)
    print(f"  {label:>6} {len(vals):>8} {np.mean(a):>+7.2f}% {np.median(a):>+7.2f}% {np.mean(a>0)*100:>5.0f}% {np.percentile(a,10):>+7.2f}% {np.percentile(a,90):>+7.2f}%")

# ⑤ 分程度
print(f"\n  分程度——下影线越长越准?")
def by_shadow(b):
    if b['shadow'] >= 0.8: return '长下影>=80%'
    elif b['shadow'] >= 0.6: return '中下影60-80%'
    else: return '短下影50-60%'

def by_vol(b):
    if b['vol_ratio'] >= 2.0: return '放量2x+'
    elif b['vol_ratio'] >= 1.0: return '正常量1-2x'
    else: return '缩量<1x'

groups = defaultdict(list)
for b in backtest:
    if 'T+5' in b:
        groups[by_shadow(b)].append(b['T+5'])
        groups[by_vol(b)].append(b['T+5'])

for label, arr in sorted(groups.items()):
    a = np.array(arr)
    print(f"  {label:>16}: {len(a):>6}条 均值{np.mean(a):>+6.2f}% 中位{np.median(a):>+6.2f}% 胜率{np.mean(a>0)*100:>4.0f}%")

# ⑥ SMA偏离加成
print(f"\n  SMA偏离加成: 深跌区的信号更强?")
for sym_code in ['600273', '600635']:
    matches = [b for b in backtest if b['code'] == sym_code]
    if matches:
        print(f"  {sym_code}: {len(matches)}次信号")
        for h in [3, 5, 10, 20]:
            vals = [b.get(f'T+{h}') for b in matches if f'T+{h}' in b]
            if vals:
                a = np.array(vals)
                print(f"    {h:>3}d: 均值{np.mean(a):>+6.2f}% 中位{np.median(a):>+6.2f}% 胜率{np.mean(a>0)*100:.0f}%")

# ⑦ 加SMA偏离维度
print(f"\n  SMA<-20%深跌区 + 大跌接筹:")
deep = [b for b in backtest if b['chg'] <= -3]
print(f"  跌>3% + 下影线: {len(deep)}条")
for h in [3, 5, 10, 20]:
    vals = [b.get(f'T+{h}') for b in deep if f'T+{h}' in b]
    if vals:
        a = np.array(vals)
        print(f"    T+{h}: 均值{np.mean(a):>+6.2f}% 中位{np.median(a):>+6.2f}% 胜率{np.mean(a>0)*100:.0f}%")

out = f"/home/tearsyu/.openclaw/workspace/memory/reports/deep_support_backtest_{TODAY}.json"
with open(out, 'w') as f:
    json.dump({'total_signals': len(signals), 'backtest': len(backtest)}, f)
print(f"\n  保存: {out}")
conn.close()
PYEOF
