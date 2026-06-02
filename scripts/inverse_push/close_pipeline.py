#!/usr/bin/env python3
"""
收盘自动管线 — Inverse Push 每日收盘后执行
每天 15:30 之后跑一次，完成：

1. 更新日线 (update_daily_prices)
2. 更新行业日线 (akshare_tuhs)
3. 大盘R增量
4. 板块S_mom增量
5. 板块动量信号
6. CSI 龙虎榜增量（v5）
7. CSI 个股资金流增量（备选）
8. 北向资金增量
9. 生成综合简报 + 写入PG

用法：
  python3 close_pipeline.py                 # 完整跑一轮
  python3 close_pipeline.py --skip-data     # 跳过数据更新（只算信号）
  python3 close_pipeline.py --steps 3,4,5   # 只跑指定步骤
"""
import os, sys, json, argparse, time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import data_finance as df

# 各模块路径
BASE = os.path.dirname(__file__)
R_INDEX = os.path.join(BASE, 'r_index_v3.py')
SECTOR_MOM = os.path.join(BASE, 'sector_momentum.py')
CAMP_CLS = os.path.join(BASE, 'camp_classifier.py')
CSI_LHB = os.path.join(BASE, 'csi_lhb_v5.py')
DAILY_SIG = os.path.join(BASE, 'daily_signals.py')
CSI_FLOW = os.path.join(BASE, 'csi_fund_flow.py')
UPDATE_DP = os.path.join(os.path.dirname(BASE), 'update_daily_prices.py')
UPDATE_ID = os.path.join(os.path.dirname(BASE), 'fetch_industry_daily.py')


def step_name(n):
    names = {
        1: '📦 更新日线',
        2: '📊 更新行业日线 (同花顺)',
        3: '📈 大盘R增量',
        4: '🔷 板块S_mom增量',
        5: '🔶 板块动量信号',
        6: '📡 CSI 龙虎榜增量 (v5)',
        7: '💰 CSI 个股资金流 (备选)',
        8: '💰 北向资金增量',
        9: '📋 生成综合简报',
    }
    return names.get(n, f'步骤{n}')


def run(cmd, step_label, timeout=120):
    """执行一个步骤"""
    now = datetime.now().strftime('%H:%M:%S')
    print(f"\n[{now}] ⏳ {step_label}")
    sys.stdout.flush()
    
    ret = os.system(cmd)
    
    status = '✅' if ret == 0 else '❌'
    now = datetime.now().strftime('%H:%M:%S')
    print(f"[{now}] {status} {step_label} (exit={ret})")
    sys.stdout.flush()
    return ret == 0


def run_python(script, args='', step_label='', timeout=120):
    py = os.path.expanduser('~/miniconda3/envs/tt/bin/python3')
    return run(f'{py} {script} {args}', step_label, timeout)


def run_camp_classifier(mode):
    py = os.path.expanduser('~/miniconda3/envs/tt/bin/python3')
    return run(f'{py} {CAMP_CLS} --mode {mode}', f'⚔️ 龙虎榜阵营分歧 ({mode})')


def run_northbound():
    """北向资金增量"""
    py = os.path.expanduser('~/miniconda3/envs/tt/bin/python3')
    cmd = f'{py} -c "import sys; sys.path.insert(0,\'scripts\'); from inverse_push.eastmoney_data import update_northbound_daily; update_northbound_daily()"'
    return run(cmd, '💰 北向资金增量')


def run_brief():
    """生成综合简报"""
    py = os.path.expanduser('~/miniconda3/envs/tt/bin/python3')
    return run(f'{py} {DAILY_SIG} --save --write-db', '📋 生成综合简报 + 保存 + 写入PG')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-data', action='store_true', help='跳过数据更新')
    parser.add_argument('--steps', type=str, help='指定步骤: 1-9')
    parser.add_argument('--no-brief', action='store_true', help='跳过简报生成')
    parser.add_argument('--date', type=str, help='指定日期 (YYYY-MM-DD)')
    args = parser.parse_args()
    
    if args.steps:
        steps = [int(s.strip()) for s in args.steps.split(',')]
    elif args.skip_data:
        steps = [3, 4, 5, 6, 7, 8, 9]
    else:
        steps = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    
    today = args.date or date.today().strftime('%Y-%m-%d')
    today_dt = date.fromisoformat(today)
    
    wd = today_dt.weekday()
    if wd >= 5:
        print(f"⚠️ {today} 是休息日，跳过")
        return
    
    print(f"{'='*60}")
    print(f"🚀 收盘自动管线 — {today}")
    print(f"   步骤: {', '.join(step_name(s) for s in steps)}")
    print(f"{'='*60}\n")
    sys.stdout.flush()
    
    all_ok = True
    
    for s in steps:
        ok = True
        label = step_name(s)
        
        if s == 1:
            ok = run_python(UPDATE_DP, '', label)
            time.sleep(3)
        elif s == 2:
            ok = run_python(UPDATE_ID, '', label)
            time.sleep(3)
        elif s == 3:
            ok = run_python(R_INDEX, '--mode market', label)
        elif s == 4:
            ok = run_python(SECTOR_MOM, '--mode incremental', label)
            time.sleep(2)
        elif s == 5:
            ok = run_python(SECTOR_MOM, '--mode signal', label)
        elif s == 6:
            ok = run_python(CSI_LHB, '--mode incremental', label)
            time.sleep(2)
        elif s == 7:
            ok = run_python(CSI_FLOW, '--mode incremental', label)
            time.sleep(2)
        elif s == 8:
            ok = run_northbound()
            time.sleep(2)
        elif s == 9:
            ok = run_brief() if not args.no_brief else True
            if ok:
                print(f"  ✅ 已写入 PG (market_signals + csi_lhb_* + northbound_daily)")
        
        if not ok:
            print(f"  ⚠️ {label} 失败")
            all_ok = False
    
    print(f"\n{'='*60}")
    if all_ok:
        print(f"🎉 收盘管线完成 — {today}")
    else:
        print(f"⚠️ 收盘管线完成（有失败）— {today}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
