#!/usr/bin/env python3
"""
试盘×筹码分布联合挖掘
======================
CYQ筹码分布（简化）:
  - 每日新筹码: 当日成交量按[low,high]均匀挂到50个价格桶
  - 历史衰减: chips *= (1 - turnover)
  - 获利盘比例: 收盘价下方筹码占比
  - 集中度: 1 - (p75-p25)/(p90-p10)，越高越集中

试盘定义:
  - 上影试盘(测抛压): 上影>=2% 量比>=1.3
  - 下影试盘(测承接): 下影>=2% 量比>=1.3

挖掘维度:
  A. 试盘类型 × 获利盘分层 → T+5/T+10
  B. 试盘后次日确认模式(量×阴阳) → T+10
  C. 获利盘高+集中度高 组合 → 最优指纹
窗口: 2025-09 ~ 2026-06
"""
import psycopg2
from collections import defaultdict

PG = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
N_BUCKETS = 50

def calc_chips(k, i):
    """计算第i日的筹码分布, 预热120日, 返回(buckets, lo, hi)"""
    start = max(0, i - 120)
    win = k[start:i + 1]
    lo = min(x[3] for x in win) * 0.98
    hi = max(x[2] for x in win) * 1.02
    if hi <= lo:
        return None, lo, hi
    step = (hi - lo) / N_BUCKETS
    buckets = [0.0] * N_BUCKETS
    for j, (td, o, h, l, c, v, t) in enumerate(win):
        if not v or h <= l:
            continue
        # 当日筹码均匀挂到[l,h]
        b0 = max(0, int((l - lo) / step))
        b1 = min(N_BUCKETS - 1, int((h - lo) / step))
        n = b1 - b0 + 1
        per = v / n
        for b in range(b0, b1 + 1):
            buckets[b] += per
        # 换手衰减: 当日换手t%→历史筹码×(1-t/100)
        decay = 1.0 - min(0.5, (t or 0) / 100.0)
        buckets = [x * decay for x in buckets]
    return buckets, lo, hi

def chip_stats(buckets, lo, hi, close):
    total = sum(buckets)
    if total <= 0 or hi <= lo:
        return None
    step = (hi - lo) / N_BUCKETS
    # 获利盘: close下方筹码占比
    cut = int((close - lo) / step)
    cut = max(0, min(N_BUCKETS, cut))
    profit = sum(buckets[:cut]) / total
    # 集中度
    prices, weights = [], []
    for b, w in enumerate(buckets):
        if w > 0:
            prices.append(lo + (b + 0.5) * step)
            weights.append(w)
    wsum = sum(weights)
    if wsum <= 0:
        return None
    mean = sum(p * w for p, w in zip(prices, weights)) / wsum
    var = sum(w * (p - mean) ** 2 for p, w in zip(prices, weights)) / wsum
    cv = (var ** 0.5) / mean if mean > 0 else 9  # 变异系数, 越小越集中
    return profit, cv

