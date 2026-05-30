#!/usr/bin/env python3
"""
龙虎榜详细数据补拉脚本
将龙虎榜个股级+机构级+营业部级数据写入PG

数据源：
1. stock_lhb_detail_em — 龙虎榜每日上榜汇总（含净买额、上榜后1/2/5/10日涨幅）
2. stock_lhb_jgmmtj_em — 机构买卖统计（机构数量、净额）
3. stock_lhb_stock_detail_em — 个股买卖前5营业部（逐个获取，含机构/深股通标识）

写入表：market_signals (signal_type='lhb_detail', 'lhb_inst', 'lhb_trader')
"""

import os, sys, time, json, argparse
from datetime import datetime, timedelta, date
sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
import data_finance as df

BASE_URL = 'http://192.168.3.14:5000'

def ensure_env():
    """确保在conda tt环境"""
    if not os.environ.get('CONDA_DEFAULT_ENV') != 'tt':
        # 提示但不强制
        pass

def get_existing_dates(signal_type):
    """获取已存在的交易日"""
    rows, _ = df._pg_execute(
        "SELECT DISTINCT trade_date FROM market_signals WHERE signal_type = %s ORDER BY trade_date",
        (signal_type,)
    )
    return {r[0] for r in rows}

def save_to_market_signals(signal_type, code, trade_date, value, change_val=None, change_pct=None, extra=None):
    """写入一条market_signals记录"""
    import json as _json
    
    def _safe_serialize(obj):
        """安全的JSON序列化，处理NaN/Inf等"""
        if obj is None:
            return None
        class SafeEncoder(_json.JSONEncoder):
            def default(self, o):
                return str(o)
        def convert(o):
            if isinstance(o, dict):
                return {k: convert(v) for k, v in o.items()}
            elif isinstance(o, list):
                return [convert(v) for v in o]
            elif isinstance(o, float):
                if o != o or o == float('inf') or o == float('-inf'):
                    return None
                return o
            return o
        try:
            return _json.dumps(convert(obj), ensure_ascii=False)
        except:
            return _json.dumps({})
    
    try:
        df._pg_execute(
            """INSERT INTO market_signals (signal_type, code, trade_date, value, change_val, change_pct, extra, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
               ON CONFLICT (signal_type, code, trade_date) 
               DO UPDATE SET value = EXCLUDED.value, change_val = EXCLUDED.change_val,
                             change_pct = EXCLUDED.change_pct, extra = EXCLUDED.extra,
                             updated_at = NOW()""",
            (signal_type, code, trade_date, value, change_val, change_pct,
             _safe_serialize(extra)),
            fetch=False
        )
        return True
    except Exception as e:
        print(f"   写入失败 {code} {trade_date}: {e}")
        return False


