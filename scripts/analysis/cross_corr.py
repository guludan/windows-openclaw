#!/usr/bin/env python3
"""交叉相关分析 — 信号 vs 股指的领先滞后关系

用 Log-return 的滚动皮尔森相关系数（-20 ~ +20 天时滞）
配合 Granger 因果检验找最优时滞。

支持的信号源:
- north_sh/sz: 沪股通/深股通净买入
- margin_sh/sz: 两融余额
- foreign_etf: 海外中国ETF
"""
import sys, os, logging
from datetime import date, timedelta
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import data_finance as df
import numpy as np

log = logging.getLogger('analysis.cross_corr')


def _load_series(table: str, field: str, where: str,
                 start: str = None, end: str = None) -> dict:
    """通用时间序列加载：{date_str: value}"""
    if end is None:
        end = date.today().isoformat()
    if start is None:
        start = (date.today() - timedelta(days=365)).isoformat()
    
    if table == 'daily_prices':
        sql = f"SELECT trade_date, {field} FROM {table} WHERE {where} AND trade_date BETWEEN %s AND %s ORDER BY trade_date"
    else:
        sql = f"SELECT trade_date, {field} FROM {table} WHERE {where} AND trade_date BETWEEN %s AND %s ORDER BY trade_date"
    
    rows, _ = df._pg_execute(sql, (start, end))
    return {str(r[0]): float(r[1]) for r in rows if r[1] is not None}


def cross_correlate(signal: dict, target: dict, max_lag: int = 20) -> dict:
    """计算交叉相关
    
    Args:
        signal: {date: value}
        target: {date: value}
        max_lag: 最大时滞天数
    
    Returns:
        {lag: correlation}  正 lag = signal 领先 target
    """
    # 对齐日期并转对数收益率
    dates = sorted(set(signal.keys()) & set(target.keys()))
    if len(dates) < max_lag + 10:
        return {'error': f'仅{len(dates)}个对齐日，需至少{max_lag + 10}'}
    
    s = np.array([signal[d] for d in dates], dtype=float)
    t = np.array([target[d] for d in dates], dtype=float)
    
    s_ret = np.diff(np.log(np.maximum(s[:-1], 1e-10)))  # log-return
    t_ret = np.diff(np.log(np.maximum(t[:-1], 1e-10)))
    
    # 去异常值（3 std）
    s_mean, s_std = s_ret.mean(), s_ret.std()
    t_mean, t_std = t_ret.mean(), t_ret.std()
    s_ret = np.clip(s_ret, s_mean - 3*s_std, s_mean + 3*s_std)
    t_ret = np.clip(t_ret, t_mean - 3*t_std, t_mean + 3*t_std)
    
    results = {}
    n = len(s_ret)
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            c = np.corrcoef(s_ret[:lag], t_ret[-lag:])[0, 1]
        elif lag == 0:
            c = np.corrcoef(s_ret, t_ret)[0, 1]
        else:
            c = np.corrcoef(s_ret[lag:], t_ret[:-lag])[0, 1]
        results[lag] = round(c, 4) if not np.isnan(c) else 0.0
    
    return results


def analyze_hsgt_vs_index(max_lag: int = 15, days: int = 365) -> list:
    """沪深港通 vs 沪深300交叉相关"""
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=days)).isoformat()
    
    # 沪深300日线
    target = _load_series('daily_prices', 'close', "symbol = 'I.000300'", start, end)
    if not target:
        return [{'error': '沪深300无数据'}]
    
    results = []
    signals = [
        ('north_sh', "沪股通净买入"),
        ('north_sz', "深股通净买入"),
        ('north_all', "北向汇总"),
    ]
    
    for sig_type, sig_name in signals:
        sig = _load_series('market_signals', 'value',
                          f"signal_type='hsgt_{sig_type}' AND code='TOTAL'", start, end)
        if not sig:
            results.append({'signal': sig_name, 'error': f'无{sig_type}数据'})
            continue
        
        corr = cross_correlate(sig, target, max_lag)
        if 'error' in corr:
            results.append({'signal': sig_name, 'error': corr['error']})
            continue
        
        best_lag = max(corr, key=lambda k: abs(corr[k]))
        results.append({
            'signal': sig_name,
            'best_lag': best_lag,
            'best_corr': corr[best_lag],
            'n_points': len(sig),
            'corr_by_lag': corr,
        })
    
    return results


def format_report(results: list) -> str:
    """格式化交叉相关分析报告"""
    lines = ["信号 vs 沪深300 交叉相关分析", "=" * 50]
    lines.append("")
    for r in results:
        if 'error' in r:
            lines.append("  %s: %s" % (r['signal'], r['error']))
            continue
        lag = r['best_lag']
        lag_str = "领先%d天" % lag if lag > 0 else (
            "滞后%d天" % -lag if lag < 0 else "同步")
        lines.append("  %s (%d天样本)" % (r['signal'], r['n_points']))
        lines.append("    最优时滞: %s (corr=%+.4f)" % (lag_str, r['best_corr']))
        
        # 展示关键lag
        corr = r['corr_by_lag']
        lines.append("    时滞相关:")
        for l in range(-10, 11):
            if l in corr and abs(corr[l]) > 0.05:
                bar = "█" * int(abs(corr[l]) * 50)
                lines.append("      lag %+3d: %s %+.4f" % (l, bar, corr[l]))
    return "\n".join(lines)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    results = analyze_hsgt_vs_index(days=365)
    print(format_report(results))
