#!/usr/bin/env python3
"""
V9 四层决策框架 v2.0 — 资金潮汐版
政策→板块→大盘→标的（和V8完全相反）
按周级别决策，日线只做执行确认

用法:
  python3 scripts/v9_framework.py run           # 全量决策（每天一次，收盘后）
  python3 scripts/v9_framework.py tide          # 资金潮汐分析（每周一次）
  python3 scripts/v9_framework.py score         # 板块真伪评分（每日）
  python3 scripts/v9_framework.py hot           # 热点持续性复查（每日）
  python3 scripts/v9_framework.py policy        # 政策嗅探（每周）
  python3 scripts/v9_framework.py trade         # 交易信号
  python3 scripts/v9_framework.py watch         # 交互式盯盘
"""

import urllib.request, json, ssl, psycopg2, re, sys, os
from datetime import datetime, timedelta
from collections import defaultdict

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
HDR = {'User-Agent': 'Mozilla/5.0'}

DB = "dbname=guludan user=guludan password=toto host=192.168.3.14"
REPORT_DIR = "/mnt/d/data/a-stock/reports"
LOG_DIR = "/mnt/d/data/a-stock/logs"
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

TODAY = datetime.now().strftime('%Y-%m-%d')
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

# ══════════════════════════════════════════════════
# 第一章：资金潮汐（按周框架，周级别信号）
# ══════════════════════════════════════════════════

def get_weekly_icbc(cur, weeks_back=11):
    """
    工商银行周线——机构资金潮汐温度计
    取最近N周工行周收盘变化，判定机构资金流向
    """
    cur.execute("""
        SELECT trade_date, close FROM daily_prices WHERE symbol='A.601398'
        AND trade_date >= %s ORDER BY trade_date
    """, ((datetime.now()-timedelta(weeks=weeks_back+2)).strftime('%Y-%m-%d'),))
    rows = [(str(r[0]), float(r[1])) for r in cur.fetchall()]
    
    # 按周聚合（取每周最后一个交易日）
    from collections import OrderedDict
    weekly = OrderedDict()
    for d, px in rows:
        dt = datetime.strptime(d, '%Y-%m-%d')
        week_key = dt.strftime('%Y-W%W')
        weekly[week_key] = {'date': d, 'close': px}
    
    # 周变化
    week_keys = list(weekly.keys())
    results = []
    for i in range(1, len(week_keys)):
        k = week_keys[i]
        pk = week_keys[i-1]
        chg = (weekly[k]['close'] - weekly[pk]['close']) / weekly[pk]['close'] * 100
        results.append({
            'week': k, 'date': weekly[k]['date'],
            'close': weekly[k]['close'], 'pct': chg
        })
    
    # 资金潮汐判定
    # 最近N周的正涨/负跌
    recent = results[-8:] if len(results) >= 8 else results
    up_weeks = sum(1 for r in recent if r['pct'] > 0)
    down_weeks = sum(1 for r in recent if r['pct'] < -0.3)
    streak = 0
    for r in reversed(recent):
        if r['pct'] > 0: streak += 1
        else: break
    
    return {
        'weekly': results,
        'up_weeks': up_weeks,
        'down_weeks': down_weeks,
        'streak': streak,
        'total': len(recent),
        'last': results[-1] if results else None,
        'trend': '涨' if up_weeks > len(recent)*0.6 else ('跌' if down_weeks > len(recent)*0.4 else '震荡'),
    }


def get_weekly_sectors(cur):
    """
    板块周级别资金流——用ETF周收益看资金轮动
    """
    etf_list = {
        'A.512480': '半导体', 'A.512760': '芯片', 'A.515070': 'AI/算力',
        'A.515050': '通信', 'A.515030': '新能源', 'A.159611': '电力设备',
        'A.562500': '机器人',
    }
    start = (datetime.now() - timedelta(weeks=12)).strftime('%Y-%m-%d')
    
    results = []
    for sym, name in etf_list.items():
        cur.execute("""
            SELECT trade_date, close FROM daily_prices WHERE symbol=%s
            AND trade_date >= %s ORDER BY trade_date
        """, (sym, start))
        rows = cur.fetchall()
        if len(rows) < 5: continue
        
        # 逐周
        weekly = OrderedDict()
        for d, px in rows:
            dt = d if isinstance(d, str) else d.strftime('%Y-%m-%d')
            week_key = datetime.strptime(dt, '%Y-%m-%d').strftime('%Y-W%W')
            weekly[week_key] = {'date': dt, 'close': float(px)}
        
        wk = list(weekly.keys())
        this_week = weekly[wk[-1]]['close'] if wk else 0
        last_week = weekly[wk[-2]]['close'] if len(wk) > 1 else this_week
        w2_ago = weekly[wk[-3]]['close'] if len(wk) > 2 else this_week
        
        chg1 = (this_week - last_week)/last_week*100
        chg2 = (this_week - w2_ago)/w2_ago*100
        
        results.append((name, chg1, chg2, this_week, last_week))
    
    return results


