#!/usr/bin/env python3
"""
庄家行为全市场扫描 v2 — 回测驱动版
集成回测结论：
1. V8阈值分层（-15%/-20%/-25%/-30%），越深优先级越高
2. 庄家阶段优先级：出货期 > 建仓期 > 试盘期 > 拉升期(熊市) > 无主力
3. 输出每日精选+操作建议（带回测胜率和预期收益）
"""
import sys, os, json, psycopg2
import numpy as np
import pandas as pd
from datetime import date, timedelta
from collections import defaultdict, OrderedDict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
MIN_FLOAT_MV = 10  # 亿

# ============== V8阈值分层（回测结论）==============
V8_TIERS = [
    ('S3深度', -0.30, 8.62, 68.1),   # 阈值, T+20中位, 胜率
    ('S2重仓', -0.25, 6.93, 65.2),
    ('S1中度', -0.20, 4.56, 61.5),
    ('V8标准', -0.15, 2.67, 58.0),
    ('V8轻仓', -0.10, 1.26, 54.3),
]

# 各阶段修正系数（回测结论：出货期>建仓期>无主力）
STAGE_MULTIPLIERS = OrderedDict([
    ('出货期', 1.4),       # 最佳：V8+出货+深熊 = +8.62% × 1.4 = +12%
    ('建仓期', 1.1),       # 稳健：V8+建仓
    ('试盘期', 0.8),       # 样本少，折扣
    ('无主力特征', 1.0),   # 基准
    ('拉升期', 0.5),       # 反指风险高
    ('洗盘期', 0.3),       # 样本太少
])

def get_regime(conn, d):
    """获取指定日期的regime"""
    cur = conn.cursor()
    cur.execute("SELECT extra FROM pool_energy WHERE level='market' AND date <= %s AND extra IS NOT NULL ORDER BY date DESC LIMIT 1", (d,))
    r = cur.fetchone()
    if not r:
        return 'SIDEWAYS'
    e = r[0] if isinstance(r[0], dict) else (json.loads(r[0]) if isinstance(r[0], str) else {})
    return e.get('regime', 'SIDEWAYS')

def get_surplus_z(conn, d):
    """获取指定日期的surplus_z"""
    cur = conn.cursor()
    cur.execute("SELECT extra FROM pool_energy WHERE level='market' AND date <= %s AND extra IS NOT NULL ORDER BY date DESC LIMIT 1", (d,))
    r = cur.fetchone()
    if not r:
        return 0
    e = r[0] if isinstance(r[0], dict) else (json.loads(r[0]) if isinstance(r[0], str) else {})
    return float(e.get('surplus_z', 0))

