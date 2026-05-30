#!/usr/bin/env python3
"""V8 Layer 0：基本面排雷过滤器 v2

基于回测验证，最终规则：
- 主过滤：最新季报净利 > 0（比纯V8 T+20提升+1.87%, 胜率+4.0%）
- 4Q累计净利覆盖太少，改用最新单季
- 预警标记（不剔除）：ROE过低、营收下滑

缺失（待爬虫平台）：
- 商誉/审计意见/立案调查/经营现金流
"""
import psycopg2
import sys

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

def get_v8_signals():
    """获取V8候选信号"""
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    cur.execute("""
        SELECT code, stock_name, industry, density,
               round(sma_dev::numeric*100,2) as sma_pct,
               round(pb::numeric,3) as pb,
               round(close::numeric,2) as close_price,
               round(sma120::numeric,2) as sma120
        FROM mv_v8_candidates
        WHERE sma_dev < -0.2 AND pb < 1.0
        ORDER BY 
            CASE WHEN density='RARE' THEN 0 ELSE 1 END,
            sma_dev
    """)
    signals = [
        {
            'code': r[0], 'name': r[1] or '', 'industry': r[2],
            'density': r[3], 'sma_pct': float(r[4]), 'pb': float(r[5]),
            'price': float(r[6]), 'sma120': float(r[7]),
        }
        for r in cur.fetchall()
    ]
    conn.close()
    return signals


def get_newest_financial(codes):
    """获取每只股票最新一期的财务数据"""
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    placeholders = ','.join(['%s'] * len(codes))
    cur.execute(f"""
        SELECT DISTINCT ON (f.symbol)
            f.symbol,
            f.end_date,
            f.net_profit,
            f.revenue,
            f.roe,
            f.bvps,
            f.eps,
            f.revenue_yoy,
            f.net_profit_yoy,
            f.quarter_type
        FROM financial_data f
        WHERE f.symbol IN ({placeholders})
        ORDER BY f.symbol, f.end_date DESC
    """, codes)
    
    fin = {}
    for r in cur.fetchall():
        fin[r[0]] = {
            'end_date': str(r[1])[:10],
            'net_profit': float(r[2]) if r[2] else None,
            'revenue': float(r[3]) if r[3] else None,
            'roe': float(r[4]) if r[4] else None,
            'bvps': float(r[5]) if r[5] else None,
            'eps': float(r[6]) if r[6] else None,
            'rev_yoy': float(r[7]) if r[7] else None,
            'np_yoy': float(r[8]) if r[8] else None,
            'quarter': r[9],
        }
    
    conn.close()
    return fin


def run_filter(signals):
    """Layer 0 过滤：最新净利 > 0"""
    codes = ['A.' + s['code'] for s in signals]
    fin_data = get_newest_financial(codes)
    # 编译两个索引: 纯6位 → A.前缀
    code_to_key = {k[2:]: k for k in fin_data}  # 'A.000001' → '000001'
    
    # 统计
    has_data = sum(1 for c in codes if c in fin_data)
    print(f'\n财务数据覆盖: {has_data}/{len(codes)}只 ({len(codes)-has_data}只无数据)\n')
    
    passed = []
    rejected_net = []
    unverified = []
    warnings_list = []
    
    for s in signals:
        code = s['code']
        fk = code_to_key.get(code)  # 转成A.前缀的key
        f = fin_data.get(fk)
        
        if f is None:
            unverified.append(s)
            s['layer0'] = {'status': 'UNVERIFIED', 'reason': '无财务数据'}
            continue
        
        # 主过滤：最新净利为负 → 剔除
        if f['net_profit'] is not None and f['net_profit'] < 0:
            rejected_net.append({**s, 'np': f['net_profit']})
            continue
        
        # 通过
        s_warns = []
        if f['roe'] is not None and f['roe'] < 0:
            s_warns.append(f'ROE={f["roe"]:.2f}%（亏损）')
        elif f['roe'] is not None and f['roe'] < 3:
            s_warns.append(f'ROE={f["roe"]:.2f}%（盈利微薄）')
        if f['rev_yoy'] is not None and f['rev_yoy'] < -30:
            s_warns.append(f'营收同比{f["rev_yoy"]:+.1f}%（大幅下滑）')
        if f['bvps'] is not None and f['bvps'] < 0:
            s_warns.append(f'BVPS={f["bvps"]:.2f}（净资产为负！）')
            # BVPS为负且净利>0 → 极端情况，但信号仍保留
        if f['net_profit'] is not None and f['net_profit'] < 1e7:
            s_warns.append(f'净利仅{f["net_profit"]/1e4:.0f}万（微利）')
        
        s['layer0'] = {
            'status': 'PASS',
            'roe': f['roe'],
            'net_profit': f['net_profit'],
            'revenue': f['revenue'],
            'bvps': f['bvps'],
            'warnings': s_warns,
        }
        passed.append(s)
    
    return passed, rejected_net, unverified, warnings_list