def tide():
    """
    资金潮汐分析（周级别核心决策框架）
    """
    print("=" * 70)
    print("  V9 资金潮汐分析 | 周级别机构资金流向")
    print("=" * 70)
    
    conn = psycopg2.connect(DB); cur = conn.cursor()
    
    # 1. 工行潮汐
    tide_data = get_weekly_icbc(cur)
    print(f"\n【潮汐温度计】工商银行近{tide_data['total']}周")
    print(f"  上涨周: {tide_data['up_weeks']}/{tide_data['total']} ({tide_data['up_weeks']/tide_data['total']*100:.0f}%)")
    print(f"  下跌周(>0.3%): {tide_data['down_weeks']}/{tide_data['total']} ({tide_data['down_weeks']/tide_data['total']*100:.0f}%)")
    print(f"  连续上涨: {tide_data['streak']}周" if tide_data['streak'] > 0 else f"  最近一周下跌")
    
    if tide_data['last']:
        l = tide_data['last']
        print(f"  最新: {l['week']} 周 {l['date']} 工行{l['close']:.2f} ({l['pct']:+.2f}%)")
    
    # 潮汐相位判定
    up_ratio = tide_data['up_weeks'] / tide_data['total']
    last_pct = tide_data['last']['pct'] if tide_data['last'] else 0
    
    if up_ratio > 0.7 and tide_data['streak'] >= 4:
        phase = "🔴 潮汐高潮（强避险）"
        capital_state = "机构全力避险，科技不可参与"
        position_limit = "仓位≤30%，不开新仓"
        action = "持有硬底标的等待"
    elif up_ratio > 0.5:
        phase = "🟡 潮汐上涨（温和避险）"
        capital_state = "机构在买银行，科技承压"
        position_limit = "仓位≤50%，不追高"
        action = "仅持有硬底标的"
    elif tide_data['down_weeks'] >= 2 and tide_data['streak'] < 0:
        phase = "🟢 潮汐退潮（资金流出银行）"
        capital_state = "机构开始出银行，资金可能转向"
        position_limit = "仓位≤70%，准备入场"
        action = "观察板块热点评分"
    elif tide_data['down_weeks'] >= 3:
        phase = "✅ 潮汐见底（资金大转向）"
        capital_state = "机构持续卖出银行，科技迎来机会"
        position_limit = "可加仓至100%"
        action = "关注板块真热点"
    else:
        phase = "⚪ 潮汐混沌"
        capital_state = "方向不明，观望"
        position_limit = "仓位≤30%"
        action = "等待信号明确"
    
    print(f"\n【潮汐相位】{phase}")
    print(f"  资金状态: {capital_state}")
    print(f"  仓位: {position_limit}")
    print(f"  操作: {action}")
    
    # 2. 板块周线
    print(f"\n【板块周线】近两周变化:")
    sectors = get_weekly_sectors(cur)
    print(f"{'板块':12} {'本周':>6} {'对比2周前':>8} {'趋势':10}")
    print("-" * 40)
    for name, w1, w2, this, last in sorted(sectors, key=lambda x: x[1], reverse=True):
        trend = "🟢转强" if w1 > w2 + 1 else ("🔴转弱" if w1 < w2 - 1 else "➖横盘")
        print(f"{name:12} {w1:>+5.2f}% {w2:>+7.2f}% {trend:10}")
    
    # 3. 综合判定
    print(f"\n【综合判定】")
    print(f"  {'='*50}")
    
    print(f"  当前是{phase}")
    print(f"  {capital_state}")
    print(f"\n  周级别规则（已固化）：")
    print(f"  ① 工行周涨幅>1% → 本周不参与任何科技板块")
    print(f"  ② 工行连续3周下跌 → 资金确认转向, 加仓窗口")
    print(f"  ③ 工行单周跌幅>1% → 开始关注, 等第2周确认")
    print(f"  ④ 周线信号优先于日线：日线波动是噪音")
    print(f"\n  参考：4/13~6/10共11周, 工行仅2周下跌(<0.5%)")
    print(f"        按周规则, 这11周根本不应该买科技股")
    
    conn.close()
    return phase, capital_state, action


