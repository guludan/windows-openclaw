#!/usr/bin/env python3
"""
A股历史数据全面回补（baostock）
=================================
拉取2015~2024日线 + 补全amount/turnover + 季度利润数据

策略：
- 自动循环，断网重试60秒
- 每100只一批
- 数据写入PG

用法:
  python3 scripts/backfill_history.py           # 自动续跑
  python3 scripts/backfill_history.py status     # 查看进度
  python3 scripts/backfill_history.py reset      # 重置状态
"""
import sys, os, logging, time, json, datetime
import baostock as bs
import psycopg2.extras as pge
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import data_finance as df

log = logging.getLogger('backfill')
STATUS_FILE = '/mnt/d/data/backfill_status.json'
BATCH_SIZE = 100

# ========== 表结构 ==========

FINANCIAL_DDL = """
CREATE TABLE IF NOT EXISTS financial_data (
    symbol text NOT NULL,
    end_date date NOT NULL,          -- 财报截止日（如2024-12-31）
    publish_date date,               -- 发布日期
    roe double precision,            -- 净资产收益率
    net_profit double precision,     -- 净利润
    net_profit_yoy double precision, -- 净利润同比
    revenue double precision,        -- 营业总收入
    revenue_yoy double precision,    -- 营收同比
    eps double precision,            -- 每股收益
    bvps double precision,           -- 每股净资产
    quarter_type integer,            -- 1=Q1, 2=中报, 3=Q3, 4=年报
    year integer,
    updated_at timestamptz DEFAULT now(),
    PRIMARY KEY (symbol, end_date)
)
"""

# ========== 状态管理 ==========

def load_status():
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except:
        return {'last_idx': 0, 'completed_hist': [], 'completed_finance': [],
                'rows_hist': 0, 'rows_finance': 0, 'failed_hist': [], 'failed_finance': []}

def save_status(st):
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, 'w') as f:
        json.dump(st, f, ensure_ascii=False, indent=2)

def symbol_map(code):
    return 'A.' + code.split('.')[1]

# ========== baostock 交互 ==========

def baostock_login(max_retries=5):
    """登录baostock，失败重试"""
    for i in range(max_retries):
        try:
            bs.logout()
        except:
            pass
        lg = bs.login()
        if lg.error_code == '0':
            return True
        log.warning(f"baostock登录失败(重试{i+1}): {lg.error_msg}")
        time.sleep(10)
    return False

def get_all_codes():
    if not baostock_login():
        return []
    try:
        rs = bs.query_all_stock(day="2024-12-31")
        codes = []
        while rs.next():
            d = rs.get_row_data()
            if d[1] == '1':
                code = d[0]
                if code.startswith('sh.6') or code.startswith('sz.0') or code.startswith('sz.3'):
                    codes.append(code)
        codes.sort()
        log.info(f"获取股票列表: {len(codes)}只")
        return codes
    finally:
        try: bs.logout()
        except: pass

# ========== 第1阶段：补日线（含amount/turnover）==========

def get_hist_existing():
    rows, _ = df._pg_execute("SELECT DISTINCT symbol FROM daily_prices WHERE symbol LIKE 'A.%%' AND trade_date < '2025-01-01'")
    return set(r[0] for r in rows)

def get_hist_needs_amount():
    """查哪些已有历史数据但缺amount的"""
    rows, _ = df._pg_execute("""
        SELECT DISTINCT symbol FROM daily_prices 
        WHERE symbol LIKE 'A.%%' AND trade_date < '2025-01-01'
          AND amount IS NULL
        LIMIT 100
    """)
    return [r[0] for r in rows]

def fetch_hist_batch(codes):
    """拉一批股票的日线（含amount/turnover），返回待插入行"""
    all_rows = []
    completed = []
    failed = []
    
    for idx, code in enumerate(codes):
        sym = code if code.startswith('A.') else symbol_map(code)
        bs_code = 'sh.' + sym[2:] if sym[2:].startswith('6') else 'sz.' + sym[2:]
        
        if idx % 10 == 0:
            if not baostock_login():
                time.sleep(10)
                continue
        
        try:
            rs = bs.query_history_k_data_plus(
                bs_code, "date,open,high,low,close,preclose,volume,amount,turn,pctChg",
                start_date="2015-01-01", end_date="2024-12-31",
                frequency="d", adjustflag="2")
            
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            
            if rows and rows[0][4] and rows[0][4] != '':
                for r in rows:
                    try:
                        ds, o, h, l, c, _, vol, amt, turn, *_ = r
                        if c and c != '' and o and o != '':
                            all_rows.append((sym, ds, float(o), float(h), float(l), float(c),
                                             int(float(vol)), float(amt) if amt else None,
                                             float(turn) if turn else None))
                    except:
                        pass
                completed.append(sym)
            else:
                completed.append(sym)  # 退市的也算完成
            
            if (idx + 1) % 20 == 0 or idx == len(codes) - 1:
                log.info(f"  日线进度 {idx+1}/{len(codes)} 缓冲{len(all_rows)}行")
        except Exception as e:
            log.warning(f"  {bs_code} 失败: {e}")
            failed.append(sym)
            try: bs.logout()
            except: pass
    
    try: bs.logout()
    except: pass
    return all_rows, completed, failed

