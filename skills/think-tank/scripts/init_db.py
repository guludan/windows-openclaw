#!/usr/bin/env python3
"""
init_db.py — 为每个人物目录初始化 SQLite 数据库

扫描 materials/raw/、papers/raw/、papers/pdfs/，注册素材到 items 表。
读取 _distilled_files.json、_quality_scores.json、papers/_meta.json 补充元数据。
注册新素材时自动进行内容去重（Jaccard相似度>0.85）。

用法:
    python3 init_db.py --all                          # 全部人物
    python3 init_db.py --person /path/to/person_dir   # 单人物
    python3 init_db.py --all --reset                  # 重置数据库（删除旧库重建）
"""

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path("/mnt/d/data/think-tank-data/personalities")
DEDUP_THRESHOLD = 0.85
DEDUP_PREVIEW_CHARS = 500

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


def jaccard_similarity(text_a: str, text_b: str) -> float:
    """基于词集合的Jaccard相似度，取前DEDUP_PREVIEW_CHARS字比较"""
    a = set(text_a[:DEDUP_PREVIEW_CHARS].split())
    b = set(text_b[:DEDUP_PREVIEW_CHARS].split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def check_dup_against_existing(content: str, existing_contents: list) -> str | None:
    """检查新内容是否与已有内容重复，返回重复文件名或None"""
    for filename, existing_preview in existing_contents:
        sim = jaccard_similarity(content, existing_preview)
        if sim >= DEDUP_THRESHOLD:
            return filename
    return None


def iter_persons():
    """Same traversal logic as init_db scan"""
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
    stats = {"items_added": 0, "items_updated": 0, "pdfs_registered": 0,
             "meta_loaded": False, "duplicates": 0}

    if reset and db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")

    now = datetime.now().isoformat()
    mat_dir = person_dir / "materials"
    mat_raw = mat_dir / "raw"
    papers_raw = person_dir / "papers" / "raw"

    # --- Phase 1: Collect existing file contents for dedup (from DB or disk) ---
    existing_contents = []  # [(filename, content_preview)]
    existing_filenames = set()

    # Load already-registered files from DB (non-discarded)
    if not reset:
        rows = conn.execute(
            "SELECT filename FROM items WHERE person=? AND status != 'discarded'",
            (person_name,)
        ).fetchall()
        for (fn,) in rows:
            existing_filenames.add(fn)

    # Also collect disk contents for comparison
    for scan_dir in [mat_dir, mat_raw, papers_raw]:
        if scan_dir and scan_dir.exists():
            for f in sorted(scan_dir.iterdir()):
                if not f.is_file() or f.name.startswith('_'):
                    continue
                try:
                    content = f.read_text(encoding='utf-8', errors='ignore')
                    existing_contents.append((f.name, content[:DEDUP_PREVIEW_CHARS * 2]))
                except Exception:
                    pass

    # --- Phase 2: Register files with dedup check ---
    def register_file(f: Path, category: str, rel_path: str, source_type_override: str = None):
        """Register a single file, checking for duplicates."""
        if f.name.startswith('_'):
            return
        if f.suffix.lower() not in ('.md', '.txt'):
            return
        if f.name in existing_filenames:
            return  # Already registered

        try:
            content = f.read_text(encoding='utf-8', errors='ignore')
            char_count = len(content)
        except Exception:
            return

        # Dedup: only check against files with DIFFERENT names
        other_contents = [(fn, c) for fn, c in existing_contents if fn != f.name]
        dup = check_dup_against_existing(content, other_contents)
        if dup:
            stats["duplicates"] += 1
            # Move to _duplicates/ and mark in DB
            file_size = char_count  # fallback
            try:
                file_size = f.stat().st_size
            except Exception:
                pass
            dup_dir = mat_dir / "_duplicates"
            dup_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(f), str(dup_dir / f.name))
            except Exception:
                pass
            conn.execute(
                "INSERT OR IGNORE INTO items (person, category, filename, source_type, status, discard_reason, file_size, char_count) "
                "VALUES (?, ?, ?, ?, 'discarded', ?, ?, ?)",
                (person_name, category, f.name, source_type_override or detect_source_type(f.name),
                 f"duplicate of {dup}", file_size, char_count)
            )
            print(f"    🔄 Duplicate: {f.name} ≈ {dup} (moved to _duplicates/)")
            existing_filenames.add(f.name)
            return

        # Register as raw
        source_type = source_type_override or detect_source_type(f.name)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO items (person, category, filename, source_type, status, file_path, file_size, char_count) "
                "VALUES (?, ?, ?, ?, 'raw', ?, ?, ?)",
                (person_name, category, f.name, source_type,
                 rel_path, f.stat().st_size, char_count)
            )
            stats["items_added"] += 1
        except Exception as e:
            print(f"    ⚠ Error inserting {f.name}: {e}")

        existing_filenames.add(f.name)
        existing_contents.append((f.name, content[:DEDUP_PREVIEW_CHARS * 2]))

    # --- Register files from materials/ (root) ---
    if mat_dir.exists():
        for f in sorted(mat_dir.iterdir()):
            if not f.is_file():
                continue
            register_file(f, 'materials', f"materials/{f.name}")

    # --- Register files from materials/raw/ (legacy) ---
    if mat_raw.exists():
        for f in sorted(mat_raw.iterdir()):
            if not f.is_file():
                continue
            register_file(f, 'materials', f"materials/raw/{f.name}")

    # --- Register files from papers/raw/ ---
    if papers_raw.exists():
        for f in sorted(papers_raw.iterdir()):
            if not f.is_file():
                continue
            register_file(f, 'papers', f"papers/raw/{f.name}", source_type_override='paper')

    # --- Register PDFs (mark has_pdf=1) ---
    pdfs_dir = person_dir / "papers" / "pdfs"
    if pdfs_dir.exists():
        for f in sorted(pdfs_dir.glob("*.pdf")):
            conn.execute("UPDATE items SET has_pdf = 1 WHERE person = ? AND filename = ?",
                        (person_name, f.stem + ".txt"))
            conn.execute("UPDATE items SET has_pdf = 1 WHERE person = ? AND filename = ?",
                        (person_name, f.stem + ".md"))
            stats["pdfs_registered"] += 1

    # --- Load papers/_meta.json for doi/year/title ---
    meta_path = person_dir / "papers" / "_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            if isinstance(meta, dict):
                for filename, entry in meta.items():
                    conn.execute("UPDATE items SET doi=?, year=?, title=? WHERE person=? AND filename=?",
                                (entry.get('doi'), entry.get('year'), entry.get('title'), person_name, filename))
                stats["meta_loaded"] = True
            elif isinstance(meta, list):
                for entry in meta:
                    fn = entry.get('filename', '')
                    conn.execute("UPDATE items SET doi=?, year=?, title=? WHERE person=? AND filename=?",
                                (entry.get('doi'), entry.get('year'), entry.get('title'), person_name, fn))
                stats["meta_loaded"] = True
        except Exception as e:
            print(f"    ⚠ Error reading _meta.json: {e}")

    # --- Load _distilled_files.json ---
    distilled_path = person_dir / "_distilled_files.json"
    if distilled_path.exists():
        try:
            distilled = json.loads(distilled_path.read_text(encoding='utf-8'))
            for fn in distilled.get('distilled_files', []):
                conn.execute("UPDATE items SET distilled=1 WHERE person=? AND filename=?", (person_name, fn))
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
                conn.execute(
                    "UPDATE items SET quality_score=?, quality_checked=1, quality_check_time=? WHERE person=? AND filename=?",
                    (score, now, person_name, fn))
        except Exception as e:
            print(f"    ⚠ Error reading _quality_scores.json: {e}")

    # --- Log ---
    conn.execute("INSERT INTO processing_log (person, action, details) VALUES (?, 'init_db', ?)",
                (person_name, json.dumps(stats, ensure_ascii=False)))

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
    total_dups = 0
    for person_dir in persons:
        stats = init_person_db(person_dir, reset=args.reset)
        total_items += stats["items_added"]
        total_dups += stats["duplicates"]
        if stats["items_added"] > 0 or stats["meta_loaded"] or stats["duplicates"] > 0:
            try:
                rel = person_dir.relative_to(BASE_DIR)
            except ValueError:
                rel = person_dir
            dup_str = f", dup={stats['duplicates']}" if stats["duplicates"] else ""
            print(f"  {rel}: items={stats['items_added']}, pdfs={stats['pdfs_registered']}, meta={'✓' if stats['meta_loaded'] else '-'}{dup_str}")

    print("=" * 60)
    print(f"Total items registered: {total_items}, duplicates found: {total_dups}")
    print("Done ✅")


if __name__ == "__main__":
    main()
