#!/usr/bin/env python3
"""
龙虎榜阵营分类器 v2
1. 知名游资营业部映射表（初始内嵌约60个）
2. 自动行为聚类（T+1卖出比例 → 重标hotel/other）
3. 每日阵营分歧度计算（机构vs游资净买入方向）
4. 阵营分歧信号写入 market_signals

阵营映射：
  - institution: 机构专用席位
  - northbound: 沪/深股通专用
  - hot_trader: 知名游资营业部
  - trader: 普通营业部（待行为判断）
  - other: 其他

输出到：market_signals (signal_type='camp_diverge', 'camp_classify')
"""
import os, sys, json, re, argparse
from datetime import datetime, timedelta, date
from collections import defaultdict

sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
import data_finance as df


# ============================================================
# 知名游资营业部初始名单（持续更新）
# 
# 来源：淘股吧、同花顺龙虎榜数据分析
# 特征：上榜频率高、交易金额大、擅长连板股操作
# ============================================================
HOT_TRADERS = frozenset([
    # 东方财富拉萨天团（散户集中营，但行为类似游资）
    "东方财富证券股份有限公司拉萨东环路第一证券营业部",
    "东方财富证券股份有限公司拉萨东环路第二证券营业部", 
    "东方财富证券股份有限公司拉萨东环路第三证券营业部",
    "东方财富证券股份有限公司拉萨团结路第一证券营业部",
    "东方财富证券股份有限公司拉萨团结路第二证券营业部",
    "东方财富证券股份有限公司拉萨金融城南环路证券营业部",
    
    # 华泰系
    "华泰证券股份有限公司总部",
    "华泰证券股份有限公司深圳益田路荣超商务中心证券营业部",
    "华泰证券股份有限公司上海武定路证券营业部",
    "华泰证券股份有限公司上海浦东新区世纪大道证券营业部",
    "华泰证券股份有限公司浙江分公司",
    "华泰证券股份有限公司郑州经三路证券营业部",
    
    # 中金财富/中金系
    "中国中金财富证券有限公司深圳分公司",
    "中国中金财富证券有限公司杭州教工路证券营业部",
    "中国中金财富证券有限公司北京宋庄路证券营业部",
    "中金财富证券股份有限公司上海分公司",
    
    # 中信系
    "中信证券股份有限公司上海分公司",
    "中信证券股份有限公司上海溧阳路证券营业部",
    "中信证券股份有限公司杭州延安路证券营业部",
    "中信证券股份有限公司深圳分公司",
    "中信证券股份有限公司北京紫竹院路证券营业部",
    "中信证券（山东）有限责任公司青岛南京路证券营业部",
    
    # 招商系
    "招商证券股份有限公司上海牡丹江路证券营业部",
    "招商证券股份有限公司北京朝外大街证券营业部",
    "招商证券股份有限公司深圳深南东路证券营业部",
    
    # 国泰君安系
    "国泰君安证券股份有限公司上海分公司",
    "国泰君安证券股份有限公司上海江苏路证券营业部",
    "国泰君安证券股份有限公司南京太平南路证券营业部",
    "国泰君安证券股份有限公司深圳益田路证券营业部",
    "国泰君安证券股份有限公司杭州五星路证券营业部",
    
    # 知名游资营业部（按地区）
    "中国银河证券股份有限公司北京中关村大街证券营业部",
    "中国银河证券股份有限公司上海杨浦区靖宇东路证券营业部",
    "兴业证券股份有限公司厦门湖里大道证券营业部",
    "兴业证券股份有限公司陕西分公司",
    "广发证券股份有限公司上海东方路证券营业部",
    "广发证券股份有限公司北京建外大街证券营业部",
    "海通证券股份有限公司上海黄浦区福州路证券营业部",
    "海通证券股份有限公司深圳分公司华富路证券营业部",
    "财通证券股份有限公司杭州上塘路证券营业部",
    "财通证券股份有限公司杭州解放东路证券营业部",
    "华鑫证券有限责任公司上海分公司",
    "华鑫证券有限责任公司深圳分公司",
    "中国国际金融股份有限公司上海分公司",  # 中金
    "中国国际金融股份有限公司北京建国门外大街证券营业部",
    "申万宏源证券有限公司上海闵行区东川路证券营业部",
    "申万宏源证券有限公司深圳金田路证券营业部",
    "平安证券股份有限公司深圳深南东路罗湖商务中心证券营业部",
    "平安证券股份有限公司浙江分公司",
    "浙商证券股份有限公司杭州五星路证券营业部",
    "浙商证券股份有限公司绍兴分公司",
    "长江证券股份有限公司上海分公司",
    "长江证券股份有限公司北京百万庄大街证券营业部",
    "光大证券股份有限公司上海世纪大道证券营业部",
    "光大证券股份有限公司宁波解放南路证券营业部",
    "天风证券股份有限公司深圳分公司",
    "天风证券股份有限公司上海浦东分公司",
    "中信建投证券股份有限公司杭州庆春路证券营业部",
    "中信建投证券股份有限公司上海徐家汇路证券营业部",
    "国金证券股份有限公司上海奉贤区金碧路证券营业部",
    "国金证券股份有限公司深圳分公司",
    
    # 量化/程序化（高频交易特征）
    "华创证券有限责任公司上海分公司",
    "华创证券有限责任公司北京万寿路证券营业部",
    "西部证券股份有限公司上海世纪大道证券营业部",
])


