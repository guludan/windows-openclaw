#!/usr/bin/env python3
"""
init_db.py — 为每个人物目录初始化 SQLite 数据库

扫描 materials/raw/、papers/raw/、papers/pdfs/，注册素材到 items 表。
读取 _distilled_files.json、_quality_scores.json、papers/_meta.json 补充元数据。

用法:
    python3 init_db.py --all                          # 全部人物
    python3 init_db.py --person /path/to/person_dir   # 单人物
    python3 init_db.py --all --reset                  # 重置数据库（删除旧库重建）
"""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path("/mnt/d/data/think-tank-data/personalities")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    person TEXT NOT NULL,
    category TEXT NOT NULL,
    filename TEXT NOT NULL,
    source_type TEXT,
    status TEXT DEFAULT 'raw',
    file_path TEXT,
    file_size INTEGER,
    char_count INTEGER,
    quality_score INTEGER,
    quality_checked INTEGER DEFAULT 0,
    quality_check_time TEXT,
    discard_reason TEXT,
    distilled INTEGER DEFAULT 0,
    distillate_path TEXT,
    distill_time TEXT,
    insight_count INTEGER DEFAULT 0,
    doi TEXT,
    year INTEGER,
    title TEXT,
    has_pdf INTEGER DEFAULT 0,
    added_time TEXT DEFAULT (datetime('now')),
    notes TEXT,
    UNIQUE(person, category, filename)
);

CREATE TABLE IF NOT EXISTS distillate_insights (
    id INTEGER PRIMARY KEY,
    item_id INTEGER REFERENCES items(id),
    insight_idx INTEGER,
    claim_preview TEXT,
    dim TEXT,
    evidence_count INTEGER,
    merged_into_v3 INTEGER DEFAULT 0,
    FOREIGN KEY (item_id) REFERENCES items(id)
);

