#!/usr/bin/env python3
"""
预备拉升筛选器 — 基于主力坐庄十阶段模型
目标：找到 ⑤洗盘尾声 → ⑥初升前夕 的股票

三段确认逻辑：
  A. 吸筹确认（2026-03 ~ 2026-06，完整数据）
     - 低位：距250日低点涨幅 < 35%
     - 温和：区间日均量/再前60日日均量 ∈ [1.0, 2.0]
     - 未爆炒：区间涨幅 < 25%
  B. 洗盘特征（近8个交易日）
     - 缩量：近5日均量 < 9月峰值量 × 0.75
     - 抗跌：区间跌幅 ∈ [-12%, +3%]，未创60日新低
     - 站稳：收盘 > MA20（基于6月前数据）
  C. 启动前兆（近3日）
     - 量能回升：近3日均量/近10日均量 > 1.05
     - 强势：收盘 > MA5，近3日阳线 ≥ 2
     - 接近箱体上沿：距60日高点 < 10%
  加分项：
     + pool_energy phase ∈ {B_ACCUM, A_RECOVER}（近20日出现）
     + 近期S1信号（vol_z>3未放量，主力异动）
     + 董监高增持（90日内）
  排除：
     - ST/*ST、退市
     - 上市 < 120日（次新，无历史可比）
     - 60日涨幅 > 40%（可能已在拉升/出货段）
     - 近3日涨幅 > 15%（已启动追高）
"""
import psycopg2
import sys
from datetime import date, timedelta
from collections import defaultdict

N_BUCKETS = 50

def calc_chips(k, i, lookback=150):
    """CYQ筹码分布: 从i往回lookback日, k元组=(td,o,h,l,c,v,turnover)"""
    start = max(0, i - lookback)
    win = k[start:i + 1]
    lo = min(x[3] for x in win if x[3]) * 0.98
    hi = max(x[2] for x in win if x[2]) * 1.02
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
    """筹码分析: 单峰度/庄成本/峰上沿/获利盘/上方套压/砸盘线"""
    total = sum(buckets)
    if total <= 0 or hi <= lo:
        return None
    step = (hi - lo) / N_BUCKETS
    peak_v = max(buckets)
    peak_i = buckets.index(peak_v)
    lo_i, hi_i = peak_i, peak_i
    while lo_i > 0 and buckets[lo_i - 1] >= peak_v * 0.5:
        lo_i -= 1
    while hi_i < N_BUCKETS - 1 and buckets[hi_i + 1] >= peak_v * 0.5:
        hi_i += 1
    peak_share = sum(buckets[lo_i:hi_i + 1]) / total
    cost = lo + (lo_i + hi_i + 1) / 2 * step          # 庄成本(密集峰中心)
    peak_top = lo + (hi_i + 1) * step                  # 峰上沿(突破线)
    cut = max(0, min(N_BUCKETS, int((close - lo) / step)))
    profit = sum(buckets[:cut]) / total                # 获利盘
    up_cut = max(0, min(N_BUCKETS, int((close * 1.2 - lo) / step)))
    overhang = sum(buckets[cut:up_cut]) / total        # 上方套压(占总筹码)
    return {"cost": cost, "peak_top": peak_top, "peak_share": peak_share,
            "profit": profit, "overhang": overhang, "smash": cost * 0.85}

PG = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

