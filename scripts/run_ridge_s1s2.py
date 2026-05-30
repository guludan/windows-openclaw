"""run_ridge_s1s2.py — S1/S2 岭回归验证（复用 S3 方法）"""
import numpy as np, pandas as pd, warnings, time, psycopg2, bisect, json
from collections import defaultdict
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score
warnings.filterwarnings("ignore")
t0 = time.time()

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
PHASES = ["B_ACCUM","B_DISTRIB","A_ACCUM","A_RECOVER","NEUTRAL","HI_EQ"]

def get_features_for_phase(phase_name):
    """为指定 phase 类型提取特征矩阵"""
    conn = psycopg2.connect(DB); cur = conn.cursor()
    
    # 取涉及股票
    cur.execute("""
        SELECT DISTINCT SUBSTRING(level, 8) as code6
        FROM pool_energy WHERE phase = %s AND date >= '2020-01-01'
    """, (phase_name,))
    codes = [r[0] for r in cur.fetchall()]
    
    # 分批取日线
    B = 200; all_px = []
    for i in range(0, len(codes), B):
        cset = "','".join(codes[i:i+B])
        cur.execute(f"""
            SELECT SUBSTRING(symbol,3), trade_date, close, turnover
            FROM daily_prices
            WHERE SUBSTRING(symbol,3) IN ('{cset}')
              AND trade_date>='2019-06-01' AND close>0
            ORDER BY 1,2
        """)
        all_px.extend(cur.fetchall())
    
    # 大盘
    cur.execute("SELECT date,phase,b_pool,a_pool FROM pool_energy WHERE level='market' AND date>='2020-01-01'")
    mk = {}
    for r in cur.fetchall():
        p = r[1] or "NEUTRAL"
        mk[r[0]] = {"ph": p, "b": r[2] or 0, "a": r[3] or 0}
    
    # 增减持
    cur.execute("SELECT code,change_date,is_increase,is_decrease FROM incr_decr WHERE change_date>='2020-01-01'")
    ins = defaultdict(list)
    for r in cur.fetchall():
        if r[2]: ins[r[0]].append((r[1],"INC"))
        elif r[3]: ins[r[0]].append((r[1],"DEC"))
    
    # 信号本身
    cur.execute("SELECT date,SUBSTRING(level,8) FROM pool_energy WHERE phase=%s AND date>='2020-01-01' ORDER BY date", (phase_name,))
    sigs = cur.fetchall()
    conn.close()
    
    px_code = defaultdict(list)
    for r in all_px: px_code[r[0]].append((r[1],r[2],r[3]))
    
    recs = []
    for ds, c6 in sigs:
        pl = px_code.get(c6,[])
        if not pl: continue
        dl = [p[0] for p in pl]
        ix = bisect.bisect_left(dl, ds)
        if ix>=len(dl) or dl[ix]!=ds: continue
        c0 = pl[ix][1] if ix<len(pl) else 0
        c3 = pl[ix+3][1] if ix+3<len(pl) else None
        c5 = pl[ix+5][1] if ix+5<len(pl) else None
        if c3 is None and c5 is None: continue
        y3 = (c3/c0-1) if(c3 and c0) else None
        y5 = (c5/c0-1) if(c5 and c0) else None
        lag5 = (c0/pl[ix-5][1]-1) if(ix>=5 and pl[ix-5][1]) else 0.0
        to0 = pl[ix][2] if ix<len(pl) else None
        tos = [pl[ix-k][2] for k in range(1,21) if ix-k>=0 and pl[ix-k][2] and pl[ix-k][2]>0]
        tz = 0.0
        if to0 and len(tos)>=5:
            mu,sd = np.mean(tos), np.std(tos)
            tz = (to0-mu)/sd if sd>0 else 0.0
        m = mk.get(ds,{"ph":"NEUTRAL","b":0,"a":0})
        il = ins.get(c6,[])
        buy7 = any(abs((dd-ds).days)<=7 and t=="INC" for dd,t in il)
        sell7 = any(abs((dd-ds).days)<=7 and t=="DEC" for dd,t in il)
        en = float(m["b"]+m["a"])
        f = {"l5r":lag5,"tz":tz,"en":en,"ib":1 if buy7 else 0,"is_":1 if(sell7 and not buy7) else 0}
        ph = m["ph"]
        for p in PHASES: f["p_"+p] = 1 if ph==p else 0
        f["y3"]=y3; f["y5"]=y5
        recs.append(f)
    
    return pd.DataFrame(recs)

