#!/usr/bin/env python3
"""
洗盘/崩盘判别器 — 区分"庄家洗盘式下跌"和"趋势性崩盘"

输入: 个股近30根K线, 熵法防线, 当前Regime
输出: K线形态模式 + 判定结论

模式:
  1. 洗盘下跌中继 — 光脚实体阴线(无上下影,跌幅>3%), 最近5天未连续阴跌
  2. 市场恐慌中继 — 长下影V反(下影>实体2倍), 成交量放大
  3. 恐慌极致 — 连续阴跌(>3天,累计跌幅>8%) + 单日极端大跌(<-5%)
  4. 筑底 — 缩量小阴小阳横盘(3天以上), 在防线附近
"""
import numpy as np
from datetime import date, timedelta
from collections import OrderedDict


def analyze_kline_pattern(kline_data, low_safe_zone=None, regime=None):
    """
    分析个股最新K线形态
    
    Args:
        kline_data: list of dicts, 每根K线 {date, open, high, low, close, volume}
                    按日期升序排列，最新一根在最后
        low_safe_zone: 熵法防线价格
        regime: 当前大盘regime (BEAR/BULL/etc)
    
    Returns:
        dict: {
            'pattern': str,  # 形态名称
            'verdict': str,  # 'buy'/'wait'/'avoid'/'watch'
            'detail': str,   # 解释
            'confidence': float,  # 0~1
        }
    """
    if not kline_data or len(kline_data) < 10:
        return {'pattern': 'UNKNOWN', 'verdict': 'watch', 'detail': '数据不足', 'confidence': 0.0}
    
    # 计算K线特征
    n = len(kline_data)
    candles = _calc_candle_features(kline_data, n)
    
    # 最新K线特征
    last = candles[-1]
    prev = candles[-2] if n >= 2 else None
    
    # === 形态判别（按优先级从高到低）===
    
    # 1. 连续阴跌+极端大跌 (恐慌极致)
    verdict, detail = _check_panic_extreme(kline_data, candles, low_safe_zone)
    if verdict:
        return {'pattern': '恐慌极致', 'verdict': verdict, 'detail': detail, 'confidence': 0.85}
    
    # 2. 光脚实体阴线 (洗盘下跌中继)
    verdict, detail = _check_bare_entity_break(kline_data, candles, low_safe_zone)
    if verdict:
        return {'pattern': '洗盘下跌中继', 'verdict': verdict, 'detail': detail, 'confidence': 0.75}
    
    # 3. 长下影V反 (市场恐慌中继)
    verdict, detail = _check_long_lower_shadow(kline_data, candles, low_safe_zone)
    if verdict:
        return {'pattern': '市场恐慌中继', 'verdict': verdict, 'detail': detail, 'confidence': 0.7}
    
    # 4. 缩量筑底
    verdict, detail = _check_bottom_building(kline_data, candles, low_safe_zone)
    if verdict:
        return {'pattern': '缩量筑底', 'verdict': verdict, 'detail': detail, 'confidence': 0.8}
    
    # 5. 正常波动
    return {'pattern': '正常波动', 'verdict': 'watch', 'detail': '无明显形态', 'confidence': 0.5}


def _calc_candle_features(kline_data, n):
    """计算每根K线的技术特征"""
    candles = []
    prev_close = None
    
    for i, k in enumerate(kline_data):
        o, h, l, c, v = float(k['open']), float(k['high']), float(k['low']), float(k['close']), int(k['volume'])
        
        d = str(k['date'])[:10] if hasattr(k['date'], 'strftime') else str(k['date'])[:10]
        
        if prev_close is None or prev_close == 0:
            chg = 0
            ampl = 0
            upper_pct = 0
            lower_pct = 0
            body_pct = 0
        else:
            chg = (c / prev_close - 1) * 100
            ampl = (h - l) / prev_close * 100
            upper_pct = (h - max(o, c)) / prev_close * 100
            lower_pct = (min(o, c) - l) / prev_close * 100
            body_pct = abs(c - o) / prev_close * 100
        
        candles.append({
            'date': d,
            'open': o, 'high': h, 'low': l, 'close': c, 'volume': v,
            'chg_pct': round(chg, 2),
            'ampl_pct': round(ampl, 2),
            'upper_pct': round(upper_pct, 2),
            'lower_pct': round(lower_pct, 2),
            'body_pct': round(body_pct, 2),
            'is_red': c >= o,
            'is_bare': upper_pct < 0.2 and lower_pct < 0.2,  # 光脚
            'is_entity': body_pct > ampl * 0.65 if ampl > 0 else False,  # 实体占主体
        })
        
        prev_close = c
    
    return candles


