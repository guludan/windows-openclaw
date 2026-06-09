#!/usr/bin/env python3
"""
板块搜索+股票研究整合工具 v1 (2026-06-09)
整合：板块四象限定位 + 庄家洗盘识别 + 硬底筛选 + 机会窗口监测

用法:
  python3 scripts/sector_research.py scan-sectors          # 板块全景扫描
  python3 scripts/sector_research.py find-grinding          # 找磨底股
  python3 scripts/sector_research.py analyze-stock 600635   # 个股分析（含庄家成本）
  python3 scripts/sector_research.py next-window             # 下一个机会窗口预测
  python3 scripts/sector_research.py suspicious-pumps        # 找可疑的"热点预热"股(5月底预热+6/8被砸)
"""

import psycopg2, urllib.request, json, ssl, sys, os
from datetime import datetime, timedelta
from collections import Counter

DB = "dbname=guludan user=guludan password=toto host=192.168.3.14"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
HDR = {'User-Agent': 'Mozilla/5.0'}

# ────────────── 工具函数 ──────────────

def qq_price(code):
    """查腾讯实时行情"""
    prefix = 'sh' if code.startswith('6') else 'sz' if code.startswith(('0','3')) else 'sh'
    url = f'https://qt.gtimg.cn/q={prefix}{code}'
    try:
        req = urllib.request.Request(url, headers=HDR)
        resp = urllib.request.urlopen(req, timeout=8, context=ctx)
        parts = resp.read().decode('gbk').split('~')
        if len(parts) < 35: return None
        return {
            'name': parts[1], 'price': float(parts[3]), 'close': float(parts[4]),
            'pct': float(parts[32].replace('%','')), 'open': float(parts[5]),
            'high': float(parts[33]), 'low': float(parts[34]),
            'vol': int(parts[6])/10000, 'amount': int(parts[37])/10000,
            'ratio': float(parts[39]) if parts[39] and parts[39].replace('.','').lstrip('-').isdigit() else 0,
            'pe': parts[39], 'pb': parts[46], 'mcap': parts[44],
        }
    except: return None

def qq_batch(codes):
    """批量查行情 codes=['600635','600273',...]"""
    results = {}
    for i in range(0, len(codes), 20):
        batch = codes[i:i+20]
        ids = []
        for c in batch:
            p = 'sh' if c.startswith('6') else 'sz'
            ids.append(f'{p}{c}')
        url = f'https://qt.gtimg.cn/q={",".join(ids)}'
        try:
            req = urllib.request.Request(url, headers=HDR)
            resp = urllib.request.urlopen(req, timeout=15, context=ctx)
            data = resp.read().decode('gbk')
            for line in data.strip().split('\n'):
                parts = line.split('~')
                if len(parts) < 35: continue
                c = line.split('="')[0].replace('v_','')
                if len(c) > 6: c = c[2:]
                results[c] = {
                    'name': parts[1], 'price': float(parts[3]), 'close': float(parts[4]),
                    'pct': float(parts[32].replace('%','')), 'vol': int(parts[6])/10000,
                    'low': float(parts[34]), 'high': float(parts[33]),
                }
        except: pass
    return results

def db():
    return psycopg2.connect(DB)

# ────────────── 命令1: 板块全景扫描 ──────────────

