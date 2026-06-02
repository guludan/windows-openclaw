#!/usr/bin/env python3
"""
CSI 催化稳定性指数 v5 — 基于龙虎榜机构资金聚合（新版数据层）

核心变化（从v4）：
  - 数据源：从akshare → eastmoney_data.py（直接调东财datacenter/push2 API）
  - 新增：机构买卖明细从RPT_BILLBOARD_INST_DETAILSHNEW获取
  - 新增：行业板块排名从push2/api/qt/clist/get获取
  - 申万行业映射：从industry_components_sw PG表获取

PG表（不变）：
  csi_lhb_detail   — 龙虎榜个股明细
  csi_lhb_industry — 行业级CSI + 信号
"""
import os, sys, time, json, argparse
from datetime import date, datetime, timedelta
import numpy as np
from collections import defaultdict, Counter

sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
import data_finance as df
import psycopg2
import psycopg2.extras as pge

# 新数据层
from inverse_push.eastmoney_data import (
    daily_dragon_tiger, lhb_inst_detail,
    industry_comparison, get_industry_fund_flow,
    fund_flow_minute, get_industry_by_code,
    map_to_sw_industry,
)

REQUEST_SLEEP = 0.3       # 请求间隔
BATCH_DB = 100


def _get_conn():
    return psycopg2.connect(
        host=df.PG_HOST, port=df.PG_PORT,
        dbname=df.PG_DB, user=df.PG_USER, password=df.PG_PASS
    )


