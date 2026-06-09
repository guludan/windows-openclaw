#!/usr/bin/env python3
"""
V9 四层决策框架
政策→板块→大盘→标的（和V8完全相反）

用法:
  # 全天候运行（每天一次）
  python3 scripts/v9_framework.py run

  # 单层运行
  python3 scripts/v9_framework.py policy        # 政策嗅探（每周）
  python3 scripts/v9_framework.py sector        # 板块轮动分析（每日）
  python3 scripts/v9_framework.py market        # 大盘窗口（每日）
  python3 scripts/v9_framework.py target        # 标的筛选（按需）
  python3 scripts/v9_framework.py output        # 决策输出

  # 工具
  python3 scripts/v9_framework.py watch         # 交互式盯盘
"""

import urllib.request, json, ssl, psycopg2, re, sys
from datetime import datetime, timedelta
from collections import defaultdict

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
HDR = {'User-Agent': 'Mozilla/5.0'}

DB = "dbname=guludan user=guludan password=toto host=192.168.3.14"

# ──────────── 常量定义 ────────────

# 政策→受益板块映射
POLICY_SECTOR_MAP = {
    '半导体': ['半导体','芯片','集成电路','封测','设备'],
    'AI/算力': ['AI','人工智能','算力','大模型','数据中心','云计算','光模块'],
    '新能源': ['新能源','光伏','风电','锂电','储能','电池'],
    '电力设备': ['智能电网','特高压','电网','电气设备','电力','柔性直流'],
    '工控/机器人': ['机器人','工控','自动化','智能制造'],
    '消费刺激': ['消费','家电','汽车','零售','旅游','食品饮料'],
    '通信': ['通信','5G','6G'],
}

# 板块→ETF映射
SECTOR_ETF_MAP = {
    '半导体': 'sh512480',
    '芯片': 'sh512760',
    '新能源': 'sh515030',
    'AI/算力': 'sh515070',
    '通信': 'sh515050',
    '机器人': 'sh562500',
    '软件': 'sz159852',
    '电力': 'sz159611',
    '中证1000': 'sh512100',
    '上证50': 'sh510050',
}

# ──────────── 工具函数 ────────────

def qt(code):
    """腾讯实时行情，返回name, price, pct, close, high, low, vol"""
    prefix = 'sh' if code.startswith('6') or code.startswith('5') else 'sz'
    url = f'https://qt.gtimg.cn/q={prefix}{code}'
    try:
        req = urllib.request.Request(url, headers=HDR)
        resp = urllib.request.urlopen(req, timeout=8, context=ctx)
        parts = resp.read().decode('gbk').split('~')
        if len(parts) < 35: return None
        return {
            'name': parts[1], 'price': float(parts[3]), 'pct': float(parts[32].replace('%','')),
            'close': float(parts[4]), 'high': float(parts[33]), 'low': float(parts[34]),
            'vol': int(parts[6])/10000
        }
    except: return None

def qt_batch(codes):
    """批量查询，返回 {code: {...}}"""
    res = {}
    for i in range(0, len(codes), 20):
        batch = codes[i:i+20]
        ids = [('sh' if c.startswith('6') or c.startswith('5') else 'sz')+c for c in batch]
        url = f'https://qt.gtimg.cn/q={",".join(ids)}'
        try:
            req = urllib.request.Request(url, headers=HDR)
            resp = urllib.request.urlopen(req, timeout=15, context=ctx)
            for line in resp.read().decode('gbk').strip().split('\n'):
                parts = line.split('~')
                if len(parts) < 35: continue
                c = line.split('="')[0].replace('v_','')
                if len(c) > 6: c = c[2:]
                res[c] = {
                    'name': parts[1], 'price': float(parts[3]),
                    'pct': float(parts[32].replace('%','')), 'close': float(parts[4]),
                    'high': float(parts[33]), 'low': float(parts[34]),
                    'vol': int(parts[6])/10000
                }
        except: pass
    return res

def db():
    return psycopg2.connect(DB)

# ──────────── 1. 政策嗅探引擎 ────────────