def print_results(passed, rejected, unverified, warnings_list):
    print('=' * 72)
    print('  V8 Layer 0 · 基本面排雷报告')
    print('  规则: 最新季报净利 > 0')
    print('=' * 72)
    
    print(f'\n🔴 剔除: 净利为负')
    print(f'  共 {len(rejected)} 只')
    if rejected:
        for s in sorted(rejected, key=lambda x: abs(x.get('np', 0)), reverse=True)[:12]:
            np_val = abs(s['np'])
            np_str = f'{np_val/1e8:.1f}亿' if abs(np_val) >= 1e8 else f'{np_val/1e4:.0f}万'
            print(f'    {s["code"]:>8} {s["name"]:<12} {s["industry"]:<16} '
                  f'SMA={s["sma_pct"]:+.2f}% PB={s["pb"]:.2f} 亏损{np_str}')
    
    print(f'\n⚠️ 未验证（无财务数据）')
    print(f'  共 {len(unverified)} 只')
    if unverified:
        for s in unverified[:5]:
            print(f'    {s["code"]:>8} {s["name"]:<12} {s["industry"]:<16} 密度={s["density"]}')
    
    print(f'\n✅ Layer 0 通过: {len(passed)} 只')
    rare = [s for s in passed if s['density'] == 'RARE']
    normal = [s for s in passed if s['density'] == 'NORMAL']
    dense = [s for s in passed if s['density'] == 'DENSE']
    print(f'  RARE: {len(rare)}只 (Wt=0.9)')
    print(f'  NORMAL: {len(normal)}只 (Wt=0.6)')
    print(f'  DENSE: {len(dense)}只 (需PB<0.5)')
    
    # 核心猎场
    print(f'\n🎯 核心猎场 TOP 15（RARE优先 + SMA深度）')
    print(f'  {"#":>3} {"代码":>8} {"名称":<12} {"行业":<16} {"密度":<6} {"SMA%":>8} {"PB":>6} {"Wt":>4} {"预警":<25}')
    print(f'  ' + '-' * 85)
    
    top = sorted(passed, key=lambda s: (0 if s['density'] == 'RARE' else 1, s['sma_pct']))
    for i, s in enumerate(top[:15], 1):
        wt = 0.9 if s['density'] == 'RARE' else 0.6
        warns = '; '.join(s['layer0'].get('warnings', []))[:25]
        print(f'  {i:3d}. {s["code"]:>8} {s["name"]:<12} {s["industry"]:<16} '
              f'{s["density"]:<6} {s["sma_pct"]:>+7.2f}% {s["pb"]:>5.2f} {wt:>3.1f} {warns:<25}')
    
    # DENSE需要PB<0.5
    if dense:
        dense_ok = [s for s in dense if s['pb'] < 0.5]
        print(f'\n  其中DENSE有效(PB<0.5): {len(dense_ok)}只')
        for s in dense_ok[:5]:
            print(f'    {s["code"]:>8} {s["name"]:<12} PB={s["pb"]:.2f}')
    
    print(f'\n' + '=' * 72)
    print(f'  🔴 = 净利为负剔除  ✅ = 通过  ⚠️ = 数据不足')
    print(f'  回测验证：V8+净利>0  T+20=+9.96% 胜率71.3%')
    print(f'  对比纯V8：T+20=+8.09% 胜率67.3%')
    print('=' * 72)


def main():
    print('V8 Layer 0 v2: 最新季报净利 > 0 ...')
    signals = get_v8_signals()
    print(f'V8原始信号: {len(signals)}只')
    
    passed, rejected, unverified, warns = run_filter(signals)
    print_results(passed, rejected, unverified, warns)
    
    return passed, rejected


if __name__ == '__main__':
    passed, rejected = main()
