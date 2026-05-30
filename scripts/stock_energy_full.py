"""
个股 pool_energy 全量写入 — S2/S3/S1事件注入后生成个股[B][A]递推记录
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import psycopg2.extras
import numpy as np
import pandas as pd
import json, os, time
from datetime import datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

def main():
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # 清空旧的个股pool_energy
    print("\n[0] 清空旧的个股信号...")
    cur.execute("DELETE FROM pool_energy WHERE level LIKE 'code:A.%'")
    print(f"  删除: {cur.rowcount}行")
    conn.commit()
    
    print("="*60)
    print("个股 pool_energy 全量写入")
    print("="*60)
    
    # 1) 加载大盘基线
    print("\n[1] 加载大盘能量...")
    cur.execute("SELECT date, b_pool, a_pool FROM pool_energy WHERE level='market' ORDER BY date")
    mkt = {r[0]: {'b': r[1], 'a': r[2]} for r in cur.fetchall()}
    print(f"  {len(mkt)}天")
    
    # 2) 获取股票列表
    print("\n[2] 股票列表...")
    cur.execute("""
        SELECT symbol FROM daily_prices
        WHERE trade_date >= '2020-01-01' AND close > 0
        GROUP BY symbol HAVING COUNT(*) > 300
        ORDER BY symbol
    """)
    stocks = [r[0] for r in cur.fetchall()]
    print(f"  {len(stocks)}只")
    
    # 3) 逐只信号提取+写入
    print("\n[3] 逐只信号提取+写入pool_energy...")
    total_written = 0
    total_signals = 0
    total_stocks_with_signal = 0
    stats = {'s2_pulse': 0, 's3_panic': 0, 's1_surge': 0}
    
    batch = []
    batch_size = 0
    
    for i, sym in enumerate(stocks):
        cur2 = conn.cursor()
        cur2.execute("""
            SELECT trade_date, close, volume, amount
            FROM daily_prices WHERE symbol=%s AND trade_date>='2020-01-01'
            ORDER BY trade_date
        """, (sym,))
        rows = cur2.fetchall()
        cur2.close()
        if len(rows) < 120:
            continue
        
        df = pd.DataFrame(rows, columns=['trade_date', 'close', 'volume', 'amount'])
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df.set_index('trade_date', inplace=True)
        df['ret'] = df['close'].pct_change()
        df['log_ret'] = np.log(df['close'] / df['close'].shift(1)).clip(-0.11, 0.11)
        df['vol_20d'] = df['log_ret'].rolling(20).std() * np.sqrt(252)
        
        n = len(df)
        vol_ma = df['vol_20d'].rolling(120).mean()
        vol_std = df['vol_20d'].rolling(120).std(ddof=1)
        df['vol_z'] = (df['vol_20d'] - vol_ma) / vol_std
        df['v_z'] = (df['volume'] - df['volume'].rolling(20).mean()) / df['volume'].rolling(20).std().clip(lower=1)
        df['amt_z'] = (df['amount'] - df['amount'].rolling(20).mean()) / df['amount'].rolling(20).std().clip(lower=1)
        
        s2_mask = (df['vol_z'] > 2.5) & (df['v_z'] > 2.0) & (df['ret'] > 0.05)
        s3_mask = (df['vol_z'] > 2.5) & (df['v_z'] > 2.0) & (df['ret'] < -0.05)
        s1_mask = (df['vol_z'] > 3.0) & (np.abs(df['v_z']) < 1.5)
        
        stock_signals = 0
        for idx in range(120, n - 20):
            d = df.index[idx]
            d_key = d.date() if hasattr(d, 'date') else d
            if d_key not in mkt:
                continue
            
            mb = mkt[d_key]
            b_mkt = mb['b']
            a_mkt = mb['a']
            
            b_in, a_in = 0, 0
            sig_types = []
            
            if s2_mask.iloc[idx]:
                b_in += 0.08
                a_in += 0.20
                sig_types.append('s2_pulse')
            if s3_mask.iloc[idx]:
                b_in += 0.15
                a_in += 0.05
                sig_types.append('s3_panic')
            if s1_mask.iloc[idx]:
                b_in += 0.06
                sig_types.append('s1_surge')
            
            if not sig_types:
                continue
            
            sig_label = '_'.join(sig_types)
            b_stock = b_mkt + b_in
            a_stock = a_mkt + a_in
            surplus = b_stock - a_stock
            imbalance = (b_stock - a_stock) / max(b_stock + a_stock, 0.01)
            
            ret_5 = float(df['close'].iloc[idx+5] / df['close'].iloc[idx] - 1) if idx+5 < n else None
            ret_20 = float(df['close'].iloc[idx+20] / df['close'].iloc[idx] - 1) if idx+20 < n else None
            
            extra = json.dumps({
                'sigs': sig_label,
                'b_in': b_in, 'a_in': a_in,
                'vol_z': round(float(df['vol_z'].iloc[idx]), 2),
                'v_z': round(float(df['v_z'].iloc[idx]), 2),
                'ret_0': round(float(df['ret'].iloc[idx]), 4),
                'b_mkt': b_mkt, 'a_mkt': a_mkt,
                'total_pool': round(b_stock + a_stock, 4),
            })
            
            batch.append((
                sym, d_key, 
                round(b_stock, 6), round(a_stock, 6),
                round(b_in, 6), round(a_in, 6),
                round(surplus, 6), round(imbalance, 5),
                sig_label, extra,
            ))
            batch_size += 1
            stock_signals += 1
            total_signals += 1
            
            for st in sig_types:
                if st in stats:
                    stats[st] += 1
        
        if stock_signals > 0:
            total_stocks_with_signal += 1
        
        if batch_size >= 500:
            _write_batch(conn, batch)
            total_written += len(batch)
            batch = []
            batch_size = 0
        
        if (i+1) % 200 == 0 or i == len(stocks) - 1:
            print(f"  {i+1}/{len(stocks)}, 信号: {total_signals}, 写入: {total_written}")
    
    # 写入剩余
    if batch:
        _write_batch(conn, batch)
        total_written += len(batch)
    
    print(f"\n[4] 完成")
    print(f"  总股票: {len(stocks)}")
    print(f"  有信号股票: {total_stocks_with_signal}")
    print(f"  总信号数: {total_signals}")
    print(f"  写入pool_energy: {total_written}")
    print(f"  信号分布: {json.dumps(stats, indent=2)}")
    conn.close()

def _write_batch(conn, batch):
    cur = conn.cursor()
    psycopg2.extras.execute_values(
        cur,
        """INSERT INTO pool_energy 
           (level, date, b_pool, a_pool, b_inject, a_inject, surplus, imbalance, phase, extra)
           VALUES %s
           ON CONFLICT (level, date) DO UPDATE SET
               b_pool=EXCLUDED.b_pool, a_pool=EXCLUDED.a_pool,
               b_inject=EXCLUDED.b_inject, a_inject=EXCLUDED.a_inject,
               surplus=EXCLUDED.surplus, imbalance=EXCLUDED.imbalance,
               extra=EXCLUDED.extra, updated_at=NOW()""",
        [('code:'+r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]) for r in batch],
        page_size=500
    )
    conn.commit()
    cur.close()

def daily_signal_brief(conn, report_date=None):
    """
    输出当日可交易信号简报（S3 定性分类 + S2+S1 信号列表）
    运行在 stock_energy_full 写入之后
    """
    from datetime import date, timedelta
    import json
    
    cur = conn.cursor()
    
    if report_date is None:
        cur.execute("SELECT MAX(date) FROM pool_energy WHERE level='market'")
        r = cur.fetchone()
        report_date = r[0] if r else date.today()
    
    print("\n" + "=" * 65)
    print(f"V6 每日信号简报 | {report_date}")
    print("=" * 65)
    
    # 1) 大盘 phase
    cur.execute("""
        SELECT date, phase, b_pool, a_pool, surplus, extra
        FROM pool_energy
        WHERE level='market' AND date >= %s - 5 AND date <= %s
        ORDER BY date DESC
    """, (report_date, report_date))
    mkt = cur.fetchall()
    if mkt:
        latest = mkt[0]
        print(f"\n📊 大盘阶段: {latest[1]} (surplus={latest[4]:+.4f})")
        print(f"   B_pool={latest[2]:.4f} A_pool={latest[3]:.4f}")
    
    # 2) 今日 S3 信号
    cur.execute("""
        SELECT e.level, e.date, e.b_inject, e.a_inject, e.extra
        FROM pool_energy e
        WHERE e.level LIKE 'code:%%'
          AND position('s3' in e.extra->>'sigs') > 0
          AND e.date = %s
        ORDER BY e.level
    """, (report_date,))
    s3_rows = cur.fetchall()
    
    if s3_rows:
        print(f"\n⚠️  今日 S3 恐慌信号: {len(s3_rows)} 只")
        print(f"   {'代码':<10} {'vol_z':<8} {'v_z':<8} {'跌幅':<8} {'评分':<6} {'动作':<10}")
        print(f"   {'-'*55}")
        
        for r in s3_rows[:10]:
            code = r[0][7:]
            extra = json.loads(r[4])
            vol_z = extra.get('vol_z', 0)
            v_z = extra.get('v_z', 0)
            ret = extra.get('ret_0', 0)
            
            # 用 qualifier 规则简单分类（不跑全量计算）
            # 这里用 extra 的信息做初步判断，post_vol 需运行 signal_qualifier
            action = "待定性"
            if v_z > 3:
                action = "关注"
            print(f"   A.{code:<7} {vol_z:<8.1f} {v_z:<8.1f} {ret:<7.1%} {'—':<6} {action:<10}")
        
        if len(s3_rows) > 10:
            print(f"   ... 还有 {len(s3_rows)-10} 只（运行 signal_qualifier 获取完整定性）")
    else:
        print(f"\n✅ 今日无 S3 恐慌信号 (最近 S3: {mkt[0][0] if mkt else 'N/A'} 无极端行情)")
    
    # 3) S2 + S1 信号列表（只输出 top 10）
    cur.execute("""
        SELECT e.level, e.date, (e.extra->>'sigs') as sigtype,
               (e.extra->>'ret_0')::float as ret0
        FROM pool_energy e
        WHERE e.level LIKE 'code:%%'
          AND (e.extra->>'sigs') IN ('s1_surge', 's2_pulse')
          AND e.date = %s
        ORDER BY ABS((e.extra->>'ret_0')::float) DESC
        LIMIT 10
    """, (report_date,))
    other_rows = cur.fetchall()
    
    if other_rows:
        print(f"\n📡 今日其他信号 (S1/S2 前10):")
        sig_map = {'s1_surge': 'S1波动', 's2_pulse': 'S2脉冲'}
        for r in other_rows:
            st = sig_map.get(r[2], r[2])
            print(f"   A.{r[0][7:]:<8} {st:<8} 涨幅={r[3]:+.1%}")
    
    # 4) 近7日增减持
    cur.execute("""
        SELECT code, is_increase, abs_change
        FROM incr_decr
        WHERE change_date BETWEEN %s AND %s
        ORDER BY abs_change DESC
        LIMIT 5
    """, (report_date - timedelta(days=7), report_date))
    incr_rows = cur.fetchall()
    if incr_rows:
        print(f"\n🏦 近7日增减持大单 (前5):")
        for r in incr_rows:
            typ = "增持" if r[1] else "减持"
            print(f"   A.{r[0]:<8} {typ} {r[2]:>10,}股")
    
    # 5) 龙虎榜
    cur.execute("""
        SELECT code, inst_net_buy
        FROM csi_lhb_detail
        WHERE trade_date = %s
        ORDER BY ABS(COALESCE(inst_net_buy, 0)) DESC
        LIMIT 5
    """, (report_date,))
    lhb_rows = cur.fetchall()
    if lhb_rows:
        print(f"\n📋 龙虎榜 (前5):")
        # 去重
        seen = set()
        for r in lhb_rows:
            if r[0] not in seen:
                print(f"   A.{r[0]:<8} inst_net={r[1]:+>12,.0f}")
                seen.add(r[0])
    
    # 6) 今日推荐关注
    print(f"\n" + "=" * 65)
    print("📋 今日决策要点:")
    print("  " + ("无极端行情，等待市场波动放大" if not s3_rows else f"{len(s3_rows)}只S3恐慌，运行signal_qualifier全面定性"))
    print("=" * 65 + "\n")
    
    cur.close()


if __name__ == '__main__':
    main()
    print("\n[5] 生成每日信号简报...")
    conn = psycopg2.connect(DB)
    daily_signal_brief(conn)
