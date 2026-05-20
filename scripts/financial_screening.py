#!/usr/bin/env python3
"""
financial_screening.py — A 股财务筛选策略

数据源：PG 数据库（工控机 192.168.3.14）
基于 baostock 财务数据（已入库 financial_data 表）

⚠️ 列名说明（baostock 字段映射）：
  - roe = roeAvg (ROE, 小数, 如 0.10=10%) ✅
  - net_profit = npMargin (净利润率, 小数)
  - net_profit_yoy = gpMargin (毛利率, 小数)
  - revenue = netProfit (净利润绝对值, 元)
  - revenue_yoy = epsTTM (每股收益 TTM, 元)
  - eps = MBRevenue (主营业务收入, 元)
  - bvps = totalShare (总股本, 股)
"""

import sys, os
import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_finance import _pg_execute, _get_pg

# ============================================================
# 配置参数
# ============================================================
CONFIG = {
    "roe_min": 0.10,              # ROE > 10%
    "net_profit_margin_min": 0,   # 净利润率 > 0（正盈利）
    "last_n_quarters_profitable": 4,  # 最近 N 个季度净利润均为正
    "target_year": 2025,           # 目标财务年份
    "target_quarter": 4,           # 目标季度
    # 近一周评估日期（5个交易日）
    "price_start": "2026-05-13",
    "price_end": "2026-05-19",
}

# 可选的额外筛选（更严格）
STRICT = {
    "net_profit_yoy_min": 0.15,   # 毛利率 > 15%
    "min_market_cap_b": None,     # 最小市值（亿）
}


# ============================================================
# 1. 财务筛选
# ============================================================
def get_stock_list() -> pd.DataFrame:
    """获取全量股票列表"""
    rows, cols = _pg_execute("SELECT code, name FROM stock_list WHERE active ORDER BY code")
    return pd.DataFrame(rows, columns=cols)


def get_financial_data(year: int, quarter_type: int) -> pd.DataFrame:
    """获取指定季度的财务数据"""
    sql = """
        SELECT f.symbol, s.name,
               f.roe, f.net_profit AS np_margin, 
               f.net_profit_yoy AS gp_margin,
               f.revenue AS net_profit_abs,
               f.revenue_yoy AS eps_ttm,
               f.eps AS main_revenue,
               f.bvps AS total_shares,
               f.end_date, f.publish_date
        FROM financial_data f
        JOIN stock_list s ON f.symbol = s.code
        WHERE f.year = %s AND f.quarter_type = %s
          AND f.roe IS NOT NULL
    """
    rows, cols = _pg_execute(sql, (year, quarter_type))
    df = pd.DataFrame(rows, columns=cols)
    
    # 数值列转浮点
    num_cols = ['roe', 'np_margin', 'gp_margin', 'net_profit_abs', 
                'eps_ttm', 'main_revenue', 'total_shares']
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    
    return df


def get_profit_timeseries(symbol: str, max_quarters: int = 4) -> list:
    """获取最近 N 个季度的净利润数据"""
    sql = """
        SELECT end_date, quarter_type, year, 
               revenue AS net_profit_abs
        FROM financial_data
        WHERE symbol = %s
        ORDER BY year DESC, quarter_type DESC
        LIMIT %s
    """
    rows, _ = _pg_execute(sql, (symbol, max_quarters))
    return [(r[0], r[1], r[2], r[3] if r[3] else 0) for r in rows]


