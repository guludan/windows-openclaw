#!/usr/bin/env python3
"""
庄家行为全市场扫描（战术层）
识别全流通A股的庄家行为阶段：建仓/洗盘/试盘/拉升/出货
牛市/熊市差异化参数
每日收盘后运行，输出到 D:/data/a-stock/reports/{today}/
"""
import sys, os, json, psycopg2
import numpy as np
from datetime import date, timedelta, datetime
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
today = date.today()

# 最小流通市值（亿），过滤微盘股
MIN_FLOAT_MV = 10

# 持仓关注
watchlist = ['002073', '600273']

conn = psycopg2.connect(DB)
cur = conn.cursor()

# 1. 获取大盘regime和phase
cur.execute("SELECT date, phase FROM pool_energy WHERE level='market' ORDER BY date DESC LIMIT 1")
phase_r = cur.fetchone()
current_phase = phase_r[1] if phase_r else 'NEUTRAL'

cur.execute("SELECT extra FROM pool_energy WHERE level='market' AND extra IS NOT NULL ORDER BY date DESC LIMIT 1")
extra_r = cur.fetchone()
regime_info = {}
if extra_r:
    try:
        e = json.loads(extra_r[0])
        regime_info = {
            'regime': e.get('regime','SIDEWAYS'),
            'sz': float(e.get('surplus_z',0)),
            'accel': float(e.get('accel',0)),
        }
    except: pass

regime = regime_info.get('regime', 'SIDEWAYS')
is_bear = regime in ('BEAR', 'BOTTOM_WARNING')
is_bull = regime == 'BULL'

# 2. 获取所有有流通市值的股票（从market_snapshots最新日）
cur.execute("""
    SELECT code, pb, float_mv FROM market_snapshots 
    WHERE trade_date = (SELECT max(trade_date) FROM market_snapshots)
    AND float_mv >= %s AND pb > 0
""", (MIN_FLOAT_MV,))
all_stocks = {r[0]: {'pb': float(r[1]), 'float_mv': float(r[2])} for r in cur.fetchall()}
print(f"流通市值>={MIN_FLOAT_MV}亿的股票: {len(all_stocks)}只")

# 3. 逐只分析
results = {}  # code -> {stage, signals, detail}