def ensure_tables():
    """确保csi_lhb_detail和csi_lhb_industry表存在（含updated_at）"""
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
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (trade_date, code)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS csi_lhb_industry (
            trade_date DATE NOT NULL,
            industry VARCHAR(50) NOT NULL,
            lhb_stock_count INTEGER DEFAULT 0,
            limit_up_count INTEGER DEFAULT 0,
            inst_stock_count INTEGER DEFAULT 0,
            total_lhb_net_buy DOUBLE PRECISION DEFAULT 0,
            total_inst_net_buy DOUBLE PRECISION DEFAULT 0,
            total_inst_buy DOUBLE PRECISION DEFAULT 0,
            total_inst_sell DOUBLE PRECISION DEFAULT 0,
            avg_lhb_net_buy DOUBLE PRECISION DEFAULT 0,
            avg_inst_net_buy DOUBLE PRECISION DEFAULT 0,
            positive_inst_ratio DOUBLE PRECISION DEFAULT 0,
            total_lhb_amount DOUBLE PRECISION DEFAULT 0,
            csi_score DOUBLE PRECISION DEFAULT 0,
            signal VARCHAR(20) DEFAULT 'neutral',
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (trade_date, industry)
        )
    """)
    
    conn.commit()
    cur.close()
    conn.close()
    print("✅ csi_lhb_detail / csi_lhb_industry 表已就绪")


def fetch_lhb_day(trade_date_str):
    """
    从东财datacenter拉取指定日期龙虎榜数据
    返回: [{...}] 或 None
    """
    dt = daily_dragon_tiger(trade_date_str)
    if not dt or dt["total_records"] == 0:
        return None
    
    # 获取机构买卖明细（精确机构数据）
    inst_list = lhb_inst_detail(trade_date_str)
    inst_map = {}
    if inst_list:
        for s in inst_list:
            inst_map[s["code"]] = s
    
    # 获取行业信息
    records = []
    for s in dt["stocks"]:
        code = s["code"]
        industry = get_industry_by_code(code)
        
        inst_info = inst_map.get(code, {})
        
        # 从解释中提取机构数量（回退方案）
        inst_buyers = inst_info.get("inst_buyers", 0)
        inst_sellers = inst_info.get("inst_sellers", 0)
        inst_net = inst_info.get("inst_net", 0)
        inst_net_ratio = inst_info.get("inst_net_ratio", 0)
        
        # 如果没有精确数据，从解释字段中提取
        if inst_buyers == 0 and inst_sellers == 0:
            explain = s.get("explain", "")
            import re
            mb = re.search(r'(\d+)家机构买入', explain)
            ms = re.search(r'(\d+)家机构卖出', explain)
            if mb: inst_buyers = int(mb.group(1))
            if ms: inst_sellers = int(ms.group(1))
            # 估算机构净买
            if inst_buyers > 0 and inst_sellers == 0:
                inst_net = s["net_buy_wan"] * 10000 * 0.3  # 估算30%是机构
            elif inst_buyers > 0 or inst_sellers > 0:
                inst_net = s["net_buy_wan"] * 10000 * 0.5  # 估算50%是机构
        
        records.append({
            'code': code,
            'name': s['name'],
            'close': s['close'],
            'change_pct': s['change_pct'],
            'is_limit_up': s['is_limit_up'],
            'lhb_net_buy': s['net_buy_wan'] * 10000,
            'lhb_buy': s['buy_wan'] * 10000,
            'lhb_sell': s['sell_wan'] * 10000,
            'total_amount': (s['buy_wan'] + s['sell_wan']) * 10000,
            'net_ratio': s['net_ratio'],
            'turnover': s['turnover_pct'],
            'float_mv': 0,  # datacenter没有直接提供流通市值
            'reason': s['reason'],
            'inst_buyers': inst_buyers,
            'inst_sellers': inst_sellers,
            'inst_net_buy': inst_net,
            'inst_net_ratio': inst_net_ratio,
            'industry': industry,
        })
    
    return records


def calc_industry_csi(records):
    """计算行业级CSI"""
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
        
        if r['inst_buyers'] > 0 or r['inst_sellers'] > 0:
            d['inst_stocks'].append(r)
            d['inst_net'] += r['inst_net_buy']
            # 买入卖出额需要从inst_map获取，这里用估算值
            if r['inst_net_buy'] > 0:
                d['inst_buy_total'] += r['inst_net_buy']
    
    # 计算CSI得分和信号
    csi_rows = []
    for ind, d in sorted(industry_data.items()):
        n = len(d['stocks'])
        lu = len(d['limit_up_stocks'])
        ins = len(d['inst_stocks'])
        pos_inst = sum(1 for s in d['inst_stocks'] if s['inst_net_buy'] > 0)
        
        # CSI综合得分
        score = 0
        if n > 0:
            score += lu / n * 3  # 涨停密度
        score += min(ins, 5) * 1.0  # 机构参与度
        if d['inst_net'] > 0:
            net_w = d['inst_net'] / 1e8
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
    
    # 信号分档（横截面百分位）
    if csi_rows:
        scores = [r['csi_score'] for r in csi_rows]
        p33, p67 = np.percentile(scores, [33, 67])
        
        for r in csi_rows:
            sc = r['csi_score']
            # 机构净买>1亿加分
            if r['total_inst_net_buy'] > 100000000:
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


def _fmt_date(s):
    """统一日期格式 YYYYMMDD → YYYY-MM-DD"""
    s = str(s).replace('-', '')
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s


def process_day(trade_date_str):
    """
    处理单个交易日
    返回 (detail_count, industry_count)
    """
    # 统一日期格式
    api_date = _fmt_date(trade_date_str)
    
    # 1. 拉龙虎榜
    records = fetch_lhb_day(api_date)
    if not records:
        return 0, 0
    
    # 2. 写入csi_lhb_detail
    conn = _get_conn()
    cur = conn.cursor()
    
    cur.execute("DELETE FROM csi_lhb_detail WHERE trade_date=%s", (trade_date_str,))
    
    # 按code去重（合并多次上榜记录）
    seen = {}
    for r in records:
        code = r['code']
        if code in seen:
            s = seen[code]
            s['lhb_net_buy'] += r['lhb_net_buy']
            s['lhb_buy'] += r['lhb_buy']
            s['lhb_sell'] += r['lhb_sell']
            s['total_amount'] += r['total_amount']
            if r['inst_net_buy'] != 0:
                s['inst_net_buy'] = r['inst_net_buy']
                s['inst_net_ratio'] = r['inst_net_ratio']
            if r['inst_buyers'] > s.get('inst_buyers', 0):
                s['inst_buyers'] = r['inst_buyers']
            if r['inst_sellers'] > s.get('inst_sellers', 0):
                s['inst_sellers'] = r['inst_sellers']
        else:
            seen[code] = dict(r)  # 浅拷贝
    
    batch = []
    for code, r in seen.items():
        batch.append((
            trade_date_str, code, r['name'],
            r['close'], r['change_pct'],
            r['lhb_net_buy'], r['lhb_buy'], r['lhb_sell'],
            r['total_amount'], r['net_ratio'],
            r['turnover'], r.get('float_mv', 0),
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
                is_limit_up=EXCLUDED.is_limit_up,
                updated_at=NOW()
        """, batch)
    conn.commit()
    
    # 3. 行业聚合
    csi_rows = calc_industry_csi(records)
    
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
# 回填
# ═══════════════════════════════════════════════

