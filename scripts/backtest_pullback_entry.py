#!/usr/bin/env python3
"""
试盘信号 → 回踩低吸策略回测（最后一验）
=======================================
逻辑: 试盘信号出现后, 5日内挂单低吸(试盘日开盘价或实体中部)
      成交后持有 T+5 / T+10 / 破位止损
未成交(未回踩) → 放弃
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
    n_total, n_filled = 0, 0

    for sym, k in daily.items():
        if sym in st or len(k) < 80:
            continue
        n = len(k)
        for i in range(6, n - 16):
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
            win = k[max(0, i-250):i+1]
            lo = min(x[3] for x in win if x[3])
            if lo and (c - lo) / lo >= 0.45:
                continue
            c60 = k[max(0, i-60)][4]
            if c60 and (c - c60) / c60 >= 0.40:
                continue
            n_total += 1

            # 挂单: 试盘日开盘价(回踩到开盘价成交)
            limit_px = o
            # 5日内等待成交
            filled_j = None
            for j in range(i + 1, min(i + 6, n)):
                if k[j][3] and k[j][3] <= limit_px:
                    filled_j = j
                    break
            if filled_j is None:
                continue  # 未回踩, 放弃
            n_filled += 1
            buy = limit_px
            # 出场
            # K1: T+5收盘
            j5 = min(filled_j + 5, n - 1)
            if k[j5][4]:
                trades["K1"].append(((k[j5][4] - buy) / buy - COST, j5 - filled_j))
            # K2: T+10收盘
            j10 = min(filled_j + 10, n - 1)
            if k[j10][4]:
                trades["K2"].append(((k[j10][4] - buy) / buy - COST, j10 - filled_j))
            # K3: 破试盘日低点止损, 否则T+5
            stopped = False
            for j in range(filled_j + 1, min(filled_j + 6, n)):
                if k[j][3] and k[j][3] < l * 0.99:
                    trades["K3"].append(((k[j][4] - buy) / buy - COST, j - filled_j))
                    stopped = True
                    break
            if not stopped and k[j5][4]:
                trades["K3"].append(((k[j5][4] - buy) / buy - COST, j5 - filled_j))
            # K4: 止损试盘低点 + 止盈8% (盘中), 超时T+8收盘
            exited = False
            for j in range(filled_j + 1, min(filled_j + 9, n)):
                lj, hj = k[j][3], k[j][2]
                if lj and lj < l * 0.99:
                    trades["K4"].append(((k[j][4] - buy) / buy - COST, j - filled_j)); exited = True; break
                if hj and hj >= buy * 1.08:
                    trades["K4"].append((0.08 - COST, j - filled_j)); exited = True; break
            if not exited:
                j8 = min(filled_j + 8, n - 1)
                if k[j8][4]:
                    trades["K4"].append(((k[j8][4] - buy) / buy - COST, j8 - filled_j))

    names = {"K1": "回踩开盘价买→T+5", "K2": "回踩开盘价买→T+10",
             "K3": "回踩买→破试盘低点止损/T+5", "K4": "回踩买→止损低点/止盈8%/T+8"}
    print(f"信号总数 {n_total}, 回踩成交 {n_filled} ({n_filled/n_total*100:.0f}%)\n")
    for key in ("K1", "K2", "K3", "K4"):
        rs = trades.get(key, [])
        if not rs:
            continue
        n_tr = len(rs)
        rets = sorted(r[0] for r in rs)
        holds = [r[1] for r in rs]
        med = rets[n_tr//2] * 100
        mean = sum(rets) / n_tr * 100
        win = sum(1 for x in rets if x > 0) / n_tr * 100
        pf_n = sum(x for x in rets if x > 0)
        pf_d = -sum(x for x in rets if x <= 0)
        pf = pf_n / pf_d if pf_d > 0 else 99
        avg_h = sum(holds) / len(holds)
        print(f"{names.get(key, key):26s}: n={n_tr} 胜率{win:5.1f}% 中位{med:+6.2f}% 均值{mean:+6.2f}% 盈亏比{pf:4.2f} 持有{avg_h:.1f}d")

if __name__ == "__main__":
    main()