analyzed = 0
for sc, info in all_stocks.items():
    # 拉日线（过去120天）
    cur.execute(f"""
        SELECT trade_date, close, open, high, low, volume, amount
        FROM daily_prices WHERE symbol='A.{sc}' AND close > 0
        ORDER BY trade_date DESC LIMIT 120
    """)
    rows = cur.fetchall()
    if len(rows) < 60:
        continue
    
    rows.reverse()
    n = len(rows)
    dates = [r[0] for r in rows]
    closes = np.array([float(r[1]) for r in rows])
    opens = np.array([float(r[2]) for r in rows])
    highs = np.array([float(r[3]) for r in rows])
    lows = np.array([float(r[4]) for r in rows])
    volumes = np.array([float(r[5]) for r in rows])
    amounts = np.array([float(r[6]) if r[6] else 0 for r in rows])
    
    idx = n - 1
    
    # === 基本指标 ===
    # 20日阳线比例
    yang_20 = np.sum(closes[-20:] >= opens[-20:]) / 20
    
    # 10日均量 / 60日均量
    vol_10 = np.mean(volumes[-10:]) if n >= 10 else np.mean(volumes)
    vol_60 = np.mean(volumes[-60:]) if n >= 60 else np.mean(volumes)
    vol_ratio_10_60 = vol_10 / max(vol_60, 1)
    
    # 20日振幅
    amp_20 = np.mean([(highs[i]-lows[i])/opens[i] for i in range(max(0, n-20), n) if opens[i] > 0]) if n >= 20 else 0
    
    # 量比(20d)
    if n >= 20:
        vol_ma20 = np.mean(volumes[-20:])
        vol_ratio = volumes[-1] / max(vol_ma20, 1)
    else:
        vol_ma20 = np.mean(volumes)
        vol_ratio = volumes[-1] / max(vol_ma20, 1)
    
    # 日涨跌
    r1d = np.full(n, np.nan)
    r1d[1:] = closes[1:] / closes[:-1] - 1
    
    # 连跌天数
    conseq_dn = np.zeros(n, dtype=int)
    for i in range(1, n):
        if not np.isnan(r1d[i]) and r1d[i] < 0:
            conseq_dn[i] = conseq_dn[i-1] + 1
    
    # 连跌总跌幅
    total_decline = np.zeros(n)
    for i in range(1, n):
        if conseq_dn[i] > 0:
            total_decline[i] = np.prod(1 + r1d[i-conseq_dn[i]+1:i+1]) - 1
    
    # === 逆势抗跌检测 ===
    cur.execute(f"SELECT close FROM daily_prices WHERE symbol='A.000001' AND close > 0 ORDER BY trade_date DESC LIMIT 5")
    idx_closes = [float(r[0]) for r in cur.fetchall()]
    anti_market = False
    if len(idx_closes) >= 2:
        idx_ret = (idx_closes[0] / idx_closes[-1] - 1)
        if idx_ret < -0.01:  # 大盘跌
            stock_ret = r1d[-1] if not np.isnan(r1d[-1]) else 0
            if stock_ret > idx_ret * 0.5:  # 比大盘少跌一半
                anti_market = True
    
    # === 阶段判定 ===
    stage = "无主力特征"
    signals = []
    
    # 熊市/牛市差异化参数
    if is_bear:
        wash_depth = -0.05  # 洗盘更深
        wash_volume = 2.0   # 放量更大
        pullback_days = 5    # 拉回更慢
        pump_duration = 5    # 脉冲拉升
    else:
        wash_depth = -0.03
        wash_volume = 1.8
        pullback_days = 3
        pump_duration = 10
    
    # (A) 建仓期判断
    is_accumulating = (yang_20 > 0.55 and 
                       1.2 <= vol_ratio_10_60 <= 1.8 and 
                       amp_20 < 0.05 and 
                       anti_market)
    
    # 连续15天确认建仓期
    # 简化：如果最近10天阳线>60%，量能温和，振幅小
    yang_10 = np.sum(closes[-10:] >= opens[-10:]) / 10
    vol_ratio_10 = np.mean(volumes[-5:]) / max(np.mean(volumes[-20:-5]), 1)
    
    if (yang_10 > 0.6 and 
        1.0 < vol_ratio_10 < 1.5 and 
        amp_20 < 0.05 and 
        anti_market and
        not np.isnan(r1d[-1]) and r1d[-1] > 0):
        stage = "建仓期"
        signals.append("绿肥红瘦")
    
    # (B) 洗盘期判断：连跌3天以上+放量 → 缩量 → V型拉回
    # 检查最近10天是否有"放量下跌→缩量→回升"模式
    for i in range(max(5, n-10), n):
        if (conseq_dn[i] >= 2 and 
            not np.isnan(r1d[i]) and r1d[i] < wash_depth and
            volumes[i] > wash_volume * np.mean(volumes[max(0,i-5):i]) and
            i + 3 < n):
            # 检查后续缩量
            post_vol = volumes[i:i+3]
            pre_vol = volumes[max(0,i-5):i].mean()
            if max(post_vol) < 0.3 * pre_vol:
                stage = "洗盘期"
                signals.append(f"洗盘砸盘({dates[i]})")
                break
    
    # (C) 试盘期判断：长上影线+冲高回落
    for i in range(max(2, n-10), n):
        candle_range = highs[i] - lows[i]
        upper_shadow = highs[i] - max(opens[i], closes[i])
        lower_shadow = min(opens[i], closes[i]) - lows[i]
        
        if (candle_range > amp_20 * 1.5 and
            upper_shadow / max(candle_range, 0.001) > 0.5 and
            upper_shadow > lower_shadow * 2 and
            closes[i] < opens[i] and  # 收阴线
            r1d[i] > -0.03 and  # 跌幅不大
            volumes[i] > 2.0 * np.mean(volumes[max(0,i-5):i])):
            stage = "试盘期"
            signals.append(f"试盘冲高回落({dates[i]})")
            break
    
    # (D) 拉升期判断
    if stage == "无主力特征":
        # 连续5日阳线为主
        yang_5 = np.sum(closes[-5:] >= opens[-5:])
        if (yang_5 >= 3 and 
            vol_ratio > 1.2 and
            not np.isnan(r1d[-1]) and r1d[-1] > 0 and
            amp_20 > 0.04):
            # 检查是否突破前60日高点
            high_60 = np.max(highs[-60:-5]) if n >= 65 else np.max(highs[:n-5])
            current_high = highs[-1]
            if current_high > high_60 * 0.95:
                stage = "拉升期"
                signals.append("量价配合突破")
                # 判断是否熊市脉冲
                if is_bear:
                    signals.append("熊市脉冲")
    
    # (E) 出货期判断
    if stage in ("拉升期", "无主力特征"):
        # 放量滞涨
        past_5_vol = np.mean(volumes[-5:])
        past_20_vol = np.mean(volumes[-20:])
        vol_increase = past_5_vol / max(past_20_vol, 1)
        
        # 长上影线次数
        shadow_count = 0
        for i in range(max(2, n-10), n):
            candle_range = highs[i] - lows[i]
            upper_shadow = highs[i] - max(opens[i], closes[i])
            if candle_range > 0 and upper_shadow / candle_range > 0.4:
                shadow_count += 1
        
        if vol_increase > 1.3 and shadow_count >= 3:
            stage = "出货期"
            signals.append("放量滞涨+长上影")
        elif vol_increase > 1.5 and stage == "拉升期":
            # 量价背离检查
            ret_5 = closes[-1] / closes[-5] - 1 if n >= 5 else 0
            if ret_5 < 0.02:  # 量放大但价格没怎么涨
                stage = "出货期"
                signals.append("量价背离")
    
    # 如果拉升但SMA偏离很深，标记V8+拉升
    # 简单SMA偏离计算
    if n >= 120:
        sma120 = np.mean(closes[-121:-1])  # -121:-1 不含当天，避免前视偏差
        sma_dev = closes[-1]/sma120 - 1
    else:
        sma_dev = 0
    
    results[sc] = {
        'name': '',  # 后面查name
        'pb': info['pb'],
        'float_mv': info['float_mv'],
        'stage': stage,
        'signals': signals,
        'sma_dev': sma_dev,
        'yang_ratio': yang_20,
        'vol_ratio': vol_ratio_10_60,
        'amp_20': amp_20,
        'anti_market': anti_market,
        'price': closes[-1],
        'ret_5': (closes[-1]/closes[-5]-1) if n >= 5 else 0,
        'conseq_dn': int(conseq_dn[-1]),
        'total_decline': float(total_decline[-1]),
    }
    
    analyzed += 1
    if analyzed % 500 == 0:
        print(f"  已分析 {analyzed}/{len(all_stocks)}...")

