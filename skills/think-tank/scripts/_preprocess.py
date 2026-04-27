#!/usr/bin/env python3
"""
_preprocess.py — 素材预处理（去噪、去HTML垃圾、去重）

用法:
    python3 _preprocess.py <person_dir>              # 处理单人物
    python3 _preprocess.py <person_dir> --dry-run     # 预览不写入
    python3 _preprocess.py --all                      # 处理所有人物

功能:
    1. 删除空文件 / 极小文件 (<200B)
    2. 删除纯404/错误页面
    3. 清理 HTML 导航噪音（jina.ai / Playwright 常见格式）
    4. 清理页脚噪音（Copyright、Privacy、Cookie 等）
    5. 去除纯导航菜单行（短链接列表）
    6. 相似度去重（--quality / --dedup-only）
    7. 低质量检测（--quality）
    8. 质量评估报告生成（--quality）
"""

import re
import sys
import os
import json
import sqlite3
import shutil
from pathlib import Path
from collections import Counter

BASE_DIR = Path("/mnt/d/data/think-tank-data/personalities")


# === 噪音模式 ===

# 纯404/错误页面
ERROR_PATTERNS = [
    r'^404\b',
    r'^page you are looking for',
    r'^the link may be broken',
    r'^the address may be mistyped',
    r'^go home',
    r'^error \d{3}',
    r'^access denied',
    r'^not found',
]

# 页脚/导航噪音行（整行匹配）
NOISE_LINE_PATTERNS = [
    r'^\*?\s*\[.*?\]\(https?://',       # [text](url) 导航链接
    r'^Image \d+:',                       # Image 标记
    r'^\*?\s*\[\]',                      # 空复选框
    r'^Copyright\b',
    r'^\s*©\s*\d',
    r'^Privacy\b',
    r'^Cookie\b',
    r'^Terms of Service',
    r'^All rights reserved',
    r'^Sign [Uu]p\b',
    r'^Log [Ii]n\b',
    r'^Request a demo',
    r'^Join the Newsletter',
    r'^First Name\s*$',
    r'^Last Name\s*$',
    r'^Email\s*$',
    r'^\* \* \*$',
    r'^---$',
    r'^\*\*\*$',
    r'^Platform$',
    r'^FeaturesPricing',
    r'^Partners$',
    r'^Nord Security',
    r'^Advertising Contacts',
    r'^Event Calendar',
    r'^Contact Us$',
    r'^Licensing$',
    r'^Working With Us',
    r'^Want a free',
    r'^Get in touch',
    r'^Need an Event Speaker',
    r'^Social Engineering Training',
    r'^Join the Newsletter',
    r'^Possible reasons',
    r'^Go home',
    r'^Business VPN',
]


def is_error_page(text: str) -> bool:
    """判断是否是纯错误/404页面"""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) < 5:
        return any(re.search(p, text, re.IGNORECASE) for p in ERROR_PATTERNS)
    return False


def clean_text(text: str) -> str:
    """清理单篇素材的噪音"""
    lines = text.split('\n')
    clean = []

    for line in lines:
        stripped = line.strip()

        # 空行
        if not stripped:
            continue

        # 导航/页脚噪音
        is_noise = False
        for pattern in NOISE_LINE_PATTERNS:
            if re.match(pattern, stripped, re.IGNORECASE):
                is_noise = True
                break
        if is_noise:
            continue

        clean.append(stripped)

    return '\n'.join(clean)


