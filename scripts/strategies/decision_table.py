#!/usr/bin/env python3
"""
V8综合评估决策 — 五维交叉验证 + 贝叶斯仓位计算

替代旧的规则矩阵(固定权重)，改为:
  1. 五个维度各自独立评分(通过/不通过)
  2. 用贝叶斯后验仓位公式：仓位 = 基础仓位 × 胜率乘数 × 样本折扣 × Regime乘数 × 防线折扣
  3. 任何维度不通过 → 降级处理

决策矩阵 (washout x regime):
  washout \ regime       BEAR        BOTTOM_WARNING  BULL    SIDEWAYS  TOP_WARNING
  恐慌极致+buy          build(0.3)  build(0.5)      build   build     watch
  缩量筑底+buy          build(0.2)  build(0.3)      build   build     watch
  洗盘下跌中继          avoid       watch           watch   watch     avoid
  市场恐慌中继          avoid       watch           watch   watch     avoid
  正常波动/watch        watch       watch           watch   watch     watch
  → build后调用 calc_bayesian_position() 计算最终仓位
"""
import sys
sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
from datetime import date, timedelta
from strategies.regime_detector import RegimeDetector, regime_dynamic_params
from strategies.washout_discriminator import analyze_kline_pattern
from strategies.layer4_regime_backtest import calc_regime_winrate, load_regime_map
from strategies.active_filter import compute_cost_levels_entropy

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

# ──────────────────────────────────────────────
# 一、五维验证矩阵 — 每个维度独立评分
# ──────────────────────────────────────────────

def check_l4(conn, sym, target_date, regime_map, current_regime='BEAR'):
    """
    L4胜率维度 (含Regime环境适配)
    参数:
        current_regime: 当前市场环境，用于判断能否使用BEAR区胜率
                         在BEAR/BOTTOM_WARNING环境下可使用BEAR区胜率
                         在其他环境下(BULL/SIDEWAYS/TOP_WARNING)回退到通用
    返回: (通过?, 得分, 说明), 得分=胜率乘数(0~1)
    """
    result, n_total, _ = calc_regime_winrate(conn, sym, target_date, regime_map, dedup=True)
    if not result or 'ALL' not in result:
        return False, 0.0, 'L4数据不足'
    
    all_r = result['ALL']
    bear_r = result.get('BEAR', {})
    
    all_n = all_r['n']
    all_wr = all_r['winrate']
    bear_wr = bear_r.get('winrate', 0)
    bear_n = bear_r.get('n', 0)
    
    # 判断当前环境是否允许使用BEAR区胜率
    bear_env = current_regime in ('BEAR', 'BOTTOM_WARNING')
    
    if bear_env and bear_n >= 5:
        # BEAR/BOTTOM_WARNING环境下，且BEAR样本充足 → 取BEAR和通用中的高者
        effective_wr = max(all_wr, bear_wr)
        effective_n = all_n
    else:
        # 非BEAR环境或BEAR样本不足 → 只能用通用胜率
        effective_wr = all_wr
        effective_n = all_n
    
    # 通过条件: 胜率>=70% 且 独立样本>=8
    passed = effective_wr >= 70 and effective_n >= 8
    
    # 得分: 胜率乘数 = (wr-50)/(100-50)，clip到0~1
    score = max(0.0, min(1.0, (effective_wr - 50) / 50))
    
    detail = f'L4:通用{all_wr:.0f}%({all_n}) BEAR{bear_wr:.0f}%({bear_n})'
    if bear_env and bear_n >= 5 and bear_wr > all_wr:
        detail += f' [仅{current_regime}环境有效]'
    if not bear_env and bear_n >= 5 and bear_wr > all_wr:
        detail += f' [非BEAR环境,仅用通用]'
    if not passed:
        detail += ' ❌'
        if effective_n < 8:
            detail += f' 样本{effective_n}<8'
        if effective_wr < 70:
            detail += f' 胜率{effective_wr:.0f}%<70%'
    
    return passed, score, detail


