#!/usr/bin/env python3
"""
市场造势链条复盘工具 — 逆向工程：事件 → 媒体 → 情绪 → 资金 → 行情

对选定的历史事件窗口，构建完整的时间线数据表：
  日期 | 事件 | 指数涨跌幅 | 涨跌停比 | 两融变化 | 北向 | 龙虎榜 | 波动率

然后标注各阶段的「造势模式」，供后续智库人物框架分析。
"""
import sys, os, logging
from datetime import date, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import data_finance as df
import json

log = logging.getLogger('market_psychology')

# ============================================================
# 时间线构建
# ============================================================

def build_timeline(
    start_date: str,
    end_date: str,
    index_symbol: str = 'I.000300'
) -> dict:
    """构建一窗事件窗口的完整数据时间线
    
    Args:
        start_date: '2025-04-01'
        end_date: '2025-04-30'
        index_symbol: 基准指数
    
    Returns:
        {date_str: {date, index_close, index_chg_pct, up_stocks, down_stocks,
                    zebra_ratio, margin_sh, margin_sz, ...}}
    """
    tl = {}
    
    # 1. 指数日线
    _load_index(tl, index_symbol, start_date, end_date)
    
    # 补充上证/创业板
    _load_index(tl, 'I.000001', start_date, end_date, prefix='sz')
    _load_index(tl, 'I.399006', start_date, end_date, prefix='cy')
    
    # 2. 两融余额（只拉上交所，深交所缺数据）
    _load_margin(tl, start_date, end_date)
    
    # 3. 涨跌停比（通过行情快照计算）
    _load_up_down_ratio(tl, start_date, end_date)
    
    # 4. 波动率
    _calc_volatility(tl)
    
    return tl


def _load_index(tl: dict, symbol: str, start: str, end: str, prefix: str = 'index'):
    """加载指数数据"""
    rows, _ = df._pg_execute(
        "SELECT trade_date, close, open, high, low, volume "
        "FROM daily_prices WHERE symbol = %s "
        "AND trade_date BETWEEN %s::date AND %s::date ORDER BY trade_date",
        (symbol, start, end))
    
    prev_close = None
    for r in rows:
        dt = str(r[0])
        if dt not in tl:
            tl[dt] = {'date': dt}
        tl[dt][f'{prefix}_close'] = float(r[1])
        if prev_close and prev_close > 0:
            tl[dt][f'{prefix}_chg_pct'] = round((float(r[1]) - prev_close) / prev_close * 100, 2)
        else:
            tl[dt][f'{prefix}_chg_pct'] = 0.0
        tl[dt][f'{prefix}_open'] = float(r[2]) if r[2] else 0
        tl[dt][f'{prefix}_high'] = float(r[3]) if r[3] else 0
        tl[dt][f'{prefix}_low'] = float(r[4]) if r[4] else 0
        tl[dt][f'{prefix}_volume'] = float(r[5]) if r[5] else 0
        prev_close = float(r[1])


def _load_margin(tl: dict, start: str, end: str):
    """加载两融数据（上交所大盘）"""
    rows, _ = df._pg_execute(
        "SELECT trade_date, value, change_val, change_pct, extra "
        "FROM market_signals WHERE signal_type='margin_sh' AND code='TOTAL' "
        "AND trade_date BETWEEN %s::date AND %s::date ORDER BY trade_date",
        (start, end))
    for r in rows:
        dt = str(r[0])
        if dt not in tl:
            tl[dt] = {'date': dt}
        tl[dt]['margin_sh'] = float(r[1]) / 1e8  # 亿
        tl[dt]['margin_sh_balance'] = float(r[2]) / 1e8 if r[2] else 0  # 融资余额
        tl[dt]['margin_sh_buy'] = float(r[3]) / 1e8 if r[3] else 0  # 融资买入额
        try:
            extra = json.loads(r[4]) if r[4] else {}
            tl[dt]['margin_sh_rqye'] = float(extra.get('rqye', 0)) / 1e8
        except:
            tl[dt]['margin_sh_rqye'] = 0
    
    # 深交所
    rows2, _ = df._pg_execute(
        "SELECT trade_date, value, change_val FROM market_signals "
        "WHERE signal_type='margin_sz' AND code='TOTAL' "
        "AND trade_date BETWEEN %s::date AND %s::date ORDER BY trade_date",
        (start, end))
    for r in rows2:
        dt = str(r[0])
        if dt not in tl:
            tl[dt] = {'date': dt}
        tl[dt]['margin_sz'] = float(r[1]) / 1e8


