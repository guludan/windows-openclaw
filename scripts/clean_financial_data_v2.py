"""
clean_financial_data_v2.py — 批量清洗 financial_data
1. 反算每股值（2026Q1 校准，清洗历史总量→每股量）
2. 加 total_shares 列
3. 输出缺失财报的股票清单
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
from datetime import date
from collections import defaultdict

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
conn = psycopg2.connect(DB)
cur = conn.cursor()

print("=" * 60)
print("financial_data 清洗 v2")
print("=" * 60)

# ── 1. 统计现状 ──
cur.execute("SELECT COUNT(*) FROM financial_data")
total_rows = cur.fetchone()[0]
cur.execute("SELECT COUNT(DISTINCT symbol) FROM financial_data")
total_stocks = cur.fetchone()[0]
print(f"\n[1] 现状: {total_rows} 行, {total_stocks} 只股票")

cur.execute("""
    SELECT MIN(end_date) as min_e, MAX(end_date) as max_e FROM financial_data
""")
r = cur.fetchone()
print(f"  季度范围: {r[0]} ~ {r[1]}")

# ── 2. 加载 2026Q1 校准数据 ──
print("\n[2] 加载 2026Q1 校准数据...")
cur.execute("""
    SELECT symbol, eps, net_profit, bvps
    FROM financial_data
    WHERE end_date = '2026-03-31'
      AND eps IS NOT NULL AND eps > 0
      AND net_profit IS NOT NULL AND net_profit > 0
""")
calib = {r[0]: {'eps': r[1], 'np': r[2], 'bvps': r[3]} for r in cur.fetchall()}

# ── 3. 计算总股本并清洗 ──
print("\n[3] 计算总股本 + 清洗...")
# 加列
cur.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='financial_data' AND column_name='total_shares'
        ) THEN
            ALTER TABLE financial_data ADD COLUMN total_shares DOUBLE PRECISION;
            ALTER TABLE financial_data ADD COLUMN eps_clean DOUBLE PRECISION;
            ALTER TABLE financial_data ADD COLUMN bvps_clean DOUBLE PRECISION;
        END IF;
    END $$;
""")
conn.commit()

# 批量更新所有行
updates = []  # (total_shares, eps_clean, bvps_clean, symbol, end_date)
no_eps_hist = 0
calib_count = 0

for code, data in calib.items():
    shares = data['np'] / data['eps']  # 总股本
    bvps_q1 = data['bvps']
    
    # 2026Q1 本身正常
    cur.execute("""
        SELECT end_date, eps, bvps FROM financial_data
        WHERE symbol = %s ORDER BY end_date
    """, (code,))
    
    has_hist = False
    for r in cur.fetchall():
        ed = r[0]; old_eps = r[1]; old_bvps = r[2]
        
        eps_clean = old_eps
        bvps_clean = old_bvps
        
        # 总量判断：如果 eps > shares（总量），或 eps > 100（不像每股值）
        if old_eps is not None and old_eps > 0:
            if old_eps > shares or old_eps > 1000:
                eps_clean = old_eps / shares
                has_hist = True
        if old_bvps is not None and old_bvps > shares:
            bvps_clean = old_bvps / shares
            has_hist = True
        
        updates.append((shares, eps_clean, bvps_clean, code, ed))
    
    if not has_hist:
        no_eps_hist += 1
    calib_count += 1
    
    if calib_count % 500 == 0:
        print(f"    {calib_count}/{len(calib)} 只已处理...")

print(f"  总清洗行: {len(updates)}")

# 批量写入临时表再 UPDATE
if updates:
    cur.execute("DROP TABLE IF EXISTS _fin_clean_temp")
    cur.execute("""
        CREATE TEMP TABLE _fin_clean_temp (
            symbol TEXT, end_date DATE,
            total_shares DOUBLE PRECISION, eps_clean DOUBLE PRECISION, bvps_clean DOUBLE PRECISION
        )
    """)
    conn.commit()
    
    # 分批写入临时表
    batch_size = 5000
    for i in range(0, len(updates), batch_size):
        batch = updates[i:i+batch_size]
        # 注意列序: (symbol, end_date, total_shares, eps_clean, bvps_clean)
        args = ','.join(cur.mogrify(
            "(%s,%s::date,%s,%s,%s)", (u[3], u[4], u[0], u[1], u[2])
        ).decode('utf-8') for u in batch)
        cur.execute(f"INSERT INTO _fin_clean_temp VALUES {args}")
    conn.commit()
    print(f"  写入临时表完成")
    
    # 一次 UPDATE JOIN
    cur.execute("""
        UPDATE financial_data fd
        SET total_shares = t.total_shares,
            eps_clean = t.eps_clean,
            bvps_clean = t.bvps_clean
        FROM _fin_clean_temp t
        WHERE fd.symbol = t.symbol AND fd.end_date = t.end_date
    """)
    conn.commit()
    print(f"  UPDATE 完成: {cur.rowcount} 行")

