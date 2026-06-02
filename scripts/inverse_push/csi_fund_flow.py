#!/usr/bin/env python3
"""
CSI 催化稳定性指数 v3 — 个股资金流全量拉取 + 行业聚合

数据源：akshare.stock_individual_fund_flow（东方财富push2his，直连关代理）
  字段：主力/超大单/大单/中单/小单 的 净额+占比, 收盘价, 涨跌幅
  历史范围：最近~121条（约6个月）
  
特点：
  - 自动清除代理环境变量（东财直连）
  - 增量模式：只拉fund_flow表中未有的股票
  - screen 后台跑全量无压力

PG表：
  fund_flow      — 个股资金流日线（PK=code+trade_date）
  csi_industry   — 申万行业聚合CSI + 信号
  
工作流：
  1. backfill: 全量回填（每个活跃股票拉~6个月历史）
  2. incremental: 每日增量（只拉当天未有的股票）
  3. aggregate: 行业聚合 + 信号生成
"""
import os, sys, time, json, argparse, math
from datetime import date, datetime, timedelta
import numpy as np
from collections import defaultdict

# ⚠️ 东财push2his必须直连，清除所有代理
for k in list(os.environ.keys()):
    if 'proxy' in k.lower() or 'PROXY' in k:
        os.environ.pop(k, None)

sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
import data_finance as df
import psycopg2
import psycopg2.extras as pge

# ━━━ 配置 ━━━
REQUEST_SLEEP = 0.3       # 单次请求间隔（秒）
BATCH_DB = 500            # 每批写入PG条数
RETRY_MAX = 3              # 失败重试次数
RETRY_WAIT = 3             # 重试等待秒数

# akshare 延迟导入（让环境变量清理先跑）
_lazy_ak = None
def _ak():
    global _lazy_ak
    if _lazy_ak is None:
        import akshare as ak
        _lazy_ak = ak
    return _lazy_ak


def _get_conn():
    return psycopg2.connect(
        host=df.PG_HOST, port=df.PG_PORT,
        dbname=df.PG_DB, user=df.PG_USER, password=df.PG_PASS
    )


def get_market(code):
    """判断市场代码"""
    if code.startswith('6'):
        return 'sh'
    elif code.startswith(('0', '3')):
        return 'sz'
    elif code.startswith(('8', '4')):
        return 'bj'
    return 'sh'


def get_stock_list():
    """获取全量活跃股票"""
    r,_ = df._pg_execute("""
        SELECT code FROM stock_list WHERE active=TRUE 
        AND code NOT LIKE '8%'  -- 北交所（akshare不支持bj市场）
        ORDER BY code
    """)
    codes = [x[0] for x in r]
    print(f"📡 股票列表: {len(codes)}只")
    return codes


def fetch_stock_fund_flow(code, market):
    """
    用akshare拉单只股票资金流历史
    返回 [(trade_date, record_dict), ...]
    """
    for attempt in range(RETRY_MAX):
        try:
            df_fund = _ak().stock_individual_fund_flow(stock=code, market=market)
            if df_fund is None or len(df_fund) == 0:
                return None
                
            records = []
            for _, row in df_fund.iterrows():
                records.append({
                    'trade_date': str(row['日期']),
                    'main_net': float(row.get('主力净流入-净额', 0) or 0),
                    'main_ratio': float(row.get('主力净流入-净占比', 0) or 0),
                    'super_net': float(row.get('超大单净流入-净额', 0) or 0),
                    'super_ratio': float(row.get('超大单净流入-净占比', 0) or 0),
                    'big_net': float(row.get('大单净流入-净额', 0) or 0),
                    'big_ratio': float(row.get('大单净流入-净占比', 0) or 0),
                    'mid_net': float(row.get('中单净流入-净额', 0) or 0),
                    'mid_ratio': float(row.get('中单净流入-净占比', 0) or 0),
                    'small_net': float(row.get('小单净流入-净额', 0) or 0),
                    'small_ratio': float(row.get('小单净流入-净占比', 0) or 0),
                    'close': float(row.get('收盘价', 0) or 0),
                    'chg_pct': float(row.get('涨跌幅', 0) or 0),
                })
            return records
        except Exception as e:
            if attempt < RETRY_MAX - 1:
                time.sleep(RETRY_WAIT)
                continue
            return None