def check_washout(washout, action_from_matrix):
    """
    洗盘判别维度
    返回: (通过?, 得分, 说明)
    """
    pattern = washout['pattern']
    verdict = washout['verdict']
    
    if verdict == 'buy':
        passed = True
        score = 1.0
        detail = f'洗盘:{pattern}/buy'
    elif verdict == 'watch':
        passed = True  # watch不算否决，只是保守
        score = 0.5
        detail = f'洗盘:{pattern}/watch'
    else:  # avoid
        passed = False
        score = 0.0
        detail = f'洗盘:{pattern}/avoid ❌ 下跌中继'
    
    # 但如果矩阵本身判了avoid，直接硬否决
    if action_from_matrix == '避开':
        passed = False
        score = 0.0
        detail = f'洗盘:{pattern}/avoid ❌ 硬否决'
    
    return passed, score, detail


def check_entropy(price, defense_line):
    """
    熵法成本维度 — 防线位置检查
    返回: (通过?, 得分, 说明)
    """
    if defense_line <= 0:
        return False, 0.0, '防线数据不足 ❌'
    
    deviation = (price / defense_line - 1) * 100
    
    if price > defense_line:
        # 现价高于防线 → 等回调
        score = 0.5
        passed = False  # 不通过(等回调)，但不是硬否决(可以等)
        detail = f'防线{defense_line:.2f} 现价+{deviation:+.1f}% ❌ 需回调'
    elif deviation >= -2:
        # 防线附近±2% → 最佳入场
        score = 1.0
        passed = True
        detail = f'防线{defense_line:.2f} 现价{deviation:+.1f}% ✅ 防线附近'
    elif deviation >= -10:
        # 跌破防线但未破前低 → 可接受
        score = 0.7
        passed = True
        detail = f'防线{defense_line:.2f} 现价{deviation:+.1f}% ⚠️ 跌破防线'
    else:
        # 跌破前低 → 不入场
        score = 0.0
        passed = False
        detail = f'防线{defense_line:.2f} 现价{deviation:+.1f}% ❌ 深跌破线'
    
    return passed, score, detail


def check_layer0(conn, sym):
    """
    Layer 0财务排雷
    返回: (通过?, 得分, 说明)
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT net_profit FROM financial_data
        WHERE symbol=%s AND net_profit IS NOT NULL
        ORDER BY end_date DESC LIMIT 1
    """, (sym,))
    row = cur.fetchone()
    cur.close()
    
    if not row or row[0] is None:
        return False, 0.3, 'L0数据不足 ⚠️'
    
    profit = row[0]
    if profit > 0:
        return True, 1.0, 'L0近期盈利 ✅'
    else:
        return False, 0.0, 'L0亏损 ❌ 直接排除'


def check_regime_fit(regime):
    """
    Regime适配维度
    返回: (通过?, 得分, 说明)
    """
    fit = {
        'BEAR': True,          # 极空 = 回调买入最佳时机
        'BOTTOM_WARNING': True,  # 熊转牛 = 积极加仓
        'SIDEWAYS': True,       # 震荡 = 可操作
        'BULL': True,           # 牛市 = 各种信号都有效
        'TOP_WARNING': False,   # 牛转熊 = 谨慎
    }
    multipliers = {
        'BEAR': 1.0,
        'BOTTOM_WARNING': 1.2,
        'SIDEWAYS': 0.8,
        'BULL': 0.7,
        'TOP_WARNING': 0.4,
    }
    passed = fit.get(regime, False)
    score = multipliers.get(regime, 0.5)
    
    detail = f'Regime:{regime}'
    if regime == 'TOP_WARNING':
        detail += ' ❌ 牛转熊期，大幅降仓'
    
    return passed, score, detail


# ──────────────────────────────────────────────
# 二、贝叶斯仓位计算
# ──────────────────────────────────────────────

