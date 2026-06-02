#!/usr/bin/env python3
"""
CSI 催化稳定性指数 v4 — 基于龙虎榜涨停板机构资金聚合

核心思路：
  CSI = 龙虎榜上榜股票（涨停板为主）的机构净买额，按申万一级行业聚合
  - 兼有"机构认可度"（机构净买入）和"板块热度"（上榜涨停密度）
  
数据源（稳定可靠）：
  1. stock_lhb_detail_em — 龙虎榜日明细（每日80只左右）
  2. stock_lhb_jgmmtj_em — 机构买卖明细（每日数千条，按日查询）
  
输出：
  csi_lhb_industry — 行业级CSI（PK: trade_date + industry）

信号含义：
  strong_catalyst — 行业有多只股票上榜 + 机构净买入 > 0
  catalyst        — 有股票上榜且机构净买入 > 0
  neutral         — 有上榜但无机构净买入
  weak            — 无上榜或净卖出

回填策略：
  - 龙虎榜历史可拉至2014年（东方财富龙虎榜数据完整）
  - 每交易日逐日回填（~2000个交易日 × 1秒/天 ≈ 30分钟全量）
  - 每日增量只拉当日
"""
import os, sys, time, json, argparse
from datetime import date, datetime, timedelta
import numpy as np
from collections import defaultdict, Counter

sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
import data_finance as df
import psycopg2
import psycopg2.extras as pge

REQUEST_SLEEP = 0.5       # 请求间隔（龙虎榜频率低，可以慢一点）
BATCH_DB = 100

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