def analyze_one_stock(df):
    """
    向量化分析一只股票的庄家行为阶段 + V8超跌层级
    返回: {stage, v8_tier, priority_score, ...}
    """
    n = len(df)
    if n < 120:
        return None
    
    closes = df['close'].values.astype(float)
    opens = df['open'].values.astype(float) if 'open' in df else closes.copy()
    highs = df['high'].values.astype(float) if 'high' in df else closes.copy()
    lows = df['low'].values.astype(float) if 'low' in df else closes.copy()
    volumes = df['volume'].values.astype(float) if 'volume' in df else np.ones(n)
    
    r1d = np.full(n, np.nan)
    r1d[1:] = closes[1:] / closes[:-1] - 1
    
    yang = closes >= opens
    idx = n - 1  # 最新交易日
    
    # === 基本指标 ===
    yang_20 = np.mean(yang[idx-19:idx+1])
    vol_10 = np.mean(volumes[idx-9:idx+1])
    vol_60 = np.mean(volumes[max(0,idx-59):idx+1])
    vol_ratio = vol_10 / max(vol_60, 1)
    
    amp_20 = np.mean([(highs[j]-lows[j])/max(opens[j],0.01) for j in range(max(0,idx-19), idx+1)])
    
    sma120 = np.mean(closes[max(0,idx-119):idx+1])
    sma_dev = closes[idx] / max(sma120, 0.01) - 1
    
    # 长上影
    candle_range = highs[idx] - lows[idx]
    upper_shadow = highs[idx] - max(opens[idx], closes[idx])
    lower_shadow = min(opens[idx], closes[idx]) - lows[idx]
    shadow_ratio = upper_shadow / max(candle_range, 0.001)
    
    # 连跌
    conseq_dn = 0
    total_decline = 0.0
    for k in range(idx, max(0, idx-5), -1):
        if not np.isnan(r1d[k]) and r1d[k] < 0:
            conseq_dn += 1
            total_decline = (1 + total_decline) * (1 + r1d[k]) - 1
        else:
            break
    
    # 前10天长上影计数
    shadow_count = 0
    for j in range(max(0, idx-9), idx+1):
        cr = highs[j] - lows[j]
        us = highs[j] - max(opens[j], closes[j])
        if cr > 0 and us / cr > 0.4:
            shadow_count += 1
    
    # 前60天最高
    high_60 = np.max(highs[max(0, idx-59):idx+1])
    
    # 量比今日
    vol_ma20 = np.mean(volumes[max(0, idx-19):idx+1])
    vol_ratio_today = volumes[idx] / max(vol_ma20, 1)
    
    # 5日阳线
    yang_5 = np.sum(yang[max(0, idx-4):idx+1])
    
    # === 阶段判定（简化为最新交易日）===
    stage = "无主力特征"
    stage_signals = []
    
    # (1) 建仓期
    if yang_20 > 0.55 and 1.0 <= vol_ratio <= 1.8 and amp_20 < 0.05:
        stage = "建仓期"
        stage_signals.append("建仓")
    
    # (2) 试盘期（长上影+冲高回落+放量）
    if (candle_range > 0 and shadow_ratio > 0.5 and upper_shadow > lower_shadow and
        closes[idx] < opens[idx] and r1d[idx] > -0.03 and r1d[idx] < 0.05 and
        vol_ratio_today > 1.5 and upper_shadow / max(closes[idx], 0.01) > 0.03):
        stage = "试盘期"
        stage_signals.append("试盘")
    
    # (3) 拉升期
    if (yang_5 >= 3 and vol_ratio > 1.1 and not np.isnan(r1d[idx]) and 
        r1d[idx] > 0 and r1d[idx] < 0.105 and amp_20 > 0.035):
        if highs[idx] > high_60 * 0.95 or highs[idx] > np.mean(closes[max(0,idx-59):idx]) * 1.1:
            stage = "拉升期"
            stage_signals.append("拉升")
    
    # (4) 出货期
    past_5_vol = np.mean(volumes[max(0, idx-4):idx+1])
    past_20_vol = np.mean(volumes[max(0, idx-19):idx+1])
    vol_increase = past_5_vol / max(past_20_vol, 1)
    
    if (vol_increase > 1.3 and shadow_count >= 3):
        if stage != "拉升期" or vol_increase > 1.5:  # 非拉升期或拉升过量
            stage = "出货期"
            stage_signals.append("出货")
    
    # (5) 洗盘判定（近10天内是否有洗盘模式）
    # 简化：只看最近连跌是否缩量
    if conseq_dn >= 2 and not np.isnan(r1d[idx]) and r1d[idx] < -0.03 and vol_ratio_today < 0.7:
        # 可能是洗盘结尾（缩量下跌）
        if stage == "无主力特征":
            stage = "洗盘期"
            stage_signals.append("缩量下跌") 
    
    # === V8层级判定 ===
    v8_tier_name = None
    v8_expected_return = 0
    v8_win_rate = 0
    for name, threshold, ret, win in V8_TIERS:
        if sma_dev < threshold:
            v8_tier_name = name
            v8_expected_return = ret
            v8_win_rate = win
            break
    
    # === 综合优先级分 ===
    stage_mult = STAGE_MULTIPLIERS.get(stage, 1.0)
    priority_score = 0
    if v8_tier_name:
        # V8层级越深优先级越高
        tier_idx = sum(1 for _, t, _, _ in V8_TIERS if sma_dev < t)
        # 直接用sma_dev深度 × 阶段系数
        depth = abs(sma_dev) * 100  # 10%偏差=10分
        priority_score = depth * stage_mult
    
    return {
        'close': closes[idx],
        'price': closes[idx],
        'sma120': sma120,
        'sma_dev': sma_dev,
        'v8_tier': v8_tier_name or '无V8',
        'v8_expected_return': v8_expected_return,
        'v8_win_rate': v8_win_rate,
        'stage': stage,
        'signals': stage_signals,
        'yang_20': yang_20,
        'vol_ratio': vol_ratio,
        'amp_20': amp_20,
        'shadow_count': shadow_count,
        'vol_ratio_today': vol_ratio_today,
        'conseq_dn': conseq_dn,
        'total_decline': total_decline,
        'priority_score': priority_score,
        'stage_mult': stage_mult,
    }

