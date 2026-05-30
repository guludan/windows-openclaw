#!/usr/bin/env python3
# preflight-ok: true
"""
pool_regime_integration.py — 动态牛熊检测集成

功能：
1. 从pool_energy读取全量surplus序列
2. 计算surplus_z (60日滚动z-score)、sz_5 (5日均线)、sz_20 (20日均线)、加速度
3. V8状态机 → 每日regime
4. 写回pool_energy.extra（surplus_z族 + regime）
5. 写入market_signals（regime_signal每日状态）

这样V8Strategy可以直接从DB读取regime，不用每次都重新计算。
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, psycopg2.extras
import numpy as np
import json
from datetime import datetime

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

# ===== V8 状态机（内联，避免循环依赖）=====
REGIME_ALIAS = {
    'BULL': 'BULL', 'TOP_WARNING': 'TOP_WARNING',
    'SIDEWAYS': 'SIDEWAYS', 'BOTTOM_WARNING': 'BOTTOM_WARNING',
    'BEAR': 'BEAR',
}

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
        'BULL': lambda sz5, sz20, sz, acc: sz5 > sz20 and sz > 0,
        'BEAR': lambda sz5, sz20, sz, acc: sz5 < sz20 and sz < -0.5,
        'SIDEWAYS': lambda sz5, sz20, sz, acc: sz5 < sz20 and -0.5 < sz < 1.5 and acc > -0.3,
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
        'BULL': lambda sz5, sz20, sz, acc: sz5 > sz20 and sz > -0.5,
        'BEAR': lambda sz5, sz20, sz, acc: sz5 < sz20 and sz < -1.5,
        'SIDEWAYS': lambda sz5, sz20, sz, acc: sz5 > sz20 and -1.5 < sz < -0.5,
        'default': 'BOTTOM_WARNING',
    },
    'BEAR': {
        'BOTTOM_WARNING': lambda sz5, sz20, sz, acc: sz5 > sz20 and sz < -0.5,
        'SIDEWAYS': lambda sz5, sz20, sz, acc: sz5 > sz20 and -0.5 < sz < 0.5,
        'default': 'BEAR',
    },
}

def resolve_regime(current, sz5, sz20, sz, acc):
    trans = TRANSITIONS.get(current, TRANSITIONS['INIT'])
    for target, cond in trans.items():
        if target == 'default':
            continue
        try:
            if cond(sz5, sz20, sz, acc):
                return REGIME_ALIAS.get(target, target)
        except:
            continue
    return REGIME_ALIAS.get(trans.get('default', 'SIDEWAYS'), 'SIDEWAYS')


def run():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 动态牛熊检测 — 开始")
    conn = psycopg2.connect(DB)
    cur = conn.cursor()

    # 1) 加载大盘surplus序列
    print("  加载surplus序列...")
    cur.execute("SELECT date, surplus, phase FROM pool_energy WHERE level='market' ORDER BY date")
    rows = cur.fetchall()
    dates = [r[0] for r in rows]
    surplus = np.array([float(r[1] or 0) for r in rows], dtype=float)
    phases = [r[2] for r in rows]
    n = len(surplus)
    print(f"  {n}天 ({dates[0]} ~ {dates[-1]})")

    # 2) 60日滚动z-score
    print("  计算surplus_z...")
    sz = np.full(n, np.nan)
    for i in range(59, n):
        seg = surplus[i-59:i+1]
        mu, sd = np.mean(seg), np.std(seg)
        if sd > 0:
            sz[i] = (surplus[i] - mu) / sd

    # 3) sz_5, sz_20 滑动平均
    def sma(arr, w):
        res = np.full(n, np.nan)
        for i in range(len(arr)):
            if i < w-1 or np.isnan(arr[i]):
                continue
            seg = arr[i-w+1:i+1]
            if np.any(np.isnan(seg)):
                continue
            res[i] = np.mean(seg)
        return res

    sz_5 = sma(sz, 5)
    sz_20 = sma(sz, 20)

    # 4) 加速度 (二阶差分)
    accel = np.full(n, np.nan)
    for i in range(2, n):
        if not np.isnan(sz[i]) and not np.isnan(sz[i-1]) and not np.isnan(sz[i-2]):
            accel[i] = sz[i] - 2*sz[i-1] + sz[i-2]

    # 5) 状态机
    print("  状态机推算...")
    regime_map = {}
    current = 'INIT'

    # 找第一个有效数据点
    first_valid = None
    for i in range(n):
        if not np.isnan(sz_5[i]) and not np.isnan(sz_20[i]):
            first_valid = i
            regime_map[dates[i]] = 'SIDEWAYS'
            current = 'SIDEWAYS'
            break

    if first_valid is None:
        print("  ERROR: 无有效数据")
        return

    for i in range(first_valid + 1, n):
        d = dates[i]
        if np.isnan(sz_5[i]) or np.isnan(sz_20[i]) or np.isnan(sz[i]):
            regime_map[d] = current
            continue
        acc = 0.0 if np.isnan(accel[i]) else accel[i]
        current = resolve_regime(current, sz_5[i], sz_20[i], sz[i], acc)
        regime_map[d] = current

    print(f"  状态分布:")
    dist = {}
    for r in regime_map.values():
        dist[r] = dist.get(r, 0) + 1
    for k, v in sorted(dist.items(), key=lambda x: -x[1]):
        print(f"    {k}: {v}天 ({v/len(regime_map)*100:.1f}%)")

    # 6) 写回pool_energy.extra
    print("  写回pool_energy.extra...")
    updated = 0
    for i in range(n):
        d = dates[i]
        regime = regime_map.get(d, 'SIDEWAYS')

        extra = {
            'advance_ratio': round(float(surplus[i] if not np.isnan(surplus[i]) else 0), 4),
            'surplus_z': round(float(sz[i]), 4) if not np.isnan(sz[i]) else None,
            'sz_5': round(float(sz_5[i]), 4) if not np.isnan(sz_5[i]) else None,
            'sz_20': round(float(sz_20[i]), 4) if not np.isnan(sz_20[i]) else None,
            'accel': round(float(accel[i]), 4) if not np.isnan(accel[i]) else None,
            'regime': regime,
        }
        # 移除None值
        extra = {k: v for k, v in extra.items() if v is not None}

        cur.execute(
            """UPDATE pool_energy SET extra = %s::jsonb, updated_at = NOW()
               WHERE level='market' AND date=%s""",
            (json.dumps(extra), d)
        )
        if cur.rowcount > 0:
            updated += 1

        if i % 500 == 0 and i > 0:
            conn.commit()
            print(f"    {i}/{n}...")

    conn.commit()
    print(f"  已更新 {updated}/{n} 条")

    # 7) 写入market_signals
    print("  写入market_signals(regime_signal)...")
    REGIME_CODES = {
        'BULL': 1, 'TOP_WARNING': 2, 'SIDEWAYS': 3,
        'BOTTOM_WARNING': 4, 'BEAR': 5,
    }
    batch_regime = []
    for d, regime in regime_map.items():
        code = REGIME_CODES.get(regime, 3)
        batch_regime.append((
            d, 'regime_signal', code,
            json.dumps({
                'regime': regime,
                'surplus_z': None,
                'phase': 'NEUTRAL',
            })
        ))

    total_ins = 0
    for i in range(0, len(batch_regime), 500):
        chunk = batch_regime[i:i+500]
        try:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO market_signals (trade_date, signal_type, value, extra)
                   VALUES %s""",
                chunk,
                template='(%s::date, %s, %s, %s::jsonb)'
            )
            total_ins += cur.rowcount
        except Exception as e:
            print(f"    insert error at batch {i}: {e}")
            conn.rollback()
        conn.commit()

    print(f"  写入 {total_ins} 条regime_signal")

    # 8) 展示近期
    print(f"\n  近期regime + surplus_z ({dates[-1]} ~ {dates[-10] if n>=10 else dates[0]}):")
    print(f"  {'日期':<12} {'surplus_z':<10} {'sz_5':<8} {'sz_20':<8} {'accel':<8} {'regime':<16} {'phase':<14}")
    start = max(0, n-30)
    for i in range(start, n):
        d = dates[i]
        r = regime_map.get(d, '')
        p = phases[i] if i < len(phases) else ''
        sz_i = f"{sz[i]:+.3f}" if not np.isnan(sz[i]) else "NaN"
        s5_i = f"{sz_5[i]:+.3f}" if not np.isnan(sz_5[i]) else ""
        s20_i = f"{sz_20[i]:+.3f}" if not np.isnan(sz_20[i]) else ""
        acc_i = f"{accel[i]:+.3f}" if not np.isnan(accel[i]) else ""
        print(f"  {d}  {sz_i:<10} {s5_i:<8} {s20_i:<8} {acc_i:<8} {r:<16} {p:<14}")

    cur.close()
    conn.close()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 完成")
    return len(regime_map)


if __name__ == '__main__':
    run()