def classify_trader(trader_name):
    """
    营业部名称→阵营分类
    优先级：机构 > 北向 > 知名游资 > 普通营业部 > other
    """
    if '机构专用' in trader_name:
        return 'institution'
    if '深股通专用' in trader_name or '沪股通专用' in trader_name:
        return 'northbound'
    if trader_name in HOT_TRADERS:
        return 'hot_trader'
    if '证券营业部' in trader_name or '分公司' in trader_name or '证券股份有限公司' in trader_name:
        return 'trader'
    return 'other'


def reclassify_lhb_trader(dates=None, dry_run=True):
    """
    对已有lhb_trader数据重新执行阵营分类
    用新的HOT_TRADERS名单替换旧的'hotel'/'other'分类
    """
    where_clause = ""
    params = []
    if dates:
        where_clause = " AND trade_date >= %s AND trade_date <= %s"
        params = [dates[0], dates[1]]
    
    rows, _ = df._pg_execute(
        "SELECT signal_type, code, trade_date, extra, extra->>'camp' as old_camp "
        "FROM market_signals WHERE signal_type = 'lhb_trader'" + where_clause,
        tuple(params) if params else None
    )
    
    changes = {'institution': 0, 'northbound': 0, 'hot_trader': 0, 'trader': 0, 'other': 0, 'skipped': 0}
    
    for r in rows:
        code = r[1]
        # code格式: stockcode_tradername
        if '_' not in code:
            changes['skipped'] += 1
            continue
        trader_name = code.split('_', 1)[1]
        
        new_camp = classify_trader(trader_name)
        old_extra = r[3] if isinstance(r[3], dict) else json.loads(r[3]) if r[3] else {}
        old_camp = old_extra.get('camp', '')
        
        # hotel → trader 是老的分类，需要更新
        if old_camp in ('hotel',) or old_camp != new_camp:
            changes[new_camp] += 1
            if not dry_run:
                old_extra['camp'] = new_camp
                old_extra['camp_v2'] = True
                try:
                    df._pg_execute(
                        "UPDATE market_signals SET extra = %s::jsonb WHERE signal_type = 'lhb_trader' AND code = %s AND trade_date = %s",
                        (json.dumps(old_extra, ensure_ascii=False), code, r[2]),
                        fetch=False
                    )
                except Exception as e:
                    print(f"  更新失败 {code} {r[2]}: {e}")
        else:
            changes['skipped'] += 1
    
    print(f"  {'试运行' if dry_run else '更新'}结果:")
    print(f"    机构(institution): +{changes['institution']}")
    print(f"    北向(northbound): +{changes['northbound']}")
    print(f"    知名游资(hot_trader): +{changes['hot_trader']}")
    print(f"    普通营业部(trader): +{changes['trader']}")
    print(f"    其他(other): +{changes['other']}")
    print(f"    跳过(不变): {changes['skipped']}")
    
    return changes


