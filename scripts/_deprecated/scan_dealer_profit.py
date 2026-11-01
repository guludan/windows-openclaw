#!/usr/bin/env python3
"""
庄家获利空间选股器 v1
======================
搜索到的实战特征 + 黄老板砸盘线规则, 量化为筛选条件:

特征库(来源: 筹码理论/实战总结):
 F1 低位单峰密集 = 主力吸筹完成(横盘区筹码集中, 峰区占比>=55%)
 F2 获利盘50~70% = 最健康拉升区
 F3 现价在峰上沿附近(刚突破或贴着) = 启动窗口
 F4 上方套牢真空(现价上方20%范围内套压<25%) = 拉升阻力小
 F5 庄家成本 = 筹码峰中心(累计换手100%法近似); 砸盘线 = 成本*0.85
 F6 吸筹期温和放量 + 近期缩量洗盘(黄老板规则: 量能变化)
 F7 未兑现: 近3日涨幅<5% 今日非大阳(黄老板规则)

输出: 峰位/庄家成本/获利盘/上方套压/拉升空间评分
"""
import psycopg2
from collections import defaultdict

PG = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
N_BUCKETS = 50

def calc_chips(k, i, lookback=150):
    """CYQ筹码: 从i往前lookback日, 返回(buckets, lo, hi)"""
    start = max(0, i - lookback)
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
        b0 = max(0, int((l - lo) / step))
        b1 = min(N_BUCKETS - 1, int((h - lo) / step))
        per = v / (b1 - b0 + 1)
        for b in range(b0, b1 + 1):
            buckets[b] += per
        decay = 1.0 - min(0.5, (t or 0) / 100.0)
        buckets = [x * decay for x in buckets]
    return buckets, lo, hi

def chip_metrics(buckets, lo, hi, close):
    """峰/获利盘/套压/成本"""
    total = sum(buckets)
    if total <= 0 or hi <= lo:
        return None
    step = (hi - lo) / N_BUCKETS
    # 主峰: 最大bucket, 峰区间=连续>=50%峰值的buckets
    peak_v = max(buckets)
    peak_i = buckets.index(peak_v)
    lo_i, hi_i = peak_i, peak_i
    while lo_i > 0 and buckets[lo_i - 1] >= peak_v * 0.5:
        lo_i -= 1
    while hi_i < N_BUCKETS - 1 and buckets[hi_i + 1] >= peak_v * 0.5:
        hi_i += 1
    peak_share = sum(buckets[lo_i:hi_i + 1]) / total          # 单峰度
    peak_center = lo + (lo_i + hi_i + 1) / 2 * step           # 峰中心=庄家成本近似
    peak_top = lo + (hi_i + 1) * step                          # 峰上沿
    # 获利盘
    cut = max(0, min(N_BUCKETS, int((close - lo) / step)))
    profit = sum(buckets[:cut]) / total
    # 上方套压(修正): close~close*1.2区间筹码占总筹码比例(绝对占比, 低=真空)
    up_cut = max(0, min(N_BUCKETS, int((close * 1.2 - lo) / step)))
    overhang = sum(buckets[cut:up_cut]) / total
    return {"peak_share": peak_share, "cost": peak_center, "peak_top": peak_top,
            "profit": profit, "overhang": overhang, "cur": close}

