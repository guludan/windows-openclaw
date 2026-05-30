#!/usr/bin/env python3
"""
V8 策略引擎 — 三重微观信号牛熊状态机
用surplus_z的5日/20日均线交叉 + 绝对值水平 + 加速度驱动5态切换

输出: /tmp/v8_state_machine.log
"""
from __future__ import print_function
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, numpy as np
from datetime import date, datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
LOG = '/tmp/v8_state_machine.log'

def log(msg='', end='\n'):
    with open(LOG, 'a') as f:
        f.write(str(msg) + end)
    print(msg, flush=True)

def banner(t):
    log(); log('='*80); log('  ' + t); log('='*80)

# V8 参数表
PARAMS = {
    'BULL': {
        'desc': '牛市 — 贝塔红利，整体放宽',
        'thresholds': {
            'A_RECOVER': -0.05, 'A_ACCUM': -0.05,
            'NEUTRAL': -0.10, 'B_ACCUM': -0.10,
            'B_DISTRIB': -0.15, 'HI_EQ': None,
        },
        'pb_condition': 'PB<1',
        'pb_hardcore': None,  # DENSE行业不额外加严
    },
    'TOP_WARNING': {
        'desc': '冲顶预警 — 过热减仓，逐步收紧',
        'thresholds': {
            'A_RECOVER': -0.05, 'A_ACCUM': -0.05,
            'NEUTRAL': -0.10, 'B_ACCUM': -0.15,
            'B_DISTRIB': -0.20, 'HI_EQ': None,
        },
        'pb_condition': 'PB<1',
        'pb_hardcore': None,
    },
    'SIDEWAYS': {
        'desc': '震荡 — 基线，严控胜率',
        'thresholds': {
            'A_RECOVER': -0.05, 'A_ACCUM': -0.05,
            'NEUTRAL': -0.15, 'B_ACCUM': -0.15,
            'B_DISTRIB': -0.20, 'HI_EQ': None,
        },
        'pb_condition': 'PB<1',
        'pb_hardcore': None,
    },
    'BOTTOM_WARNING': {
        'desc': '底部预警 — 准备参与反弹，渐进放宽',
        'thresholds': {
            'A_RECOVER': -0.05, 'A_ACCUM': -0.05,
            'NEUTRAL': -0.15, 'B_ACCUM': -0.15,
            'B_DISTRIB': -0.20, 'HI_EQ': None,
        },
        'pb_condition': 'PB<1',
        'pb_hardcore': None,
    },
    'BEAR': {
        'desc': '熊市 — 全面收窄，B_DISTRIB关闭',
        'thresholds': {
            'A_RECOVER': -0.10, 'A_ACCUM': -0.10,
            'NEUTRAL': -0.20, 'B_ACCUM': -0.25,
            'B_DISTRIB': None,  # 关闭
            'HI_EQ': None,
        },
        'pb_condition': 'PB<1',
        'pb_hardcore': {'B_ACCUM': 0.7},  # B_ACCUM需PB<0.7
    },
}

# 状态名映射: to_xxx -> PARAMS keys
TO_PARAM = {
    'BULL': 'BULL', 'BEAR': 'BEAR', 'SIDEWAYS': 'SIDEWAYS',
    'TOP_WARNING': 'TOP_WARNING', 'BOTTOM_WARNING': 'BOTTOM_WARNING',
    'to_bull': 'BULL', 'to_bear': 'BEAR', 'to_sideways': 'SIDEWAYS',
    'to_top_warning': 'TOP_WARNING', 'to_bottom_warning': 'BOTTOM_WARNING',
}

