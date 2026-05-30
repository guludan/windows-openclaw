"""v7_bt_phase1.py — V7 全量回测 Phase1: 信号生成
逐只股票逐日扫描 S1/S2/S3 信号，写入 backtest_signals 表
PG 内完成，避免 Python 全量传输

用法:
  # 先创建表
  python3 v7_bt_phase1.py --init
  # 分段跑回测 (默认 2015-01 ~ 2026-05, 可选范围)
  python3 v7_bt_phase1.py --start 2020-01-01 --end 2020-06-30
  python3 v7_bt_phase1.py --start 2020-07-01 --end 2020-12-31
  # ...每次跑半年
"""
import psycopg2, psycopg2.extras, json, time, sys
from datetime import date, timedelta
from collections import defaultdict

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
DB_FAST = DB + " options='-c statement_timeout=0'"

def init_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS backtest_signals (
            symbol VARCHAR(12),
            trade_date DATE,
            signal_type VARCHAR(20),  -- 's3_panic','s2_pulse','s1_surge','no_signal'
            close NUMERIC,
            turnover NUMERIC,
            ret NUMERIC,           -- 当日涨跌
            ret5 NUMERIC,          -- 5日涨跌
            vz2 NUMERIC,           -- 波动突增率
            tz NUMERIC,            -- 换手Z值
            ret5d NUMERIC,         -- 未来5日收益
            ret10d NUMERIC,        -- 未来10日收益
            ret20d NUMERIC,        -- 未来20日收益
            v7_score NUMERIC,      -- V7 评分
            extra JSONB,
            PRIMARY KEY (symbol, trade_date)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bt_trade_date ON backtest_signals(trade_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bt_signal_type ON backtest_signals(signal_type)")
    conn.commit()
    print(f"✅ 表 backtest_signals 就绪")

def run_segment(conn, start_dt, end_dt, verbose=False):
    """跑一个日期段"""
    cur = conn.cursor()
    t0 = time.time()
    
    # 1) 大盘相空间 — 信号检测需要大盘 phase
    cur.execute("SELECT date, b_pool, a_pool, phase FROM pool_energy WHERE level='market' AND date>=%s AND date<=%s ORDER BY date", (start_dt, end_dt))
    mkt = {r[0]: {'b': float(r[1]), 'a': float(r[2]), 'phase': r[3] or 'NEUTRAL'} for r in cur.fetchall()}
    
    if not mkt:
        print(f"⚠️ {start_dt}~{end_dt} 无大盘数据")
        return
    
    print(f"📊 大盘 {len(mkt)} 天 ({time.time()-t0:.0f}s)")
    
    # 2) 获取此时间段有数据的股票
    cur.execute("""
        SELECT DISTINCT SUBSTRING(symbol,3) FROM daily_prices
        WHERE trade_date>=%s AND trade_date<=%s AND close>0
    """, (start_dt, end_dt))
    stock_codes = [r[0] for r in cur.fetchall()]
    print(f"📈 股票: {len(stock_codes):,} 只")
    
    # 3) 逐只股票全历史拉取 + 信号扫描
    total_signals = 0
    s3_count = s2_count = s1_count = 0
    batch = []
    
    for si, c6 in enumerate(stock_codes):
        # 拉该股票全量日线（从 2014 年起用于窗口计算）
        cur.execute("""
            SELECT trade_date, close, turnover, volume
            FROM daily_prices
            WHERE symbol='A.'||%s AND trade_date>='2014-06-01' AND trade_date<=%s
              AND close>0
            ORDER BY trade_date
        """, (c6, end_dt))
        rows = cur.fetchall()
        
        if len(rows) < 30:
            continue
        
        # 逐日扫描
        for idx in range(20, len(rows)):
            dt = rows[idx][0]
            if dt < start_dt or dt > end_dt:
                continue
            if dt not in mkt:
                continue
            
            c0 = rows[idx][1]
            to0 = rows[idx][2] or 0
            vol0 = rows[idx][3] or 0
            
            ret = c0 / rows[idx-1][1] - 1
            
            # 除权过滤: abs(ret)>20% → 跳过
            if abs(ret) > 0.20:
                continue
            
            # 特征
            ret5 = c0 / rows[idx-5][1] - 1 if idx >= 5 else 0
            
            r20_ret = [rows[idx-k][1]/rows[idx-k-1][1]-1 for k in range(1, 21)]
            vz2 = np.std(r20_ret) if len(r20_ret) >= 15 else 0
            vol5 = np.mean([abs(rows[idx-k][1]/rows[idx-k-1][1]-1) for k in range(5)])
            vz2 = vol5 / vz2 if vz2 > 0 else 0
            
            # tz: 优先 turnover，fallback volume
            to20 = [rows[idx-k][2] for k in range(1, 21) if rows[idx-k][2] is not None and rows[idx-k][2] > 0]
            if len(to20) >= 5:
                mu = np.mean(to20)
                sd = np.std(to20)
                tz = (to0 - mu) / sd if sd > 0 else 0
            elif vol0 > 0:
                # fallback to volume
                v20 = [rows[idx-k][3] for k in range(1, 21) if rows[idx-k][3] is not None and rows[idx-k][3] > 0]
                if len(v20) >= 5:
                    mu = np.mean(v20); sd = np.std(v20)
                    tz = (vol0 - mu) / sd if sd > 0 else 0
                else:
                    tz = 0
            else:
                tz = 0
            
            # 信号检测
            sig = None
            if vz2 > 2.5 and abs(tz) > 2.0 and ret < -0.05:
                sig = 's3_panic'
            elif vz2 > 2.5 and tz > 2.0 and ret > 0.05:
                sig = 's2_pulse'
            elif vz2 > 3.0 and abs(tz) < 1.5:
                sig = 's1_surge'
            
            if not sig:
                continue
            
            # 未来收益
            f5 = rows[idx+5][1]/c0 - 1 if idx+5 < len(rows) else None
            f10 = rows[idx+10][1]/c0 - 1 if idx+10 < len(rows) else None
            f20 = rows[idx+20][1]/c0 - 1 if idx+20 < len(rows) else None
            
            # 大盘能量 (没有 pool_energy 时用 1.0)
            mkt_energy = mkt[dt]
            energy = mkt_energy['b'] + mkt_energy['a'] if mkt_energy else 1.0
            
            batch.append((c6, dt, sig, float(c0), float(to0 or 0), float(ret), float(ret5), float(vz2), float(tz), 
                          float(f5) if f5 is not None else None, float(f10) if f10 is not None else None, 
                          float(f20) if f20 is not None else None, float(energy), mkt_energy['phase']))
            if sig == 's3_panic': s3_count += 1
            elif sig == 's2_pulse': s2_count += 1
            else: s1_count += 1
        
        # 每 200 只打印进度
        if verbose and (si+1) % 200 == 0:
            print(f"  ...{si+1}/{len(stock_codes)} 信号:{len(batch):,} ({time.time()-t0:.0f}s)")
        
        # 批量写 PG（每 2000 条提交）
        if len(batch) >= 2000:
            _write_batch(conn, batch, mkt_energy['phase'] if mkt_energy else 'NEUTRAL')
            total_signals += len(batch)
            batch = []
    
    # 写剩余
    if batch:
        _write_batch(conn, batch, mkt_energy['phase'] if mkt_energy else 'NEUTRAL')
        total_signals += len(batch)
    
    print(f"✅ {start_dt}~{end_dt}: 总信号:{total_signals:,} S3:{s3_count} S2:{s2_count} S1:{s1_count} ({time.time()-t0:.0f}s)")


import numpy as np

def _write_batch(conn, batch, default_phase):
    """批量写入 backtest_signals，含 V7 评分"""
    cur = conn.cursor()
    values = []
    for v in batch:
        values.append((v[0], v[1], v[2], float(v[3]), float(v[4]), float(v[5]), float(v[6]), float(v[7]), float(v[8]), 
                       float(v[9]) if v[9] is not None else None,
                       float(v[10]) if v[10] is not None else None,
                       float(v[11]) if v[11] is not None else None,
                       float(v[12]),
                       json.dumps({'sig':v[2],'energy':round(float(v[12]),4),'phase':v[13]})))
    
    psycopg2.extras.execute_values(cur, """
        INSERT INTO backtest_signals
        (symbol, trade_date, signal_type, close, turnover, ret, ret5, vz2, tz, ret5d, ret10d, ret20d, v7_score, extra)
        VALUES %s
        ON CONFLICT (symbol, trade_date) DO NOTHING
    """, values,
    template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")
    conn.commit()


def backfill_v7_scores(conn, start_dt, end_dt):
    """统一回填 V7 评分到 backtest_signals (S3 + S2)"""
    cur = conn.cursor()
    
    # 获取大盘能量
    cur.execute("SELECT date, b_pool, a_pool, phase FROM pool_energy WHERE level='market' AND date>=%s AND date<=%s ORDER BY date",
        (start_dt, end_dt))
    mkt = {r[0]: {'energy': float(r[1])+float(r[2]), 'phase': r[3]} for r in cur.fetchall()}
    
    for sig_type, func_name in [('s3_panic', 'v7_score_s3'), ('s2_pulse', 'v7_score_s2_bdistrib')]:
        cur.execute("""
            SELECT symbol, trade_date, ret5, tz, extra::text
            FROM backtest_signals
            WHERE trade_date>=%s AND trade_date<=%s AND signal_type=%s
            ORDER BY trade_date
        """, (start_dt, end_dt, sig_type))
        rows = cur.fetchall()
        if not rows:
            print(f"  {sig_type}: 0 条, 跳过")
            continue
        
        print(f"  {sig_type} ({func_name}): {len(rows)} 条回填中...")
        batch = []
        for sym, dt, ret5, tz_, extra_raw in rows:
            try: extra = json.loads(extra_raw)
            except: extra = {}
            mk = mkt.get(dt, {'energy': 1.0, 'phase': 'NEUTRAL'})
            energy = mk['energy']
            phase = extra.get('phase', mk['phase'])
            
            if sig_type == 's3_panic':
                cur.execute("SELECT v7_score_s3(%s,%s,%s,0,0,0,%s)", 
                    (float(ret5 or 0), float(tz_ or 0), float(energy), phase))
            else:
                cur.execute("SELECT v7_score_s2_bdistrib(%s,%s,%s,0,0)",
                    (float(ret5 or 0), float(tz_ or 0), float(energy)))
            score = cur.fetchone()[0]
            batch.append((float(score), json.dumps(extra | {'v7_score': float(score)}), sym, dt))
            
            if len(batch) >= 500:
                _update_v7_batch(conn, batch); batch=[]
        if batch:
            _update_v7_batch(conn, batch)
    print(f"  V7评分全部完成")


def _update_v7_batch(conn, batch):
    cur = conn.cursor()
    psycopg2.extras.execute_values(cur,
        "UPDATE backtest_signals SET v7_score = data.v7_score, extra = data.extra FROM (VALUES %s) AS data(v7_score, extra, symbol, trade_date) WHERE backtest_signals.symbol=data.symbol AND backtest_signals.trade_date=data.trade_date",
        batch, template="(%s, %s::jsonb, %s, %s)")
    conn.commit()


def stats(conn, start_dt, end_dt):
    """统计"""
    cur = conn.cursor()
    cur.execute("""
        SELECT signal_type, COUNT(*),
               ROUND(AVG(ret5d)::numeric,4),
               ROUND(AVG(ret10d)::numeric,4),
               ROUND(AVG(ret20d)::numeric,4),
               ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP(ORDER BY ret20d)::numeric,4),
               ROUND(SUM(CASE WHEN ret20d>0 THEN 1 ELSE 0 END)::numeric/COUNT(*),4) AS win_rate
        FROM backtest_signals
        WHERE trade_date>=%s AND trade_date<=%s AND signal_type IN ('s3_panic','s2_pulse','s1_surge')
        GROUP BY signal_type
    """, (start_dt, end_dt))
    print(f"\n📊 回测统计 {start_dt}~{end_dt}:")
    print(f"  {'信号':>12s} {'数量':>6s} {'T+5':>8s} {'T+10':>8s} {'T+20':>8s} {'T+20中位':>10s} {'胜率':>6s}")
    print("  "+"-"*60)
    for r in cur.fetchall():
        print(f"  {r[0]:>12s} {r[1]:>6d} {float(r[2] or 0):>+7.2%} {float(r[3] or 0):>+7.2%} {float(r[4] or 0):>+7.2%} {float(r[5] or 0):>+7.2%} {float(r[6] or 0):>6.1%}")
    
    for sig_label, sig in [('S3', 's3_panic'), ('S2', 's2_pulse')]:
        cur.execute("""
            SELECT v7_score_ntile, COUNT(*),
                   ROUND(AVG(ret20d)::numeric,4)
            FROM (
                SELECT ret20d, NTILE(5) OVER(ORDER BY v7_score) AS v7_score_ntile
                FROM backtest_signals
                WHERE signal_type=%s AND trade_date>=%s AND trade_date<=%s
                  AND v7_score IS NOT NULL
            ) sub
            GROUP BY v7_score_ntile
            ORDER BY v7_score_ntile
        """, (sig, start_dt, end_dt))
        rows = cur.fetchall()
        if rows:
            print(f"\n  {sig_label} V7评分五等分:")
            for r in rows:
                print(f"    Q{r[0]}: {r[1]}条 T+20: {float(r[2] or 0):+.2%}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--init', action='store_true')
    parser.add_argument('--start', default='2015-01-05')
    parser.add_argument('--end', default='2026-05-27')
    parser.add_argument('--v7', action='store_true', help='回填V7评分')
    parser.add_argument('--stats', action='store_true', help='统计')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    
    conn = psycopg2.connect(DB_FAST)
    conn.set_session(autocommit=False)
    
    if args.init:
        init_table(conn)
        conn.close()
        sys.exit(0)
    
    start_dt = date.fromisoformat(args.start)
    end_dt = date.fromisoformat(args.end)
    
    if args.v7:
        backfill_v7_scores(conn, start_dt, end_dt)
    elif args.stats:
        stats(conn, start_dt, end_dt)
    else:
        # 信号生成
        run_segment(conn, start_dt, end_dt, verbose=args.verbose)
    
    conn.close()
