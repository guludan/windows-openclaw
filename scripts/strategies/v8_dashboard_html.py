#!/usr/bin/env python3
"""V8共振策略 · HTML仪表盘生成器"""
import os
import datetime

def render_html(data):
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    
    signals = data.get('signals', [])
    regime = data.get('regime', 'UNKNOWN')
    surplus_z = data.get('surplus_z', 0)
    phase = data.get('phase', 'NEUTRAL')
    total_scanned = data.get('total_scanned', 0)
    signal_count = len(signals)
    
    # 市场状态颜色
    regime_colors = {'BULL': '#27ae60', 'SIDEWAYS': '#f39c12', 'BEAR': '#e74c3c'}
    rc = regime_colors.get(regime, '#95a5a6')
    
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>V8共振策略 · {now}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,'Segoe UI',Roboto,sans-serif; background:#0d1117; color:#c9d1d9; padding:20px; }}
h1 {{ color:#58a6ff; font-size:22px; margin-bottom:4px; }}
h2 {{ color:#8b949e; font-size:14px; font-weight:400; margin-bottom:20px; }}
h3 {{ color:#58a6ff; font-size:16px; margin:16px 0 8px; }}

.regime-badge {{ display:inline-block; padding:4px 12px; border-radius:12px; font-weight:700; font-size:14px; color:#fff; }}
.regime-BEAR {{ background:#e74c3c; }}
.regime-BULL {{ background:#27ae60; }}
.regime-SIDEWAYS {{ background:#f39c12; }}

.stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; margin:16px 0; }}
.stat-card {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:14px; text-align:center; }}
.stat-card .value {{ font-size:24px; font-weight:700; color:#58a6ff; }}
.stat-card .label {{ font-size:12px; color:#8b949e; margin-top:4px; }}

table {{ width:100%; border-collapse:collapse; margin:8px 0 20px; font-size:13px; }}
th {{ background:#161b22; color:#8b949e; padding:8px 6px; text-align:left; border-bottom:2px solid #30363d; }}
td {{ padding:6px; border-bottom:1px solid #21262d; }}
tr:hover {{ background:#1c2128; }}
.sma-pct {{ font-family:'JetBrains Mono','Consolas',monospace; }}
.negative {{ color:#f85149; }}
.positive {{ color:#3fb950; }}
.dense {{ background:#1f3a2f; color:#7ee787; padding:2px 6px; border-radius:4px; font-size:11px; }}
.rare {{ background:#3d2e00; color:#d29922; padding:2px 6px; border-radius:4px; font-size:11px; }}
.normal {{ background:#21262d; color:#8b949e; padding:2px 6px; border-radius:4px; font-size:11px; }}
.wt-high {{ color:#d29922; font-weight:700; }}
.stop-loss {{ color:#f85149; }}
.footer {{ text-align:center; color:#484f58; font-size:11px; margin-top:30px; border-top:1px solid #21262d; padding-top:16px; }}
</style>
</head>
<body>
<h1>V8 共振策略 · 每日仪表盘</h1>
<h2>{now} | 数据日期: 2026-05-28</h2>

<div class="stats">
  <div class="stat-card">
    <div class="value"><span class="regime-badge regime-{regime}">{regime}</span></div>
    <div class="label">市场状态</div>
  </div>
  <div class="stat-card">
    <div class="value" style="color:{'#f85149' if surplus_z < -1.5 else '#d29922' if surplus_z < 0 else '#3fb950'}">{surplus_z:.3f}</div>
    <div class="label">surplus_z</div>
  </div>
  <div class="stat-card">
    <div class="value">{phase}</div>
    <div class="label">Phase</div>
  </div>
  <div class="stat-card">
    <div class="value" style="color:#f85149">{total_scanned}</div>
    <div class="label">扫描标的</div>
  </div>
  <div class="stat-card">
    <div class="value" style="color:#d29922">{signal_count}</div>
    <div class="label">V8共振信号</div>
  </div>
</div>

<h3>🎯 V8共振信号 (按SMA%升序，深度超跌优先)</h3>
<table>
<thead><tr>
  <th>#</th><th>代码</th><th>名称</th><th>行业</th><th style="text-align:center">密度</th>
  <th style="text-align:right">SMA%</th><th style="text-align:right">PB</th><th style="text-align:right">现价</th>
  <th style="text-align:right">P1目标</th><th style="text-align:right">P2目标</th><th style="text-align:right">止损</th><th style="text-align:center">Wt</th>
</tr></thead>
<tbody>
'''
    
    for i, s in enumerate(signals[:500], 1):
        code = s.get('code', '')
        name = s.get('name', '')
        industry = s.get('industry', 'N/A')
        density = s.get('density', 'NORMAL')
        sma_pct = s.get('sma_pct', 0)
        pb = s.get('pb', 0)
        close = s.get('close', 0)
        p1 = s.get('p1', 0)
        p2 = s.get('p2', 0)
        stop = s.get('stop', 0)
        wt = s.get('wt', 0.6)
        
        den_class = density.lower()
        wt_class = 'wt-high' if wt >= 0.9 else ''
        
        sma_color = 'negative' if sma_pct < 0 else 'positive'
        
        html += f'''<tr>
  <td>{i}</td>
  <td>{code}</td>
  <td>{name}</td>
  <td>{industry}</td>
  <td style="text-align:center"><span class="{den_class}">{density}</span></td>
  <td class="sma-pct {sma_color}" style="text-align:right">{sma_pct:+.2f}%</td>
  <td class="sma-pct" style="text-align:right">{pb:.2f}</td>
  <td class="sma-pct" style="text-align:right">{close:.2f}</td>
  <td class="sma-pct" style="text-align:right">{p1:.2f}</td>
  <td class="sma-pct" style="text-align:right">{p2:.2f}</td>
  <td class="sma-pct stop-loss" style="text-align:right">{stop:.2f}</td>
  <td class="{wt_class}" style="text-align:center">{wt:.1f}</td>
</tr>
'''
    
    html += '''</tbody></table>
<div class="footer">
V8共振策略 · 自动生成 | ⚠ 管住手就是赚钱。狙击手的特性是大部分时间在等待。
</div>
</body></html>'''
    
    return html


def generate(signals, regime='BEAR', surplus_z=-2.054, phase='NEUTRAL', total_scanned=5519):
    data = {
        'signals': signals,
        'regime': regime,
        'surplus_z': surplus_z,
        'phase': phase,
        'total_scanned': total_scanned,
    }
    return render_html(data)


if __name__ == '__main__':
    import psycopg2
    DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # 从物化视图拿V8信号数据
    cur.execute("""
        SELECT code, stock_name, industry, density, 
               sma_dev*100 as sma_pct, pb, close,
               sma120*0.95 as p1, sma120 as p2, sma120*0.95 - close*0.05 as stop_loss,
               CASE WHEN density='RARE' THEN 0.9 ELSE 0.6 END as wt
        FROM mv_v8_candidates
        WHERE sma_dev < -0.2 AND pb < 1.0
        ORDER BY sma_dev
    """)
    rows = cur.fetchall()
    conn.close()
    
    signals = [
        {
            'code': r[0], 'name': r[1] or '', 'industry': r[2],
            'density': r[3], 'sma_pct': float(r[4]), 'pb': float(r[5]),
            'close': float(r[6]), 'p1': float(r[7]), 'p2': float(r[8]),
            'stop': float(r[9]), 'wt': r[10],
        }
        for r in rows
    ]
    
    html = generate(signals)
    path = '/tmp/v8_dashboard_2026-05-28.html'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'✅ HTML仪表盘已生成: {path}')
    print(f'   共 {len(signals)} 个V8信号')
