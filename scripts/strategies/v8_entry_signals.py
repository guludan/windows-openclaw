#!/usr/bin/env python3
"""V8 入场信号 · N型曲线区间规则

基于回测的SMA深度区间判定（非精确目标价）：
- 增强：现价已在-40%~-35%，直接入场0.9
- 常规：-20%~-25%，直接入场0.6
- 跳过：-25%~-35%，胜率骤降不参与
- 不参与：Tier1深跌>25%（胜率37%以下），Tier2浅跌<20%

补仓：跌到-40%以下补一次（仓位×0.5）
止盈：回到-25%卖半数，回到-15%清仓
止损：跌-5%且SMA未到-40%，止损
"""
import psycopg2
import sys

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'


def get_entry_zone(sma_pct, margin_pct):
    """N型曲线区间判定"""
    if sma_pct >= 0:
        return ('等待', 0.0, '未超卖')
    sma = abs(sma_pct)
    is_tier1 = margin_pct is not None and margin_pct > 5

    if is_tier1:
        if 18 <= sma <= 20:
            return ('轻仓', 0.3, 'Tier1浅跌试探')
        else:
            return ('不参与', 0.0, 'Tier1深跌是陷阱')
    else:
        if 20 <= sma <= 25:
            return ('常规', 0.6, '胜率最优区63%')
        elif 25 <= sma <= 35:
            return ('跳过', 0.0, '中跌区胜率54%')
        elif 35 <= sma <= 40:
            return ('增强', 0.9, '黄金坑+14.7%')
        elif sma >= 40:
            return ('试探', 0.3, '深跌区胜率41%')
        else:  # sma < 20
            return ('等待', 0.0, '回调不够深')


def action_summary(sma_pct, margin_pct):
    """区间对应的补仓/止盈/止损规则"""
    sma = abs(sma_pct)
    is_tier1 = margin_pct is not None and margin_pct > 5
    if is_tier1 or sma < 20 or (25 <= sma < 35):
        return ''
    rules = []
    if 20 <= sma < 25:
        rules.append('现价入场')
        rules.append('回到-15%清仓')
        rules.append('跌破-25%暂停，等-35%以下')
    elif 35 <= sma < 40:
        rules.append('现价入场')
        rules.append('再跌到-40%以下补仓×0.5')
        rules.append('回到-25%卖半')
        rules.append('回到-15%清仓')
        rules.append('入场价-5%且SMA>-40%则止损')
    elif sma >= 40:
        rules.append('小仓试探0.3')
        rules.append('有赚即走，严格止损')
    return ' | '.join(rules)