def calc_bayesian_position(base_pos, l4_score, l4_n, regime_mult, defense_score, 
                           regime='BEAR'):
    """
    贝叶斯后验仓位: 仓位 = 基础仓位 × 胜率乘数 × 样本折扣 × Regime乘数 × 防线折扣
    
    参数:
        base_pos: float 基础仓位 (取决于regime)
        l4_score: float 胜率乘数 (0~1, 由check_l4的score传入)
        l4_n: int 独立样本量
        regime_mult: float Regime乘数
        defense_score: float 防线位置折扣 (由check_entropy的score传入)
    
    返回:
        float 最终仓位 (0~1)
    """
    if base_pos <= 0 or l4_score <= 0 or defense_score <= 0:
        return 0.0
    
    # 胜率乘数 (已经check_l4算好了)
    win_mult = l4_score
    
    # 样本量折扣: min(n/20, 1.0)
    sample_discount = min(l4_n / 20, 1.0)
    
    # 防线折扣: 直接使用entropy的score
    defense_mult = defense_score
    
    # 计算
    position = base_pos * win_mult * sample_discount * regime_mult * defense_mult
    
    # 向下取整到5%的倍数
    position = round(position * 20) / 20
    
    return min(position, base_pos)  # 最高不超过基础仓位


# ──────────────────────────────────────────────
# 三、主决策入口
# ──────────────────────────────────────────────

# 决策矩阵 (washout x regime → 基础操作+基础仓位)
DECISION_MATRIX = {
    ('恐慌极致', 'buy'): {
        'BEAR': ('建仓', 0.3),
        'BOTTOM_WARNING': ('建仓', 0.5),
        'BULL': ('建仓', 0.8),
        'SIDEWAYS': ('建仓', 0.3),
        'TOP_WARNING': ('观望', 0.0),
    },
    ('缩量筑底', 'buy'): {
        'BEAR': ('建仓', 0.2),
        'BOTTOM_WARNING': ('建仓', 0.3),
        'BULL': ('建仓', 0.6),
        'SIDEWAYS': ('建仓', 0.2),
        'TOP_WARNING': ('观望', 0.0),
    },
    ('洗盘下跌中继', 'avoid'): {
        'BEAR': ('避开', 0.0),
        'BOTTOM_WARNING': ('观望', 0.0),
        'BULL': ('观望', 0.0),
        'SIDEWAYS': ('观望', 0.0),
        'TOP_WARNING': ('避开', 0.0),
    },
    ('市场恐慌中继', 'avoid'): {
        'BEAR': ('避开', 0.0),
        'BOTTOM_WARNING': ('观望', 0.0),
        'BULL': ('观望', 0.0),
        'SIDEWAYS': ('观望', 0.0),
        'TOP_WARNING': ('避开', 0.0),
    },
}

DEFAULT_DECISION = {
    'BEAR': ('观望', 0.0),
    'BOTTOM_WARNING': ('观望', 0.1),
    'BULL': ('观望', 0.1),
    'SIDEWAYS': ('观望', 0.0),
    'TOP_WARNING': ('观望', 0.0),
}


