"""
pool_signal_update.py — 对 pool_energy 中已有占位记录进行 S1/S2/S3 信号检测并更新 extra.sigs
只跑今日（或指定日期），不删老数据。
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, psycopg2.extras, json, time, argparse
import numpy as np
from datetime import date, timedelta
from collections import defaultdict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default='2026-05-27')
    args = parser.parse_args()
    target = date.fromisoformat(args.date)
    tgt_str = target.isoformat()
    t0 = time.time()
    
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    print(f"pool_energy 信号检测 — {target}")
    
    # 1) 取所有有 pool_energy 占位的股票
    cur.execute("""
        SELECT DISTINCT level FROM pool_energy 
        WHERE level LIKE 'code:A.%' AND date='%s'
    """ % tgt_str)
    levels = [r[0] for r in cur.fetchall()]
    print(f"  有占位的股票: {len(levels)} 只")
    
    # 2) 拉这些股票的日线（close + volume/换手前 40 天）
    codes = [l.replace('code:A.','') for l in levels]
    if not codes:
        print("  无占位数据，退出")
        conn.close()
        return
    
    # 分批查日线
    B = 200; all_px = []
    for i in range(0, len(codes), B):
        cset = "','".join(codes[i:i+B])
        cur.execute(f"""
            SELECT SUBSTRING(symbol,3), trade_date, close, turnover
            FROM daily_prices
            WHERE SUBSTRING(symbol,3) IN ('{cset}')
              AND trade_date>='%s' AND trade_date<='%s'" % ((target - timedelta(days=40)).isoformat(), tgt_str)
              AND close>0
            ORDER BY 1, 2
        """)
        all_px.extend(cur.fetchall())
    print(f"  日线: {len(all_px)} 行 ({time.time()-t0:.0f}s)")
    
    # 构建 per-stock 的时间序列
    px = defaultdict(list)
    for c6, dt, cl, to in all_px: px[c6].append((dt, cl, to or 0))
    
    # 3) 大盘数据
    cur.execute("SELECT date, b_pool, a_pool, phase FROM pool_energy WHERE level='market' ORDER BY date")
    mkt = {r[0]: {'b': r[1], 'a': r[2], 'phase': r[3] or 'NEUTRAL'} for r in cur.fetchall()}
    
    # 4) 逐只检测信号
    updated = 0; stats = {'s1_surge':0,'s2_pulse':0,'s3_panic':0}
    
    for c6 in codes:
        pl = px.get(c6, [])
        if len(pl) < 25: continue
        dl = [p[0] for p in pl]
        
        try: idx = dl.index(target)
        except ValueError: continue
        if idx < 5: continue
        
        c0 = pl[idx][1]
        # 20日换手率统计
        to_20 = [pl[idx-k][2] for k in range(1,21) if idx-k>=0 and pl[idx-k][2] > 0]
        to0 = pl[idx][2]
        v_z = 0.0
        if to0 and len(to_20) >= 5:
            mu, sd = np.mean(to_20), np.std(to_20)
            v_z = (to0 - mu) / sd if sd > 0 else 0.0
        
        # 20日收益波动率
        ret_20 = []
        for k in range(1,21):
            if idx-k >= 0 and pl[idx-k][1] and pl[idx-k-1][1]:
                ret_20.append(pl[idx-k][1]/pl[idx-k-1][1]-1)
        vol_20d = np.std(ret_20) if len(ret_20) >= 10 else 0.01
        
        # vol_z: 最近5日平均波动 / vol_20d
        ret_5d_abs = []
        for k in range(5):
            if idx-k >= 0 and pl[idx-k][1] and pl[idx-k-1][1]:
                ret_5d_abs.append(abs(pl[idx-k][1]/pl[idx-k-1][1]-1))
        vol_5d = np.mean(ret_5d_abs) if ret_5d_abs else 0.01
        vol_z = vol_5d / vol_20d if vol_20d > 0 else 0
        
        # 今日收益
        c1 = pl[idx-1][1]
        ret = (c0 / c1 - 1) if c1 else 0.0
        
        # S2 / S3 / S1 判定
        sigs = 'no_signal'
        sig_types = []
        
        if vol_z > 2.5 and v_z > 2.0 and ret > 0.05:
            sig_types.append('s2_pulse')
        if vol_z > 2.5 and v_z > 2.0 and ret < -0.05:
            sig_types.append('s3_panic')
        if vol_z > 3.0 and abs(v_z) < 1.5:
            sig_types.append('s1_surge')
        
        if sig_types:
            sigs = '/'.join(sig_types)
            m = mkt.get(target, {'b':0,'a':0,'phase':'NEUTRAL'})
            extra = json.dumps({
                'sigs': sigs,
                'vol_z': round(float(vol_z), 2),
                'v_z': round(float(v_z), 2),
                'ret': round(float(ret), 4),
                'b_in': round(float(m['b']), 4),
                'a_in': round(float(m['a']), 4),
                'b_mkt': round(float(m['b']), 4),
                'a_mkt': round(float(m['a']), 4),
                'total_pool': round(float(m['b']+m['a']), 4),
            })
            
            cur.execute("""
                UPDATE pool_energy SET
                    b_inject=%s, a_inject=%s,
                    phase=%s, extra=%s
                WHERE level='%s' AND date='%s'
            """ % (m['b'], m['a'], m['phase'], extra, 'code:A.'+c6, tgt_str))
            
            for st in sig_types: stats[st] = stats.get(st, 0) + 1
            updated += 1
        
        if updated % 100 == 0 and updated > 0:
            print(f"  已更新: {updated} ({time.time()-t0:.1f}s)", flush=True)
    
    conn.commit()
    print(f"\n完成 ({time.time()-t0:.0f}s)")
    print(f"  检测股票: {len(codes)}")
    print(f"  信号更新: {updated}")
    for k,v in stats.items(): print(f"    {k}: {v}")
    conn.close()

if __name__ == '__main__':
    main()
