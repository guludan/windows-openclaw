#!/usr/bin/env python3
"""回验v2: 试盘影线信号 × 吸筹背景 分层统计
只在"有吸筹痕迹"的股票里验证 黄老板规则的后半句
吸筹背景（简化版）:
  - 低位: 当日close距窗口内最低价 < 35%
  - 前期温和放量: 前60日均量 / 再前60日均量 ∈ [1.0, 2.2]
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
        WHERE trade_date >= '2025-09-01' AND trade_date <= '2026-06-26' AND symbol LIKE 'A.%'
        ORDER BY symbol, trade_date
    """)
    rows = cur.fetchall()
    print(f"拉取 {len(rows)} 条 (2025-09~2026-06)")
    by_sym = defaultdict(list)
    for sym, td, o, h, l, c, v in rows:
        by_sym[sym].append((td, o, h, l, c, v))
    cur.execute("SELECT code FROM stock_list WHERE name LIKE '%%ST%%' OR name LIKE '%%退%%'")
    st = {'A.' + r[0] for r in cur.fetchall()}

    groups = {"bear_all": [], "bull_all": [], "bear_accum": [], "bull_accum": []}
    for sym, k in by_sym.items():
        if sym in st or len(k) < 120:
            continue
        lows = [x[3] for x in k]
        vols = [x[5] for x in k]
        for i in range(125, len(k) - 10):
            td, o, h, l, c, v = k[i]
            if not all([o, h, c, v]):
                continue
            body_top = max(o, c)
            shadow = (h - body_top) / body_top
            if shadow < 0.02:
                continue
            v5 = sum(x[5] for x in k[i-5:i]) / 5
            if v5 == 0:
                continue
            if v / v5 < 1.3:
                continue
            # 吸筹背景判断
            lo_win = min(lows[i-120:i])
            pos = (c - lo_win) / lo_win if lo_win > 0 else 99
            v60a = sum(vols[i-60:i]) / 60
            v60b = sum(vols[i-120:i-60]) / 60
            accum_bg = (pos < 0.35) and v60b > 0 and (1.0 <= v60a / v60b <= 2.2)
            def fut(n):
                j = min(i + n, len(k) - 1)
                return (k[j][4] - c) / c
            item = (fut(1), fut(3), fut(5), fut(10))
            key = ("bear" if shadow > 0.03 else "bull") + ("_accum" if accum_bg else "_all")
            groups[key].append(item)

    def stats(name, arr, idx):
        if not arr:
            return f"  {name} T+{[1,3,5,10][idx]}: n=0"
        vals = sorted(x[idx] for x in arr)
        n = len(vals)
        mean = sum(vals) / n
        med = vals[n // 2]
        win = sum(1 for v in vals if v > 0) / n
        return f"  {name} T+{[1,3,5,10][idx]}: 均值{mean*100:+.2f}% 中位{med*100:+.2f}% 胜率{win*100:.1f}% (n={n})"

    print(f"\n{'='*70}")
    print("分层回验: 试盘信号 × 吸筹背景")
    print(f"{'='*70}")
    print("\n【吸筹背景 + 影线>3%（吸筹池砸盘组）】")
    for i in range(4):
        print(stats("accum-bear", groups["bear_accum"], i))
    print("\n【吸筹背景 + 影线2~3%（吸筹池前兆组）★黄老板核心规则】")
    for i in range(4):
        print(stats("accum-bull", groups["bull_accum"], i))
    print("\n【无吸筹背景 + 影线2~3%（对照组）】")
    for i in range(4):
        print(stats("noaccum-bull", groups["bull_all"], i))
    conn.close()

if __name__ == "__main__":
    main()