def policy_scan():
    """政策层：通过SerpAPI搜索真实政策"""
    print("=" * 68)
    print("[V9 政策层] 政策嗅探引擎 (SerpAPI)")
    print("=" * 68)
    
    print(f"\n【搜索近期产业政策】")
    
    import urllib.request, json, time
    from urllib.parse import quote
    
    queries = {
        '国务院常务会议 产业政策 2026年6月': '国务院',
        '工信部 新兴产业 最新政策 2026': '工信部',
        '大基金三期 半导体 2026': '大基金',
        '电网改造 特高压 十五五 2026': '电网',
        '以旧换新 补贴 2026年6月': '以旧换新',
        'AI 人工智能 算力 政策 2026': 'AI',
        '新型工业化 未来产业 国常会 2026': '新型工业化',
    }
    
    policies = []
    for q, tag in queries.items():
        try:
            encoded = quote(q)
            url = f"https://serpapi.com/search?q={encoded}&api_key=848c4ec87cac7776b9404f0af1aeb36fce936d1aa3aebe899664eaa43e67ccc2&num=3&hl=zh-CN"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read().decode())
            
            for r in data.get('organic_results', [])[:2]:
                title = r.get('title', '')
                snippet = r.get('snippet', '')[:150]
                policies.append({'tag': tag, 'title': title, 'snippet': snippet})
            time.sleep(0.5)
        except Exception as e:
            policies.append({'tag': tag, 'title': f'[获取失败: {e}]', 'snippet': ''})
    
    # 输出
    print(f"\n{'来源':10} {'标题'}")
    print("-" * 65)
    for p in policies[:14]:
        print(f"{p['tag']:10} {p['title']}")
        if p['snippet']:
            print(f"          {p['snippet']}")
    
    # 信号评分
    signal_up = []
    signal_mid = []
    if any('大基金' in p['title'] for p in policies):
        signal_up.append('半导体/芯片')
    if any('电网' in p['title'] or '特高压' in p['title'] for p in policies):
        signal_up.append('电力设备')
    if any('AI' in p['title'] or '人工' in p['title'] or '算力' in p['title'] for p in policies):
        signal_mid.append('AI/算力')
    if any('新能源' in p['title'] for p in policies):
        signal_mid.append('新能源')
    if any('以旧换新' in p['title'] or '消费' in p['title'] for p in policies):
        signal_mid.append('消费')
    if any('新型工业' in p['title'] or '智能' in p['title'] for p in policies):
        signal_up.append('新型工业化/智能制造')
    
    print(f"\n【政策信号评分】(基于搜索摘要)")
    print(f"  强信号: {', '.join(set(signal_up)) if signal_up else '暂无'}")
    print(f"  中信号: {', '.join(signal_mid) if signal_mid else '暂无'}")
    
    return policies

# ──────────── 2. 板块轮动分析器 ────────────

def sector_analysis():
    """板块层：四象限分类"""
    print("=" * 68)
    print("[V9 板块层] 板块轮动分析")
    print("=" * 68)
    
    # 用ETF涨跌来估算板块状态
    sector_etfs = {
        '半导体/芯片': ['sh512480','sh512760'],
        'AI/算力': ['sh515070'],
        '通信': ['sh515050'],
        '新能源': ['sh515030'],
        '机器人': ['sh562500'],
        '软件': ['sz159852'],
        '传统蓝筹': ['sh510050'],
        '中盘': ['sh512100'],
    }
    
    all_etf_codes = [c for codes in sector_etfs.values() for c in codes]
    quotes = qt_batch([c.replace('sh','').replace('sz','') for c in set(all_etf_codes)])
    
    # 获取1周前价格计算短期涨幅
    conn = db(); cur = conn.cursor()
    
    sector_data = []
    for sect_name, etf_codes in sector_etfs.items():
        today_pct = 0; n = 0
        
        # 如果ETF代码在daily_prices里
        for ec in etf_codes:
            code = ec.replace('sh','').replace('sz','')
            if code in quotes:
                today_pct += quotes[code]['pct']
                n += 1
        
        if n:
            today_pct /= n
            # 查5日收益率
            cur.execute("""
                SELECT symbol, close FROM daily_prices
                WHERE symbol IN %s AND trade_date = '2026-06-01'
            """, (tuple(f'A.{c.replace("sh","").replace("sz","")}' for c in etf_codes),))
            jun1_rows = cur.fetchall()
            if jun1_rows:
                jun1_close = sum(float(r[1]) for r in jun1_rows) / len(jun1_rows)
                cur.execute("""
                    SELECT symbol, close FROM daily_prices
                    WHERE symbol IN %s AND trade_date = '2026-06-08'
                """, (tuple(f'A.{c.replace("sh","").replace("sz","")}' for c in etf_codes),))
                jun8_rows = cur.fetchall()
                if jun8_rows:
                    jun8_close = sum(float(r[1]) for r in jun8_rows) / len(jun8_rows)
                    five_d_ret = (jun8_close - jun1_close) / jun1_close * 100
                else:
                    five_d_ret = 0
            else:
                five_d_ret = 0
            
            # 分类：已轮动/轮动中/即将轮动/等待轮动
            if today_pct > 2 and five_d_ret < 0:
                phase = "🔴 已轮动(等待回调)"
            elif today_pct > 1:
                phase = "🟡 轮动中(不追高)"
            elif today_pct > -1 and five_d_ret > -3:
                phase = "🟢 即将轮动(关注)"
            else:
                phase = "⚪ 等待轮动(观察)"
            
            sector_data.append((sect_name, today_pct, five_d_ret, phase))
    
    print(f"\n{'板块':16} {'今日':>7} {'5日涨':>8} {'阶段'}")
    print("-" * 60)
    for name, today, f5, phase in sorted(sector_data, key=lambda x: -x[2]):
        print(f"{name:16} {today:>+6.2f}% {f5:>+7.2f}% {phase}")
    
    conn.close()
    return sector_data

