#!/usr/bin/env python3
"""
今日试盘扫描器 — 每日收盘后运行
================================
信号定义（黄老板口径）:
  上影试盘: 放量(量比>=1.3) + 冲高>=2%(相对昨收) + 回落0.5~3%(高点→收盘)
            [回落>3%的是砸盘预警,单独列黑名单]
  下影试盘: 放量(量比>=1.3) + 探底>=2%(低点低于昨收2%) + 收回>=2%(低点→收盘)
            + 未破位(收盘跌幅<3%)

整体走势背景（排除高位出货/垃圾票）:
  - 距一年低点 < 45% (低位区)
  - 近60日涨幅 < 40% (未爆炒)
  - 非ST/退市
  - 近20日均量 >= 60~20日均量×0.8 (前期有量能积累,非死票)

输出按: 信号新鲜度(今日>昨日) + 背景质量 排序
"""
import psycopg2
from collections import defaultdict
from datetime import date

PG = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

def main():
    conn = psycopg2.connect(PG)
    cur = conn.cursor()
    # 近120个交易日数据(一年低点用250日,取现有窗口)
    cur.execute("""
        SELECT symbol, trade_date, open, high, low, close, volume
        FROM daily_prices
        WHERE trade_date >= '2025-09-01' AND symbol LIKE 'A.%'
        ORDER BY symbol, trade_date
    """)
    daily = defaultdict(list)
    for sym, td, o, h, l, c, v in cur.fetchall():
        daily[sym].append((str(td), o, h, l, c, v))
    cur.execute("SELECT code, name FROM stock_list")
    names = {'A.' + r[0]: r[1] for r in cur.fetchall()}
    cur.execute("SELECT code FROM stock_list WHERE name LIKE '%%ST%%' OR name LIKE '%%退%%'")
    st = {'A.' + r[0] for r in cur.fetchall()}
    conn.close()

    results = []  # (sortkey, 行)
    blacklist = []  # 砸盘预警
    latest_date = max(k[-1][0] for k in daily.values())
    print(f"数据截至: {latest_date}\n")

    for sym, k in daily.items():
        if sym in st or len(k) < 80 or sym not in names or names.get(sym, '').startswith(('N', 'C')):
            continue
        n = len(k)
        # 背景检查(以最近一天为基准)
        c_now = k[-1][4]
        if not c_now:
            continue
        # 一年低点(近250日)
        lo250 = min(x[3] for x in k[-250:] if x[3])
        pos_low = (c_now - lo250) / lo250
        if pos_low >= 0.45:
            continue  # 不在低位区
        # 近60日涨幅
        c60 = k[-61][4] if n >= 61 else k[0][4]
        if c60 and (c_now - c60) / c60 >= 0.40:
            continue  # 已爆炒
        # 近5日累计涨幅<8%(排除已连续拉升)
        c5 = k[-6][4] if n >= 6 else k[0][4]
        if c5 and (c_now - c5) / c5 >= 0.08:
            continue
        # 近3日无单日大跌>5%(排除出货中)
        if any(x[4] and x[5] and k[j-1][4] and (x[4] - k[j-1][4]) / k[j-1][4] < -0.05 for j, x in enumerate(k[-3:], n-3) if j > 0):
            continue
        # 量能背景: 近20日均量 vs 60~20日均量
        if n >= 61:
            v20 = sum(x[5] or 0 for x in k[-20:]) / 20
            v40 = sum(x[5] or 0 for x in k[-60:-20]) / 40
            if v40 == 0 or v20 < v40 * 0.8:
                continue  # 死票无资金

        # 扫描近5日的试盘信号
        for i in range(n - 5, n):
            if i < 6:
                continue
            td, o, h, l, c, v = k[i]
            pc = k[i-1][4]
            if not all([o, h, l, c, v, pc]):
                continue
            vol5 = sum(x[5] or 0 for x in k[i-5:i]) / 5
            if vol5 == 0 or v / vol5 < 1.3:
                continue
            vr = v / vol5
            days_ago = n - 1 - i
            if vr > 3.5:
                continue  # 量比过大可能是消息股非试盘
            base = (sym, td, days_ago, vr)

            # --- 上影试盘 ---
            up = (h - pc) / pc          # 冲高幅度
            dd = (h - c) / h            # 回落幅度
            if up >= 0.02 and 0.005 <= dd <= 0.03:
                score = (5 if days_ago == 0 else 3 if days_ago == 1 else 1 if days_ago == 2 else 0)
                if pos_low < 0.15: score += 4
                elif pos_low < 0.25: score += 2
                if 1.5 <= vr <= 2.5: score += 2
                elif 1.3 <= vr <= 3: score += 1
                # 洗盘特征: 信号前5日振幅收窄+缩量
                pre5 = k[max(0,i-5):i]
                if pre5 and pre5[-1][5]:
                    vol_pre5 = sum(x[5] or 0 for x in pre5) / 5
                    vol_pre10 = sum(x[5] or 0 for x in k[max(0,i-10):i-5]) / 5 if i >= 10 else vol_pre5
                    if vol_pre10 > 0 and vol_pre5 < vol_pre10 * 0.85:
                        score += 3
                results.append((score, f"{sym[2:]} {names.get(sym):　<6} {td} ▲上影 冲高{up*100:.1f}% 回落{dd*100:.1f}% 量比{vr:.1f} 距低点{pos_low*100:.0f}%"))
            # 砸盘预警(黑名单)
            if up >= 0.02 and dd > 0.03 and days_ago == 0 and sym in names:
                blacklist.append(f"{sym[2:]} {names.get(sym):　<6} {td} ⚠冲高回落{dd*100:.1f}%>3% 抛压大")

            # --- 下影试盘 ---
            dn = (pc - l) / pc          # 探底深度(相对昨收)
            rec = (c - l) / l           # 低点→收盘回升
            brk = (c - pc) / pc         # 收盘相对昨收(未破位)
            if dn >= 0.02 and rec >= 0.02 and brk > -0.03:
                score = (5 if days_ago == 0 else 3 if days_ago == 1 else 1 if days_ago == 2 else 0)
                if pos_low < 0.15: score += 4
                elif pos_low < 0.25: score += 2
                if 1.5 <= vr <= 2.5: score += 2
                elif 1.3 <= vr <= 3: score += 1
                pre5 = k[max(0,i-5):i]
                if pre5 and pre5[-1][5]:
                    vol_pre5 = sum(x[5] or 0 for x in pre5) / 5
                    vol_pre10 = sum(x[5] or 0 for x in k[max(0,i-10):i-5]) / 5 if i >= 10 else vol_pre5
                    if vol_pre10 > 0 and vol_pre5 < vol_pre10 * 0.85:
                        score += 3
                results.append((score, f"{sym[2:]} {names.get(sym):　<6} {td} ▼下影 探底{dn*100:.1f}% 收回{rec*100:.1f}% 量比{vr:.1f} 距低点{pos_low*100:.0f}%"))

    results.sort(key=lambda x: -x[0])
    print(f"=== 试盘信号候选(近5日, 强背景过滤后) 共{len(results)}条 ===")
    for s, line in results[:30]:
        print(f"[{s:2d}] {line}")
    print(f"\n=== 今日砸盘预警(回落>3%) {len(blacklist)}条 ===")
    for line in blacklist[:15]:
        print(f"  {line}")

if __name__ == "__main__":
    main()
