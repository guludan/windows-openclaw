#!/usr/bin/env python3
"""
数据导入脚本 - 将 ACLED/UCDP/GDELT 原始数据导入 SQLite
用法:
  python3 data_import.py --source ucdp --file eastern_europe_events.csv
  python3 data_import.py --source gdelt-events --dir gdelt/ --pattern "events_*.zip"
  python3 data_import.py --source gdelt-gkg --dir gdelt/ --pattern "gkg_*.zip"
  python3 data_import.py --source acled --file acled_export.csv
"""
import argparse, csv, json, sqlite3, os, sys, zipfile, io
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'populist_crisis.db')

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def import_ucdp(filepath):
    """导入 UCDP GED Events 数据"""
    conn = get_conn()
    
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    count = 0
    for r in rows:
        # Insert location
        loc_id = None
        if r.get('country'):
            conn.execute("""
                INSERT OR IGNORE INTO locations (country, country_code, adm_1, adm_2, latitude, longitude, where_desc)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (r['country'], None, r.get('adm_1'), r.get('adm_2'),
                  float(r['latitude']) if r.get('latitude') else None,
                  float(r['longitude']) if r.get('longitude') else None,
                  r.get('where_description')))
            cur = conn.execute("SELECT id FROM locations WHERE country=? AND COALESCE(adm_1,'')=COALESCE(?,'') AND COALESCE(adm_2,'')=COALESCE(?,'')",
                             (r['country'], r.get('adm_1',''), r.get('adm_2','')))
            loc_id = cur.fetchone()['id']
        
        # Insert actors
        actor_a_id = actor_b_id = None
        for field, side in [('side_a','side_a'),('side_b','side_b')]:
            if r.get(field):
                conn.execute("""
                    INSERT OR IGNORE INTO actors (name, actor_type, side, source_db, source_id)
                    VALUES (?, 'unknown', ?, 'UCDP', ?)
                """, (r[field], side, r.get(f'{field}_dset_id','')))
                cur = conn.execute("SELECT id FROM actors WHERE source_db='UCDP' AND source_id=?",
                                 (r.get(f'{field}_dset_id',''),))
                if side == 'side_a': actor_a_id = cur.fetchone()['id']
                else: actor_b_id = cur.fetchone()['id']
        
        # Insert event
        event_date = r.get('date_start', r.get('date_end', ''))
        if event_date and len(event_date) >= 10:
            event_date = event_date[:10]
        
        conn.execute("""
            INSERT OR IGNORE INTO conflict_events
            (source, source_id, event_date, date_start, date_end, date_prec,
             location_id, country, latitude, longitude, where_desc,
             actor_a_id, actor_b_id, actor_a, actor_b,
             event_type, fatalities, fatalities_a, fatalities_b, fatalities_civilians,
             source_article, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ('UCDP', r['id'], event_date, r.get('date_start',''), r.get('date_end',''),
              int(r.get('date_prec',1)), loc_id, r['country'],
              float(r['latitude']) if r.get('latitude') else None,
              float(r['longitude']) if r.get('longitude') else None,
              r.get('where_description',''),
              actor_a_id, actor_b_id, r.get('side_a',''), r.get('side_b',''),
              r.get('type_of_violence',''),
              int(r.get('best',0) or 0), int(r.get('deaths_a',0) or 0),
              int(r.get('deaths_b',0) or 0), int(r.get('deaths_civilians',0) or 0),
              r.get('source_article',''),
              f"conflict_name={r.get('conflict_name','')}"))
        count += 1
    
    conn.commit()
    
    # Record version
    conn.execute("""
        INSERT OR IGNORE INTO data_versions (source, version, download_date, record_count)
        VALUES ('UCDP', '25.1', ?, ?)
    """, (datetime.now().strftime('%Y-%m-%d'), count))
    conn.commit()
    conn.close()
    print(f"✅ Imported {count} UCDP events")

def import_acled(filepath):
    """导入 ACLED 数据"""
    conn = get_conn()
    
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    count = 0
    for r in rows:
        loc_id = None
        country = r.get('country','')
        if country:
            conn.execute("""
                INSERT OR IGNORE INTO locations (country, adm_1, adm_2, latitude, longitude, where_desc)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (country, r.get('admin1'), r.get('admin2'),
                  float(r['latitude']) if r.get('latitude') else None,
                  float(r['longitude']) if r.get('longitude') else None,
                  r.get('location','')))
            cur = conn.execute("SELECT id FROM locations WHERE country=? AND COALESCE(adm_1,'')=COALESCE(?,'') AND COALESCE(adm_2,'')=COALESCE(?,'')",
                             (country, r.get('admin1',''), r.get('admin2','')))
            loc_id = cur.fetchone()['id']
        
        conn.execute("""
            INSERT OR IGNORE INTO conflict_events
            (source, source_id, event_date, date_prec,
             location_id, country, latitude, longitude, where_desc,
             actor_a, actor_b, interaction,
             event_type, sub_event_type, fatalities,
             source_article, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ('ACLED', r.get('data_id','') or r.get('event_id_cnty',''),
              r.get('event_date','')[:10] if r.get('event_date') else None,
              int(r.get('date_precision',1)),
              loc_id, country,
              float(r['latitude']) if r.get('latitude') else None,
              float(r['longitude']) if r.get('longitude') else None,
              r.get('location',''),
              r.get('actor1',''), r.get('actor2',''), r.get('interaction',''),
              r.get('event_type',''), r.get('sub_event_type',''),
              int(r.get('fatalities',0) or 0),
              r.get('source',''), r.get('notes','')))
        count += 1
    
    conn.commit()
    conn.execute("""
        INSERT OR IGNORE INTO data_versions (source, version, download_date, record_count)
        VALUES ('ACLED', 'v1', ?, ?)
    """, (datetime.now().strftime('%Y-%m-%d'), count))
    conn.commit()
    conn.close()
    print(f"✅ Imported {count} ACLED events")

def import_gdelt_events(directory, pattern):
    """导入 GDELT Events 每日文件"""
    conn = get_conn()
    import glob
    files = sorted(glob.glob(os.path.join(directory, pattern)))
    
    version_id = None
    count = 0
    
    for zipfile_path in files:
        basename = os.path.basename(zipfile_path)
        datestr = basename.replace('events_','').replace('.export.CSV.zip','').replace('.zip','')
        
        print(f"  Processing {basename}...")
        with zipfile.ZipFile(zipfile_path) as zf:
            for name in zf.namelist():
                if name.endswith('.CSV') or name.endswith('.csv'):
                    with zf.open(name) as f:
                        text = io.TextIOWrapper(f, encoding='utf-8', errors='replace')
                        reader = csv.DictReader(text)
                        for r in reader:
                            if not r.get('GLOBALEVENTID'):
                                continue
                            conn.execute("""
                                INSERT OR IGNORE INTO gdelt_events
                                (global_event_id, event_date, actor1_code, actor1_name,
                                 actor2_code, actor2_name, event_code, event_base_code, root_code,
                                 action_geo_type, action_geo_fullname, action_geo_country_code,
                                 action_geo_lat, action_geo_lon,
                                 goldstein_scale, num_mentions, num_sources, num_articles, avg_tone,
                                 source_file)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """, (r['GLOBALEVENTID'], r.get('SQLDATE',''),
                                  r.get('Actor1Code'), r.get('Actor1Name'),
                                  r.get('Actor2Code'), r.get('Actor2Name'),
                                  r.get('EventCode'), r.get('EventBaseCode'), r.get('RootCode'),
                                  int(r.get('ActionGeo_Type',0) or 0),
                                  r.get('ActionGeo_FullName'), r.get('ActionGeo_CountryCode'),
                                  float(r['ActionGeo_Lat']) if r.get('ActionGeo_Lat') else None,
                                  float(r['ActionGeo_Lon']) if r.get('ActionGeo_Lon') else None,
                                  float(r['GoldsteinScale']) if r.get('GoldsteinScale') else None,
                                  int(r.get('NumMentions',0) or 0),
                                  int(r.get('NumSources',0) or 0),
                                  int(r.get('NumArticles',0) or 0),
                                  float(r['AvgTone']) if r.get('AvgTone') else None,
                                  basename))
                            count += 1
                            if count % 100000 == 0:
                                conn.commit()
                                print(f"    {count} events processed...")
        
        conn.commit()
    
    conn.execute("""
        INSERT OR IGNORE INTO data_versions (source, version, download_date, record_count)
        VALUES ('GDELT_EVENTS', 'daily', ?, ?)
    """, (datetime.now().strftime('%Y-%m-%d'), count))
    conn.commit()
    conn.close()
    print(f"✅ Imported {count} GDELT events from {len(files)} files")

def import_gdelt_gkg(directory, pattern):
    """导入 GDELT GKG 每日文件"""
    conn = get_conn()
    import glob
    files = sorted(glob.glob(os.path.join(directory, pattern)))
    
    count = 0
    for zipfile_path in files:
        basename = os.path.basename(zipfile_path)
        print(f"  Processing {basename}...")
        with zipfile.ZipFile(zipfile_path) as zf:
            for name in zf.namelist():
                if name.endswith('.csv') or name.endswith('.CSV'):
                    with zf.open(name) as f:
                        # GKG uses tab separation
                        text = io.TextIOWrapper(f, encoding='utf-8', errors='replace')
                        reader = csv.reader(text, delimiter='\t')
                        for cols in reader:
                            if len(cols) < 4:
                                continue
                            gkg_id = cols[0]
                            date = cols[1]
                            source_url = cols[4] if len(cols) > 4 else ''
                            themes = cols[7] if len(cols) > 7 else ''
                            locations = cols[8] if len(cols) > 8 else ''
                            persons = cols[10] if len(cols) > 10 else ''
                            orgs = cols[11] if len(cols) > 11 else ''
                            tone = float(cols[15]) if len(cols) > 15 and cols[15] else None
                            
                            conn.execute("""
                                INSERT OR IGNORE INTO gdelt_gkg
                                (gkg_record_id, date, source_url, themes, locations,
                                 persons, organizations, tone, source_file)
                                VALUES (?,?,?,?,?,?,?,?,?)
                            """, (gkg_id, date, source_url, themes, locations, persons, orgs, tone, basename))
                            count += 1
        conn.commit()
    
    conn.execute("""
        INSERT OR IGNORE INTO data_versions (source, version, download_date, record_count)
        VALUES ('GDELT_GKG', '2.1', ?, ?)
    """, (datetime.now().strftime('%Y-%m-%d'), count))
    conn.commit()
    conn.close()
    print(f"✅ Imported {count} GDELT GKG records from {len(files)} files")

def main():
    parser = argparse.ArgumentParser(description='导入冲突数据到 SQLite')
    parser.add_argument('--source', required=True, choices=['ucdp','acled','gdelt-events','gdelt-gkg'])
    parser.add_argument('--file', help='CSV 文件路径')
    parser.add_argument('--dir', help='目录路径（GDELT 批量）')
    parser.add_argument('--pattern', default='*.zip', help='文件匹配模式')
    args = parser.parse_args()
    
    if args.source == 'ucdp':
        if not args.file:
            print("Error: --file required for UCDP")
            sys.exit(1)
        import_ucdp(args.file)
    elif args.source == 'acled':
        if not args.file:
            print("Error: --file required for ACLED")
            sys.exit(1)
        import_acled(args.file)
    elif args.source == 'gdelt-events':
        import_gdelt_events(args.dir or '.', args.pattern)
    elif args.source == 'gdelt-gkg':
        import_gdelt_gkg(args.dir or '.', args.pattern)

if __name__ == '__main__':
    main()
