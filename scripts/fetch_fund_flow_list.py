#!/usr/bin/env python3
"""
按股票列表补资金流（东财 push2his fflow/daykline，curl + 1.5s 间隔）
用法: python3 fetch_fund_flow_list.py /tmp/candidates.txt
     文件每行一个纯6位代码；也接受 A.xxxxxx
红线：外部API间隔>=1.5s，每300只歇20s（遵循 9/21 东财封禁教训）
"""
import subprocess, sys, time, json, os
import psycopg2, psycopg2.extras as pge

PG = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
SLEEP = 1.5
for k in list(os.environ.keys()):
    if 'proxy' in k.lower():
        os.environ.pop(k, None)

def secid(code):
    if code.startswith('6'): return '1.' + code
    if code.startswith(('0','3')): return '0.' + code
    if code.startswith(('8','4')): return '0.' + code
    return '1.' + code

def fetch(code):
    url = ("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
           "?lmt=0&klt=101&secid=" + secid(code) +
           "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63")
    try:
        out = subprocess.run(["curl","-s","--max-time","15",url],
                             capture_output=True, text=True, timeout=20).stdout
        j = json.loads(out)
        klines = (j.get("data") or {}).get("klines") or []
        recs = []
        for line in klines:
            p = line.split(",")
            # f51日期 f52主力 f53小单 f54中单 f55大单 f56超大 f62收盘 f63涨跌幅
            recs.append((code, p[0], float(p[1]), float(p[2]), float(p[3]),
                         float(p[4]), float(p[5]), float(p[6]),
                         float(p[7]), float(p[8]), float(p[9]), float(p[10]),
                         float(p[11]), float(p[12])))
        return recs
    except Exception as e:
        return None

def main():
    codes = []
    with open(sys.argv[1]) as f:
        for ln in f:
            c = ln.strip().replace('A.','')
            if c: codes.append(c)
    codes = sorted(set(codes))
    print(f"待补资金流: {len(codes)} 只")
    conn = psycopg2.connect(PG); cur = conn.cursor()
    ok = fail = 0
    for i, code in enumerate(codes):
        recs = fetch(code)
        if recs:
            pge.execute_values(cur, """
                INSERT INTO fund_flow (code,trade_date,main_net,small_net,mid_net,big_net,super_net,
                    main_ratio,small_ratio,mid_ratio,big_ratio,super_ratio,close,chg_pct)
                VALUES %s
                ON CONFLICT (code,trade_date) DO UPDATE SET
                    main_net=EXCLUDED.main_net, small_net=EXCLUDED.small_net, mid_net=EXCLUDED.mid_net,
                    big_net=EXCLUDED.big_net, super_net=EXCLUDED.super_net,
                    main_ratio=EXCLUDED.main_ratio, small_ratio=EXCLUDED.small_ratio,
                    mid_ratio=EXCLUDED.mid_ratio, big_ratio=EXCLUDED.big_ratio,
                    super_ratio=EXCLUDED.super_ratio, close=EXCLUDED.close, chg_pct=EXCLUDED.chg_pct
            """, recs, page_size=500)
            conn.commit(); ok += 1
        else:
            fail += 1
        if (i+1) % 50 == 0:
            print(f"  [{i+1}/{len(codes)}] ok={ok} fail={fail}")
        time.sleep(SLEEP)
        if (i+1) % 300 == 0:
            time.sleep(20)
    print(f"✅ 完成 ok={ok} fail={fail}")
    conn.close()

if __name__ == "__main__":
    main()