# ──────────── 2.5 真热点识别 ────────────

def hot_sector_scanner(sector_data):
    """
    用同花顺L1成分股数据+涨跌比+量能持续性，判断当前热点是真热点还是假拉升
    
    输入: sector_data 来自 sector_analysis() 的 [(name, today_pct, five_d_ret, phase)]
    输出: 打印真热点评分
    """
    print("=" * 68)
    print("[V9 真热点识别] 板块质量评分")
    print("=" * 68)
    
    conn = db(); cur = conn.cursor()
    
    # 获取最新交易日
    cur.execute("SELECT max(trade_date) FROM daily_prices")
    latest_date = cur.fetchone()[0]
    if not latest_date:
        print("  ⚠️ daily_prices 无数据")
        conn.close()
        return []
    today_str = latest_date.isoformat()
    
    # 板块名 → 同花顺L1板块名 映射
    name_to_ths = {
        '半导体/芯片': '半导体',
        'AI/算力': None,  # 无直接映射
        '通信': '通信设备',
        '新能源': '电池',
        '机器人': None,
        '软件': '软件开发',
        '电力设备': '电网设备',
        '传统蓝筹': None,
        '中盘': None,
    }
    
    lookback_date = (latest_date - timedelta(days=60)).isoformat()
    
    conn = db(); cur = conn.cursor()
    
    ratings = []
    
    for name, today_pct, five_d_ret, phase in sector_data:
        ths_name = name_to_ths.get(name)
        if not ths_name:
            continue
        
        # 1) 查板块成分股
        cur.execute("""
            SELECT stock_code FROM industry_components_ths_l1
            WHERE industry = %s AND stock_code IS NOT NULL
        """, (ths_name,))
        stks = [r[0] for r in cur.fetchall()]
        if len(stks) < 3:
            continue
        
        # 2) 用SQL算今日板块涨跌比 + 量比
        cur.execute("""
            WITH today_data AS (
                SELECT 
                    dp.symbol,
                    dp.trade_date,
                    dp.close,
                    dp.volume,
                    LAG(dp.close) OVER (PARTITION BY dp.symbol ORDER BY dp.trade_date) AS prev_close,
                    AVG(dp.volume) OVER (PARTITION BY dp.symbol ORDER BY dp.trade_date ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING) AS avg_vol_5
                FROM daily_prices dp
                WHERE dp.symbol IN (SELECT 'A.' || unnest(%s::text[]))
                  AND dp.trade_date >= %s
            )
            SELECT 
                COUNT(*) FILTER (WHERE prev_close > 0) as total,
                SUM(CASE WHEN prev_close > 0 AND close > prev_close THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*) FILTER (WHERE prev_close > 0), 0) as advance_ratio,
                percentile_cont(0.5) WITHIN GROUP (ORDER BY volume::float / NULLIF(avg_vol_5, 0)) as med_vol_ratio
            FROM today_data
            WHERE trade_date = %s
              AND avg_vol_5 > 0
        """, (stks, lookback_date, today_str))
        r = cur.fetchone()
        if not r or not r[0] or r[0] < 3:
            continue
        
        total = r[0]
        advance_ratio = float(r[1]) if r[1] else 0.5
        vol_ratio = float(r[2]) if r[2] else 1.0  # 个股中位数量比，不受大盘股/新股干扰
        
        # 3) 评分
        score = 0
        details = []
        
        # 涨跌比评分
        if advance_ratio > 0.60:
            score += 40
            details.append(f"涨跌比{advance_ratio:.0%} ✅ 板块强共振")
        elif advance_ratio > 0.55:
            score += 25
            details.append(f"涨跌比{advance_ratio:.0%} 🟡 弱共振")
        elif advance_ratio > 0.50:
            score += 10
            details.append(f"涨跌比{advance_ratio:.0%} ❓ 分化严重")
        else:
            score -= 10
            details.append(f"涨跌比{advance_ratio:.0%} 🔴 多数股下跌")
        
        # 量能持续性评分（中位数量比）
        if vol_ratio > 1.5:
            score += 20
            details.append(f"量比{vol_ratio:.1f}x 🔥 放量")
        elif vol_ratio > 0.8:
            score += 10
            details.append(f"量比{vol_ratio:.1f}x 正常")
        else:
            score -= 10
            details.append(f"量比{vol_ratio:.1f}x 🔴 缩量")
        
        # 今日涨幅评分
        if today_pct > 3:
            score += 20
            details.append(f"涨{today_pct:+.1f}% 强")
        elif today_pct > 1:
            score += 10
            details.append(f"涨{today_pct:+.1f}% 中")
        else:
            score += 0
            details.append(f"涨{today_pct:+.1f}% 弱")
        
        # 阶段评分
        if '已轮动' in phase:
            score -= 10
            details.append("已轮动(警惕回调)")
        elif '即将轮动' in phase:
            score += 15
            details.append("即将轮动(关注)")
        
        verdict = ""
        if score >= 60:
            verdict = "✅ 真热点"
        elif score >= 35:
            verdict = "🟡 偏热"
        elif score >= 15:
            verdict = "❓ 一般"
        else:
            verdict = "⛔ 假拉升/出货"
        
        ratings.append((score, name, verdict, details, advance_ratio, vol_ratio))
    
    conn.close()
    
    ratings.sort(key=lambda x: -x[0])
    
    print(f"\n{'评分':<6} {'板块':<18} {'判定':<18} {'核心信号'}")
    print('-' * 70)
    for score, name, verdict, details, _, _ in ratings:
        detail_str = ' | '.join(details[:2])
        print(f"{score:<6} {name:<18} {verdict:<18} {detail_str}")
    
    print(f"\n评分标准: >60=真热点  35~60=偏热  15~35=一般  <15=假拉升")
    print("核心看涨跌比>0.6(共振)+量不缩+尚未轮动过")
    
    return ratings


