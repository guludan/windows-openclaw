#!/usr/bin/env python3
"""
backfill_limit_up.py — 从 daily_prices 回算涨停数据写入 daily_limit_up

用法: python3 scripts/backfill_limit_up.py [--start 2026-05-26] [--end 2026-05-26]
默认: 从最后一条数据日期往后补
"""
import sys, os, time, logging, argparse
from datetime import date, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("limit_up")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import data_finance as df

def calc_limit_pct(code: str) -> float:
    """判断涨停百分比阈值"""
    if code.startswith('688') or code.startswith('3'):
        return 0.198
    return 0.098

def backfill_single_day(td: str):
    """回填一日涨停数据"""
    log.info(f"  回填 {td}")
    
    # 获取当日所有日线
    rows, _ = df._pg_execute("""
        SELECT d_now.symbol, d_now.close, d_now.open, d_now.high, d_now.low,
               d_now.volume, d_now.amount, d_prev.close as prev_close
        FROM daily_prices d_now
        JOIN daily_prices d_prev 
          ON d_prev.symbol = d_now.symbol 
         AND d_prev.trade_date = (
             SELECT MAX(trade_date) FROM daily_prices 
             WHERE symbol = d_now.symbol AND trade_date < %s::date
         )
        WHERE d_now.trade_date = %s::date
          AND d_now.symbol LIKE 'A.%%'
    """, (td, td))
    
    count = 0
    for r in rows:
        sym, close, open_p, high, low, vol, amt, prev_close = r
        if not prev_close or prev_close <= 0:
            continue
        code = sym.split('.')[1]
        pct = (close - prev_close) / prev_close
        limit_pct = calc_limit_pct(code)
        is_up = pct >= limit_pct - 0.001  # 浮点容差
        amp = (high - low) / prev_close if prev_close else 0
        
        if not is_up:
            continue
        
        # 判断是否ST（跌幅阈值判断，简化：看pct是否接近5%）
        is_star = abs(pct - 0.05) < 0.005 or abs(pct - (-0.05)) < 0.005
        
        # 前30天涨停次数
        rows30, _ = df._pg_execute(
            "SELECT COUNT(*) FROM daily_limit_up WHERE code=%s AND trade_date>=%s::date AND is_limit_up=true",
            (code, (date.fromisoformat(td) - timedelta(days=30)).isoformat()))
        prev_ban_30d = rows30[0][0] if rows30 else 0
        
        # 行业（从industry_components_ths_l1 取）
        ind_rows, _ = df._pg_execute(
            "SELECT industry FROM industry_components_ths_l1 WHERE stock_code=%s LIMIT 1",
            (code,))
        industry = ind_rows[0][0] if ind_rows else None
        
        # 估算市值：close * ? 用 amount/volume 算均价 * 流通股没法算，先null
        est_mv = None
        
        # 相空间
        phase_rows, _ = df._pg_execute(
            "SELECT phase FROM pool_energy WHERE level='market' AND date=%s::date",
            (td,))
        phase = phase_rows[0][0] if phase_rows else None
        
        df._pg_execute(
            """INSERT INTO daily_limit_up 
               (trade_date, code, close, ret_0, amplitude, volume, 
                is_limit_up, is_star, limit_pct, prev_close, 
                prev_ban_30d, industry, est_mv, phase)
               VALUES (%s::date,%s,%s,%s,%s,%s,true,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (trade_date, code) DO NOTHING""",
            (td, code, close, pct, amp, vol,
             is_star, limit_pct, prev_close,
             prev_ban_30d, industry, est_mv, phase),
            fetch=False)
        count += 1
    
    log.info(f"    {td}: {count} 条涨停写入")
    return count

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', type=str)
    parser.add_argument('--end', type=str)
    args = parser.parse_args()
    
    # 确定开始日期
    if args.start:
        start = date.fromisoformat(args.start)
    else:
        rows, _ = df._pg_execute("SELECT MAX(trade_date)::text FROM daily_limit_up")
        last = rows[0][0] if rows and rows[0][0] else '2024-01-01'
        start = date.fromisoformat(last) + timedelta(days=1)
    
    if args.end:
        end = date.fromisoformat(args.end)
    else:
        rows, _ = df._pg_execute("SELECT MAX(trade_date)::text FROM daily_prices WHERE symbol LIKE 'A.%%'")
        end = date.fromisoformat(rows[0][0])
    
    log.info(f"涨停回填: {start.isoformat()} ~ {end.isoformat()}")
    total = 0
    d = start
    while d <= end:
        # 检查是否交易日
        rows, _ = df._pg_execute(
            "SELECT COUNT(*) FROM daily_prices WHERE trade_date=%s::date AND symbol LIKE 'A.%%'",
            (d.isoformat(),))
        if rows and rows[0][0] > 1000:  # 有交易
            total += backfill_single_day(d.isoformat())
        d += timedelta(days=1)
    
    log.info(f"\n✅ 涨停回填完成: 共 {total} 条")

if __name__ == '__main__':
    main()