# 状态转换矩阵
TRANSITIONS = {
    'INIT': {
        'BULL': lambda sz5, sz20, sz, acc: sz5 > sz20 and sz > -0.5,
        'BEAR': lambda sz5, sz20, sz, acc: sz5 < sz20 and sz < 0.5,
        'default': 'SIDEWAYS',
    },
    'BULL': {
        'TOP_WARNING': lambda sz5, sz20, sz, acc: (sz5 < sz20 and sz > 1.5) or (sz5 < sz20 and acc < -0.3 and sz > 0.5),
        'SIDEWAYS': lambda sz5, sz20, sz, acc: sz5 < sz20 and -1.0 < sz < 1.5 and abs(acc) < 0.3,
        'BEAR': lambda sz5, sz20, sz, acc: sz5 < sz20 and sz < -0.5,
        'default': 'BULL',
    },
    'TOP_WARNING': {
        'BULL': lambda sz5, sz20, sz, acc: sz5 > sz20 and sz > 0,  # 金叉回升
        'BEAR': lambda sz5, sz20, sz, acc: sz5 < sz20 and sz < -0.5,  # 死叉确认入熊
        'SIDEWAYS': lambda sz5, sz20, sz, acc: sz5 < sz20 and -0.5 < sz < 1.5 and acc > -0.3,  # 温和回落
        'default': 'TOP_WARNING',
    },
    'SIDEWAYS': {
        'BULL': lambda sz5, sz20, sz, acc: sz5 > sz20 and sz > 0.5,
        'TOP_WARNING': lambda sz5, sz20, sz, acc: sz > 2.0 and acc < -0.5,
        'BOTTOM_WARNING': lambda sz5, sz20, sz, acc: sz < -2.0 and acc > 0.5,
        'BEAR': lambda sz5, sz20, sz, acc: sz5 < sz20 and sz < -1.0 and acc < 0,
        'default': 'SIDEWAYS',
    },
    'BOTTOM_WARNING': {
        'BULL': lambda sz5, sz20, sz, acc: sz5 > sz20 and sz > -0.5,  # 金叉确认脱离底部
        'BEAR': lambda sz5, sz20, sz, acc: sz5 < sz20 and sz < -1.5,  # 继续下挫
        'SIDEWAYS': lambda sz5, sz20, sz, acc: sz5 > sz20 and -1.5 < sz < -0.5,  # 缓慢回升回震荡
        'default': 'BOTTOM_WARNING',
    },
    'BEAR': {
        'BOTTOM_WARNING': lambda sz5, sz20, sz, acc: sz5 > sz20 and sz < -0.5,  # 金叉但仍在负值
        'SIDEWAYS': lambda sz5, sz20, sz, acc: sz5 > sz20 and -0.5 < sz < 0.5,  # 金叉回到零轴
        'default': 'BEAR',
    },
}

def resolve_next_state(current, sz5, sz20, sz, acc):
    """找到第一个匹配的转换"""
    trans = TRANSITIONS.get(current, TRANSITIONS['INIT'])
    for target, cond in trans.items():
        if target == 'default': continue
        try:
            if cond(sz5, sz20, sz, acc):
                return TO_PARAM.get(target, target)
        except:
            continue
    return TO_PARAM.get(trans.get('default', 'SIDEWAYS'), trans.get('default', 'SIDEWAYS'))


