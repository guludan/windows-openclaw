#!/usr/bin/env python3
"""
V8 仪表盘 v3 — MD格式输出 + 推荐列表
"""
from __future__ import print_function
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
from strategies.v8_strategy import V8Strategy, PARAMS_MATRIX
import psycopg2, numpy as np
from datetime import date, datetime, timedelta
from collections import Counter, defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
STABILITY = 2

def scan(target_date=None):
    if target_date is None:
        target_date = date.today()
    
    conn = psycopg2.connect(DB)
    conn.autocommit = True
    cur = conn.cursor()
    
    def load_stock_info():
        sig = {}
        try:
            cur.execute("SELECT code, stock_name, industry, density FROM mv_v8_candidates")
            for code, name, ind, dens in cur.fetchall():
                sig[code] = {'name': (name or '').strip(), 'industry': ind, 'density': dens}
        except Exception:
            try:
                cur.execute("""
                    SELECT s.stock_code, s.stock_name, 
                           COALESCE(s.industry, ic.industry, 'N/A'),
                           COALESCE(ic.bk_density_level, 'NORMAL')
                    FROM stock_industry s
                    LEFT JOIN industry_components_ths_l1 ic ON s.stock_code=ic.stock_code
                """)
                for code, name, ind, dens in cur.fetchall():
                    sig[code] = {'name': (name or '').strip(), 'industry': ind, 'density': dens}
            except:
                sig = {}
        return sig
    stock_info = load_stock_info()
    
    cur.execute("SELECT MAX(trade_date) FROM daily_prices")
    latest_data = cur.fetchone()[0]
    if latest_data and latest_data < target_date:
        target_date = latest_data
    
    out = []
    def w(s=''): out.append(str(s))
    
    today_str = target_date.strftime('%Y-%m-%d')
    
    w(f'---')
    w(f'title: V8 共振策略 · 每日仪表盘')
    w(f'date: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    w(f'data_date: {today_str}')
    w(f'---')
    w()
    w(f'# V8 共振策略 · 每日仪表盘')
    w(f'')
    
    # ==================== [1] 市场状态 ====================
    w(f'## 1. 市场状态')
    
    strategy = V8Strategy()
    regime_map = strategy.load_regime_series()
    
    recent_dates = sorted([d for d in regime_map if d <= target_date])
    if len(recent_dates) >= STABILITY:
        last_n_states = [regime_map[d] for d in recent_dates[-STABILITY:]]
        stable = len(set(last_n_states)) == 1
        regime = last_n_states[-1]
    else:
        stable = True
        regime = regime_map.get(target_date, 'SIDEWAYS')
    
    params = PARAMS_MATRIX.get(regime, PARAMS_MATRIX['SIDEWAYS'])
    stable_mark = '' if stable else ' ⚠️ 不稳定'
    state_seq = [(d, regime_map.get(d, 'N/A')) for d in recent_dates[-10:]]
    
    w(f'- **Regime**: {regime}{stable_mark}')
    w(f'- **描述**: {params["desc"]}')
    w(f'- **连续{STABILITY}天**: {last_n_states}')
    w()
    w(f'| 日期 | 状态 |')
    w(f'|------|------|')
    for d, s in state_seq:
        mk = ' ◀' if d == target_date else ''
        w(f'| {d} | {s}{mk} |')
    w()
    
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
    
    w(f'- **surplus_z**: {sz[-1]:+.3f} — {level}')
    w(f'- **均线**: 5日={z5:+.3f}  20日={z20:+.3f} — {cross}')
    w(f'- **加速度**: {accel:+.3f} — {omens}')
    
    if sz[-1] > -2.0:
        gap = sz[-1] + 2.0
        w(f'- 距极空(-2.0): {gap:.3f}（再跌{gap:.1f}个std → BOTTOM_WARNING）')
    else:
        w(f'- 已触发极空区域 (sz={sz[-1]:+.3f})')
    
    if accel > 0 and sz[-1] < -1.0 and z5 < z20:
        w(f'- 🔍 死叉中加速度转正 → 潜在底背离前兆')
    w()
    
    cur.execute("SELECT date, phase FROM pool_energy WHERE level='market' ORDER BY date DESC LIMIT 5")
    w(f'| 日期 | Phase |')
    w(f'|------|-------|')
    for d, p in reversed(cur.fetchall()):
        w(f'| {d} | {p} |')
    w()
    
    cur.execute("SELECT phase FROM pool_energy WHERE level='market' AND date=%s", (target_date,))
    ph = cur.fetchone()
    today_phase = ph[0] if ph else 'NEUTRAL'
    p_entry = params.get(today_phase)
    
    w(f'- **当前Phase**: {today_phase}')
    if p_entry is None:
        w(f'- ⛔ {today_phase} 在 {regime} 下已关闭')
    else:
        w(f'- **入场参数**: SMA < {p_entry["sma"]:+3.0%} | PB < {p_entry["pb"]:.1f} | 权重 = {p_entry["w"]:.1f}')
        w(f'- **行业规则**: RARE→SMA×0.8 | DENSE→PB<0.5')
    w()
    
    # ==================== [2] 全市场扫描 ====================
    w(f'## 2. 全市场扫描')
    
    cur.execute("""
        SELECT code, stock_name, industry, density, close, sma120, pb, sma_dev, bvps, real_pb
        FROM mv_v8_candidates
        WHERE trade_date = %s AND sma120 > 0 AND close > 0
        ORDER BY code
    """, (target_date,))
    mc_rows = cur.fetchall()
    if not mc_rows:
        cur.execute("SELECT MAX(trade_date) FROM mv_v8_candidates")
        latest_td = cur.fetchone()[0]
        cur.execute("""
            SELECT code, stock_name, industry, density, close, sma120, pb, sma_dev, bvps, real_pb
            FROM mv_v8_candidates
            WHERE trade_date = %s AND sma120 > 0 AND close > 0
            ORDER BY code
        """, (latest_td,))
        mc_rows = cur.fetchall()
        cur.execute("SELECT MAX(trade_date) FROM daily_prices")
        dt = cur.fetchone()[0]
        if dt > target_date:
            target_date = dt
        w(f'- ⚠️ 物化视图日期: {latest_td}，行情日期: {dt}')
    else:
        w(f'- ✅ 物化视图数据已就绪')
    cur.execute("SELECT MAX(trade_date) FROM daily_prices")
    dt = cur.fetchone()[0]
    if dt and dt > target_date:
        target_date = dt
    
    # 停牌除权
    xr_cache = {}
    stale_cache = set()
    
    cur.execute("""
        SELECT substring(d.symbol,3) as code FROM daily_prices d
        WHERE d.trade_date >= %s - 10 AND d.trade_date <= %s
        GROUP BY d.symbol
        HAVING MIN(volume) = 0 AND MAX(volume) = 0
    """, (target_date, target_date))
    for r in cur.fetchall():
        stale_cache.add(r[0])
    
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
    
    sma_stats = {'<0': 0, '<-5%': 0, '<-10%': 0, '<-15%': 0, '<-20%': 0}
    pb_stats = {'<1.0': 0, '<0.8': 0, '<0.5': 0}
    near_signals = []
    v8_signals = []
    industry_underwater = Counter()
    industry_total = Counter()
    scanned = 0
    
    for row in mc_rows:
        code, name, industry, density, close, sma120, pb, sma_dev, bvps, real_pb = row
        raw_pb = real_pb if real_pb is not None and real_pb > 0 else pb
        
        if code in stale_cache:
            continue
        
        xr = xr_cache.get(code)
        if xr and xr['days'] < 20:
            continue
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
        
        if -0.20 <= sma_dev <= -0.10 and 0.7 <= raw_pb <= 1.0:
            near_signals.append((code, ind_name, density, sma_dev, raw_pb, close, sma120))
        
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
                    pass
                elif raw_pb < t_pb:
                    if is_xr_mid:
                        t1 = sma120 * 0.95
                        t2 = sma120
                    else:
                        t1 = sma120 * 0.95
                        t2 = sma120
                    sl = close * 0.95
                    wgt_rated = wgt
                    # 深跌+放量增强: SMA<-20% 且 量比>1.5 → 仓位+0.1, 评分+10
                    vol_boost = 0
                    if sma_dev < -0.20:
                        try:
                            cur.execute("""
                                SELECT v / NULLIF(AVG(v) OVER(ORDER BY trade_date ROWS BETWEEN 40 PRECEDING AND 1 PRECEDING), 1) as vr
                                FROM (SELECT volume as v FROM daily_prices WHERE symbol=%s AND trade_date<=%s AND close>0 ORDER BY trade_date DESC LIMIT 41) sub
                                LIMIT 1
                            """, (('A.'+code, target_date)))
                            vr_r = cur.fetchone()
                            if vr_r and vr_r[0] and vr_r[0] > 1.5:
                                vol_boost = 10
                                wgt_rated = min(1.0, wgt + 0.1)
                        except:
                            pass
                    v8_signals.append((code, ind_name, density, sma_dev, raw_pb, close, sma120, t1, t2, sl, wgt_rated, vol_boost))
    
    conn.close()
    
    w(f'- **排除**: 停牌{len(stale_cache)}只，严重除权{xr_dirty}只，中度除权{xr_med}只')
    w(f'- **有效扫描**: {scanned:,}只')
    w()
    
    w(f'### SMA超跌分布')
    w(f'| 阈值 | 数量 | 占比 |')
    w(f'|------|------|------|')
    for k in ['<0', '<-5%', '<-10%', '<-15%', '<-20%']:
        pct = sma_stats[k]/max(scanned,1)*100
        bar = '█' * int(sma_stats[k] / max(scanned/20, 1))
        w(f'| {k:>8} | {sma_stats[k]:,} | {pct:.1f}% {bar} |')
    w()
    
    w(f'### PB破净分布')
    w(f'| 阈值 | 数量 | 占比 |')
    w(f'|------|------|------|')
    for k in ['<1.0', '<0.8', '<0.5']:
        pct = pb_stats[k]/max(scanned,1)*100
        bar = '█' * int(pb_stats[k] / max(pb_stats.get('<1.0',1)/20, 1))
        w(f'| {k:>8} | {pb_stats[k]:,} | {pct:.1f}% {bar} |')
    w()
    
    # 接近触发
    w(f'### 接近触发 (SMA -10%~-20% + PB 0.7~1.0) — {len(near_signals)}只')
    if near_signals:
        w(f'| 代码 | 名称 | 行业 | 密度 | SMA偏离 | PB | 现价 | SMA120 | 距P1 |')
        w(f'|------|------|------|------|---------|----|------|--------|------|')
        for ns in sorted(near_signals, key=lambda x: x[3])[:20]:
            code, ind, dens, sd, rpb, px, sma = ns
            si = stock_info.get(code, {})
            name = si.get('name', '') or ''
            industry = si.get('industry', '') or ind
            gap_to_p1 = (px - sma*0.95) / px
            dens_s = dens[:4] if dens else ''
            w(f'| {code} | {name} | {industry[:8]} | {dens_s} | {sd:+.2%} | {rpb:.2f} | {px:.2f} | {sma:.2f} | {gap_to_p1:+.2%} |')
        if len(near_signals) > 20:
            w(f'\n*...还有{len(near_signals)-20}只*')
    w()
    
    # ==================== V8信号 ====================
    w(f'## 3. V8共振信号 — {len(v8_signals)}个')
    if v8_signals:
        v8_signals.sort(key=lambda x: x[3])
        w(f'| # | 代码 | 名称 | 行业 | 密度 | SMA偏离 | PB | 现价 | 放量🔥 | P1 | P2 | 止损 | 权重 |')
        w(f'|---|------|------|------|------|---------|----|------|--------|----|----|------|------|')
        for i, s in enumerate(v8_signals):
            code, ind, dens, sd, rpb, px, sma, t1, t2, sl, wgt, vol_boost = s
            si = stock_info.get(code, {})
            name = si.get('name', '') or ''
            industry = si.get('industry', '') or ind
            dens_s = dens[:4] if dens else ''
            pb_str = f'{rpb:.2f}' if rpb < 0.5 else f'{rpb:.2f}'
            vol_tag = '🔥' if vol_boost > 0 else ''
            w(f'| {i+1} | {code} | {name} | {industry[:8]} | {dens_s} | {sd:+.2%} | {pb_str} | {px:.2f} | {vol_tag:>6} | {t1:.2f} | {t2:.2f} | {sl:.2f} | {wgt:.1f} |')
    w()
    
    # ==================== [3] 行业破净密度 ====================
    w(f'## 4. 行业破净密度 TOP 10')
    
    ind_pcts = [(ind, industry_underwater[ind], industry_total[ind], industry_underwater[ind]/industry_total[ind]*100)
                for ind in industry_underwater if industry_total[ind] >= 5]
    ind_pcts.sort(key=lambda x: x[3], reverse=True)
    
    w(f'| 行业 | 破净率 | 比例 |')
    w(f'|------|--------|------|')
    for ind, u, t, pct in ind_pcts[:10]:
        w(f'| {ind} | {pct:.1f}% | {u}/{t} |')
    w()
    
    w(f'### 重资产行业破净详情')
    heavy_kw = ['银行', '钢铁', '房地产', '建筑', '建材', '煤炭', '有色', '石油', '公用']
    w(f'| 行业 | 破净率 | 比例 |')
    w(f'|------|--------|------|')
    for ind, u, t, pct in ind_pcts:
        if any(kw in ind for kw in heavy_kw):
            w(f'| {ind} | {pct:.1f}% | {u}/{t} |')
    w()
    
    # ==================== [4] 历史参考 ====================
    w(f'## 5. 历史参考')
    
    hist_ref = {
        '2022-10': 'BEAR极空, sz<-2.5, 触发BOTTOM_WARNING → V底反弹+22% (86%胜率)',
        '2023-05': 'BEAR/SIDEWAYS边界, 信号56条/年, 全年-3.66%',
        '2024-01': 'BEAR, sz=-2.65, 底背离 → 924行情V底反弹',
        '2025-11': 'BEAR, sz→-2.80, 类似当前结构, 信号279条',
    }
    
    match_reasons = []
    for period, desc in hist_ref.items():
        matches = []
        if regime in desc: matches.append('regime匹配')
        if '底背离' in desc and omens == '⬆底背离': matches.append('底背离')
        match_str = f'（{"，".join(matches)}）' if matches else ''
        w(f'- **{period}**: {desc} {match_str}')
    w()
    
    # ==================== [5] 操作建议 ====================
    w(f'## 6. 操作建议')
    
    if v8_signals:
        w(f'✅ **今日V8信号 {len(v8_signals)}个** — 可操作')
        w(f'- 入场: 现价')
        w(f'- 止盈1/2: SMA-5%')
        w(f'- 清仓: SMA')
        w(f'- 止损: -5%')
    else:
        w(f'⏸ 今日无V8信号，建议观望')
        n_near = len(near_signals)
        if sma_stats['<-15%'] == 0 and sma_stats['<-10%'] == 0:
            w('- 全市场无超跌标的，条件过严')
        elif pb_stats['<1.0'] == 0:
            w('- 全市场无破净标的')
        elif sma_stats['<-20%'] > 0 and pb_stats['<1.0'] > 0:
            w(f'- 超跌({sma_stats["<-20%"]:,}只)与破净({pb_stats["<1.0"]:,}只)未交叉')
        else:
            w('- 超跌与破净条件未同时满足')
        w()
        w('- **触发V8的三种路径**:')
        w('  - A) 继续跌→sz<-2.0+accel>0.5→BOTTOM_WARNING→极空入场')
        w('  - B) 反弹→金叉→SIDEWAYS→回归基线参数')
        w('  - C) 局部崩盘→某板块暴跌+破净→个别信号')
        if n_near > 0:
            w(f'- 📋 关注清单: {n_near}只接近触发（再跌5-10%可能进入V8范围）')
    w()
    
    # ====== 推荐列表 ======
    w(f'---')
    w(f'## 优先推荐 (评分排序)')
    
    if v8_signals:
        # 用cost_structure评分排序
        recs = []
        for s in v8_signals:
            code, ind, dens, sd, rpb, px, sma, t1, t2, sl, wgt, vol_boost = s
            si = stock_info.get(code, {})
            name = si.get('name', '') or ''
            industry = si.get('industry', '') or ind
            
            # 综合评分
            score = 60
            # SMA偏离越深越好
            if sd < -0.20: score += 15
            elif sd < -0.15: score += 10
            elif sd < -0.10: score += 5
            # PB越低越好
            if rpb is not None and rpb < 0.6: score += 10
            elif rpb is not None and rpb < 0.8: score += 5
            # DENSE行业减分（弱势反弹弱）
            if dens == 'DENSE': score -= 10
            elif dens == 'RARE': score += 10
            # 低价优先（回撤可控）
            if px < 3: score += 5
            elif px < 5: score += 3
            elif px > 30: score -= 5
            # 权重加成
            if wgt >= 1.0: score += 10
            elif wgt >= 0.7: score += 5
            # 深跌+放量增强 (已验证 T+20 +7.25%, 66%胜率)
            score += vol_boost
            
            recs.append((score, code, name, industry, sd, rpb, px, sma, t1, t2, sl, wgt))
        
        recs.sort(reverse=True)
        
        w(f'| 评分 | 代码 | 名称 | 行业 | SMA偏离 | PB | 现价 | P1 | P2 | 止损 | 权重 |')
        w(f'|------|------|------|------|---------|----|------|----|----|------|------|')
        for r in recs[:20]:
            score, code, name, industry, sd, rpb, px, sma, t1, t2, sl, wgt = r
            pb_str = f'{rpb:.2f}' if rpb is not None else 'n/a'
            w(f'| **{score}** | {code} | {name} | {industry[:8]} | {sd:+.2%} | {pb_str} | {px:.2f} | {t1:.2f} | {t2:.2f} | {sl:.2f} | {wgt:.1f} |')
    else:
        w(f'*今日无V8共振信号，无法生成推荐*')
    w()
    
    w(f'---')
    w(f'*V8策略提醒：管住手就是赚钱。狙击手的特性是大部分时间在等待。*')
    
    report = '\n'.join(out)
    
    # 保存MD
    md_path = f'/mnt/d/data/a-stock/reports/{today_str}/v8_dashboard_{today_str}.md'
    with open(md_path, 'w') as f:
        f.write(report)
    
    # 也保存txt版供终端查看（去掉头部front matter）
    txt_path = f'/home/tearsyu/.openclaw/workspace/memory/reports/v8_dashboard_{today_str}.txt'
    with open(txt_path, 'w') as f:
        f.write(report.replace('---\n', '', 2))
    
    print(report)
    print(f'\n📄 MD: {md_path}')
    print(f'📄 TXT: {txt_path}')
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
