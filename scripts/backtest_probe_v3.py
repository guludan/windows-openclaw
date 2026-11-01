#!/usr/bin/env python3
"""
黄老板试盘规则精确回测 v2
==========================
定义（按黄老板口径）:
  试盘 = 放量(vol/vol_ma5>=1.3) + 日内拉升后回落(高点相对昨收涨幅>=2%)
  回落 = (high - close) / high  (从最高点到收盘的跌幅)

规则:
  回落>3%  → 抛压过大 → 次日(T+1)砸盘
  回落<=3% → 抛压可承受 → T+3之内有拉升

窗口: 2025-09-01 ~ 2026-06-26 全市场(排除ST)
输出: 分组T+1/T+3(收盘vs期间最高)表现
"""
import psycopg2
from collections import defaultdict

PG = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

def main():
    conn = psycopg2.connect(PG)
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, trade_date, open, high, low, close, volume
        FROM daily_prices
        WHERE trade_date >= '2025-08-01' AND trade_date <= '2026-06-26' AND symbol LIKE 'A.%'
        ORDER BY symbol, trade_date
    """)
    daily = defaultdict(list)
    for sym, td, o, h, l, c, v in cur.fetchall():
        daily[sym].append((str(td), o, h, l, c, v))
    cur.execute("SELECT code FROM stock_list WHERE name LIKE '%%ST%%' OR name LIKE '%%退%%'")
    st = {'A.' + r[0] for r in cur.fetchall()}
    conn.close()
    print(f"{len(daily)} 只加载完成")

    g_hard = []  # 回落>3% (抛压大)
    g_ok = []    # 回落0~3% (抛压可承受)
    for sym, k in daily.items():
        if sym in st or len(k) < 30:
            continue
        for i in range(5, len(k) - 4):
            td, o, h, l, c, v = k[i]
            pc = k[i-1][4]  # 昨收
            if not all([o, h, c, v, pc]):
                continue
            vol5 = sum(x[5] for x in k[i-5:i]) / 5
            if vol5 == 0:
                continue
            if v / vol5 < 1.3:
                continue  # 必须放量
            intraday_up = (h - pc) / pc  # 高点相对昨收的拉升幅度
            if intraday_up < 0.02:
                continue  # 必须有拉升动作(冲高>=2%)
            drawdown = (h - c) / h  # 回落幅度: 最高点→收盘
            if drawdown <= 0.005:
                continue  # 无回落(收在最高)不算试盘
            # 未来收益
            r1 = (k[i+1][4] - c) / c
            r3_close = (k[i+3][4] - c) / c
            r3_high = max(k[i+1][2], k[i+2][2], k[i+3][2])  # T+3内最高
            r3_max = (r3_high - c) / c
            rec = (sym, td, drawdown, intraday_up, r1, r3_close, r3_max)
            if drawdown > 0.03:
                g_hard.append(rec)
            else:
                g_ok.append(rec)

    def stat(recs, label):
        n = len(recs)
        if n == 0:
            print(f"\n{label}: 无样本"); return
        print(f"\n{'='*64}\n{label} (n={n})\n{'='*64}")
        for idx, name in [(4, "T+1收盘"), (5, "T+3收盘"), (6, "T+3期间最高")]:
            vals = sorted(r[idx] for r in recs)
            med = vals[n//2] * 100
            mean = sum(vals) / n * 100
            win = sum(1 for x in vals if x > 0) / n * 100
            p10 = vals[int(n*0.1)] * 100
            p90 = vals[int(n*0.9)] * 100
            print(f"  {name:10s}: 中位{med:+6.2f}% 均值{mean:+6.2f}% 胜率{win:5.1f}% [P10:{p10:+.1f}% P90:{p90:+.1f}%]")

    stat(g_hard, "■ 回落>3%（抛压过大 → 预测次日砸盘）")
    stat(g_ok,   "■ 回落0.5~3%（抛压可承受 → 预测T+3内拉升）")

    # 细分: 回落0.5~1.5 / 1.5~3 边界效应
    for lo, hi, lab in [(0.005, 0.015, "回落0.5~1.5%"), (0.015, 0.03, "回落1.5~3%")]:
        sub = [r for r in g_ok if lo <= r[2] < hi]
        stat(sub, f"□ 细分 {lab}")

    # 交叉: 回落>3% 且次日确实砸(验证黄老板"第二天砸") —— 条件概率
    if g_hard:
        n = len(g_hard)
        down1 = sum(1 for r in g_hard if r[4] < 0) / n * 100
        down2 = sum(1 for r in g_hard if r[4] < -0.01) / n * 100
        print(f"\n■ 回落>3%组 次日下跌概率: {down1:.1f}% | 次日跌超1%概率: {down2:.1f}%")

if __name__ == "__main__":
    main()
