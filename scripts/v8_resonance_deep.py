"""V8 共振策略 — 深入分析
关键发现需要进一步拆解:

1. SMA<-30% vs SMA<-15%: 为什么深偏离反而差?
2. 2026年共振劣化原因
3. "共振+大跌"组合的真实情况
"""
import psycopg2, numpy as np
from collections import defaultdict
import pickle, os

cache_path = './.v8_cache.pkl'
if not os.path.exists(cache_path):
    print("先跑 v8_resonance_v2.py 生成缓存")
    exit()

with open(cache_path, 'rb') as f:
    cache = pickle.load(f)
results_sma = cache['sma']        # SMA<-15%
results_resonance = cache['resonance']  # SMA<-15% + PB<-10%
print(f"SMA<-15%: {len(results_sma)}  共振: {len(results_resonance)}")

# 1) SMA偏离度 vs 收益: 为什么-30%比-15%差?
# 可能因为深偏离在熊市中失锚
print(f"\n{'='*70}")
print(f"  SMA偏离度 vs T+5 收益 — 详细分层")
print(f"{'='*70}")
sma_buckets = defaultdict(list)
for r in results_sma:
    sd_bucket = int(r['sd'] * 100)  # 例如 -18 → -18%
    sma_buckets[sd_bucket].append(r['f5'])

for sd in sorted(sma_buckets.keys()):
    rs = np.array(sma_buckets[sd])
    print(f"  SMA偏度 {sd:>+3d}%: {len(rs):>4d}条  均值{np.mean(rs):+.2%}  胜率{np.mean(rs>0):.1%}")

# 2) 2026年共振失效
print(f"\n{'='*70}")
print(f"  共振信号: 2026逐月拆解")
print(f"{'='*70}")
monthly = defaultdict(list)
for r in results_resonance:
    if r['date'].year == 2026 or r['date'].year == 2025:
        m = f"{r['date'].year}-{r['date'].month:02d}"
        monthly[m].append(r['f5'])
for m in sorted(monthly.keys()):
    rs = np.array(monthly[m])
    print(f"  {m}: {len(rs):>4d}条  均值{np.mean(rs):+.2%}  中位{np.median(rs):+.2%}  胜率{np.mean(rs>0):.1%}")

# 3) 看2026年触发时的大盘phase
print(f"\n{'='*70}")
print(f"  共振信号按大盘phase (2026)")
print(f"{'='*70}")
conn = psycopg2.connect("host=192.168.3.14 dbname=guludan user=guludan password=toto")
cur = conn.cursor()
cur.execute("SELECT date, phase FROM pool_energy WHERE level='market' ORDER BY date")
mkt_phase = {r[0]: r[1] or 'NEUTRAL' for r in cur.fetchall()}
conn.close()

by_phase = defaultdict(list)
by_phase_2026 = defaultdict(list)
for r in results_resonance:
    ph = mkt_phase.get(r['date'], 'UNKNOWN')
    by_phase[ph].append(r['f5'])
    if r['date'].year == 2026:
        by_phase_2026[ph].append(r['f5'])

print("  全量按phase:")
for ph in ['B_ACCUM','A_RECOVER','NEUTRAL','B_DISTRIB','A_ACCUM']:
    vals = by_phase.get(ph)
    if not vals: continue
    rs = np.array(vals)
    print(f"  {ph:>15s}: {len(rs):>4d}条  均值{np.mean(rs):+.2%}  胜率{np.mean(rs>0):.1%}")

print("  2026按phase:")
for ph in ['B_ACCUM','A_RECOVER','NEUTRAL','B_DISTRIB','A_ACCUM']:
    vals = by_phase_2026.get(ph)
    if not vals: continue
    rs = np.array(vals)
    print(f"  {ph:>15s}: {len(rs):>4d}条  均值{np.mean(rs):+.2%}  胜率{np.mean(rs>0):.1%}")

# 4) 共振 + 大跌 的真实组合
print(f"\n{'='*70}")
print(f"  共振+当日大跌 对比 纯下跌(无共振)")
print(f"{'='*70}")
# 从results_sma里找: 同样是SMA<-15%,有共振vs无共振
with_pb = defaultdict(list)
no_pb = defaultdict(list)
for r in results_sma:
    if r['pb_dev'] < -0.10:
        with_pb[f"all"].append(r['f5'])
    else:
        no_pb[f"all"].append(r['f5'])
    # 只看大跌的
    if r['ret'] < -0.05:
        if r['pb_dev'] < -0.10:
            with_pb[f"大跌5"].append(r['f5'])
        else:
            no_pb[f"大跌5"].append(r['f5'])

print("  同是SMA<-15%:")
for tag in ['all', '大跌5']:
    wp = with_pb.get(tag,[]); npb = no_pb.get(tag,[])
    if not wp or not npb: continue
    print(f"  {tag}:")
    print(f"    有PB过滤(N={len(wp)}): 均值{np.mean(wp):+.2%} 胜率{np.mean(np.array(wp)>0):.1%}")
    print(f"    无PB过滤(N={len(npb)}): 均值{np.mean(npb):+.2%} 胜率{np.mean(np.array(npb)>0):.1%}")
    print(f"    提升: 均值差{np.mean(wp)-np.mean(npb):+.2%} 胜率差{np.mean(np.array(wp)>0)-np.mean(np.array(npb)>0):+.1%}")

print(f"\n✅")