def preprocess_person(person_dir: Path, dry_run: bool = False) -> dict:
    """预处理单人物的所有素材（从 materials/raw/ 读取）"""
    mat_dir = person_dir / "materials"
    raw_dir = mat_dir / "raw"
    if not raw_dir.exists():
        raw_dir = mat_dir
    if not raw_dir.exists():
        return {"deleted": 0, "cleaned": 0, "errors": []}

    stats = {"deleted": 0, "cleaned": 0, "errors": []}
    total_savings = 0

    # Open DB if exists
    db_path = person_dir / "_db.sqlite"
    db_conn = None
    try:
        if db_path.exists():
            db_conn = sqlite3.connect(str(db_path))
    except Exception:
        db_conn = None

    deleted_files = []

    for f in sorted(raw_dir.glob("*")):
        if f.name.startswith('_'):
            continue
        if not f.is_file():
            continue

        try:
            content = f.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            stats["errors"].append(f"{f.name}: read error: {e}")
            continue

        # 1. 极小文件删除
        if len(content) < 200:
            reason = f"too small ({len(content)}B)"
            if not dry_run:
                f.unlink()
                deleted_files.append((f.name, reason))
            stats["deleted"] += 1
            stats["errors"].append(f"{f.name}: {reason}, deleted")
            continue

        # 2. 纯错误/404页面删除
        if is_error_page(content):
            reason = "error/404 page"
            if not dry_run:
                f.unlink()
                deleted_files.append((f.name, reason))
            stats["deleted"] += 1
            stats["errors"].append(f"{f.name}: {reason}, deleted")
            continue

        # 3. 清理噪音
        cleaned = clean_text(content)
        savings = len(content) - len(cleaned)

        if savings > 100:
            if not dry_run:
                f.write_text(cleaned, encoding='utf-8')
            stats["cleaned"] += 1
            total_savings += savings
            stats["errors"].append(f"{f.name}: {len(content)}B → {len(cleaned)}B (-{savings}B)")

    # Update DB for deleted files
    if db_conn and deleted_files and not dry_run:
        try:
            for fname, reason in deleted_files:
                db_conn.execute(
                    "UPDATE items SET status='discarded', discard_reason=? WHERE filename=?",
                    (reason, fname)
                )
            db_conn.commit()
        except Exception:
            pass
        finally:
            db_conn.close()
    elif db_conn:
        db_conn.close()

    if total_savings > 0:
        print(f"  Total cleaned: {total_savings:,}B", flush=True)

    return stats


# === 新增：相似度去重 ===

def jaccard_similarity(text_a: str, text_b: str) -> float:
    """基于词集合的Jaccard相似度，取前500字比较"""
    a = set(text_a[:500].split())
    b = set(text_b[:500].split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def dedup_person(person_dir: Path, dry_run: bool = False) -> dict:
    """相似度去重：>0.85的保留较长的，其余移到_duplicates/"""
    mat_dir = person_dir / "materials"
    raw_dir = mat_dir / "raw"
    if not raw_dir.exists():
        raw_dir = mat_dir
    if not raw_dir.exists():
        return {"duplicates": 0, "errors": []}

    stats = {"duplicates": 0, "errors": []}
    files = sorted(
        [f for f in raw_dir.glob("*") if f.is_file() and not f.name.startswith('_')],
        key=lambda f: f.stat().st_size, reverse=True
    )

    if not files:
        return stats

    # 读取所有文件内容
    contents = {}
    for f in files:
        try:
            contents[f] = f.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            contents[f] = ""

    dup_dir = mat_dir / "_duplicates"
    db_path = person_dir / "_db.sqlite"
    conn = sqlite3.connect(str(db_path)) if db_path.exists() else None
    removed = set()

    for i, fa in enumerate(files):
        if fa in removed:
            continue
        for fb in files[i+1:]:
            if fb in removed:
                continue
            sim = jaccard_similarity(contents[fa], contents[fb])
            if sim > 0.85:
                # fa更长（已按size降序），移除fb
                victim = fb
                removed.add(victim)
                stats["duplicates"] += 1
                stats["errors"].append(f"{victim.name}: duplicate of {fa.name} (sim={sim:.2f})")
                if not dry_run:
                    dup_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(victim), str(dup_dir / victim.name))
                    if conn:
                        conn.execute("UPDATE items SET status='discarded', discard_reason='duplicate' WHERE filename=?", (victim.name,))

    if conn:
        conn.commit()
        conn.close()
    return stats


# === 新增：低质量检测 ===