def fetch_lhb_daily(start_date, end_date):
    """拉取龙虎榜每日汇总 (stock_lhb_detail_em)"""
    import akshare as ak
    
    existing = get_existing_dates('lhb_daily')
    print(f"  已有lhb_daily交易日: {len(existing)} 天")
    
    # 生成日期范围
    d = datetime.strptime(start_date, '%Y%m%d')
    end = datetime.strptime(end_date, '%Y%m%d')
    dates_to_fetch = []
    while d <= end:
        ds = d.strftime('%Y%m%d')
        if ds not in existing:
            dates_to_fetch.append(ds)
        d += timedelta(days=1)
    
    print(f"  需要拉取: {len(dates_to_fetch)} 天")
    if not dates_to_fetch:
        print("  已是最新，跳过")
        return
    
    total = 0
    for i, ds in enumerate(dates_to_fetch):
        try:
            df_data = ak.stock_lhb_detail_em(start_date=ds, end_date=ds)
            if df_data is not None and len(df_data) > 0:
                count = 0
                for _, row in df_data.iterrows():
                    code = str(row['代码'])
                    name = row.get('名称', '')
                    net_buy = float(row.get('龙虎榜净买额', 0) or 0)
                    buy = float(row.get('龙虎榜买入额', 0) or 0)
                    sell = float(row.get('龙虎榜卖出额', 0) or 0)
                    turnover = float(row.get('龙虎榜成交额', 0) or 0)
                    pct = float(row.get('涨跌幅', 0) or 0)
                    reason = row.get('上榜原因', '')
                    after_1d = float(row.get('上榜后1日', 0)) if row.get('上榜后1日') and str(row.get('上榜后1日')).strip() else None
                    after_2d = float(row.get('上榜后2日', 0)) if row.get('上榜后2日') and str(row.get('上榜后2日')).strip() else None
                    after_5d = float(row.get('上榜后5日', 0)) if row.get('上榜后5日') and str(row.get('上榜后5日')).strip() else None
                    after_10d = float(row.get('上榜后10日', 0)) if row.get('上榜后10日') and str(row.get('上榜后10日')).strip() else None
                    
                    extra = {
                        'name': name, 'buy': buy, 'sell': sell,
                        'turnover': turnover, 'reason': reason,
                        'pct': pct,
                        'after_1d': after_1d, 'after_2d': after_2d,
                        'after_5d': after_5d, 'after_10d': after_10d
                    }
                    if save_to_market_signals('lhb_daily', code, ds, net_buy, None, pct, extra):
                        count += 1
                
                total += count
                if (i+1) % 5 == 0 or i == len(dates_to_fetch)-1:
                    print(f"    [{i+1}/{len(dates_to_fetch)}] {ds}: {count} 条")
            else:
                print(f"    [{i+1}/{len(dates_to_fetch)}] {ds}: 空")
            time.sleep(1.5)
        except Exception as e:
            print(f"    [{i+1}/{len(dates_to_fetch)}] {ds}: 失败 - {e}")
            time.sleep(3)
    
    print(f"  ✅ lhb_daily 完成, 共写入 {total} 条")


def fetch_lhb_institution(start_date, end_date):
    """拉取机构买卖统计 (stock_lhb_jgmmtj_em)"""
    import akshare as ak
    
    existing = get_existing_dates('lhb_inst')
    print(f"  已有lhb_inst交易日: {len(existing)} 天")
    
    d = datetime.strptime(start_date, '%Y%m%d')
    end = datetime.strptime(end_date, '%Y%m%d')
    dates_to_fetch = []
    while d <= end:
        ds = d.strftime('%Y%m%d')
        if ds not in existing:
            dates_to_fetch.append(ds)
        d += timedelta(days=1)
    
    print(f"  需要拉取: {len(dates_to_fetch)} 天")
    if not dates_to_fetch:
        print("  已是最新，跳过")
        return
    
    total = 0
    for i, ds in enumerate(dates_to_fetch):
        try:
            df_data = ak.stock_lhb_jgmmtj_em(start_date=ds, end_date=ds)
            if df_data is not None and len(df_data) > 0:
                count = 0
                for _, row in df_data.iterrows():
                    code = str(row['代码'])
                    name = row.get('名称', '')
                    buy_institutions = int(row.get('买方机构数', 0) or 0)
                    sell_institutions = int(row.get('卖方机构数', 0) or 0)
                    inst_buy = float(row.get('机构买入总额', 0) or 0)
                    inst_sell = float(row.get('机构卖出总额', 0) or 0)
                    inst_net = float(row.get('机构买入净额', 0) or 0)
                    total_turnover = float(row.get('市场总成交额', 0) or 0)
                    ratio = float(row.get('机构净买额占总成交额比', 0) or 0)
                    turnover_rate = float(row.get('换手率', 0) or 0)
                    mkt_cap = float(row.get('流通市值', 0) or 0)
                    reason = row.get('上榜原因', '')
                    
                    extra = {
                        'name': name,
                        'buy_inst_cnt': buy_institutions,
                        'sell_inst_cnt': sell_institutions,
                        'inst_buy': inst_buy,
                        'inst_sell': inst_sell,
                        'inst_net': inst_net,
                        'total_turnover': total_turnover,
                        'inst_ratio': ratio,
                        'turnover_rate': turnover_rate,
                        'mkt_cap': mkt_cap,
                        'reason': reason
                    }
                    if save_to_market_signals('lhb_inst', code, ds, inst_net, None, ratio, extra):
                        count += 1
                
                total += count
                if (i+1) % 10 == 0 or i == len(dates_to_fetch)-1:
                    print(f"    [{i+1}/{len(dates_to_fetch)}] {ds}: {count} 条")
            else:
                if (i+1) % 30 == 0:
                    print(f"    [{i+1}/{len(dates_to_fetch)}] {ds}: 空")
            time.sleep(1.2)
        except Exception as e:
            if (i+1) % 20 == 0:
                print(f"    [{i+1}/{len(dates_to_fetch)}] {ds}: 失败 - {e}")
            time.sleep(2)
    
    print(f"  ✅ lhb_inst 完成, 共写入 {total} 条")


