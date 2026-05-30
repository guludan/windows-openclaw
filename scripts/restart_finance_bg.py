#!/usr/bin/env python3
"""
在screen中后台跑财务数据补拉。
防止session超时SIGKILL。
"""
import os, sys, subprocess, time, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("restart_finance")

SCRIPT = os.path.expanduser("~/.openclaw/workspace/scripts/fetch_financial_data.py")
LOGFILE = "/tmp/fetch_finance2.log"
PYTHON = os.path.expanduser("~/miniconda3/envs/tt/bin/python3")

# 先算还差多少
sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/scripts"))
import data_finance as df
conn = df._get_pg()
cur = conn.cursor()
cur.execute("SELECT count(DISTINCT symbol) FROM financial_data")
done = int(cur.fetchone()[0])
cur.execute("SELECT count(*) FROM stock_list")
total_count = int(cur.fetchone()[0])
cur.close()
conn.close()
remaining = max(0, total_count - done)
log.info(f"已完: {done} 只, 剩余: {remaining} 只 (总{total_count}只)")

if remaining > 0:
    screen_cmd = f"screen -dmS fetch_finance {PYTHON} {SCRIPT} 2>&1 | tee {LOGFILE}"
    subprocess.run(screen_cmd, shell=True, check=False)
    log.info(f"screen 已启动: fetch_finance")
else:
    log.info("财务数据已全，不需要再跑")