def backfill_all(from_year=2014):
    """全量回填龙虎榜数据"""
    r,_ = df._pg_execute("""
        SELECT DISTINCT trade_date FROM daily_prices 
        WHERE trade_date >= %s
        ORDER BY trade_date
    """, (f"{from_year}-01-01",))
    
    all_dates = [x[0] for x in r]
    print(f"📡 龙虎榜全量回填: {from_year} ~ 今, {len(all_dates)}个交易日")
    
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
            pct = (idx+1) / len(to_fetch) * 100
            remain = (elapsed / (idx+1)) * (len(to_fetch)-idx-1)
            print(f"  [{idx+1}/{len(to_fetch)}] {pct:.0f}% {ds}: {detail_n}只 | {elapsed:.0f}s, 预计剩余{remain:.0f}s")
        
        time.sleep(REQUEST_SLEEP)
    
    elapsed = time.time() - t0
    print(f"\n✅ 龙虎榜全量回填完成! {total_details}条明细, {total_industries}条行业")
    print(f"  耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")


# ═══════════════════════════════════════════════
# 增量
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
    
    print(f"\n  🔥 催化最强TOP5:")
    for row in r[:5]:
        ind, stocks, limit, inst, inst_net, lhb_net, score, sig = row
        inst_w = inst_net / 1e8 if inst_net else 0
        print(f"    {ind:12s} 得分{score:.2f} | {stocks}只上榜({limit}涨停) 机构{inst}只 净买{inst_w:.2f}亿  {sig}")
    
    print(f"\n  🧊 催化最弱TOP5:")
    for row in r[-5:]:
        ind, stocks, limit, inst, inst_net, lhb_net, score, sig = row
        inst_w = inst_net / 1e8 if inst_net else 0
        print(f"    {ind:12s} 得分{score:.2f} | {stocks}只上榜({limit}涨停) 机构净买{inst_w:.2f}亿  {sig}")
    
    sig_counts = Counter(row[7] for row in r)
    print(f"\n  信号分布: {dict(sig_counts)}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='CSI v5 — 龙虎榜机构数据新数据层')
    parser.add_argument('--mode', choices=['backfill', 'incremental', 'ensure', 'test'],
                       default='incremental')
    parser.add_argument('--from', type=int, default=2014, dest='from_year',
                       help='回填起始年份')
    parser.add_argument('--date', type=str, default=None,
                       help='指定日期（YYYYMMDD）')
    args = parser.parse_args()
    
    ensure_tables()
    
    if args.mode == 'test':
        print(f"🧪 测试模式: 拉取{args.date or '今日'}数据")
        ds = _fmt_date(args.date or date.today().strftime('%Y%m%d'))
        records = fetch_lhb_day(ds)
        if records:
            print(f"  ✅ 获取到{len(records)}条")
            csi = calc_industry_csi(records)
            for r in csi[:3]:
                print(f"    {r['industry']}: 得分{r['csi_score']:.2f} {r['signal']} 上榜{r['lhb_stock_count']}只")
        else:
            print("  ❌ 无数据")
    
    elif args.mode == 'backfill':
        print(f"📡 全量回填 {args.from_year}~今...")
        backfill_all(args.from_year)
    
    elif args.mode == 'incremental':
        incremental()
    
    elif args.mode == 'ensure':
        print("✅ 表已就绪")