def fetch_lhb_trader_detail(date_str, max_stocks=50):
    """
    拉取某日龙虎榜个股的营业部级买卖明细 (stock_lhb_stock_detail_em)
    耗时较大，只拉最近N只
    """
    import akshare as ak
    
    # 先获取该日lhb_daily的股票列表
    # 检查该日已有数据，跳过已处理的股票
    existing, _ = df._pg_execute(
        "SELECT code FROM market_signals WHERE signal_type = 'lhb_trader' AND trade_date = %s",
        (date_str,)
    )
    existing_codes = set()
    for r in existing:
        code_trader = r[0]
        stock_code = code_trader.split('_')[0] if '_' in code_trader else code_trader
        existing_codes.add(stock_code)
    
    rows, _ = df._pg_execute(
        "SELECT code, extra FROM market_signals WHERE signal_type = 'lhb_daily' AND trade_date = %s ORDER BY value DESC",
        (date_str,)
    )
    
    stocks = []
    for r in rows:
        if r[1]:
            extra = r[1] if isinstance(r[1], dict) else json.loads(r[1])
            name = extra.get('name', '')
            if r[0] not in existing_codes:
                stocks.append((r[0], name))
    
    if not stocks:
        return 0
    
    # 只取前N只
    stocks = stocks[:max_stocks]
    
    count = 0
    for code, name in stocks:
        try:
            # 买入前5
            buy_df = ak.stock_lhb_stock_detail_em(symbol=code, date=date_str, flag='买入')
            if buy_df is not None and len(buy_df) > 0:
                for _, row in buy_df.iterrows():
                    trader_name = str(row.get('交易营业部名称', ''))
                    buy_amt = float(row.get('买入金额', 0) or 0)
                    sell_amt = float(row.get('卖出金额', 0) or 0)
                    net_amt = float(row.get('净额', 0) or 0)
                    buy_ratio = float(row.get('买入金额-占总成交比例', 0)) if row.get('买入金额-占总成交比例') else None
                    sell_ratio = float(row.get('卖出金额-占总成交比例', 0)) if row.get('卖出金额-占总成交比例') else None
                    reason_type = row.get('类型', '')
                    
                    # 识别阵营
                    camp = 'other'
                    if '机构专用' in trader_name:
                        camp = 'institution'
                    elif '深股通' in trader_name or '沪股通' in trader_name:
                        camp = 'northbound'
                    elif '证券营业部' in trader_name or '分公司' in trader_name:
                        # 识别知名游资
                        camp = 'hotel'
                    
                    extra = {
                        'name': name,
                        'trader': trader_name,
                        'camp': camp,
                        'side': 'buy',
                        'buy_amt': buy_amt,
                        'sell_amt': sell_amt,
                        'net_amt': net_amt,
                        'buy_ratio': buy_ratio,
                        'sell_ratio': sell_ratio,
                        'reason_type': reason_type
                    }
                    save_to_market_signals('lhb_trader', f"{code}_{trader_name}", date_str,
                                          net_amt, None, None, extra)
                    count += 1
        except Exception as e:
            pass
        
        # 卖出前5营业部
        try:
            sell_df = ak.stock_lhb_stock_detail_em(symbol=code, date=date_str, flag='卖出')
            if sell_df is not None and len(sell_df) > 0:
                for _, row in sell_df.iterrows():
                    trader_name = str(row.get('交易营业部名称', ''))
                    buy_amt = float(row.get('买入金额', 0) or 0)
                    sell_amt = float(row.get('卖出金额', 0) or 0)
                    net_amt = float(row.get('净额', 0) or 0)
                    buy_ratio = float(row.get('买入金额-占总成交比例', 0)) if row.get('买入金额-占总成交比例') else None
                    sell_ratio = float(row.get('卖出金额-占总成交比例', 0)) if row.get('卖出金额-占总成交比例') else None
                    reason_type = row.get('类型', '')
                    
                    camp = 'other'
                    if '机构专用' in trader_name:
                        camp = 'institution'
                    elif '深股通' in trader_name or '沪股通' in trader_name:
                        camp = 'northbound'
                    elif '证券营业部' in trader_name or '分公司' in trader_name:
                        camp = 'hotel'
                    
                    extra = {
                        'name': name,
                        'trader': trader_name,
                        'camp': camp,
                        'side': 'sell',
                        'buy_amt': buy_amt,
                        'sell_amt': sell_amt,
                        'net_amt': net_amt,
                        'buy_ratio': buy_ratio,
                        'sell_ratio': sell_ratio,
                        'reason_type': reason_type
                    }
                    save_to_market_signals('lhb_trader', f"{code}_{trader_name}", date_str,
                                          net_amt, None, None, extra)
                    count += 1
        except Exception as e:
            pass
        
        time.sleep(0.5)
    
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='龙虎榜详细数据补拉')
    parser.add_argument('--mode', choices=['daily', 'inst', 'trader', 'all'], default='all',
                       help='模式: daily=每日汇总, inst=机构买卖, trader=营业部明细, all=全量')
    parser.add_argument('--start-date', type=str, default='20260401',
                       help='起始日期 yyyymmdd')
    parser.add_argument('--end-date', type=str, default=None,
                       help='结束日期 yyyymmdd (默认今天)')
    args = parser.parse_args()
    
    if args.end_date is None:
        args.end_date = date.today().strftime('%Y%m%d')
    
    print(f"📡 龙虎榜详细数据补拉")
    print(f"   模式: {args.mode}")
    print(f"   范围: {args.start_date} ~ {args.end_date}")
    
    if args.mode in ('all', 'daily'):
        print("\n📥 拉取龙虎榜每日汇总 (lhb_daily)...")
        fetch_lhb_daily(args.start_date, args.end_date)
    
    if args.mode in ('all', 'inst'):
        print("\n📥 拉取机构买卖统计 (lhb_inst)...")
        fetch_lhb_institution(args.start_date, args.end_date)
    
    if args.mode in ('all', 'trader'):
        print("\n📥 拉取营业部明细 (lhb_trader)...")
        d = datetime.strptime(args.start_date, '%Y%m%d')
        end = datetime.strptime(args.end_date, '%Y%m%d')
        total = 0
        while d <= end:
            ds = d.strftime('%Y%m%d')
            print(f"  {ds}...", end=' ', flush=True)
            c = fetch_lhb_trader_detail(ds)
            print(f"{c} 条新增" if c > 0 else "无新增或已有")
            total += c
            d += timedelta(days=1)
        print(f"  ✅ lhb_trader 完成, 共写入 {total} 条")
    
    print("\n✅ 全部完成")
