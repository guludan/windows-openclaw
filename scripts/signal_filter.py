"""除权除息假信号过滤器 — 挂在信号检测前"""
import numpy as np

def detect_exright(rows, idx, stock_code=''):
    """
    判定 idx 位置是否除权/除息/复牌等假信号
    
    参数:
        rows: list of (close, turnover) 或 (trade_date, close, turnover) 按日期升序
        idx: 当前交易日索引
        stock_code: 股票代码（用于判断涨跌幅阈值）
    
    返回:
        reason: None=正常信号, str=排除原因
    """
    if idx < 1 or idx >= len(rows):
        return None
    
    # 兼容两种格式
    if len(rows[idx]) >= 3:
        c0 = rows[idx][1]
        c1 = rows[idx-1][1]
    else:
        c0 = rows[idx][0]
        c1 = rows[idx-1][0]
    
    if c0 is None or c1 is None or c1 <= 0:
        return None
    
    ret = c0 / c1 - 1
    abs_ret = abs(ret)
    
    # --- 规则1: 极端涨跌幅 ---
    # 辅助函数
    def _close(i): return rows[i][1] if len(rows[i])>=3 else rows[i][0]
    def _turn(i): 
        if len(rows[i])>=3: return rows[i][2] or 0
        return 0
    
    # --- 规则1: 极端涨跌幅 ---
    if stock_code and stock_code[0] == '3':
        threshold = 0.25
    elif stock_code and stock_code[0] in ('4','8'):
        threshold = 0.40
    else:
        threshold = 0.20
    
    if abs_ret > threshold:
        return f"EXRIGHT: ret={ret:+.2%} exceeds {threshold*100:.0f}% threshold"
    
    # --- 规则2: 停牌后复牌 ---
    if idx >= 2:
        recent_to = _turn(idx-1)
        if recent_to < 0.001 and abs_ret > 0.15:
            return f"EXRIGHT: halt-resume ret={ret:+.2%}, zero volume prev day"
    
    # --- 规则3: 无量异常波动 ---
    to_today = _turn(idx) if idx >= 0 else 0
    if to_today > 0:
        to20 = [_turn(idx-k-1) for k in range(20) if idx-k-1 >= 0 and _turn(idx-k-1) > 0]
        if len(to20) >= 5:
            avg_to20 = np.mean(to20)
            if avg_to20 > 0 and to_today / avg_to20 < 0.3 and abs_ret > 0.15:
                return f"EXRIGHT: anemic vol ret={ret:+.2%} turn={to_today:.2f} vs ma20={avg_to20:.2f} (ratio={to_today/avg_to20:.2f})"
    
    # --- 规则4: 组合判断 ---
    if idx >= 5:
        c5 = _close(idx-5) if idx-5 >= 0 else None
        if c5 and c5 > 0:
            cum_ret = c0 / c5 - 1
            if abs_ret > 0.10 and cum_ret < 0.15 and abs_ret / max(abs(cum_ret), 0.001) > 5:
                return f"EXRIGHT: ret={ret:+.2%} vs cum5={cum_ret:+.2%} ratio={abs_ret/max(abs(cum_ret),0.001):.1f}x"
    
    return None


def filter_signals(rows, idx, sig_types):
    """
    对信号进行除权过滤
    
    返回:
        (filtered_types, reason): 过滤后的信号列表和排除原因
    """
    if not sig_types:
        return sig_types, None
    
    reason = detect_exright(rows, idx)
    if reason:
        return [], reason
    
    return sig_types, None