LOW_QUALITY_TEXT_RATIO = 0.30


def is_index_page(text: str) -> bool:
    """纯目录页/索引页检测：连续>5行[text](url)且正文段落<100字"""
    md_link_pattern = re.compile(r'^\s*\*?\s*\[.+?\]\(https?://[^)]+\)')
    consecutive_links = 0
    prose_chars = 0

    for line in text.split('\n'):
        if md_link_pattern.match(line):
            consecutive_links += 1
        else:
            consecutive_links = 0
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and not stripped.startswith('|'):
                prose_chars += len(stripped)

        if consecutive_links > 5 and prose_chars < 100:
            return True

    return False


def effective_text_ratio(text: str) -> float:
    """实质性文本占比（去HTML标签、空行后）"""
    # 去HTML标签
    clean = re.sub(r'<[^>]+>', '', text)
    # 去markdown链接只保留文字
    clean = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', clean)
    # 去噪音符号
    clean = re.sub(r'[#*\-_=>|`~]', '', clean)
    # 有效文字（非空白）
    effective = len(clean.replace('\n', '').replace(' ', ''))
    total = len(text.replace('\n', '').replace(' ', ''))
    if total == 0:
        return 0.0
    return effective / total


def low_quality_check(person_dir: Path, dry_run: bool = False) -> dict:
    """低质量检测：文本比例低或索引页移到_low_quality/"""
    mat_dir = person_dir / "materials"
    raw_dir = mat_dir / "raw"
    if not raw_dir.exists():
        raw_dir = mat_dir
    if not raw_dir.exists():
        return {"low_quality": 0, "errors": []}

    stats = {"low_quality": 0, "errors": []}
    lq_dir = mat_dir / "_low_quality"

    # Open DB if exists
    db_path = person_dir / "_db.sqlite"
    db_conn = None
    try:
        if db_path.exists():
            db_conn = sqlite3.connect(str(db_path))
    except Exception:
        db_conn = None

    moved_files = []

    for f in sorted(raw_dir.glob("*")):
        if not f.is_file() or f.name.startswith('_'):
            continue
        try:
            content = f.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue

        reason = None
        if effective_text_ratio(content) < LOW_QUALITY_TEXT_RATIO:
            reason = f"low text ratio ({effective_text_ratio(content):.0%})"
        elif is_index_page(content):
            reason = "index/navigation page"

        if reason:
            stats["low_quality"] += 1
            stats["errors"].append(f"{f.name}: {reason}")
            if not dry_run:
                lq_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(lq_dir / f.name))
                moved_files.append((f.name, reason))

    # Update DB for moved files
    if db_conn and moved_files and not dry_run:
        try:
            for fname, reason in moved_files:
                db_conn.execute(
                    "UPDATE items SET status='discarded', discard_reason=? WHERE filename=?",
                    (reason, fname)
                )
            db_conn.commit()
        except Exception:
            pass
        finally:
            db_conn.close()
    elif db_conn:
        db_conn.close()

    return stats


# === 新增：质量评估报告 ===

def detect_source_type(filename: str) -> str:
    """从文件名判断来源类型"""
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
    return 'unknown'


def generate_quality_report(person_dir: Path, dry_run: bool = False) -> dict:
    """生成质量评估报告JSON"""
    mat_dir = person_dir / "materials"
    raw_dir = mat_dir / "raw"
    if not raw_dir.exists():
        raw_dir = mat_dir
    if not raw_dir.exists():
        return {"report_entries": 0, "errors": []}

    report = []
    for f in sorted(raw_dir.glob("*")):
        if not f.is_file() or f.name.startswith('_'):
            continue
        try:
            content = f.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue

        report.append({
            "filename": f.name,
            "size": f.stat().st_size,
            "chars": len(content),
            "preview": content[:200].replace('\n', ' ').strip(),
            "source_type": detect_source_type(f.name),
        })

    if not dry_run:
        report_path = mat_dir / "_quality_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    return {"report_entries": len(report), "errors": []}