# ═══════════════════════════════════════════════
# 全量回填
# ═══════════════════════════════════════════════

def backfill_all():
    """
    全量资金流回填：
    对每个活跃股票拉~6个月历史数据
    预计：5000只 × 0.3s = 25分钟
    """
    codes = get_stock_list()
    print(f"📡 全量回填: {len(codes)}只股票")
    print(f"  ⚠️  每只拉~121条（6个月）")
    print(f"  预估时间: {len(codes)*REQUEST_SLEEP/60:.0f}分钟")
    
    # 查已有数据量
    r_existing,_ = df._pg_execute("SELECT COUNT(DISTINCT code) FROM fund_flow")
    existing_cnt = r_existing[0][0] if r_existing and r_existing[0][0] else 0
    if existing_cnt > 0:
        print(f"  已有{existing_cnt}只股票的数据，将跳过已有股票（仅拉新）")
    
    existing_set = set()
    if existing_cnt > 0:
        r_codes,_ = df._pg_execute("SELECT DISTINCT code FROM fund_flow")
        existing_set = set(x[0] for x in r_codes)
        codes = [c for c in codes if c not in existing_set]
        print(f"  待拉取: {len(codes)}只")
    
    conn = _get_conn()
    cur = conn.cursor()
    total_written = 0
    total_failed = 0
    t0 = time.time()
    
    for idx, code in enumerate(codes):
        market = get_market(code)
        records = fetch_stock_fund_flow(code, market)
        
        if records:
            # 获取成交额和市值（从market_snapshots匹配）
            dates_in = [r['trade_date'] for r in records]
            min_d, max_d = min(dates_in), max(dates_in)
            amt_r,_ = df._pg_execute(
                "SELECT trade_date::text, amount, total_mv FROM market_snapshots WHERE code=%s AND trade_date BETWEEN %s AND %s",
                (code, min_d, max_d)
            )
            amt_map = {x[0]: (float(x[1]) if x[1] else 0, float(x[2]) if x[2] else 0) for x in amt_r}
            
            # 批量写入
            batch = []
            for r in records:
                td = r['trade_date']
                amount, total_mv = amt_map.get(td, (0, 0))
                batch.append((
                    code, td,
                    r['main_net'], r['main_ratio'],
                    r['small_net'], r['small_ratio'],
                    r['mid_net'], r['mid_ratio'],
                    r['big_net'], r['big_ratio'],
                    r['super_net'], r['super_ratio'],
                    amount, r['close'], r['chg_pct'], total_mv,
                ))
            
            if batch:
                pge.execute_values(cur, """
                    INSERT INTO fund_flow (code,trade_date,main_net,main_ratio,small_net,small_ratio,
                        mid_net,mid_ratio,big_net,big_ratio,super_net,super_ratio,
                        amount,close,chg_pct,total_mv)
                    VALUES %s
                    ON CONFLICT (code,trade_date) DO UPDATE SET
                        main_net=EXCLUDED.main_net, main_ratio=EXCLUDED.main_ratio,
                        small_net=EXCLUDED.small_net, small_ratio=EXCLUDED.small_ratio,
                        mid_net=EXCLUDED.mid_net, mid_ratio=EXCLUDED.mid_ratio,
                        big_net=EXCLUDED.big_net, big_ratio=EXCLUDED.big_ratio,
                        super_net=EXCLUDED.super_net, super_ratio=EXCLUDED.super_ratio,
                        amount=EXCLUDED.amount, close=EXCLUDED.close, chg_pct=EXCLUDED.chg_pct,
                        total_mv=EXCLUDED.total_mv
                """, batch, page_size=BATCH_DB)
                conn.commit()
                total_written += len(batch)
        else:
            total_failed += 1
        
        # 进度
        if (idx+1) % 200 == 0:
            pct = (idx+1)/len(codes)*100
            elapsed = time.time() - t0
            remain = (len(codes)-idx-1) * REQUEST_SLEEP
            print(f"  [{idx+1}/{len(codes)}] {pct:.0f}% | {total_written}条写入, {total_failed}失败 | {elapsed:.0f}s / 预计剩余{remain:.0f}s")
        
        time.sleep(REQUEST_SLEEP)
    
    elapsed = time.time() - t0
    print(f"\n✅ 全量回填完成! {total_written}条写入, {total_failed}只失败")
    print(f"  总耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"  平均: {elapsed/len(codes):.2f}s/只")
    
    # 统计
    r_stat,_ = df._pg_execute("""
        SELECT MIN(trade_date)::text, MAX(trade_date)::text, COUNT(*) 
        FROM fund_flow
    """)
    print(f"  PG fund_flow: {r_stat[0][0]} ~ {r_stat[0][1]}, {r_stat[0][2]}条")
    
    cur.close()
    conn.close()


# ═══════════════════════════════════════════════
# 增量更新
# ═══════════════════════════════════════════════

def incremental():
    """每日增量：只拉当天未有的股票"""
    codes = get_stock_list()
    today_str = str(date.today())
    print(f"📡 增量更新: {today_str}")
    
    # 查当日已有
    existing,_ = df._pg_execute(
        "SELECT DISTINCT code FROM fund_flow WHERE trade_date=%s", (today_str,)
    )
    existing_set = set(x[0] for x in existing)
    to_fetch = [c for c in codes if c not in existing_set]
    print(f"  已有{len(existing_set)}只, 待拉{len(to_fetch)}只")
    
    if not to_fetch:
        print("  ✅ 全部已更新")
        return True
    
    # 获取今日成交额/市值（一次性批量）
    snap_r,_ = df._pg_execute(
        "SELECT code, amount, total_mv FROM market_snapshots WHERE trade_date=%s",
        (today_str,)
    )
    snap_map = {x[0]: (float(x[1]) if x[1] else 0, float(x[2]) if x[2] else 0) for x in snap_r}
    
    conn = _get_conn()
    cur = conn.cursor()
    t0 = time.time()
    written = 0
    failed = 0
    
    for idx, code in enumerate(to_fetch):
        market = get_market(code)
        records = fetch_stock_fund_flow(code, market)
        
        if records:
            # 只写今天的数据
            today_record = None
            for r in records:
                if r['trade_date'] >= today_str:
                    today_record = r
                    break
                elif r['trade_date'] == today_str:
                    today_record = r
                    break
            
            if today_record:
                amount, total_mv = snap_map.get(code, (0, 0))
                r = today_record
                cur.execute("""
                    INSERT INTO fund_flow (code,trade_date,main_net,main_ratio,
                        small_net,small_ratio,mid_net,mid_ratio,
                        big_net,big_ratio,super_net,super_ratio,
                        amount,close,chg_pct,total_mv)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (code,trade_date) DO UPDATE SET
                        main_net=EXCLUDED.main_net, main_ratio=EXCLUDED.main_ratio,
                        small_net=EXCLUDED.small_net, small_ratio=EXCLUDED.small_ratio,
                        mid_net=EXCLUDED.mid_net, mid_ratio=EXCLUDED.mid_ratio,
                        big_net=EXCLUDED.big_net, big_ratio=EXCLUDED.big_ratio,
                        super_net=EXCLUDED.super_net, super_ratio=EXCLUDED.super_ratio,
                        amount=EXCLUDED.amount, close=EXCLUDED.close, chg_pct=EXCLUDED.chg_pct,
                        total_mv=EXCLUDED.total_mv
                """, (code, today_str, r['main_net'], r['main_ratio'],
                      r['small_net'], r['small_ratio'],
                      r['mid_net'], r['mid_ratio'],
                      r['big_net'], r['big_ratio'],
                      r['super_net'], r['super_ratio'],
                      amount, r['close'], r['chg_pct'], total_mv))
                written += 1
            else:
                failed += 1
        else:
            failed += 1
        
        if (idx+1) % 500 == 0:
            conn.commit()
            elapsed = time.time() - t0
            print(f"  [{idx+1}/{len(to_fetch)}] 写入{written}条, 失败{failed}只 | {elapsed:.0f}s")
        
        time.sleep(REQUEST_SLEEP)
    
    conn.commit()
    elapsed = time.time() - t0
    print(f"\n✅ 增量更新完成: {written}条, 失败{failed}只, 耗时{elapsed:.0f}s")
    
    cur.close()
    conn.close()
    
    return failed < len(to_fetch) * 0.5  # 失败率低于50%算成功


# ═══════════════════════════════════════════════
# 行业聚合 + 信号
# ═══════════════════════════════════════════════

def aggregate(target_date=None):
    """按申万行业聚合资金流 → 写入csi_industry表"""
    if target_date is None:
        r,_ = df._pg_execute("SELECT MAX(trade_date) FROM fund_flow")
        if not r or not r[0][0]:
            print("❌ fund_flow无数据")
            return
        target_date = r[0][0]
    
    ds = target_date.strftime('%Y-%m-%d') if hasattr(target_date, 'strftime') else str(target_date)
    print(f"\n📊 CSI行业聚合: {ds}")
    
    # 1. 获取当日fund_flow
    r,_ = df._pg_execute("""
        SELECT code, main_net, main_ratio, amount, total_mv 
        FROM fund_flow WHERE trade_date=%s
    """, (ds,))
    
    if not r:
        print(f"  ❌ {ds} 无fund_flow数据")
        return
    
    stock_fund = {x[0]: {
        'main_net': float(x[1]) if x[1] else 0,
        'main_ratio': float(x[2]) if x[2] else 0,
        'amount': float(x[3]) if x[3] else 0,
        'total_mv': float(x[4]) if x[4] else 0,
    } for x in r}
    print(f"  📥 个股资金流: {len(stock_fund)}只")
    
    # 2. 行业映射
    r2,_ = df._pg_execute("SELECT stock_code, industry_name FROM industry_components_sw")
    stock_industry = {}
    industry_stocks = defaultdict(list)
    for scode, ind_name in r2:
        clean_code = scode.split('.')[0]
        stock_industry[clean_code] = ind_name
        industry_stocks[ind_name].append(clean_code)
    
    print(f"  🏢 行业: {len(industry_stocks)}个")
    
    # 3. 聚合
    csi_rows = []
    for ind_name in sorted(industry_stocks.keys()):
        stocks = industry_stocks[ind_name]
        fund_ratios = []
        amounts = []
        mvs = []
        main_nets = []
        valid = 0
        
        for sc in stocks:
            if sc in stock_fund:
                f = stock_fund[sc]
                amt = f['amount']
                if amt and amt > 0:
                    fr = f['main_net'] / amt
                else:
                    fr = f['main_ratio'] / 100 if f['main_ratio'] else 0
                
                fund_ratios.append(fr)
                amounts.append(amt)
                mvs.append(f['total_mv'])
                main_nets.append(f['main_net'])
                valid += 1
        
        if valid < 3:
            continue
        
        farr = np.array(fund_ratios)
        aarr = np.array(amounts)
        marr = np.array(mvs)
        narr = np.array(main_nets)
        
        csi_equal = float(np.mean(farr))
        csi_amount_w = float(np.average(farr, weights=np.maximum(aarr, 1)))
        csi_mv_w = float(np.average(farr, weights=np.maximum(marr, 1))) if np.sum(np.maximum(marr, 1)) > 0 else csi_equal
        
        pos_ratio = float(np.mean(farr > 0))
        net_buy = float(np.mean(farr[farr > 0])) if any(farr > 0) else 0
        net_sell = float(np.mean(farr[farr < 0])) if any(farr < 0) else 0
        total_main = float(np.sum(narr))
        
        csi_rows.append({
            'trade_date': ds, 'industry': ind_name,
            'csi_equal': round(csi_equal, 6),
            'csi_amount_weighted': round(csi_amount_w, 6),
            'csi_mv_weighted': round(csi_mv_w, 6),
            'positive_ratio': round(pos_ratio, 4),
            'net_buy_rate': round(net_buy, 6),
            'net_sell_rate': round(net_sell, 6),
            'total_main_net': round(total_main, 2),
            'valid_stocks': valid,
        })
    
    # 4. 信号生成（横截面百分位）
    if csi_rows:
        csi_vals = [r['csi_amount_weighted'] for r in csi_rows]
        p20, p40, p60, p80 = np.percentile(csi_vals, [20, 40, 60, 80])
        n = len(csi_vals)
        for r_rec in csi_rows:
            val = r_rec['csi_amount_weighted']
            pct = sum(1 for v in csi_vals if v <= val) / n
            if val >= p80:
                r_rec['signal'] = 'strong_catalyst'
            elif val >= p60:
                r_rec['signal'] = 'catalyst'
            elif val >= p40:
                r_rec['signal'] = 'neutral'
            elif val >= p20:
                r_rec['signal'] = 'weak_drain'
            else:
                r_rec['signal'] = 'strong_drain'
    
    # 5. 写入csi_industry
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM csi_industry WHERE trade_date=%s", (ds,))
    
    pge.execute_values(cur, """
        INSERT INTO csi_industry (trade_date, industry,
            csi_equal, csi_amount_weighted, csi_mv_weighted,
            positive_ratio, net_buy_rate, net_sell_rate,
            total_main_net, valid_stocks, signal)
        VALUES %s
    """, [
        (r['trade_date'], r['industry'],
         r['csi_equal'], r['csi_amount_weighted'], r['csi_mv_weighted'],
         r['positive_ratio'], r['net_buy_rate'], r['net_sell_rate'],
         r['total_main_net'], r['valid_stocks'], r['signal'])
        for r in csi_rows
    ])
    conn.commit()
    cur.close()
    conn.close()
    
    print(f"\n📊 CSI聚合完成: {len(csi_rows)}个行业")
    if csi_rows:
        print(f"  CSI中位数: {np.median(csi_vals):+.6f}")
        print(f"  范围: {min(csi_vals):+.6f} ~ {max(csi_vals):+.6f}")
        
        sorted_rows = sorted(csi_rows, key=lambda x: x['csi_amount_weighted'])
        print(f"\n  🔥 最强CSI (前5):")
        for r_rec in sorted_rows[-5:]:
            print(f"    {r_rec['industry']:12s} {r_rec['csi_amount_weighted']:+.6f}  {r_rec['signal']}")
        print(f"\n  🧊 最弱CSI (后5):")
        for r_rec in sorted_rows[:5]:
            print(f"    {r_rec['industry']:12s} {r_rec['csi_amount_weighted']:+.4f}  {r_rec['signal']}")
    
    sig_counts = Counter(r['signal'] for r in csi_rows)
    print(f"\n  📊 信号分布: {dict(sig_counts)}")


if __name__ == '__main__':
    from collections import Counter
    
    parser = argparse.ArgumentParser(description='CSI v3 — 个股资金流（东财akshare直连）')
    parser.add_argument('--mode', choices=['backfill', 'incremental', 'aggregate'],
                       default='incremental')
    args = parser.parse_args()
    
    if args.mode == 'backfill':
        print("""
╔══════════════════════════════════════════════╗
║   CSI 全量回填                               ║
║   数据源: 东方财富 stock_individual_fund_flow  ║
║   范围: ~6个月 (最近121条/只)                 ║
║   预估: 5000只 × 0.3s = 25分钟               ║
║   建议: screen后台跑                          ║
╚══════════════════════════════════════════════╝
        """)
        backfill_all()
        aggregate()
    
    elif args.mode == 'incremental':
        ok = incremental()
        if ok:
            aggregate()
        else:
            print("⚠️ 增量更新失败率过高，跳过行业聚合")
    
    elif args.mode == 'aggregate':
        aggregate()