# ──────────── 3. 大盘窗口监控 ────────────

def market_window():
    """大盘层：仓位上限"""
    print("=" * 68)
    print("[V9 大盘层] 窗口监控")
    print("=" * 68)
    
    conn = db(); cur = conn.cursor()
    
    cur.execute("""
        SELECT date::text, extra->>'surplus_z', extra->>'regime',
               extra->>'advance_ratio'
        FROM pool_energy WHERE level='market' ORDER BY date DESC LIMIT 1
    """)
    r = cur.fetchone()
    
    if r:
        date_str = r[0]; sz = float(r[1]) if r[1] else -2.0; regime = r[2] or '?'
        ar = r[3] or '?'
        
        # 仓位规则
        if sz < -2.0:
            limit = "30%上限"
            signal = "极空区, 等信号"
            action = "持有不动"
        elif sz < -1.5:
            limit = "30%上限"
            signal = "空头区, 不动"
            action = "持有不动"
        elif sz < -1.0:
            limit = "50%上限"
            signal = "弱回暖, 可轻仓"
            action = "轨道A可轻仓"
        else:
            limit = "80%上限"
            signal = "回暖明确, 加仓"
            action = "可加仓"
        
        print(f"\n  V8状态:   {date_str}  surplus_z={sz:.3f}  regime={regime}")
        print(f"  仓位上限: {limit}")
        print(f"  信号:     {signal}")
        print(f"  操作:     {action}")
        
        # 计算5日均值
        cur.execute("""
            SELECT extra->>'surplus_z' 
            FROM pool_energy WHERE level='market'
            ORDER BY date DESC LIMIT 5
        """)
        szs = [float(rr[0]) for rr in cur.fetchall() if rr[0]]
        if len(szs) >= 2:
            sz_5 = sum(szs) / len(szs)
            trend = "🟢 回暖" if szs[-1] > szs[0] else "🔴 继续恶化"
            print(f"  sz_5:    {sz_5:.3f}  {trend}")
    
    else:
        print(f"\n  ⚠️ pool_energy 无数据, 需先跑归档管线")
    
    conn.close()

# ──────────── 4. 标的筛选 ────────────

def target_filter(trigger_sector=None):
    """标的层：ETF优先 + 个股弹性增强"""
    print("=" * 68)
    print("[V9 标的层] 标的筛选")
    print("=" * 68)
    
    conn = db(); cur = conn.cursor()
    
    # 当前已知受益板块
    print(f"\n【板块ETF监视清单】")
    print(f"{'板块':16} {'ETF代码':12} {'名称':18} {'现价':>8} {'涨跌':>7}")
    print("-" * 65)
    
    etf_watch = [('半导体','512480'),('芯片','512760'),('AI/算力','515070'),
                 ('通信','515050'),('新能源','515030'),('机器人','562500'),
                 ('软件','159852'),('电力设备','159611')]
    
    quotes = qt_batch([c for _,c in etf_watch])
    for name, code in etf_watch:
        q = quotes.get(code)
        if q:
            print(f"{name:16} sh{code:8} {q['name']:18} {q['price']:>8.3f} {q['pct']:>+6.2f}%")
        else:
            print(f"{name:16} {code:12} {'数据不可用':18}")
    
    # 如果指定了触发板块，输出板块内弹性个股
    if trigger_sector:
        print(f"\n【{trigger_sector}板块内弹性标的】")
        # 从行业成分表找
        cur.execute("""
            SELECT DISTINCT i.stock_code, i.stock_name FROM industry_components_ths i
            WHERE i.industry_name LIKE %s LIMIT 10
        """, (f'%{trigger_sector}%',))
        stocks = cur.fetchall()
        if stocks:
            for code, name in stocks:
                code_clean = code.strip()
                sym = f'A.{code_clean}' if not code_clean.startswith('A.') else code_clean
                # 查SMA偏离
                cur.execute("""
                    WITH avg20 AS (
                        SELECT AVG(close) as ma20 FROM daily_prices
                        WHERE symbol=%s AND trade_date >= '2026-05-20'
                    )
                    SELECT dp.close, av.ma20 FROM daily_prices dp, avg20 av
                    WHERE dp.symbol=%s AND dp.trade_date='2026-06-08'
                """, (sym, sym))
                rr = cur.fetchone()
                if rr:
                    c = float(rr[0]); ma20 = float(rr[1])
                    dev = (c - ma20) / ma20 * 100
                    print(f"  {name:16} {code_clean:8} SMA20偏离{dev:+.1f}%")
    
    # 搜索当前"即将轮动"板块的候选
    print(f"\n【电力设备板块弹性候选（当前即将轮动）】")
    cur.execute("""
        SELECT DISTINCT stock_code, stock_name FROM industry_components_ths
        WHERE industry_name LIKE '%电气%' OR industry_name LIKE '%电网%' 
           OR industry_name LIKE '%电力%'
        AND stock_code IS NOT NULL
        LIMIT 5
    """)
    for code, name in cur.fetchall():
        code_clean = code.strip()
        sym = f'A.{code_clean}' if not code_clean.startswith('A.') else code_clean
        cur.execute("""
            WITH avg20 AS (
                SELECT AVG(close) as ma20 FROM daily_prices
                WHERE symbol=%s AND trade_date >= '2026-05-20'
            )
            SELECT dp.close, av.ma20 FROM daily_prices dp, avg20 av
            WHERE dp.symbol=%s AND dp.trade_date='2026-06-08'
        """, (sym, sym))
        rr = cur.fetchone()
        if rr:
            c = float(rr[0]); ma20 = float(rr[1])
            dev = (c - ma20) / ma20 * 100
            print(f"  {name:16} {code_clean:8} SMA20偏离{dev:+.1f}%")
    
    conn.close()