def compute_camp_diverge(trade_date):
    """
    计算某日阵营分歧度
    对每只龙虎榜上榜股票，统计机构 vs 游资 净买入方向
    
    输出四象限信号：
      🟢 机构买+游资卖 → 中期看多
      🔴 机构卖+游资买 → 风险信号
      ⚪ 同买 → 共识加速
      ⚫ 同卖 → 双杀
    """
    ds = trade_date.strftime('%Y-%m-%d')
    
    # 获取该日所有lhb_trader数据
    rows, _ = df._pg_execute(
        "SELECT code, extra FROM market_signals "
        "WHERE signal_type = 'lhb_trader' AND trade_date = %s",
        (ds,)
    )
    
    if not rows:
        return []
    
    # 按股票汇总各阵营净额
    camp_by_stock = defaultdict(lambda: {'institution': 0, 'hot_trader': 0, 'trader': 0, 'northbound': 0, 'other': 0})
    stock_names = {}
    
    for r in rows:
        code_full = r[0]
        extra = r[1] if isinstance(r[1], dict) else json.loads(r[1]) if r[1] else {}
        
        camp = extra.get('camp', 'other')
        net_amt = extra.get('net_amt', 0) or 0
        stock_code = code_full.split('_')[0]
        name = extra.get('name', '')
        
        if stock_code:
            camp_by_stock[stock_code][camp] += net_amt
            if name:
                stock_names[stock_code] = name
    
    # 计算每只股票的阵营分歧
    results = []
    for code, camps in camp_by_stock.items():
        inst_net = camps['institution']
        hot_net = camps['hot_trader']
        trader_net = camps['trader']
        nb_net = camps['northbound']
        
        # 游资总净额 = 知名游资 + 普通营业部（hot_trader + trader）
        total_hot = hot_net + trader_net
        
        # 分歧信号
        if inst_net > 0 and total_hot < 0:
            signal = 'inst_buy_hot_sell'  # 🟢 机构买+游资卖
        elif inst_net < 0 and total_hot > 0:
            signal = 'inst_sell_hot_buy'  # 🔴 机构卖+游资买
        elif inst_net > 0 and total_hot > 0:
            signal = 'both_buy'           # ⚪ 同买
        elif inst_net < 0 and total_hot < 0:
            signal = 'both_sell'          # ⚫ 同卖
        else:
            signal = 'no_diverge'
        
        # 分歧强度
        diverge_strength = 0
        if signal == 'inst_buy_hot_sell':
            diverge_strength = min(abs(inst_net) / max(abs(total_hot), 1), 10.0)
        elif signal == 'inst_sell_hot_buy':
            diverge_strength = min(abs(total_hot) / max(abs(inst_net), 1), 10.0)
        
        results.append({
            'code': code,
            'name': stock_names.get(code, ''),
            'inst_net': inst_net,
            'hot_net': total_hot,
            'north_net': nb_net,
            'signal': signal,
            'strength': diverge_strength,
        })
    
    return results