def make_decision(conn, sym, target_date=None):
    """
    全流程综合决策
    
    返回: dict = {
        'code', 'current_price', 'regime',
        'washout_pattern', 'washout_verdict', 'washout_detail',
        'true_cost', 'defense_line', 'sma_deviation',
        'l4_general_winrate', 'l4_bear_winrate', 'l4_n',
        'layer0',
        'checks': {维度名: (通过?, 说明)},
        'action', 'position'(贝叶斯计算),
        'stop_loss', 'take_profit', 'reason'
    }
    """
    if target_date is None:
        target_date = date.today()
    
    code = sym[2:]
    cur = conn.cursor()
    
    # ── 1. 最新收盘价 ──
    cur.execute("""
        SELECT close FROM daily_prices
        WHERE symbol=%s AND trade_date <= %s AND close>0
        ORDER BY trade_date DESC LIMIT 1
    """, (sym, target_date))
    price_r = cur.fetchone()
    if not price_r:
        return None
    price = float(price_r[0])
    
    # ── 2. Regime ──
    detector = RegimeDetector(conn)
    regime_data = detector.get_current_regime(target_date)
    regime = regime_data['regime']
    regime_params = regime_dynamic_params(regime)
    
    # ── 3. 熵法成本 ──
    ea = compute_cost_levels_entropy(conn, sym, target_date, lookback=100)
    if not ea:
        ea = {}
    true_cost = ea.get('true_cost_vwap', 0)
    defense_line = ea.get('low_safe_zone', 0)
    
    # ── 4. 洗盘判别 ──
    cur.execute("""
        SELECT trade_date, open, high, low, close, volume
        FROM daily_prices
        WHERE symbol=%s AND trade_date BETWEEN %s AND %s
          AND close>0 AND volume>0
        ORDER BY trade_date
    """, (sym, target_date - timedelta(days=30), target_date))
    kline_rows = cur.fetchall()
    
    washout = {'pattern': '正常波动', 'verdict': 'watch', 'detail': '数据不足'}
    if len(kline_rows) >= 10:
        kline = [
            {'date': r[0], 'open': float(r[1]), 'high': float(r[2]),
             'low': float(r[3]), 'close': float(r[4]), 'volume': int(r[5])}
            for r in kline_rows
        ]
        washout = analyze_kline_pattern(kline, defense_line, regime)
    
    # ── 5. 分层L4 (去重) ──
    regime_map = load_regime_map(conn)
    result, _, _ = calc_regime_winrate(conn, sym, target_date, regime_map, dedup=True)
    l4_general = result.get('ALL', {}).get('winrate', 0) if result else 0
    l4_bear = result.get('BEAR', {}).get('winrate', 0) if result else 0
    l4_n = result.get('ALL', {}).get('n', 0) if result else 0
    l4_effective = max(l4_general, l4_bear)
    # 样本量按BEAR区取，不足用通用
    bear_n = result.get('BEAR', {}).get('n', 0) if result else 0
    effective_n = max(l4_n, bear_n)
    
    # ── 6. SMA偏离 ──
    cur.execute("""
        SELECT close FROM daily_prices
        WHERE symbol=%s AND trade_date BETWEEN %s AND %s
          AND close>0 AND volume>0
        ORDER BY trade_date
    """, (sym, target_date - timedelta(days=200), target_date))
    closes = [float(r[0]) for r in cur.fetchall()]
    sma_dev = 0
    if len(closes) >= 120:
        sma120 = sum(closes[-120:]) / 120
        sma_dev = (closes[-1] / sma120 - 1) * 100
    
    # ── 7. Layer 0 ──
    cur.execute("""
        SELECT net_profit FROM financial_data
        WHERE symbol=%s AND net_profit IS NOT NULL
        ORDER BY end_date DESC LIMIT 1
    """, (sym,))
    layer0_r = cur.fetchone()
    layer0 = '✅ 盈利' if (layer0_r and layer0_r[0] and layer0_r[0] > 0) else '⚠️ 亏损'
    if layer0_r is None:
        layer0 = '数据不足'
    
    # ── 8. 从矩阵获取基础操作 ──
    pattern = washout['pattern']
    verdict = washout['verdict']
    decision_key = (pattern, verdict)
    matrix_entry = DECISION_MATRIX.get(decision_key, {}).get(
        regime, DEFAULT_DECISION.get(regime, ('观望', 0.0))
    )
    base_action, base_weight = matrix_entry
    
    # ── 9. 五维交叉验证 ──
    checks = {}
    
    l4_passed, l4_score, l4_detail = check_l4(conn, sym, target_date, regime_map, current_regime=regime)
    checks['L4胜率'] = (l4_passed, l4_detail)
    
    washout_passed, washout_score, washout_detail = check_washout(washout, base_action)
    checks['洗盘判别'] = (washout_passed, washout_detail)
    
    entropy_passed, entropy_score, entropy_detail = check_entropy(price, defense_line)
    checks['防线位置'] = (entropy_passed, entropy_detail)
    
    l0_passed, l0_score, l0_detail = check_layer0(conn, sym)
    checks['财务排雷'] = (l0_passed, l0_detail)
    
    regime_passed, regime_score, regime_detail = check_regime_fit(regime)
    checks['Regime适配'] = (regime_passed, regime_detail)
    
    # ── 10. 否决链 ──
    veto_reasons = []
    action = base_action
    bayesian_pos = 0.0
    
    # Layer 0硬否决
    if not l0_passed:
        action = '避开'
        bayesian_pos = 0.0
        veto_reasons.append('财务排雷不通过')
    
    # 洗盘硬否决
    if not washout_passed and action not in ('观望',):
        action = '观望'
        bayesian_pos = 0.0
        veto_reasons.append(f'洗盘形态否决')
    
    # 防线深跌破前低
    if entropy_score == 0.0 and action in ('建仓', '轻仓'):
        action = '观望'
        veto_reasons.append('深跌破防线')
    
    # L4不通过 → 降档
    if not l4_passed and action in ('建仓',):
        action = '观望' if effective_n < 5 else '轻仓'
        veto_reasons.append(f'L4不达标(胜率{l4_effective:.0f}% 样本{effective_n})')
    
    # ── 11. 贝叶斯仓位计算 ──
    if action in ('建仓', '轻仓', '加仓'):
        base_pos = base_weight  # 从矩阵获取的基础仓位
        bayesian_pos = calc_bayesian_position(
            base_pos=base_pos,
            l4_score=l4_score,
            l4_n=effective_n,
            regime_mult=regime_score,
            defense_score=entropy_score,
            regime=regime
        )
        
        # 如果仓位太低就不建仓了
        if bayesian_pos < 0.05:
            action = '观望'
            veto_reasons.append(f'贝叶斯仓位{bayesian_pos:.0%}<5%')
    
    # ── 12. 止损止盈 ──
    stop_loss = 0
    take_profit = 0
    if action in ('建仓', '轻仓', '加仓') and defense_line > 0:
        stop_loss = round(defense_line * 0.97, 2)
    if action in ('建仓', '轻仓', '加仓') and true_cost > 0:
        take_profit = round(true_cost, 2)
    
    # ── 13. 决策理由 ──
    reason_parts = [f'K线:{pattern}/{verdict}', f'Regime:{regime}']
    if veto_reasons:
        reason_parts.append(f'否决:{"+".join(veto_reasons)}')
    else:
        reason_parts.append(f'仓位{bayesian_pos:.0%}')
        if true_cost > 0:
            reason_parts.append(f'跌破成本{(price/true_cost-1)*100:.0f}%')
    
    reason = ' | '.join(reason_parts)
    
    return {
        'code': code,
        'current_price': price,
        'regime': regime,
        'washout_pattern': pattern,
        'washout_verdict': verdict,
        'washout_detail': washout['detail'],
        'true_cost': round(true_cost, 2) if true_cost else 0,
        'defense_line': round(defense_line, 2) if defense_line else 0,
        'sma_deviation': round(sma_dev, 1),
        'l4_general_winrate': l4_general,
        'l4_bear_winrate': l4_bear,
        'l4_n': l4_n,
        'layer0': layer0,
        'checks': checks,
        'action': action,
        'position': bayesian_pos,
        'stop_loss': stop_loss,
        'take_profit': take_profit,
        'reason': reason,
    }