# ══════════════════════════════════════════════════
# 第二章：舆论反转信号
# ══════════════════════════════════════════════════

def sentiment_pulse(sectors=None):
    """舆论反转映射：媒体恐慌→买入, 媒体吹涨→卖出"""
    print("=" * 70)
    print("  V9 舆论反转信号")
    print("=" * 70)
    
    conn = psycopg2.connect(DB); cur = conn.cursor()
    
    # 工行今日位置
    cur.execute("SELECT close FROM daily_prices WHERE symbol='A.601398' ORDER BY trade_date DESC LIMIT 1")
    icbc_r = cur.fetchone()
    icbc_pos = float(icbc_r[0]) if icbc_r else 7.5
    
    print(f"\n【工行温度】{icbc_pos:.2f}", end="")
    if icbc_pos > 7.5:
        print(" → 🔴 高位避险（清仓窗口）" )
    elif icbc_pos > 7.2:
        print(" → 🟡 高位（观望）")
    elif icbc_pos < 7.0:
        print(" → 🟢 低位（买入窗口）")
    else:
        print(" → ➖ 中性")
    
    print("\n【舆论反转规则】")
    print("  恐慌阶段（媒体/散户恐慌）→ 买入窗口")
    print("  狂热阶段（媒体/散户吹涨）→ 清仓窗口")
    print("  正常阶段 → 不操作")
    
    # 基于工行位置的情绪映射
    if icbc_pos > 7.5:
        sentiment = "恐慌（科技持有人在绝望）"
        action_desc = "买入窗口 —— 但需等工行跌回7.2以下"
    elif icbc_pos > 7.3:
        sentiment = "怀疑（持有人在犹豫要不要割）"
        action_desc = "持有等待, 不加仓不减仓"
    elif icbc_pos > 7.0:
        sentiment = "绝望（深套, 大量割肉）"
        action_desc = "关注, 准备入场"
    else:
        sentiment = "麻木/放弃"
        action_desc = "入场窗口开启"
    
    print(f"\n  当前情绪: {sentiment}")
    print(f"  映射操作: {action_desc}")
    
    conn.close()
    return sentiment


# ══════════════════════════════════════════════════
# 第三章：板块真伪评分
# ══════════════════════════════════════════════════