# ── 4. 验证 ──
print("\n[4] 清洗验证:")
cur.execute("""
    SELECT fd.symbol, fd.end_date::text, fd.total_shares,
           fd.eps_clean, fd.bvps_clean,
           dp.close,
           CASE WHEN fd.eps_clean IS NOT NULL AND fd.eps_clean > 0 AND fd.eps_clean < 1000 
                THEN ROUND((dp.close / fd.eps_clean)::numeric, 2) 
                ELSE NULL END as implied_pe,
           CASE WHEN fd.bvps_clean IS NOT NULL AND fd.bvps_clean > 0 AND fd.bvps_clean < 1000 
                THEN ROUND((dp.close / fd.bvps_clean)::numeric, 2) 
                ELSE NULL END as implied_pb
    FROM financial_data fd
    JOIN daily_prices dp ON dp.symbol = 'A.' || fd.symbol AND dp.trade_date = fd.end_date
    WHERE fd.symbol IN ('000001', '600519', '000002')
      AND dp.close > 0
    ORDER BY fd.symbol, fd.end_date
""")
print(f"{'symbol':>8s} {'end_date':12s} {'总股本':>12s} {'eps':>8s} {'bvps':>8s} {'close':>8s} {'PE':>8s} {'PB':>8s}")
for r in cur.fetchall():
    print(f"  {r[0]:>8s} {r[1]:12s} {str(r[2]):>12s} {str(r[3]):>8s} {str(r[4]):>8s} {r[5]:>8.2f} {str(r[6]):>8s} {str(r[7]):>8s}")

# ── 5. 全量 PE/PB 合理性统计 ──
print("\n[5] PE/PB 合理性统计:")
cur.execute("""
    WITH pe_pb AS (
        SELECT fd.symbol, fd.end_date,
               dp.close / fd.eps_clean as pe,
               dp.close / fd.bvps_clean as pb
        FROM financial_data fd
        JOIN daily_prices dp ON dp.symbol = 'A.' || fd.symbol AND dp.trade_date = fd.end_date
        WHERE fd.eps_clean IS NOT NULL AND fd.eps_clean > 0 AND fd.eps_clean < 1000
          AND fd.bvps_clean IS NOT NULL AND fd.bvps_clean > 0
          AND dp.close > 0
    )
    SELECT COUNT(*) as total,
           COUNT(*) FILTER(WHERE pe > 0 AND pe < 200) as pe_ok,
           COUNT(*) FILTER(WHERE pb > 0 AND pb < 50) as pb_ok,
           ROUND(AVG(pe)::numeric, 2) as avg_pe,
           ROUND(AVG(pb)::numeric, 2) as avg_pb
    FROM pe_pb
""")
r = cur.fetchone()
total = r[0]
if total > 0:
    pe_ok = r[1]
    pb_ok = r[2]
    print(f"  总样本: {total}")
    print(f"  PE 合理(0~200): {pe_ok} ({pe_ok*100/total:.1f}%)")
    print(f"  PB 合理(0~50):  {pb_ok} ({pb_ok*100/total:.1f}%)")
    print(f"  平均 PE: {r[3]}")
    print(f"  平均 PB: {r[4]}")
    
    if pe_ok/total > 0.5 and pb_ok/total > 0.5:
        print(f"\n✅ 清洗有效：PE {pe_ok*100/total:.0f}% + PB {pb_ok*100/total:.0f}% 合理")
    else:
        print(f"\n❌ 清洗不足：PE/PB 合理率过低，考虑弃用")

# ── 6. 缺失财报的股票 ──
print("\n[6] 财报缺失分析:")
cur.execute("""
    SELECT sl.code, sl.name
    FROM stock_list sl
    WHERE sl.active = TRUE
      AND sl.code NOT IN (
          SELECT DISTINCT symbol FROM financial_data
      )
    ORDER BY sl.code
""")
missing = cur.fetchall()
print(f"  完全没有财报数据的股票: {len(missing)} 只")
if missing:
    print(f"  示例: {missing[:5]}")

# 缺最新季度的
cur.execute("""
    SELECT sl.code, sl.name
    FROM stock_list sl
    LEFT JOIN financial_data fd ON fd.symbol = 'A.' || sl.code AND fd.end_date = '2026-03-31'
    WHERE sl.active = TRUE AND fd.symbol IS NULL
    ORDER BY sl.code
""")
missing_q1 = cur.fetchall()
print(f"  缺 2026Q1 数据的股票: {len(missing_q1)} 只")

conn.close()
print(f"\n✅ 完成")