def get_v8_signals():
    """获取V8原始信号"""
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT code, stock_name, industry, density,
               round(sma_dev::numeric * 100, 2) as sma_pct,
               round(pb::numeric, 3) as pb,
               round(close::numeric, 2) as close,
               round(sma120::numeric, 2) as sma120
        FROM mv_v8_candidates
        WHERE sma_dev < -0.2 AND pb < 1.0
        ORDER BY code
    """)
    signals = []
    for r in cur.fetchall():
        signals.append({
            'code': r[0], 'name': r[1] or '', 'industry': r[2] or '',
            'density': r[3] or '',
            'sma_pct': float(r[4]), 'pb': float(r[5]),
            'close': float(r[6]), 'sma120': float(r[7]),
        })
    conn.close()
    return signals


def get_financial(codes):
    """获取财务数据"""
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    placeholders = ','.join(['%s'] * len(codes))
    cur.execute(f"""
        SELECT DISTINCT ON (symbol)
               symbol, net_profit, revenue, roe
        FROM financial_data
        WHERE symbol IN ({placeholders}) AND end_date = '2026-03-31'
        ORDER BY symbol, end_date DESC
    """, codes)
    fin = {}
    for r in cur.fetchall():
        np_val = float(r[1]) if r[1] else 0
        rev_val = float(r[2]) if r[2] else 0
        fin[r[0]] = {
            'np': np_val,
            'rev': rev_val,
            'margin': np_val / rev_val * 100 if rev_val > 0 else None,
            'roe': float(r[3]) if r[3] else None,
        }
    conn.close()
    return fin


def main():
    print(f'\n{"=" * 100}')
    print(f'  V8 场内信号 · N型曲线区间规则')
    print(f'  规则：区间判定，不设目标价')
    print(f'{"=" * 100}\n')

    signals = get_v8_signals()
    codes = ['A.' + s['code'] for s in signals]

    fin = get_financial(codes)

    passed, rejected, unverified = [], [], []
    tier1_count, tier2_count = 0, 0

    for s in signals:
        code = s['code']
        finfo = fin.get(code)
        if finfo is None:
            unverified.append(s)
            continue
        np_val = finfo['np']
        margin = finfo['margin']
        if np_val <= 0:
            rejected.append({**s, 'np': np_val, 'margin': margin})
            continue
        warnings = []
        if margin is not None and margin < 3:
            warnings.append(f'净利率仅{margin:.2f}%（盈利微薄）')
        if margin is not None and margin < 1:
            warnings.append(f'净利仅{np_val/1e4:.0f}万')
        s['layer0'] = {
            'passed': True, 'warnings': warnings,
            'np': np_val, 'margin': margin, 'roe': finfo['roe'],
        }
        passed.append(s)
        if margin is not None and margin > 5:
            tier1_count += 1
        else:
            tier2_count += 1

    # 摘要
    print(f'  Layer 0 通过: {len(passed)}只 (Tier1 {tier1_count} / Tier2 {tier2_count})')
    print(f'  净利为负剔除: {len(rejected)}只')
    print(f'  无财务数据: {len(unverified)}只\n')

    # 按入场等级分组输出
    priority = {'增强': 0, '常规': 1, '轻仓': 2, '试探': 3}

    def sort_key(s):
        a, _, _ = get_entry_zone(s['sma_pct'], s['layer0'].get('margin'))
        return (priority.get(a, 9), s['density'] != 'RARE', s['sma_pct'])

    sorted_passed = sorted(passed, key=sort_key)

    # 分组：可进场 vs 跳过/等待
    actionable = []
    skipped = []
    for s in sorted_passed:
        a, p, r = get_entry_zone(s['sma_pct'], s['layer0'].get('margin'))
        if p > 0:
            actionable.append(s)
        else:
            skipped.append(s)

    # 可进场清单
    print(f'  {"=" * 100}')
    print(f'  🎯 可进场清单 — {len(actionable)}只')
    print(f'  {"=" * 100}')
    print(f'  {"代码":>8} {"名称":<12} {"行业":<16} {"密度":<6} {"SMA%":>8} {"PB":>6} {"净利率":>6} {"仓位":>4} {"进场理由":<18}')
    print(f'  {"-" * 80}')

    for s in actionable:
        a, p, r = get_entry_zone(s['sma_pct'], s['layer0'].get('margin'))
        margin = s['layer0'].get('margin', 0)
        margin_str = f'{margin:.1f}%' if margin else 'N/A'
        pos_str = f'{a}{p:.1f}'
        print(f'  {s["code"]:>8} {s["name"]:<12} {s["industry"]:<16} '
              f'{s["density"]:<6} {s["sma_pct"]:>+7.2f}% {s["pb"]:>5.2f} {margin_str:>6} '
              f'{pos_str:>4} {r[:16]:<18}')

    # 操作规则
    print(f'\n  {"=" * 100}')
    print(f'  📋 操作规则')
    print(f'  {"=" * 100}')

    # 按进场等级展示操作规则示例
    shown_zones = set()
    for s in actionable:
        a, p, r = get_entry_zone(s['sma_pct'], s['layer0'].get('margin'))
        if a not in shown_zones:
            shown_zones.add(a)
            summary = action_summary(s['sma_pct'], s['layer0'].get('margin'))
            if summary:
                print(f'\n  ■ {a}区 (仓位{p}):')
                for item in summary.split(' | '):
                    print(f'    • {item}')
            else:
                print(f'\n  ■ {a}区 (仓位{p})')

    # 统一规则尾表
    print(f'''
  ──────────────────────────────────────────────────
  补仓与止盈统一规则:

  补仓:  入场后跌到 SMA < -40% → 补一次 (仓位×0.5)，总仓位不超1.0
  止盈1: 反弹到 SMA > -25%    → 卖半
  止盈2: 反弹到 SMA > -15%    → 清仓剩余
  止损:  跌超入场价-5% 且 SMA未到-40% → 止损
         跌超入场价-5% 且 SMA已到-40% → 补仓后止损线下调至补仓均价-5%

  ⚠️ T+20胜率63%意味着每3次有1次亏损
  ⚠️ 增强区历史信号仅66次，统计显著性有限
  ──────────────────────────────────────────────────\n''')

    # 跳过/等待
    if skipped:
        print(f'\n  📋 跳过/等待 — {len(skipped)}只 (当前不在可入场区间)')
        for s in skipped[:10]:
            a, p, r = get_entry_zone(s['sma_pct'], s['layer0'].get('margin'))
            print(f'    {s["code"]:>8} {s["name"]:<12} SMA={s["sma_pct"]:+.2f}% → {r[:20]}')


if __name__ == '__main__':
    main()
