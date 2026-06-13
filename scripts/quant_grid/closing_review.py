"""收盘复盘 - 识别当日机构策略集"""
import urllib.request, json, sys
from datetime import date

TODAY = date.today().strftime('%Y%m%d')
CODES = {'600273': '嘉化能源', '600635': '大众公用', '002073': '软控股份'}
POSITIONS = {'600273': {'shares': 200, 'cost': 7.2451}, '600635': {'shares': 300, 'cost': 5.1000}, '002073': {'shares': 250, 'cost': 5.7350}}

def prefix(code):
    return 'sh' if code[0] in '69' else 'sz'

def fetch_quote(code):
    url = f'https://qt.gtimg.cn/q={prefix(code)}{code}'
    req = urllib.request.Request(url); req.add_header('User-Agent','Mozilla/5.0')
    resp = urllib.request.urlopen(req, timeout=10)
    for line in resp.read().decode('gbk').split(';'):
        if '=' not in line or '"' not in line: continue
        vals = line.split('"')[1].split('~')
        if len(vals) < 50: continue
        return {'price': float(vals[3]), 'pct': float(vals[32]), 'high': float(vals[33]),
                'low': float(vals[34]), 'vol_ratio': float(vals[49]), 'amount': float(vals[37]),
                'amplitude': float(vals[43])}
    return None

def fetch_pts(code):
    url = f'http://ifzq.gtimg.cn/appstock/app/minute/query?code={prefix(code)}{code}'
    req = urllib.request.Request(url); req.add_header('User-Agent','Mozilla/5.0')
    resp = urllib.request.urlopen(req, timeout=10)
    d = json.loads(resp.read().decode('utf-8'))
    raw = d.get('data',{}).get(prefix(code)+code,{}).get('data',{})
    rows = raw.get('data', []) if isinstance(raw, dict) else raw
    pts = []
    for r in rows:
        parts = r.split()
        if len(parts) >= 4:
            pts.append((parts[0], float(parts[1]), int(parts[2]), float(parts[3])))
    return pts

def identify(pts, q):
    if not pts or len(pts) < 20: return ['数据不足']
    s = []
    open15 = pts[:15]
    open_chg = (open15[-1][1] - open15[0][1])/open15[0][1]*100
    if open_chg < -1.5:
        s.append('S1-开盘杀')
    elif abs(open_chg) < 0.5:
        s.append('S0-缩量横盘')
    mid = [p for p in pts if '1040' <= p[0] <= '1055']
    if mid:
        low_mid = min(p[1] for p in mid)
        low_all = min(p[1] for p in pts)
        if abs(low_mid - low_all) < 0.05:
            s.append('S2-时间窗口砸')
    aft = [p for p in pts if p[0] >= '1300']
    if len(aft) >= 5:
        chg = (aft[-1][1]-aft[0][1])/aft[0][1]*100
        if chg < -0.8: s.append('S3-午后回落')
        elif chg > 0.5: s.append('S4-尾盘拉升')
    if q and q.get('amplitude',0) > 4:
        s.append('S5-全面杀跌')
    return s

def main():
    print(f'{TODAY} 收盘复盘\n')
    for code, name in CODES.items():
        q = fetch_quote(code)
        pts = fetch_pts(code)
        strats = identify(pts, q)
        pos = POSITIONS.get(code)
        if pos and q:
            profit = (q['price'] - pos['cost']) * pos['shares'] * 100
            profit_pct = (q['price']/pos['cost']-1)*100
        else:
            profit = profit_pct = 0

        print(f'【{name}({code})】')
        print(f'  收盘: {q["price"]:.2f} ({q["pct"]:+.2f}%)  量比{q["vol_ratio"]:.1f}  振幅{q["amplitude"]:.1f}%')
        print(f'  盈亏: {profit:.0f}元 ({profit_pct:+.2f}%)')
        print(f'  策略: {", ".join(strats)}')

        if pts:
            low = min(pts, key=lambda p: p[1])
            high = max(pts, key=lambda p: p[1])
            print(f'  低点: {low[0]} @{low[1]:.2f}  高点: {high[0]} @{high[1]:.2f}')

            last5 = pts[-5:]
            lchg = (last5[-1][1]-last5[0][1])/last5[0][1]*100
            lvol = sum(p[2] for p in last5)//len(last5)
            avol = sum(p[2] for p in pts)//len(pts)
            print(f'  尾盘: {lchg:+.2f}%  均量{lvol//100:.0f}手(全天{avol//100:.0f})')
            msg = '⬆尾盘抢筹(S4)' if lchg > 0.3 else '⬇尾盘杀跌' if lchg < -0.3 else '➡尾盘平稳'
            print(f'  {msg}')
        print()

if __name__ == '__main__':
    main()
