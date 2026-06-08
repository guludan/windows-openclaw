#!/usr/bin/env python3
"""
分时缓存脚本 — 每分钟拉取量化T+0派系+持仓的分时数据
用法: python3 cache_intraday.py [--start HH:MM]
运行时段: 早盘 09:30~11:30, 午盘 13:00~15:00
"""

import psycopg2
import requests
import re
import time
import sys
from datetime import datetime, date

DB = "dbname=guludan user=guludan password=toto host=192.168.3.14"

# 监控标的: 量化T+0派系 + 持仓 + 疑似
WATCH_LIST = {
    'sh600635': ('A.600635', '大众公用'),
    'sz300284': ('A.300284', '苏交科'),
    'sh600622': ('A.600622', '光大嘉宝'),
    'sh600333': ('A.600333', '长春燃气'),
    'sh603617': ('A.603617', '君禾股份'),
    'sh601579': ('A.601579', '会稽山'),
    'sh600273': ('A.600273', '嘉化能源'),
    'sz002073': ('A.002073', '软控股份'),
    'sz000850': ('A.000850', '华茂股份'),
}

TODAY = date.today()

def fetch_minute_data(code_qt):
    """从腾讯API拉分时数据"""
    url = f"https://qt.gtimg.cn/q={code_qt}"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
        m = re.search(r'=\"([^\"]*)\"', r.text)
        if not m: return None
        pp = m.group(1).split('~')
        return {
            'price': float(pp[3]) if pp[3] else None,
            'high': float(pp[33]) if pp[33] else None,
            'low': float(pp[34]) if pp[34] else None,
            'volume': float(pp[36]) if pp[36] else 0,  # 累计成交量(手)
            'prev_close': float(pp[4]) if pp[4] else None,
            'name': pp[1],
        }
    except Exception as e:
        return None

def cache_batch():
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    now = datetime.now()
    time_slot = now.strftime('%H:%M')
    
    # 批量获取所有标的分时
    codes = '|'.join(WATCH_LIST.keys())
    
    # 逐个获取更可靠
    count = 0
    for qt_code, (symbol, name) in WATCH_LIST.items():
        data = fetch_minute_data(qt_code)
        if data and data['price']:
            try:
                cur.execute("""
                    INSERT INTO intraday_cache (symbol, date, time_slot, price, volume)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, date, time_slot) DO UPDATE SET
                        price = EXCLUDED.price,
                        volume = EXCLUDED.volume
                """, (symbol, TODAY, time_slot, data['price'], data['volume']))
                count += 1
            except Exception as e:
                print(f"  DB error {name}: {e}")
    
    conn.commit()
    cur.close()
    conn.close()
    return count

def main():
    start_time = sys.argv[1] if len(sys.argv) > 1 else None
    
    print(f"📊 分时缓存启动 | {TODAY}")
    if start_time:
        print(f"   指定开始时间: {start_time}")
    
    while True:
        now = datetime.now()
        h, m_val = now.hour, now.minute
        
        # 允许运行时段
        in_morning = (h == 9 and m_val >= 30) or (h == 10) or (h == 11 and m_val <= 30)
        in_afternoon = (h == 13) or (h == 14) or (h == 15 and m_val == 0)
        
        if not (in_morning or in_afternoon):
            if now.second < 5:
                print(f"  非交易时段, 等待... ({now.strftime('%H:%M')})")
            time.sleep(30)
            continue
        
        # 每分钟的第30秒拉数据(给分时数据足够时间刷新)
        if m_val % 1 == 0 and now.second > 20 and now.second < 40:
            count = cache_batch()
            ts = now.strftime('%H:%M:%S')
            print(f"  [{ts}] 缓存 {count}/{len(WATCH_LIST)} 只")
            time.sleep(5)  # 避免重复拉
        
        time.sleep(1)

if __name__ == '__main__':
    main()