def update_camp_diverge(trade_date):
    """
    计算并写入所有上榜股票的阵营分歧度
    """
    ds = trade_date.strftime('%Y-%m-%d')
    results = compute_camp_diverge(trade_date)
    
    if not results:
        return []
    
    # 统计各信号数量
    signal_counts = defaultdict(int)
    for item in results:
        signal_counts[item['signal']] += 1
    
    # 按分歧强度排序
    results.sort(key=lambda x: x['strength'], reverse=True)
    
    # 写入market_signals
    total_written = 0
    for item in results:
        extra = {
            'name': item['name'],
            'inst_net': item['inst_net'],
            'hot_net': item['hot_net'],
            'north_net': item['north_net'],
            'signal': item['signal'],
            'strength': item['strength'],
            'type': 'camp_diverge'
        }
        try:
            df._pg_execute(
                """INSERT INTO market_signals (signal_type, code, trade_date, value, extra, updated_at)
                   VALUES (%s, %s, %s, %s, %s::jsonb, NOW())
                   ON CONFLICT (signal_type, code, trade_date)
                   DO UPDATE SET value = EXCLUDED.value, extra = EXCLUDED.extra, updated_at = NOW()""",
                ('camp_diverge', item['code'], ds, item['strength'],
                 json.dumps(extra, ensure_ascii=False)),
                fetch=False
            )
            total_written += 1
        except Exception as e:
            pass
    
    print(f"  {ds}: {len(results)} 只, 写入{total_written}")
    print(f"    信号分布: inst_buy_hot_sell={signal_counts.get('inst_buy_hot_sell', 0)}, "
          f"inst_sell_hot_buy={signal_counts.get('inst_sell_hot_buy', 0)}, "
          f"both_buy={signal_counts.get('both_buy', 0)}, "
          f"both_sell={signal_counts.get('both_sell', 0)}")
    
    # 写入关键的 🟢 和 🔴 信号
    key_signals = [r for r in results if r['signal'] in ('inst_buy_hot_sell', 'inst_sell_hot_buy') and r['strength'] > 1.5]
    if key_signals:
        print(f"\n    🔑 关键阵营分歧:")
        for r in key_signals[:10]:
            sig_icon = '🟢' if r['signal'] == 'inst_buy_hot_sell' else '🔴'
            print(f"      {sig_icon} {r['code']}({r['name']}): "
                  f"机构={r['inst_net']/1e8:.2f}亿 游资={r['hot_net']/1e8:.2f}亿 "
                  f"强度={r['strength']:.1f}")
    
    # 写入市场总览分歧（用aggregate信号）
    total_inst = sum(r['inst_net'] for r in results if r['inst_net'] > 0)
    total_inst_sell = abs(sum(r['inst_net'] for r in results if r['inst_net'] < 0))
    total_hot_buy = sum(r['hot_net'] for r in results if r['hot_net'] > 0)
    total_hot_sell = abs(sum(r['hot_net'] for r in results if r['hot_net'] < 0))
    
    aggregate = {
        'total_inst_buy': total_inst,
        'total_inst_sell': total_inst_sell,
        'total_hot_buy': total_hot_buy,
        'total_hot_sell': total_hot_sell,
        'buy_signals': dict(signal_counts),
        'total_stocks': len(results),
        'type': 'camp_summary'
    }
    market_signal = 'neutral'
    if total_inst > total_inst_sell and total_hot_sell > total_hot_buy:
        market_signal = 'inst_buy_hot_sell_mkt'
    elif total_inst_sell > total_inst and total_hot_buy > total_hot_sell:
        market_signal = 'inst_sell_hot_buy_mkt'
    
    try:
        # 方案C: 用inst_buy_ratio = total_inst_buy / (total_inst_buy + total_inst_sell)
        # diverge_strength = (inst_buy_ratio - 0.5) - (hot_buy_ratio - 0.5), 值域约-1~1
        inst_total_buy_sell = total_inst + total_inst_sell
        hot_total_buy_sell = total_hot_buy + total_hot_sell
        inst_ratio = round(total_inst / max(inst_total_buy_sell, 1e4), 4) if inst_total_buy_sell > 1e4 else 0.5
        hot_ratio = round(total_hot_buy / max(hot_total_buy_sell, 1e4), 4) if hot_total_buy_sell > 1e4 else 0.5
        diverge_strength = round((inst_ratio - 0.5) - (hot_ratio - 0.5), 4)
        
        df._pg_execute(
            """INSERT INTO market_signals (signal_type, code, trade_date, value, extra, updated_at)
               VALUES (%s, %s, %s, %s, %s::jsonb, NOW())
               ON CONFLICT (signal_type, code, trade_date)
               DO UPDATE SET value = EXCLUDED.value, extra = EXCLUDED.extra, updated_at = NOW()""",
            ('camp_diverge_summary', 'market', ds,
             diverge_strength,
             json.dumps(aggregate, ensure_ascii=False)),
            fetch=False
        )
    except:
        pass
    
    return results


