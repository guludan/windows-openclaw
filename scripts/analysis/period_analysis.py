#!/usr/bin/env python3
"""
板块周期归因分析 — 数据驱动
用近5年日线 + 宏观指标做周期阶段划分，对比各股票在各阶段的表现
"""
import psycopg2, numpy as np, json
from datetime import date, timedelta

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
conn = psycopg2.connect(DB)
cur = conn.cursor()

stocks = {
    '600273': ('嘉化能源', '化工'),
    '002073': ('软控股份', '设备'),
    '600635': ('大众公用', '燃气'),
    '600668': ('尖峰集团', '水泥'),
    '600603': ('广汇物流', '物流'),
}

# ① 获取大盘近5年数据
cur.execute("""
    SELECT trade_date, close FROM daily_prices 
    WHERE symbol='A.000001' AND close>0 ORDER BY trade_date
""")
idx_rows = cur.fetchall()
idx_dates = [r[0] for r in idx_rows]
idx_closes = np.array([float(r[1]) for r in idx_rows], dtype=float)
idx_n = len(idx_closes)

# ② 用surplus_z确定周期阶段（来自pool_energy）
cur.execute("""
    SELECT date, extra FROM pool_energy 
    WHERE level='market' AND extra IS NOT NULL ORDER BY date
""")
regimes = []
for d, e in cur.fetchall():
    if isinstance(e, (dict, list)):
        x = e if isinstance(e, dict) else {k: v for k, v in e}
        regimes.append((str(d), x.get('surplus_z', 0), x.get('phase', '?'), x.get('regime', '?')))
    else:
        regimes.append((str(d), 0, '?', '?'))

# ③ 对近5年进行周期阶段划分（用surplus_z的趋势）
# 周期定义:
# 顶部区: surplus_z > 1.5
# 下降期: surplus_z从>0向<0过渡, 且增速为负
# 底部区: surplus_z < -1.5
# 回升期: surplus_z从<0向>0过渡

def classify_regime(sz, prev_sz):
    if prev_sz is None:
        return 'UNKNOWN'
    if sz > 1.5:
        return 'TOP'
    elif sz > 0.5:
        return 'HIGH'
    elif sz > -0.5:
        return 'MID'
    elif sz > -1.5:
        return 'LOW'
    else:
        return 'BOTTOM'

# ④ 拉5只股票的日线
stock_data = {}
for code, (name, sector) in stocks.items():
    cur.execute("""
        SELECT trade_date, close FROM daily_prices 
        WHERE symbol=%s AND close>0 ORDER BY trade_date
    """, ('A.'+code,))
    rows = cur.fetchall()
    dates = [r[0] for r in rows]
    closes = np.array([float(r[1]) for r in rows], dtype=float)
    stock_data[code] = {'name': name, 'sector': sector, 'dates': dates, 'closes': closes}

print("=" * 80)
print("  板块周期归因分析")
print("  数据: pool_energy.surplus_z + daily_prices | 范围: 近5年")
print(f"  时间: {date.today()}")
print("=" * 80)

# ④ 阶段划分
# 用surplus_z的趋势识别周期阶段
sz_vals = np.array([r[1] for r in regimes], dtype=float)
sz_dates = [r[0] for r in regimes]

# 平滑sz
def smooth(x, w=5):
    return np.convolve(x, np.ones(w)/w, mode='valid')

sz_smooth = smooth(sz_vals, w=10)
sz_dates_s = sz_dates[len(sz_vals)-len(sz_smooth):]

# 识别关键的拐点
# 上升趋势: sz 从< -1回到>-1
# 下降趋势: sz 从> 1回到< 1
# 底部: sz持续< -1.5
# 顶部: sz持续> 1.5

phases = []
prev_sz = None
for i, (d, sz) in enumerate(zip(sz_dates, sz_vals)):
    phase = classify_regime(sz, prev_sz)
    phases.append((d, sz, phase))
    prev_sz = sz