def run_ridge(df, signal_name):
    """对给定数据框跑 T+3 和 T+5 岭回归"""
    cols = ["l5r","tz","en","ib","is_"]+["p_"+p for p in PHASES]
    fnames = ["lagged_5d_return","turnover_z","total_energy","insider_buy","insider_sell",
              "phase_B_ACCUM","phase_B_DISTRIB","phase_A_ACCUM","phase_A_RECOVER","phase_NEUTRAL","phase_HI_EQ"]
    
    results = {}
    print(f"\n{'='*60}")
    print(f"  {signal_name} (N={len(df)})")
    print(f"{'='*60}")
    
    for yc, label in [("y3","T+3"),("y5","T+5")]:
        dfo = df.dropna(subset=[yc]); N = len(dfo)
        if N < 50:
            print(f"  {label}: 样本不足 {N}")
            results[label] = {"alpha":None,"cv_r2":None,"test_r2":None,"stable_cnt":0,"coefs":{}}
            continue
        
        X = dfo[cols].fillna(0).values; y = dfo[yc].values
        Xs = StandardScaler().fit_transform(X)
        sp = int(N*0.67)
        X_tr,y_tr,X_te,y_te = Xs[:sp],y[:sp],Xs[sp:],y[sp:]
        
        a_ext = np.logspace(-3, 5, 100)
        m = RidgeCV(alphas=a_ext, cv=5, scoring="r2").fit(Xs, y)
        cv = cross_val_score(RidgeCV(alphas=a_ext, cv=3), Xs, y, cv=5, scoring="r2")
        
        # 正则化路径（符号稳定性）
        stable = []
        for i, col in enumerate(cols):
            # 手动检查符号稳定性：从 alpha=0.1 到 10
            seg = []
            for alp in np.logspace(-1, 1, 20):
                rr = RidgeCV(alphas=[alp], cv=3).fit(Xs, y)
                seg.append(rr.coef_[i])
            if np.all(np.array(seg)>0) or np.all(np.array(seg)<0):
                stable.append(col)
        
        alpha_status = "⚠触及边界" if m.alpha_>=99999 else ("⚠>2000" if m.alpha_>=2000 else ("✅<500适中" if m.alpha_<500 else "⚠500-2000"))
        
        print(f"\n  {label}:")
        print(f"    最优alpha={m.alpha_:.2f} {alpha_status}")
        print(f"    训练R2={m.score(X_tr,y_tr):.4f} 测试R2={m.score(X_te,y_te):.4f}")
        print(f"    CV R2={cv.mean():.4f}+-{cv.std():.4f}")
        print(f"    符号稳定: {len(stable)}/{len(cols)}")
        for col,c in zip(cols,m.coef_):
            tag = " ✅" if col in stable else ""
            print(f"      {col:>20s}: {c:.6f}{tag}")
        
        results[label] = {
            "alpha": float(m.alpha_),
            "train_r2": float(m.score(X_tr,y_tr)),
            "test_r2": float(m.score(X_te,y_te)),
            "cv_r2": float(cv.mean()),
            "cv_std": float(cv.std()),
            "stable_cnt": len(stable),
            "total_feats": len(cols),
            "coefs": {col: float(c) for col,c in zip(cols,m.coef_)}
        }
        
        # 判定
        if cv.mean() < 0.02 and (label=="T+3" or True):
            results[label]["fail_cv"] = True
        if len(stable) == 0:
            results[label]["fail_stable"] = True
        if (label=="T+3" and m.score(X_te,y_te)<=0) or (label=="T+5" and m.score(X_te,y_te)<=0):
            results[label]["fail_test"] = True
    
    return results

# ── 主循环 ──
for signal in ["s1_surge", "s2_pulse"]:
    print(f"\n{'='*70}")
    print(f"  正在处理: {signal}")
    print(f"{'='*70}")
    df = get_features_for_phase(signal)
    print(f"  特征矩阵: {len(df)} 行", flush=True)
    results = run_ridge(df, signal.upper())
    
    # 判定
    print(f"\n  判定:")
    for lbl in ["T+3","T+5"]:
        r = results.get(lbl,{})
        if r.get("alpha") is None:
            print(f"    {lbl}: 跳过（样本不足）")
            continue
        f_cv = r.get("fail_cv", False) and not (r["cv_r2"] >= 0.02)
        f_stb = r.get("fail_stable", False)
        f_tst = r.get("fail_test", False)
        fails = [f for f,b in [("CV",f_cv),("稳定",f_stb),("测试",f_tst)] if b]
        if not fails:
            print(f"    {lbl}: ✅ 全部通过 (CV={r['cv_r2']:.4f}, 稳定={r['stable_cnt']}/{r['total_feats']}, 测试R2={r['test_r2']:.4f})")
        else:
            print(f"    {lbl}: ❌ 失败: {', '.join(fails)} (CV={r['cv_r2']:.4f}, 稳定={r['stable_cnt']}/{r['total_feats']}, 测试R2={r['test_r2']:.4f})")

print(f"\n{'='*70}")
print(f"总耗时: {time.time()-t0:.0f}s")
print("✅ 完成")
