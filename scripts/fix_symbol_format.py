"""
统一 daily_prices.symbol 格式修复
纯6位数字symbol → A.前缀（与历史格式一致）
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
from datetime import datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

def main():
    conn = psycopg2.connect(DB)
    conn.autocommit = False
    cur = conn.cursor()
    
    print("="*60)
    print(f"symbol 统一修复", datetime.now().strftime('%H:%M'))
    print("="*60)
    
    # 1) 统计
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT symbol) FROM daily_prices WHERE symbol ~ '^[0-9]{6}$'")
    pure_rows, pure_stocks = cur.fetchone()
    print(f"纯6位数字: {pure_rows}条, {pure_stocks}只")
    
    # 2) 备份
    print("备份纯6位数据...")
    cur.execute("DROP TABLE IF EXISTS daily_prices_bak")
    cur.execute("CREATE TABLE daily_prices_bak (LIKE daily_prices)")
    cur.execute("INSERT INTO daily_prices_bak SELECT * FROM daily_prices WHERE symbol ~ '^[0-9]{6}$'")
    conn.commit()
    print(f"  已备份 {pure_rows}条")
    
    # 3) 先删掉目标表中会被UPDATE冲突的行
    print("删除冲突的现有A.前缀记录...")
    cur.execute("""
        DELETE FROM daily_prices d
        WHERE d.symbol LIKE 'A.______'
        AND (substring(d.symbol FROM 3), d.trade_date) IN (
            SELECT p.symbol, p.trade_date
            FROM daily_prices p WHERE p.symbol ~ '^[0-9]{6}$'
        )
    """)
    del_cnt = cur.rowcount
    conn.commit()
    print(f"  删除冲突: {del_cnt}行")
    
    # 4) UPDATE
    print("UPDATE symbol → A.前缀...")
    cur.execute("UPDATE daily_prices SET symbol = 'A.' || symbol WHERE symbol ~ '^[0-9]{6}$'")
    upd_cnt = cur.rowcount
    conn.commit()
    print(f"  UPDATE: {upd_cnt}行")
    
    # 5) 修复pool_energy — 先删可能冲突
    print("修复pool_energy...")
    cur.execute("""
        DELETE FROM pool_energy e1
        WHERE e1.level LIKE 'code:______' AND e1.level NOT LIKE 'code:A.%'
        AND (('code:A.' || substring(e1.level, 6), e1.date) IN (
            SELECT level, date FROM pool_energy WHERE level LIKE 'code:A.%'
        ))
    """)
    print(f"  删除重复pool_energy: {cur.rowcount}行")
    conn.commit()
    
    cur.execute("""
        UPDATE pool_energy 
        SET level = 'code:A.' || substring(level, 6)
        WHERE level LIKE 'code:______' AND level NOT LIKE 'code:A.%'
    """)
    print(f"  pool_energy更新: {cur.rowcount}行")
    conn.commit()
    
    # 6) 验证
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT symbol) FROM daily_prices WHERE symbol ~ '^[0-9]{6}$'")
    r = cur.fetchone()
    print(f"\n纯6位残: {r[0]}条, {r[1]}只")
    
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT symbol) FROM daily_prices WHERE symbol LIKE 'A.______'")
    r = cur.fetchone()
    print(f"A.前缀: {r[0]}条, {r[1]}只")
    
    # 中国石化
    cur.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM daily_prices WHERE symbol = 'A.600028'")
    r = cur.fetchone()
    print(f"A.600028: {r[0]}条, {r[1]}~{r[2]}")
    
    conn.close()
    print("✅ 修复完成")

if __name__ == '__main__':
    main()
