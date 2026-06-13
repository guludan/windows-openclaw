#!/usr/bin/env python3
"""反制策略日报 v2.0 — 每日收盘后自动生成，核心是对策不是分析"""

import argparse, json, os, sys, urllib.request
import psycopg2
from datetime import datetime

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
REPORT_DIR = "/mnt/d/data/a-stock/reports"
HDR = {'User-Agent': 'Mozilla/5.0'}

# 反制策略库：[检测条件, 心理原理, 反制操作1, 反制操作2, 反制操作3, 危险等级]
COUNTER_STRATEGIES = [
    ["窗口陷阱", "10:40-10:50放量砸盘", "机构用固定窗口制造可预测性幻觉",
     "10:35-10:45休盘不看", "量比>2.5+跌>2%才动手", "不符合条件就当没看见", "高"],
    ["V型反弹诱多", "急跌后5分钟内拉回一半跌幅", "散户把恐惧解除解释为机会,产生FOMO",
     "V型反弹不追", "等回踩确认不破低点再动", "第一波是机构对倒,第二波才是真的", "高"],
    ["尾盘拉抬诱多", "14:30-14:55突然拉升0.5-1%", "机构用少量资金拉尾盘,让散户以为明天要高开",
     "尾盘拉升不追", "14:55拉的90%明天低开", "拉升时量比<1.2是假拉", "中"],
    ["连续阴跌磨心态", "连续3-5天,每天跌0.5-2%,量比0.7-1.2", "习得性无助——每天亏一点耗尽行动力",
     "第4天强制检查止损位", "总亏损>10%直接减半仓", "机构希望你不动,你不动他们就赢了", "致命"],
    ["开盘急杀制造恐慌", "09:30-09:45跌>1.5%", "开盘是注意力最集中的时候,急杀触发最大恐惧",
     "开盘前5分钟不交易", "09:30-09:45不看盘", "急杀后10:00前不继续跌是假摔", "高"],
    ["整数关口保卫战", "价格在整数关口反复争夺", "散户对整数关口有情感依恋",
     "整数关口不挂单", "机构最喜欢在整数关口做假突破", "等跌破整数后最多1%再动手", "中"],
    ["量比陷阱", "量比突然放大到2.0以上价格同时波动", "散户看到量就激动以为大资金进场",
     "量比>2+跌>2%=恐慌盘可以接", "量比>2+涨>2%=出货盘不能追", "量比>3=大概率机构对倒单", "中"],
    ["横盘磨底假动作", "连续横盘3-5天振幅<2.5%量缩", "散户以为筑底,机构在横盘期间悄悄出货",
     "横盘期不操作", "横盘结束时的方向突破才有意义", "向下突破大概率是洗盘不是真跌", "中"],
    ["成本锚点陷阱", "价格在你成本价附近反复但不过", "机构知道你的成本价,故意不过让你继续扛",
     "止损设在成本价-5%以上", "不等于等回本再卖", "总在想回本就卖=已经掉进锚点", "致命"],
    ["分时图钓鱼线", "分时图垂直拉升5分钟涨>2%", "视觉冲击最大,散户看到垂直拉升本能地追",
     "垂直拉升到高点不追", "5分钟涨>2%+量比>3,90%是钓鱼线", "钓鱼线10-20分钟内回落", "高"],
    ["好消息出尽是利空", "公司出利好当天高开低走", "散户认为利好=上涨,机构借利好出货",
     "利好公布当天不买", "高开>2%等半小时回落企稳再判断", "公布后成交量异常放大=出货", "中"],
    ["板块联动掩护", "同板块在涨唯独你的不涨或跌", "散户想板块都涨了迟早会跟,被板块迷惑",
     "板块涨你的跌=机构在单独控盘", "不等板块带它上去", "等它自己企稳", "中"],
]