def screen_stocks(df_q: pd.DataFrame) -> pd.DataFrame:
    """执行财务筛选"""
    print(f"\n{'='*60}")
    print(f"📊 财务筛选条件：")
    print(f"  - ROE > {CONFIG['roe_min']*100:.0f}%")
    print(f"  - 净利润率 > {CONFIG['net_profit_margin_min']*100:.0f}%")
    print(f"  - 最近 {CONFIG['last_n_quarters_profitable']} 个季度净利润均为正")
    print(f"  - 基准季度: {CONFIG['target_year']}年Q{CONFIG['target_quarter']}")
    print(f"{'='*60}")
    
    # 基础筛选
    mask = (
        (df_q['roe'] > CONFIG['roe_min']) &
        (df_q['np_margin'] > CONFIG['net_profit_margin_min']) &
        (df_q['net_profit_abs'] > 0)
    )
    df_filtered = df_q[mask].copy()
    print(f"  基础条件通过: {len(df_filtered)} / {len(df_q)} 只")
    
    # 检查最近 N 个季度利润均为正
    profitable_stocks = []
    print(f"  检查最近 {CONFIG['last_n_quarters_profitable']} 个季度净利润...")
    
    for _, row in df_filtered.iterrows():
        symbol = row['symbol']
        ts = get_profit_timeseries(symbol, CONFIG['last_n_quarters_profitable'])
        all_positive = all(t[3] > 0 for t in ts)
        if all_positive and len(ts) >= CONFIG['last_n_quarters_profitable']:
            profitable_stocks.append(row)
    
    result = pd.DataFrame(profitable_stocks).reset_index(drop=True)
    print(f"  最近 {CONFIG['last_n_quarters_profitable']} 季均盈利: {len(result)} 只")
    
    return result


# ============================================================
# 2. 近一周涨跌评估
# ============================================================
def get_price_data(symbols: list, start_date: str, end_date: str) -> pd.DataFrame:
    """获取股票列表在指定日期区间的收盘价"""
    if not symbols:
        return pd.DataFrame()
    
    # 分批查询（避免 SQL 过长）
    batch_size = 500
    all_rows = []
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        placeholders = ','.join(['%s'] * len(batch))
        sql = f"""
            SELECT symbol, trade_date, close
            FROM daily_prices
            WHERE symbol LIKE 'A.%%%%' 
              AND SUBSTRING(symbol FROM 3) IN ({placeholders})
              AND trade_date BETWEEN %s AND %s
            ORDER BY symbol, trade_date
        """
        rows, cols = _pg_execute(sql, tuple(batch) + (start_date, end_date))
        all_rows.extend(rows)
    
    if not all_rows:
        return pd.DataFrame()
    
    df = pd.DataFrame(all_rows, columns=['symbol', 'trade_date', 'close'])
    df['symbol'] = df['symbol'].str.replace('A.', '', regex=False)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df


def calc_weekly_change(stock_df: pd.DataFrame, all_df: pd.DataFrame = None) -> pd.DataFrame:
    """计算近一周涨跌幅
    
    用最新价 / 最旧价 - 1，要求至少有2个交易日数据
    """
    results = []
    for symbol, grp in stock_df.groupby('symbol'):
        grp = grp.sort_values('trade_date')
        if len(grp) < 2:
            continue
        first_close = grp['close'].iloc[0]
        last_close = grp['close'].iloc[-1]
        if first_close > 0:
            pct_change = (last_close / first_close - 1) * 100
        else:
            pct_change = 0.0
        results.append({'symbol': symbol, 'change_pct': pct_change})
    
    return pd.DataFrame(results)


# ============================================================
# 3. 全市场基准
# ============================================================
def get_market_avg_change(start_date: str, end_date: str) -> dict:
    """计算全市场平均涨跌幅"""
    sql = """
        WITH price_range AS (
            SELECT symbol,
                   MIN(trade_date) AS first_day,
                   MAX(trade_date) AS last_day
            FROM daily_prices
            WHERE symbol LIKE 'A.%%'
              AND trade_date BETWEEN %s AND %s
            GROUP BY symbol
            HAVING COUNT(*) >= 2
        ),
        first_prices AS (
            SELECT dp.symbol, dp.close
            FROM daily_prices dp
            JOIN price_range pr ON dp.symbol = pr.symbol AND dp.trade_date = pr.first_day
        ),
        last_prices AS (
            SELECT dp.symbol, dp.close
            FROM daily_prices dp
            JOIN price_range pr ON dp.symbol = pr.symbol AND dp.trade_date = pr.last_day
        )
        SELECT
            AVG((lp.close / NULLIF(fp.close, 0) - 1) * 100) AS avg_change,
            COUNT(*) AS stock_count,
            SUM(CASE WHEN lp.close > fp.close THEN 1 ELSE 0 END) AS up_count,
            SUM(CASE WHEN lp.close < fp.close THEN 1 ELSE 0 END) AS down_count
        FROM first_prices fp
        JOIN last_prices lp ON fp.symbol = lp.symbol
    """
    rows, cols = _pg_execute(sql, (start_date, end_date))
    if rows:
        return {
            'avg_change': float(rows[0][0]) if rows[0][0] else 0,
            'count': int(rows[0][1]) if rows[0][1] else 0,
            'up': int(rows[0][2]) if rows[0][2] else 0,
            'down': int(rows[0][3]) if rows[0][3] else 0,
        }
    return {'avg_change': 0, 'count': 0, 'up': 0, 'down': 0}


