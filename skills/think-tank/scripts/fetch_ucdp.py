#!/usr/bin/env python3
"""拉取UCDP冲突事件数据 → D盘"""
import csv, json, os, time, requests
from pathlib import Path

OUT_DIR = Path("/mnt/d/data/conflict-data/ucdp")
API_KEY = os.environ.get("UCDP_API_KEY", "")
BASE = "https://ucdpapi.pcr.uu.se/api/georeferenced"

OUT_DIR.mkdir(parents=True, exist_ok=True)

def fetch_page(year, page=1, limit=50000):
    params = {
        "key": API_KEY,
        "year": year,
        "page": page,
        "limit": limit,
    }
    r = requests.get(BASE, params=params, timeout=60)
    if r.status_code == 200:
        return r.json()
    else:
        print(f"  ERROR {year} p{page}: {r.status_code} {r.text[:200]}")
        return None

for year in range(2010, 2027):
    fname = OUT_DIR / f"ucdp_{year}.json"
    if fname.exists() and fname.stat().st_size > 1000:
        print(f"[{year}] already exists ({fname.stat().st_size} bytes)")
        continue
    
    print(f"[{year}] fetching...", end=" ", flush=True)
    data = fetch_page(year)
    if data and isinstance(data, list):
        with open(fname, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"✅ {len(data)} events")
    elif data and isinstance(data, dict):
        # might be paginated or have Result key
        items = data.get("Result", data.get("data", data))
        if isinstance(items, list):
            with open(fname, 'w', encoding='utf-8') as f:
                json.dump(items, f, ensure_ascii=False)
            print(f"✅ {len(items)} events")
        else:
            print(f"unexpected format: {list(data.keys())[:5]}")
    else:
        print(f"❌ no data")
    time.sleep(1)

print("\nDone!")
total = sum(1 for f in OUT_DIR.glob("*.json") for _ in json.load(open(f)))
print(f"Total events: {total}")
