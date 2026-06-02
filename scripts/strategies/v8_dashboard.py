#!/usr/bin/env python3
"""
V8 仪表盘 v2 — 全量扫描，稳定输出
"""
from __future__ import print_function
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
from strategies.v8_strategy import V8Strategy, PARAMS_MATRIX
import psycopg2, numpy as np
from datetime import date, datetime, timedelta
from collections import Counter, defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
STABILITY = 2  # 连续N天同一状态才确认

def scan(target_date=None):
    if target_date is None:
        target_date = date.today()
    
    conn = psycopg2.connect(DB)
    conn.autocommit = True
    cur = conn.cursor()
    
    # 加载行业和名称映射
    def load_stock_info():
        sig = {}  # code -> {name, industry, density}
        try:
            cur.execute("""
                SELECT code, stock_name, industry, density
                FROM mv_v8_candidates
                UNION
                SELECT s.stock_code, s.stock_name, 
                       COALESCE(s.industry, ic.industry, 'N/A'),
                       COALESCE(ic.bk_density_level, s.industry_detail, 'NORMAL')
                FROM stock_industry s
                LEFT JOIN industry_components_ths_l1 ic ON s.stock_code=ic.stock_code
                ON CONFLICT DO NOTHING
            """)
            for code, name, ind, dens in cur.fetchall():
                sig[code] = {'name': (name or '').strip(), 'industry': ind, 'density': dens}
        except Exception:
            # fallback: 从物化视图
            try:
                cur.execute("SELECT code, stock_name, industry, density FROM mv_v8_candidates")
                for code, name, ind, dens in cur.fetchall():
                    sig[code] = {'name': (name or '').strip(), 'industry': ind, 'density': dens}
            except:
                sig = {}
        return sig
    stock_info = load_stock_info()
    
    # 数据完整性检查
    cur.execute("SELECT MAX(trade_date) FROM daily_prices")
    latest_data = cur.fetchone()[0]
    if latest_data and latest_data < target_date:
        target_date = latest_data
    
    out = []
    def w(s=''): out.append(str(s))
    
    today_str = target_date.strftime('%Y-%m-%d')
    w(f'╔══════════════════════════════════════════════════════════════════════════╗')
    w(f'║            V8 共振策略 · 每日仪表盘                                    ║')
    w(f'║  {datetime.now().strftime("%Y-%m-%d %H:%M")}  |  数据日期: {today_str}  ║')
    w(f'╚══════════════════════════════════════════════════════════════════════════╝')
    w()
    
    # ==================== [1] 市场状态 ====================
    w('┌── [1] 市场状态（带稳定性检查）')
    
    strategy = V8Strategy()
    regime_map = strategy.load_regime_series()
    
    # 稳定性检查
    recent_dates = sorted([d for d in regime_map if d <= target_date])
    if len(recent_dates) >= STABILITY:
        last_n_states = [regime_map[d] for d in recent_dates[-STABILITY:]]
        stable = len(set(last_n_states)) == 1
        regime = last_n_states[-1]
    else:
        stable = True
        regime = regime_map.get(target_date, 'SIDEWAYS')
    
    params = PARAMS_MATRIX.get(regime, PARAMS_MATRIX['SIDEWAYS'])
    stable_mark = '' if stable else ' ⚠️ 状态不稳定'
    
    # 近10天状态序列
    state_seq = [(d, regime_map.get(d, 'N/A')) for d in recent_dates[-10:]]
    
    w(f'├  Regime: {regime:16s}  {params["desc"]}{stable_mark}')
    w(f'├  最近{STABILITY}天状态: {last_n_states}')
    w(f'├  状态序列:')
    for d, s in state_seq:
        mk = ' ◀' if d == target_date else ''
        w(f'├    {d}: {s}{mk}')
    
    # surplus_z
    cur.execute("SELECT date, surplus FROM pool_energy WHERE level='market' ORDER BY date DESC LIMIT 60")
    rows = [r for r in reversed(cur.fetchall())]
    surplus = np.array([float(r[1]) for r in rows], dtype=float)
    n_sz = len(surplus)
    
    sz_mu, sz_sd = np.mean(surplus), np.std(surplus)
    sz = (surplus - sz_mu) / sz_sd if sz_sd > 0 else surplus
    z5 = np.mean(sz[-5:]) if n_sz >= 5 else 0
    z20 = np.mean(sz[-20:]) if n_sz >= 20 else 0
    accel = sz[-1] - 2*sz[-2] + sz[-3] if n_sz >= 3 else 0
    
    cross = '金叉↑' if z5 > z20 else '死叉↓'
    level = '🔥过热' if sz[-1] > 2 else '❄极空' if sz[-1] < -2 else '▬正常'
    omens = '⬆底背离' if accel > 0.5 and sz[-1] < -1 else '⬇顶背离' if accel < -0.5 and sz[-1] > 1 else '▬中性'
    
    w(f'├')
    w(f'├  surplus_z: {sz[-1]:+.3f}  {level}')
    w(f'├  5日均值={z5:+.3f}  20日均值={z20:+.3f}  {cross}')
    w(f'├  加速度: {accel:+.3f}  {omens}')
    
    # 极空预警
    if sz[-1] > -2.0:
        gap = sz[-1] + 2.0
        w(f'├  📡 距极空(-2.0): {gap:.3f} (再跌{gap:.1f}个std → BOTTOM_WARNING)')
    else:
        w(f'├  📡 已触发极空区域 (sz={sz[-1]:+.3f})')
    
    if accel > 0 and sz[-1] < -1.0 and z5 < z20:
        w(f'├  🔍 死叉中加速度转正 → 潜在底背离前兆')
    
    # Phase
    cur.execute("SELECT date, phase FROM pool_energy WHERE level='market' ORDER BY date DESC LIMIT 5")
    w(f'├')
    w(f'├  最近Phase:')
    for d, p in reversed(cur.fetchall()):
        w(f'├    {d}: {p}')
    
    cur.execute("SELECT phase FROM pool_energy WHERE level='market' AND date=%s", (target_date,))
    ph = cur.fetchone()
    today_phase = ph[0] if ph else 'NEUTRAL'
    p_entry = params.get(today_phase)
    
    w(f'├')
    w(f'├  Phase: {today_phase}')
    if p_entry is None:
        w(f'├  ⛔ {today_phase}在{regime}下已关闭')
    else:
        w(f'├  🔫 SMA<{p_entry["sma"]:+3.0%} | PB<{p_entry["pb"]:.1f} | W={p_entry["w"]:.1f}')
        w(f'├  📋 行业: RARE→SMA×0.8 | DENSE→PB<0.5')
    
    # ==================== [2] 全市场扫描（物化视图提速版）====================
    w(f'├')
    w(f'┌── [2] 全市场扫描（物化视图 mv_v8_candidates）')
    
    # 从物化视图读取（物化视图数据预先生成，不用在仪表盘里刷新）
    # 如果当日数据不存在，尝试从latest获取
    cur.execute("""
        SELECT code, stock_name, industry, density, close, sma120, pb, sma_dev, bvps, real_pb
        FROM mv_v8_candidates
        WHERE trade_date = %s AND sma120 > 0 AND close > 0
        ORDER BY code
    """, (target_date,))
    mc_rows = cur.fetchall()
    if not mc_rows:
        # 回退：读最新的日期
        cur.execute("SELECT MAX(trade_date) FROM mv_v8_candidates")
        latest_td = cur.fetchone()[0]
        cur.execute("""
            SELECT code, stock_name, industry, density, close, sma120, pb, sma_dev, bvps, real_pb
            FROM mv_v8_candidates
            WHERE trade_date = %s AND sma120 > 0 AND close > 0
            ORDER BY code
        """, (latest_td,))
        mc_rows = cur.fetchall()
        # 用daily_prices的最新日期做target_date
        cur.execute("SELECT MAX(trade_date) FROM daily_prices")
        dt = cur.fetchone()[0]
        if dt > target_date:
            target_date = dt
        w(f'├  ⚠️ 当日({target_date})无物化视图, 用{latest_td}（市值数据最新为{dt}）')
    else:
        w(f'├  物化视图数据已就绪')
    cur.execute("SELECT MAX(trade_date) FROM daily_prices")
    dt = cur.fetchone()[0]
    if dt and dt > target_date:
        target_date = dt
    
    # ====== 停牌 & 除权过滤预加载 ======
    xr_cache = {}  # code -> {'ex_gap':.., 'ex_days':..} 或 None
    stale_cache = set()
    
    w(f'├')
    # 检查停牌（连续5天零成交最近5天内）
    cur.execute("""
        SELECT substring(d.symbol,3) as code FROM daily_prices d
        WHERE d.trade_date >= %s - 10 AND d.trade_date <= %s
        GROUP BY d.symbol
        HAVING MIN(volume) = 0 AND MAX(volume) = 0
    """, (target_date, target_date))
    for r in cur.fetchall():
        stale_cache.add(r[0])
    w(f'├  停牌过滤: {len(stale_cache)}只停牌')
    
    # 除权检测：查近期gap<-5%的交易日
    cur.execute("""
        SELECT x.code, x.ex_date, x.gap, 
               %s - x.ex_date as days_since
        FROM (
            SELECT substring(d.symbol,3) as code,
                   d.trade_date as ex_date,
                   (d.open / NULLIF(LAG(d.close) OVER (PARTITION BY d.symbol ORDER BY d.trade_date), 0) - 1) as gap
            FROM daily_prices d
            WHERE d.trade_date >= %s - 180 AND d.trade_date <= %s
        ) x
        WHERE x.gap < -0.045
        ORDER BY x.code, x.ex_date
    """, (target_date, target_date, target_date))
    xr_rows = cur.fetchall()
    xr_cache = {}
    for code, ex_d, gap, ds in xr_rows:
        if code not in xr_cache or ds < xr_cache[code]['days']:
            xr_cache[code] = {'date': ex_d.strftime('%m-%d'), 'gap': gap, 'days': ds}
    
    xr_dirty = sum(1 for x in xr_cache.values() if x['days'] < 20)
    xr_med = sum(1 for x in xr_cache.values() if x['days'] < 60)
    w(f'├  除权检测: {xr_dirty}只严重(<20d), {xr_med}只中度(<60d)')
    
    # 建立联合行业&密度：物化视图已包含stock_industry + ths数据
    # 但物化视图不包含pool_energy的phase/signals信息，需要补
    
    sma_stats = {'<0': 0, '<-5%': 0, '<-10%': 0, '<-15%': 0, '<-20%': 0}
    pb_stats = {'<1.0': 0, '<0.8': 0, '<0.5': 0}
    near_signals = []
    v8_signals = []
    industry_underwater = Counter()
    industry_total = Counter()
    scanned = 0
    
    for row in mc_rows:
        code, name, industry, density, close, sma120, pb, sma_dev, bvps, real_pb = row
        
        # 使用真实PB替代伪PB
        raw_pb = real_pb if real_pb is not None and real_pb > 0 else pb
        
        # 停牌过滤
        if code in stale_cache:
            continue
        
        # 除权污染过滤：严重(<20天)完全跳过，中度(<60天)跳过V8筛选
        xr = xr_cache.get(code)
        if xr and xr['days'] < 20:
            continue  # 严重除权污染，跳过
        is_xr_mid = xr and xr['days'] < 60
        
        scanned += 1
        
        if sma_dev < 0: sma_stats['<0'] += 1
        if sma_dev < -0.05: sma_stats['<-5%'] += 1
        if sma_dev < -0.10: sma_stats['<-10%'] += 1
        if sma_dev < -0.15: sma_stats['<-15%'] += 1
        if sma_dev < -0.20: sma_stats['<-20%'] += 1
        if raw_pb < 1.0: pb_stats['<1.0'] += 1
        if raw_pb < 0.8: pb_stats['<0.8'] += 1
        if raw_pb < 0.5: pb_stats['<0.5'] += 1
        
        ind_name = industry
        
        industry_total[ind_name] += 1
        if raw_pb < 1.0:
            industry_underwater[ind_name] += 1
        
        # 接近触发
        if -0.20 <= sma_dev <= -0.10 and 0.7 <= raw_pb <= 1.0:
            near_signals.append((code, ind_name, density, sma_dev, raw_pb, close, sma120))
        
        # V8实际信号
        matrix = strategy.get_params(regime, today_phase)
        if matrix is not None:
            t_sma = matrix['sma']
            t_pb = matrix['pb']
            wgt = matrix['w']
            if density == 'RARE':
                t_sma *= 0.8
                wgt *= 1.5
            elif density == 'DENSE':
                wgt *= 0.6
            
            if sma_dev < t_sma:
                if density == 'DENSE' and raw_pb >= 0.5:
                    pass  # DENSE标的只在PB<0.5时才纳入
                elif raw_pb < t_pb:
                    # 除权中度污染的用prev_close*0.95/1.0替代P1/P2
                    if is_xr_mid:
                        t1 = sma120 * 0.95
                        t2 = sma120
                    else:
                        t1 = sma120 * 0.95
                        t2 = sma120
                    sl = close * 0.95
                    v8_signals.append((code, ind_name, density, sma_dev, raw_pb, close, sma120, t1, t2, sl, wgt))
    
    conn.close()
    
    w(f'├  扫描完成: {scanned:,}只')
    w(f'├  ── SMA超跌分布 ──')
    for k in ['<0', '<-5%', '<-10%', '<-15%', '<-20%']:
        bar = '█' * int(sma_stats[k] / max(scanned/20, 1))
        w(f'├  {k:>8}: {sma_stats[k]:,}只 {bar}')
    w(f'├  ── PB破净分布 ──')
    for k in ['<1.0', '<0.8', '<0.5']:
        bar = '█' * int(pb_stats[k] / max(pb_stats.get('<1.0',1)/20, 1))
        w(f'├  {k:>8}: {pb_stats[k]:,}只 {bar}')
    
    # ==================== 接近触发列表 ====================
    w(f'├')
    w(f'├  🔍 接近触发 (SMA-10%~-20% + PB 0.7~1.0): {len(near_signals)}只')
    if near_signals:
        w(f'├  {"代码":>8} {"行业":<14} {"密度":<7} {"SMA%":>7} {"PB":>5} {"现价":>7} {"SMA120":>8}  距P1%')
        w(f'├  {"─"*68}')
        for ns in sorted(near_signals, key=lambda x: x[3])[:20]:
            code, ind, dens, sd, rpb, px, sma = ns
            si = stock_info.get(code, {})
            name = si.get('name', '') or ''
            industry = si.get('industry', '') or ind
            gap_to_p1 = (px - sma*0.95) / px
            w(f'├  {code:>8} {name:<12} {industry:<14} {dens:<7} {sd:>+6.2%} {rpb:>5.2f} {px:>7.2f} {sma:>8.2f}  {gap_to_p1:>+6.2%}')
        if len(near_signals) > 20:
            w(f'├  ... 还有{len(near_signals)-20}只')
    
    # ==================== V8信号 ====================
    w(f'├')
    w(f'├  🎯 V8共振信号: {len(v8_signals)}个')
    if v8_signals:
        v8_signals.sort(key=lambda x: x[3])
        w(f'├  {"#":<4}{"代码":>8} {"名称":<12} {"行业":<14} {"密度":<7} {"SMA%":>7} {"PB":>5} {"现价":>7} {"P1":>8} {"P2":>8} {"止损":>7} {"Wt":>4}')
        w(f'├  {"─"*100}')
        for i, s in enumerate(v8_signals):
            code, ind, dens, sd, rpb, px, sma, t1, t2, sl, wgt = s
            si = stock_info.get(code, {})
            name = si.get('name', '') or ''
            industry = si.get('industry', '') or ind
            if rpb >= 0.5:
                w(f'├  {i+1:<4}{code:>8} {name:<12} {industry:<14} {dens:<7} {sd:>+6.2%} {rpb:>5.2f} {px:>7.2f} {t1:>8.2f} {t2:>8.2f} {sl:>7.2f} {wgt:>.1f}')
            else:
                w(f'├  {i+1:<4}{code:>8} {name:<12} {industry:<14} {dens:<7} {sd:>+6.2%}  n/a  {px:>7.2f} {t1:>8.2f} {t2:>8.2f} {sl:>7.2f} {wgt:>.1f}')
    
    # ==================== 行业破净密度 ====================
    w(f'├')
    w(f'┌── [3] 行业破净密度 TOP 10')
    
    ind_pcts = [(ind, industry_underwater[ind], industry_total[ind], industry_underwater[ind]/industry_total[ind]*100)
                for ind in industry_underwater if industry_total[ind] >= 5]
    ind_pcts.sort(key=lambda x: x[3], reverse=True)
    
    for ind, u, t, pct in ind_pcts[:10]:
        bar = '█' * int(pct/10) + '░' * (10 - min(int(pct/10), 10))
        w(f'├  {ind:<14} {bar} {pct:5.1f}% ({u}/{t})')
    
    # 重资产行业重点
    heavy_kw = ['银行', '钢铁', '房地产', '建筑', '建材', '煤炭', '有色', '石油', '公用']
    w(f'├')
    w(f'├  重资产行业破净详情:')
    for ind, u, t, pct in ind_pcts:
        if any(kw in ind for kw in heavy_kw):
            bar = '█' * int(pct/10) + '░' * (10 - min(int(pct/10), 10))
            w(f'├  {ind:<14} {bar} {pct:5.1f}% ({u}/{t})')
    
    # ==================== 历史参考 ====================
    w(f'├')
    w(f'┌── [4] 历史参考')
    
    hist_ref = {
        '2022-10': 'BEAR极空, sz<-2.5, 触发BOTTOM_WARNING → V底反弹+22% (86%胜率)',
        '2023-05': 'BEAR/SIDEWAYS边界, 信号56条/年, 全年-3.66%',
        '2024-01': 'BEAR, sz=-2.65, 底背离 → 924行情V底反弹',
        '2025-11': 'BEAR, sz→-2.80, 类似当前结构, 信号279条',
    }
    
    w(f'├  当前: {regime}+{today_phase}+{cross}')
    w(f'├  相似历史:')
    for period, desc in hist_ref.items():
        sim = ''
        if regime in desc: sim = ' 🔺'
        elif '底背离' in desc and omens == '⬆底背离': sim = ' 🔺'
        w(f'├    {period}: {desc}{sim}')
    
    # ==================== 操作建议 ====================
    w(f'├')
    w(f'┌── [5] 操作建议')
    
    if v8_signals:
        w(f'├  ✅ 今日有V8信号 ({len(v8_signals)}个)')
        w(f'├  策略: P1(SMA-5%)卖1/2, P2(SMA)清仓, -5%止损')
    else:
        w(f'├  ⏸ 今日无V8信号，建议观望')
        w(f'├')
        
        n_near = len(near_signals)
        if sma_stats['<-15%'] == 0 and sma_stats['<-10%'] == 0:
            w(f'├  全市场无超跌标的，条件过严')
        elif pb_stats['<1.0'] == 0:
            w(f'├  全市场无破净标的')
        elif sma_stats['<-20%'] > 0 and pb_stats['<1.0'] > 0:
            w(f'├  超跌({sma_stats["<-20%"]:,}只)与破净({pb_stats["<1.0"]:,}只)未交叉')
        else:
            w(f'├  超跌(PB够低的超跌不够，超跌的没破净)')
        
        w(f'├')
        w(f'├  触发V8的三种路径:')
        w(f'├  A) 继续跌→sz<-2.0+accel>0.5→BOTTOM_WARNING→极空入场')
        w(f'├  B) 反弹→金叉→SIDEWAYS→回归基线参数')
        w(f'├  C) 局部崩盘→某板块暴跌+破净→个别信号')
        
        if n_near > 0:
            w(f'├')
            w(f'├  📋 关注清单: {n_near}只接近触发 (再跌5-10%可能进入V8范围)')
    
    w('└──')
    w()
    w('=' * 72)
    w('V8策略提醒：管住手就是赚钱。狙击手的特性是大部分时间在等待。')
    w('=' * 72)
    
    report = '\n'.join(out)
    report_path = f'/home/tearsyu/.openclaw/workspace/memory/reports/v8_dashboard_{today_str}.txt'
    with open(report_path, 'w') as f:
        f.write(report)
    
    print(report)
    print(f'\n报告: {report_path}')
    return report

if __name__ == '__main__':
    import sys
    target = None
    if len(sys.argv) > 1:
        from datetime import date
        try:
            y,m,d = sys.argv[1].split('-')
            target = date(int(y), int(m), int(d))
        except: pass
    scan(target)
