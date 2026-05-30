"""
clean_financial_data.py — 清洗 financial_data 表

问题：2024-2025 年的 eps/bvps 存的是总量不是每股值
      2026Q1 是正常的每股值

清洗方法：
  1. 对每只股票，取 2026-03-31 的 eps 和 net_profit
  2. 反算总股本 = net_profit / eps（注意 net_profit 可能是季度值）
  3. 用总股本修正所有历史行：eps = eps_old / shares，bvps = bvps_old / shares
  4. 写入新列 eps_clean, bvps_clean

如果清洗后 PE/PB 仍不合理（负值或极端值），则弃用 PE/PB 维度。
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import numpy as np
from datetime import date

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
conn = psycopg2.connect(DB)
cur = conn.cursor()

print("=" * 60)
print("financial_data 清洗")
print("=" * 60)

# 1. 统计所有股票
cur.execute("SELECT DISTINCT symbol FROM financial_data")
symbols = [r[0] for r in cur.fetchall()]
print(f"\n[1] 总股票数: {len(symbols)}")

# 2. 找到有 2026Q1 数据的股票（用于校准）
cur.execute("""
    SELECT symbol, eps, net_profit, bvps
    FROM financial_data
    WHERE end_date = '2026-03-31'
      AND eps IS NOT NULL AND eps > 0
      AND net_profit IS NOT NULL
    ORDER BY symbol
""")
calib_rows = {r[0]: {'eps': r[1], 'net_profit': r[2], 'bvps': r[3]} for r in cur.fetchall()}
print(f"  有 2026Q1 校准数据的股票: {len(calib_rows)}")

# 3. 反算总股本
# eps_Q1 = 一季度的每股收益，net_profit_Q1 = 一季度总净利
# 理论上总股本 = net_profit_Q1 / eps_Q1
for code, data in list(calib_rows.items()):
    eps_q1 = data['eps']
    np_q1 = data['net_profit']
    if np_q1 and np_q1 > 0 and eps_q1 and eps_q1 > 0:
        shares = np_q1 / eps_q1
        data['shares'] = shares  # 总股本
    else:
        data['shares'] = None

# 4. 对有 2026Q1 数据的股票清洗历史数据
cleanable = sum(1 for d in calib_rows.values() if d.get('shares'))
print(f"  可清洗（有总股本）: {cleanable}")

updated = failed = 0
for code, data in calib_rows.items():
    if 'shares' not in data or not data['shares']:
        failed += 1
        continue
    
    shares = data['shares']
    
    # 2026Q1 本身是正常的，不需要修正
    cur.execute("""
        SELECT end_date, eps, bvps
        FROM financial_data
        WHERE symbol = %s AND end_date < '2026-03-31'
          AND (eps IS NOT NULL OR bvps IS NOT NULL)
        ORDER BY end_date
    """, (code,))
    
    for r in cur.fetchall():
        ed = r[0]; old_eps = r[1]; old_bvps = r[2]
        eps_clean = old_eps / shares if old_eps is not None and old_eps > shares else old_eps
        bvps_clean = old_bvps / shares if old_bvps is not None and old_bvps > shares else old_bvps
        
        # 写入临时列
        if (eps_clean != old_eps) or (bvps_clean != old_bvps):
            conn.rollback()  # 重置事务
            cur.execute("""
                UPDATE financial_data
                SET eps = %s, bvps = %s
                WHERE symbol = %s AND end_date = %s
            """, (eps_clean, bvps_clean, code, ed))
            conn.commit()
            updated += 1

print(f"  已更新: {updated} 行, 跳过: {failed} 只")

# 5. 验证清洗结果
cur.execute("""
    SELECT fd.symbol, fd.end_date::text, fd.eps, fd.bvps, dp.close,
           CASE WHEN fd.eps > 0 AND fd.eps < 1000 
                THEN ROUND((dp.close / fd.eps)::numeric, 2) 
                ELSE NULL END as implied_pe,
           CASE WHEN fd.bvps > 0 
                THEN ROUND((dp.close / fd.bvps)::numeric, 2) 
                ELSE NULL END as implied_pb
    FROM financial_data fd
    JOIN daily_prices dp ON dp.symbol = 'A.' || fd.symbol AND dp.trade_date = fd.end_date
    WHERE fd.symbol IN ('000001', '600519')
      AND dp.close > 0
    ORDER BY fd.symbol, fd.end_date
""")
print(f"\n[2] 清洗验证:")
print(f"{'symbol':>8s} {'end_date':12s} {'eps_clean':>10s} {'bvps_clean':>10s} {'close':>8s} {'PE':>8s} {'PB':>8s}")
for r in cur.fetchall():
    pe_str = str(r[5]) if r[5] else 'N/A'
    pb_str = str(r[6]) if r[6] else 'N/A'
    print(f"  {r[0]:>8s} {r[1]:12s} {r[2]:>10.4f} {r[3]:>10.2f} {r[4]:>8.2f} {pe_str:>8s} {pb_str:>8s}")

# 6. 整体统计：清洗后的 PE/PB 合理比例
cur.execute("""
    WITH pe_pb AS (
        SELECT fd.symbol, fd.end_date,
               CASE WHEN fd.eps > 0 AND fd.eps < 1000 THEN dp.close / fd.eps ELSE NULL END as pe,
               CASE WHEN fd.bvps > 0 AND fd.bvps < 1000 THEN dp.close / fd.bvps ELSE NULL END as pb
        FROM financial_data fd
        JOIN daily_prices dp ON dp.symbol = 'A.' || fd.symbol AND dp.trade_date = fd.end_date
        WHERE dp.close > 0 AND fd.eps IS NOT NULL AND fd.bvps IS NOT NULL
    )
    SELECT COUNT(*) as total,
           COUNT(*) FILTER(WHERE pe > 0 AND pe < 200) as pe_ok,
           COUNT(*) FILTER(WHERE pe >= 200 OR pe <= 0) as pe_bad,
           COUNT(*) FILTER(WHERE pb > 0 AND pb < 50) as pb_ok,
           COUNT(*) FILTER(WHERE pb >= 50 OR pb <= 0) as pb_bad
    FROM pe_pb
""")
r = cur.fetchone()
total = r[0]
print(f"\n[3] 清洗后 PE/PB 合理性:")
print(f"  总样本: {total}")
print(f"  PE 合理(0~200): {r[1]} ({r[1]*100/total:.1f}%)")
print(f"  PE 不合理: {r[2]} ({r[2]*100/total:.1f}%)") if total else print()
print(f"  PB 合理(0~50): {r[3]} ({r[3]*100/total:.1f}%)")
print(f"  PB 不合理: {r[4]} ({r[4]*100/total:.1f}%)") if total else print()

conn.close()

# 决策
if total > 0 and r[1] / total > 0.5:
    print(f"\n✅ 清洗有效：PE 合理性 {r[1]*100/total:.1f}%")
    print("  可以继续使用 PE/PB 作为 V7 因子")
else:
    print(f"\n❌ 清洗失败：PE 合理性仅 {r[1]*100/total:.1f}%")
    print("  弃用 PE/PB 维度")
