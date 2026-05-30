"""run_ridge_s1s2_segmented.py — S1/S2 按 phase 分段岭回归"""
import numpy as np, pandas as pd, warnings, time, psycopg2, bisect, json
from collections import defaultdict
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score
warnings.filterwarnings("ignore")
t0 = time.time()

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
PHASES = ["B_ACCUM","B_DISTRIB","A_ACCUM","A_RECOVER","NEUTRAL","HI_EQ"]
COL_NAMES = ["lagged_5d_return","turnover_z","total_energy","insider_buy","insider_sell"]

def load_data(phase_name):
    """一次性加载所有数据（S1/S2 共用）"""
    conn = psycopg2.connect(DB); cur = conn.cursor()
    
    # 取所有涉及股票（S1 ∪ S2）
    cur.execute("""
        SELECT DISTINCT SUBSTRING(level, 8) as code6
        FROM pool_energy WHERE phase IN ('s1_surge','s2_pulse') AND date >= '2020-01-01'
    """)
    codes = [r[0] for r in cur.fetchall()]
    
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
    print(f"  日线: {len(all_px)}", flush=True)
    
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
    
    conn.close()
    
    px_code = defaultdict(list)
    for r in all_px: px_code[r[0]].append((r[1],r[2],r[3]))
    return px_code, mk, ins

def build_features(phase_name, px_code, mk, ins):
    """为指定 signal_type 构建特征"""
    conn = psycopg2.connect(DB); cur = conn.cursor()
    cur.execute("SELECT date,SUBSTRING(level,8) FROM pool_energy WHERE phase=%s AND date>='2020-01-01' ORDER BY date", (phase_name,))
    sigs = cur.fetchall(); conn.close()
    
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
        f = {"l5r":lag5,"tz":tz,"en":en,
             "ib":1 if buy7 else 0,"is_":1 if(sell7 and not buy7) else 0,
             "phase": m["ph"], "y3":y3, "y5":y5}
        recs.append(f)
    
    df = pd.DataFrame(recs)
    print(f"  特征: {len(df)} 行", flush=True)
    
    # 按 phase 分组统计
    if len(df) > 0 and "phase" in df.columns:
        grp = df.groupby("phase").size()
        for ph, cnt in grp.sort_values(ascending=False).items():
            print(f"    {ph:15s}: {cnt} ({cnt/len(df)*100:.0f}%)", flush=True)
    
    return df

def run_segmented_ridge(df, signal_name):
    """按大盘 phase 分段跑岭回归"""
    cols = ["l5r","tz","en","ib","is_"]
    results = {}
    
    print(f"\n{'='*70}", flush=True)
    print(f"  {signal_name} — 分段回归", flush=True)
    print(f"{'='*70}", flush=True)
    
    # 先全量跑一次作为 baseline
    for yc, label in [("y3","T+3"),("y5","T+5")]:
        print(f"\n--- {signal_name} 全量 {label} ---", flush=True)
        for phase_name in ["ALL"] + PHASES:
            if phase_name == "ALL":
                df_seg = df.dropna(subset=[yc])
                seg_label = "全量"
            else:
                df_seg = df[df["phase"]==phase_name].dropna(subset=[yc])
                seg_label = phase_name
            
            N = len(df_seg)
            if N < 50:
                print(f"  {seg_label:15s}: 样本不足 {N}", flush=True)
                results[(signal_name, label, seg_label)] = None
                continue
            
            X = df_seg[cols].fillna(0).values; y = df_seg[yc].values
            Xs = StandardScaler().fit_transform(X)
            sp = int(N*0.67)
            X_tr,y_tr,X_te,y_te = Xs[:sp],y[:sp],Xs[sp:],y[sp:]
            
            a_ext = np.logspace(-3, 5, 100)
            m = RidgeCV(alphas=a_ext, cv=5, scoring="r2").fit(Xs, y)
            cv = cross_val_score(RidgeCV(alphas=a_ext, cv=3), Xs, y, cv=5, scoring="r2")
            
            # 符号稳定性
            stable = 0
            for i in range(len(cols)):
                seg = []
                for alp in np.logspace(-1, 1, 20):
                    rr = RidgeCV(alphas=[alp], cv=3).fit(Xs, y)
                    seg.append(rr.coef_[i])
                if np.all(np.array(seg)>0) or np.all(np.array(seg)<0):
                    stable += 1
            
            alpha_s = "⚠边界" if m.alpha_>=99999 else ("⚠>2k" if m.alpha_>=2000 else ("✅<500" if m.alpha_<500 else f"α={m.alpha_:.0f}"))
            
            passed = cv.mean() >= 0.02 and stable > 0 and m.score(X_te,y_te) > 0
            tag = "✅" if passed else "❌"
            
            print(f"  {seg_label:15s}: N={N:5d} α={m.alpha_:>9.2f}({alpha_s}) CV_R2={cv.mean():+.4f}±{cv.std():.4f} 测试={m.score(X_te,y_te):+.4f} 稳定={stable}/{len(cols)} {tag}", flush=True)
            
            coefs = {}
            for i, col in enumerate(cols):
                c = m.coef_[i]
                coefs[col] = float(c)
                if abs(c) > 0.001:  # 只打印有意义的系数
                    pass  # 需要才打
            
            results[(signal_name, label, seg_label)] = {
                "alpha": float(m.alpha_),
                "cv_r2": float(cv.mean()),
                "test_r2": float(m.score(X_te,y_te)),
                "stable_cnt": stable,
                "coefs": coefs,
                "passed": passed
            }
    
    return results

# ── 加载数据（S1+S2 统一加载，减少 DB 开销）───
print("="*70, flush=True)
print("  加载数据...", flush=True)
print("="*70, flush=True)
px_code, mk, ins = load_data(None)

all_results = {}
for signal in ["s1_surge", "s2_pulse"]:
    print(f"\n{'='*70}", flush=True)
    print(f"  构建 {signal} 特征...", flush=True)
    print(f"{'='*70}", flush=True)
    df = build_features(signal, px_code, mk, ins)
    results = run_segmented_ridge(df, signal.upper())
    all_results.update(results)

# ── 总结 ──
print(f"\n{'='*70}", flush=True)
print("  分段岭回归总结", flush=True)
print(f"{'='*70}", flush=True)
print(f"{'信号':15s} {'分段':15s} {'窗口':5s} {'N':>7s} {'CV_R2':>8s} {'测试R2':>8s} {'稳定':>6s} {'状态':>6s}", flush=True)
print("-"*70, flush=True)

summary = []
for key, result in sorted(all_results.items()):
    if result is None:
        continue
    sig, win, seg = key
    N = 0  # 不知道N，后面补
    print(f"{sig:15s} {seg:15s} {win:5s} {N:>7d} {result['cv_r2']:+.4f}  {result['test_r2']:+.4f}  {result['stable_cnt']}/5 {'✅' if result['passed'] else '❌'}", flush=True)

# 记录哪些分段通过
passed_segments = [(sig, win, seg) for (sig, win, seg), r in all_results.items() if r and r["passed"]]
print(f"\n通过的分段: {len(passed_segments)}个", flush=True)
for s, w, seg in passed_segments:
    r = all_results[(s, w, seg)]
    print(f"  {s:15s} {seg:15s} {w:5s}: CV_R2={r['cv_r2']:.4f} 系数={r['coefs']}", flush=True)

print(f"\n总耗时: {time.time()-t0:.0f}s", flush=True)
print("✅ 完成", flush=True)