def main():
    open(LOG, 'w').close()
    banner(f'V8 状态机 — 三重微观信号牛熊切换')
    log(f'  启动: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    
    # 加载pool_energy数据
    log('\n[1] 加载pool_energy...')
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT date, surplus FROM pool_energy WHERE level='market' ORDER BY date")
    rows = cur.fetchall()
    conn.close()
    
    dates = [r[0] for r in rows]
    surplus = np.array([float(r[1] or 0) for r in rows], dtype=float)
    n = len(surplus)
    
    # 60日滚动z-score
    sz = np.full(n, np.nan)
    for i in range(59, n):
        seg = surplus[i-59:i+1]
        mu, sd = np.mean(seg), np.std(seg)
        if sd > 0:
            sz[i] = (surplus[i] - mu) / sd
    
    def thr_sma(arr, w):
        res = np.full(n, np.nan)
        for i in range(len(arr)):
            if i < w-1 or np.isnan(arr[i]): continue
            seg = arr[i-w+1:i+1]
            if np.any(np.isnan(seg)): continue
            res[i] = np.mean(seg)
        return res
    
    sz_5 = thr_sma(sz, 5)
    sz_20 = thr_sma(sz, 20)
    
    accel = np.full(n, np.nan)
    for i in range(2, n):
        if not np.isnan(sz[i]) and not np.isnan(sz[i-1]) and not np.isnan(sz[i-2]):
            accel[i] = sz[i] - 2*sz[i-1] + sz[i-2]
    
    # 运行状态机
    log('\n[2] 状态机遍历...')
    states = np.full(n, '', dtype=object)
    first_valid = None
    for i in range(n):
        if not np.isnan(sz_5[i]) and not np.isnan(sz_20[i]):
            first_valid = i
            states[i] = 'SIDEWAYS'  # 起始
            break
    
    for i in range(first_valid+1, n):
        if np.isnan(sz_5[i]) or np.isnan(sz_20[i]) or np.isnan(sz[i]):
            states[i] = states[i-1] if states[i-1] else 'SIDEWAYS'
            continue
        a = 0.0 if np.isnan(accel[i]) else accel[i]
        prev = states[i-1] if states[i-1] else 'SIDEWAYS'
        states[i] = resolve_next_state(prev, sz_5[i], sz_20[i], sz[i], a)
    
    # 统计状态分布
    log('\n[3] 状态分布统计:')
    from collections import Counter
    cnt = Counter(s for s in states if s)
    total = sum(cnt.values())
    for state, count in cnt.most_common():
        pct = count / total * 100
        desc = PARAMS.get(state, {}).get('desc', '')
        log(f'  {state:<16} {count:>6,}天 ({pct:>5.1f}%)  {desc}')
    
    # 打印关键拐点和转换
    banner('关键拐点 — 状态切换记录')
    log(f'  {"Date":<12} {"State":<16} {"sz":>7} {"sz_5":>7} {"sz_20":>7} {"accel":>7} {"信号":>50}')
    log(f'  {"─"*100}')
    
    windows = [
        ('2022年熊市', date(2022,1,1), date(2022,12,31)),
        ('2024年1月V底→924', date(2024,1,1), date(2025,1,1)),
        ('2025年见顶', date(2025,1,1), date(2025,12,31)),
        ('2026年当前', date(2026,1,1), date(2026,5,27)),
    ]
    
    for title, start, end in windows:
        log(f'\n  ═══ {title} ═══')
        log(f'  {"Date":<12} {"State":<16} {"sz":>7} {"sz_5":>7} {"sz_20":>7} {"accel":>7} 信号')
        log(f'  {"─"*85}')
        
        prev_s = ''
        for i in range(n):
            d = dates[i]
            if d < start or d > end: continue
            if np.isnan(sz[i]) or np.isnan(sz_5[i]) or np.isnan(sz_20[i]): continue
            
            s = states[i] if states[i] else ''
            changed = (s != prev_s and prev_s != '')
            
            # 只在状态切换或极端sz时打印
            sig_parts = []
            if sz[i] > 2.0: sig_parts.append('🔥过热')
            if sz[i] < -2.0: sig_parts.append('❄极空')
            if sz[i] > 1.5 and sz[i] < 2.0 and not np.isnan(accel[i]) and accel[i] < -0.5: sig_parts.append('⚠冲顶预警')
            if not np.isnan(accel[i]):
                if accel[i] > 0.5 and sz[i] < -1.0: sig_parts.append('⬆底背离')
                if accel[i] < -0.5 and sz[i] > 1.0: sig_parts.append('⬇顶背离')
            
            flag = changed or abs(sz[i]) > 2.0 or (abs(accel[i]) > 0.5 if not np.isnan(accel[i]) else False)
            
            if flag:
                joint = '→' if changed else '  '
                sig_str = ' | '.join(sig_parts) if sig_parts else ''
                a_str = f'{accel[i]:+7.3f}' if not np.isnan(accel[i]) else '   NaN'
                log(f'  {d:%Y-%m-%d} {joint} {s:<16} {sz[i]:>+7.3f} {sz_5[i]:>+7.3f} {sz_20[i]:>+7.3f} {a_str}  {sig_str}')
            
            if changed:
                prev_s = s
    
    # 按时间段聚合展示
    banner('V8参数矩阵 vs 实际市场状态')
    log(f'  {"日期段":<20} {"状态":<16} {"Phase":<30} {"SMA阈值":<20} {"PB条件":<15}')
    log(f'  {"─"*100}')
    
    # 展示每个状态持续期间的参数
    prev_s = ''
    state_start = None
    window_sigs = []
    
    for i in range(n):
        d = dates[i]
        if d < date(2020,1,1): continue
        if np.isnan(sz[i]) or np.isnan(sz_5[i]) or np.isnan(sz_20[i]): continue
        
        s = states[i] if states[i] else ''
        if s != prev_s and prev_s:
            # 状态切换输出
            params = PARAMS.get(prev_s, {})
            th = params.get('thresholds', {})
            th_str = []
            for ph in ['A_RECOVER', 'A_ACCUM', 'NEUTRAL', 'B_ACCUM', 'B_DISTRIB']:
                v = th.get(ph)
                if v is None:
                    th_str.append(f'{ph[:5]}=关闭')
                else:
                    th_str.append(f'{ph[:5]}<-{abs(v*100):.0f}%')
            
            pb_str = params.get('pb_condition', '')
            extra = params.get('pb_hardcore', {})
            if extra:
                pb_str += f' ({extra})'
            
            # 计算持续时间
            dur_days = (d - state_start).days if state_start else 0
            
            th_str = ' '.join(th_str)
            log(f'  {state_start:%Y-%m-%d}~{d:%Y-%m-%d} ({dur_days}d)  {prev_s:<16} {th_str:<50} {pb_str:<20}')
        
        if s and s != prev_s:
            state_start = d
        prev_s = s
    
    # 最后一段
    if prev_s and state_start:
        last_d = dates[n-1]
        dur = (last_d - state_start).days
        params = PARAMS.get(prev_s, {})
        th = params.get('thresholds', {})
        th_str = ' '.join([f'{ph[:5]}<-{abs(v*100):.0f}%' if v is not None else f'{ph[:5]}=关闭' for ph, v in sorted(th.items())])
        pb_str = params.get('pb_condition', '')
        log(f'  {state_start:%Y-%m-%d}~至今 ({dur}d)   {prev_s:<16} {th_str:<50} {pb_str:<20}')
    
    banner('完成!')
    log(f'  日志: {LOG}')

if __name__ == '__main__':
    main()
