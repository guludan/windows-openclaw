#!/usr/bin/env python3
"""
午间盘面监测脚本（11:30~13:00 午休时运行）
扫北向资金、主力资金、盘中V8信号、持仓实时盈亏
数据源：东财API + 同花顺L1日快照（market_snapshots）
"""
import sys, os, json, requests, psycopg2
import numpy as np
from datetime import date, datetime, timedelta

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
today = date.today()

# 持仓
holdings = {
    '002073': {'name': '软控股份', 'cost': 5.68, 'weight': 0.7},
    '600273': {'name': '嘉化能源', 'cost': 7.30, 'weight': 0.3},
}

out = []
out.append(f"# 午间盘面监测 | {today}")
out.append(f"")

conn = psycopg2.connect(DB)
cur = conn.cursor()

# 1. 大盘phase
out.append(f"## 大盘特征")
cur.execute("SELECT date, phase, surplus, b_pool, a_pool FROM pool_energy WHERE level='market' ORDER BY date DESC LIMIT 1")
r = cur.fetchone()
if r:
    sur = float(r[2])
    b = float(r[3])
    a = float(r[4])
    imba = a - b
    out.append(f"- Phase: {r[1]} | B={b:.4f} | A={a:.4f} | 多空差={imba:.4f}")
    cur.execute("SELECT date, surplus FROM pool_energy WHERE level='market' ORDER BY date DESC LIMIT 2")
    r2 = cur.fetchall()
    if len(r2) == 2:
        sur_today = float(r2[0][1])
        sur_yest = float(r2[1][1])
        out.append(f"- surplus变化: {sur_yest:+.4f} → {sur_today:+.4f} ({'🔄 改善' if sur_today > sur_yest else '⬇️ 恶化'})")

# 2. 北向资金（从market_signals）
out.append(f"")
out.append(f"## 北向资金")
cur.execute(f"""
    SELECT trade_date, value, change_val FROM market_signals 
    WHERE signal_type LIKE '%north%' OR signal_type LIKE '%北向%'
    ORDER BY trade_date DESC LIMIT 5
""")
north_rows = cur.fetchall()
if north_rows:
    for r in north_rows:
        out.append(f"- {r[0]}: {float(r[1]):.2f} (变动:{float(r[2]):.2f})" if r[2] else f"- {r[0]}: {float(r[1]):.2f}")
else:
    out.append(f"- 北向资金: 盘中数据暂未更新")

# 3. surplus_z和regime
cur.execute("SELECT extra FROM pool_energy WHERE level='market' AND extra IS NOT NULL ORDER BY date DESC LIMIT 1")
r = cur.fetchone()
if r:
    try:
        import json
        e = json.loads(r[0])
        out.append(f"- surplus_z: {float(e.get('surplus_z',0)):.3f} | regime: {e.get('regime','?')} | accel: {float(e.get('accel',0)):.3f}")
    except: pass

# pool_energy最新
cur.execute("SELECT date, phase, surplus, b_pool, a_pool FROM pool_energy WHERE level='market' ORDER BY date DESC LIMIT 1")
r = cur.fetchone()
if r:
    out.append(f"- 最新Phase: {r[1]} | surplus={float(r[2]):+.4f} | B={float(r[3]):.4f} | A={float(r[4]):.4f}")
    cur.execute(f"SELECT extra->>'surplus_z',extra->>'regime',extra->>'accel' FROM pool_energy WHERE level='market' AND date='{r[0]}'")
    r2 = cur.fetchone()
    if r2:
        sz = float(r2[0]) if r2[0] else None
        regime = r2[1] or '?'
        accel = float(r2[2]) if r2[2] else None
        out.append(f"- Regime: {regime} | surplus_z: {sz:.3f} | 加速度: {accel:.3f}")

# 4. 持仓实时盈亏（从market_snapshots最近一日）
out.append(f"")
out.append(f"## 持仓状态")
out.append(f"| 股票 | 成本 | 现价(最新) | 浮盈 | 止损线 | PB | 状态 |")
out.append(f"|---|---|---|---|---|---|---|")