def quality_pipeline(person_dir: Path, dry_run: bool = False) -> dict:
    """执行完整质量检测pipeline"""
    stats = {"duplicates": 0, "low_quality": 0, "report_entries": 0, "errors": []}

    r1 = dedup_person(person_dir, dry_run)
    stats["duplicates"] = r1["duplicates"]
    stats["errors"].extend(r1["errors"])

    r2 = low_quality_check(person_dir, dry_run)
    stats["low_quality"] = r2["low_quality"]
    stats["errors"].extend(r2["errors"])

    r3 = generate_quality_report(person_dir, dry_run)
    stats["report_entries"] = r3["report_entries"]
    stats["errors"].extend(r3["errors"])

    return stats


def main():
    import argparse
    parser = argparse.ArgumentParser(description="素材预处理")
    parser.add_argument("person_dir", nargs="?", help="人物目录路径")
    parser.add_argument("--all", action="store_true", help="处理所有人物")
    parser.add_argument("--dry-run", action="store_true", help="预览不写入")
    parser.add_argument("--quality", action="store_true", help="执行全部质量检测（去重+低质量+报告）")
    parser.add_argument("--dedup-only", action="store_true", help="只做相似度去重")
    args = parser.parse_args()

    def _iter_persons():
        """遍历所有人物目录"""
        for group_dir in sorted(BASE_DIR.iterdir()):
            if not group_dir.is_dir():
                continue
            for sub_dir in sorted(group_dir.iterdir()):
                if not sub_dir.is_dir():
                    continue
                for person_dir in sorted(sub_dir.iterdir()):
                    if not person_dir.is_dir():
                        continue
                    if not (person_dir / "materials").exists():
                        continue
                    yield person_dir, group_dir.name, sub_dir.name

    # 选择处理模式
    do_preprocess = not (args.quality or args.dedup_only)
    do_dedup = args.dedup_only or args.quality
    do_lq = args.quality
    do_report = args.quality

    if args.all:
        # --all 模式
        for person_dir, gname, sname in _iter_persons():
            name = person_dir.name
            print(f"[{gname}/{sname}] {name}...", flush=True)

            if do_preprocess:
                result = preprocess_person(person_dir, dry_run=args.dry_run)
                print(f"  deleted={result['deleted']}, cleaned={result['cleaned']}", flush=True)
            if do_dedup:
                r = dedup_person(person_dir, dry_run=args.dry_run)
                print(f"  duplicates={r['duplicates']}", flush=True)
            if do_lq:
                r = low_quality_check(person_dir, dry_run=args.dry_run)
                print(f"  low_quality={r['low_quality']}", flush=True)
            if do_report:
                r = generate_quality_report(person_dir, dry_run=args.dry_run)
                print(f"  report_entries={r['report_entries']}", flush=True)

        print(f"\n{'='*60}")
        print(f"Dry run: {args.dry_run}")

    elif args.person_dir:
        person_dir = Path(args.person_dir)
        if not person_dir.exists():
            print(f"ERROR: {person_dir} not found", file=sys.stderr)
            sys.exit(1)

        print(f"Processing: {person_dir.name} (dry_run={args.dry_run})", flush=True)

        if do_preprocess:
            result = preprocess_person(person_dir, dry_run=args.dry_run)
            print(f"Deleted: {result['deleted']}, Cleaned: {result['cleaned']}")
            for e in result['errors']:
                print(f"  {e}")

        if do_dedup:
            r = dedup_person(person_dir, dry_run=args.dry_run)
            print(f"Duplicates removed: {r['duplicates']}")
            for e in r['errors']:
                print(f"  {e}")

        if do_lq:
            r = low_quality_check(person_dir, dry_run=args.dry_run)
            print(f"Low quality: {r['low_quality']}")
            for e in r['errors']:
                print(f"  {e}")

        if do_report:
            r = generate_quality_report(person_dir, dry_run=args.dry_run)
            print(f"Report entries: {r['report_entries']}")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