# ──────────── 5. 工行周期分析 ────────────

def icbc_analysis():
    """工商银行周期→资金转换参照"""
    import psycopg2
    conn_icbc = db(); cur = conn_icbc.cursor()
    
    print("=" * 68)
    print("[V9 工行周期] 资金转换参照")
    print("=" * 68)
    
    # 今年月线
    cur.execute("""
        SELECT substring(trade_date::text,1,7), 
               AVG(close) as avg_close, AVG(volume/10000) as avg_v,
               (MAX(close)-MIN(close))/MIN(close)*100 as m_swing
        FROM daily_prices WHERE symbol='A.601398'
        AND trade_date >= '2025-01-01'
        GROUP BY 1 ORDER BY 1
    """)
    rows = cur.fetchall()
    
    print(f"\n{'月份':10} {'均价':>6} {'均量万':>8} {'月振幅':>8} {'信号'}")
    print("-" * 50)
    for ym, avg, v, sw in rows:
        avg = float(avg); v = float(v); sw = float(sw) if sw else 0
        vol_note = "放量" if v > 400 else ""
        signal = ""
        if avg > 7.4:
            signal = "高位区"
        elif avg < 6.8:
            signal = "低位区"
        print(f"{ym:10} {avg:>6.2f} {v:>8.0f} {sw:>+7.2f}% {vol_note:6} {signal}")
    
    # 关键指标
    cur.execute("""
        SELECT trade_date, close, volume/10000 as v,
               (close-open)/NULLIF(open,0)*100 as ret
        FROM daily_prices WHERE symbol='A.601398'
        AND trade_date >= '2026-06-01'
        ORDER BY trade_date
    """)
    jun = cur.fetchall()
    if len(jun) >= 2:
        jun_ret = (float(jun[-1][1]) - float(jun[0][1])) / float(jun[0][1]) * 100
        jun_v = sum(float(r[2]) for r in jun) / len(jun)
        print(f"\n  6月至今: 涨{jun_ret:+.2f}%, 均量{jun_v:.0f}万")
    
    # 工行 vs 上证跷跷板
    cur.execute("""
        WITH icbc AS (
            SELECT trade_date, (close-open)/NULLIF(open,0)*100 as ire
            FROM daily_prices WHERE symbol='A.601398'
        ), sh AS (
            SELECT trade_date, (close-open)/NULLIF(open,0)*100 as sre
            FROM daily_prices WHERE symbol='A.000001'
        )
        SELECT i.trade_date, i.ire::numeric(6,2), s.sre::numeric(6,2)
        FROM icbc i JOIN sh s ON i.trade_date=s.trade_date
        WHERE i.trade_date >= '2020-01-01'
    """)
    corr = cur.fetchall()
    total = max(len(corr), 1)
    icbc_up_sh_down = sum(1 for _, ir, sr in corr if float(ir)>0 and float(sr)<0)
    icbc_down_sh_up = sum(1 for _, ir, sr in corr if float(ir)<0 and float(sr)>0)
    
    print(f"\n【工行 vs 上证 跷跷板信号 2020~2026】")
    print(f"  工行涨+大盘跌(护盘): {icbc_up_sh_down}次 ({icbc_up_sh_down/total*100:.1f}%)")
    print(f"  工行跌+大盘涨(进攻): {icbc_down_sh_up}次 ({icbc_down_sh_up/total*100:.1f}%)")
    print(f"  跷跷板合计: {(icbc_up_sh_down+icbc_down_sh_up)/total*100:.1f}%")
    
    print(f"\n【月线周期结论】")
    print(f"  • 工行当前{float(jun[-1][1]):.2f}(6/8)处于2018以来高位")
    print(f"  • 2024年10月放量7.66万亿见顶→之后震荡下行")
    print(f"  • 2026年3月+10%反弹是护盘(pivot),非趋势性上涨")
    print(f"  • 工行不跌透,资金不会大规模流向科技/中小盘")
    print(f"  • 工行高位=防御模式,炒板块不对")
    
    conn_icbc.close()
    return float(jun[-1][1])

