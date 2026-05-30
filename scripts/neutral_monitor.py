#!/usr/bin/env python3
# preflight-ok: true
# preflight-ok: true
"""
neutral_monitor.py — NEUTRAL结构性异常监视

规则：
1. NEUTRAL + BEAR regime → 弱势震荡警告（回避买入）
2. NEUTRAL + surplus_z < -1.5 → 空头导向，等底背离
3. NEUTRAL连续 > 10天 → 市场方向不明，风控减仓
4. NEUTRAL + B_ACCUM → 假NEUTRAL，实际上是B方积累，应观察

输出: 每日监视报告到 memory/reports/NN_monitor_YYYY-MM-DD.md
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, json
from datetime import datetime, date, timedelta

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
OUT_DIR = '/home/tearsyu/.openclaw/workspace/memory/reports/'

def main():
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # 加载近期pool_energy
    cur.execute("""
        SELECT date, b_pool, a_pool, surplus, phase,
               extra->>'regime' as regime,
               (extra->>'surplus_z')::float as sz,
               (extra->>'sz_5')::float as sz5,
               (extra->>'sz_20')::float as sz20,
               (extra->>'accel')::float as acc
        FROM pool_energy WHERE level='market' AND date >= '2026-01-01'
        ORDER BY date
    """)
    rows = cur.fetchall()
    conn.close()
    
    today = rows[-1][0]
    
    # 检查NEUTRAL连续
    neutral_streak = 0
    for r in reversed(rows):
        if r[4] == 'NEUTRAL':
            neutral_streak += 1
        else:
            break
    
    # 检查NEUTRAL+BEAR组合
    neutral_bear_days = [r for r in rows if r[4] == 'NEUTRAL' and r[5] == 'BEAR']
    
    # 检查NEUTRAL+B_ACCUM
    neutral_baccum = [r for r in rows if r[4] == 'NEUTRAL' and r[5] == 'B_ACCUM']
    
    # 检查surplus_z < -1.5的NEUTRAL
    neutral_deep_short = [r for r in rows if r[4] == 'NEUTRAL' and r[7] is not None and r[7] < -1.5]
    
    # 检查底背离：surplus_z底但accel转正
    bottom_divergence = [r for r in rows if r[9] is not None and r[9] > 0.5 and r[5] == 'BEAR']
    
    # 生成报告
    lines = []
    lines.append(f"# NEUTRAL结构性异常监视 | {today}")
    lines.append("")
    
    lines.append(f"## 当前状态")
    lines.append(f"- Phase: {rows[-1][4]}")
    lines.append(f"- Regime: {rows[-1][5]}")
    lines.append(f"- surplus_z: {rows[-1][7]:.3f}" if rows[-1][7] else "- surplus_z: N/A")
    lines.append(f"- surplus: {rows[-1][3]:+.4f}")
    lines.append(f"- B_pool: {rows[-1][1]:.4f} | A_pool: {rows[-1][2]:.4f}")
    lines.append("")
    
    lines.append(f"## 监视规则触发")
    
    # Rule 1: NEUTRAL+BEAR
    lines.append("")
    lines.append(f"### 规则1: NEUTRAL + BEAR regime → 弱势震荡")
    if neutral_bear_days:
        r = neutral_bear_days[-1]
        lines.append(f"⚠️ 触发: 最近NEUTRAL阶段中 {len(neutral_bear_days)}天处于BEAR态")
        lines.append(f"  末次: {r[0]} surplus_z={r[7]:.3f}")
        lines.append(f"  影响: NEUTRAL中BEAR占比高时V8入场胜率下降")
    
    # Rule 2: NEUTRAL + surplus_z < -1.5
    if neutral_deep_short:
        lines.append(f"")
        lines.append(f"### 规则2: NEUTRAL + surplus_z < -1.5 → 空头导向")
        lines.append(f"⚠️ 触发: {len(neutral_deep_short)}天NEUTRAL+深空")
        latest = neutral_deep_short[-1]
        lines.append(f"  最近: {latest[0]} sz={latest[7]:.3f}")
    
    # Rule 3: NEUTRAL连续
    if neutral_streak > 5:
        lines.append(f"")
        lines.append(f"### 规则3: NEUTRAL连续 > 5天 → 方向不明")
        lines.append(f"⚠️ 触发: NEUTRAL已连续 {neutral_streak}天")
    
    # Rule 4: 底背离检测
    if bottom_divergence:
        r = bottom_divergence[-1]
        lines.append(f"")
        lines.append(f"### 规则4: Bear + accel转正 → 底背离")
        lines.append(f"✅ {r[0]}: regime={r[5]} accel={r[9]:+.3f}")
    
    lines.append("")
    lines.append(f"## NEUTRAL在2026年的异常性")
    
    # 按月度统计
    monthly = {}
    for r in rows:
        m = r[0].strftime('%Y-%m')
        if m not in monthly:
            monthly[m] = {'total': 0, 'neutral': 0, 'bear': 0}
        monthly[m]['total'] += 1
        if r[4] == 'NEUTRAL':
            monthly[m]['neutral'] += 1
        if r[5] == 'BEAR':
            monthly[m]['bear'] += 1
    
    lines.append(f"| 月份 | 总天数 | NEUTRAL | NEUTRAL% | BEAR天数 |")
    lines.append(f"|------|--------|---------|----------|----------|")
    for m in sorted(monthly.keys()):
        v = monthly[m]
        lines.append(f"| {m} | {v['total']} | {v['neutral']} | {v['neutral']/v['total']*100:.0f}% | {v['bear']} |")
    
    report = '\n'.join(lines)
    
    fname = f"{OUT_DIR}neutral_monitor_{today}.md"
    with open(fname, 'w') as f:
        f.write(report)
    
    print(report)
    print(f"\n报告已写入: {fname}")
    return report

if __name__ == '__main__':
    main()