def write_hist_batch(rows):
    if not rows:
        return 0
    conn = df._get_pg()
    cur = conn.cursor()
    pge.execute_values(cur,
        "INSERT INTO daily_prices (symbol, trade_date, open, high, low, close, volume, amount, turnover) "
        "VALUES %s ON CONFLICT (symbol, trade_date) DO UPDATE SET amount=EXCLUDED.amount, turnover=EXCLUDED.turnover",
        rows, page_size=2000)
    conn.commit()
    cur.close()
    return len(rows)

# ========== 第2阶段：拉季度利润数据 ==========

def get_finance_existing():
    rows, _ = df._pg_execute("SELECT DISTINCT symbol FROM financial_data")
    return set(r[0] for r in rows)

def fetch_finance_batch(codes):
    """拉一批股票的季度利润数据"""
    all_rows = []
    completed = []
    failed = []
    
    for idx, code in enumerate(codes):
        sym = code if code.startswith('A.') else symbol_map(code)
        bs_code = 'sh.' + sym[2:] if sym[2:].startswith('6') else 'sz.' + sym[2:]
        
        if idx % 10 == 0:
            if not baostock_login():
                time.sleep(10)
                continue
        
        try:
            for year in range(2015, 2025):
                for quarter in [1, 2, 3, 4]:
                    rs = bs.query_profit_data(code=bs_code, year=str(year), quarter=str(quarter))
                    while rs.next():
                        row = rs.get_row_data()
                        # ['code', 'pubDate', 'statDate', 'roe', 'netProfit', 'netProfitYOY',
                        #  'total_operate_income', 'total_operate_income_yoy', 'eps', 'bps', ...]
                        try:
                            all_rows.append((
                                sym, row[2], row[1],   # symbol, statDate, pubDate
                                float(row[3]) if row[3] else None,  # roe
                                float(row[4]) if row[4] else None,  # netProfit
                                float(row[5]) if row[5] else None,   # netProfitYOY
                                float(row[6]) if row[6] else None,  # revenue
                                float(row[7]) if row[7] else None,  # revenueYOY
                                float(row[8]) if row[8] else None,  # eps
                                float(row[9]) if row[9] else None,  # bvps
                                quarter, year
                            ))
                        except:
                            pass
            completed.append(sym)
            
            if (idx + 1) % 20 == 0 or idx == len(codes) - 1:
                log.info(f"  财务进度 {idx+1}/{len(codes)} 缓冲{len(all_rows)}行")
        except Exception as e:
            log.warning(f"  财务 {bs_code} 失败: {e}")
            failed.append(sym)
            try: bs.logout()
            except: pass
    
    try: bs.logout()
    except: pass
    return all_rows, completed, failed

def write_finance_batch(rows):
    if not rows:
        return 0
    conn = df._get_pg()
    cur = conn.cursor()
    pge.execute_values(cur,
        "INSERT INTO financial_data (symbol, end_date, publish_date, roe, net_profit, "
        "net_profit_yoy, revenue, revenue_yoy, eps, bvps, quarter_type, year) "
        "VALUES %s ON CONFLICT (symbol, end_date) DO UPDATE SET "
        "roe=EXCLUDED.roe, net_profit=EXCLUDED.net_profit, revenue=EXCLUDED.revenue, "
        "eps=EXCLUDED.eps, bvps=EXCLUDED.bvps, publish_date=EXCLUDED.publish_date",
        rows, page_size=500)
    conn.commit()
    cur.close()
    return len(rows)

# ========== 自动循环主逻辑 ==========