def _check_panic_extreme(kline_data, candles, low_safe_zone):
    """
    恐慌极致：连续阴跌(>3天,累计跌>8%) + 单日极端大跌(<-5%)
    这是唯一可抄底的形态
    """
    n = len(candles)
    if n < 5:
        return None, None
    
    last = candles[-1]
    
    # 当日是否极端大跌
    if last['chg_pct'] >= -5:
        return None, None
    
    # 检查前4天是否连续下跌
    lookback = min(5, n - 1)
    prev_candles = candles[-lookback-1:-1]  # 不包括当天
    
    consec_down = 0
    cumul_down = 0
    for c in reversed(prev_candles):  # 最近到最远
        if c['chg_pct'] < 0:
            consec_down += 1
            cumul_down += c['chg_pct']
        else:
            break
    
    cumul_down += last['chg_pct']  # 加上当天的大跌
    
    if consec_down >= 3 and cumul_down <= -8:
        detail = (
            f"连续{consec_down+1}天下跌(含今天), 累计跌{cumul_down:.1f}%"
            f" 今日大跌{last['chg_pct']:.1f}%"
        )
        if low_safe_zone and last['close'] <= low_safe_zone:
            detail += f" ✅ 跌至防线{low_safe_zone}附近，可以考虑抄底"
            return 'buy', detail
        elif low_safe_zone and last['close'] <= low_safe_zone * 1.05:
            detail += f" 🟡 接近防线{low_safe_zone}，准备抄底"
            return 'watch', detail
        else:
            detail += " 尚未跌至防线，继续观望"
            return 'wait', detail
    
    return None, None


def _check_bare_entity_break(kline_data, candles, low_safe_zone):
    """
    光脚实体阴线：无上下影或极短，实体占振幅>65%, 跌幅>3%
    最近5天未出现连续阴跌 → 洗盘下跌中继
    已经出现连续阴跌 → 可能是恐慌的一部分，交给_panic_extreme判断
    """
    last = candles[-1]
    
    # 条件：下跌、实体阴、光脚
    if not (last['chg_pct'] < -2 and last['is_entity'] and last['is_bare']):
        return None, None
    
    # 检查前5天是否已有连续阴跌
    n = len(candles)
    lookback = min(6, n - 1)
    prev = candles[-lookback:-1]
    
    consec_down = 0
    for c in reversed(prev):
        if c['chg_pct'] < 0:
            consec_down += 1
        else:
            break
    
    if consec_down >= 2:
        # 已经连跌，可能是恐慌的一部分
        return None, None
    
    detail = (
        f"光脚实体阴线, 跌{last['chg_pct']:.1f}%"
        f" 振幅{last['ampl_pct']:.1f}% 实体{last['body_pct']:.1f}%"
        f" 上影{last['upper_pct']:.2f}% 下影{last['lower_pct']:.2f}%"
        f" 近5天未连续阴跌 → 洗盘下跌中继，不可抄底"
    )
    return 'avoid', detail


def _check_long_lower_shadow(kline_data, candles, low_safe_zone):
    """
    长下影V反：下影线 > 实体2倍 + 成交量放大
    """
    last = candles[-1]
    
    if last['lower_pct'] <= last['body_pct'] * 2 or last['body_pct'] <= 0.5:
        return None, None
    
    if last['chg_pct'] > 0:
        # 阳线带长下影 — 有人接手，不一定是恐慌
        return None, None
    
    # 成交量放大：对比前3日均量
    n = len(candles)
    lookback = min(4, n - 1)
    avg_vol = np.mean([c['volume'] for c in candles[-lookback:-1]])
    
    if avg_vol > 0 and last['volume'] > avg_vol * 1.5:
        detail = (
            f"长下影V反, 下影{last['lower_pct']:.2f}% 实体{last['body_pct']:.2f}%"
            f" 放量{last['volume']/10000:.0f}万手 (均值{avg_vol/10000:.0f}万)"
            f" → 市场恐慌中继，不可抄底"
        )
        return 'avoid', detail
    
    return None, None


