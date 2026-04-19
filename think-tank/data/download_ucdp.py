#!/usr/bin/env python3
"""下载 UCDP GED Events 东欧相关数据"""
import requests, csv, json, os, time

TOKEN = '3704a6e6c39684d5'
HEADERS = {'x-ucdp-access-token': TOKEN}
BASE = 'https://ucdpapi.pcr.uu.se/api/gedevents/25.1'
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'ucdp')
os.makedirs(OUTDIR, exist_ok=True)

TARGET_COUNTRIES = [
    'Ukraine', 'Russia', 'Poland', 'Hungary', 'Romania', 'Moldova', 'Belarus',
    'Czech Republic', 'Slovakia', 'Lithuania', 'Latvia', 'Estonia'
]

def load_progress():
    pfile = os.path.join(OUTDIR, '_progress.json')
    if os.path.exists(pfile):
        with open(pfile) as f:
            return json.load(f)
    return {'page': 1, 'collected': 0}

def save_progress(p):
    with open(os.path.join(OUTDIR, '_progress.json'), 'w') as f:
        json.dump(p, f)

def main():
    progress = load_progress()
    page = progress['page']
    
    outpath = os.path.join(OUTDIR, 'eastern_europe_events.csv')
    write_header = (page == 1 and not os.path.exists(outpath))
    
    if write_header:
        # 先取一行看字段
        r = requests.get(BASE, headers=HEADERS, params={'page': 1, 'pagesize': 1}, timeout=30)
        fields = list(r.json()['Result'][0].keys()) if r.json().get('Result') else []
        with open(outpath, 'w', newline='', encoding='utf-8') as f:
            csv.DictWriter(f, fieldnames=fields).writeheader()
    
    # Get total pages
    r = requests.get(BASE, headers=HEADERS, params={'page': 1, 'pagesize': 1}, timeout=30)
    total = r.json().get('TotalPages', 0)
    print(f"Total pages: {total}, resuming from page {page}")
    
    collected = progress['collected']
    
    with open(outpath, 'a', newline='', encoding='utf-8') as f:
        writer = None
        
        while page <= total:
            try:
                r = requests.get(BASE, headers=HEADERS, params={'page': page, 'pagesize': 1000}, timeout=30)
                d = r.json()
                results = d.get('Result', [])
                if not results:
                    break
                
                if writer is None:
                    writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
                
                for e in results:
                    if any(c.lower() in e['country'].lower() for c in TARGET_COUNTRIES):
                        writer.writerow(e)
                        collected += 1
                
                if page % 2000 == 0:
                    print(f"Page {page}/{total} | collected: {collected}")
                    save_progress({'page': page + 1, 'collected': collected})
                
                page += 1
                time.sleep(0.05)  # rate limit
                
            except Exception as ex:
                print(f"Error at page {page}: {ex}, retrying in 5s...")
                time.sleep(5)
    
    # Cleanup
    if os.path.exists(os.path.join(OUTDIR, '_progress.json')):
        os.remove(os.path.join(OUTDIR, '_progress.json'))
    
    size = os.path.getsize(outpath)
    print(f"\nDone! {collected} events saved to {outpath} ({size/1024/1024:.1f} MB)")

if __name__ == '__main__':
    main()