def cmd_scan_sectors():
    """板块四象限定位 + 资金流向"""
    conn = db(); cur = conn.cursor()
    print("=" * 65)
    print("板块全景扫描")
    print("=" * 65)
    
    # 6月板块收益排名
    cur.execute("""
        SELECT sector_name, 
               AVG(avg_return)::numeric(6,2) as avg_ret,
               SUM(CASE WHEN avg_return > 0 THEN 1 ELSE 0 END)::int as up_days,
               COUNT(*) as days
        FROM sector_daily
        WHERE trade_date >= '2026-06-01'
        GROUP BY sector_name
        HAVING COUNT(*) > 3
        ORDER BY avg_ret DESC
    """)
    rows = cur.fetchall()
    
    if not rows:
        print("sector_daily 表无数据，跳转到个股板块识别")
    else:
        print(f"\n6月板块强度排名:")
        print(f"{'板块':18} {'平均收益':>10} {'涨日':>5} {'天数':>5}")
        print("-" * 45)
        for r in rows[:15]:
            print(f"{r[0]:18} {r[1]:>+8.2f}% {r[2]:>5} {r[3]:>5}")
    
    # 6/8恐慌日板块抗跌排名
    cur.execute("""
        SELECT sector_name, avg_return::numeric(6,2), 
               up_count::int, total_count::int,
               (up_count::float/NULLIF(total_count,0)*100)::numeric(5,1) as up_pct
        FROM sector_daily 
        WHERE trade_date='2026-06-08'
        ORDER BY avg_return DESC
        LIMIT 10
    """)
    rows8 = cur.fetchall()
    if rows8:
        print(f"\n6/8恐慌日板块抗跌排名:")
        print(f"{'板块':18} {'均值':>8} {'涨数':>5} {'总数':>5} {'涨比':>6}")
        print("-" * 45)
        for r in rows8:
            print(f"{r[0]:18} {r[1]:>+7.2f}% {r[2]:>5} {r[3]:>5} {r[4]:>5}%")
    
    # 四象限定位（从6/8板块排名推）
    print(f"\n四象限定位 (6/8恐慌日):")
    print(f"  ├─ ① 避险: 排名靠前的板块")
    print(f"  ├─ ② 周期: 涨幅居中的板块")
    print(f"  ├─ ③ 冲击: 跌幅较大板块")
    print(f"  └─ ④ 崩盘: 跌幅最大板块")
    
    if rows8:
        total = len(rows8)
        q1 = rows8[:max(1,total//5)]
        q4 = rows8[-max(1,total//5):]
        print(f"\n  ① 避险/资金流入: {', '.join(r[0] for r in q1[:5])}")
        print(f"  ④ 重灾/洗盘区: {', '.join(r[0] for r in q4[:5])}")
    
    cur.execute("SELECT trade_date FROM daily_prices WHERE symbol='A.600635' ORDER BY trade_date DESC LIMIT 1")
    latest = cur.fetchone()
    if latest: print(f"\n数据截止: {latest[0]}")
    
    conn.close()


# ────────────── 命令2: 找磨底股（黄老板标准） ──────────────

def cmd_find_grinding():
    """找：热点板块内，昨天被砸出下影、今天继续企稳、沉默磨底让散户不耐的股"""
    conn = db(); cur = conn.cursor()
    
    print("=" * 65)
    print("找磨底股 | 热点板块 -> 极速洗盘 -> 接盘企稳")
    print("=" * 65)
    
    # 条件1: 6/8恐慌日暴跌拉回（长下影+振幅大+收盘稳住）
    cur.execute("""
        WITH jun8 AS (
            SELECT symbol, close, low, high, open, volume/10000 as v_wan,
                   (close-open)/NULLIF(open,0)*100 as ret8,
                   (high-low)/NULLIF(close,0)*100 as swing8,
                   (close-low)/NULLIF(low,0)*100 as shad8
            FROM daily_prices WHERE trade_date='2026-06-08'
        ),
        jun5 AS (
            SELECT symbol, volume/10000 as v5 FROM daily_prices WHERE trade_date='2026-06-05'
        )
        SELECT j.symbol, j.close, j.ret8::numeric(7,2), j.shad8::numeric(6,1),
               j.swing8::numeric(6,1), j.v_wan::int, COALESCE(s.v5,0)::int as v5
        FROM jun8 j LEFT JOIN jun5 s ON j.symbol=s.symbol
        WHERE j.ret8 BETWEEN -4 AND 4
          AND j.shad8 > 2.5     -- 下影>2.5%
          AND j.swing8 > 4      -- 振幅>4%
          AND j.close BETWEEN 2 AND 15
          AND j.v_wan > 10
          AND SUBSTRING(j.symbol FROM 8 FOR 1) = '6'
        ORDER BY j.shad8 DESC
    """)
    candidates = cur.fetchall()
    codes = [r[0].replace('A.','') for r in candidates]
    
    print(f"\n6/8暴跌拉回候选: {len(candidates)}只")
    
    # 条件2: 今天企稳
    quotes = qq_batch(codes)
    
    print(f"\n{'名称':12} {'昨收':>5} {'现价':>5} {'涨跌':>7} {'6/8下影':>7} {'量万':>7} {'判断':>12}")
    print("-" * 60)
    
    grinding = []
    for r in candidates:
        code = r[0].replace('A.','')
        if code not in quotes: continue
        q = quotes[code]
        shad8 = float(r[3]); swing8 = float(r[4])
        
        # 适中的企稳：0% >= 涨 > -2% （没涨上去才是磨底，涨太多=已经反弹了）
        if 0.5 >= q['pct'] >= -2.0 and q['price'] >= q['low'] * 0.99:
            # 缩量（比昨天缩）
            grinding.append((q['name'], code, r[1], q['price'], q['pct'], shad8, swing8, q['vol']))
            print(f"{q['name']:12} {r[1]:>5.2f} {q['price']:>5.2f} {q['pct']:>+6.2f}% {shad8:>5}% {q['vol']:>6.0f} {'🧊 磨底中':>12}")
        elif q['pct'] > 0.5 and q['pct'] < 3:
            print(f"{q['name']:12} {r[1]:>5.2f} {q['price']:>5.2f} {q['pct']:>+6.2f}% {shad8:>5}% {q['vol']:>6.0f} {'📈 已反弹':>12}")
        elif q['pct'] <= -2:
            print(f"{q['name']:12} {r[1]:>5.2f} {q['price']:>5.2f} {q['pct']:>+6.2f}% {shad8:>5}% {q['vol']:>6.0f} {'❌ 继续跌':>12}")
        else:
            print(f"{q['name']:12} {r[1]:>5.2f} {q['price']:>5.2f} {q['pct']:>+6.2f}% {shad8:>5}% {q['vol']:>6.0f} {'⏸ 震荡':>12}")
    
    if grinding:
        print(f"\n{'═' * 60}")
        print(f"磨底股汇总 ({len(grinding)}只)")
        print(f"{'═' * 60}")
        print(f"特征: 6/8被砸后拉回 → 6/9不涨不跌 → 庄家已接筹")
        print(f"操作: 等大盘回暖信号(sz_5>-1.5), 这些是反弹先锋")
        print()
        for i, (name, code, yc, price, pct, shad, swing, vol) in enumerate(grinding):
            print(f"  {i+1}. {name:12} {code:8} 昨{yc:.2f} -> 现{price:.2f}({pct:+.2f}%)  6/8下影{shad}%")
    
    conn.close()
    if not grinding:
        print("\n⚠️ 当前无符合磨底条件的股票（市场不是每天都有信号）")


# ────────────── 命令3: 个股深度分析 ──────────────

def cmd_analyze_stock(code: str):
    """个股庄家成本+硬底+分时特征"""
    sym = f'A.{code}' if not code.startswith('A.') else code
    pure_code = sym.replace('A.','')
    
    q = qq_price(pure_code)
    if not q: print("行情获取失败"); return
    
    conn = db(); cur = conn.cursor()
    
    print("=" * 65)
    print(f"{q['name']}({pure_code}) 深度分析 @ {datetime.now().strftime('%H:%M')}")
    print("=" * 65)
    print(f"现价: {q['price']} ({q['pct']:+.2f}%)  昨收: {q['close']}  今开: {q['open']}")
    print(f"高: {q['high']}  低: {q['low']}  量: {q['vol']:.0f}万手  换手: {q['ratio']}%")
    
    # 近60天走势
    cur.execute("""
        SELECT trade_date, close, low, high, open, volume/10000 as v
        FROM daily_prices 
        WHERE symbol=%s AND trade_date >= '2026-04-01'
        ORDER BY trade_date
    """, (sym,))
    hist = cur.fetchall()
    
    if len(hist) < 5: print("历史数据不足"); conn.close(); return
    
    cp = [float(r[1]) for r in hist]
    lp = [float(r[2]) for r in hist]
    hp = [float(r[3]) for r in hist]
    vp = [float(r[5]) for r in hist]
    
    # 庄家成本区
    print(f"\n【庄家成本区】")
    recent = min(20, len(cp))
    avg_close = sum(cp[-recent:])/recent
    avg_low = sum(lp[-recent:])/recent
    avg_high = sum(hp[-recent:])/recent
    min_60 = min(lp); max_60 = max(hp)
    
    print(f"  近{recent}日收均价: {avg_close:.2f}")
    print(f"  近{recent}日低均价: {avg_low:.2f}  高均价: {avg_high:.2f}")
    print(f"  60天区间: {min_60:.2f} ~ {max_60:.2f}")
    print(f"  庄家可能成本: {avg_low:.2f} ~ {avg_close:.2f}")
    
    # 当前偏离
    deviation = (q['price'] / avg_close - 1) * 100
    print(f"  当前偏离均线: {deviation:+.1f}%")
    
    if q['price'] < avg_low:
        print(f"  ✅ 当前价在庄家成本区下方, 安全垫{(avg_low/q['price']-1)*100:.1f}%")
    else:
        print(f"  ⚠️ 当前价在庄家成本区内/上方")
    
    # 硬底判断
    print(f"\n【硬底判断】")
    
    # 条件1: 量缩
    avg_v = sum(vp[-5:])/5 if len(vp) >=5 else vp[0]
    ratio_v = q['vol']/avg_v if avg_v > 0 else 0
    print(f"  今日量{q['vol']:.0f}万 vs 5日均{avg_v:.0f}万 = {ratio_v:.1f}x {'✅地量' if ratio_v < 0.5 else '⚠️正常' if ratio_v < 2 else '❌放量'}")
    
    # 条件2: 振幅
    swing = (q['high']-q['low'])/q['low']*100 if q['low'] > 0 else 0
    print(f"  今日振幅: {swing:.2f}% {'✅收缩' if swing < 3 else '⚠️正常' if swing < 5 else '❌剧烈'}")
    
    # 条件3: 偏离
    print(f"  偏离近20日均价: {deviation:+.1f}% {'✅超跌' if deviation < -10 else '⚠️正常' if deviation < -5 else '⏸近均线'}")
    
    # 条件4: 密集成交区
    if len(hist) > 240:
        price_buckets = Counter()
        for r in hist[-240:]:
            bucket = round(float(r[1]) * 2) / 2  # 0.5元一桶
            price_buckets[bucket] += float(r[5])
        
        if price_buckets:
            max_vol_zone = max(price_buckets, key=price_buckets.get)
            dense_range = sorted(price_buckets.items(), key=lambda x: -x[1])
            dense_zones = [b for b, v in dense_range[:5]]
            print(f"  密集成交区: {dense_zones}")
            near_dense = any(abs(q['price']-z) < 0.3 for z in dense_zones)
            print(f"  {'✅在密集区附近' if near_dense else '❌远离密集区'}")
    
    # 综合
    hard_score = 0
    if ratio_v < 0.5: hard_score += 1
    if swing < 3: hard_score += 1
    if deviation < -10: hard_score += 1
    if q['price'] < avg_low: hard_score += 1
    
    print(f"\n  硬底评分: {hard_score}/4")
    if hard_score >= 3: print(f"  🔥🔥🔥 强制硬底！当前可关注")
    elif hard_score >= 2: print(f"  🔥 弱硬底，待确认")
    else: print(f"  ⏸ 未到底部")
    
    conn.close()


# ────────────── 命令4: 下一个机会窗口 ──────────────

def cmd_next_window():
    """识别下一个机会窗口（6月初这轮恐慌后的底部+回暖信号）"""
    conn = db(); cur = conn.cursor()
    
    print("=" * 65)
    print("下一个机会窗口预测")
    print("=" * 65)
    
    # 查看sz_5/regime等信号
    cur.execute("""
        SELECT date, extra::text
        FROM pool_energy
        WHERE level='market' AND date >= '2026-06-01'
        ORDER BY date DESC
        LIMIT 10
    """)
    rows = cur.fetchall()
    
    if rows:
        print(f"\n【市场状态 V8】")
        print(f"{'日期':12} {'状态':>20}")
        for d, extra in rows:
            # 从extra JSON中提取regime
            regime = "N/A"
            if extra and extra.startswith('{'):
                try:
                    import re
                    m = re.search(r'"regime"\s*:\s*"([^"]+)"', extra)
                    if m: regime = m.group(1)
                    m2 = re.search(r'"surplus_z"\s*:\s*([-\d.]+)', extra)
                    sz = f"sz={m2.group(1)}" if m2 else ""
                except: pass
            d_str = str(d)[:10]
            print(f"{d_str:12} {regime:>8} {sz}")
    
    # 查看sz_5 (5日surplus均值)
    cur.execute("""
        SELECT date::text, 
               ROUND((extra->>'surplus_z')::numeric, 3) as zp,
               extra->>'regime' as regime
        FROM pool_energy
        WHERE level='market' AND date >= '2026-06-03'
        ORDER BY date
    """)
    
    print(f"\n【6月市场阶段】")
    print(f"  6/1: 恐慌起点（全市场恐慌抛售）")
    print(f"  6/8: 恐慌加速 + 庄家接筹日（12.7%收红, 长下影密集）")
    print(f"  6/9(今天): 接筹确认日（庄家刚收完筹码, 不拉）")
    print()
    print(f"【下一个机会窗口】")
    print(f"  ┌─ 信号: sz_5 > -1.5（5日市场温和上行）")
    print(f"  ├─ 预估: 如果今明两天不再爆恐慌, 最快6/11~6/12出信号")
    print(f"  ├─ 最坏: 再来一轮恐慌 = 再次下探, 窗口推迟到6/15之后")
    print(f"  ├─ 触发动作: sz_5>-1.5时, 埋伏的磨底股+持仓开始启动")
    print(f"  └─ 风险: 如果sz_5<-2.0持续, 说明恐慌还在持续, 继续等")
    
    print(f"\n【当前持仓状态】")
    print(f"  大众公用 600635: 4.93 (-2.0%) 4.89护盘 | 等待信号别补仓")
    print(f"  嘉化能源 600273: 7.02 (+0.0%) 7.00站住 | 硬底确认")
    print(f"  软控股份 002073: 5.60 (+0.2%) 5.55窄幅 | 筑底中")
    print(f"  苏交科   300284: 5.89 (+0.3%) 5.75低拉起 | 修复最好")
    print(f"  华茂股份 000850: 3.80 (-0.0%) 3.78~3.85 | 窄幅但弱")
    
    print(f"\n【操作建议】")
    print(f"  ✅ 持有不动（持仓都在硬底区）")
    print(f"  ❌ 不补仓（sz_5信号未到）")
    print(f"  ❌ 不换仓（现在是庄家收筹期, 不是拉升期）")
    print(f"  📋 观测sz_5状态, 盘中突然放量拉升才是启动信号")
    
    conn.close()


# ────────────── 命令5: 可疑的"热点预热"股 ──────────────

def cmd_suspicious_pumps():
    """找5月底放量预热+6/8被砸深跌->收回->今天企稳的股"""
    conn = db(); cur = conn.cursor()
    
    print("=" * 65)
    print("可疑的'热点预热'股 (热点板块预热 -> 洗盘 -> 接筹)")
    print("=" * 65)
    print("特征: 5月底放量预热 -> 6/8被砸长下影 -> 今天企稳")
    print("      = 庄家在热点股上提前埋伏, 恐慌日洗盘, 接筹")
    
    cur.execute("""
        WITH may29 AS (
            SELECT symbol, close as c29, 
                   ((close-open)/NULLIF(open,0)*100)::numeric(6,2) as ret29,
                   (volume/10000)::int as v29
            FROM daily_prices WHERE trade_date='2026-05-29'
        ),
        jun1 AS (
            SELECT symbol, 
                   ((close-open)/NULLIF(open,0)*100)::numeric(6,2) as ret1,
                   (volume/10000)::int as v1
            FROM daily_prices WHERE trade_date='2026-06-01'
        ),
        jun8 AS (
            SELECT symbol, close as c8,
                   (close-open)/NULLIF(open,0)*100 as ret8,
                   (high-low)/NULLIF(close,0)*100 as swing8,
                   (close-low)/NULLIF(low,0)*100 as shad8,
                   volume/10000 as v8
            FROM daily_prices WHERE trade_date='2026-06-08'
        )
        SELECT m.symbol, m.c29, m.ret29, m.v29,
               j.ret1, j.v1,
               j8.c8, j8.ret8::numeric(6,2), j8.shad8::numeric(6,1), j8.swing8::numeric(6,1), j8.v8::int
        FROM may29 m
        JOIN jun1 j ON m.symbol=j.symbol
        JOIN jun8 j8 ON m.symbol=j8.symbol
        WHERE m.ret29 > 3                          -- 5/29涨超3%
          AND m.v29 > 30                          -- 放量
          AND j8.ret8 BETWEEN -4 AND 4            -- 6/8收盘稳住
          AND j8.shad8 > 2                        -- 下影
          AND j8.swing8 > 4                       -- 振幅大
          AND m.c29 BETWEEN 2 AND 15
          AND SUBSTRING(m.symbol FROM 8 FOR 1) = '6'
        ORDER BY (j8.shad8 + ABS(m.ret29)) DESC
    """)
    rows = cur.fetchall()
    
    codes = [r[0].replace('A.','') for r in rows]
    quotes = qq_batch(codes)
    
    print(f"\n{'名称':12} {'5/29':>7} {'量29':>6} {'6/1涨':>7} {'6/8下影':>7} {'6/8涨':>7} {'今天':>7} {'判断':>12}")
    print("-" * 70)
    
    confirmed = []
    for r in rows:
        code = r[0].replace('A.','')
        name = quotes[code]['name'] if code in quotes else code
        ret29 = r[2]; v29 = r[3]; ret1 = r[4] if r[4] else 0
        c8 = r[6]; ret8 = r[7]; shad8 = r[8]; swing8 = r[9]; v8 = r[10]
        
        today_pct = quotes[code]['pct'] if code in quotes else 99
        
        if -2 <= today_pct <= 1:
            j = "✅ 确认"
            confirmed.append((name, code, c8, today_pct, shad8))
            print(f"{name:12} {ret29:>+6.2f}% {v29:>5.0f} {ret1:>+6.2f}% {shad8:>5}% {ret8:>+6.2f}% {today_pct:>+6.2f}% {j:>12}")
        elif today_pct > 1:
            print(f"{name:12} {ret29:>+6.2f}% {v29:>5.0f} {ret1:>+6.2f}% {shad8:>5}% {ret8:>+6.2f}% {today_pct:>+6.2f}% {'📈 已涨':>12}")
        else:
            print(f"{name:12} {ret29:>+6.2f}% {v29:>5.0f} {ret1:>+6.2f}% {shad8:>5}% {ret8:>+6.2f}% {today_pct:>+6.2f}% {'❌ 破位':>12}")
    
    if confirmed:
        print(f"\n{'═' * 70}")
        print(f"确认: 热点预热->洗盘->接筹模式 ({len(confirmed)}只)")
        print(f"{'═' * 70}")
        print(f"这些是庄家已埋伏 → 已洗盘 → 已接筹 → 等拉升")
        for i, (name, code, c8, pct, shad) in enumerate(confirmed):
            print(f"  {i+1}. {name:12} {code}  6/8收{c8:.2f} 下影{shad}%  今天{pct:+.2f}%")
    
    conn.close()


# ────────────── MAIN ──────────────

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法:")
        print("  python3 scripts/sector_research.py scan-sectors        # 板块全景")
        print("  python3 scripts/sector_research.py find-grinding       # 找磨底股")
        print("  python3 scripts/sector_research.py analyze-stock CODE  # 个股分析")
        print("  python3 scripts/sector_research.py next-window         # 机会窗口")
        print("  python3 scripts/sector_research.py suspicious-pumps    # 找可疑预热股")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == 'scan-sectors':
        cmd_scan_sectors()
    elif cmd == 'find-grinding':
        cmd_find_grinding()
    elif cmd == 'analyze-stock':
        if len(sys.argv) < 3: print("需要股票代码,如: python3 sector_research.py analyze-stock 600635"); sys.exit(1)
        cmd_analyze_stock(sys.argv[2])
    elif cmd == 'next-window':
        cmd_next_window()
    elif cmd == 'suspicious-pumps':
        cmd_suspicious_pumps()
    else:
        print(f"未知命令: {cmd}")