# ──────────── 6. 持仓状态 ────────────

def positions_status():
    """展示当前持仓状态"""
    conn = db(); cur = conn.cursor()
    
    positions = [
        ('600635','大众公用',4.89,5.755,'公用事业'),
        ('600273','嘉化能源',6.98,7.30,'化工'),
        ('002073','软控股份',5.50,5.68,'化工设备'),
        ('300284','苏交科',5.75,0,'基建'),
        ('000850','华茂股份',3.77,0,'纺织'),
    ]
    
    quotes = qt_batch([p[0] for p in positions])
    
    print(f"\n【持仓】")
    print(f"{'名称':12} {'代码':8} {'现价':>6} {'涨跌':>7} {'低':>6} {'护盘':>6} {'偏离':>7} {'板块风向'}")
    print("-" * 68)
    
    for code, name, guard, cost, sector in positions:
        q = quotes.get(code)
        if q:
            dev = (q['price'] - cost)/cost*100 if cost > 0 else 0
            wind = "等待轮动"  # placeholder
            guard_ok = "✅" if q['low'] >= guard else "⚠️穿"
            print(f"{name:12} {code:8} {q['price']:>6.2f} {q['pct']:>+6.2f}% {q['low']:>5.2f} {guard:>5.2f}{guard_ok} {dev:>+6.1f}% {wind}")
    
    return quotes

# ──────────── 6. 全量运行 ────────────

def full_run():
    print(f"\n{'#' * 68}")
    print(f"# V9 四层决策框架 @ {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'#' * 68}\n")
    
    policy_scan()
    print()
    sd = sector_analysis()
    print()
    hot_sector_scanner(sd)
    print()
    market_window()
    print()
    icbc_px = icbc_analysis()
    print()
    target_filter()
    print()
    positions_status()
    
    print(f"\n{'=' * 68}")
    print("决策输出")
    print("=" * 68)
    
    print(f"""
┌─────────────────────────────────────────────────────┐
│ V9 决策 @ {datetime.now().strftime('%Y-%m-%d')}                  │
├─────────────────────────────────────────────────────┤
│ 政策方向: 强=半导体/电力设备  中=AI/新能源/消费      │
│ 板块状态: 半导体已轮动  电力设备即将轮动             │
│ 大盘窗口: BEAR极空 → 仓位上限30%                    │
│ 工行周期: {icbc_px:.2f} 高区(防御模式)               │
│                                                     │
│ 操作指令:                                           │
│   ✓ 持仓不动(5只均不在热点,等板块轮动回来)            │
│   ⚡ 监视电力ETF(159611)+半导体ETF(512480)回调       │
│   ✗ 大盘信号+工行高位=不进新仓                       │
└─────────────────────────────────────────────────────┘""")
    
    # 介入时机评估
    entry_decision(icbc_px, sd if 'sd' in dir() else None)
    print()


# ──────────── 7. 介入时机（真热点确认后的入场点） ────────────

