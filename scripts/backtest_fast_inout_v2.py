#!/usr/bin/env python3
"""
试盘日直接买入 + 次日条件出场（v2快进快出）
=============================================
逻辑: 试盘信号日收盘买入(不赌确认,先上车)
      D1验证: 放量阳(确认) → 持有到D3/D5收盘卖
              阴线/未确认 → D1收盘卖(快出)
对比: 纯T+1卖 / 条件持有D3 / 条件持有D5
成本: 双边0.15%
"""
import psycopg2
from collections import defaultdict

PG = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
COST = 0.0015

def main():
    conn = psycopg2.connect(PG)
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, trade_date, open, high, low, close, volume
        FROM daily_prices
        WHERE trade_date >= '2025-09-01' AND symbol LIKE 'A.%'
        ORDER BY symbol, trade_date
    """)
    daily = defaultdict(list)
    for sym, td, o, h, l, c, v in cur.fetchall():
        daily[sym].append((str(td), o, h, l, c, v))
    cur.execute("SELECT code FROM stock_list WHERE name LIKE '%%ST%%' OR name LIKE '%%退%%'")
    st = {'A.' + r[0] for r in cur.fetchall()}
    conn.close()

    trades = defaultdict(list)

    for sym, k in daily.items():
        if sym in st or len(k) < 80:
            continue
        n = len(k)
        for i in range(6, n - 6):
            td, o, h, l, c, v = k[i]
            pc = k[i-1][4]
            if not all([o, h, l, c, v, pc]):
                continue
            vol5 = sum(x[5] or 0 for x in k[i-5:i]) / 5
            if vol5 == 0 or v / vol5 < 1.3:
                continue
            up = (h - pc) / pc
            dd = (h - c) / h
            dn = (pc - l) / pc
            recov = (c - l) / l
            brk = (c - pc) / pc
            sig = None
            if up >= 0.02 and 0.005 <= dd <= 0.03:
                sig = "up"
            elif dn >= 0.02 and recov >= 0.02 and brk > -0.03:
                sig = "dn"
            if not sig:
                continue
            # 背景过滤
            win = k[max(0, i-250):i+1]
            lo = min(x[3] for x in win if x[3])
            if lo and (c - lo) / lo >= 0.45:
                continue
            c60 = k[max(0, i-60)][4]
            if c60 and (c - c60) / c60 >= 0.40:
                continue

            buy = c  # 试盘日收盘买
            # D1
            td1, o1, h1, l1, c1, v1 = k[i+1]
            if not all([o1, h1, l1, c1, v1]):
                continue
            d1_ret = (c1 - c) / c
            confirmed = c1 > o1 and d1_ret > 0.005 and v1 >= v * 0.9

            # 策略F: 无条件T+1卖
            trades["F"].append((d1_ret - COST, 1))
            # 策略G: D1确认→持有D3卖; 未确认→D1卖
            if confirmed:
                j = min(i + 3, n - 1)
                trades["G"].append(((k[j][4] - buy) / buy - COST, j - i))
            else:
                trades["G"].append((d1_ret - COST, 1))
            # 策略H: D1确认→持有D5卖; 未确认→D1卖
            if confirmed:
                j = min(i + 5, n - 1)
                trades["H"].append(((k[j][4] - buy) / buy - COST, j - i))
            else:
                trades["H"].append((d1_ret - COST, 1))
            # 策略I: D1确认→持有D10卖; 未确认→D1卖
            if confirmed:
                j = min(i + 10, n - 1)
                trades["I"].append(((k[j][4] - buy) / buy - COST, j - i))
            else:
                trades["I"].append((d1_ret - COST, 1))
            # 策略J: D1确认→破试盘日实体止损,否则D5卖; 未确认→D1卖
            if confirmed:
                body_low = min(o, c)
                exited = False
                for j in range(i + 2, min(i + 6, n)):
                    lj = k[j][3]
                    if lj and lj < body_low * 0.99:
                        trades["J"].append(((k[j][4] - buy) / buy - COST, j - i))
                        exited = True
                        break
                if not exited:
                    j = min(i + 5, n - 1)
                    trades["J"].append(((k[j][4] - buy) / buy - COST, j - i))
            else:
                trades["J"].append((d1_ret - COST, 1))

    names = {
        "F": "无条件T+1卖",
        "G": "确认→D3, 否→D1",
        "H": "确认→D5, 否→D1",
        "I": "确认→D10, 否→D1",
        "J": "确认→破实体止损/D5, 否→D1",
    }
    print(f"背景过滤后信号数: {len(trades['F'])}\n")
    for s in "FGHIJ":
        rs = trades[s]
        n = len(rs)
        rets = sorted(r[0] for r in rs)
        holds = [r[1] for r in rs]
        med = rets[n//2] * 100
        mean = sum(rets) / n * 100
        win = sum(1 for x in rets if x > 0) / n * 100
        pf_n = sum(x for x in rets if x > 0)
        pf_d = -sum(x for x in rets if x <= 0)
        pf = pf_n / pf_d if pf_d > 0 else 99
        avg_h = sum(holds) / len(holds)
        # 简单资金曲线: 逐笔复利(假设串行满仓)
        eq = 1.0
        for r in rets:
            eq *= (1 + r)
        annual = (eq ** (250 / max(avg_h, 1)) - 1) * 100
        print(f"{names[s]:22s}: 胜率{win:5.1f}% 中位{med:+6.2f}% 均值{mean:+6.2f}% 盈亏比{pf:4.2f} 持有{avg_h:.1f}d 复利{eq:.3f}")

if __name__ == "__main__":
    main()