def main():
    conn = psycopg2.connect(PG)
    cur = conn.cursor()

    # 活跃股票清单（排除ST/次新）
    cur.execute("""
        SELECT code FROM stock_list
        WHERE name NOT LIKE '%%ST%%' AND name NOT LIKE '%%退%%'
    """)
    universe = {'A.' + r[0] for r in cur.fetchall()}
    print(f"股票池: {len(universe)} 只（排ST/退市）")

    # ---- A段：吸筹确认（3月~6月，完整窗口）----
    cur.execute("""
        SELECT symbol,
               MIN(low) AS lo250, MAX(close) FILTER (WHERE trade_date >= '2026-03-01') AS hi_recent,
               AVG(close) FILTER (WHERE trade_date >= '2026-05-26' AND trade_date <= '2026-06-26') AS close_jun,
               AVG(volume) FILTER (WHERE trade_date >= '2026-03-01' AND trade_date <= '2026-06-26') AS vol_ab,
               AVG(volume) FILTER (WHERE trade_date >= '2025-12-15' AND trade_date < '2026-03-01') AS vol_pre,
               MAX(close) FILTER (WHERE trade_date >= '2026-04-01' AND trade_date <= '2026-06-26') AS close_max_ab
        FROM daily_prices
        WHERE trade_date >= '2025-12-15' AND trade_date <= '2026-06-26'
        GROUP BY symbol
    """)
    accum = {}
    for sym, lo250, hi_recent, close_jun, vol_ab, vol_pre, close_max_ab in cur.fetchall():
        if sym not in universe or not all([lo250, close_jun, vol_ab, vol_pre, close_max_ab]):
            continue
        if vol_pre == 0:
            continue
        # 低位：现价距250日低点 < 35%
        pos = (close_jun - lo250) / lo250 if lo250 > 0 else 99
        if pos > 0.35:
            continue
        # 温和放量 1.0~2.0
        ratio = vol_ab / vol_pre
        if not (0.95 <= ratio <= 2.2):
            continue
        # 区间未爆炒（用区间高点相对起点）
        accum[sym] = {"pos": pos, "vol_ratio": ratio}

    print(f"A段吸筹确认: {len(accum)} 只")

    # ---- B+C段：近端洗盘+启动（9/11~9/21）----
    cur.execute("""
        SELECT symbol,
               AVG(volume) FILTER (WHERE trade_date >= '2026-09-15') AS vol_5d,
               MAX(volume) FILTER (WHERE trade_date >= '2026-09-11') AS vol_peak,
               MIN(close)  FILTER (WHERE trade_date >= '2026-09-11') AS close_min,
               MAX(close)  FILTER (WHERE trade_date >= '2026-09-11') AS close_max,
               (SELECT close FROM daily_prices d2 WHERE d2.symbol = d.symbol
                 AND d2.trade_date = '2026-09-21') AS close_now,
               AVG(volume) FILTER (WHERE trade_date >= '2026-09-11' AND trade_date <= '2026-09-18') AS vol_10d
        FROM daily_prices d
        WHERE trade_date >= '2026-09-11'
        GROUP BY symbol
    """)
    wash = {}
    for sym, vol_5d, vol_peak, close_min, close_max, close_now, vol_10d in cur.fetchall():
        if sym not in accum or not all([vol_5d, vol_peak, close_min, close_max, close_now, vol_10d]):
            continue
        # 缩量洗盘
        if vol_5d > vol_peak * 0.78:
            continue
        # 抗跌不创新低（对比9月窗口内部）且未暴涨
        rng = (close_max - close_min) / close_min if close_min > 0 else 9
        if rng > 0.25:  # 窗口内振幅过大=异动股，不是洗盘
            continue
        # 量能回升：近5日/前段
        vr = vol_5d / vol_10d if vol_10d > 0 else 0
        if vr < 0.9:  # 还在缩，未回升
            continue
        # ★ 黄老板决定(9/22): "死水"无法可靠描述, 砍掉该硬剔。
        #   缩量回踩(华新建材型)/缩量洗盘一律视为健康形态, 不在此剔除。
        wash[sym] = {"close_now": close_now, "vol_recover": vr, "close_max_9m": close_max}

    print(f"B段洗盘+量能回升: {len(wash)} 只")

    # ---- C段补：MA5/阳线/箱体位置（用6月前60日高点做蓄势箱体）----
    cur.execute("""
        SELECT symbol, MAX(close) AS hi60
        FROM daily_prices
        WHERE trade_date >= '2026-03-25' AND trade_date <= '2026-06-26' AND symbol LIKE 'A.%'
        GROUP BY symbol
    """)
    box_high = dict(cur.fetchall())

    candidates = []
    pool_confirm = []  # 池2: 试盘待确认(东方雨虹/良信型)
    syms = list(wash.keys())
    for i in range(0, len(syms), 200):
        batch = syms[i:i+200]
        cur.execute("""
            SELECT symbol, trade_date, close, open, high, low
            FROM daily_prices
            WHERE symbol = ANY(%s) AND trade_date >= '2026-09-11'
            ORDER BY symbol, trade_date
        """, (batch,))
        by_sym = {}
        for sym, td, c, o, h, l in cur.fetchall():
            by_sym.setdefault(sym, []).append((c, o, h, l, td))
        for sym, rows in by_sym.items():
            if len(rows) < 6:
                continue
            closes = [r[0] for r in rows]
            ma5 = sum(closes[-5:]) / 5
            close_now = wash[sym]["close_now"]
            # ★★ 位置硬校验(9/22锐捷教训, 两池通用)
            cur.execute("""
                SELECT MIN(low) FROM daily_prices
                WHERE symbol=%s AND trade_date >= '2025-09-22'
            """, (sym,))
            lo250 = cur.fetchone()[0]
            # ★ 9/22修正: 位置阈值放宽到90%(含华新+64%)，仍挡锐捷式翻倍(+108%)
            if lo250 and (close_now - lo250) / lo250 >= 0.90:
                continue  # 已在年内顶部
            cur.execute("""
                SELECT close FROM daily_prices
                WHERE symbol=%s AND trade_date <= '2026-06-26'
                ORDER BY trade_date DESC LIMIT 1
            """, (sym,))
            r60 = cur.fetchone()
            if r60 and r60[0] and (close_now - r60[0]) / r60[0] >= 0.60:
                continue  # 近一季已大涨=高位箱体派发区
            # ---- 池2分流: 今日/昨日试盘信号(冲高>=2%+回落0.5~3%+量比) ----
            pc = closes[-2] if len(closes) >= 2 else closes[0]
            probe_today = False
            for j in (len(rows) - 1, len(rows) - 2):
                if j < 1:
                    continue
                c, o, h, l, td = rows[j]
                pcj = rows[j-1][0]
                if not all([c, o, h, l, pcj]):
                    continue
                # ★ 试盘定义(9/22修正): 真上影线——(最高-实体顶)/实体顶 ≥1.5% 且 收离最高≥0.5%
                top = max(o, c)
                if top <= 0:
                    continue
                shadow = (h - top) / top
                dd = (h - c) / h
                if shadow >= 0.015 and dd >= 0.005:
                    probe_today = True
                    break
            if probe_today:
                pool_confirm.append(sym)  # 待确认池: 不管当日实体多大(冲高回落大阳本就是试盘形态)
                continue
            # ---- 池1: 前夜埋伏(未启动小K线) ----
            c0 = closes[-4] if len(closes) >= 4 else closes[0]
            ret3 = (close_now - c0) / c0 if c0 else 9
            if ret3 >= 0.05:
                continue  # 已连涨兑现
            last_o, last_c = rows[-1][0], rows[-1][1]
            if last_o and (last_c - last_o) / last_o >= 0.05:
                continue  # 今日大阳且非试盘形态=已启动
            if close_now <= ma5 * 0.995 or close_now > ma5 * 1.025:
                continue
            bodies = [abs(c - o) / o for c, o, h, l, td in rows[-5:] if o]
            if not bodies or sum(bodies) / len(bodies) > 0.018:
                continue
            # ★ 还在洗盘区: 站上MA5但未远离(刚冒头, 涨幅<MA5上方2.5%)
            if close_now <= ma5 * 0.995 or close_now > ma5 * 1.025:
                continue
            # 近5日小实体洗盘(平均实体<1.8%)
            bodies = [abs(c - o) / o for c, o, h, l, td in rows[-5:] if o]
            if not bodies or sum(bodies) / len(bodies) > 0.018:
                continue
            # 距60日箱体上沿<12%（洗盘不深，距前高近=蓄势待发）
            hi60 = box_high.get(sym)
            if not hi60 or close_now < hi60 * 0.55:
                continue  # 距前高跌幅太深，不是洗盘是下跌中继
            near_high = (hi60 - close_now) / close_now
            if near_high > 0.12:
                continue
            candidates.append(sym)

    print(f"池1前夜埋伏: {len(candidates)} 只 | 池2待确认: {len(pool_confirm)} 只")

    # ---- D段：砸盘预警过滤（影线>3%且量比≥1.3，回验胜率42%→剔除）----
    cur.execute("""
        SELECT symbol, trade_date, open, high, low, close, volume
        FROM daily_prices
        WHERE trade_date >= '2026-09-11' AND symbol LIKE 'A.%'
        ORDER BY symbol, trade_date
    """)
    by_sym2 = {}
    for sym, td, o, h, l, c, v in cur.fetchall():
        by_sym2.setdefault(sym, []).append((o, h, l, c, v))
    dropped = []
    kept = []
    mild_probe = {}  # 温和试盘观察列
    probe_boost = {}  # 试盘+次日放量阳加分
    absorb_map = {}   # 砸盘吸筹(长下影)标记
    for sym in candidates + pool_confirm:
        k = by_sym2.get(sym, [])
        if len(k) < 6:
            kept.append(sym)
            continue
        bad = False
        absorb_hit = None
        for i in range(3, len(k)):  # 近几个交易日
            o, h, l, c, v = k[i]
            if not all([o, h, l, c, v]):
                continue
            # 砸盘吸筹日: 长下影线(≥2%) + 收盘微涨/微跌 (黄老板9/22 东方雨虹型)
            pc0 = k[i-1][3] if i > 0 else o
            if pc0:
                bot = min(o, c)
                lshadow = (bot - l) / bot if bot else 0
                chg0 = (c - pc0) / pc0
                if lshadow >= 0.02 and -0.025 <= chg0 <= 0.025:
                    absorb_hit = lshadow
            shadow = (h - max(o, c)) / max(o, c)
            v5 = sum(x[4] for x in k[max(0,i-5):i]) / max(1, min(i, 5))
            if v5 == 0:
                continue
            vr = v / v5
            if shadow > 0.03 and vr >= 1.3:
                bad = True
                break
            if 0.02 <= shadow <= 0.03 and vr >= 1.3:
                # ★ 时效检查: 只统计近2日内的试盘(黄老板规则: 3天前的信号已过期)
                if len(k) - 1 - i > 2:
                    continue
                # 检查次日确认模式（回验: 次日放量阳胜率66%）
                if i + 1 < len(k):
                    o2, h2, l2, c2, v2 = k[i + 1]
                    if c2 and c and v2 and v > 0:
                        nxt_ret = (c2 - c) / c
                        nxt_vr = v2 / v
                        if nxt_ret > 0 and nxt_vr >= 1.0:
                            mild_probe[sym] = f"影线{shadow*100:.1f}%→次日放量阳★({nxt_vr:.1f}x)"
                            probe_boost[sym] = 15
                            continue
                mild_probe[sym] = f"影线{shadow*100:.1f}%×量{vr:.1f}"
        if absorb_hit:
            absorb_map[sym] = absorb_hit
        if bad:
            dropped.append(sym)
        else:
            kept.append(sym)
    print(f"D段砸盘预警剔除: {len(dropped)} 只（剩余{len(kept)}只）")
    kept_set = set(kept)
    candidates = [s for s in candidates if s in kept_set]
    pool_confirm = [s for s in pool_confirm if s in kept_set]

    # ---- 诱多剔除(9/22 黄老板): 主力净流出 + 股价上涨 = 散户接盘(优宁维型) ----
    dupo_drop = []
    cur.execute("""
        SELECT code, trade_date, main_net, chg_pct FROM fund_flow
        WHERE trade_date >= '2026-09-11' ORDER BY code, trade_date
    """)
    fmap = {}
    for code, td, mn, chg in cur.fetchall():
        fmap.setdefault('A.' + code, []).append((float(mn or 0), float(chg or 0)))
    if fmap:
        surv = []
        for s in candidates + pool_confirm:
            lst = fmap.get(s)
            if lst and lst[-1][0] <= 0 and lst[-1][1] > 0.5:
                dupo_drop.append(s)
            else:
                surv.append(s)
        survivors = set(surv)
        candidates = [s for s in candidates if s in survivors]
        pool_confirm = [s for s in pool_confirm if s in survivors]
    print(f"诱多剔除(主力净流出+上涨): {len(dupo_drop)} 只")

    # ---- 加分项 ----
    bonus = {}
    # pool_energy phase
    cur.execute("""
        SELECT SUBSTRING(level FROM 8) AS sym, phase, COUNT(*)
        FROM pool_energy
        WHERE level LIKE 'code:%%' AND date >= '2026-09-01'
        GROUP BY 1, 2
    """)
    for sym, phase, cnt in cur.fetchall():
        if phase in ("B_ACCUM", "A_RECOVER"):
            bonus.setdefault(sym, []).append(f"池phase={phase}x{cnt}")
    # S1信号（修正9/22: 实际存于 backtest_signals 表, 名 s1_surge, 非 market_signals.surge）
    # TODO: 上游 stock_energy_incr 自2026-05-26后停更, 需修复增量; 暂用近半年窗口兜底
    cur.execute("""
        SELECT 'A.' || symbol, COUNT(*) FROM backtest_signals
        WHERE signal_type = 's1_surge' AND trade_date >= '2026-04-01'
        GROUP BY 1
    """)
    for sym, cnt in cur.fetchall():
        if sym:
            bonus.setdefault(sym, []).append(f"S1异动x{cnt}")
    # 董监高增持（90日）
    cur.execute("""
        SELECT 'A.' || security_code FROM exec_hold_changes
        WHERE change_date >= '2026-06-23' AND change_shares > 0
        GROUP BY 1
    """)
    for (sym,) in cur.fetchall():
        bonus.setdefault(sym, []).append("董监高增持")

    # ---- 输出（量价+CYQ筹码分析层）----
    # 拉候选近150日K线+换手率做筹码分析
    cur.execute("""
        SELECT code, trade_date, turnover FROM market_snapshots
        WHERE trade_date >= '2026-06-01' AND code LIKE 'A.%'
    """)
    tmap = defaultdict(dict)
    for code, td, t in cur.fetchall():
        if t is not None:
            tmap[code][str(td)] = t
    chip_out = {}
    all_final = candidates + pool_confirm
    if all_final:
        cur.execute("""
            SELECT symbol, trade_date, open, high, low, close, volume
            FROM daily_prices
            WHERE symbol = ANY(%s) AND trade_date >= '2026-01-01'
            ORDER BY symbol, trade_date
        """, (all_final,))
        kk = defaultdict(list)
        for sym, td, o, h, l, c, v in cur.fetchall():
            kk[sym].append((str(td), o, h, l, c, v, tmap.get(sym, {}).get(str(td))))
        for sym, k in kk.items():
            if len(k) < 60:
                continue
            i = len(k) - 1
            c_now = k[i][4]
            if not c_now:
                continue
            buckets, blo, bhi = calc_chips(k, i)
            if buckets is None:
                continue
            m = chip_metrics(buckets, blo, bhi, c_now)
            if m:
                # 量价验证: 近5日均量/前5日 + 今日量比
                v5 = sum(x[5] or 0 for x in k[-5:]) / 5
                v5p = sum(x[5] or 0 for x in k[-10:-5]) / 5
                m["vol_trend"] = v5 / v5p if v5p else 0
                m["cur"] = c_now
                chip_out[sym] = m

    scored = []
    for sym in candidates + pool_confirm:
        score = 60  # 基础分：过了三段筛选
        extra = bonus.get(sym, [])
        if any("池phase" in e for e in extra): score += 15
        if any("S1" in e for e in extra): score += 10
        if any("增持" in e for e in extra): score += 15
        score += probe_boost.get(sym, 0)  # 试盘+次日放量阳
        if absorb_map.get(sym): score += 15  # 长下影砸盘吸筹(东方雨虹型)
        scored.append((score, sym, extra))

    scored.sort(reverse=True)
    # ★ 筹码质量门槛(9/22教训): 获利盘不在30~80%或套压>60%=筹码结构差, 移入排除区
    final_list, rejected = [], []
    for item in scored:
        m = chip_out.get(item[1])
        if m and not (0.30 <= m["profit"] <= 0.80 and m["overhang"] <= 0.60):
            rejected.append(item)
        else:
            final_list.append(item)
    scored = final_list
    bar = '=' * 100
    print(f"\n{bar}\n🎯 预备拉升候选（{len(scored)}只，按评分排序，含CYQ筹码分析）\n{bar}")
    print(f"{'分':>3} {'代码':<10}{'名称':<8}{'现价':>7}{'庄成本':>7}{'获利盘':>6}{'套压':>6}{'单峰':>6}{'砸盘线':>7}{'量趋':>5}  标记")
    for score, sym, extra in scored[:200]:
        cur.execute("SELECT name FROM stock_list WHERE code=%s", (sym[2:],))
        name = cur.fetchone()
        name = name[0] if name else "?"
        probe = mild_probe.get(sym, "")
        m = chip_out.get(sym)
        tags = []
        if any("增持" in e for e in extra): tags.append("增持")
        if any("S1" in e for e in extra): tags.append("S1")
        if any("池phase" in e for e in extra): tags.append("池")
        if sym in absorb_map: tags.append(f"吸筹(下影{absorb_map[sym]*100:.1f}%)")
        if probe: tags.append("试盘★" if "★" in probe else "试盘")
        if m:
            print(f"{score:3d} {sym:<10}{name:<8}{m['cur']:>7.2f}{m['cost']:>7.2f}"
                  f"{m['profit']*100:>5.0f}%{m['overhang']*100:>5.0f}%{m['peak_share']*100:>5.0f}%"
                  f"{m['smash']:>7.2f}{m['vol_trend']:>5.2f}  {' '.join(tags)}")
        else:
            print(f"{score:3d} {sym:<10}{name:<8}{'—':>7}{'—':>7}{'—':>6}{'—':>6}{'—':>6}{'—':>7}{'—':>5}  {' '.join(tags)}")

    # 导出候选代码（给 fetch_fund_flow_list.py 补资金流用）
    try:
        with open('/tmp/candidates.txt', 'w') as fh:
            for score, sym, extra in scored:
                fh.write(sym.replace('A.', '') + '\n')
        print(f"\n📄 候选代码导出 /tmp/candidates.txt ({len(scored)}只)")
    except Exception:
        pass

    conn.close()

if __name__ == "__main__":
    main()
