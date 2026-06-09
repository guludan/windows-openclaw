#!/usr/bin/env python3
"""
V9 交易主控制器 (全自动管线)
5000本金, 20%亏损上限=1000, 换取经验和信息流

用法:
  python3 scripts/trade_master.py daily   → 每日全管线
  python3 scripts/trade_master.py status  → 当前状态简报
"""

import sys, json, subprocess, os, psycopg2
from datetime import datetime

DB = "dbname=guludan user=guludan password=toto host=192.168.3.14"

def log(msg, fname=None):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    d = f'/mnt/d/data/a-stock/logs'
    os.makedirs(d, exist_ok=True)
    with open(f'{d}/trade_master.log', 'a') as f:
        f.write(line + '\n')
    if fname:
        with open(f'{d}/{fname}', 'a') as f:
            f.write(line + '\n')

def run_step(step_num, desc, cmd):
    """运行一个管线步骤,返回成功/失败"""
    log(f"  [{step_num}] {desc}...")
    print(f"    $ {cmd[:80]}...")
    start = datetime.now()
    ret = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
    elapsed = (datetime.now() - start).total_seconds()
    
    if ret.returncode == 0:
        log(f"  ✅ {desc} ({elapsed:.0f}s)")
        return True
    else:
        log(f"  ❌ {desc} — {ret.stderr[:200]} ({elapsed:.0f}s)", "errors.log")
        return False

def daily_pipeline():
    log("=" * 60, "daily_run.log")
    log("V9 每日全自动管线启动", "daily_run.log")
    
    steps = [
        # 数据层
        (1, "mootdx日线同步", "~/miniconda3/envs/tt/bin/python3 ~/.openclaw/workspace/scripts/update_daily_mootdx.py"),
        (2, "ETF/宏观/龙虎榜", "~/miniconda3/envs/tt/bin/python3 ~/.openclaw/workspace/scripts/data_finance.py --update-daily"),
        (3, "涨停数据", "~/miniconda3/envs/tt/bin/python3 ~/.openclaw/workspace/scripts/backfill_limit_up.py"),
        
        # V6引擎
        (4, "相空间[B]/[A]递推", "~/miniconda3/envs/tt/bin/python3 ~/.openclaw/workspace/scripts/pool_engine_v1.py"),
        (5, "动态牛熊检测", "~/miniconda3/envs/tt/bin/python3 ~/.openclaw/workspace/scripts/pool_regime_integration.py"),
        (6, "股票能量增量", "~/miniconda3/envs/tt/bin/python3 ~/.openclaw/workspace/scripts/stock_energy_incr.py"),
        (7, "董监高持股变动", "PYTHONUNBUFFERED=1 ~/miniconda3/envs/tt/bin/python3 ~/.openclaw/workspace/scripts/fetch_exec_hold_daily.py"),
        
        # V9四层决策
        (8, "V9四层决策框架", "~/miniconda3/envs/tt/bin/python3 ~/.openclaw/workspace/scripts/v9_framework.py run"),
        
        # 收盘报告
        (9, "统一收盘报告", "~/miniconda3/envs/tt/bin/python3 ~/.openclaw/workspace/scripts/closing_report.py"),
        
        # V9决策写入数据库
        (10, "V9决策持久化", "~/miniconda3/envs/tt/bin/python3 ~/.openclaw/workspace/scripts/v9_framework.py run"),
    ]
    
    results = []
    for step_num, desc, cmd in steps:
        ok = run_step(step_num, desc, cmd)
        results.append((desc, ok))
    
    # 汇总
    ok_count = sum(1 for _, ok in results if ok)
    fail_count = sum(1 for _, ok in results if not ok)
    
    summary = f"""
{'='*60}
V9 每日管线完成 @ {datetime.now().strftime('%Y-%m-%d %H:%M')}
{'='*60}
✅ {ok_count}/{len(steps)} 成功  ❌ {fail_count} 失败
"""
    for desc, ok in results:
        summary += f"  {'✅' if ok else '❌'} {desc}\n"
    
    log(summary, "daily_run.log")
    return ok_count == len(steps)