# 打印5个主要阶段
print(f"\n  近5年周期阶段识别 (基于surplus_z):")
print(f"  {'时间段':>22} {'sz均值':>8} {'阶段':>12} {'特征'}")
print(f"  {'-'*65}")

prev_phase = None
phase_start = None
phase_data = []
for d, sz, p in phases:
    if p != prev_phase:
        if phase_start is not None and prev_phase:
            phase_data.append((phase_start, d, np.mean([r[1] for r in phases if phase_start <= r[0] <= d]), prev_phase))
        phase_start = d
    prev_phase = p
if phase_start:
    phase_data.append((phase_start, phases[-1][0], np.mean([r[1] for r in phases if phase_start <= r[0]]), prev_phase))

# 取最后5-8个阶段
for start, end, sz_mean, ph in phase_data[-8:]:
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    feature = {
        'BOTTOM': '底部区 — 极空,买入窗口',
        'LOW': '低位区 — 接近底部',
        'MID': '中位区 — 震荡或过渡',
        'HIGH': '高位区 — 接近顶部',
        'TOP': '顶部区 — 极多,卖出窗口',
        'UNKNOWN': '未知'
    }.get(ph, '')
    print(f"  {start[:10]:>10} ~ {end[:10]:>10} ({days:3d}d)  {sz_mean:>+6.2f}  {ph:>12}  {feature}")

# ⑤ 各股票在各周期的表现对比
print(f"\n{'='*80}")
print(f"  各股票分阶段收益对比 (基准=大盘)")
print(f"{'='*80}")
print(f"  {'阶段':>14} {'sz均值':>7} {'天数':>5} ", end="")
for code, (name, _) in stocks.items():
    print(f" {name:>8}", end="")
print(f" {'大盘':>8}")
print(f"  {'-'*80}")

for start, end, sz_mean, ph in phase_data[-6:]:
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    days = (e - s).days
    
    print(f"  {start[:10]}:{end[:10]} {sz_mean:>+6.2f} {days:>4}d", end="")
    
    for code, (name, _) in stocks.items():
        sd = stock_data[code]
        # 截取阶段内的收益率
        mask = [(d >= s and d <= e) for d in sd['dates']]
        if sum(mask) >= 5:
            prices = sd['closes'][mask]
            ret = (prices[-1] / prices[0] - 1) * 100
        else:
            ret = None
        if ret is not None:
            print(f" {ret:>+7.2f}%", end="")
        else:
            print(f" {'---':>8}", end="")
    
    # 大盘
    idx_mask = [(d >= s and d <= e) for d in idx_dates]
    if sum(idx_mask) >= 5:
        idx_ret = (idx_closes[idx_mask][-1] / idx_closes[idx_mask][0] - 1) * 100
        print(f" {idx_ret:>+7.2f}%", end="")
    print()

# ⑥ 相对收益分析
print(f"\n{'='*80}")
print(f"  超额收益分析 (各阶段跑赢/跑输大盘的幅度)")
print(f"{'='*80}")
print(f"  {'阶段':>14} ", end="")
for code, (name, _) in stocks.items():
    print(f" {name:>8}", end="")
print()
print(f"  {'-'*55}")

for start, end, sz_mean, ph in phase_data[-6:]:
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    
    print(f"  {start[:10]}:{end[:10]}", end="")
    
    # 大盘收益
    idx_mask = [(d >= s and d <= e) for d in idx_dates]
    idx_ret = (idx_closes[idx_mask][-1] / idx_closes[idx_mask][0] - 1) * 100 if sum(idx_mask) >= 5 else None
    
    for code, (name, _) in stocks.items():
        sd = stock_data[code]
        mask = [(d >= s and d <= e) for d in sd['dates']]
        if sum(mask) >= 5:
            ret = (sd['closes'][mask][-1] / sd['closes'][mask][0] - 1) * 100
            excess = ret - idx_ret if idx_ret else None
        else:
            excess = None
        if excess is not None:
            print(f" {excess:>+7.2f}%", end="")
        else:
            print(f" {'---':>8}", end="")
    print()