def main():
    conn = psycopg2.connect(PG)
    cur = conn.cursor()
    print("拉取日线(2025-09~2026-06)...")
    cur.execute("""
        SELECT symbol, trade_date, open, high, low, close, volume
        FROM daily_prices
        WHERE trade_date >= '2025-09-01' AND trade_date <= '2026-06-26' AND symbol LIKE 'A.%'
        ORDER BY symbol, trade_date
    """)
    daily = defaultdict(list)
    for sym, td, o, h, l, c, v in cur.fetchall():
        daily[sym].append((td, o, h, l, c, v, None))
    print(f"{len(daily)} 只")
    # 换手率从market_snapshots
    print("拉取换手率快照...")
    cur.execute("""
        SELECT code, trade_date, turnover FROM market_snapshots
        WHERE trade_date >= '2025-09-01' AND trade_date <= '2026-06-26' AND code LIKE 'A.%'
    """)
    turnover_map = defaultdict(dict)
    for code, td, t in cur.fetchall():
        turnover_map[code][str(td)] = t
    cur.execute("SELECT code FROM stock_list WHERE name LIKE '%%ST%%' OR name LIKE '%%退%%'")
    st = {'A.' + r[0] for r in cur.fetchall()}
    conn.close()

    # 样本收集
    rec_up = []   # 上影试盘: (sym, profit, cv, next_mode, r5, r10)
    rec_dn = []   # 下影试盘
    for sym, k in daily.items():
        if sym in st or len(k) < 140:
            continue
        tmap = turnover_map.get(sym, {})
        # 注入换手率
        k2 = []
        for row in k:
            td, o, h, l, c, v, _ = row
            t = tmap.get(str(td))
            k2.append((td, o, h, l, c, v, t))
        for i in range(130, len(k2) - 11):
            td, o, h, l, c, v, t = k2[i]
            if not all([o, h, c, v]):
                continue
            v5 = sum(x[5] for x in k2[i-5:i]) / 5
            if v5 == 0:
                continue
            vr = v / v5
            if vr < 1.3:
                continue
            up_shadow = (h - max(o, c)) / max(o, c)
            dn_shadow = (min(o, c) - l) / (l if l > 0 else 1)
            sig = None
            if up_shadow >= 0.02 and up_shadow > dn_shadow:
                sig = "up"
            elif dn_shadow >= 0.02 and dn_shadow > up_shadow:
                sig = "dn"
            if not sig:
                continue
            buckets, lo, hi = calc_chips(k2, i)
            if buckets is None:
                continue
            cs = chip_stats(buckets, lo, hi, c)
            if not cs:
                continue
            profit, cv = cs
            # 次日确认模式
            if i + 1 < len(k2):
                _, o2, h2, l2, c2, v2, _ = k2[i + 1]
                if c2 and c:
                    dn_ret = (c2 - c) / c
                    v_ratio2 = v2 / v if v > 0 else 1
                    vol_mode = "缩量" if v_ratio2 < 0.8 else ("放量" if v_ratio2 > 1.3 else "平量")
                    dir_mode = "阳" if dn_ret > 0 else "阴"
                    next_mode = vol_mode + dir_mode
                else:
                    next_mode, dn_ret = "?", 0
            else:
                next_mode, dn_ret = "?", 0
            def fut(n):
                j = min(i + n, len(k2) - 1)
                return (k2[j][4] - c) / c if k2[j][4] else 0
            rec = (sym, profit, cv, next_mode, fut(5), fut(10))
            (rec_up if sig == "up" else rec_dn).append(rec)

    def report(title, recs):
        print(f"\n{'='*70}\n{title} (n={len(recs)})\n{'='*70}")
        # A. 获利盘分层
        print("\n■ 按试盘日获利盘分层:")
        layers = [(0, 0.3, "获利盘<30%(深套区)"), (0.3, 0.7, "获利盘30~70%"),
                  (0.7, 1.01, "获利盘>70%(获利丰厚)")]
        for a, b, name in layers:
            sub = [r for r in recs if a <= r[1] < b]
            if len(sub) < 30:
                print(f"  {name}: n={len(sub)} 样本不足")
                continue
            for idx, lab in [(4, "T+5"), (5, "T+10")]:
                vals = sorted(r[idx] for r in sub)
                n = len(vals)
                med = vals[n//2] * 100
                win = sum(1 for x in vals if x > 0) / n * 100
                print(f"  {name} {lab}: 中位{med:+.2f}% 胜率{win:.1f}% (n={n})")
        # C. 集中度分层(仅获利盘>70%)
        print("\n■ 获利盘>70% × 筹码集中度分层:")
        base = [r for r in recs if r[1] >= 0.7]
        if len(base) >= 60:
            cvs = sorted(r[2] for r in base)
            q50 = cvs[len(cvs)//2]
            for name, flt in [("集中(CV低)", lambda r: r[2] <= q50), ("分散(CV高)", lambda r: r[2] > q50)]:
                sub = [r for r in base if flt(r)]
                if len(sub) < 30:
                    continue
                for idx, lab in [(4, "T+5"), (5, "T+10")]:
                    vals = sorted(r[idx] for r in sub)
                    n = len(vals)
                    med = vals[n//2] * 100
                    win = sum(1 for x in vals if x > 0) / n * 100
                    print(f"  {name} {lab}: 中位{med:+.2f}% 胜率{win:.1f}% (n={n})")
        # B. 次日确认模式
        print("\n■ 试盘次日确认模式 → T+10:")
        modes = defaultdict(list)
        for r in recs:
            modes[r[3]].append(r[5])
        for m, vals in sorted(modes.items(), key=lambda x: -len(x[1])):
            if len(vals) < 30:
                continue
            vals = sorted(vals)
            n = len(vals)
            med = vals[n//2] * 100
            win = sum(1 for x in vals if x > 0) / n * 100
            print(f"  次日{m}: T+10中位{med:+.2f}% 胜率{win:.1f}% (n={n})")

    report("▲ 上影试盘（测上方抛压）", rec_up)
    report("▼ 下影试盘（测下方承接）", rec_dn)

if __name__ == "__main__":
    main()
