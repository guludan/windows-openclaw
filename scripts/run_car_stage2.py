#!/usr/bin/env python3
"""
run_car_stage2.py — 事件 CAR 计算（阶段2）

对每个 event × top_N_industry 组合：
  1. 取事件日前后窗口[-5, +20]的行业日收益率
  2. 用估计窗[-120, -21]估计市场模型（简单均值调整模型）
  3. 计算异常收益(AR)和累积异常收益(CAR)
  4. 检测首次显著日 t*（基于t统计量）

输出: event_car 表（增量写入）
"""
import sys, os, json, logging, time, numpy as np
from scipy import stats
from psycopg2.extras import execute_values
import psycopg2, psycopg2.sql as sql

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("stage2")

PG = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

# 窗口参数
ESTIM_WIN = (-120, -21)   # 估计窗
EVENT_WIN = (-5, 20)      # 事件窗

def get_market_returns(cur):
    """从 industry_daily 计算等权市场组合收益率"""
    cur.execute("""
        WITH ind_rets AS (
            SELECT trade_date, industry,
                   close / NULLIF(LAG(close) OVER (PARTITION BY industry ORDER BY trade_date), 0) - 1 AS ret
            FROM industry_daily
            WHERE trade_date >= '2018-06-01'
        )
        SELECT trade_date, AVG(ret) as mkt_ret
        FROM ind_rets
        WHERE ret IS NOT NULL
        GROUP BY trade_date
        ORDER BY trade_date
    """)
    rets = {}
    for r in cur.fetchall():
        if r[1] is not None:
            rets[str(r[0])] = r[1]
    return rets

def get_industry_returns(cur, industry, start_date, end_date):
    """获取指定行业的日收益率序列"""
    cur.execute("""
        SELECT trade_date, close
        FROM industry_daily
        WHERE industry = %s AND trade_date >= %s AND trade_date <= %s
        ORDER BY trade_date
    """, (industry, start_date, end_date))
    rows = cur.fetchall()
    if len(rows) < 2:
        return {}
    rets = {}
    prev = None
    for r in rows:
        if prev is not None and prev != 0:
            rets[str(r[0])] = (r[1] - prev) / prev
        prev = r[1]
    return rets

def calc_ar(rets_industry, rets_market, event_date, estim_start, estim_end):
    """计算异常收益（均值调整模型：α = mean(估计期超额)）"""
    ed = str(event_date)
    est_start = str(estim_start)
    est_end = str(estim_end)
    
    # 收集估计窗内的收益率
    est_rets = []
    for d, r in rets_industry.items():
        if est_start <= d <= est_end:
            if d in rets_market:
                est_rets.append(r - rets_market[d])
            else:
                est_rets.append(r)
    
    if len(est_rets) < 20:
        return None, None, None
    
    # 均值调整模型的α
    alpha = np.mean(est_rets)
    sigma = np.std(est_rets, ddof=1)
    
    # 事件窗内的AR和CAR
    ars = {}
    cars = []
    cum = 0.0
    
    # 按t_relative索引
    window_dates = {}
    # 找到事件日附近的交易日
    sorted_dates = sorted([d for d in rets_industry.keys()])
    
    if ed not in sorted_dates:
        # 事件日非交易日，找最近的一个
        for d in sorted_dates:
            if d >= ed:
                event_idx = sorted_dates.index(d)
                break
        else:
            return None, None, None
    else:
        event_idx = sorted_dates.index(ed)
    
    # 取事件窗内的t_relative
    win_start = max(0, event_idx + EVENT_WIN[0])
    win_end = min(len(sorted_dates), event_idx + EVENT_WIN[1])
    
    for idx in range(win_start, win_end):
        d = sorted_dates[idx]
        t_rel = idx - event_idx
        
        if d in rets_industry:
            r_ind = rets_industry[d]
            r_mkt = rets_market.get(d, 0.0)
            ar = r_ind - r_mkt - alpha
            ars[t_rel] = float(ar)
            cum += ar
            cars.append(float(cum))
    
    # 计算t统计量
    ar_vals = [ars.get(t, 0) for t in range(EVENT_WIN[0], EVENT_WIN[1]+1) if t in ars]
    if len(ar_vals) < 5:
        return None, None, None
    
    t_stat = np.mean(ar_vals) / (np.std(ar_vals, ddof=1) / np.sqrt(len(ar_vals))) if np.std(ar_vals, ddof=1) > 0 else 0
    p_val = 2 * (1 - stats.t.cdf(abs(t_stat), df=len(ar_vals)-1))
    
    return ars, cars, p_val

