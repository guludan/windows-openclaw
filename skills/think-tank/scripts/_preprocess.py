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
"""

import re
import sys
import os
import shutil
from pathlib import Path

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
    """预处理单人物的所有素材"""
    mat_dir = person_dir / "materials"
    if not mat_dir.exists():
        return {"deleted": 0, "cleaned": 0, "errors": []}

    stats = {"deleted": 0, "cleaned": 0, "errors": []}
    total_savings = 0

    for f in sorted(mat_dir.glob("*")):
        if f.name.startswith('_'):  # 跳过 meta 文件
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
            if not dry_run:
                f.unlink()
            stats["deleted"] += 1
            stats["errors"].append(f"{f.name}: too small ({len(content)}B), deleted")
            continue

        # 2. 纯错误/404页面删除
        if is_error_page(content):
            if not dry_run:
                f.unlink()
            stats["deleted"] += 1
            stats["errors"].append(f"{f.name}: error/404 page, deleted")
            continue

        # 3. 清理噪音
        cleaned = clean_text(content)
        savings = len(content) - len(cleaned)

        if savings > 100:  # 只在节省超过100B时重写
            if not dry_run:
                f.write_text(cleaned, encoding='utf-8')
            stats["cleaned"] += 1
            total_savings += savings
            stats["errors"].append(f"{f.name}: {len(content)}B → {len(cleaned)}B (-{savings}B)")

    if total_savings > 0:
        print(f"  Total cleaned: {total_savings:,}B", flush=True)

    return stats


def main():
    import argparse
    parser = argparse.ArgumentParser(description="素材预处理")
    parser.add_argument("person_dir", nargs="?", help="人物目录路径")
    parser.add_argument("--all", action="store_true", help="处理所有人物")
    parser.add_argument("--dry-run", action="store_true", help="预览不写入")
    args = parser.parse_args()

    if args.all:
        total_deleted = 0
        total_cleaned = 0
        total_errors = []

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

                    name = person_dir.name
                    print(f"[{group_dir.name}/{sub_dir.name}] {name}...", flush=True)
                    result = preprocess_person(person_dir, dry_run=args.dry_run)
                    total_deleted += result["deleted"]
                    total_cleaned += result["cleaned"]
                    total_errors.extend([f"  {name}: {e}" for e in result["errors"]])

        print(f"\n{'='*60}")
        print(f"Dry run: {args.dry_run}")
        print(f"Total deleted: {total_deleted} files")
        print(f"Total cleaned: {total_cleaned} files")
        if total_errors:
            print(f"\nDetails:")
            for e in total_errors:
                print(e)

    elif args.person_dir:
        person_dir = Path(args.person_dir)
        if not person_dir.exists():
            print(f"ERROR: {person_dir} not found", file=sys.stderr)
            sys.exit(1)
        print(f"Processing: {person_dir.name} (dry_run={args.dry_run})", flush=True)
        result = preprocess_person(person_dir, dry_run=args.dry_run)
        print(f"\nDeleted: {result['deleted']} files")
        print(f"Cleaned: {result['cleaned']} files")
        for e in result['errors']:
            print(f"  {e}")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