def auto_cluster_traders(days=30, min_occurrences=3):
    """
    自动行为聚类：对普通营业部(trader)和other进行分析
    如果某营业部T+1卖出比例>70%，标记为游资
    如果某营业部经常与机构同方向，标记为机构马甲
    
    写入market_signals (signal_type='camp_classify')
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    
    rows, _ = df._pg_execute(
        "SELECT code, trade_date, extra FROM market_signals "
        "WHERE signal_type = 'lhb_trader' AND trade_date >= %s AND trade_date <= %s",
        (start_date, end_date)
    )
    
    # 按营业部分组
    trader_actions = defaultdict(list)
    trader_camp_counts = defaultdict(lambda: defaultdict(int))
    
    for r in rows:
        code_full = r[0]
        extra = r[1] if isinstance(r[1], dict) else json.loads(r[1]) if r[1] else {}
        
        camp = extra.get('camp', 'other')
        trader_name = code_full.split('_', 1)[1] if '_' in code_full else ''
        side = extra.get('side', '')
        net_amt = extra.get('net_amt', 0) or 0
        
        if not trader_name:
            continue
        
        trader_actions[trader_name].append({
            'date': r[2], 'camp': camp, 'side': side, 'net_amt': net_amt,
            'stock_code': code_full.split('_')[0],
            'name': extra.get('name', '')
        })
        trader_camp_counts[trader_name][camp] += 1
    
    # 分析每个营业部的行为
    clusters = {'promote_to_hot': [], 'promote_to_inst': [], 'keep_trader': []}
    
    for trader, actions in trader_actions.items():
        if len(actions) < min_occurrences:
            continue
        
        # 统计买卖方向
        buy_count = sum(1 for a in actions if a['side'] == 'buy' and a['net_amt'] > 0)
        sell_count = sum(1 for a in actions if a['side'] == 'sell' and a['net_amt'] < 0)
        total_trades = buy_count + sell_count
        
        if total_trades < min_occurrences:
            continue
        
        # 检查T+1行为（同一股票隔日反向）
        # 简化版：如果买入后很快又卖出的比例高
        stock_buys = defaultdict(list)
        for a in actions:
            if a['side'] == 'buy' and a['net_amt'] > 0:
                stock_buys[a['stock_code']].append(a['date'])
        
        flip_count = 0
        for sc, dates in stock_buys.items():
            if len(dates) >= 2:
                # 同一股票被该营业部买入两次或以上，间隔短
                sorted_dates = sorted(dates)
                for i in range(len(sorted_dates) - 1):
                    gap = (sorted_dates[i+1] - sorted_dates[i]).days
                    if gap <= 3:  # 3天内有反向操作
                        flip_count += 1
        
        flip_ratio = flip_count / max(len(stock_buys), 1)
        
        # 如果该营业部经常在institution买入后跟进 → 可能是机构马甲
        inst_follow_ratio = 0
        if buy_count > 3:
            inst_stocks = set()
            for a in actions:
                if a['camp'] == 'institution' and a['net_amt'] > 0:
                    inst_stocks.add(a['stock_code'])
            if inst_stocks:
                follow = sum(1 for a in actions if a['stock_code'] in inst_stocks and a['side'] == 'buy' and a['net_amt'] > 0)
                inst_follow_ratio = follow / max(len(inst_stocks), 1)
        
        # 决策
        promoted = False
        if flip_ratio > 0.7 and buy_count >= min_occurrences:
            clusters['promote_to_hot'].append(trader)
            promoted = True
        if not promoted and inst_follow_ratio > 0.6 and buy_count >= min_occurrences:
            clusters['promote_to_inst'].append(trader)
            promoted = True
        if not promoted:
            clusters['keep_trader'].append(trader)
    
    # 输出结果
    print(f"\n🔬 自动行为聚类结果 ({days}天, 最少{min_occurrences}次)")
    print(f"  升级为游资: {len(clusters['promote_to_hot'])}")
    for t in clusters['promote_to_hot'][:10]:
        print(f"    {t}")
    if len(clusters['promote_to_hot']) > 10:
        print(f"    ... 还有{len(clusters['promote_to_hot'])-10}个")
    
    print(f"  升级为机构马甲: {len(clusters['promote_to_inst'])}")
    for t in clusters['promote_to_inst'][:5]:
        print(f"    {t}")
    
    print(f"  保持普通营业部: {len(clusters['keep_trader'])}")
    
    return clusters


def print_summary(latest_date=None):
    """打印最近阵营分歧度摘要"""
    if not latest_date:
        latest_date = date.today()
    
    # 获取最近camp_diverge_summary
    rows, _ = df._pg_execute(
        "SELECT trade_date, value, extra FROM market_signals "
        "WHERE signal_type = 'camp_diverge_summary' ORDER BY trade_date DESC LIMIT 5"
    )
    
    if not rows:
        print("  暂无阵营分歧数据")
        return
    
    print(f"\n{'='*70}")
    print(f"📊 阵营分歧度摘要")
    print(f"{'='*70}")
    print(f"{'日期':<12} {'机构vs游资比':<14} {'buy信号':<12} {'sell信号':<13} {'同买':<8} {'同卖':<8}")
    
    for r in rows:
        extra = r[2] if isinstance(r[2], dict) else json.loads(r[2]) if r[2] else {}
        sigs = extra.get('buy_signals', {})
        print(f"{str(r[0]):<12} {r[1]:<+14.2f} "
              f"{sigs.get('inst_buy_hot_sell', 0):<8} "
              f"{sigs.get('inst_sell_hot_buy', 0):<8} "
              f"{sigs.get('both_buy', 0):<8} "
              f"{sigs.get('both_sell', 0):<8}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='龙虎榜阵营分类器 v2')
    parser.add_argument('--mode', choices=['reclassify', 'diverge', 'cluster', 'summary', 'all'],
                       default='all', help='模式')
    parser.add_argument('--start-date', type=str, default=None)
    parser.add_argument('--end-date', type=str, default=None)
    parser.add_argument('--dry-run', action='store_true', default=True,
                       help='reclassify时只预览不改')
    parser.add_argument('--no-dry-run', action='store_false', dest='dry_run')
    parser.add_argument('--cluster-days', type=int, default=60,
                       help='行为聚类的时间窗口')
    args = parser.parse_args()
    
    print(f"📡 龙虎榜阵营分类器 v2")
    
    if args.end_date:
        end = datetime.strptime(args.end_date, '%Y%m%d').date()
    else:
        end = date.today()
    
    if args.start_date:
        start = datetime.strptime(args.start_date, '%Y%m%d').date()
    else:
        start = end - timedelta(days=30)
    
    if args.mode in ('reclassify', 'all'):
        print(f"\n📥 重新分类已有lhb_trader数据...")
        reclassify_lhb_trader(dry_run=args.dry_run)
    
    if args.mode in ('cluster', 'all'):
        print(f"\n📥 自动行为聚类...")
        auto_cluster_traders(days=args.cluster_days)
    
    if args.mode in ('diverge', 'all'):
        print(f"\n📥 计算阵营分歧度...")
        # 只算最近N天
        d = start
        while d <= end:
            # 检查该日是否有lhb_trader数据
            cnt, _ = df._pg_execute(
                "SELECT count(*) FROM market_signals WHERE signal_type='lhb_trader' AND trade_date=%s",
                (d,)
            )
            if cnt and cnt[0][0] > 0:
                update_camp_diverge(d)
            d += timedelta(days=1)
    
    if args.mode in ('summary', 'all'):
        print_summary(end)
    
    print("\n✅ 完成")