def score_sectors():
    """多维度板块评分: 涨跌+工行背离+周线验证"""
    print("=" * 70)
    print("  V9 板块真伪热点评分（日级别确认）")
    print("=" * 70)
    print("  核心: 板块涨+工行涨=假热点🔴 | 板块涨+工行跌=真热点🟢")
    
    etf_list = [
        ('512480','半导体'), ('512760','芯片'), ('515070','AI/算力'),
        ('515050','通信'), ('515030','新能源'), ('159611','电力设备'),
        ('562500','机器人'), ('159852','软件'),
    ]
    
    # 今日实时
    codes = ['sh'+c for c,_ in etf_list]
    url = 'https://qt.gtimg.cn/q=' + ','.join(codes)
    req = urllib.request.Request(url, headers=HDR)
    resp = urllib.request.urlopen(req, timeout=8, context=ctx)
    
    today_data = {}
    for line in resp.read().decode('gbk').strip().split('\n'):
        parts = line.split('~')
        if len(parts) < 35: continue
        today_data[parts[2]] = {
            'pct': float(parts[32].replace('%','')),
            'vol': int(parts[6])/10000,
            'price': float(parts[3]),
            'yclose': float(parts[4]),
        }
    
    # 工行今日
    req = urllib.request.Request('https://qt.gtimg.cn/q=sh601398', headers=HDR)
    resp = urllib.request.urlopen(req, timeout=5, context=ctx)
    icbc_data = resp.read().decode('gbk').split('~')
    icbc_today = float(icbc_data[32].replace('%',''))
    icbc_price = float(icbc_data[3])
    
    # 上证今日
    req = urllib.request.Request('https://qt.gtimg.cn/q=sh000001', headers=HDR)
    resp = urllib.request.urlopen(req, timeout=5, context=ctx)
    sh_today = float(resp.read().decode('gbk').split('~')[32].replace('%',''))
    
    conn = psycopg2.connect(DB); cur = conn.cursor()
    
    results = []
    for code, name in etf_list:
        td = today_data.get(code, {})
        tret = td.get('pct', 0)
        tvol = td.get('vol', 0)
        
        # 周线: 查数据库中本周ETF数据
        cur.execute("""
            SELECT close FROM daily_prices WHERE symbol=%s
            AND trade_date >= %s ORDER BY trade_date DESC LIMIT 1
        """, ('A.'+code, (datetime.now()-timedelta(days=7)).strftime('%Y-%m-%d')))
        r = cur.fetchone()
        week_close = float(r[0]) if r else None
        today_price = td.get('price', 0)
        week_chg = (today_price - week_close)/week_close*100 if week_close and today_price else 0
        
        # 评分指标
        
        # A. 日线涨跌幅分(25分)
        ret_score = 25 if tret > 3 else (18 if tret > 1 else (12 if tret > 0 else (8 if tret > -1 else 3)))
        
        # B. 工行背离分(35分—最关键)
        if tret > 0 and icbc_today < -0.3:
            icbc_score = 33  # 板块涨+工行跌=真热点
        elif tret > 0 and icbc_today > 0:
            icbc_score = 5   # 板块涨+工行涨=避险假热点
        elif tret > 0 and icbc_today <= 0:
            icbc_score = 25  # 板块涨+工行平
        elif tret < 0 and icbc_today > 0.5:
            icbc_score = 10  # 板块跌+工行暴涨=防御
        elif tret < -2:
            icbc_score = 15  # 板块大跌，可能是超卖
        else:
            icbc_score = 20
        
        # C. 周线延续分(20分)
        week_score = 0
        if week_chg > 3 and tret > 1:
            week_score = 18  # 周线+日线延续=真强
        elif week_chg > 0 and tret < 0:
            week_score = 5   # 周涨但日跌=回调
        elif week_chg > 0:
            week_score = 15  # 周线在涨
        elif week_chg < -3 and tret > 0:
            week_score = 12  # 周跌深但日反弹=试探
        else:
            week_score = 10
        
        # D. 大盘超额分(10分)
        excess = tret - sh_today
        excess_score = 10 if excess > 3 else (7 if excess > 1 else 4)
        
        # E. 工行绝对位置分(10分)
        if icbc_price > 7.5:
            pos_score = 2    # 工行高位=所有板块减分
        elif icbc_price > 7.2:
            pos_score = 5
        else:
            pos_score = 8    # 工行低位=加分
        
        total = ret_score + icbc_score + week_score + excess_score + pos_score
        total = min(total, 100)
        
        # 周级别滤网（潮汐滤网）
        tide_filter = ""
        if icbc_today > 1:
            tide_filter = " [潮汐滤网:工行周涨>1%→本周科技不可参与]"
            total = int(total * 0.6)  # 潮汐高位，所有评分打6折
        
        if total >= 75:
            verdict = "🟢真热点"
        elif total >= 60:
            verdict = "🟡观察"
        elif total >= 40:
            verdict = "🔴假热点"
        else:
            verdict = "🔴回避"
        
        results.append((name, tret, week_chg, total, verdict, tide_filter))
    
    # 输出
    print(f"\n{'板块':12} {'今日':>6} {'本周':>7} {'总分':>4} {'判定':>10} {'滤网'}")
    print("-" * 60)
    for r in sorted(results, key=lambda x: x[3], reverse=True):
        print(f"{r[0]:12} {r[1]:>+5.2f}% {r[2]:>+5.2f}% {r[3]:>3} {r[4]:>10} {r[5]}")
    
    print(f"\n【潮汐滤网】工行今日{icbc_today:.2f}% 位置{icbc_price:.2f}")
    if icbc_today > 1:
        print("  工行周涨>1% → 所有板块评分打6折 → 本周无真热点")
    elif icbc_today > 0.3:
        print("  工行微涨 → 评分正常,但偏谨慎")
    else:
        print("  工行下跌 → 评分正常")
    
    conn.close()


# ══════════════════════════════════════════════════
# 第四章：热点持续性复查
# ══════════════════════════════════════════════════