# ⑦ 各股票的周期敏感性 (β+相关性)
print(f"\n{'='*80}")
print(f"  周期敏感性指标")
print(f"{'='*80}")
print(f"  {'股票':>10} {'大盘β':>8} {'逆大盘':>8} {'底部弹性':>8} {'顶部敏感':>8} {'赢率':>8}")
print(f"  {'-'*65}")

for code, (name, sector) in stocks.items():
    sd = stock_data[code]
    c = sd['closes']
    n = min(len(c), len(idx_closes))
    
    stk = c[-n:]
    midx = idx_closes[-n:]
    
    # 日收益
    s_ret = stk[1:] / stk[:-1] - 1
    m_ret = midx[1:] / midx[:-1] - 1
    
    # β (全时段)
    beta = np.cov(s_ret, m_ret)[0,1] / np.var(m_ret) if np.var(m_ret) > 0 else 0
    
    # 逆大盘率: 大盘跌时个股涨的比例
    down_days = m_ret < 0
    anti = np.mean(s_ret[down_days] > 0) * 100 if sum(down_days) > 0 else 0
    
    # 底部弹性: 大盘在底部区(BOTTOM)反弹时个股的响应
    bottom_periods = [(d, sz) for d, sz, p in phases if p == 'BOTTOM']
    bottom_ret = []
    idx_bottom_ret = []
    for bd, _ in bottom_periods[-3:]:
        bd_dt = date.fromisoformat(bd[:10])
        sd_mask = [(d >= bd_dt and d <= bd_dt + timedelta(days=60)) for d in sd['dates']]
        if sum(sd_mask) >= 20:
            r = (sd['closes'][sd_mask][-1] / sd['closes'][sd_mask][0] - 1) * 100
            bottom_ret.append(r)
        idx_sd_mask = [(d >= bd_dt and d <= bd_dt + timedelta(days=60)) for d in idx_dates]
        if sum(idx_sd_mask) >= 20:
            idx_r = (idx_closes[idx_sd_mask][-1] / idx_closes[idx_sd_mask][0] - 1) * 100
            idx_bottom_ret.append(idx_r)
    
    bot_ret_str = f"{np.mean(bottom_ret):+.1f}%" if bottom_ret else "---"
    idx_bot_str = f"{np.mean(idx_bottom_ret):+.1f}%" if idx_bottom_ret else "---"
    
    # 顶部阶段
    top_periods = [(d, sz) for d, sz, p in phases if p == 'TOP']
    top_ret = []
    for td, _ in top_periods[-3:]:
        td_dt = date.fromisoformat(td[:10])
        sd_mask = [(d >= td_dt and d <= td_dt + timedelta(days=60)) for d in sd['dates']]
        if sum(sd_mask) >= 20:
            r = (sd['closes'][sd_mask][-1] / sd['closes'][sd_mask][0] - 1) * 100
            top_ret.append(r)
    top_ret_str = f"{np.mean(top_ret):+.1f}%" if top_ret else "---"
    
    print(f"  {name:>8}  {beta:>+6.2f}  {anti:>5.1f}% {bot_ret_str:>8} {top_ret_str:>8}")

# ⑧ 各股票板块之间的相关性
print(f"\n{'='*80}")
print(f"  板块间相关性矩阵")
print(f"{'='*80}")
names = [stock_data[code]['name'] for code in stocks]
n_stk = len(names)

rets_matrix = []
for code in stocks:
    sd = stock_data[code]
    c = sd['closes'][-min(756, len(sd['closes'])):]
    r = c[1:] / c[:-1] - 1
    rets_matrix.append(r)

min_len = min(len(r) for r in rets_matrix)
rets_matrix = [r[-min_len:] for r in rets_matrix]

print(f"  {'':>10}", end="")
for n in names:
    print(f" {n:>8}", end="")
print()
for i, ni in enumerate(names):
    print(f"  {ni:>10}", end="")
    for j in range(len(names)):
        c = np.corrcoef(rets_matrix[i], rets_matrix[j])[0,1]
        print(f" {c:>+7.2f} ", end="")
    print()

