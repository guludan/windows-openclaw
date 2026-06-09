#!/usr/bin/env python3
"""
V8增强管线 — 全市场扫描 + 完整决策表

流程:
  1. 牛熊识别 → Regime + 动态参数
  2. V8超跌筛选 (SMA<-15%, PB<1.0)
  3. Layer 0财务排雷
  4. 洗盘/崩盘判别
  5. 熵法成本分析
  6. 分层L4回测 (含Regime维度)
  7. 博弈决策表 → 最终交易清单

输出: 按操作优先级排序的交易清单
"""
import sys, json
sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import numpy as np
from datetime import date, timedelta
from collections import defaultdict
from strategies.regime_detector import RegimeDetector, regime_dynamic_params
from strategies.washout_discriminator import analyze_kline_pattern
from strategies.active_filter import compute_cost_levels_entropy, DB
from strategies.layer4_regime_backtest import calc_regime_winrate, load_regime_map, layer0_check
from strategies.decision_table import make_decision


def scan_all(target_date=None):
    """全市场扫描"""
    if target_date is None:
        target_date = date.today()
    
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    # 0. 牛熊识别
    detector = RegimeDetector(conn)
    regime_data = detector.get_current_regime(target_date)
    params = regime_dynamic_params(regime_data['regime'])
    print(f"{'='*80}")
    print(f"V8增强管线 — 全市场扫描 {target_date}")
    print(f"Regime: {regime_data['regime']} (surplus_z={regime_data['surplus_z']}) | {params['desc']}")
    print(f"仓位上限: {params['max_position']*100:.0f}%")
    print(f"{'='*80}\n")
    
    # 1. V8超跌筛选
    print("【1】V8超跌筛选 (SMA<-15%, PB<1.0) ...")
    cur.execute("""
        WITH sma AS (
            SELECT symbol,
                   AVG(close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW) as sma120,
                   close, trade_date
            FROM daily_prices
            WHERE trade_date <= %s AND close>0 AND volume>0
        )
        SELECT d.symbol, sl.close, sl.close / NULLIF(sl.sma120,0) - 1 as sma_dev
        FROM (
            SELECT symbol, MAX(trade_date) as last_date
            FROM daily_prices WHERE trade_date <= %s AND close>0 AND volume>0
            GROUP BY symbol
        ) d
        JOIN sma sl ON sl.symbol=d.symbol AND sl.trade_date=d.last_date
        WHERE sl.sma120 IS NOT NULL AND sl.close / sl.sma120 - 1 < -0.15
          AND sl.close > 1.0
          AND sl.symbol NOT IN ('A.000001','A.399001','A.399006')
        ORDER BY sma_dev
    """, (target_date, target_date))
    
    candidates = []
    for r in cur.fetchall():
        # 检查PB (用bvps)
        sym = r[0]
        close = float(r[1])
        cur.execute("""
            SELECT bvps FROM financial_data 
            WHERE symbol=%s AND bvps>0 ORDER BY end_date DESC LIMIT 1
        """, (sym,))
        bvps_r = cur.fetchone()
        pb = close / float(bvps_r[0]) if bvps_r and bvps_r[0] else 999
        
        if pb < 1.0:
            candidates.append({
                'sym': sym,
                'close': close,
                'sma_dev': round(float(r[2])*100, 1),
                'pb': round(pb, 2),
            })
    
    print(f"  通过: {len(candidates)} 只\n")
    
    if len(candidates) == 0:
        conn.close()
        return []
    
    # 2-7. 逐只执行全流程
    print(f"【2~7】全流程分析 ({len(candidates)}只, 预计{len(candidates)*8}秒)...")
    
    results = []
    for i, cand in enumerate(candidates):
        if i % 10 == 0 and i > 0:
            print(f"  进度: {i}/{len(candidates)}, 已出结果: {len(results)}")
        
        try:
            d = make_decision(conn, cand['sym'], target_date)
            if d:
                d['sma_dev'] = cand['sma_dev']
                d['pb'] = cand['pb']
                results.append(d)
        except Exception as e:
            pass
    
    # 排序: 操作优先级(建仓>轻仓=加仓) + 跌破成本深度
    def sort_key(x):
        action_rank = {'建仓': 0, '轻仓': 1, '加仓': 2, '观望': 9, '避开': 10}
        ar = action_rank.get(x.get('action', ''), 9)
        # 同等级按跌破成本排序
        cost = x.get('true_cost', 0)
        price = x.get('current_price', 0)
        deviation = (price / cost - 1) * 100 if cost > 0 else 0
        return (ar, deviation)
    
    results.sort(key=sort_key)
    
    # === 输出 ===
    print(f"\n{'='*100}")
    print(f"最终交易清单")
    print(f"{'='*100}")
    print(f"{'#':>3} {'代码':<8} {'现价':>6} {'操作':<8} {'仓位':>5} {'Regime':<14} {'洗盘':<14} {'成本':>6} {'防线':>6} {'SMA偏':>6} {'L4通':>6} {'L4熊':>6} {'L0':<6} {'止损':>6} {'止盈':>6}")
    print('-' * 100)
    
    for idx, r in enumerate(results[:20], 1):
        print(f"{idx:>3d} {r['code']:<8} {r['current_price']:>6.2f} {r['action']:<8} {r['position']*100:>4.0f}% {r['regime']:<14} {r['washout_pattern']:<14} {r['true_cost']:>6.2f} {r['defense_line']:>6.2f} {r.get('sma_dev','?'):>6} {r.get('l4_general_winrate',0):>5.0f}% {r.get('l4_bear_winrate',0):>5.0f}% {r['layer0'][:2]:<6} {r['stop_loss']:>6.2f} {r['take_profit']:>6.2f}")
        print(f"  {r['reason']}")
        print(f"  {r['washout_detail'][:90]}")
    
    conn.close()
    return results


if __name__ == '__main__':
    target = date(2026, 6, 5)
    results = scan_all(target)
    
    # 保存到文件
    import json
    out_path = f'/home/tearsyu/.openclaw/workspace/memory/reports/full_scan_{target.strftime("%Y%m%d")}.json'
    with open(out_path, 'w') as f:
        json.dump(results, f, default=str, indent=2)
    print(f"\n完整结果保存至: {out_path}")