def main():
    conn = psycopg2.connect(PG)
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, trade_date, open, high, low, close, volume
        FROM daily_prices
        WHERE trade_date >= '2026-01-01' AND symbol LIKE 'A.%'
        ORDER BY symbol, trade_date
    """)
    daily = defaultdict(list)
    for sym, td, o, h, l, c, v in cur.fetchall():
        daily[sym].append((str(td), o, h, l, c, v, None))
    # 换手率
    cur.execute("""
        SELECT code, trade_date, turnover FROM market_snapshots
        WHERE trade_date >= '2026-01-01' AND code LIKE 'A.%'
    """)
    tmap = defaultdict(dict)
    for code, td, t in cur.fetchall():
        tmap[code][str(td)] = t
    cur.execute("SELECT code, name FROM stock_list")
    names = {'A.' + r[0]: r[1] for r in cur.fetchall()}
    cur.execute("SELECT code FROM stock_list WHERE name LIKE '%%ST%%' OR name LIKE '%%退%%'")
    st = {'A.' + r[0] for r in cur.fetchall()}
    conn.close()
    print(f"加载 {len(daily)} 只")

    results = []
    for sym, k0 in daily.items():
        if sym in st or sym not in names or len(k0) < 60:
            continue
        n = len(k0)
        # 注入换手率
        tm = tmap.get(sym, {})
        k = [(td, o, h, l, c, v, tm.get(td)) for td, o, h, l, c, v, _ in k0]
        i = n - 1
        td, o, h, l, c, v, t = k[i]
        if not all([o, h, l, c, v]):
            continue
        # F7 未兑现(黄老板规则)
        c3 = k[i-3][4] if i >= 3 else c
        if c3 and (c - c3) / c3 >= 0.05:
            continue
        if o and (c - o) / o >= 0.05:
            continue
        # 低位(距一年低点<45%)
        lo250 = min(x[3] for x in k if x[3])
        if lo250 and (c - lo250) / lo250 >= 0.45:
            continue
        # F6 量能: 吸筹期(30~90日前)均量 vs 更早(120~180日前), 温和放量1.0~2.5
        if n >= 180:
            v_acc = sum(x[5] or 0 for x in k[-90:-30]) / 60
            v_pre = sum(x[5] or 0 for x in k[-180:-120]) / 60
            if v_pre == 0 or not (1.0 <= v_acc / v_pre <= 2.5):
                continue
        # 筹码指标
        buckets, blo, bhi = calc_chips(k, i)
        if buckets is None:
            continue
        m = chip_metrics(buckets, blo, bhi, c)
        if not m:
            continue
        # 评分
        score = 0
        # F1 单峰密集
        if m["peak_share"] >= 0.55:
            score += 25
        elif m["peak_share"] >= 0.45:
            score += 15
        # F3 现价 vs 峰上沿: 峰内或刚突破<6%
        rel = (c - m["peak_top"]) / m["peak_top"] if m["peak_top"] else 9
        if -0.08 <= rel <= 0.06:
            score += 25
        elif -0.15 <= rel <= 0.10:
            score += 12
        # F2 获利盘 40~70% 健康区
        if 0.40 <= m["profit"] <= 0.70:
            score += 20
        elif 0.30 <= m["profit"] <= 0.80:
            score += 10
        # F4 上方真空(修正: 占总筹码比例)
        if m["overhang"] < 0.15:
            score += 20
        elif m["overhang"] < 0.25:
            score += 10
        if score < 50:
            continue
        # 拉升空间: 峰上沿到一年内高点的大致空间(近似用近250日高点)
        hi250 = max(x[2] for x in k if x[2])
        upside = (hi250 - c) / c if hi250 else 0
        # 砸盘线(黄老板): 成本*0.85
        smash_line = m["cost"] * 0.85
        results.append((score, sym, m, upside, smash_line))

    results.sort(key=lambda x: -x[0])
    print(f"\n=== 庄家获利空间候选 (评分>=50, 共{len(results)}只) ===")
    print(f"{'评分':>3} {'代码':<7}{'名称':<7}{'现价':>7}{'庄成本':>7}{'获利盘':>6}{'套压':>5}{'单峰':>5}{'空间':>6} {'砸盘线':>7}")
    for score, sym, m, upside, smash in results[:25]:
        print(f"{score:>3} {sym[2:]:<7}{names.get(sym,''):<7}{m.get('cur',0):>7.2f}{m['cost']:>7.2f}"
              f"{m['profit']*100:>5.0f}%{m['overhang']*100:>4.0f}%{m['peak_share']*100:>4.0f}%{upside*100:>5.0f}% {smash:>7.2f}")

if __name__ == "__main__":
    main()
