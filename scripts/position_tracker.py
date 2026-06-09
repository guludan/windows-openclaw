#!/usr/bin/env python3
"""
持仓个股资金行为追踪（精确到个股的机构出货节奏监控）
收盘后运行，输出到 D:/data/a-stock/reports/{today}/
"""
import sys, os, json, psycopg2
import numpy as np
from datetime import date, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
today = date.today()

# 持仓配置
holdings = {
    '002073': {'name': '软控股份', 'buy_date': '2026-06-05', 'buy_price': 5.755, 'cost': 5.755, 'shares': 200, 'weight': 0.25},
    '600273': {'name': '嘉化能源', 'buy_date': '2026-06-05', 'buy_price': 7.2451, 'cost': 7.2451, 'shares': 400, 'weight': 0.40},
    '600635': {'name': '大众公用', 'buy_date': '2026-06-05', 'buy_price': 5.1051, 'cost': 5.1051, 'shares': 200, 'weight': 0.35},
}

conn = psycopg2.connect(DB)
cur = conn.cursor()

# 1. 大盘环境
cur.execute("SELECT date, phase, surplus, b_pool, a_pool FROM pool_energy WHERE level='market' ORDER BY date")
pm_data = cur.fetchall()

cur.execute("SELECT date, extra->>'sz_5', extra->>'sz_20', extra->>'surplus_z', extra->>'regime', extra->>'accel', extra->>'advance_ratio' FROM pool_energy WHERE level='market' AND extra IS NOT NULL ORDER BY date")
regime_data = {}
for r in cur.fetchall():
    try:
        regime_data[r[0]] = {
            'sz5': float(r[1]) if r[1] else None,
            'sz20': float(r[2]) if r[2] else None,
            'sz': float(r[3]) if r[3] else None,
            'regime': r[4],
            'accel': float(r[5]) if r[5] else None,
            'ar': float(r[6])*100 if r[6] else None,
        }
    except: pass

# 极空区持续天数
sz_sink_days = 0
for d, ri in sorted(regime_data.items()):
    if ri.get('sz') is not None and ri['sz'] < -2.0:
        sz_sink_days += 1
    else:
        sz_sink_days = 0

# 取最新可用数据（今天可能还未写入）
market_dates = [d for d in regime_data.keys() if d <= today]
market = regime_data.get(max(market_dates), {}) if market_dates else {}
market_phase = None
for r in pm_data:
    if r[0] <= today:
        market_phase = r[1]

out_lines = []
out_lines.append(f"# 持仓个股资金行为监控 | {today}")
out_lines.append("")
out_lines.append(f"## 大盘环境")
out_lines.append(f"- Regime: {market.get('regime','?')}")
out_lines.append(f"- Phase: {market_phase}")
sz = market.get('sz', 0) if market.get('sz') is not None else 0
sz5 = market.get('sz5', 0) if market.get('sz5') is not None else 0
sz20 = market.get('sz20', 0) if market.get('sz20') is not None else 0
ar = market.get('ar', 0) if market.get('ar') is not None else 0
accel = market.get('accel', 0) if market.get('accel') is not None else 0
out_lines.append(f"- surplus_z: {sz:.3f} (sz5={sz5:.3f} sz20={sz20:.3f})")
out_lines.append(f"- 加速度: {accel:.3f}")
out_lines.append(f"- 上涨比率: {ar:.1f}%")
out_lines.append(f"- 极空区已持续: {sz_sink_days}天")
out_lines.append("")