def status_report():
    """当前大盘+持仓+V9状态简报"""
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    print("=" * 60)
    print(f"V9 交易状态 @ {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    # 资金
    print(f"\n💰 资金: 5000 (止损线4000, 可亏1000)")
    
    # 持仓盈亏
    cur.execute("""
        SELECT symbol, name, cost, current, shares, 
               (current-cost)*shares AS profit,
               (current-cost)/cost*100 AS pct
        FROM positions WHERE active=true
    """)
    positions = cur.fetchall()
    
    total_cost = 0
    total_market = 0
    if positions:
        print(f"\n{'名称':12} {'成本':>8} {'现价':>8} {'仓位':>6} {'盈亏':>8} {'%':>7}")
        print("-" * 55)
        for sym, name, cost, cur_p, shares, profit, pct in positions:
            cost = float(cost); cur_p = float(cur_p); shares = float(shares)
            pos_value = cur_p * shares
            total_cost += cost * shares
            total_market += pos_value
            print(f"{name:12} {cost:>7.3f} {cur_p:>7.3f} {shares:>5.0f}股 {profit:>+7.2f} {pct:>+6.2f}%")
        
        total_pnl = total_market - total_cost
        print("-" * 55)
        print(f"{'总计':12} {total_cost:>8.0f} {total_market:>8.0f} {'':6} {total_pnl:>+7.2f} {total_pnl/total_cost*100:>+6.2f}%")
        print(f"  仓位比例: {total_market/5000*100:.0f}%")
    else:
        print(f"  (空仓)")
    
    # 最新V9决策
    cur.execute("SELECT date, policy, sector, market_window, action, sz FROM v9_decisions ORDER BY date DESC LIMIT 1")
    r = cur.fetchone()
    if r:
        print(f"\n📋 最新V9决策 ({r[0]}):")
        print(f"  政策: {str(r[1])[:60]}")
        print(f"  板块: {str(r[2])[:40]}")
        print(f"  大盘: sz={r[5]}")
        print(f"  操作: {str(r[4])}")
    
    # 风险检查
    print(f"\n⚠️  风险监控:")
    remaining = 1000 - abs(total_pnl) if total_pnl < 0 else 1000
    print(f"  可用亏损额度: {remaining:.0f}/1000")
    if abs(total_pnl) > 500:
        print(f"  🔴 已使用超过50%亏损额度!")
    if abs(total_pnl) > 800:
        print(f"  🚨 已使用超过80%亏损额度,考虑减仓!")
    
    conn.close()

def record_trade(symbol, name, action, price, shares, reason):
    """记录一笔交易到数据库和经验文件"""
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    cur.execute("""
        INSERT INTO trade_log (date, symbol, name, action, price, shares, value, reason)
        VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s)
    """, (symbol, name, action, price, shares, round(price*shares, 2), reason))
    conn.commit()
    
    # 同时写入经验文件
    with open('/mnt/d/data/a-stock/logs/trade_experience.md', 'a') as f:
        ts = datetime.now().strftime('%Y-%m-%d %H:%M')
        f.write(f"\n## {ts} | {name}({symbol}) {action}\n")
        f.write(f"- 价格: {price} × {shares}股 = {price*shares:.0f}\n")
        f.write(f"- 原因: {reason}\n")
        f.write(f"- 总资金: 5000\n")
    
    log(f"交易记录: {symbol} {action} {price}×{shares} {reason}")
    conn.close()

def init_db():
    """初始化交易表"""
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            cost REAL,
            current REAL,
            shares REAL,
            active BOOLEAN DEFAULT true,
            entered DATE DEFAULT CURRENT_DATE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_log (
            id SERIAL PRIMARY KEY,
            date DATE DEFAULT CURRENT_DATE,
            symbol TEXT,
            name TEXT,
            action TEXT,
            price REAL,
            shares REAL,
            value REAL,
            reason TEXT
        )
    """)
    # 如果position表为空,初始化当前持仓
    cur.execute("SELECT COUNT(*) FROM positions WHERE active=true")
    if cur.fetchone()[0] == 0:
        positions_init = [
            ('600635', '大众公用', 4.93, 300),
            ('600273', '嘉化能源', 6.99, 200),
            ('002073', '软控股份', 5.56, 250),
            ('300284', '苏交科', 5.84, 250),
            ('000850', '华茂股份', 3.80, 200),
        ]
        for sym, name, cost, shares in positions_init:
            cur.execute("""
                INSERT INTO positions (symbol, name, cost, current, shares)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
            """, (sym, name, cost, cost, shares))
        print(f"📝 已初始化5只持仓, 总成本约{sum(c*s for _,_,c,s in positions_init):.0f}")
    
    conn.commit()
    conn.close()

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    
    if cmd == 'daily':
        init_db()
        daily_pipeline()
    elif cmd == 'status':
        init_db()
        status_report()
    elif cmd == 'init':
        init_db()
        print("✅ 初始化完成")
    elif cmd == 'trade':
        # trade_master.py trade 600635 大众公用 buy 4.90 300 "加仓"
        if len(sys.argv) >= 7:
            record_trade(sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5]), int(sys.argv[6]), sys.argv[7])
        else:
            print("用法: trade_master.py trade <symbol> <name> <buy/sell> <price> <shares> <reason>")
    else:
        print("用法: python3 scripts/trade_master.py [daily|status|init|trade]")