# ============================================================
# 4. 计算更严格的 YoY 利润增长（手动计算，不依赖 net_profit_yoy 列）
# ============================================================
def calc_yoy_growth(df: pd.DataFrame) -> pd.DataFrame:
    """计算净利润同比增速（基于 net_profit_abs 绝对值跨年对比）
    
    Q4 2025 vs Q4 2024 的净利润同比
    """
    year = CONFIG['target_year']
    prev_year = year - 1
    
    symbols = df['symbol'].tolist()
    if not symbols:
        df['net_profit_yoy_pct'] = None
        return df
    
    # 获取去年同期的净利润
    sql = """
        SELECT symbol, revenue AS net_profit_abs
        FROM financial_data
        WHERE symbol = ANY(%s)
          AND year = %s AND quarter_type = %s
    """
    conn = _get_pg()
    cur = conn.cursor()
    cur.execute(sql, (symbols, prev_year, CONFIG['target_quarter']))
    prev_data = {r[0]: (float(r[1]) if r[1] else 0) for r in cur.fetchall()}
    cur.close()
    
    yoy_list = []
    for _, row in df.iterrows():
        sym = row['symbol']
        current_np = float(row['net_profit_abs']) if row['net_profit_abs'] else 0
        prev_np = prev_data.get(sym, 0)
        if prev_np > 0 and current_np > 0:
            yoy = (current_np - prev_np) / prev_np * 100
        elif prev_np <= 0 and current_np > 0:
            yoy = float('inf')  # 扭亏为盈
        elif current_np <= 0:
            yoy = -100.0  # 亏损
        else:
            yoy = 0.0
        yoy_list.append(yoy)
    
    df['net_profit_yoy_pct'] = yoy_list
    df = df[df['net_profit_yoy_pct'] > 0]  # 净利润同比增长
    return df


