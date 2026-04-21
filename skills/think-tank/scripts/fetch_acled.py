#!/usr/bin/env python3
"""拉取ACLED冲突事件数据 → D盘"""
import json, os, time, csv, io
from pathlib import Path
import requests

OUT_DIR = Path("/mnt/d/data/conflict-data/acled")
EMAIL = os.environ.get("ACLED_EMAIL", "")
PASSWORD = os.environ.get("ACLED_PASSWORD", "")
OUT_DIR.mkdir(parents=True, exist_ok=True)

def login():
    """获取ACLED access key"""
    r = requests.post("https://api.acleddata.com/acled/v2/login",
                      data={"email": EMAIL, "password": PASSWORD}, timeout=30)
    if r.status_code == 200:
        token = r.json().get("token") or r.json().get("data", {}).get("token", "")
        if token:
            print(f"Login OK, token: {token[:20]}...")
            return token
    print(f"Login failed: {r.status_code} {r.text[:200]}")
    return None

def fetch_page(key, year, page=1):
    params = {
        "key": key,
        "year": year,
        "limit": 50000,
        "page": page,
        "fields": "event_id_cnty,event_date,year,actor1,actor2,assoc_actor_1,assoc_actor_2,inter1,inter2,interaction,country,admin1,admin2,admin3,location,latitude,longitude,source,sources,fatalities,timestamp"
    }
    r = requests.get("https://api.acleddata.com/acled/v2/events/read",
                     params=params, timeout=120)
    if r.status_code == 200:
        return r.json()
    print(f"  ERROR {year}: {r.status_code}")
    return None

TOKEN = login()
if not TOKEN:
    print("Cannot login to ACLED, skipping")
    exit(1)

for year in range(2010, 2027):
    fname = OUT_DIR / f"acled_{year}.json"
    if fname.exists() and fname.stat().st_size > 1000:
        print(f"[{year}] exists ({fname.stat().st_size} bytes)")
        continue
    
    print(f"[{year}] fetching...", end=" ", flush=True)
    data = fetch_page(TOKEN, year)
    if data:
        count = data.get("count", 0)
        items = data.get("data", [])
        with open(fname, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False)
        print(f"✅ {count} events")
    else:
        print("❌")
    time.sleep(1.5)

print("\nDone!")
total = sum(len(json.load(open(f))) for f in OUT_DIR.glob("*.json"))
print(f"Total events: {total}")