if __name__ == '__main__':
    conn = psycopg2.connect(DB)
    target = date(2026, 6, 5)
    
    codes = ['000850', '002073', '600635', '600273', '000912', '300284', '600810',
             '002440', '002394', '600649', '603367', '000959']
    names = {
        '000850': '华茂', '002073': '软控', '600635': '大众',
        '600273': '嘉化', '000912': '泸天化', '300284': '苏交科', '600810': '神马',
        '002440': '闰土', '002394': '联发', '600649': '城投', '603367': '辰欣', '000959': '首钢',
    }
    
    print(f"{'='*110}")
    print(f"V8综合评估框架 v2 — 五维交叉验证 + 贝叶斯仓位  {target}")
    print(f"{'='*110}")
    print(f"{'代码':<6} {'名称':<6} {'现价':>6} {'Regime':<14} {'洗盘':<14} {'成本':>7} {'防线':>6} {'L4通':>7} {'L4熊':>7} {'n':>3} {'L0':<8} {'操作':<6} {'仓位':>5} {'止损':>6} {'止盈':>7}")
    print('-' * 110)
    
    all_results = []
    
    for code in codes:
        sym = f'A.{code}'
        name = names.get(code, code)
        d = make_decision(conn, sym, target)
        if d:
            all_results.append(d)
            pos_pct = f"{d['position']:.0%}" if d['position'] > 0 else '-'
            print(f"{code:<6} {name:<6} {d['current_price']:>6.2f} {d['regime']:<14} {d['washout_pattern']:<14} {d['true_cost']:>7.2f} {d['defense_line']:>6.2f} {d['l4_general_winrate']:>6.0f}% {d['l4_bear_winrate']:>6.0f}% {d['l4_n']:>3} {d['layer0']:<8} {d['action']:<6} {pos_pct:>5} {d['stop_loss']:>6.2f} {d['take_profit']:>7.2f}")
            print(f"  📝 {d['reason']}")
            # 五维check详情
            c = d['checks']
            print(f"  🔍 L4:{c['L4胜率'][1]} | 洗盘:{c['洗盘判别'][1]} | 防线:{c['防线位置'][1]} | 财务:{c['财务排雷'][1]} | Regime:{c['Regime适配'][1]}")
            if d['washout_verdict'] in ('buy', 'watch'):
                washout_info = d['washout_detail'][:80] if d['washout_detail'] else ''
                if washout_info:
                    print(f"  🎯 洗盘:{washout_info}")
    
    # 评级表
    print(f"\n{'='*110}")
    print(f"评级表 (按贝叶斯仓位排序)")
    print(f"{'='*110}")
    
    # 自动评级 (只对实际建仓标的评有效级)
    def auto_grade(d):
        c = d['checks']
        passed = sum(1 for k, (ok, _) in c.items() if ok)
        all_pass = all(ok for ok, _ in c.values())
        if d['action'] not in ('建仓', '轻仓', '加仓') or d['position'] <= 0:
            return 'C'
        if all_pass and d['position'] >= 0.10:
            return 'A'
        elif passed >= 4 and d['position'] >= 0.05:
            return 'B'
        else:
            return 'C(条件不足)'
    
    sorted_results = sorted(all_results, key=lambda x: -x['position'])
    
    print(f"{'代码':<6} {'名称':<6} {'评级':<8} {'操作':<6} {'仓位':>5} {'通过':>5} {'L4':<6} {'洗盘':<6} {'防线':<6} {'财务':<6} {'Regime':<8} {'备注'}")
    print('-' * 110)
    
    for d in sorted_results:
        grade = auto_grade(d)
        pos_pct = f"{d['position']:.0%}" if d['position'] > 0 else '-'
        c = d['checks']
        marks = []
        for k in ['L4胜率', '洗盘判别', '防线位置', '财务排雷', 'Regime适配']:
            ok, _ = c.get(k, (False, ''))
            marks.append('✅' if ok else '❌')
        passed = sum(1 for k, (ok, _) in c.items() if ok)
        
        note = ''
        if d['action'] in ('建仓', '轻仓'):
            if d['position'] >= 0.10:
                note = '建议执行'
            else:
                note = '观察等买点'
        elif d['action'] == '观望':
            note = '条件不足'
        elif d['action'] == '避开':
            note = '硬否决'
        
        print(f"{d['code']:<6} {names.get(d['code'], d['code']):<6} {grade:<8} {d['action']:<6} {pos_pct:>5} {passed:>4}/5 {' '.join(marks)} {note}")
    
    conn.close()
