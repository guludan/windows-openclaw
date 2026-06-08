#!/usr/bin/env python3
"""
最终评级表 — 使用 rolling_bt_candidates.py 已跑的准确数据
=================================================
完全替代 final_ratings.py 的重算逻辑
"""
import json, os

OUT = '/home/tearsyu/.openclaw/workspace/memory/cost_deviation'

stocks = [
    {
        'name': '嘉化能源', 'code': '600273',
        'wr_all': 68, 'wr_bear_deep': 77, 'wr_bear_extreme': 88,
        'tail_risk': 0, 'n_bear_extreme': 17,
        'stage': '无主力特征', 'hold_state': '正常',
        'profit_trend': '恶化(-62%)', 'sdev': -22.9,
        'base': 'B', 'be_boost': True, 'stage_boost': 0,
        'profit_penalty': 1, 'hold_penalty': 0,
    },
    {
        'name': '软控股份', 'code': '002073',
        'wr_all': 67, 'wr_bear_deep': 'N/A', 'wr_bear_extreme': 'N/A',
        'tail_risk': 1, 'n_bear_extreme': 'N/A',
        'stage': '无主力特征', 'hold_state': '正常',
        'profit_trend': '恶化(-60%)', 'sdev': -24.6,
        'base': 'B', 'be_boost': 0, 'stage_boost': 0,
        'profit_penalty': 1, 'hold_penalty': 0,
    },
    {
        'name': '大众公用', 'code': '600635',
        'wr_all': 61, 'wr_bear_deep': 85, 'wr_bear_extreme': 86,
        'tail_risk': 0, 'n_bear_extreme': 56,
        'stage': '建仓期', 'hold_state': '出逃',
        'profit_trend': '恶化(-74%)', 'sdev': -15.0,
        'base': 'C', 'be_boost': True, 'stage_boost': 0.5,
        'profit_penalty': 1, 'hold_penalty': 1,
    },
    {
        'name': '尖峰集团', 'code': '600668',
        'wr_all': 57, 'wr_bear_deep': 68, 'wr_bear_extreme': 69,
        'tail_risk': 0, 'n_bear_extreme': 55,
        'stage': '无主力特征', 'hold_state': '正常',
        'profit_trend': '恶化(-103%)', 'sdev': -26.1,
        'base': 'C', 'be_boost': True, 'stage_boost': 0,
        'profit_penalty': 1, 'hold_penalty': 0,
    },
    {
        'name': '广汇物流', 'code': '600603',
        'wr_all': 58, 'wr_bear_deep': 42, 'wr_bear_extreme': 47,
        'tail_risk': 2, 'n_bear_extreme': 83,
        'stage': '无主力特征', 'hold_state': '轻度活跃',
        'profit_trend': '恶化(-93%)', 'sdev': -25.9,
        'base': 'C', 'be_boost': False, 'stage_boost': 0,
        'profit_penalty': 1, 'hold_penalty': 0,
    },
]

# 评分逻辑
def calc(record):
    b = record
    level_map = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
    base_num = level_map[b['base']]
    
    up = 0
    down = 0
    reasons = []
    
    # BEAR深跌加分（实际确认该环境胜率明显优于全市场）
    if b.get('be_boost') and isinstance(b['wr_bear_deep'], (int,float)) and b['wr_bear_deep'] >= b['wr_all'] + 5:
        up += 1
        reasons.append(f'BEAR深跌{b["wr_bear_deep"]}%')
    
    # 庄家行为
    if b['stage'] == '建仓期':
        up += 0.5
        reasons.append('建仓+0.5')
    elif b['stage'] == '出货期':
        up += 1
        reasons.append('出货+1')
    
    # 净利润恶化
    if b['profit_penalty']:
        down += 1
        reasons.append('净利-1')
    
    # 筹码出逃
    if b['hold_penalty']:
        down += 1
        reasons.append('出逃-1')
    
    final_num = base_num + down - up
    final_num = max(0, min(3, round(final_num)))
    rev = {0: 'A', 1: 'B', 2: 'C', 3: 'D'}
    final = rev[final_num]
    
    pos = { 'A': 1.0, 'B': 0.6, 'C': 0.3, 'D': 0 }[final]
    return final, pos, ', '.join(reasons) if reasons else '基线'


print(f"\n{'='*90}")
print(f"  最终评级表 V2 — 2026-06-04 | 基于逐日滚动回测准确数据")
print(f"{'='*90}")
print(f"\n  {'股票':>8} {'代码':>8} {'T+20wr':>6} {'BEAR深跌':>8} {'极空深跌':>8} {'极端亏':>6} {'庄家':>8} {'筹码':>8} {'等级':>4} {'仓位':>5} {'理由'}")
print(f"  {'-'*95}")

for b in stocks:
    grade, pos, reason = calc(b)
    sig = '✅' if grade in ('A','B') else ('⚠️' if grade == 'C' else '❌')
    print(f"  {b['name']:>8} {b['code']:>8} {b['wr_all']:>5.0f}% {str(b['wr_bear_deep']):>7} {str(b['wr_bear_extreme']):>7} {b['tail_risk']:>5}% {b['stage']:>8} {b['hold_state']:>8} {sig}{grade:>3} {pos:>4.1f}x {reason}")

# 操作指南
print(f"\n  --- 操作指南 ---")
print(f"  A级 (1.0x): V8满足即入场 | P1卖1/3, P2卖1/3, 剩余等A_RECOVER脉冲")
print(f"  B级 (0.6x): V8满足即入场 | P1卖1/2, P2清仓")
print(f"  C级 (0.3x): 仅BEAR极空入场 | P1卖1/2, P2清仓")
print(f"  D级 (0): 不参与")

print(f"\n  {'='*50}")
print(f"  持仓建议")
print(f"  {'='*50}")
for b in stocks:
    grade, pos, reason = calc(b)
    if grade in ('A','B'):
        note = "可加至满仓" if grade == 'A' else "维持当前仓位不加"
        print(f"  ✅ {b['name']}({b['code']}): {grade}级 {pos}x - {note}")
    else:
        wait = ''
        if b['sdev'] and b['sdev'] > -18:
            wait = f' 当前偏离{b["sdev"]:.1f}%, 等加深到-18%以下'
        elif grade == 'D':
            wait = ' 评级D级暂不参与'
        else:
            wait = f' 偏离{b["sdev"]:.1f}%, 等条件触发'
        print(f"  ⏸ {b['name']}({b['code']}): {grade}级 -{wait}")

# 保存
os.makedirs(OUT, exist_ok=True)
outp = os.path.join(OUT, 'final_ratings_v2.json')
with open(outp, 'w') as f:
    json.dump(stocks, f, ensure_ascii=False, indent=1)
print(f"\n  保存: {outp}")