def _load_up_down_ratio(tl: dict, start: str, end: str):
    """从 daily_prices 计算每日涨跌家数比"""
    rows, _ = df._pg_execute(
        "SELECT trade_date, symbol, close "
        "FROM daily_prices WHERE symbol LIKE 'A.%%' "
        "AND trade_date BETWEEN %s::date AND %s::date ORDER BY trade_date",
        (start, end))
    
    prev = {}
    daily = {}
    for r in rows:
        dt = str(r[0])
        code = r[1]
        close = float(r[2]) if r[2] else 0
        if dt not in daily:
            daily[dt] = {'up': 0, 'down': 0, 'flat': 0, 'limit_up': 0, 'limit_down': 0}
        
        if code in prev and prev[code] > 0:
            chg = (close - prev[code]) / prev[code] * 100
            if chg > 0:
                daily[dt]['up'] += 1
                if chg >= 9.5:
                    daily[dt]['limit_up'] += 1
            elif chg < 0:
                daily[dt]['down'] += 1
                if chg <= -9.5:
                    daily[dt]['limit_down'] += 1
            else:
                daily[dt]['flat'] += 1
        
        prev[code] = close
    
    for dt, stats in daily.items():
        if dt not in tl:
            tl[dt] = {'date': dt}
        tl[dt].update(stats)


def _calc_volatility(tl: dict, window: int = 20):
    """计算滚动波动率（基于日收益率）"""
    dates = sorted(tl.keys())
    returns = []
    for dt in dates:
        entry = tl[dt]
        if 'index_chg_pct' in entry:
            returns.append(entry['index_chg_pct'])
        else:
            returns.append(0)
    
    # 滚动年化波动率
    import numpy as np
    for i, dt in enumerate(dates):
        if i >= window - 1:
            window_returns = returns[i - window + 1:i + 1]
            vol = np.std(window_returns) * (252 ** 0.5)
            tl[dt]['volatility'] = round(vol, 2)
        else:
            tl[dt]['volatility'] = 0.0


def format_timeline_text(tl: dict, top_n: int = 10) -> str:
    """格式化为易读时间线"""
    dates = sorted(tl.keys())
    lines = ["市场造势链条复盘", "=" * 80]
    lines.append("")
    lines.append("%-12s %-10s %-8s %-8s %-8s %-10s" % (
        "日期", "沪深300", "涨跌停", "两融余额", "波动率", "融资买入"))
    lines.append("-" * 80)
    
    for dt in dates:
        e = tl[dt]
        index_chg = e.get('index_chg_pct', 0)
        marker = "🔥" if abs(index_chg) >= 3 else (
            "⭐" if abs(index_chg) >= 1.5 else "  ")
        
        up_ratio = ""
        if 'up' in e and 'down' in e and (e['up'] + e['down']) > 0:
            up_ratio = "%d/%d" % (e.get('limit_up',0), e.get('limit_down',0))
        
        margin_val = e.get('margin_sh', 0)
        vol = e.get('volatility', 0)
        margin_buy = e.get('margin_sh_buy', 0)
        
        lines.append("%s %-10s %+6.2f%% %8s %8.0f亿 %6.1f %8.0f亿" % (
            marker, dt, index_chg, up_ratio, margin_val, vol, margin_buy))
    
    return "\n".join(lines)


# ============================================================
# 关键变化检测
# ============================================================

def detect_anomalies(tl: dict, threshold: float = 2.0) -> list:
    """检测异常日（大幅波动 + 两融异动 + 涨跌停比异常）
    
    Returns: [{date, anomalies: [...]}]
    """
    results = []
    for dt, e in sorted(tl.items()):
        anomalies = []
        
        # 指数异常
        idx_chg = abs(e.get('index_chg_pct', 0))
        if idx_chg >= threshold:
            anomalies.append("指数波动>%.1f%% (%+.2f%%)" % (threshold, e.get('index_chg_pct', 0)))
        
        # 两融突变
        margin = e.get('margin_sh', 0)
        dates = sorted(tl.keys())
        idx = dates.index(dt)
        if idx > 0:
            prev_dt = dates[idx - 1]
            prev_margin = tl[prev_dt].get('margin_sh', 0)
            if prev_margin > 0:
                chg = (margin - prev_margin) / prev_margin * 100
                if abs(chg) > 0.5:
                    anomalies.append("两融日变%.2f%% (%+.0f亿)" % (chg, margin - prev_margin))
        
        # 涨跌停比极端
        if 'up' in e and 'down' in e and (e['up'] + e['down']) > 0:
            total = e['up'] + e['down']
            limit_up_ratio = e.get('limit_up', 0) / total * 100
            limit_down_ratio = e.get('limit_down', 0) / total * 100
            if limit_up_ratio > 5:
                anomalies.append("涨停占比%.1f%%" % limit_up_ratio)
            if limit_down_ratio > 5:
                anomalies.append("跌停占比%.1f%%" % limit_down_ratio)
        
        if anomalies:
            results.append({'date': dt, 'anomalies': anomalies})
    
    return results


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    
    # 默认：2025年4月关税冲击
    tl = build_timeline('2025-04-01', '2025-04-30')
    print(format_timeline_text(tl))
    
    print("\n\n异常检测:")
    anomalies = detect_anomalies(tl)
    for a in anomalies:
        print("  %s: %s" % (a['date'], '; '.join(a['anomalies'])))
    
    # 保存到文件
    import json
    out_path = '/mnt/d/data/think-tank/reports/tariff_shock_202504_timeline.json'
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(tl, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n时间线已保存: {out_path} ({len(tl)} 天)")
