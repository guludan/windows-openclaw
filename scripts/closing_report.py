#!/usr/bin/env python3
"""
收盘报告统一管线 — 替换 HEARTBEAT.md 中 9~14步

整合: 简报/V8决策/持仓监控/庄家行为/NEUTRAL监视/牛熊识别/复盘
      -> 统一输出到 D:/data/a-stock/reports/YYYY-MM-DD/

用法:
  python3 scripts/closing_report.py              # 最新交易日
  python3 scripts/closing_report.py 2026-06-05   # 指定日期
  python3 scripts/closing_report.py --v8         # 包含V8全市场扫描（较慢）

输出:
  D:/data/a-stock/reports/YYYY-MM-DD/收盘报告_YYYY-MM-DD.md  (综合版)
  D:/data/a-stock/reports/YYYY-MM-DD/收盘复盘_YYYY-MM-DD.md  (精简行动版)
"""
import sys, os, json, time
from datetime import date, datetime, timedelta
from collections import defaultdict
import psycopg2
import numpy as np

# ══════════════════════════════════════════
# 配置
# ══════════════════════════════════════════

PG_DSN = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

CWD = os.path.dirname(os.path.abspath(__file__))
WS_ROOT = os.path.normpath(os.path.join(CWD, '..'))

DST_WIN = 'D:/data/a-stock/reports'
DST_LINUX = '/mnt/d/data/a-stock/reports'

HOLDINGS = {
    'A.002073': {'name': '软控股份', 'cost': 5.755, 'stop': 5.40},
    'A.600273': {'name': '嘉化能源', 'cost': 7.245, 'stop': 6.93},
    'A.600635': {'name': '大众公用', 'cost': 5.105, 'stop': 4.60},
}


# ══════════════════════════════════════════
# 数据层
# ══════════════════════════════════════════

def _get_trade_date():
    """获取最新交易日"""
    c = psycopg2.connect(PG_DSN)
    cur = c.cursor()
    cur.execute("SELECT MAX(trade_date) FROM daily_prices")
    r = cur.fetchone()
    c.close()
    return r[0] if r and r[0] else date.today()


