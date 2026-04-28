#!/usr/bin/env python3
"""
migrate_structure.py — 素材目录结构迁移脚本

将混乱的 materials/papers/ 目录重组为标准化结构:
  materials/raw/         — 非论文素材
  papers/raw/            — 论文素材
  papers/distillates/    — 蒸馏产物
  papers/pdfs/           — PDF原文

用法:
    python3 migrate_structure.py --dry-run              # 预览所有变更
    python3 migrate_structure.py                        # 执行迁移
    python3 migrate_structure.py --person <dir>         # 只处理单个人物
    python3 migrate_structure.py --person <dir> --dry-run
"""

import argparse
import shutil
from pathlib import Path

BASE_DIR = Path("/mnt/d/data/think-tank-data/personalities")


def iter_persons():
    """遍历所有人物目录（3层嵌套: group/subcategory/person）"""
    for group_dir in sorted(BASE_DIR.iterdir()):
        if not group_dir.is_dir() or group_dir.name.startswith('.') or group_dir.name == 'name_map.json':
            continue
        for sub_dir in sorted(group_dir.iterdir()):
            if not sub_dir.is_dir():
                continue
            # Check if sub_dir has materials/ directly (it IS a person dir)
            if (sub_dir / "materials").exists():
                yield sub_dir
                continue
            # Otherwise it's a subcategory, go deeper
            for person_dir in sorted(sub_dir.iterdir()):
                if not person_dir.is_dir():
                    continue
                if (person_dir / "materials").exists() or (person_dir / "papers").exists():
                    yield person_dir


def migrate_person(person_dir: Path, dry_run: bool = False) -> dict:
    """对单个人物执行目录迁移"""
    stats = {"moved": 0, "created_dirs": 0, "cleaned_dirs": 0, "errors": []}
    name = person_dir.name
    moved_files = set()  # Track moved files for dry-run accuracy

    def do_move(src: Path, dst: Path):
        if dry_run:
            if src.name in moved_files:
                return  # Already moved in a previous step (dry-run)
            moved_files.add(src.name)
            stats["moved"] += 1
            print(f"    [DRY] {src.relative_to(person_dir)} → {dst.relative_to(person_dir)}")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            stats["moved"] += 1

    def do_mkdir(p: Path):
        if not p.exists():
            if dry_run:
                print(f"    [DRY] mkdir {p.relative_to(person_dir)}")
            else:
                p.mkdir(parents=True, exist_ok=True)
            stats["created_dirs"] += 1

    mat_dir = person_dir / "materials"
    papers_dir = person_dir / "papers"

    # --- Step 1: Move paper_*.txt from materials/ to papers/raw/ ---
    if mat_dir.exists():
        paper_files = [f for f in mat_dir.glob("paper_*.[tm][xd]t") if f.is_file()]
        if paper_files:
            papers_raw = papers_dir / "raw"
            do_mkdir(papers_raw)
            for f in paper_files:
                do_move(f, papers_raw / f.name)

    # --- Step 2: Move materials/_papers_meta.json → papers/_meta.json ---
    if mat_dir.exists():
        meta_src = mat_dir / "_papers_meta.json"
        if meta_src.exists():
            do_mkdir(papers_dir)
            do_move(meta_src, papers_dir / "_meta.json")

    # --- Step 3: Move non-underscore .txt files from materials/ to materials/raw/ ---
    if mat_dir.exists():
        raw_dir = mat_dir / "raw"
        txt_files = [f for f in mat_dir.iterdir()
                     if f.is_file() and f.suffix in ('.txt', '.md') and not f.name.startswith('_')
                     and f.name not in moved_files]
        if txt_files:
            do_mkdir(raw_dir)
            for f in txt_files:
                do_move(f, raw_dir / f.name)

    # --- Step 4: Move papers/texts/*.txt → papers/raw/ ---
    texts_dir = papers_dir / "texts"
    if texts_dir.exists():
        txt_files = list(texts_dir.glob("*.[tm][xd]t"))
        if txt_files:
            papers_raw = papers_dir / "raw"
            do_mkdir(papers_raw)
            for f in txt_files:
                dst = papers_raw / f.name
                if not dst.exists():
                    do_move(f, dst)
                else:
                    stats["errors"].append(f"skip {f.name}: already exists in papers/raw/")

    # --- Step 5: Move papers/extracted/*.json → papers/distillates/ ---
    extracted_dir = papers_dir / "extracted"
    if extracted_dir.exists():
        json_files = list(extracted_dir.glob("*.json"))
        if json_files:
            dist_dir = papers_dir / "distillates"
            do_mkdir(dist_dir)
            for f in json_files:
                do_move(f, dist_dir / f.name)

    # --- Step 6: Move papers/aggregation.json → papers/_aggregation.json ---
    agg_src = papers_dir / "aggregation.json"
    if agg_src.exists():
        do_move(agg_src, papers_dir / "_aggregation.json")

    # --- Step 7: Clean empty directories ---
    for empty_candidate in [texts_dir, extracted_dir]:
        if empty_candidate and empty_candidate.exists():
            try:
                remaining = list(empty_candidate.iterdir())
                if not remaining:
                    if dry_run:
                        print(f"    [DRY] rmdir {empty_candidate.relative_to(person_dir)}")
                    else:
                        empty_candidate.rmdir()
                    stats["cleaned_dirs"] += 1
            except OSError:
                pass

    return stats


def main():
    parser = argparse.ArgumentParser(description="素材目录结构迁移")
    parser.add_argument("--dry-run", action="store_true", help="预览变更不执行")
    parser.add_argument("--person", metavar="DIR", help="只处理指定人物目录")
    args = parser.parse_args()

    if args.person:
        person_dir = Path(args.person)
        if not person_dir.is_dir():
            # Try to find it under BASE_DIR
            candidates = list(BASE_DIR.rglob(args.person))
            if not candidates:
                print(f"ERROR: {args.person} not found", file=__import__('sys').stderr)
                __import__('sys').exit(1)
            person_dir = candidates[0]
        persons = [person_dir]
    else:
        persons = list(iter_persons())

    print(f"Found {len(persons)} person directories")
    print(f"Dry run: {args.dry_run}")
    print("=" * 60)

    total_stats = {"moved": 0, "created_dirs": 0, "cleaned_dirs": 0, "persons_processed": 0}

    for person_dir in persons:
        stats = migrate_person(person_dir, dry_run=args.dry_run)
        if stats["moved"] > 0 or stats["created_dirs"] > 0:
            print(f"  {person_dir.relative_to(BASE_DIR)}: moved={stats['moved']}, dirs={stats['created_dirs']}, cleaned={stats['cleaned_dirs']}")
            for e in stats["errors"]:
                print(f"    ⚠ {e}")
        total_stats["moved"] += stats["moved"]
        total_stats["created_dirs"] += stats["created_dirs"]
        total_stats["cleaned_dirs"] += stats["cleaned_dirs"]
        total_stats["persons_processed"] += 1

    print("=" * 60)
    print(f"Processed: {total_stats['persons_processed']} persons")
    print(f"Total moved: {total_stats['moved']}")
    print(f"Total dirs created: {total_stats['created_dirs']}")
    print(f"Total dirs cleaned: {total_stats['cleaned_dirs']}")
    if args.dry_run:
        print("\n⚠ DRY RUN — no changes were made")


if __name__ == "__main__":
    main()