# 4. 补充股票名称
codes = list(results.keys())
for i in range(0, len(codes), 200):
    batch = codes[i:i+200]
    syms = ["'A."+c+"'" for c in batch]
    cur.execute(f"SELECT code, name FROM stock_list WHERE code IN ({','.join(['%s']*len(batch))})", tuple(batch))
    for r in cur.fetchall():
        if r[0] in results:
            results[r[0]]['name'] = r[1] or ''

# 5. 按阶段分类输出
by_stage = defaultdict(list)
for sc, info in results.items():
    by_stage[info['stage']].append((sc, info))

# 排序
for stage_name in by_stage:
    by_stage[stage_name].sort(key=lambda x: x[1]['sma_dev'])  # 按SMA偏离排序（最深的最优）

# 持仓检查
holdings_check = []
for sc in watchlist:
    if sc in results:
        holdings_check.append(results[sc])
    else:
        holdings_check.append(None)

# ===== 输出 =====
out = []
out.append(f"# 庄家行为全市场扫描 | {today}")
out.append(f"")
out.append(f"**Regime**: {regime} | **Phase**: {current_phase} | **sz**: {regime_info.get('sz',0):.3f}")
out.append(f"**总分析**: {analyzed}只 | **流通市值>={MIN_FLOAT_MV}亿**")
out.append(f"")

# 持仓检查
out.append(f"## 当前持仓监控")
out.append(f"")
for i, sc in enumerate(watchlist):
    info = results.get(sc)
    if not info: continue
    stage = info['stage']
    sigs = ' | '.join(info['signals']) if info['signals'] else '无'
    out.append(f"### {info['name']}({sc}) — **{stage}**")
    out.append(f"| 指标 | 数值 |")
    out.append(f"|---|---|")
    out.append(f"| 现价 | {info['price']:.2f} |")
    out.append(f"| PB | {info['pb']:.2f} |")
    out.append(f"| SMA偏离 | {info['sma_dev']:+.2%} |")
    out.append(f"| 阳线比例(20d) | {info['yang_ratio']:.0%} |")
    out.append(f"| 量比(10/60) | {info['vol_ratio']:.2f}x |")
    out.append(f"| 振幅(20d) | {info['amp_20']:.2%} |")
    out.append(f"| 抗跌(大盘跌时) | {'✅' if info['anti_market'] else '❌'} |")
    out.append(f"| 信号 | {sigs} |")
    advice_map = {
        '建仓期': '持有不动。等洗盘/试盘信号出现后加仓。',
        '洗盘期': '⚠️ 洗盘砸盘！成交量已萎缩时补仓。',
        '试盘期': '⚠️ 试盘冲高回落！等回调到试盘前水平加仓。',
        '拉升期': '🔴 拉升中！减仓窗口在中后期。持续关注是否进入出货期。',
        '出货期': '🔴 主力出货！建议减仓或清仓。',
        '无主力特征': '无主力特征。持有等信号。',
    }
    out.append(f"| 建议 | {advice_map.get(stage, '持有等信号。')} |")
    out.append(f"")
    out.append(f"---")
    out.append(f"")

