#!/usr/bin/env python3
"""拉取 A股季度财务数据（最新2年）到 PG"""
import sys, os, time, logging
import baostock as bs
import psycopg2.extras as pge

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data_finance as df

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fetch_finance")

def main():
    lg = bs.login()
    if lg.error_code != "0":
        log.error(f"baostock login failed: {lg.error_msg}")
        return

    try:
        rows, _ = df._pg_execute("SELECT DISTINCT symbol FROM financial_data")
        existing = set(r[0] for r in rows)
        log.info(f"已有财务数据的股票: {len(existing)} 只")

        all_codes = df.get_active_codes()
        need = [c for c in all_codes if c not in existing]
        log.info(f"需补财报的股票: {len(need)} 只（总{len(all_codes)}只）")

        total_rows = 0
        failed = []

        for idx, code in enumerate(need):
            bs_code = ("sh." if code.startswith("6") else "sz.") + code

            if idx % 20 == 0:
                log.info(f"进度 {idx}/{len(need)}, 已写入 {total_rows} 行")

            try:
                for year in [2024, 2025]:
                    for quarter in [1, 2, 3, 4]:
                        rs = bs.query_profit_data(code=bs_code, year=str(year), quarter=str(quarter))
                        batch = []
                        while rs.next():
                            row = rs.get_row_data()
                            try:
                                batch.append((
                                    code, row[2], row[1],
                                    float(row[3]) if row[3] else None,
                                    float(row[4]) if row[4] else None,
                                    float(row[5]) if row[5] else None,
                                    float(row[6]) if row[6] else None,
                                    float(row[7]) if row[7] else None,
                                    float(row[8]) if row[8] else None,
                                    float(row[9]) if row[9] else None,
                                    quarter, year
                                ))
                            except:
                                pass
                        if batch:
                            conn = df._get_pg()
                            cur = conn.cursor()
                            pge.execute_values(cur,
                                "INSERT INTO financial_data (symbol, end_date, publish_date, roe, net_profit, "
                                "net_profit_yoy, revenue, revenue_yoy, eps, bvps, quarter_type, year) "
                                "VALUES %s ON CONFLICT (symbol, end_date) DO UPDATE SET "
                                "roe=EXCLUDED.roe, net_profit=EXCLUDED.net_profit, revenue=EXCLUDED.revenue, "
                                "eps=EXCLUDED.eps, bvps=EXCLUDED.bvps, publish_date=EXCLUDED.publish_date",
                                batch, page_size=500)
                            conn.commit()
                            cur.close()
                            total_rows += len(batch)
            except Exception as e:
                failed.append(code)
                log.warning(f"  {bs_code} 失败: {e}")
                try:
                    bs.logout()
                except:
                    pass
                lg = bs.login()
                time.sleep(2)

        log.info(f"\n✅ 完成！总写入: {total_rows} 行, 失败: {len(failed)} 只")
        if failed:
            log.info(f"失败股票(前10): {failed[:10]}")
    finally:
        bs.logout()

if __name__ == "__main__":
    main()