CREATE TABLE IF NOT EXISTS processing_log (
    id INTEGER PRIMARY KEY,
    person TEXT,
    action TEXT,
    details TEXT,
    timestamp TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_items_person ON items(person);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(person, status);
CREATE INDEX IF NOT EXISTS idx_items_distilled ON items(person, distilled);
"""


def detect_source_type(filename: str) -> str:
    name = filename.lower()
    if name.startswith('paper_'):
        return 'paper'
    if name.startswith('exa'):
        return 'exa'
    if name.startswith('tavily'):
        return 'tavily'
    if 'marxists' in name:
        return 'marxists'
    if name.startswith('zhihu_'):
        return 'zhihu'
    if name.startswith('cls_'):
        return 'cls'
    if 'wikipedia' in name:
        return 'wikipedia'
    if name.startswith('bv') or '转录' in name:
        return 'bilibili'
    return 'unknown'


def iter_persons():
    """Same traversal as migrate_structure.py"""
    for group_dir in sorted(BASE_DIR.iterdir()):
        if not group_dir.is_dir() or group_dir.name.startswith('.'):
            continue
        for sub_dir in sorted(group_dir.iterdir()):
            if not sub_dir.is_dir():
                continue
            if (sub_dir / "materials").exists() or (sub_dir / "papers").exists():
                yield sub_dir
                continue
            for person_dir in sorted(sub_dir.iterdir()):
                if not person_dir.is_dir():
                    continue
                if (person_dir / "materials").exists() or (person_dir / "papers").exists():
                    yield person_dir


def init_person_db(person_dir: Path, reset: bool = False) -> dict:
    """Initialize or update _db.sqlite for a person"""
    db_path = person_dir / "_db.sqlite"
    person_name = person_dir.name
    stats = {"items_added": 0, "items_updated": 0, "pdfs_registered": 0, "meta_loaded": False}

    if reset and db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)

    now = datetime.now().isoformat()

    # --- Register files from materials/raw/ ---
    mat_raw = person_dir / "materials" / "raw"
    if mat_raw.exists():
        for f in sorted(mat_raw.iterdir()):
            if not f.is_file():
                continue
            try:
                content = f.read_text(encoding='utf-8', errors='ignore')
                char_count = len(content)
            except Exception:
                char_count = 0
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO items (person, category, filename, source_type, status, file_path, file_size, char_count)
                    VALUES (?, 'materials', ?, ?, 'raw', ?, ?, ?)
                """, (person_name, f.name, detect_source_type(f.name),
                      f"materials/raw/{f.name}", f.stat().st_size, char_count))
                if conn.total_changes:
                    stats["items_added"] += 1
            except Exception as e:
                print(f"    ⚠ Error inserting {f.name}: {e}")

    # --- Register files from papers/raw/ ---
    papers_raw = person_dir / "papers" / "raw"
    if papers_raw.exists():
        for f in sorted(papers_raw.iterdir()):
            if not f.is_file():
                continue
            try:
                content = f.read_text(encoding='utf-8', errors='ignore')
                char_count = len(content)
            except Exception:
                char_count = 0
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO items (person, category, filename, source_type, status, file_path, file_size, char_count)
                    VALUES (?, 'papers', ?, 'paper', 'raw', ?, ?, ?)
                """, (person_name, f.name, f"papers/raw/{f.name}", f.stat().st_size, char_count))
                if conn.total_changes:
                    stats["items_added"] += 1
            except Exception as e:
                print(f"    ⚠ Error inserting {f.name}: {e}")

    # --- Register PDFs (mark has_pdf=1) ---
    pdfs_dir = person_dir / "papers" / "pdfs"
    if pdfs_dir.exists():
        for f in sorted(pdfs_dir.glob("*.pdf")):
            conn.execute("""
                UPDATE items SET has_pdf = 1 WHERE person = ? AND filename = ?
            """, (person_name, f.stem + ".txt"))
            # Also try without .txt extension
            conn.execute("""
                UPDATE items SET has_pdf = 1 WHERE person = ? AND filename = ?
            """, (person_name, f.name))
            stats["pdfs_registered"] += 1

    # --- Load papers/_meta.json for doi/year/title ---
    meta_path = person_dir / "papers" / "_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            if isinstance(meta, dict):
                for filename, entry in meta.items():
                    conn.execute("""
                        UPDATE items SET doi=?, year=?, title=? WHERE person=? AND filename=?
                    """, (entry.get('doi'), entry.get('year'), entry.get('title'),
                          person_name, filename))
                stats["meta_loaded"] = True
            elif isinstance(meta, list):
                for entry in meta:
                    fn = entry.get('filename', '')
                    conn.execute("""
                        UPDATE items SET doi=?, year=?, title=? WHERE person=? AND filename=?
                    """, (entry.get('doi'), entry.get('year'), entry.get('title'),
                          person_name, fn))
                stats["meta_loaded"] = True
        except Exception as e:
            print(f"    ⚠ Error reading _meta.json: {e}")

    # --- Load _distilled_files.json ---
    distilled_path = person_dir / "_distilled_files.json"
    if distilled_path.exists():
        try:
            distilled = json.loads(distilled_path.read_text(encoding='utf-8'))
            for fn in distilled.get('distilled_files', []):
                conn.execute("""
                    UPDATE items SET distilled=1 WHERE person=? AND filename=?
                """, (person_name, fn))
        except Exception as e:
            print(f"    ⚠ Error reading _distilled_files.json: {e}")

    # --- Load materials/_quality_scores.json ---
    qs_path = person_dir / "materials" / "_quality_scores.json"
    if qs_path.exists():
        try:
            scores = json.loads(qs_path.read_text(encoding='utf-8'))
            for entry in scores:
                fn = entry.get('filename', '')
                score = entry.get('score', 0)
                conn.execute("""
                    UPDATE items SET quality_score=?, quality_checked=1, quality_check_time=?
                    WHERE person=? AND filename=?
                """, (score, now, person_name, fn))
        except Exception as e:
            print(f"    ⚠ Error reading _quality_scores.json: {e}")

    # --- Log ---
    conn.execute("""
        INSERT INTO processing_log (person, action, details) VALUES (?, 'init_db', ?)
    """, (person_name, json.dumps(stats, ensure_ascii=False)))

    conn.commit()
    conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description="初始化人物素材SQLite数据库")
    parser.add_argument("--all", action="store_true", help="处理所有人物")
    parser.add_argument("--person", metavar="DIR", help="只处理指定人物目录")
    parser.add_argument("--reset", action="store_true", help="删除旧库重建")
    args = parser.parse_args()

    if args.person:
        person_dir = Path(args.person)
        if not person_dir.is_dir():
            candidates = list(BASE_DIR.rglob(args.person))
            if not candidates:
                print(f"ERROR: {args.person} not found")
                import sys; sys.exit(1)
            person_dir = candidates[0]
        persons = [person_dir]
    elif args.all:
        persons = list(iter_persons())
    else:
        parser.print_help()
        import sys; sys.exit(1)

    print(f"Initializing DB for {len(persons)} persons")
    print("=" * 60)

    total_items = 0
    for person_dir in persons:
        stats = init_person_db(person_dir, reset=args.reset)
        total_items += stats["items_added"]
        if stats["items_added"] > 0 or stats["meta_loaded"]:
            try:
                rel = person_dir.relative_to(BASE_DIR)
            except ValueError:
                rel = person_dir
            print(f"  {rel}: items={stats['items_added']}, pdfs={stats['pdfs_registered']}, meta={'✓' if stats['meta_loaded'] else '-'}")

    print("=" * 60)
    print(f"Total items registered: {total_items}")
    print("Done ✅")


if __name__ == "__main__":
    main()
