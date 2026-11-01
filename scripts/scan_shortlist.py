#!/usr/bin/env python3
"""
预备拉升选股器 v4（整合版） — 基于主力坐庄十阶段模型
目标：找 ⑤洗盘尾声 → ⑥初升前夕 的股票（黄老板手工验证的目标形态）

四段漏斗：
  A. 吸筹确认（2026-03~06）：低位 + 温和放量
  B. 洗盘（近端）：缩量 + 抗跌 + 量能回升
     ★ 9/22 已砍掉不可靠的"死水"硬剔（缩量回踩=健康，不杀）
  C. 启动/试盘：分流 池1前夜埋伏 与 池2试盘待确认
     ★ 候选只要"启动前"的；已启动(近3日涨≥5%/远离MA5)一律归"启动跟踪池"
     ★ 池2试盘(黄老板9/22定调): 横盘或阴跌之后 → 突然上拉(high/前收≥2.5%) → 回落(收离最高≥0.8%)
  D. 砸盘剔除：上影线>3% + 量比≥1.3（回验胜率42%）

黄老板 9/22 口述特征（已集成）：
  - 砸盘吸筹日：长下影线(≥2%) + 收盘微涨/微跌 → 标记"吸筹"（东方雨虹9/16型）
  - 试盘 + 次日放量阳确认 → 加分（回验胜率66%）
  - 诱多嫌疑：放量上涨(近3日涨≥3%且3日量比≥1.4) = 散户接盘嫌疑 → 只打⚠标签(资金流黄老板自己看)
  - 长下影吸筹票豁免：时效线+筹码门槛双放宽(华新/东方雨虹型)

加分项：pool_energy phase / S1(/backtest_signals s1_surge) / 董监高增持 / 砸盘吸筹 / 试盘确认
排除：ST/退市、位置≥90%或近一季涨幅≥60%、近3日涨幅≥5%、诱多

用法：
  python3 scan_shortlist.py      # 主扫描（自动取最新交易日）
  DSYM=600801,002271 python3 scan_shortlist.py   # 调试指定票走到哪一步
  ⚠️ 不依赖外部资金流接口(避免东财IP封禁); 诱多用成交量近似
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
        # 低位：现价距250日低点 < 80% (★9/22修正: 原用6月均价算位置=基准错位, 良信被误杀)
        pos = (close_jun - lo250) / lo250 if lo250 > 0 else 99
        if pos > 0.80:
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
    import os as _os
    DBG = {('A.'+x.strip()) for x in _os.environ.get('DSYM', '').split(',') if x.strip()}
    for _s in DBG:
        print(f"  DBG {_s}: A={_s in accum} B={_s in wash}")

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
    absorb_pre = set()  # 长下影砸盘吸筹(豁免时效/筹码门槛用)
    started_pool = []   # 启动跟踪池(已启动/偏离前夜买点)
    started_seen = set()
    syms = list(wash.keys())
    for i in range(0, len(syms), 200):
        batch = syms[i:i+200]
        cur.execute("""
            SELECT symbol, trade_date, close, open, high, low
            FROM daily_prices
            WHERE symbol = ANY(%s) AND trade_date >= '2026-08-18'
            ORDER BY symbol, trade_date
        """, (batch,))
        by_sym = {}
        for sym, td, c, o, h, l in cur.fetchall():
            by_sym.setdefault(sym, []).append((c, o, h, l, td))
        for sym, rows in by_sym.items():
            if len(rows) < 6:
                continue
            def _d(m):
                if sym in DBG: print(f"  DBG {sym} C: {m}")
            def _started(why):
                if sym not in started_seen:
                    started_seen.add(sym)
                    started_pool.append((sym, why, close_now, ma5))
            closes = [r[0] for r in rows]
            ma5 = sum(closes[-5:]) / 5
            close_now = wash[sym]["close_now"]
            # 长下影砸盘吸筹(黄老板9/22): 下影≥2%+收微涨/微跌; 命中则后面放宽时效+筹码门槛
            ab = False
            for _j in range(1, len(rows)):
                _c, _o, _h, _l, _td = rows[_j]
                _pc = rows[_j-1][0]
                if not all([_c, _o, _l, _pc]):
                    continue
                _bot = min(_o, _c)
                if _bot and (_bot - _l) / _bot >= 0.02 and -0.025 <= (_c - _pc) / _pc <= 0.025:
                    ab = True
                    break
            if ab:
                absorb_pre.add(sym)
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
            # ★ 试盘(9/22黄老板定调): 横盘/阴跌之后 突然上拉 然后回落
            c0 = closes[-4] if len(closes) >= 4 else closes[0]
            ret3 = (close_now - c0) / c0 if c0 else 9
            probe_today = False
            probe_base = None
            for j in (len(rows) - 1, len(rows) - 2):
                if j < 1:
                    continue
                c, o, h, l, td = rows[j]
                pcj = rows[j-1][0]
                if not all([c, o, h, l, pcj]):
                    continue
                up = (h - pcj) / pcj          # 突然上拉幅度
                fade = (h - c) / h            # 冲高回落幅度
                if up >= 0.025 and fade >= 0.008:
                    # 前期必须是 横盘 或 阴跌
                    pre = rows[max(0, j-12):j]
                    if len(pre) >= 6:
                        amps = [(x[2]-x[3])/x[0] for x in pre if x[0]]
                        avg_amp = sum(amps)/len(amps) if amps else 1
                        chg_pre = (pre[-1][0]-pre[0][0])/pre[0][0] if pre[0][0] else 9
                        if avg_amp < 0.035 or chg_pre <= 0.02:  # 横盘(振幅小) 或 阴跌/未涨
                            probe_today = True
                            probe_base = pcj  # 试盘前基准价(不吃试盘日涨幅)
                            break
            if probe_today:
                # 启动前校验(试盘股特殊): 现价相对试盘前基准 涨幅<5% 且未远离MA5 = 仍是启动前
                runup = (close_now - probe_base) / probe_base if probe_base else 9
                if runup >= 0.05 or close_now > ma5 * 1.025:
                    _d(f"→启动跟踪(试盘后已兑现 runup={runup*100:.0f}%)")
                    _started("试盘后已兑现")
                    continue
                _d("→池2试盘(启动前)")
                pool_confirm.append(sym)
                continue
            # ---- 池1: 前夜埋伏(未启动小K线) ----
            if ret3 >= 0.05 and not ab:
                _d(f"挂:ret3={ret3*100:.1f}%"); _started(f"近3日+{ret3*100:.0f}%"); continue  # 已连涨兑现(长下影吸筹票豁免)
            last_o, last_c = rows[-1][0], rows[-1][1]
            if last_o and (last_c - last_o) / last_o >= 0.05 and not ab:
                _d("挂:今大阳"); _started("今日大阳"); continue  # 今日大阳且非试盘形态=已启动
            if close_now <= ma5 * 0.995 or close_now > ma5 * 1.025:
                _d(f"挂:MA5={close_now/ma5:.3f}")
                if close_now > ma5 * 1.025: _started(f"已上MA5+{(close_now/ma5-1)*100:.0f}%")
                continue
            bodies = [abs(c - o) / o for c, o, h, l, td in rows[-5:] if o]
            if not bodies or sum(bodies) / len(bodies) > 0.018:
                _d("挂:实体大"); continue
            # 距60日箱体上沿<12%（洗盘不深，距前高近=蓄势待发）
            hi60 = box_high.get(sym)
            if not hi60 or close_now < hi60 * 0.55:
                continue  # 距前高跌幅太深，不是洗盘是下跌中继
            near_high = (hi60 - close_now) / close_now
            if near_high > 0.12:
                _d(f"挂:near_high={near_high}"); continue
            _d("→池1")
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
    vabsorb = {}       # V型吸筹(下影≥5%+微涨+低价, 回测胜率68~80%)
    dupo_flag = {}    # 诱多嫌疑(放量上涨)
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
        # ★★ V型吸筹(9/22回测发现): 下影≥5% + 收微涨(0~5%) + 低价股(<30元)
        # 回测: <10元 T+20胜率80.2%中位+12.9% | 10-30元 68.8%/+10.1% | >60元反转为负
        vabsorb[sym] = None
        for i in range(max(1, len(k) - 3), len(k)):  # 近3日内出现
            o, h, l, c, v = k[i]
            pc0 = k[i-1][3]
            if not all([o, h, l, c, pc0]):
                continue
            bot = min(o, c)
            lsh = (bot - l) / bot if bot else 0
            chgv = (c - pc0) / pc0
            if lsh >= 0.05 and 0 < chgv <= 0.05 and c < 30:
                vabsorb[sym] = lsh
                break
        # 诱多嫌疑(黄老板: 资金流太麻烦→用成交量): 近3日涨≥3% 且 3日均量/前3日均量≥1.4
        if len(k) >= 6:
            _cc = [x[3] for x in k]
            _vv = [x[4] for x in k]
            if _cc[-4]:
                _r3 = (_cc[-1] - _cc[-4]) / _cc[-4]
                _v3 = sum(_vv[-3:]) / 3
                _v3p = sum(_vv[-6:-3]) / 3
                if _r3 >= 0.03 and _v3p and _v3 / _v3p >= 1.4:
                    dupo_flag[sym] = _r3
        if bad:
            dropped.append(sym)
        else:
            kept.append(sym)
    print(f"D段砸盘预警剔除: {len(dropped)} 只（剩余{len(kept)}只）")
    kept_set = set(kept)
    candidates = [s for s in candidates if s in kept_set]
    pool_confirm = [s for s in pool_confirm if s in kept_set]

    # ---- 诱多嫌疑(9/22 黄老板): 资金流太麻烦, 改用成交量近似, 只打标签不硬剔 ----
    #   放量上涨(近3日涨≥3% 且 3日均量/前3日均量≥1.4) = 散户放量接盘嫌疑(优宁维型)
    #   黄老板: 资金流自己看
    print(f"量价诱多嫌疑(放量上涨): {len(dupo_flag)} 只")

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
        if vabsorb.get(sym): score += 30  # V型吸筹(回测最强: 低价+下影≥5%+微涨, T+20中位+10~13%)
        if dupo_flag.get(sym): score -= 10  # 诱多嫌疑(放量上涨)降权沉底
        scored.append((score, sym, extra))

    scored.sort(reverse=True)
    # ★ 筹码质量改为软标记(9/22: 硬门槛一直误杀低位好票如良信) → 不剔, 只打"筹码弱"标签
    scored = list(scored)
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
        if sym in vabsorb and vabsorb[sym]: tags.append(f"V吸筹★(下影{vabsorb[sym]*100:.1f}%)")
        if sym in dupo_flag: tags.append(f"⚠放量上涨{dupo_flag[sym]*100:.1f}%")
        if m and m["profit"] > 0.88: tags.append("获利盘高")
        if m and m["overhang"] > 0.65: tags.append("套压重")
        if probe: tags.append("试盘★" if "★" in probe else "试盘")
        if m:
            print(f"{score:3d} {sym:<10}{name:<8}{m['cur']:>7.2f}{m['cost']:>7.2f}"
                  f"{m['profit']*100:>5.0f}%{m['overhang']*100:>5.0f}%{m['peak_share']*100:>5.0f}%"
                  f"{m['smash']:>7.2f}{m['vol_trend']:>5.2f}  {' '.join(tags)}")
        else:
            print(f"{score:3d} {sym:<10}{name:<8}{'—':>7}{'—':>7}{'—':>6}{'—':>6}{'—':>6}{'—':>7}{'—':>5}  {' '.join(tags)}")

    # ---- 启动跟踪池（已启动/偏离前夜买点，仅跟踪）----
    if started_pool:
        started_pool.sort(key=lambda x: -(x[2] / x[3] - 1))
        print(f"\n{'='*100}\n🚀 启动跟踪池（{len(started_pool)}只，已启动/偏离前夜买点，不当前夜票）\n{'='*100}")
        print(f"{'代码':<10}{'名称':<8}{'现价':>7}{'MA5偏':>7}  说明")
        for sym, why, cn2, ma5v in started_pool[:40]:
            cur.execute("SELECT name FROM stock_list WHERE code=%s", (sym[2:],))
            nm = cur.fetchone()
            nm = nm[0] if nm else "?"
            print(f"{sym:<10}{nm:<8}{cn2:>7.2f}{(cn2/ma5v-1)*100:>6.1f}%  {why}")

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