# ============================================================
# 主流程
# ============================================================
def main():
    print("🚀 A 股财务筛选策略 v1.0")
    print(f"   运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Step 1: 获取财务数据
    print(f"\n📡 获取 {CONFIG['target_year']}年Q{CONFIG['target_quarter']} 财务数据...")
    df_fin = get_financial_data(CONFIG['target_year'], CONFIG['target_quarter'])
    print(f"   共 {len(df_fin)} 只股票有完整财务数据")
    
    # Step 2: 基础财务筛选
    df_qualified = screen_stocks(df_fin)
    if df_qualified.empty:
        print("\n⚠️ 没有符合基础条件的股票")
        sys.exit(0)
    
    # Step 3: 净利润同比增长筛选
    print(f"\n📈 计算净利润同比增速 ({CONFIG['target_year']}Q{CONFIG['target_quarter']} vs {CONFIG['target_year']-1}Q{CONFIG['target_quarter']})...")
    df_qualified = calc_yoy_growth(df_qualified)
    print(f"   净利润同比增长 > 0: {len(df_qualified)} 只")
    
    if df_qualified.empty:
        print("\n⚠️ 没有同时满足所有条件的股票")
        sys.exit(0)
    
    # Step 4: 获取价格数据
    print(f"\n📡 获取近一周价格数据 ({CONFIG['price_start']} ~ {CONFIG['price_end']})...")
    symbols = df_qualified['symbol'].tolist()
    df_prices = get_price_data(symbols, CONFIG['price_start'], CONFIG['price_end'])
    print(f"   获取到 {len(symbols)} 只股票的价格数据")
    
    # Step 5: 计算涨跌
    df_changes = calc_weekly_change(df_prices)
    
    # Step 6: 合并结果
    df_result = df_qualified.merge(df_changes, on='symbol', how='left')
    df_result['change_pct'] = df_result['change_pct'].fillna(0.0)
    
    # 全市场基准
    market = get_market_avg_change(CONFIG['price_start'], CONFIG['price_end'])
    
    # Step 7: 输出
    print(f"\n{'='*80}")
    print(f"🏆 筛选结果汇总")
    print(f"{'='*80}")
    print(f"   合格股票: {len(df_result)} 只")
    
    if len(df_result) > 0:
        avg_roe = df_result['roe'].mean() * 100
        avg_np = df_result['net_profit_abs'].mean() / 1e8
        avg_change = df_result['change_pct'].mean()
        up_count = (df_result['change_pct'] > 0).sum()
        down_count = (df_result['change_pct'] < 0).sum()
        beat_market = (df_result['change_pct'] > market['avg_change']).sum()
        
        print(f"   平均 ROE: {avg_roe:.2f}%")
        print(f"   平均净利润: {avg_np:.2f}亿")
        print(f"   近一周平均涨跌: {avg_change:+.2f}%")
        print(f"   上涨: {up_count} | 下跌: {down_count}")
        print(f"   跑赢全市场({market['avg_change']:+.2f}%): {beat_market}/{len(df_result)} ({beat_market/max(len(df_result),1)*100:.0f}%)")
    
    # 按涨跌幅排序
    df_result = df_result.sort_values('change_pct', ascending=False).reset_index(drop=True)
    
    # 打印表格
    print(f"\n{'='*100}")
    print(f"📋 股票列表（按近一周涨跌幅排序）")
    print(f"{'='*100}")
    print(f"{'代码':<8} {'名称':<12} {'ROE(%)':<10} {'毛利率(%)':<12} {'净利润同比(%)':<16} {'近一周涨跌(%)':<14}")
    print(f"{'-'*8} {'-'*12} {'-'*10} {'-'*12} {'-'*16} {'-'*14}")
    
    for _, row in df_result.iterrows():
        gp = float(row['gp_margin']) * 100 if row['gp_margin'] else 0
        np_yoy = float(row['net_profit_yoy_pct']) if row['net_profit_yoy_pct'] else 0
        roe = float(row['roe']) * 100
        chg = float(row['change_pct'])
        
        # 格式化同比
        if np_yoy == float('inf'):
            np_yoy_str = "扭亏为盈"
        else:
            np_yoy_str = f"{np_yoy:+.1f}"
        
        # 标识跑赢/跑输市场
        tag = " 🟢" if chg > market['avg_change'] else (" 🔴" if chg < market['avg_change'] else "")
        
        print(f"{row['symbol']:<8} {row['name']:<12} {roe:<10.2f} {gp:<12.2f} {np_yoy_str:<16} {chg:<+14.2f}{tag}")
    
    # 汇总
    print(f"\n{'='*80}")
    print(f"📊 全市场基准（{CONFIG['price_start']} ~ {CONFIG['price_end']}）：")
    print(f"   全市场平均涨跌: {market['avg_change']:+.2f}%")
    print(f"   全市场可统计: {market['count']} 只")
    print(f"   上涨: {market['up']} | 下跌: {market['down']}")
    print(f"{'='*80}")
    
    # 绩效统计
    if len(df_result) > 0:
        print(f"\n📊 筛选池绩效：")
        print(f"   数量: {len(df_result)} 只")
        print(f"   平均 ROE: {df_result['roe'].mean()*100:.2f}%")
        print(f"   平均毛利率: {df_result['gp_margin'].mean()*100:.2f}%")
        print(f"   平均涨跌: {df_result['change_pct'].mean():+.2f}%")
        print(f"   中位涨跌: {df_result['change_pct'].median():+.2f}%")
        print(f"   跑赢市场: {beat_market}/{len(df_result)} ({beat_market/len(df_result)*100:.1f}%)")
        print(f"   最大涨幅: {df_result['change_pct'].max():+.2f}%")
        print(f"   最大跌幅: {df_result['change_pct'].min():+.2f}%")
    
    print(f"\n✅ 筛选完成")


if __name__ == "__main__":
    main()