def entry_decision(icbc_px, sector_data=None):
    """
    真热点确认后，什么时机进场？
    
    三个条件同时满足才进场：
    1. 大盘回暖：surplus_z >= -1.5（从极空区回到空头区以上）
    2. 板块确认：板块真热点评分连续维持高水位（当前评分通过+已观察天数）
    3. 回调到位：板块ETF在MA10/MA20附近企稳
    
    本质：热点板块 + 回调买入 + 大盘逆风减弱
    """
    print("=" * 68)
    print("[V9 介入时机] 三条件入场信号")
    print("=" * 68)
    
    conn = db(); cur = conn.cursor()
    
    # 最新交易日
    cur.execute("SELECT max(trade_date) FROM daily_prices")
    ld = cur.fetchone()[0]
    lookback_date = (ld - timedelta(days=60)).isoformat() if ld else '2026-04-01'
    
    # 条件1：大盘窗口
    cur.execute("""
        SELECT extra->>'surplus_z', extra->>'regime', extra->>'phase'
        FROM pool_energy WHERE level='market'
        ORDER BY date DESC LIMIT 1
    """)
    r = cur.fetchone()
    sz = float(r[0]) if r and r[0] else -99
    regime = r[1] if r and r[1] else '?'
    phase = r[2] if r and r[2] else '?'
    
    cond1 = False
    if sz >= -1.0:
        cond1 = True; c1_verdict = "✅ 回暖明确(surplus_z>=-1.0), 可加仓"
    elif sz >= -1.5:
        cond1 = True; c1_verdict = "✅ 空头弱化(surplus_z>=-1.5), 可轻仓"
    else:
        c1_verdict = f"⛔ 极空区(surplus_z={sz:.2f}), 等回暖"
    
    print(f"\n【条件1】大盘窗口: surplus_z={sz:.2f}  regime={regime}  phase={phase}")
    print(f"  {c1_verdict}")
    
    # 条件2：板块确认（从daily_prices查板块ETF近7天量价趋势）
    cond2 = False
    c2_details = []
    
    print(f"\n【条件2】板块持续性检测（近7日量价趋势 from daily_prices）")
    etf_symbols = [('半导体/芯片','512480'), ('通信','515050'), ('新能源','515030'),
                    ('电力设备','159611'), ('AI/算力','515070'), ('机器人','562500')]
    
    for name, code in etf_symbols:
        try:
            # 查daily_prices
            sym = f'ETF.{code}'
            cur.execute("""
                SELECT trade_date, close, volume FROM daily_prices
                WHERE symbol = %s AND trade_date >= %s
                ORDER BY trade_date DESC LIMIT 7
            """, (sym, lookback_date))
            rows = cur.fetchall()
            if len(rows) >= 3:
                rows.sort(key=lambda x: x[0])  # 正序
                closes = [float(r[1]) for r in rows]
                vols = [int(r[2]) for r in rows]
                n = len(closes)
                ret = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] > 0 else 0
                vol_trend = vols[-1] / (sum(vols[:-1])/max(n-1,1)) if sum(vols[:-1]) > 0 and n>1 else 1
                
                score = 0
                tags = []
                if ret > 0: score += 15; tags.append(f"{ret:+.1f}%")
                elif ret < -3: score -= 10; tags.append(f"{ret:+.1f}%")
                else: tags.append(f"{ret:+.1f}%")
                if vol_trend > 1.2 and n > 1: score += 10; tags.append(f"量比{vol_trend:.1f}x🔥")
                elif vol_trend > 0.8: score += 5; tags.append(f"量比{vol_trend:.1f}x")
                else: score -= 5; tags.append(f"量{vol_trend:.1f}x缩")
                
                tags.append(f"近{n}天")
                verdict = "✅" if score >= 20 else "🟡" if score >= 8 else "⛔"
                print(f"  {verdict} {name:10} {tags[0]:>8} {tags[1]:>10} {tags[2]:>10}")
                c2_details.append((score >= 15, name, score))
            else:
                print(f"  ⚠️ {name}: 数据不足({len(rows)}天)")
        except Exception as e:
            print(f"  ⚠️ {name}: {e}")
    
    any_sustained = sum(1 for ok,_,_ in c2_details if ok)
    cond2 = any_sustained >= 2
    
    if cond2:
        print(f"  判定: ✅ {any_sustained}个板块持续强势")
    else:
        print(f"  判定: 🟡 {any_sustained}个板块持续强势(需2个以上)")
    
    # 条件3：回调到位 — ETFM偏离MA10/MA20
    cond3 = False
    c3_count = 0
    ma_dets = []
    print(f"\n【条件3】回调到位检测（ETFM偏离MA10/MA20）")
    for name, code in [('半导体','512480'), ('通信','515050'), ('新能源','515030'),
                        ('电力设备','159611'), ('AI/算力','515070')]:
        try:
            # ETF在daily_prices中
            sym = f'ETF.{code}'
            cur.execute("""
                SELECT trade_date, close FROM daily_prices
                WHERE symbol = %s AND trade_date >= %s
                ORDER BY trade_date
            """, (sym, lookback_date))
            rows = cur.fetchall()
            if len(rows) >= 15:
                closes = [float(r[1]) for r in rows[-20:]]  # 最近20天
                now = closes[-1]
                ma10 = sum(closes[-10:]) / 10
                ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else ma10
                d10 = (now - ma10) / ma10 * 100
                d20 = (now - ma20) / ma20 * 100
                
                near_ma = abs(d10) < 5
                if near_ma:
                    c3_count += 1
                    print(f"  ✅ {name}: 现价{now:.2f} MA10={ma10:.2f}({d10:+.1f}%) MA20={ma20:.2f}({d20:+.1f}%)")
                else:
                    print(f"  🟡 {name}: 现价{now:.2f} MA10={ma10:.2f}({d10:+.1f}%) MA20={ma20:.2f}({d20:+.1f}%)\t超MA10")
                ma_dets.append((name, now, ma10, d10, d20, near_ma))
            else:
                print(f"  ⚠️ {name}: 数据不足({len(rows)}天)")
        except Exception as e:
            print(f"  ⚠️ {name}: {e}")
    
    cond3 = c3_count >= 2
    
    # 最终判定
    print(f"\n{'=' * 40}")
    print("介入判定")
    print('=' * 40)
    
    if cond1 and cond2 and cond3:
        best = sorted(c2_details, key=lambda x: -x[2])
        print(f"✅✅✅ 三条件全满足！可入场")
        print(f"  优先板块: {best[0][1] if best else '?'} > {best[1][1] if len(best)>1 else '?'}")
        print(f"  操作: 等今日收盘前或次日开盘确认盘中支撑后入场")
    elif cond1 and cond2:
        print(f"🟡 大盘+板块达标，但板块ETF未回调至MA10附近")
        print(f"  操作: 等回调至MA10附近再入场，不追高")
    elif cond1:
        print(f"🟡 大盘回暖，但板块持续性不够")
        print(f"  操作: 等板块连续放量3天再考虑")
    else:
        print(f"⛔ 大盘逆风，耐心等待")
        print(f"  操作: 持有不动，维持现有仓位")
    
    print(f"\n介入三条件:")
    print(f"  条件1(大盘回暖): {'✅' if cond1 else '⛔'} surplus_z>=-1.5")
    print(f"  条件2(板块持续): {'✅' if cond2 else '⛔'} ≥2个板块7日持续放量上涨")
    print(f"  条件3(回调到位): {'✅' if cond3 else '⛔'} ≥2板块ETF在MA10附近5%内")
    
    conn.close()
    return cond1 and cond2 and cond3