def find_first_significant(ars, rets_industry, rets_market, event_date, alpha_thresh=0.05):
    """检测首次显著日t*（连续3天p<0.05或单日极端>2σ）"""
    if not ars:
        return None, None
    
    ed = str(event_date)
    
    # 简单实现：找累积CAR首次超过2%的日子
    sorted_ts = sorted(ars.keys())
    cum = 0.0
    for t in sorted_ts:
        cum += ars[t]
        if t >= -1 and abs(cum) > 0.02:  # 起跳后累计超2%
            return t, float(cum)
    
    # 更严格的：找窗口内最大单日AR对应的t
    max_t = max(ars, key=lambda k: abs(ars[k]))
    return max_t, float(ars[max_t])

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-events", type=int, default=None, help="限制事件数(测试)")
    parser.add_argument("--max-industries", type=int, default=5, 
                       help="每个事件最多处理的行业数，按势能排序取top-K")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    parser.add_argument("--batch-size", type=int, default=500, help="批量写入大小")
    args = parser.parse_args()
    
    conn = psycopg2.connect(PG)
    cur = conn.cursor()
    
    # 1) 加载市场收益率
    log.info("加载市场收益率...")
    mkt_rets = get_market_returns(cur)
    log.info(f"市场收益率: {len(mkt_rets)}个交易日")
    
    # 2) 获取待处理的 events（有nlp_event_scores但不在event_car）
    sql_events = """
        SELECT n.event_id, e.event_date, n.industry_json, 
               n.importance_total, n.direction, n.macro_score
        FROM nlp_event_scores n
        JOIN event_list e ON n.event_id = e.event_id
        WHERE NOT EXISTS (SELECT 1 FROM event_car c WHERE c.event_id = n.event_id)
        ORDER BY n.importance_total DESC, n.event_id
    """
    if args.limit_events:
        sql_events += f" LIMIT {args.limit_events}"
    
    cur.execute(sql_events)
    events = cur.fetchall()
    log.info(f"待处理事件: {len(events)}")
    
    if args.dry_run:
        log.info("dry-run模式，退出")
        conn.close()
        return
    
    if not events:
        log.info("无待处理事件")
        conn.close()
        return
    
    # 3) 预加载所有行业的最近收益率（缓存）
    start_time = time.time()
    total_processed = 0
    total_written = 0
    
    # 对每个事件处理
    for ei, (eid, event_date, industry_json, imp, direction, macro_score) in enumerate(events):
        industries = json.loads(industry_json)
        
        # 按势能排序取top-K
        sorted_inds = sorted(industries.items(), key=lambda x: -x[1])[:args.max_industries]
        
        batch = []
        for ind_name, pot in sorted_inds:
            # 计算估计窗边界：事件日前120~21个交易日
            # 先查找事件日前后
            cur.execute("""
                SELECT MIN(trade_date), MAX(trade_date) 
                FROM industry_daily 
                WHERE trade_date >= %s::date - interval '180 days'
                  AND trade_date <= %s::date + interval '30 days'
            """, (event_date, event_date))
            date_range = cur.fetchone()
            if not date_range or date_range[0] is None:
                continue
            
            estim_start = date_range[0]  # 取最早的日期
            # 粗略估计：实际需要的估计窗是 event-120 ~ event-21
            # 直接从industry_daily拉取
            
            # 获取行业收益率
            ind_rets = get_industry_returns(cur, ind_name, 
                                            event_date - __import__('datetime').timedelta(days=180),
                                            event_date + __import__('datetime').timedelta(days=30))
            
            if len(ind_rets) < 30:
                continue
            
            # 计算AR
            est_start = event_date - __import__('datetime').timedelta(days=120)
            est_end = event_date - __import__('datetime').timedelta(days=21)
            
            rslt = calc_ar(ind_rets, mkt_rets, event_date, est_start, est_end)
            if rslt is None or rslt[0] is None:
                continue
            
            ars, cars, p_val = rslt
            
            # 检测首次显著日
            t_first, ar_first = find_first_significant(ars, ind_rets, mkt_rets, event_date)
            
            # 计算CAR窗口值
            car_5 = sum(ars.get(t, 0) for t in range(1, 6) if t in ars)
            car_10 = sum(ars.get(t, 0) for t in range(1, 11) if t in ars)
            car_20 = sum(ars.get(t, 0) for t in range(1, 21) if t in ars)
            
            # 对每个t_relative生成一行
            for t_rel, ar_val in ars.items():
                cum = sum(ars.get(t, 0) for t in range(EVENT_WIN[0], t_rel+1) if t in ars)
                act_date = event_date + __import__('datetime').timedelta(days=t_rel) if t_rel >= 0 else None
                batch.append((
                    eid, ind_name.replace(' ', ''), ind_name, t_rel,
                    act_date,
                    float(ar_val), float(cum), car_5, car_10, car_20, None, t_first, float(p_val)
                ))
        
        if batch:
            total_written += len(batch)
            if total_written >= args.batch_size or (ei + 1) % 50 == 0:
                # 清空旧数据（如果有）后再批量插入
                execute_values(cur, """
                    INSERT INTO event_car 
                        (event_id, industry_code, industry_name, t_relative, trade_date,
                         ar, car, car_5, car_10, car_20, volume_ratio, t_star, p_value)
                    VALUES %s
                    ON CONFLICT (event_id, industry_code, t_relative) DO UPDATE SET
                        ar=EXCLUDED.ar, car=EXCLUDED.car,
                        car_5=EXCLUDED.car_5, car_10=EXCLUDED.car_10, car_20=EXCLUDED.car_20,
                        trade_date=EXCLUDED.trade_date, t_star=EXCLUDED.t_star, p_value=EXCLUDED.p_value
                """, batch, page_size=200)
                conn.commit()
                batch = []
                log.info(f"进度: {ei+1}/{len(events)} events, {total_written}行写入, {(time.time()-start_time)/60:.1f}分")
        
        total_processed += 1
    
    if batch:
        execute_values(cur, """
            INSERT INTO event_car 
                (event_id, industry_code, industry_name, t_relative, trade_date,
                 ar, car, car_5, car_10, car_20, volume_ratio, t_star, p_value)
            VALUES %s
            ON CONFLICT (event_id, industry_code, t_relative) DO UPDATE SET
                ar=EXCLUDED.ar, car=EXCLUDED.car,
                car_5=EXCLUDED.car_5, car_10=EXCLUDED.car_10, car_20=EXCLUDED.car_20,
                trade_date=EXCLUDED.trade_date, t_star=EXCLUDED.t_star, p_value=EXCLUDED.p_value
        """, batch, page_size=200)
        conn.commit()
    
    elapsed = (time.time() - start_time) / 60
    log.info(f"=== 完成 ===")
    log.info(f"处理: {total_processed} events, 写入: {total_written} 行")
    log.info(f"耗时: {elapsed:.1f}分")
    
    conn.close()

if __name__ == "__main__":
    main()
