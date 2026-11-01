#!/usr/bin/env python3
"""
试盘信号回验 + 扫描 — 黄老板规则：
  长上影线 + 影线跌幅>3%  → 次日大概率砸盘
  长上影线 + 影线跌幅<3%  → 后续几日进吸筹/拉升区

定义:
  upper_shadow = (high - max(open, close)) / max(open, close)
  试盘日: upper_shadow >= 2% 且 量比(vs前5日均量) >= 1.3
回验窗口: 2026-01-05 ~ 2026-06-26（完整数据段）
  组1 bearish: shadow > 3%
  组2 bullish: 2% <= shadow <= 3%
统计: T+1/T+3/T+5/T+10 收益
"""
import psycopg2
from collections import defaultdict

PG = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

def main():
    conn = psycopg2.connect(PG)
    cur = conn.cursor()

    # 取2026-01以来的全部日线（K线+量）
    print("拉取2026-01~06日线...")
    cur.execute("""
        SELECT symbol, trade_date, open, high, low, close, volume
        FROM daily_prices
        WHERE trade_date >= '2025-12-15' AND trade_date <= '2026-06-26' AND symbol LIKE 'A.%'
        ORDER BY symbol, trade_date
    """)
    rows = cur.fetchall()
    print(f"共 {len(rows)} 条")

    # 按股票组织
    by_sym = defaultdict(list)
    for sym, td, o, h, l, c, v in rows:
        by_sym[sym].append((td, o, h, l, c, v))

    # 排除ST（用stock_list）
    cur.execute("SELECT code FROM stock_list WHERE name LIKE '%%ST%%' OR name LIKE '%%退%%'")
    st = {'A.' + r[0] for r in cur.fetchall()}

    bear = []  # shadow>3%
    bull = []  # 2%~3%
    for sym, k in by_sym.items():
        if sym in st or len(k) < 30:
            continue
        for i in range(5, len(k) - 10):
            td, o, h, l, c, v = k[i]
            if not all([o, h, c, v]):
                continue
            body_top = max(o, c)
            shadow = (h - body_top) / body_top
            if shadow < 0.02:
                continue
            # 量比 vs 前5日均量
            v5 = sum(x[5] for x in k[i-5:i]) / 5
            if v5 == 0:
                continue
            vr = v / v5
            if vr < 1.3:
                continue
            # 未来收益
            c0 = c
            def fut(n):
                j = min(i + n, len(k) - 1)
                return (k[j][4] - c0) / c0
            item = (sym, str(td), shadow, vr, fut(1), fut(3), fut(5), fut(10))
            if shadow > 0.03:
                bear.append(item)
            else:
                bull.append(item)

    def stats(name, arr, idx):
        vals = sorted(x[idx] for x in arr)
        n = len(vals)
        mean = sum(vals) / n
        med = vals[n // 2]
        win = sum(1 for v in vals if v > 0) / n
        return f"  {name} T+{[1,3,5,10][idx-4]}: 均值{mean*100:+.2f}% 中位{med*100:+.2f}% 胜率{win*100:.1f}% (n={n})"

    print(f"\n{'='*70}")
    print(f"回验结果 (2026-01~06, 试盘=上影>=2%且量比>=1.3)")
    print(f"{'='*70}")
    print(f"\n组1 砸盘预警 (影线>3%): {len(bear)} 次")
    for i in range(4, 8):
        print(stats("砸盘组", bear, i))
    print(f"\n组2 拉升前兆 (影线2%~3%): {len(bull)} 次")
    for i in range(4, 8):
        print(stats("前兆组", bull, i))

    # 基线：全样本任意日后T+5
    print("\n（基线参考：随机日T+5均值约+0.2%~0.5%，牛市段更高）")

    conn.close()

if __name__ == "__main__":
    main()