def main():
    today = date.today()
    
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    regime = get_regime(conn, today)
    sz = get_surplus_z(conn, today)
    
    # 获取全市场股票
    cur.execute("""
        SELECT s.code, s.name, m.pb, m.float_mv FROM stock_list s
        JOIN market_snapshots m ON s.code = m.code
        WHERE m.trade_date = (SELECT max(trade_date) FROM market_snapshots)
        AND m.float_mv >= %s AND m.pb > 0
    """, (MIN_FLOAT_MV,))
    stocks_data = {r[0]: {'name': r[1], 'pb': float(r[2]), 'float_mv': float(r[3])} for r in cur.fetchall()}
    
    print(f"待分析: {len(stocks_data)}只")
    
    # 逐只分析
    results = {}
    analyzed = 0
    for sc, info in stocks_data.items():
        cur.execute(f"""
            SELECT trade_date, close, open, high, low, volume
            FROM daily_prices WHERE symbol='A.{sc}' AND close > 0
            ORDER BY trade_date DESC LIMIT 120
        """)
        rows = cur.fetchall()
        if len(rows) < 120:
            continue
        
        # 反转成时间序
        rows.reverse()
        df = pd.DataFrame(rows, columns=['trade_date','close','open','high','low','volume'])
        
        res = analyze_one_stock(df)
        if res:
            res['code'] = sc
            res['name'] = info['name']
            res['pb'] = info['pb']
            res['float_mv'] = info['float_mv']
            results[sc] = res
        
        analyzed += 1
        if analyzed % 500 == 0:
            print(f"  {analyzed}/{len(stocks_data)}...")
    
    conn.close()
    print(f"完成: {len(results)}只")
    
    # 按V8分层排序
    by_v8 = defaultdict(list)
    for sc, res in results.items():
        if res['v8_tier'] != '无V8':
            by_v8[res['v8_tier']].append((sc, res))
    
    # 各V8层内按优先级排序
    for tier in by_v8:
        by_v8[tier].sort(key=lambda x: x[1]['priority_score'], reverse=True)
    
    # === 输出 ===
    out = []
    out.append(f"# 庄家行为全市场扫描 v2 | {today}")
    out.append(f"")
    out.append(f"**Regime**: {regime} | **surplus_z**: {sz:.3f}")
    out.append(f"**分析**: {len(results)}只 | **V8信号**: {sum(1 for r in results.values() if r['v8_tier']!='无V8')}只")
    out.append(f"")
    
    # V8+各阶段的统计
    v8_results = {sc: r for sc, r in results.items() if r['v8_tier'] != '无V8'}
    stage_counts = defaultdict(int)
    total_weight = 0
    total_score = 0
    for sc, r in v8_results.items():
        stage_counts[r['stage']] += 1
    
    v8_stage_order = ['出货期', '建仓期', '试盘期', '洗盘期', '拉升期', '无主力特征']
    v8_stage_parts = []
    for s in v8_stage_order:
        c = stage_counts.get(s, 0)
        if c > 0:
            mult = STAGE_MULTIPLIERS.get(s, 1.0)
            label = f"{s}({c}只)"
            if mult >= 1.3:
                label += "⭐"
            v8_stage_parts.append(label)
    out.append(f"**V8分布**: {' | '.join(v8_stage_parts) if v8_stage_parts else '无'}")
    
    # === V8分层输出 ===
    out.append(f"")
    out.append(f"## V8精选（按层级）")
    out.append(f"")
    for tier_name, threshold, exp_ret, win_rate in V8_TIERS:
        stocks = by_v8.get(tier_name, [])
        if not stocks:
            continue
        
        out.append(f"### {tier_name} (sma_dev < {threshold:.0%}) — {len(stocks)}只")
        out.append(f"")
        out.append(f"| # | 代码 | 名称 | PB | 阶段 | 优先级 | sma_dev | T+20预期 | T+20胜率 |")
        out.append(f"|---|---|---|---|---|---|---|---|---|")
        
        for rank, (sc, res) in enumerate(stocks[:20], 1):
            stage_label = res['stage']
            mult = STAGE_MULTIPLIERS.get(res['stage'], 1.0)
            expected_pct = exp_ret * mult * 100
            out.append(f"| {rank} | {sc} | {res['name'][:6] if res['name'] else '?'} | {res['pb']:.2f} | {stage_label} | {res['priority_score']:.0f} | {res['sma_dev']:+.2%} | +{expected_pct:.1f}% | {win_rate:.1f}% |")
        out.append(f"")
    
    # === 当前持仓 ===
    watchlist = ['002073', '600273']
    out.append(f"## 当前持仓")
    out.append(f"")
    out.append(f"| 代码 | 名称 | 现价 | PB | 阶段 | V8层级 | sma_dev | 建议 |")
    out.append(f"|---|---|---|---|---|---|---|---|")
    for sc in watchlist:
        if sc not in results:
            out.append(f"| {sc} | - | - | - | - | - | - | ⚠️ 无数据 |")
            continue
        r = results[sc]
        
        # 阶段建议（回测修正）
        if r['v8_tier'] == '无V8':
            advice = "未到V8，等待进一步下跌"
        elif r['stage'] == '出货期':
            # 最佳情况
            advice = f"✅ 主力仍在(出货期)。预计T+20 +2.7%~+8.6% 胜率~68%"
        elif r['stage'] == '建仓期':
            advice = f"✅ 主力在建仓。T+20预期+1.1%~+2.3%"
        elif r['stage'] == '无主力特征':
            advice = f"🟡 V8超跌反弹。T+20预期+2.0%~+5.6%"
        else:
            advice = f"持有观察"
        
        out.append(f"| {sc} | {r['name'][:6] if r['name'] else '?'} | {r['price']:.2f} | {r['pb']:.2f} | {r['stage']} | {r['v8_tier']} | {r['sma_dev']:+.2%} | {advice} |")
    
    # 写入
    report_dir = "/home/tearsyu/.openclaw/workspace/memory/reports"
    os.makedirs(report_dir, exist_ok=True)
    report_path = f"{report_dir}/zhuangjia_v2_{today}.md"
    with open(report_path, 'w') as f:
        f.write('\n'.join(out))
    
    disk_dir = f"/mnt/d/data/reports/{today}"
    os.makedirs(disk_dir, exist_ok=True)
    disk_path = f"{disk_dir}/庄家行为监控_v2_{today}.md"
    with open(disk_path, 'w') as f:
        f.write('\n'.join(out))
    
    print(f"\n已写入 {disk_path}")
    print("\n".join(out[:30]))

if __name__ == '__main__':
    main()
