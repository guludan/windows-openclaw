"""s1_threshold_scan.py — S1 vol_z 阈值扫描：看 CV R² 何时转正"""
import numpy as np, pandas as pd, warnings, time, psycopg2, bisect
from collections import defaultdict
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score
warnings.filterwarnings("ignore")
t0 = time.time()

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

# ── 1. S1 全部信号 + 实际 vol_z 值 ──
conn = psycopg2.connect(DB); cur = conn.cursor()
cur.execute("""
    SELECT date, SUBSTRING(level,8) as code6
    FROM pool_energy WHERE phase='s1_surge' AND date>='2020-01-01' ORDER BY date
""")
s1_list = cur.fetchall()
print(f"S1 全量: {len(s1_list)}", flush=True)

# 取 vol_z —— 从 pool_energy.extra 解析
# extra 是 JSON 字段，含 vol_z
cur.execute("""
    SELECT pe.date, SUBSTRING(pe.level,8) as code6, 
           (pe.extra->>'vol_z')::numeric as vol_z_actual,
           mp.phase as mkt_phase
    FROM pool_energy pe
    LEFT JOIN pool_energy mp ON mp.level='market' AND mp.date=pe.date
    WHERE pe.phase='s1_surge' AND pe.date>='2020-01-01'
    ORDER BY pe.date
""")
s1_vol = cur.fetchall()
conn.close()
print(f"S1 含 vol_z: {len(s1_vol)}", flush=True)

# vol_z 分布
vols = np.array([float(r[2]) for r in s1_vol if r[2] is not None])
print(f"vol_z 分布: min={vols.min():.1f} P25={np.percentile(vols,25):.1f} P50={np.percentile(vols,50):.1f} P75={np.percentile(vols,75):.1f} max={vols.max():.1f}", flush=True)

# 按 vol_z 阈值筛选后的信号数
for thr in [3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 8.0, 10.0]:
    cnt = (vols >= thr).sum()
    print(f"  vol_z >={thr:4.1f}: {cnt} ({cnt/len(vols)*100:.1f}%)", flush=True)