def get_quote(code):
    """腾讯行情"""
    pfx = 'sh' if code.startswith('6') else 'sz'
    req = urllib.request.Request(f'https://qt.gtimg.cn/q={pfx}{code}', headers=HDR)
    resp = urllib.request.urlopen(req, timeout=8)
    parts = resp.read().decode('gbk').split('~')
    if len(parts) < 40:
        return None
    return {
        'name': parts[1], 'price': float(parts[3]),
        'pct': float(parts[32].replace('%','')),
        'high': float(parts[33]), 'low': float(parts[34]),
        'vol_ratio': float(parts[49]) if parts[49] else 0,
        'amplitude': float(parts[43]) if parts[43] else 0,
    }

def get_minute(code):
    """分时特征"""
    pfx = 'sh' if code.startswith('6') else 'sz'
    req = urllib.request.Request(f'http://ifzq.gtimg.cn/appstock/app/minute/query?code={pfx}{code}', headers=HDR)
    resp = urllib.request.urlopen(req, timeout=8)
    d = json.loads(resp.read().decode('utf-8'))
    raw = d.get('data', {}).get(pfx+code, {}).get('data', {})
    rows = raw.get('data', []) if isinstance(raw, dict) else []
    if not rows:
        return {}
    pts = []
    for r in rows:
        parts = r.split()
        if len(parts) >= 4:
            pts.append({'t': parts[0], 'p': float(parts[1])})
    if not pts:
        return {}
    low = min(pts, key=lambda x: x['p'])
    tail = [x for x in pts if x['t'] >= '1430']
    tail_trend = (tail[-1]['p'] - tail[0]['p']) / tail[0]['p'] * 100 if tail else 0
    w = [x for x in pts if '1040' <= x['t'] <= '1055']
    window_low = min(w, key=lambda x: x['p']) if w else None
    open15 = pts[:15]
    open_drop = (open15[-1]['p'] - pts[0]['p']) / pts[0]['p'] * 100 if open15 else 0
    return {
        'open': pts[0]['p'], 'close': pts[-1]['p'],
        'low_time': low['t'], 'low_price': low['p'],
        'tail_trend': round(tail_trend, 2),
        'open_drop': round(open_drop, 2),
        'window_low_time': window_low['t'] if window_low else None,
        'window_low_price': window_low['p'] if window_low else None,
    }

def detect_traps(quotes, features):
    """检测今日陷阱"""
    active = []
    for t in COUNTER_STRATEGIES:
        name = t[0]; detect = t[1]
        for code, q in quotes.items():
            f = features.get(code, {})
            if name == '窗口陷阱' and f.get('window_low_price') and q.get('vol_ratio', 0) > 1.0:
                active.append({**{'name': name, 'code': code, 'level': t[6], 'counter': t[3]}})
            elif name == 'V型反弹诱多' and f.get('open_drop', 0) < -1.5:
                low = f.get('low_price', 0)
                close = f.get('close', 0)
                if low and close and (close - low) / low > 0.02:
                    active.append({**{'name': name, 'code': code, 'level': t[6], 'counter': t[3]}})
            elif name == '尾盘拉抬诱多' and f.get('tail_trend', 0) > 0.5:
                if q.get('vol_ratio', 0) < 1.5:
                    active.append({**{'name': name, 'code': code, 'level': '高', 'counter': t[3]}})
            elif name == '开盘急杀' and f.get('open_drop', 0) < -1.5 and q.get('vol_ratio', 0) > 1.2:
                active.append({**{'name': name, 'code': code, 'level': t[6], 'counter': t[3]}})
            elif name == '量比陷阱' and q.get('vol_ratio', 0) > 2.0:
                active.append({**{'name': name, 'code': code, 'level': t[6],
                                  'direction': '涨' if q.get('pct',0) > 0 else '跌', 'counter': t[3]}})
    return active