def ensure_tables():
    """确保csi_lhb_industry表和csi_lhb_detail表存在"""
    conn = _get_conn()
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS csi_lhb_detail (
            trade_date DATE NOT NULL,
            code VARCHAR(10) NOT NULL,
            name VARCHAR(50),
            close DOUBLE PRECISION,
            change_pct DOUBLE PRECISION,
            lhb_net_buy DOUBLE PRECISION,
            lhb_buy DOUBLE PRECISION,
            lhb_sell DOUBLE PRECISION,
            total_amount DOUBLE PRECISION,
            net_ratio DOUBLE PRECISION,
            turnover DOUBLE PRECISION,
            float_mv DOUBLE PRECISION,
            reason TEXT,
            inst_buyers INTEGER DEFAULT 0,
            inst_sellers INTEGER DEFAULT 0,
            inst_net_buy DOUBLE PRECISION DEFAULT 0,
            inst_net_ratio DOUBLE PRECISION DEFAULT 0,
            industry VARCHAR(50),
            is_limit_up BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (trade_date, code)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS csi_lhb_industry (
            trade_date DATE NOT NULL,
            industry VARCHAR(50) NOT NULL,
            lhb_stock_count INTEGER DEFAULT 0,        -- 上榜股票数
            limit_up_count INTEGER DEFAULT 0,          -- 涨停股票数
            inst_stock_count INTEGER DEFAULT 0,         -- 有机构参与的上榜股票数
            total_lhb_net_buy DOUBLE PRECISION DEFAULT 0,  -- 龙虎榜净买额合计
            total_inst_net_buy DOUBLE PRECISION DEFAULT 0, -- 机构净买额合计
            total_inst_buy DOUBLE PRECISION DEFAULT 0,     -- 机构买入额合计
            total_inst_sell DOUBLE PRECISION DEFAULT 0,    -- 机构卖出额合计
            avg_lhb_net_buy DOUBLE PRECISION DEFAULT 0,    -- 平均净买额
            avg_inst_net_buy DOUBLE PRECISION DEFAULT 0,   -- 平均机构净买额
            positive_inst_ratio DOUBLE PRECISION DEFAULT 0, -- 机构净买入>0的股票占比
            total_lhb_amount DOUBLE PRECISION DEFAULT 0,   -- 龙虎榜总成交额
            csi_score DOUBLE PRECISION DEFAULT 0,          -- 综合催化得分
            signal VARCHAR(20) DEFAULT 'neutral',
            PRIMARY KEY (trade_date, industry)
        )
    """)
    
    conn.commit()
    cur.close()
    conn.close()
    print("✅ csi_lhb_detail / csi_lhb_industry 表已就绪")


def get_industry(code):
    """从industry_components_sw获取申万一级行业"""
    r,_ = df._pg_execute(
        "SELECT industry_name FROM industry_components_sw WHERE stock_code LIKE %s",
        (code + '%',)
    )
    return r[0][0] if r else None


def parse_inst_text(text):
    """解析龙虎榜解读文本中的机构数量"""
    if not text or '机构' not in text:
        return 0, 0
    import re
    inst_buy = 0
    inst_sell = 0
    
    m_buy = re.search(r'(\d+)家机构买入', text)
    if m_buy:
        inst_buy = int(m_buy.group(1))
    
    m_sell = re.search(r'(\d+)家机构卖出', text)
    if m_sell:
        inst_sell = int(m_sell.group(1))
    
    return inst_buy, inst_sell


def fetch_lhb_day(trade_date_str):
    """
    拉取指定日期的龙虎榜数据
    返回 [(code, trade_date, record_dict), ...]
    """
    try:
        df_detail = _ak().stock_lhb_detail_em(
            start_date=trade_date_str, end_date=trade_date_str
        )
        if df_detail is None or len(df_detail) == 0:
            return None
        
        records = []
        for _, row in df_detail.iterrows():
            code = str(row['代码']).strip()
            name = str(row['名称']).strip()
            
            inst_buy, inst_sell = parse_inst_text(str(row.get('解读', '')))
            is_limit = float(row.get('涨跌幅', 0) or 0) >= 9.5
            industry = get_industry(code)
            
            net_buy = float(row.get('龙虎榜净买额', 0) or 0)
            net_buy_w = int(net_buy) / 100  # 万元→亿
            
            records.append({
                'code': code,
                'name': name,
                'close': float(row.get('收盘价', 0) or 0),
                'change_pct': float(row.get('涨跌幅', 0) or 0),
                'lhb_net_buy': float(row.get('龙虎榜净买额', 0) or 0),
                'lhb_buy': float(row.get('龙虎榜买入额', 0) or 0),
                'lhb_sell': float(row.get('龙虎榜卖出额', 0) or 0),
                'total_amount': float(row.get('龙虎榜成交额', 0) or 0),
                'net_ratio': float(row.get('净买额占总成交比', 0) or 0),
                'turnover': float(row.get('换手率', 0) or 0),
                'float_mv': float(row.get('流通市值', 0) or 0),
                'reason': str(row.get('上榜原因', '')),
                'inst_buyers': inst_buy,
                'inst_sellers': inst_sell,
                'is_limit_up': is_limit,
                'industry': industry,
            })
        
        return records
    
    except Exception as e:
        print(f"  ❌ 拉取失败: {str(e)[:80]}")
        return None


def fetch_inst_detail(trade_date_str):
    """
    拉取机构买卖明细（更精确的机构数据）
    返回 {(code): {inst_buy, inst_sell, inst_net, inst_ratio}, ...}
    注意：stock_lhb_jgmmtj_em 实际是时间段查询，返回的是区间汇总
    """
    try:
        # 用当天到当天，确保只拉当天的数据
        df_inst = _ak().stock_lhb_jgmmtj_em(
            start_date=trade_date_str, end_date=trade_date_str
        )
        if df_inst is None or len(df_inst) == 0:
            return {}
        
        inst_map = {}
        for _, row in df_inst.iterrows():
            code = str(row['代码']).strip()
            inst_map[code] = {
                'inst_buyers': int(row.get('买方机构数', 0) or 0),
                'inst_sellers': int(row.get('卖方机构数', 0) or 0),
                'inst_buy': float(row.get('机构买入总额', 0) or 0),
                'inst_sell': float(row.get('机构卖出总额', 0) or 0),
                'inst_net': float(row.get('机构买入净额', 0) or 0),
                'inst_net_ratio': float(row.get('机构净买额占总成交额比', 0) or 0),
            }
        return inst_map
    
    except Exception as e:
        return {}


def calc_industry_csi(records, inst_map):
    """计算行业级CSI"""
    # 按行业聚合
    industry_data = defaultdict(lambda: {
        'stocks': [], 'limit_up_stocks': [], 'inst_stocks': [],
        'lhb_net': 0, 'inst_net': 0, 'inst_buy_total': 0, 'inst_sell_total': 0,
        'lhb_amount': 0,
    })
    
    for r in records:
        ind = r['industry']
        if not ind:
            continue
        
        d = industry_data[ind]
        d['stocks'].append(r)
        
        if r['is_limit_up']:
            d['limit_up_stocks'].append(r)
        
        d['lhb_net'] += r['lhb_net_buy']
        d['lhb_amount'] += r['total_amount']
        
        # 机构数据（优先用inst_map的精确数据）
        inst_info = inst_map.get(r['code'], {})
        if inst_info:
            r['inst_buyers'] = inst_info.get('inst_buyers', r['inst_buyers'])
            r['inst_sellers'] = inst_info.get('inst_sellers', r['inst_sellers'])
            r['inst_net_buy'] = inst_info.get('inst_net', 0)
            r['inst_net_ratio'] = inst_info.get('inst_net_ratio', 0)
        else:
            # 从解读估算
            r['inst_net_buy'] = r['lhb_net_buy'] * 0.3 if r['inst_buyers'] > 0 and r['inst_sellers'] == 0 else 0
            r['inst_net_ratio'] = 0
        
        if r['inst_buyers'] > 0 or r['inst_sellers'] > 0:
            d['inst_stocks'].append(r)
            d['inst_net'] += r['inst_net_buy']
            d['inst_buy_total'] += inst_info.get('inst_buy', 0) if inst_info else 0
            d['inst_sell_total'] += inst_info.get('inst_sell', 0) if inst_info else 0
    
    # 计算CSI得分和信号
    csi_rows = []
    for ind, d in sorted(industry_data.items()):
        n = len(d['stocks'])
        lu = len(d['limit_up_stocks'])
        ins = len(d['inst_stocks'])
        pos_inst = sum(1 for s in d['inst_stocks'] if s['inst_net_buy'] > 0)
        
        # CSI综合得分（多维度）
        score = 0
        # 涨停密度（越高越好）
        if n > 0:
            score += lu / n * 3  # 涨停比例，最高3分
        # 机构参与度（质量分）
        score += min(ins, 5) * 1.0  # 每只机构参与的股票1分
        # 机构净买额（量分）
        if d['inst_net'] > 0:
            net_w = d['inst_net'] / 1e8  # 转亿
            score += min(net_w, 10) * 0.3
        
        csi_rows.append({
            'industry': ind,
            'lhb_stock_count': n,
            'limit_up_count': lu,
            'inst_stock_count': ins,
            'total_lhb_net_buy': round(d['lhb_net'], 2),
            'total_inst_net_buy': round(d['inst_net'], 2),
            'total_inst_buy': round(d['inst_buy_total'], 2),
            'total_inst_sell': round(d['inst_sell_total'], 2),
            'avg_lhb_net_buy': round(d['lhb_net'] / n, 2) if n > 0 else 0,
            'avg_inst_net_buy': round(d['inst_net'] / ins, 2) if ins > 0 else 0,
            'positive_inst_ratio': round(pos_inst / ins, 4) if ins > 0 else 0,
            'total_lhb_amount': round(d['lhb_amount'], 2),
            'csi_score': round(score, 4),
        })
    
    # 信号分档（横截面）
    if csi_rows:
        scores = [r['csi_score'] for r in csi_rows]
        p33, p67 = np.percentile(scores, [33, 67])
        n_total = len(scores)
        
        for r in csi_rows:
            sc = r['csi_score']
            # 加分条件
            if r['total_inst_net_buy'] > 100000000:  # 机构净买>1亿
                sc += 1
            
            if sc >= p67 + 0.5:
                r['signal'] = 'strong_catalyst'
            elif sc >= p67:
                r['signal'] = 'catalyst'
            elif sc >= p33:
                r['signal'] = 'neutral'
            elif r['lhb_stock_count'] > 0:
                r['signal'] = 'weak'
            else:
                r['signal'] = 'none'
            r['csi_score'] = round(sc, 4)
    
    return csi_rows


def process_day(trade_date_str):
    """
    处理单个交易日：拉龙虎榜 + 机构明细 → 写入detail表 → 行业聚合
    返回 (detail_count, industry_count)
    """
    # 1. 拉龙虎榜明细
    records = fetch_lhb_day(trade_date_str)
    if not records:
        return 0, 0
    
    # 2. 拉机构买卖明细（补充精确机构数据）
    inst_map = fetch_inst_detail(trade_date_str)
    
    # 3. 写入csi_lhb_detail（单只股票可能多次上榜，去重后写入）
    conn = _get_conn()
    cur = conn.cursor()
    
    cur.execute("DELETE FROM csi_lhb_detail WHERE trade_date=%s", (trade_date_str,))
    
    # 按code去重（多次上榜取合并数据）
    seen = {}
    for r in records:
        code = r['code']
        inst_info = inst_map.get(code, {})
        inst_net = inst_info.get('inst_net', 0) if inst_info else 0
        inst_ratio = inst_info.get('inst_net_ratio', 0) if inst_info else 0
        
        if code in seen:
            # 合并：累加净买额，更新机构信息
            s = seen[code]
            s['lhb_net_buy'] += r['lhb_net_buy']
            s['lhb_buy'] += r['lhb_buy']
            s['lhb_sell'] += r['lhb_sell']
            s['total_amount'] += r['total_amount']
            if inst_net != 0:
                s['inst_net_buy'] = inst_net
                s['inst_net_ratio'] = inst_ratio
            if r['inst_buyers'] > s.get('inst_buyers', 0):
                s['inst_buyers'] = r['inst_buyers']
            if r['inst_sellers'] > s.get('inst_sellers', 0):
                s['inst_sellers'] = r['inst_sellers']
        else:
            seen[code] = {
                'name': r['name'], 'close': r['close'],
                'change_pct': r['change_pct'],
                'lhb_net_buy': r['lhb_net_buy'],
                'lhb_buy': r['lhb_buy'],
                'lhb_sell': r['lhb_sell'],
                'total_amount': r['total_amount'],
                'net_ratio': r['net_ratio'],
                'turnover': r['turnover'],
                'float_mv': r['float_mv'],
                'reason': r['reason'],
                'inst_buyers': r['inst_buyers'],
                'inst_sellers': r['inst_sellers'],
                'inst_net_buy': inst_net,
                'inst_net_ratio': inst_ratio,
                'industry': r['industry'],
                'is_limit_up': r['is_limit_up'],
            }
    
    batch = []
    for code, r in seen.items():
        batch.append((
            trade_date_str, code, r['name'],
            r['close'], r['change_pct'],
            r['lhb_net_buy'], r['lhb_buy'], r['lhb_sell'],
            r['total_amount'], r['net_ratio'],
            r['turnover'], r['float_mv'],
            r['reason'], r['inst_buyers'], r['inst_sellers'],
            r['inst_net_buy'], r['inst_net_ratio'],
            r['industry'], r['is_limit_up'],
        ))
    
    if batch:
        pge.execute_values(cur, """
            INSERT INTO csi_lhb_detail 
                (trade_date, code, name, close, change_pct,
                 lhb_net_buy, lhb_buy, lhb_sell,
                 total_amount, net_ratio,
                 turnover, float_mv,
                 reason, inst_buyers, inst_sellers,
                 inst_net_buy, inst_net_ratio,
                 industry, is_limit_up)
            VALUES %s
            ON CONFLICT (trade_date, code) DO UPDATE SET
                lhb_net_buy=EXCLUDED.lhb_net_buy,
                inst_net_buy=EXCLUDED.inst_net_buy,
                is_limit_up=EXCLUDED.is_limit_up
        """, batch)
    conn.commit()
    
    # 4. 行业聚合
    csi_rows = calc_industry_csi(records, inst_map)
    
    if csi_rows:
        cur.execute("DELETE FROM csi_lhb_industry WHERE trade_date=%s", (trade_date_str,))
        
        pge.execute_values(cur, """
            INSERT INTO csi_lhb_industry
                (trade_date, industry, lhb_stock_count, limit_up_count,
                 inst_stock_count, total_lhb_net_buy, total_inst_net_buy,
                 total_inst_buy, total_inst_sell,
                 avg_lhb_net_buy, avg_inst_net_buy,
                 positive_inst_ratio, total_lhb_amount,
                 csi_score, signal)
            VALUES %s
        """, [
            (trade_date_str, r['industry'],
             r['lhb_stock_count'], r['limit_up_count'],
             r['inst_stock_count'], r['total_lhb_net_buy'], r['total_inst_net_buy'],
             r['total_inst_buy'], r['total_inst_sell'],
             r['avg_lhb_net_buy'], r['avg_inst_net_buy'],
             r['positive_inst_ratio'], r['total_lhb_amount'],
             r['csi_score'], r['signal'])
            for r in csi_rows
        ])
        conn.commit()
    
    cur.close()
    conn.close()
    
    return len(records), len(csi_rows)


# ═══════════════════════════════════════════════
# 全量回填
# ═══════════════════════════════════════════════

def backfill_all(from_year=2014):
    """
    全量回填龙虎榜数据
    从from_year年起逐日回填所有交易日
    """
    # 获取交易日历
    r,_ = df._pg_execute("""
        SELECT DISTINCT trade_date FROM daily_prices 
        WHERE trade_date >= %s
        ORDER BY trade_date
    """, (f"{from_year}-01-01",))
    
    all_dates = [x[0] for x in r]
    print(f"📡 龙虎榜全量回填: {from_year} ~ 今, {len(all_dates)}个交易日")
    
    # 已有数据
    existing,_ = df._pg_execute(
        "SELECT DISTINCT trade_date FROM csi_lhb_industry ORDER BY trade_date"
    )
    existing_set = set(x[0] for x in existing)
    to_fetch = [d for d in all_dates if d not in existing_set]
    
    print(f"  已有{len(existing_set)}天, 待补{len(to_fetch)}天")
    
    if not to_fetch:
        print("  ✅ 全部已完成")
        return
    
    total_details = 0
    total_industries = 0
    t0 = time.time()
    
    for idx, td in enumerate(to_fetch):
        ds = td.strftime('%Y%m%d')
        
        detail_n, ind_n = process_day(ds)
        total_details += detail_n
        total_industries += ind_n
        
        if (idx+1) % 50 == 0:
            elapsed = time.time() - t0
            remain = (len(to_fetch)-idx-1) * REQUEST_SLEEP
            print(f"  [{idx+1}/{len(to_fetch)}] {ds}: {detail_n}只上榜, {ind_n}行业 | {elapsed:.0f}s, 预计剩余{remain:.0f}s")
        
        time.sleep(REQUEST_SLEEP)
    
    elapsed = time.time() - t0
    print(f"\n✅ 龙虎榜全量回填完成!")
    print(f"  {total_details}条明细, {total_industries}条行业聚合")
    print(f"  耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    
    # 统计
    r_stat,_ = df._pg_execute("""
        SELECT MIN(trade_date)::text, MAX(trade_date)::text, COUNT(*) 
        FROM csi_lhb_industry
    """)
    print(f"  csi_lhb_industry: {r_stat[0][0]} ~ {r_stat[0][1]}, {r_stat[0][2]}天")


# ═══════════════════════════════════════════════
# 每日增量
# ═══════════════════════════════════════════════

def incremental():
    """每日增量"""
    today_str = date.today().strftime('%Y%m%d')
    print(f"📡 增量更新: {today_str}")
    
    # 查是否已有
    r,_ = df._pg_execute(
        "SELECT COUNT(*) FROM csi_lhb_industry WHERE trade_date=%s",
        (date.today(),)
    )
    if r and r[0][0] > 0:
        print(f"  ✅ {today_str} 已有数据，跳过")
        # 但可以展示结果
        show_results(date.today().strftime('%Y-%m-%d'))
        return True
    
    detail_n, ind_n = process_day(today_str)
    print(f"\n  ✅ 写入: {detail_n}只龙虎榜, {ind_n}个行业")
    
    if ind_n > 0:
        show_results(date.today().strftime('%Y-%m-%d'))
    
    return True


def show_results(ds=None):
    """展示某日CSI结果"""
    if ds is None:
        ds = str(date.today())
    
    r,_ = df._pg_execute("""
        SELECT industry, lhb_stock_count, limit_up_count, inst_stock_count,
               total_inst_net_buy, total_lhb_net_buy, csi_score, signal
        FROM csi_lhb_industry
        WHERE trade_date=%s
        ORDER BY csi_score DESC
    """, (ds,))
    
    if not r:
        print(f"  {ds} 无数据")
        return
    
    print(f"\n📊 {ds} CSI龙虎榜聚合结果 ({len(r)}个行业):")
    
    # Top 5
    print(f"\n  🔥 催化最强TOP5:")
    for row in r[:5]:
        ind, stocks, limit, inst, inst_net, lhb_net, score, sig = row
        inst_w = inst_net / 1e8 if inst_net else 0
        print(f"    {ind:12s} 得分{score:.2f} | {stocks}只上榜({limit}涨停) 机构{inst}只 净买{inst_w:.2f}亿  {sig}")
    
    # Bottom 5
    print(f"\n  🧊 催化最弱TOP5:")
    for row in r[-5:]:
        ind, stocks, limit, inst, inst_net, lhb_net, score, sig = row
        inst_w = inst_net / 1e8 if inst_net else 0
        print(f"    {ind:12s} 得分{score:.2f} | {stocks}只上榜({limit}涨停) 机构净买{inst_w:.2f}亿  {sig}")
    
    from collections import Counter
    sig_counts = Counter(row[7] for row in r)
    print(f"\n  信号分布: {dict(sig_counts)}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='CSI v4 — 龙虎榜涨停板机构资金聚合')
    parser.add_argument('--mode', choices=['backfill', 'incremental', 'ensure'],
                       default='incremental')
    parser.add_argument('--from', type=int, default=2014, dest='from_year',
                       help='回填起始年份（默认2014）')
    args = parser.parse_args()
    
    ensure_tables()
    
    if args.mode == 'backfill':
        print(f"📡 全量回填 {args.from_year}~今...")
        backfill_all(args.from_year)
    
    elif args.mode == 'incremental':
        incremental()
    
    elif args.mode == 'ensure':
        print("✅ 表已就绪，退出")