def hot_check():
    """复查昨日大涨板块是否一日游"""
    print("=" * 70)
    print("  V9 热点持续性复查")
    print("=" * 70)
    print("  规则: 昨日涨>3%的板块→今日验真→一日游判定")
    
    etf_list = {
        '512480':'半导体','512760':'芯片','515070':'AI/算力',
        '515050':'通信','515030':'新能源','159611':'电力设备','562500':'机器人'
    }
    
    # 今日实时
    codes = ['sh'+c for c in etf_list]
    url = 'https://qt.gtimg.cn/q=' + ','.join(codes)
    req = urllib.request.Request(url, headers=HDR)
    resp = urllib.request.urlopen(req, timeout=8, context=ctx)
    
    today = {}
    for line in resp.read().decode('gbk').strip().split('\n'):
        parts = line.split('~')
        if len(parts) < 35: continue
        today[parts[2]] = float(parts[32].replace('%',''))
    
    conn = psycopg2.connect(DB); cur = conn.cursor()
    
    results = []
    for code, name in etf_list.items():
        tret = today.get(code, 0)
        
        # 昨天收盘
        cur.execute("""
            SELECT close FROM daily_prices WHERE symbol=%s
            AND trade_date < %s ORDER BY trade_date DESC LIMIT 1
        """, ('A.'+code, TODAY))
        r = cur.fetchone()
        
        # 前天
        cur.execute("""
            SELECT close FROM daily_prices WHERE symbol=%s
            AND trade_date < '2026-06-09' ORDER BY trade_date DESC LIMIT 1
        """, ('A.'+code,))
        r2 = cur.fetchone()
        
        yret = None
        if r and r2:
            yret = (float(r[0]) - float(r2[0])) / float(r2[0]) * 100
        
        verdict = "一日游" if (yret and yret > 3 and abs(tret) < 2) else ("持续上涨" if (yret and yret > 2 and tret > 2) else "正常")
        flag = "❌" if verdict == "一日游" else ("✅" if verdict == "持续上涨" else "➖")
        
        results.append((name, yret or 0, tret, verdict, flag))
    
    print(f"\n{'板块':10} {'昨日涨':>6} {'今日涨':>6} {'判定':12}")
    print("-" * 38)
    for r in results:
        print(f"  {r[4]} {r[0]:10} {r[1]:>+5.2f}% {r[2]:>+5.2f}% {r[3]:12}")
    
    conn.close()


# ══════════════════════════════════════════════════
# 第五章：政策嗅探
# ══════════════════════════════════════════════════

def policy_scan():
    """SerpAPI搜索最近政策方向（每周1次）"""
    print("=" * 70)
    print("  V9 政策嗅探 (SerpAPI)")
    print("=" * 70)
    print("[计划中: 使用SerpAPI搜索7条政策方向, 自动评分]")
    print(f"  今日暂使用缓存数据")
    print(f"\n  当前政策方向（6/8~6/10）:")
    print(f"    国务院: 稳增长、促消费（中性偏暖）")
    print(f"    大基金三期: 半导体设备（有政策支撑但资金不足）")
    print(f"    电力改革: 电改深化,智能电网（长期利好,短线无催化）")
    print(f"  → 全部为中长期政策,无短期催化")


# ══════════════════════════════════════════════════
# 主决策引擎
# ══════════════════════════════════════════════════