def load_positions():
    try:
        conn = psycopg2.connect(DB)
        cur = conn.cursor()
        cur.execute("SELECT code, shares, cost FROM positions WHERE shares > 0")
        rows = cur.fetchall()
        cur.close(); conn.close()
        if rows:
            return {r[0]: {'shares': int(r[1]), 'cost': float(r[2])} for r in rows}
    except:
        pass
    return {"600273": {"shares": 200, "cost": 7.2451},
            "600635": {"shares": 400, "cost": 5.0685},
            "002073": {"shares": 250, "cost": 5.735}}

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', default=datetime.now().strftime('%Y-%m-%d'))
    args = p.parse_args()
    today = args.date
    ds = today.replace('-', '')

    pos = load_positions()
    quotes = {}; features = {}
    for c in pos:
        q = get_quote(c)
        if q: quotes[c] = q
        f = get_minute(c)
        if f: features[c] = f

    traps = detect_traps(quotes, features)

    # 计算持仓汇总
    tc = 0; tv = 0
    pd = []
    for c, po in sorted(pos.items()):
        q = quotes.get(c, {}); f = features.get(c, {})
        pr = q.get('price', 0); co = po['cost']; sh = po['shares']
        v = pr * sh; oc = co * sh; tc += oc; tv += v
        pd.append({'code': c, 'name': q.get('name', c), 'shares': sh,
                   'cost': co, 'price': pr, 'pct': q.get('pct', 0),
                   'pp': (pr-co)/co*100 if co else 0, 'vr': q.get('vol_ratio', 0),
                   'amp': q.get('amplitude', 0), 'feat': f})
    tpp = (tv - tc) / tc * 100 if tc else 0

    L = []
    L.append(f"# 反制策略日报 - {today}")
    L.append("")
    L.append("核心原则: 机构做局让散户接盘。分析无用,只有反制策略能活。")
    L.append("")

    # 第一步: 今日陷阱
    L.append("## 今日触发陷阱")
    L.append("")
    if traps:
        for t in traps:
            d = f"({t.get('direction','')})" if 'direction' in t else ''
            L.append(f"- {t['name']}{d} [{t['code']}] - 反制: {t['counter']}")
    else:
        L.append("今日没有检测到明确的机构陷阱。量比和波动都在正常范围。")
        L.append("注意: 没有陷阱不代表没操作 - 他们可能在磨底等筹码到位。")
    L.append("")

    # 持仓快照
    L.append("---")
    L.append("## 持仓状态")
    L.append("")
    L.append(f"{'代码':<8} {'名称':<8} {'仓位':<6} {'成本':<8} {'现价':<6} {'当日':<7} {'浮动':<7} {'振幅(量比)':<12}")
    L.append("-" * 60)
    for p in pd:
        L.append(f"{p['code']:<8} {p['name']:<8} {p['shares']}股 {p['cost']:<8.4f} {p['price']:<6.2f} {p['pct']:+.2f}% {p['pp']:+.2f}% {p['amp']:.1f}%({p['vr']:.1f})")
    L.append(f"{'合计':<8} {'':<8} {'':<6} {'':<8} {'':<6} {'':<7} {tpp:+.2f}%({tv:.0f})")
    L.append("")

    # 关键信号
    L.append("---")
    L.append("## 今日关键信号")
    L.append("")
    for p in pd:
        f = p['feat']
        L.append(f"### {p['name']}({p['code']})")
        if f:
            L.append(f"- 开{f.get('open',0):.2f} 低{f.get('low_price',0):.2f}({f.get('low_time','?')}) 收{f.get('close',0):.2f}")
            L.append(f"- 10:40窗口: {f.get('window_low_price','-')}@{f.get('window_low_time','?')}")
            L.append(f"- 尾盘方向: {f.get('tail_trend',0):+.2f}% 开盘方向: {f.get('open_drop',0):+.2f}%")
            if f.get('tail_trend',0) > 0.5:
                L.append("  >> 尾盘诱多陷阱,明天高开不要追")
            elif f.get('tail_trend',0) < -0.5:
                L.append("  >> 尾盘杀跌,明天低开不要割")
            if f.get('open_drop',0) < -1.5:
                L.append("  >> 开盘急杀,制造恐慌")
        else:
            L.append("- 无分时数据")
        L.append("")

    # 心理学家精简诊断
    L.append("---")
    L.append("## 心理诊断(3分钟)")
    L.append("")
    L.append(f"卡尼曼(认知): 今天你中了{' '.join([t['name'] for t in traps[:3]]) if traps else '常规'}陷阱。")
    L.append("你的直觉看到行情就想操作,但理性告诉你这是局。")
    L.append("反制: 关掉直觉的投票权,今天不做任何冲动决策。")
    L.append("")
    L.append(f"塞勒(心理账户): 你把持仓分开看每只亏一点感觉还好。但总账亏{tpp:.1f}%。")
    L.append(f"{'距离止损线还有余地' if abs(tpp) < 10 else '该启动风控了'}")
    L.append("反制: 今天只看总盈亏,不看单只。")
    L.append("")
    L.append("萨波尔斯基(神经): 前额叶决策区被亏损压着,决策质量下降。")
    L.append("反制: 今天任何重大决定先等30分钟再做。")
    L.append("")

    # 明日作战计划
    L.append("---")
    L.append("## 明日作战计划")
    L.append("")
    L.append("时间线:")
    L.append(f"  {'开盘前':<12} 读反制速查表,不看群聊论坛")
    L.append(f"  {'09:30-09:45':<12} 不看盘,让机构自己表演")
    L.append(f"  {'09:45-10:30':<12} 判断真跌还是假摔")
    L.append(f"  {'10:30-10:50':<12} **机构固定窗口砸盘时段,不追不逃**")
    L.append(f"  {'10:50-11:30':<12} 判断V反还是持续下跌")
    L.append(f"  {'13:00-14:00':<12} 午盘正常监控")
    L.append(f"  {'14:00-14:55':<12} 警惕尾盘拉抬诱多")
    L.append(f"  {'14:55-15:00':<12} 收盘前检查止损位")
    L.append("")

    # 具体指令
    L.append("具体操作指令:")
    has_tailup = any(f.get('tail_trend',0) > 0.5 for f in features.values())
    has_taildn = any(f.get('tail_trend',0) < -0.5 for f in features.values())
    if has_tailup:
        L.append("- 今日尾盘拉升,明天高开>1%不追,等30分钟看回落")
    if has_taildn:
        L.append("- 今日尾盘杀跌,明天低开>1%不割,等10:30后再判断")
    for p in pd:
        if p['pp'] < -8:
            L.append(f"- {p['name']}: 亏损{p['pp']:.1f}%,再跌>2%无条件减半仓")
        elif p['pp'] < -5:
            L.append(f"- {p['name']}: 亏损{p['pp']:.1f}%,止损设在{p['cost']*0.9:.2f}")
        else:
            L.append(f"- {p['name']}: 亏损{p['pp']:.1f}%,正常范围继续等")
    L.append("")
    L.append(f"资金: 总仓位{tv:.0f}/{tc:.0f}={tv/tc*100:.0f}% | 资金余{5000-tc:.0f} | 止损线4000(差{tv-4000:.0f})")
    L.append("")

    # 反制速查表
    L.append("---")
    L.append("## 反制速查表(明天开盘前看一眼)")
    L.append("")
    for t in COUNTER_STRATEGIES:
        is_active = any(at['name'] == t[0] for at in traps)
        m = '>> ' if is_active else '   '
        L.append(f"{m}{t[1]:<30} -> {t[3]}")
    L.append("")
    L.append(">> = 今日已触发的陷阱,重点关注")
    L.append("")

    # 收盘检查清单
    L.append("---")
    L.append("## 收盘检查清单")
    L.append("")
    L.append("[ ] 今日是否遵守了操作计划?")
    L.append("[ ] 是否有计划外操作?触发情绪是什么?")
    L.append("[ ] 今天机构的主要手法是什么?")
    L.append("[ ] 我今天比昨天更了解这个局了吗?")
    L.append("")

    report = '\n'.join(L)

    # 输出D盘
    dp = os.path.join(REPORT_DIR, today, f'反制策略日报_{ds}.md')
    os.makedirs(os.path.dirname(dp), exist_ok=True)
    with open(dp, 'w', encoding='utf-8') as f:
        f.write(report)

    # 输出workspace
    wp = f'/home/tearsyu/.openclaw/workspace/memory/reports/反制策略日报_{ds}.md'
    with open(wp, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f"OK: {dp}")
    print(f"OK: {wp}")

if __name__ == '__main__':
    main()