def _check_bottom_building(kline_data, candles, low_safe_zone):
    """
    缩量筑底：连续3天以上缩量小阴小阳，在防线附近
    """
    n = len(candles)
    if n < 4:
        return None, None
    
    # 最近3天是否都是小阴小阳 (振幅<4%, 涨跌幅绝对值<4%)
    recent = candles[-3:]
    
    all_small = all(
        abs(c['chg_pct']) < 4 and c['ampl_pct'] < 5
        for c in recent
    )
    
    if not all_small:
        return None, None
    
    # 缩量：日均量 < 前10日均值 * 0.8
    lookback = min(10, n - 3)
    avg_vol_10 = np.mean([c['volume'] for c in candles[-lookback-3:-3]]) if lookback >= 3 else 0
    avg_vol_3 = np.mean([c['volume'] for c in recent])
    
    # 最新收盘是否在防线附近
    last = candles[-1]
    
    detail_parts = [f"连续3天缩量小阴小阳"]
    
    if avg_vol_10 > 0 and avg_vol_3 < avg_vol_10 * 0.8:
        detail_parts.append(f"缩量{avg_vol_3/10000:.0f}万/日(前10日均{avg_vol_10/10000:.0f}万)")
    else:
        detail_parts.append(f"量{avg_vol_3/10000:.0f}万/日")
    
    if low_safe_zone:
        pct_from_line = (last['close'] / low_safe_zone - 1) * 100
        if pct_from_line < 0:
            detail_parts.append(f"已跌破防线{low_safe_zone} ({pct_from_line:.1f}%)")
            detail_parts.append("缩量筑底+破防线 → 可试探性建仓")
            detail = ' '.join(detail_parts)
            return 'buy', detail
        elif pct_from_line < 3:
            detail_parts.append(f"接近防线{low_safe_zone} ({pct_from_line:+.1f}%)")
            detail_parts.append("缩量筑底+接近防线 → 准备好，等回踩")
            detail = ' '.join(detail_parts)
            return 'watch', detail
        else:
            detail_parts.append(f"距防线{low_safe_zone}尚远 ({pct_from_line:+.1f}%)")
            detail_parts.append("继续观望")
            detail = ' '.join(detail_parts)
            return 'wait', detail
    else:
        detail_parts.append("防线数据不足")
        detail = ' '.join(detail_parts)
        return 'watch', detail


def test_on_stock(conn, sym, target_date, low_safe_zone):
    """对一只股票运行判别器"""
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, open, high, low, close, volume
        FROM daily_prices
        WHERE symbol=%s AND trade_date BETWEEN %s AND %s
          AND close>0 AND volume>0
        ORDER BY trade_date
    """, (sym, target_date - timedelta(days=30), target_date))
    rows = cur.fetchall()
    
    if len(rows) < 10:
        return None
    
    kline = [
        {'date': r[0], 'open': float(r[1]), 'high': float(r[2]),
         'low': float(r[3]), 'close': float(r[4]), 'volume': int(r[5])}
        for r in rows
    ]
    
    return analyze_kline_pattern(kline, low_safe_zone)


if __name__ == '__main__':
    import sys
    sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
    import psycopg2
    from strategies.active_filter import DB, compute_cost_levels_entropy
    from datetime import date
    
    target = date(2026, 6, 5)
    conn = psycopg2.connect(DB)
    
    codes = ['000850', '002073', '600635', '600273', '000912', '300284', '600810']
    names = {
        '000850': '华茂股份', '002073': '软控股份', '600635': '大众公用',
        '600273': '嘉化能源', '000912': '泸天化', '300284': '苏交科', '600810': '神马股份'
    }
    
    print(f"{'='*70}")
    print(f"洗盘/崩盘判别器 — {target}")
    print(f"{'='*70}")
    print(f"{'代码':<8} {'名称':<10} {'形态':<16} {'判定':<12} {'详情'}")
    print('-' * 70)
    
    for code in codes:
        sym = f'A.{code}'
        name = names.get(code, code)
        
        # 获取熵法防线
        ea = compute_cost_levels_entropy(conn, sym, target, lookback=100)
        ls = ea.get('low_safe_zone', None) if ea else None
        
        result = test_on_stock(conn, sym, target, ls)
        
        if result:
            verdict_map = {'buy': '可抄底 🟢', 'watch': '观望 🟡', 'wait': '等待 🟠', 'avoid': '避开 ❌'}
            vr = verdict_map.get(result['verdict'], result['verdict'])
            print(f"{code:<8} {name:<10} {result['pattern']:<16} {vr:<12} {result['detail'][:60]}")
        else:
            print(f"{code:<8} {name:<10} {'数据不足':<16} {'-':<12} {'-':>60}")
    
    conn.close()