for sc, info in holdings.items():
    name = info['name']
    buy_price = info['buy_price']
    cost = info['cost']
    
    cur.execute(f"""
        SELECT trade_date, close, open, high, low, volume, amount
        FROM daily_prices WHERE symbol='A.{sc}' AND close > 0
        ORDER BY trade_date
    """)
    rows = cur.fetchall()
    if not rows:
        out_lines.append(f"## {name}({sc}) — 无数据\n")
        continue
    
    dates = [r[0] for r in rows]
    closes = np.array([float(r[1]) for r in rows])
    opens = np.array([float(r[2]) for r in rows])
    highs = np.array([float(r[3]) for r in rows])
    lows = np.array([float(r[4]) for r in rows])
    volumes = np.array([float(r[5]) for r in rows])
    amounts = np.array([float(r[6]) if r[6] else 0 for r in rows])
    n = len(closes)
    idx = n - 1
    
    # SMA120
    sma120 = np.full(n, np.nan)
    cum = np.cumsum(closes)
    for i in range(119, n):
        sma120[i] = cum[i]/120 if i == 119 else (cum[i]-cum[i-120])/120
    sma_dev = closes/sma120 - 1
    
    # 量比(20d)
    vol_ma20 = np.full(n, np.nan)
    if n >= 20:
        vc = np.cumsum(volumes)
        vol_ma20[19] = vc[19]/20
        vol_ma20[20:] = (vc[20:] - vc[:n-20])/20
    vol_ratio = np.where(vol_ma20 > 0, volumes / np.maximum(vol_ma20, 1), np.nan)
    
    # 从market_snapshots拉PB和流通市值
    cur.execute(f"SELECT pb, float_mv FROM market_snapshots WHERE code='{sc}' AND trade_date >= '{today - timedelta(days=14)}' ORDER BY trade_date DESC LIMIT 1")
    snap = cur.fetchone()
    pb_val = float(snap[0]) if snap and snap[0] else None
    float_mv = float(snap[1]) if snap and snap[1] else None
    
    # 换手率（float_mv是亿，amount是元）
    turnover = None
    if float_mv and float_mv > 0 and amounts[idx] > 0:
        # float_mv是亿(100M) → 转为元，amount是元
        float_mv_yuan = float_mv * 100000000
        turnover = amounts[idx] / float_mv_yuan * 100
    
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
    
    # 振幅(20d)
    cur_amp = np.mean([(highs[j]-lows[j])/opens[j] for j in range(max(0,idx-19), idx+1) if opens[j] > 0]) if n > 1 else 0
    prev_amp = np.mean([(highs[j]-lows[j])/opens[j] for j in range(max(0,idx-39), max(0,idx-19)) if opens[j] > 0])
    amp_trend = (cur_amp / prev_amp - 1) if prev_amp > 0 else 0
    
    # 前5日涨幅
    ret5 = closes[idx]/closes[max(0,idx-5)] - 1 if n >= 6 else 0
    
    # SMA120斜率(20d)
    sma_slope = sma120[idx]/sma120[max(0,idx-20)] - 1 if not np.isnan(sma120[idx]) and not np.isnan(sma120[max(0,idx-20)]) else 0
    
    # 当前浮亏/浮盈
    cur_price = closes[idx]
    pnl = cur_price / cost - 1
    
    # ========== 机构资金行为分析 ==========
    last_30_r1d = r1d[-30:] if n >= 30 else r1d[-n:]
    last_30_vol = vol_ratio[-30:] if n >= 30 else vol_ratio[-n:]
    
    # 特征1：连跌后突然放量上涨（机构试盘）
    trial_signal = False
    for i in range(max(0, n-5), n):
        if conseq_dn[i-1] >= 3 and not np.isnan(r1d[i]) and r1d[i] > 0.02 and not np.isnan(vol_ratio[i]) and vol_ratio[i] > 1.5:
            trial_signal = True
    
    # 特征2：放量阳线+次日缩量（机构急拉后停手）
    pump_signal = False
    pump_days = 0
    for i in range(max(2, n-10), n):
        if not np.isnan(r1d[i]) and r1d[i] > 0.03 and not np.isnan(vol_ratio[i]) and vol_ratio[i] > 1.8:
            pump_days += 1
    if pump_days >= 1:
        pump_signal = True
    
    # 特征3：量能萎缩+振幅收窄（无人问津，进入底部磨盘）
    shrink_signal = False
    if not np.isnan(vol_ratio[idx]) and vol_ratio[idx] < 0.7 and amp_trend < -0.2:
        shrink_signal = True
    
    # 特征4：温和放量+小幅阳线推升（机构吸筹）
    accumulate_signal = False
    recent_vol = [vol_ratio[min(i, n-1)] for i in range(n-10, n) if not np.isnan(vol_ratio[min(i, n-1)])]
    recent_ret = [r1d[i] for i in range(n-10, n) if not np.isnan(r1d[i])]
    if len(recent_vol) >= 5 and len(recent_ret) >= 5:
        avg_v = np.mean(recent_vol[-5:])
        avg_v_prev = np.mean(recent_vol[:5])
        avg_r = np.mean(recent_ret[-5:])
        if avg_v > 0.8 and avg_r > 0:
            accumulate_signal = True
    
    # ========== 输出个股卡片 ==========
    out_lines.append(f"## {name}({sc})")
    out_lines.append("")
    out_lines.append(f"### 持仓状态")
    out_lines.append(f"| 指标 | 数值 |")
    out_lines.append(f"|---|---|")
    out_lines.append(f"| 买入价 | {cost:.2f} |")
    out_lines.append(f"| 现价 | {cur_price:.2f} |")
    out_lines.append(f"| 浮盈/浮亏 | **{pnl:+.2%}** |")
    out_lines.append(f"| 止损线 | **{cost*0.95:.2f}** (-5%) |")
    out_lines.append(f"| 仓位权重 | {info['weight']:.1f} |")
    out_lines.append("")
    
    # 技术面
    out_lines.append(f"### 技术面")
    out_lines.append(f"| 指标 | 今日 | 5日前 | 方向 |")
    out_lines.append(f"|---|---|---|---|")
    out_lines.append(f"| SMA偏离 | {sma_dev[idx]:+.2%} | {sma_dev[max(0,idx-5)]:+.2%} | {'🔄 向好' if sma_dev[idx] > sma_dev[max(0,idx-5)] else '⬇️ 恶化' if sma_dev[idx] < sma_dev[max(0,idx-5)] else '➡️ 持平'} |")
    pb_str = f"| PB | {pb_val:.2f} | - | ✅ 破净" if pb_val and pb_val < 1 else f"| PB | {pb_val:.2f} | - | ⚠️ 未破净" if pb_val else "| PB | ? | - | ?"
    out_lines.append(pb_str)
    turnover_str = f"{turnover:.2f}%" if turnover is not None else "?"
    turnover_tag = '🔥 活跃' if turnover and turnover > 2 else '💤 低迷' if turnover and turnover < 0.5 else '✅ 正常' if turnover else '?'
    out_lines.append(f"| 换手率 | {turnover_str} | - | {turnover_tag} |")
    out_lines.append(f"| 量比(20d) | {vol_ratio[idx]:.2f}x | {vol_ratio[max(0,idx-5)]:.2f}x | {'⬆️ 放量' if vol_ratio[idx] > 1.5 else '⬇️ 缩量' if vol_ratio[idx] < 0.7 else '➡️ 正常'} |")
    out_lines.append(f"| 连跌/连涨 | {conseq_dn[idx]}d {'📉' if conseq_dn[idx] > 0 else '📈' if conseq_dn[idx] == 0 and r1d[idx] > 0 else '—'} | {'🔴 连跌' if conseq_dn[idx] >= 3 else '🟡 短期' if conseq_dn[idx] >= 1 else '🟢 止跌'} |")
    out_lines.append(f"| 振幅趋势 | {amp_trend:+.2%} | - | {'⚡ 活跃化' if amp_trend > 0.1 else '💤 收缩中' if amp_trend < -0.1 else '➡️ 持平'} |")
    out_lines.append(f"| 前5日涨跌 | {ret5:+.2%} | - | {'📈 反弹' if ret5 > 0.03 else '📉 下跌' if ret5 < -0.03 else '➡️ 横盘'} |")
    out_lines.append("")
    
    # 资金行为分析
    out_lines.append(f"### 资金行为信号")
    out_lines.append(f"| 信号 | 判定 | 含义 |")
    out_lines.append(f"|---|---|---|")
    out_lines.append(f"| 🔵 试盘信号 | {'✅ 有' if trial_signal else '❌ 无'} | {'连跌后放量反弹，机构试探抛压' if trial_signal else '未见连跌后放量拉升，机构尚未动手' if not trial_signal else ''} |")
    out_lines.append(f"| 🔴 脉冲信号 | {'✅ 有(近10日'+str(pump_days)+'次)' if pump_signal else '❌ 无'} | {'机构急拉吸引跟风盘' if pump_signal else '未见急拉脉冲'} |")
    out_lines.append(f"| 🟢 磨底信号 | {'✅ 有(量缩+振幅收窄)' if shrink_signal else '❌ 无'} | {'无人关注，底部磨盘区' if shrink_signal else '量能正常或假放量'} |")
    out_lines.append(f"| 🟡 吸筹信号 | {'✅ 有(温和放量+小阳推升)' if accumulate_signal else '❌ 无'} | {'机构在底部悄悄拿筹码' if accumulate_signal else '未见温和吸筹痕迹'} |")
    out_lines.append("")
    
    # 综合判断
    out_lines.append(f"### 综合判断与操作建议")
    
    # 近期10天量价行为明细
    out_lines.append(f"**近10日量价明细：**")
    hdr = f"{'日期':>12} {'收盘':>7} {'涨跌':>8} {'量比':>6} {'振幅':>6} {'连跌':>4}"
    out_lines.append(f"| {'日期':>12} | {'收盘':>7} | {'涨跌':>9} | {'量比':>6} | {'振幅':>6} | {'连跌':>4} | {'判断':>10} |")
    out_lines.append(f"|{'—'*14}|{'—'*9}|{'—'*11}|{'—'*8}|{'—'*8}|{'—'*6}|{'—'*12}|")
    for i in range(max(0, n-10), n):
        d = dates[i]
        a = highs[i] - lows[i]
        am = a/opens[i] if opens[i] > 0 else 0
        vr = vol_ratio[i] if not np.isnan(vol_ratio[i]) else 0
        r1 = r1d[i] if not np.isnan(r1d[i]) else 0
        cd = conseq_dn[i]
        
        # 每日定性
        if r1 > 0.03 and vr > 1.8:
            tag = '⚡拉升'
        elif r1 < -0.03 and vr > 1.5:
            tag = '💥砸盘'
        elif vr < 0.7 and abs(r1) < 0.02:
            tag = '💤磨底'
        elif vr > 1.5 and abs(r1) < 0.02:
            tag = '⚠️异常'
        elif r1 > 0 and vr > 1.0:
            tag = '📗温和涨'
        elif r1 < 0 and cd >= 3:
            tag = '📉连跌'
        else:
            tag = '➡️平淡'
        
        out_lines.append(f"| {d} | {closes[i]:>7.2f} | {r1:>+8.2%} | {vr:>5.2f}x | {am:>5.2%} | {cd:>3d} | {tag:>10} |")
    
    out_lines.append("")
    
    # 操作建议
    if pnl < -0.05:
        action = "🔴 **触及止损线！建议执行止损**"
    elif sz_sink_days < 7:
        action = "🟢 **极空区早期，持有不动。等A_RECOVER切换后出现脉冲拉升时减仓。**"
    elif sz_sink_days >= 7 and not pump_signal:
        action = "🟡 **极空区已持续较久，持有。密切关注是否出现机构试盘/脉冲信号。**"
    elif pump_signal:
        action = "🔴 **脉冲拉升已出现！如果连续拉涨≥2天，主动减仓1/2锁定利润。**"
    elif shrink_signal:
        action = "🟢 **底部磨盘特征明显，持有。磨盘结束后大概率有一波脉冲拉升。**"
    elif accumulate_signal:
        action = "🟢 **机构在持续吸筹，持有。等吸筹完毕后脉冲拉升时减仓。**"
    else:
        action = "🟢 **持有现仓，等待信号明朗。**"
    
    out_lines.append(f"**→ {action}**")
    out_lines.append("")
    out_lines.append("---")
    out_lines.append("")

conn.close()

# 写入
report_dir = "/home/tearsyu/.openclaw/workspace/memory/reports"
os.makedirs(report_dir, exist_ok=True)
report_path = f"{report_dir}/position_tracker_{today}.md"
with open(report_path, 'w') as f:
    f.write('\n'.join(out_lines))

disk_dir = f"/mnt/d/data/a-stock/reports/{today}"
os.makedirs(disk_dir, exist_ok=True)
disk_path = f"{disk_dir}/持仓资金行为监控_{today}.md"
with open(disk_path, 'w') as f:
    f.write('\n'.join(out_lines))

print(f"已写入 {disk_path}")
print('\n'.join(out_lines[:5]))