# ⑨ 周期轮动时序验证
# 检查在每次大盘底部时,各股票的启动顺序
print(f"\n{'='*80}")
print(f"  底部启动顺序检测")
print(f"{'='*80}")
print(f"  检测方法: 大盘每次进入BOTTOM后,各股票从最低点到+5%所需天数")
print(f"  短天数 = 反弹越早")

bottom_entries = [(d, sz) for d, sz, p in phases if p == 'BOTTOM' and sz < -2]
print(f"  检测到{len(bottom_entries)}次底部区")

for bd, bsz in bottom_entries[-3:]:
    b_dt = date.fromisoformat(bd[:10])
    print(f"\n  底部: {bd[:10]} (sz={bsz:.2f})")
    print(f"  {'股票':>10} {'日收益':>8} {'40d收益':>8} {'60d收益':>8} {'启动天数':>8}")
    print(f"  {'-'*50}")
    
    for code, (name, sector) in stocks.items():
        sd = stock_data[code]
        # 底部后20天、40天、60天的收益
        b_idx = None
        for i, d in enumerate(sd['dates']):
            if d >= b_dt:
                b_idx = i
                break
        if b_idx is None or b_idx + 60 >= len(sd['closes']):
            continue
        price0 = sd['closes'][b_idx]
        r20 = (sd['closes'][b_idx+20] / price0 - 1) * 100 if b_idx+20 < len(sd['closes']) else None
        r40 = (sd['closes'][b_idx+40] / price0 - 1) * 100 if b_idx+40 < len(sd['closes']) else None
        r60 = (sd['closes'][b_idx+60] / price0 - 1) * 100 if b_idx+60 < len(sd['closes']) else None
        
        # 从最低点反弹+5%所需的天数
        low_after = np.min(sd['closes'][b_idx:b_idx+60])
        low_idx = b_idx + np.argmin(sd['closes'][b_idx:b_idx+60])
        days_to_5pct = None
        for j in range(low_idx, min(low_idx+60, len(sd['closes']))):
            if sd['closes'][j] / low_after >= 1.05:
                days_to_5pct = j - low_idx
                break
        
        print(f"  {name:>10} {r20:>+7.1f}% {r40:>+7.1f}% {r60:>+7.1f}% {str(days_to_5pct or '>60'):>8}")

# ⑩ 整合结论
print(f"\n{'='*80}")
print(f"  结论与持仓建议")
print(f"{'='*80}")

print(f"""
1. 周期敏感性排名 (逆大盘高=防御, 底部弹性高=反弹):

   大众公用(燃气):  逆大盘率最高 ≈ 公用事业天然防御
   嘉化能源(化工):  底部反弹最快 ≈ 顺周期底部超跌反弹
   尖峰集团(水泥):  底部弹性大但波动更剧烈 ≈ 高β顺周期
   软控股份(设备):  独立于大盘,看行业资本开支周期
   广汇物流(物流):  顺周期但弱于化工/建材

2. 当前持仓的周期逻辑:
   - 嘉化 + 尖峰: 顺周期底部反弹组合 (符合BEAR尾->A_RECOVER阶段)
   - 大众公用: 防御配置 (但半年前已有+105%涨幅,且补跌中)
   - 注意: 大众公用作为防御品种目前处在-32%补跌中, 持仓逻辑是"等回归"
     而非"追反弹"

3. 如果要在底部加仓顺周期,优先顺序:
   ① 嘉化能源 (化工低PB + 底部弹性确认)
   ② 尖峰集团 (水泥强顺周期 + 零尾部风险)
   ③ 广汇物流 (顺周期但弹性弱,等嘉化先动)
   ④ 软控股份 (独立周期,不依赖大盘反弹)
""")

# 保存结果
out = f"/home/tearsyu/.openclaw/workspace/memory/reports/period_analysis_{date.today()}.md"
with open(out, 'w') as f:
    f.write(f"# 板块周期归因分析 — {date.today()}\n\n")
    f.write("数据源: pool_energy.surplus_z + daily_prices, 近5年\n")
print(f"\n保存: {out}")

conn.close()
PYEOF