def auto_backfill():
    # 初始化表
    df._pg_execute(FINANCIAL_DDL, fetch=False)
    log.info("表初始化完成")
    
    st = load_status()
    all_codes = get_all_codes()
    if not all_codes:
        log.error("获取股票列表失败，等60秒重试")
        return
    
    st['total_codes'] = len(all_codes)
    save_status(st)
    
    # 已经完成历史日线的
    existing_hist = get_hist_existing()
    existing_finance = get_finance_existing()
    
    # 缺amount的
    needs_amount = get_hist_needs_amount()
    needs_amount_set = set(needs_amount)
    
    # 全部A代码列表
    all_a_codes = [symbol_map(c) for c in all_codes]
    
    # --- 阶段1: 补缺日线（没有历史数据 or 缺amount） ---
    remaining = [c for c in all_a_codes if c not in existing_hist or c in needs_amount_set]
    remaining = [c for c in remaining if c not in st.get('completed_hist', [])]
    
    log.info(f"=== 阶段1: 补日线 ===")
    log.info(f"已有历史数据: {len(existing_hist)}只, 缺amount: {len(needs_amount)}只, 待补: {len(remaining)}只")
    
    while remaining:
        batch = remaining[:BATCH_SIZE]
        log.info(f"补日线批次 {len(batch)}只 ({batch[0]}~{batch[-1]})")
        
        rows, completed, failed = fetch_hist_batch(batch)
        cnt = write_hist_batch(rows)
        
        st['completed_hist'] = st.get('completed_hist', []) + completed
        st['failed_hist'] = st.get('failed_hist', []) + failed
        st['rows_hist'] = st.get('rows_hist', 0) + cnt
        save_status(st)
        
        log.info(f"日线批次: +{cnt}行, +{len(completed)}只, 累计{st['rows_hist']}行, "
                 f"失败{len(failed)}只, 索引{st.get('last_idx_hist', 0)}")
        
        remaining = remaining[BATCH_SIZE:]
        
        if remaining:
            log.info(f"剩余日线: {len(remaining)}只, 等3秒继续...")
            time.sleep(3)
    
    log.info("=== 阶段1 完成 ===")
    
    # --- 阶段2: 补利润数据 ---
    remaining_fin = [c for c in all_a_codes if c not in existing_finance]
    remaining_fin = [c for c in remaining_fin if c not in st.get('completed_finance', [])]
    
    log.info(f"=== 阶段2: 补利润数据 ===")
    log.info(f"已有利润: {len(existing_finance)}只, 待补: {len(remaining_fin)}只")
    
    while remaining_fin:
        batch = remaining_fin[:BATCH_SIZE]
        log.info(f"补利润批次 {len(batch)}只 ({batch[0]}~{batch[-1]})")
        
        rows, completed, failed = fetch_finance_batch(batch)
        cnt = write_finance_batch(rows)
        
        st['completed_finance'] = st.get('completed_finance', []) + completed
        st['failed_finance'] = st.get('failed_finance', []) + failed
        st['rows_finance'] = st.get('rows_finance', 0) + cnt
        save_status(st)
        
        log.info(f"利润批次: +{cnt}行, +{len(completed)}只, 累计{st['rows_finance']}行, 失败{len(failed)}只")
        
        remaining_fin = remaining_fin[BATCH_SIZE:]
        
        if remaining_fin:
            log.info(f"剩余利润: {len(remaining_fin)}只, 等3秒继续...")
            time.sleep(3)
    
    log.info("=== 全部完成! ===")
    return True

# ========== 主入口 ==========

def show_status():
    st = load_status()
    existing_hist = get_hist_existing()
    existing_fin = get_finance_existing()
    needs_amt = get_hist_needs_amount()
    
    print(f"=== 数据回补进度 ===")
    print(f"  股票总数: {st.get('total_codes', '?')}")
    print(f"  --- 日线 ---")
    print(f"  已入库: {len(existing_hist)}只")
    print(f"  已完成(状态文件): {len(st.get('completed_hist', []))}只")
    print(f"  缺amount/turnover: {len(needs_amt)}只")
    print(f"  累计行数: {st.get('rows_hist', 0)}行")
    print(f"  --- 利润数据 ---")
    print(f"  已入库: {len(existing_fin)}只")
    print(f"  已完成(状态文件): {len(st.get('completed_finance', []))}只")
    print(f"  累计行数: {st.get('rows_finance', 0)}行")
    
    # PG中实际总数
    rows, _ = df._pg_execute("SELECT count(*) FROM daily_prices WHERE symbol LIKE 'A.%%' AND trade_date < '2025-01-01'")
    print(f"\nPG实际数据: daily_prices历史{rows[0][0]}行")
    
    try:
        rows, _ = df._pg_execute("SELECT count(*) FROM financial_data")
        print(f"financial_data: {rows[0][0]}行")
    except:
        pass

def reset_status():
    os.remove(STATUS_FILE)
    print("状态已重置")

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == 'status':
            show_status()
            sys.exit(0)
        elif cmd == 'reset':
            reset_status()
            sys.exit(0)
    
    # 自动循环（断网重试）
    while True:
        try:
            ok = auto_backfill()
            if ok:
                break
        except Exception as e:
            log.error(f"异常: {e}")
        
        log.info("60秒后重试...")
        time.sleep(60)