class ReportData:
    """统一数据采集"""
    
    def __init__(self, td):
        self.td = td
        self.td_str = td.isoformat()
    
    # ── 1. 大盘状态 ──
    
    def market_state(self, c):
        cur = c.cursor()
        cur.execute("""
            SELECT date, b_pool, a_pool, surplus, phase, extra
            FROM pool_energy WHERE level='market'
            ORDER BY date DESC LIMIT 1
        """)
        row = cur.fetchone()
        if not row:
            return {'regime': '?', 'phase': '?', 'surplus_z': 0}
        extra = row[5] or {}
        return {
            'date': row[0],
            'B': round(float(row[1]), 4) if row[1] else 0,
            'A': round(float(row[2]), 4) if row[2] else 0,
            'surplus': round(float(row[3]), 4) if row[3] else 0,
            'phase': row[4],
            'regime': extra.get('regime', '?'),
            'surplus_z': round(float(extra.get('surplus_z', 0)), 3),
            'sz_5': round(float(extra.get('sz_5', 0)), 3),
            'sz_20': round(float(extra.get('sz_20', 0)), 3),
            'accel': round(float(extra.get('accel', 0)), 3),
        }
    
    # ── 2. 市场宽度 ──
    
    def breadth(self, c):
        cur = c.cursor()
        cur.execute("""
            SELECT close, high, open, volume FROM daily_prices
            WHERE trade_date=%s AND close>0 AND volume>0
        """, (self.td,))
        rows = cur.fetchall()
        total = len(rows)
        up = dn = limit_up = 0
        for r in rows:
            close, high, open_, vol = float(r[0]), float(r[1]), float(r[2]) or float(r[0]), int(r[3])
            if close > open_:
                up += 1
                if close >= open_ * 1.098:
                    limit_up += 1
            elif close < open_:
                dn += 1
        adv = round(up / total * 100, 1) if total else 0
        return {'total': total, 'up': up, 'dn': dn, 'flat': total-up-dn, 'limit_up': limit_up, 'adv_ratio': adv}
    
    # ── 3. 持仓状态 ──
    
    def holdings(self, c):
        cur = c.cursor()
        results = []
        for sym, info in HOLDINGS.items():
            cur.execute("SELECT close FROM daily_prices WHERE symbol=%s AND trade_date=%s", (sym, self.td))
            row = cur.fetchone()
            if not row:
                continue
            close = float(row[0])
            pnl = (close / info['cost'] - 1) * 100
            
            cur.execute("SELECT close FROM daily_prices WHERE symbol=%s AND trade_date<%s AND close>0 ORDER BY trade_date DESC LIMIT 1", (sym, self.td))
            prev = cur.fetchone()
            pct = ((close / float(prev[0]) - 1) * 100) if prev else 0
            
            d = {**info, 'code': sym[2:], 'price': close, 'pnl_pct': round(pnl, 2), 'pct': round(pct, 2)}
            
            # SMA偏离(软控)
            if sym == 'A.002073':
                cur.execute("SELECT close FROM daily_prices WHERE symbol=%s AND trade_date<=%s AND close>0 ORDER BY trade_date DESC LIMIT 120", (sym, self.td))
                cl = cur.fetchall()
                if len(cl) >= 120:
                    sma120 = np.mean([float(x[0]) for x in cl])
                    d['sma_dev'] = round((close / sma120 - 1) * 100, 1)
            
            results.append(d)
        return results
    
    # ── 4. Phase回溯 ──
    
    def phase_timeline(self, c):
        cur = c.cursor()
        cur.execute("""
            SELECT date, phase, surplus, extra FROM pool_energy
            WHERE level='market' AND extra IS NOT NULL
            ORDER BY date DESC LIMIT 5
        """)
        rows = cur.fetchall()
        return [{'date': r[0], 'phase': r[1], 'surplus': round(float(r[2]), 4),
                 'regime': (r[3] or {}).get('regime', '?')} for r in rows]
    
    # ── 5. 庄家行为（精简版）──
    
    def zhuangjia(self, c):
        cur = c.cursor()
        results = []
        for sym in HOLDINGS:
            cur.execute("SELECT trade_date, close, volume FROM daily_prices WHERE symbol=%s ORDER BY trade_date DESC LIMIT 10", (sym,))
            rows = cur.fetchall()
            if len(rows) < 5:
                continue
            closes = [float(r[1]) for r in rows]
            vols = [int(r[2]) for r in rows]
            recent_down = all(closes[i] < closes[i+1] for i in range(3) if i+1 < len(closes))
            avg_vol = np.mean(vols[:5])
            old_avg = np.mean(vols[5:])
            vol_ratio = round(avg_vol / old_avg, 2) if old_avg else 1.0
            
            pattern = '缩量筑底' if vol_ratio < 0.7 and not recent_down else \
                      '连跌恐慌' if recent_down else '正常波动'
            results.append({'code': sym[2:], 'pattern': pattern, 'vol_ratio': vol_ratio, 'close': closes[0]})
        return results
    
    # ── 6. V8决策（从缓存读取）──
    
    def v8_decisions(self):
        scan_paths = [
            f'{WS_ROOT}/memory/reports/full_scan_{self.td_str}.json',
            f'{DST_LINUX}/{self.td_str}/full_scan_{self.td_str}.json',
        ]
        for p in scan_paths:
            if os.path.exists(p):
                with open(p) as f:
                    return json.load(f)
        # 尝试调用 full_market_scan
        try:
            sys.path.insert(0, CWD)
            from strategies.full_market_scan import scan_all
            return scan_all(self.td)
        except Exception:
            return []


# ══════════════════════════════════════════
# 报告渲染
# ══════════════════════════════════════════