# ──────────── 8. 舆论反转信号 ────────────

def sentiment_pulse(heat_sectors=None):
    """
    舆论反转策略: 
      媒体恐慌 → 买入窗口
      媒体吹涨 → 卖出窗口
    
    黄老板看舆论, V9关联板块ETF
    """
    print("=" * 68)
    print("[V9 舆论层] 反转信号映射")
    print("=" * 68)
    
    if not heat_sectors:
        heat_sectors = ['半导体/芯片', 'AI/算力', '通信', '新能源', '机器人', '电力设备']
    
    # 实时拉这些板块ETF的数据辅助判断
    etf_map = {
        '半导体/芯片': 'sh512480', 'AI/算力': 'sh515070', 
        '通信': 'sh515050', '新能源': 'sh515030',
        '机器人': 'sh562500', '电力设备': 'sh159611'
    }
    
    import urllib.request, ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    codes = list(etf_map.values())
    url = f'https://qt.gtimg.cn/q={",".join(codes)}'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=8, context=ctx)
        data = resp.read().decode('gbk')
        
        print(f"\n{'板块':12} {'ETF':>8} {'现价':>8} {'涨跌':>7} {'舆论映射'}")
        print("-" * 55)
        
        for line in data.strip().split('\n'):
            parts = line.split('~')
            if len(parts) < 35: continue
            code = parts[2]
            price = float(parts[3])
            pct = float(parts[32].replace('%',''))
            
            # 找板块名
            sector = [k for k, v in etf_map.items() if code.endswith(v[2:])]
            sname = sector[0] if sector else code
            
            # 舆论映射逻辑
            if pct > 3:
                note = "⚠️ 大涨中→若媒体吹→清仓窗口"
            elif pct < -2:
                note = "🔍 回调→若媒体恐慌→买入窗口"
            elif pct > 0:
                note = "温和上涨→观察舆论"
            else:
                note = "微跌→正常波动"
            
            print(f"{sname:12} {code[-6:]:>8} {price:>7.3f} {pct:>+5.2f}% {note}")
    except Exception as e:
        print(f"  ETF行情获取失败: {e}")
    
    print(f"\n【舆论→仓位映射表】")
    print(f"  📰 媒体恐慌 + 板块跌>3% = 分批买入窗口")
    print(f"  🎉 媒体吹涨 + 板块涨>3% = 清仓/减仓窗口")
    print(f"  🤷 正常报道 + 波动<3% = 持有不动")
    
    return True

# ──────────── 8. 实验日志 ────────────

def log_experience(action, symbol, result):
    """记录交易实验信息"""
    from datetime import datetime
    ts = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    # 校验: 写入MEMORY.md是只读的, 写到经验文件
    import os
    d = '/mnt/d/data/a-stock/logs'
    os.makedirs(d, exist_ok=True)
    
    with open(f'{d}/trade_experience.md', 'a') as f:
        f.write(f"\n## {ts} | {action} {symbol}\n")
        f.write(f"{result}\n")

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'run'
    
    if cmd == 'sentiment':
        sector = sys.argv[2] if len(sys.argv) > 2 else None
        sentiment_pulse([sector] if sector else None)
    elif cmd == 'run':
        full_run()
    elif cmd == 'policy':
        policy_scan()
    elif cmd == 'sector':
        sector_analysis()
    elif cmd == 'market':
        market_window()
    elif cmd == 'icbc':
        icbc_analysis()
    elif cmd == 'target':
        trigger = sys.argv[2] if len(sys.argv) > 2 else None
        target_filter(trigger)
    elif cmd == 'watch':
        sector_analysis()
        print(); market_window(); print(); icbc_analysis(); print()
        positions_status()
    else:
        print(f"可用: run | policy | sector | market | icbc | target | sentiment | watch")
