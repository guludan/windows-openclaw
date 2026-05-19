#!/usr/bin/env python3
"""用 baostock 回填日线（腾讯接口失败的股票走 baostock）"""
import sys, os, time, logging, json
from datetime import datetime, date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data_finance as df

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backfill_bs")

import baostock as bs

def main():
    lg = bs.login()
    if lg.error_code != '0':
        log.error(f"baostock login failed: {lg.error_msg}")
        return

    try:
        # 找出所有已有 code（去重）
        rows, _ = df._pg_execute("SELECT DISTINCT symbol FROM daily_prices")
        existing = set()
        for r in rows:
            sym = r[0].strip()
            if sym.startswith("A.") and len(sym) == 8:
                existing.add(sym[2:])
            elif sym.isdigit() and len(sym) == 6:
                existing.add(sym)
            elif '.' in sym and len(sym.split('.')[-1]) == 6:
                existing.add(sym.split('.')[-1])

        # 从 stock_list 取所有 active 股票
        codes = df.get_active_codes()
        # 也看 stock_list 表
        rows_sl, _ = df._pg_execute("SELECT DISTINCT code FROM stock_list")
        sl_codes = [r[0] for r in rows_sl]
        all_codes = list(set(codes + sl_codes))

        # 过滤出需要补的
        need = [c for c in all_codes if c not in existing]
        log.info(f"已有 {len(existing)} 只, 需要补 {len(need)} 只（从 {len(all_codes)} 候选）")

        if not need:
            log.info("✅ 全部已有日线")
            return

        success, failed = 0, 0
        batch = []
        total = len(need)
        t0 = time.time()

        for idx, code in enumerate(need):
            prefix = "sh" if code.startswith(("5","6","9")) else "sz"
            bs_code = f"{prefix}.{code}"
            try:
                rs = bs.query_history_k_data_plus(
                    bs_code, 'date,open,high,low,close,volume',
                    start_date='2023-01-01', end_date=date.today().isoformat(),
                    frequency='d', adjustflag='2'
                )
                rows_raw = []
                while rs.next():
                    rows_raw.append(rs.get_row_data())
                if len(rows_raw) > 0:
                    for row in rows_raw:
                        d, o, h, l, c, v = row
                        if d == 'None': continue
                        try:
                            batch.append((code, d, float(o), float(h), float(l), float(c), float(v), "baostock"))
                        except (ValueError, TypeError):
                            pass
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1

            if idx > 0 and idx % 200 == 0:
                elapsed = time.time() - t0
                log.info(f"  进度 {idx}/{total}, 成功={success}, 失败={failed}, {idx/elapsed:.1f}只/秒")
                if batch:
                    df._pg_executemany(
                        "INSERT INTO daily_prices (symbol, trade_date, open, high, low, close, volume, source) VALUES %s "
                        "ON CONFLICT (symbol, trade_date) DO UPDATE SET close=EXCLUDED.close",
                        batch,
                        template="(%s, %s::date, %s, %s, %s, %s, %s, %s)"
                    )
                    log.info(f"  入库 {len(batch)} 行")
                    batch = []

        # 最后一批
        if batch:
            df._pg_executemany(
                "INSERT INTO daily_prices (symbol, trade_date, open, high, low, close, volume, source) VALUES %s "
                "ON CONFLICT (symbol, trade_date) DO UPDATE SET close=EXCLUDED.close",
                batch,
                template="(%s, %s::date, %s, %s, %s, %s, %s, %s)"
            )

        elapsed = time.time() - t0
        log.info(f"\n✅ baostock 回填完成: {success} 成功, {failed} 失败 ({elapsed:.0f}s)")
    finally:
        bs.logout()

if __name__ == "__main__":
    main()