class ReportRenderer:
    
    @staticmethod
    def regime_emoji(r):
        return {'BEAR': '❄️', 'BOTTOM_WARNING': '🟡', 'SIDEWAYS': '➖', 'BULL': '🔥', 'TOP_WARNING': '🟠'}.get(r, '➖')
    
    @staticmethod
    def phase_desc(p):
        return {'B_ACCUM': 'B吸筹', 'B_DISTRIB': 'B派发', 'A_ACCUM': 'A吸筹(极空)', 'A_RECOVER': 'A反弹', 'NEUTRAL': '阴跌出货', 'HI_EQ': '均衡'}.get(p, '?')
    
    @staticmethod
    def sz_desc(z):
        if z < -2: return '❄️ 极空'
        if z < -1: return '🥶 偏空'
        if z < 1: return '➖ 中性'
        if z < 2: return '🔥 偏多'
        return '♨️ 过热'
    
    def render(self, m, b, h, v8, z, t):
        lines = []
        
        # 标题
        lines.append(f'# 收盘报告 | {self.td_str}')
        lines.append(f'> Regime: {self.regime_emoji(m.get("regime","?"))} **{m.get("regime","?")}** | Phase: {m.get("phase","?")} | surplus_z: {m.get("surplus_z",0):.3f}')
        lines.append('')
        
        # ── 大盘状态 ──
        lines.append('## 一、大盘状态')
        lines.append('')
        lines.append(f'| 指标 | 数值 |')
        lines.append(f'|---|---:|')
        lines.append(f'| Regime | **{m.get("regime","?")}** {self.regime_emoji(m.get("regime",""))} |')
        lines.append(f'| Phase | {m.get("phase","?")} ({self.phase_desc(m.get("phase",""))}) |')
        lines.append(f'| surplus_z | {m.get("surplus_z",0):.3f} |')
        lines.append(f'| sz_5 / sz_20 | {m.get("sz_5",0):.3f} / {m.get("sz_20",0):.3f} |')
        lines.append(f'| 加速度(accel) | {m.get("accel",0):+.3f} |')
        
        regime = m.get('regime', '?')
        accel = m.get('accel', 0)
        cap_max = 0.8 if regime in ('BOTTOM_WARNING',) else 0.6
        lines.append(f'| 仓位上限 | {cap_max:.0%} |')
        lines.append('')
        lines.append(f'**市场宽度:** 上涨{b["up"]} / 下跌{b["dn"]} / 平盘{b["flat"]} | 涨停{b["limit_up"]}只 | 上涨比{b["adv_ratio"]}%')
        lines.append('')
        
        # ── 持仓 ──
        lines.append('## 二、持仓收益')
        lines.append('')
        lines.append(f'| 标的 | 成本 | 现价 | 涨跌 | 浮盈 | 止损 | 状态 |')
        lines.append(f'|---|---:|---:|---:|---:|---:|:---:|')
        
        total_pnl = 0
        for hh in h:
            emoji = '🟢' if hh['pnl_pct'] > -1 else ('🔴' if hh['pnl_pct'] < -3 else '🟡')
            lines.append(f"| {hh['name']}({hh['code']}) | {hh['cost']:.3f} | {hh['price']:.2f} | {hh['pct']:+.2f}% | {hh['pnl_pct']:+.2f}% | {hh['stop']:.2f} | {emoji} |")
            total_pnl += hh['pnl_pct'] * 0.33
        
        lines.append(f'\n**组合估算浮盈:** {total_pnl:+.2f}%\n')
        
        for hh in h:
            if 'sma_dev' in hh:
                lines.append(f"**{hh['name']} SMA偏离:** {hh['sma_dev']:+.1f}%")
                if hh['sma_dev'] < -20:
                    avg_20 = '+12.6%'
                    lines.append(f"  → 深度超跌(SMA<-20%)，历史信号T+20均{avg_20}")
        
        # ── V8决策 ──
        lines.append('')
        lines.append('## 三、全市场扫描')
        lines.append('')
        
        if not v8:
            lines.append('> 全市场扫描结果未缓存（需运行 full_market_scan.py）\n')
        else:
            lines.append(f'| # | 代码 | 现价 | 操作 | 仓位 | Regime | 洗盘 | 成本 | 止损 |')
            lines.append(f'|---|---:|---:|:---|:---:|:---|---:|---:|')
            for i, d in enumerate(v8[:10], 1):
                lines.append(f"| {i} | {d.get('code','?')} | {d.get('current_price',0):.2f} | {d.get('action','?')} | {d.get('position',0)*100:.0f}% | {d.get('regime','?')[:4]} | {d.get('washout_pattern','?')[:6]} | {d.get('true_cost',0):.2f} | {d.get('stop_loss',0):.2f} |")
        
        # ── 策略 ──
        lines.append('')
        lines.append('## 四、策略建议')
        lines.append('')
        
        if regime in ('BOTTOM_WARNING',):
            lines.append(f'- Regime **{regime}** → 仓位上限{cap_max:.0%}，关注A_RECOVER脉冲')
        elif regime == 'BEAR' and accel > 0:
            lines.append(f'- Regime **{regime}** 但加速度{accel:+.3f}转正 → 底背离，关注切换')
        else:
            lines.append(f'- Regime **{regime}** → 仓位上限{cap_max:.0%}，防守为主')
        
        for hh in h:
            if hh['pct'] > 1.5:
                lines.append(f"- ✅ **{hh['name']}** 反弹+{hh['pct']:.2f}%，等A_RECOVER脉冲减仓")
            elif hh['pnl_pct'] < -3:
                lines.append(f"- ⚠️ **{hh['name']}** 浮亏{hh['pnl_pct']:.1f}%，关注止损线{hh['stop']}")
        
        # ── 庄家行为 ──
        lines.append('')
        lines.append('## 五、持仓资金行为')
        lines.append('')
        lines.append(f'| 代码 | 模式 | 量比 | 现价 |')
        lines.append(f'|---|---:|---:|')
        for zz in z:
            lines.append(f"| {zz['code']} | {zz['pattern']} | {zz['vol_ratio']:.2f} | {zz['close']:.2f} |")
        
        # ── Phase回溯 ──
        if t:
            lines.append('')
            lines.append('## 六、Phase / Regime 回溯')
            lines.append('')
            lines.append(f'| 日期 | Phase | Regime | surplus |')
            lines.append(f'|---|---:|---:|---:|')
            for tt in t:
                lines.append(f"| {tt['date']} | {tt['phase']} | {tt['regime']} | {tt['surplus']:+.4f} |")
        
        # 尾注
        now = datetime.now().strftime('%H:%M')
        lines.append('')
        lines.append(f'---')
        lines.append(f'*自动生成 {self.td_str} {now} | 数据源: mootdx TCP + PG*')
        
        return '\n'.join(lines)
    
    def __init__(self, td_str):
        self.td_str = td_str