def trade_signal():
    """
    交易信号——潮汐+评分+舆论三合一
    
    输出: 操作建议 (BUY/HOLD/SELL/WAIT)
    """
    conn = psycopg2.connect(DB); cur = conn.cursor()
    
    # 1. 潮汐相位
    phase, capital_state, _ = tide()
    
    # 2. 工行今日
    req = urllib.request.Request('https://qt.gtimg.cn/q=sh601398', headers=HDR)
    resp = urllib.request.urlopen(req, timeout=5, context=ctx)
    icbc_parts = resp.read().decode('gbk').split('~')
    icbc_price = float(icbc_parts[3])
    icbc_pct = float(icbc_parts[32].replace('%',''))
    icbc_high = float(icbc_parts[33])
    
    print(f"\n{'='*70}")
    print("  【交易信号】潮汐+评分+舆论三合一决策")
    print(f"{'='*70}")
    
    print(f"\n  工行: {icbc_price:.2f} ({icbc_pct:+.2f}%) 日内高{icbc_high:.2f}")
    
    # 信号矩阵
    TIDE_DECISION = {
        "🔴 潮汐高潮（强避险）": "STOP — 不开新仓,不抄底",
        "🟡 潮汐上涨（温和避险）": "HOLD — 持有硬底等",
        "🟢 潮汐退潮（资金流出银行）": "WATCH — 准备入场,观察板块",
        "✅ 潮汐见底（资金大转向）": "BUY — 入场窗口已开",
    }
    
    action_tide = TIDE_DECISION.get(phase, "WAIT")
    
    # 舆论反转
    sentiment_text = sentiment_pulse()
    
    # 信号矩阵
    print(f"\n【潮汐信号】{phase}")
    print(f"  → {action_tide}")
    
    # 工行状态机
    print(f"\n【工行位置】{icbc_price:.2f} ", end="")
    if icbc_price > 7.5:
        icbc_state = "高位避险区"
        icbc_action = "🔴 禁止开新仓"
        weight = 0.3
    elif icbc_price > 7.3:
        icbc_state = "中性偏高位"
        icbc_action = "🟡 仅持有,不加仓"
        weight = 0.5
    elif icbc_price > 7.0:
        icbc_state = "中性"
        icbc_action = "观察, 等待确认"
        weight = 0.7
    elif icbc_price > 6.8:
        icbc_state = "低位区"
        icbc_action = "🟢 准备入场"
        weight = 1.0
    else:
        icbc_state = "极低位(罕见)"
        icbc_action = "✅ 满仓入!"
        weight = 1.2
    
    print(f"{icbc_state} → {icbc_action}")
    print(f"  仓位系数: {weight:.1f}x")
    
    # 最终判定
    print(f"\n{'='*70}")
    buyable = icbc_price < 7.3 or icbc_pct < -0.5
    holdable = icbc_price < 7.7
    
    if not buyable and not holdable:
        final = "🔴 STOP — 工行高位+潮汐避险 = 一切暂停"
    elif not buyable and holdable:
        final = "🟡 HOLD — 持有硬底标的等待,不开新仓"
    elif buyable and holdable and icbc_pct < -0.5:
        final = "🟢 WATCH — 资金有转暖迹象,关注板块评分"
    else:
        final = "⚪ WAIT — 方向不明"
    
    print(f"  最终: {final}")
    
    # 当前持仓评估
    print(f"\n【持仓评估】")
    holdings = [
        ('600273','嘉化能源',6.99,7.00),
        ('600635','大众公用',4.93,4.91),
        ('002073','软控股份',5.56,5.46),
    ]
    for code, name, cost, current in holdings:
        pnl = (current - cost) / cost * 100
        if pnl < -3:
            rec = "止损 ⚠️"
        elif pnl < -1:
            rec = "承压"
        elif name in ['嘉化能源','大众公用'] and icbc_price > 7.5:
            rec = "硬底扛着"
        else:
            rec = "持有"
        print(f"  {name:8} 成本{cost:.3f}→{current:.3f} {pnl:+.2f}% → {rec}")
    
    conn.close()
    return final


# ══════════════════════════════════════════════════
# 全量运行
# ══════════════════════════════════════════════════

def full_run():
    print(f"\n  V9 资金潮汐决策框架 — {TODAY}")
    print(f"  {'='*60}\n")
    
    print("【阶段一】政策嗅探（每周）")
    policy_scan()
    
    print(f"\n{'='*70}")
    print("【阶段二】资金潮汐（周级别核心）")
    tide()
    
    print(f"\n{'='*70}")
    print("【阶段三】板块真伪评分（日确认）")
    score_sectors()
    
    print(f"\n{'='*70}")
    print("【阶段四】交易信号")
    trade_signal()


# ══════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'run'
    
    if cmd == 'tide':
        tide()
    elif cmd == 'score':
        score_sectors()
    elif cmd == 'hot':
        hot_check()
    elif cmd == 'sentiment':
        sentiment_pulse()
    elif cmd == 'policy':
        policy_scan()
    elif cmd == 'trade':
        trade_signal()
    elif cmd == 'run':
        full_run()
    elif cmd == 'watch':
        print("\n  V9 交互式盯盘\n" + "="*60)
        tide()
        print()
        score_sectors()
        print()
        sentiment_pulse()
    else:
        print(f"可用: run | tide | score | hot | sentiment | policy | trade | watch")
