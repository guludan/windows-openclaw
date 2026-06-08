#!/usr/bin/env python3
"""
盘中盯盘脚本 — 嘉化能源 + 大众公用
拉腾讯实时行情，判断盘口异常
"""
import urllib.request, re, json, sys, os
from datetime import datetime

TARGETS = ['sh600273', 'sh600635']
NAMES = {'sh600273': '嘉化能源', 'sh600635': '大众公用'}
THRESHOLDS = {
    'sh600273': {'name': '嘉化能源', 'buy_price': 7.10, 'stop_loss': 6.93},
    'sh600635': {'name': '大众公用', 'buy_price': 5.06, 'stop_loss': 4.85},
}

def fetch():
    url = "http://qt.gtimg.cn/q=" + ",".join(TARGETS)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    data = urllib.request.urlopen(req, timeout=5).read()
    return data.decode('gbk')

def parse(resp_text):
    results = {}
    for line in resp_text.strip().split('\n'):
        if not line.strip(): continue
        m = re.match(r'v_(\w+)="(.+)"', line.strip().rstrip(';'))
        if not m: continue
        code = m.group(1)
        f = m.group(2).split('~')
        if code not in TARGETS: continue
        
        price = float(f[3])
        close_y = float(f[4])
        open_ = float(f[5])
        high = float(f[33])
        low = float(f[34])
        volume = int(f[6])
        amount = float(f[37])  # 万
        chg = float(f[31])
        chgp = float(f[32])
        tm = f[30]
        
        # 盘口
        buy = [(float(f[9+i*2]), int(f[10+i*2]) if f[10+i*2] else 0) for i in range(5)]
        sell = [(float(f[19+i*2]), int(f[20+i*2]) if f[20+i*2] else 0) for i in range(5)]
        
        total_buy_vol = sum(v for _, v in buy)
        total_sell_vol = sum(v for _, v in sell)
        ratio = total_sell_vol / max(total_buy_vol, 1)
        
        results[code] = {
            'name': NAMES.get(code, f[1]),
            'price': price,
            'close_y': close_y,
            'open': open_,
            'high': high,
            'low': low,
            'volume': volume,
            'amount': amount / 1e4 if amount > 10000 else amount,  # 转亿
            'chg': chg,
            'chgp': chgp,
            'time': tm[8:14],
            'buy_vol': total_buy_vol,
            'sell_vol': total_sell_vol,
            'ratio': ratio,
            'top_bid_vol': buy[0][1],  # 买1量
            'top_ask_vol': sell[0][1],  # 卖1量
        }
    return results

def check_alarms(d):
    alerts = []
    # 止损
    for code, info in THRESHOLDS.items():
        if code not in d: continue
        p = d[code]['price']
        sl = info['stop_loss']
        bp = info['buy_price']
        name = info['name']
        
        if p <= sl:
            alerts.append(f"🔴 {name}跌破止损{sl}! 现价{p:.2f}")
        elif p <= bp * 0.98:
            alerts.append(f"⚠️ {name}接近止损{sL:.2f}, 现价{p:.2f}")
        
        # 涨跌幅异常
        if d[code]['chgp'] >= 5:
            alerts.append(f"📈 {name}大涨{d[code]['chgp']:.1f}%!")
        elif d[code]['chgp'] <= -5:
            alerts.append(f"📉 {name}大跌{d[code]['chgp']:.1f}%!")
        
        # 盘口异常
        if d[code]['ratio'] > 2:
            alerts.append(f"🔻 {name}抛压过重! 卖/买={d[code]['ratio']:.1f}x")
        
        # 放量异常（相对自身）
    return alerts

# 拉一次数据输出
d = parse(fetch())

for code, info in sorted(d.items()):
    n = info
    print(f"\n  {n['name']} | {n['time']}")
    print(f"  现价{n['price']:.2f} 昨收{n['close_y']:.2f} {n['chg']:+.2f}({n['chgp']:+.2f}%)")
    print(f"  高{n['high']:.2f} 低{n['low']:.2f} 量{n['volume']/1e4:.1f}万手 额{n['amount']:.2f}亿")
    print(f"  卖/买={n['ratio']:.2f}x 买1={n['top_bid_vol']:,}手 卖1={n['top_ask_vol']:,}手")
    
    # 盈亏
    th = THRESHOLDS.get(code)
    if th:
        pnl = (n['price'] / th['buy_price'] - 1) * 100
        print(f"  建仓{th['buy_price']} 盈亏{pnl:+.2f}% 止损{th['stop_loss']} 距止损{(n['price']/th['stop_loss']-1)*100:.1f}%")

# 保存到文件
outdir = '/home/tearsyu/.openclaw/workspace/memory/reports'
os.makedirs(outdir, exist_ok=True)
ts = datetime.now().strftime('%H%M')
outpath = os.path.join(outdir, f'intraday_{ts}.json')
with open(outpath, 'w') as f:
    json.dump({k: {kk: vv for kk, vv in v.items() if kk != 'name'} for k, v in d.items()}, f, default=str)