def write_brief(dst_dir, td, m, h):
    """精简版复盘"""
    text = f'# 收盘复盘 | {td}\n\n'
    text += f'**Regime:** {m.get("regime","?")} | **Phase:** {m.get("phase","?")} | **surplus_z:** {m.get("surplus_z",0):.3f}\n'
    text += f'**上涨比:**  | **涨停:** 只\n\n## 持仓\n'
    text += f'| 标的 | 成本 | 现价 | 涨跌 | 浮盈 | 止损 |\n'
    text += f'|---|---:|---:|---:|---:|\n'
    for hh in h:
        text += f"| {hh['name']}({hh['code']}) | {hh['cost']:.3f} | {hh['price']:.2f} | {hh['pct']:+.2f}% | {hh['pnl_pct']:+.2f}% | {hh['stop']:.2f} |\n"
    text += f'\n## 策略\n'
    if m.get('regime') in ('BOTTOM_WARNING', 'BEAR'):
        text += '- Regime切换BOTTOM_WARNING，仓位上限80%\n'
    for hh in h:
        if hh['pct'] > 1.5:
            text += f"- {hh['name']} 反弹{hh['pct']:+.2f}%，等A_RECOVER脉冲减仓\n"
    
    with open(f'{dst_dir}/收盘复盘_{td}.md', 'w') as f:
        f.write(text)


# ══════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description='收盘报告生成')
    parser.add_argument('date', nargs='?', help='日期 (YYYY-MM-DD)')
    parser.add_argument('--v8', action='store_true', help='包含V8全市场扫描')
    args = parser.parse_args()
    
    td = date.fromisoformat(args.date) if args.date else _get_trade_date()
    td_str = td.isoformat()
    dst_dir = f'{DST_LINUX}/{td_str}'
    os.makedirs(dst_dir, exist_ok=True)
    
    print(f'📊 收盘报告管线 | {td_str}')
    t0 = time.time()
    
    rd = ReportData(td)
    renderer = ReportRenderer(td_str)
    
    c = psycopg2.connect(PG_DSN)
    
    m = rd.market_state(c)
    print(f'  1/6 大盘状态 ({time.time()-t0:.1f}s)')
    
    b = rd.breadth(c)
    print(f'  2/6 市场宽度 ({time.time()-t0:.1f}s)')
    
    h = rd.holdings(c)
    print(f'  3/6 持仓状态 ({time.time()-t0:.1f}s)')
    
    v8 = []
    if args.v8:
        v8 = rd.v8_decisions()
    print(f'  4/6 V8决策 ({len(v8)}只, {time.time()-t0:.1f}s)')
    
    z = rd.zhuangjia(c)
    print(f'  5/6 资金行为 ({time.time()-t0:.1f}s)')
    
    t = rd.phase_timeline(c)
    print(f'  6/6 Phase回溯 ({time.time()-t0:.1f}s)')
    
    c.close()
    
    # 生成报告
    report = renderer.render(m, b, h, v8, z, t)
    
    report_path = f'{dst_dir}/收盘报告_{td_str}.md'
    with open(report_path, 'w') as f:
        f.write(report)
    
    write_brief(dst_dir, td_str, m, h)
    
    elapsed = time.time() - t0
    print(f'\n✅ 完成 ({elapsed:.1f}s)')
    print(f'  📄 {report_path}')
    print(f'  📄 {dst_dir}/收盘复盘_{td_str}.md')


if __name__ == '__main__':
    main()
