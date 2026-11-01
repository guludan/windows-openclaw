#!/usr/bin/env python3
"""
试盘确认快进快出策略回测
==========================
入场: 试盘信号(上影回落0.5~3%或下影收回, 放量>=1.3)后
      次日确认(阳线+涨>1%+量比>=1.0) → 确认日收盘买入
出场策略:
  A: T+1收盘卖
  B: T+2收盘卖
  C: T+3收盘卖
  D: 止盈+5% / 止损-3% (盘中触发, 先判止损)
  E: 跌破试盘日实体下沿止损, 否则T+3收盘卖
成本: 双边0.15% (佣金万2.5+印花税千1)
窗口: 2025-09 ~ 2026-09-21
分层: 全样本 vs 背景过滤(低位<45%+未爆炒+量能积累)
"""
import psycopg2
from collections import defaultdict

PG = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
COST = 0.0015  # 双边

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

    # 策略容器
    trades = defaultdict(list)  # strat -> [(ret, hold_days)]

    def exit_sim(k, i_buy, buy_px, strat, key=None):
        """i_buy=确认日index, buy_px=确认日收盘"""
        key = key or strat
        n = len(k)
        if strat in ("A", "B", "C"):
            off = {"A": 1, "B": 2, "C": 3}[strat]
            j = min(i_buy + off, n - 1)
            sell = k[j][4]
            if not sell:
                return
            trades[key].append(((sell - buy_px) / buy_px - COST, j - i_buy))
        elif strat == "D":
            for j in range(i_buy + 1, min(i_buy + 6, n)):
                _, o, h, l, c, v = k[j]
                if not all([l, h, c]):
                    continue
                if l <= buy_px * 0.97:  # 先判止损(保守)
                    trades[key].append((-0.03 - COST, j - i_buy)); return
                if h >= buy_px * 1.05:
                    trades[key].append((0.05 - COST, j - i_buy)); return
            sell = k[min(i_buy + 5, n - 1)][4]
            if sell:
                trades[key].append(((sell - buy_px) / buy_px - COST, min(i_buy+5, n-1) - i_buy))
        elif strat == "E":
            body_low = k[i_buy - 1][1]  # 试盘日实体下沿=open(近似)
            for j in range(i_buy + 1, min(i_buy + 4, n)):
                l = k[j][3]
                c = k[j][4]
                if l and l < body_low * 0.99 and c is not None:
                    trades[key].append(((c - buy_px) / buy_px - COST, j - i_buy)); return
            sell = k[min(i_buy + 3, n - 1)][4]
            if sell:
                trades[key].append(((sell - buy_px) / buy_px - COST, min(i_buy+3, n-1) - i_buy))

    n_sig_all, n_sig_bg = 0, 0
    for sym, k in daily.items():
        if sym in st or len(k) < 80:
            continue
        n = len(k)
        # 背景预计算(滚动): 每个信号日的位置
        lo250_cache = {}
        def pos_low(i):
            if i not in lo250_cache:
                win = k[max(0, i-250):i+1]
                lo = min(x[3] for x in win if x[3])
                lo250_cache[i] = (k[i][4] - lo) / lo if lo and k[i][4] else 9
            return lo250_cache[i]

        for i in range(6, n - 6):
            td, o, h, l, c, v = k[i]
            pc = k[i-1][4]
            if not all([o, h, l, c, v, pc]):
                continue
            vol5 = sum(x[5] or 0 for x in k[i-5:i]) / 5
            if vol5 == 0 or v / vol5 < 1.3:
                continue
            vr = v / vol5
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
            # 次日确认: 阳线+涨>1%+量比>=1.0(相对试盘日量)
            i2 = i + 1
            td2, o2, h2, l2, c2, v2 = k[i2]
            if not all([o2, h2, l2, c2, v2]) or v == 0:
                continue
            if not (c2 > o2 and (c2 - c) / c > 0.01 and v2 / v >= 1.0):
                continue
            n_sig_all += 1
            # 出场模拟(全样本)
            for strat in ("A", "B", "C", "D", "E"):
                exit_sim(k, i2, c2, strat)
            # 背景过滤样本
            pl = pos_low(i)
            c60 = k[max(0, i-60)][4]
            if pl < 0.45 and c60 and (c - c60) / c60 < 0.40:
                n_sig_bg += 1
                for strat in ("A2", "B2", "C2", "D2", "E2"):
                    exit_sim(k, i2, c2, strat[:-1], key=strat)

    def report(label, pref, n_sig):
        print(f"\n■ {label} (确认信号 {n_sig} 次)")
        names = {"A": "T+1卖", "B": "T+2卖", "C": "T+3卖", "D": "止盈5%/止损3%", "E": "破试盘实体止损+T+3"}
        for s in "ABCDE":
            key = s + pref
            if key not in trades:
                continue
            rs = trades[key]
            n = len(rs)
            if n == 0:
                continue
            rets = sorted(r[0] for r in rs)
            holds = [r[1] for r in rs]
            med = rets[n//2] * 100
            mean = sum(rets) / n * 100
            win = sum(1 for x in rets if x > 0) / n * 100
            pf_num = sum(x for x in rets if x > 0)
            pf_den = -sum(x for x in rets if x <= 0)
            pf = pf_num / pf_den if pf_den > 0 else 99
            p90 = rets[int(n*0.9)] * 100
            p10 = rets[int(n*0.1)] * 100
            avg_hold = sum(holds) / len(holds)
            # 年化估算: 假设资金满转, 收益/持有天数*250
            daily_ret = mean / 100 / max(avg_hold, 1)
            print(f"  {names[s]:16s}: 胜率{win:5.1f}% 中位{med:+6.2f}% 均值{mean:+6.2f}% 盈亏比{pf:4.2f} 持有{avg_hold:.1f}d [P10:{p10:+.1f} P90:{p90:+.1f}]")

    report("全样本(无背景过滤)", "", n_sig_all)
    report("背景过滤(低位+未爆炒)", "2", n_sig_bg)

if __name__ == "__main__":
    main()