# 按阶段列出
for stage_name in ["建仓期", "洗盘期", "试盘期", "拉升期", "出货期"]:
    stocks = by_stage.get(stage_name, [])
    out.append(f"")
    out.append(f"## {stage_name} — {len(stocks)}只")
    out.append(f"")
    if stage_name == '建仓期':
        out.append(f"| 代码 | 名称 | PB | SMA偏离 | 阳线率 | 量比 | 振幅 | 抗跌 |")
        out.append(f"|---|---|---|---|---|---|---|---|")
        for sc, info in stocks[:15]:
            out.append(f"| {sc} | {info.get('name','?')[:6]} | {info['pb']:.2f} | {info['sma_dev']:+.2%} | {info['yang_ratio']:.0%} | {info['vol_ratio']:.2f}x | {info['amp_20']:.2%} | {'✅' if info['anti_market'] else '❌'} |")
    elif stage_name == '洗盘期':
        out.append(f"| 代码 | 名称 | PB | SMA偏离 | 连跌天数 | 总跌幅 | 信号 |")
        out.append(f"|---|---|---|---|---|---|---|")
        for sc, info in stocks[:15]:
            sig_str = info['signals'][0] if info['signals'] else ''
            out.append(f"| {sc} | {info.get('name','?')[:6]} | {info['pb']:.2f} | {info['sma_dev']:+.2%} | {info['conseq_dn']}d | {info['total_decline']:.2%} | {sig_str} |")
    elif stage_name == '试盘期':
        out.append(f"| 代码 | 名称 | PB | SMA偏离 | 5日涨跌 | 连跌 |")
        out.append(f"|---|---|---|---|---|---|")
        for sc, info in stocks[:15]:
            out.append(f"| {sc} | {info.get('name','?')[:6]} | {info['pb']:.2f} | {info['sma_dev']:+.2%} | {info['ret_5']:+.2%} | {info['conseq_dn']}d |")
    elif stage_name == '拉升期':
        out.append(f"| 代码 | 名称 | PB | SMA偏离 | 5日涨跌 | 量比 |")
        out.append(f"|---|---|---|---|---|---|")
        for sc, info in stocks[:15]:
            out.append(f"| {sc} | {info.get('name','?')[:6]} | {info['pb']:.2f} | {info['sma_dev']:+.2%} | {info['ret_5']:+.2%} | {info['vol_ratio']:.2f}x |")
    elif stage_name == '出货期':
        out.append(f"| 代码 | 名称 | PB | SMA偏离 | 5日涨跌 | 信号 |")
        out.append(f"|---|---|---|---|---|---|")
        for sc, info in stocks[:15]:
            sig_str = info['signals'][0] if info['signals'] else ''
            out.append(f"| {sc} | {info.get('name','?')[:6]} | {info['pb']:.2f} | {info['sma_dev']:+.2%} | {info['ret_5']:+.2%} | {sig_str} |")

# V8+建仓重叠的（最高优先级）
v8_overlap = [(sc, info) for sc, info in by_stage.get('建仓期', []) if info['sma_dev'] < -0.15]
v8_overlap.sort(key=lambda x: x[1]['sma_dev'])
out.append(f"")
out.append(f"## V8+建仓重叠（最高优先级）")
out.append(f"")
if v8_overlap:
    out.append(f"| 代码 | 名称 | PB | SMA偏离 | 阳线率 | 量比 |")
    out.append(f"|---|---|---|---|---|---|")
    for sc, info in v8_overlap[:10]:
        out.append(f"| {sc} | {info.get('name','?')[:6]} | {info['pb']:.2f} | {info['sma_dev']:+.2%} | {info['yang_ratio']:.0%} | {info['vol_ratio']:.2f}x |")
else:
    out.append(f"暂无符合条件的标的")
    out.append(f"")
    out.append(f"（V8超跌信号标的当前未处于建仓期——这是正常的，极空区机构也在观望）")

# 写入
report_dir = "/home/tearsyu/.openclaw/workspace/memory/reports"
os.makedirs(report_dir, exist_ok=True)
report_path = f"{report_dir}/zhuangjia_{today}.md"
with open(report_path, 'w') as f:
    f.write('\n'.join(out))

disk_dir = f"/mnt/d/data/a-stock/reports/{today}"
os.makedirs(disk_dir, exist_ok=True)
disk_path = f"{disk_dir}/庄家行为监控_{today}.md"
with open(disk_path, 'w') as f:
    f.write('\n'.join(out))

print(f"\n已写入 {disk_path}")
print(f"- 建仓期: {len(by_stage.get('建仓期',[]))}只")
print(f"- 洗盘期: {len(by_stage.get('洗盘期',[]))}只")
print(f"- 试盘期: {len(by_stage.get('试盘期',[]))}只")
print(f"- 拉升期: {len(by_stage.get('拉升期',[]))}只")
print(f"- 出货期: {len(by_stage.get('出货期',[]))}只")
print(f"- V8+建仓重叠: {len(v8_overlap)}只")

conn.close()