for sc, info in holdings.items():
    name = info['name']
    cost = info['cost']
    
    # 从snapshot拿最新数据
    cur.execute(f"SELECT price, pb, float_mv, amount FROM market_snapshots WHERE code='{sc}' ORDER BY trade_date DESC LIMIT 1")
    snap = cur.fetchone()
    if snap:
        cur_price = float(snap[0])
        pb = float(snap[1]) if snap[1] else None
        amount = float(snap[3]) if snap[3] else 0
        pnl = cur_price / cost - 1
        stop_loss = cost * 0.95
        
        # 状态
        if pnl < -0.05:
            status = "🔴 止损"
        elif pnl < -0.03:
            status = "🟡 接近止损"
        elif pnl < 0:
            status = "🟢 浮亏"
        else:
            status = "🟢 浮盈"
        
        stop_str = f"**{stop_loss:.2f}**"
        out.append(f"| {name}({sc}) | {cost:.2f} | {cur_price:.2f} | {pnl:+.2%} | {stop_str} | {pb:.2f} | {status} |")
    else:
        # 从日线拿
        cur.execute(f"SELECT close FROM daily_prices WHERE symbol='A.{sc}' AND close>0 ORDER BY trade_date DESC LIMIT 1")
        r = cur.fetchone()
        if r:
            cur_price = float(r[0])
            pnl = cur_price / cost - 1
            out.append(f"| {name}({sc}) | {cost:.2f} | {cur_price:.2f} | {pnl:+.2%} | {cost*0.95:.2f} | ? | {'🟢' if pnl >= -0.05 else '🔴'} |")
        else:
            out.append(f"| {name}({sc}) | {cost:.2f} | ? | ? | ? | ? | ⚠️ 无数据 |")

# 5. 今日V8信号扫描（从dashboard输出文件拉）
out.append(f"")
out.append(f"## 今日V8信号（收盘数据）")
dashboard_path = f"/home/tearsyu/.openclaw/workspace/memory/reports/v8_dashboard_{today}.txt"
if os.path.exists(dashboard_path):
    with open(dashboard_path) as f:
        content = f.read()
        lines = content.split('\n')
        signal_count = 0
        for line in lines:
            if '├' in line and '信号' in line:
                continue
            if '├' in line:
                signal_count += 1
        out.append(f"- 今日V8信号: {signal_count}只")
        
        # 找通过Layer 0的Top 5
        for line in lines:
            if '✅推荐' in line or '序号' in line:
                pass
        # 简单提取前5行中带推荐的
        top5 = [l for l in lines if '│' in l and '排' not in l and '序' not in l][:5]
        for t in top5:
            parts = t.split()
            if len(parts) >= 3:
                out.append(f"  {parts[0]} {parts[1]}")
else:
    out.append(f"- ❌ 仪表盘文件不存在（收盘管线尚未运行）")

# 6. 可转债折价扫描
out.append(f"")
out.append(f"## 可转债折价扫描")
try:
    import akshare as ak
    df = ak.bond_zh_cov()
    # 排除退市/ST
    df = df[
        (~df['债券简称'].str.contains('退', na=False)) &
        (~df['正股简称'].str.startswith('R', na=False)) &
        (~df['正股简称'].str.contains('ST', na=False)) &
        (~df['正股简称'].str.contains('退', na=False))
    ]
    discount = df[df['转股溢价率'] < 0].sort_values('转股溢价率')
    deep = df[df['转股溢价率'] < -3]
    out.append(f"- 折价可转债: {len(discount)}只 | 深度折价(<-3%): {len(deep)}只")
    if len(discount) > 0:
        for _, row in discount.head(5).iterrows():
            out.append(f"  {row['债券简称']}({row['正股代码']}) 溢价率={row['转股溢价率']:.2f}%")
except Exception as e:
    out.append(f"- 获取失败: {e}")

# 7. 今日操作建议
out.append(f"")
out.append(f"## 今日操作建议")
# 简单判断
cur.execute("SELECT date, phase, surplus FROM pool_energy WHERE level='market' ORDER BY date DESC LIMIT 1")
phase_r = cur.fetchone()
if phase_r:
    phase = phase_r[1]
    if phase == 'A_ACCUM':
        out.append(f"- 当前处于 {phase}（极空区），持有现仓不动。等A_RECOVER切换后脉冲拉升时减仓。")
    elif phase == 'A_RECOVER':
        out.append(f"- 当前处于 {phase}（脉冲拉升期）！密切关注是否连续拉涨≥2天，适时减仓1/2。")
    elif phase == 'NEUTRAL':
        out.append(f"- 当前处于 {phase}（阴跌出货期），持有剩余仓位等二次脉冲或主动清仓。")
    elif phase in ('B_ACCUM', 'B_DISTRIB'):
        out.append(f"- 当前处于 {phase}，机构开始重新布局。开始筛选新的V8信号。")

conn.close()

# 写入
report_dir = "/home/tearsyu/.openclaw/workspace/memory/reports"
os.makedirs(report_dir, exist_ok=True)
report_path = f"{report_dir}/lunch-{today}.md"
with open(report_path, 'w') as f:
    f.write('\n'.join(out))

disk_dir = f"/mnt/d/data/reports/{today}"
os.makedirs(disk_dir, exist_ok=True)
disk_path = f"{disk_dir}/午间盘面_{today}.md"
with open(disk_path, 'w') as f:
    f.write('\n'.join(out))

print(f"已写入 {disk_path}")
for l in out:
    print(l)
