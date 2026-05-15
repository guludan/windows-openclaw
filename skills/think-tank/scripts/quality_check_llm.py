#!/usr/bin/env python3
"""
quality_check_llm.py — LLM质量评分工具

用法:
    python3 quality_check_llm.py <person_dir>                    # 生成评分prompt
    python3 quality_check_llm.py <person_dir> --apply <scores>   # 应用评分结果
    python3 quality_check_llm.py <person_dir> --apply-from-file  # 从_quality_scores.json应用

流程:
    1. 读取 _quality_report.json（由_preprocess.py --quality 生成）
    2. 生成 _quality_prompt.md（给LLM的评分指令）
    3. 主session通过 sessions_spawn 调LLM处理prompt，返回JSON评分
    4. 评分写入 _quality_scores.json
    5. --apply 将action=discard的文件移到 _discarded/
"""

import json
import sqlite3
import sys
import shutil
from datetime import datetime
from pathlib import Path


def generate_prompt(person_dir: Path, batch_size: int = 8) -> list:
    """生成LLM评分prompt文件，按批次分段，避免超出上下文
    
    返回prompt文件路径列表，每个文件包含一批素材的评分指令。
    """
    report_path = person_dir / "materials" / "_quality_report.json"
    if not report_path.exists():
        print(f"ERROR: {report_path} not found. Run _preprocess.py --quality first.", file=sys.stderr)
        sys.exit(1)

    with open(report_path, 'r', encoding='utf-8') as f:
        report = json.load(f)

    if not report:
        print(f"WARNING: empty report for {person_dir.name}", file=sys.stderr)
        return []

    person_name = person_dir.name

    # 加载名称映射，获取别名（用于评分时识别中英文名素材）
    name_map_path = Path("/mnt/d/data/think-tank-data/personalities/name_map.json")
    aliases = []
    if name_map_path.exists():
        with open(name_map_path, 'r', encoding='utf-8') as f:
            name_map = json.load(f)
        if person_name in name_map:
            entry = name_map[person_name]
            if entry.get("zh"):
                aliases.append(entry["zh"])
            if entry.get("en"):
                aliases.append(entry["en"])
        else:
            for key, entry in name_map.items():
                if entry.get("en", "").lower() == person_name.lower():
                    aliases.append(key)
                    if entry.get("zh"):
                        aliases.append(entry["zh"])
                    break
    alias_note = f"\n别名: {', '.join(aliases)}" if aliases else ""

    # 跳过已评分的文件，避免重复
    scores_path = person_dir / 'materials' / '_quality_scores.json'
    already_scored = set()
    if scores_path.exists():
        try:
            with open(scores_path, 'r', encoding='utf-8') as f:
                already_scored = {s['filename'] for s in json.load(f)}
        except:
            pass

    # 过滤掉已评分的 和 磁盘上不存在的文件
    mat_dir = person_dir / 'materials'
    existing_files = set()
    for d in ['raw', '']:  # materials/raw/ and materials/
        scan = mat_dir / d if d else mat_dir
        if scan.exists():
            existing_files.update(f.name for f in scan.iterdir() if f.is_file() and not f.name.startswith('_'))
    papers_dir = person_dir / 'papers'
    for d in ['raw', '']:
        scan = papers_dir / d if d else papers_dir
        if scan.exists():
            existing_files.update(f.name for f in scan.iterdir() if f.is_file() and not f.name.startswith('_'))
    
    report = [item for item in report if item['filename'] not in already_scored and item['filename'] in existing_files]
    if not report:
        print(f'All items already scored for {person_dir.name}, skipping.', flush=True)
        return []

    type_counts = {}
    for item in report:
        t = item['source_type']
        type_counts[t] = type_counts.get(t, 0) + 1

    # 从模板文件读取prompt头
    template_path = Path("~/.openclaw/workspace/skills/think-tank/templates/quality_score_prompt.md").expanduser()
    if template_path.exists():
        template = template_path.read_text(encoding='utf-8')
        all_names = ', '.join([person_name] + aliases)
        alias_str = ', '.join(aliases)
        # 替换模板变量
        header_text = template.replace('{person_name}', person_name) \
            .replace('{alias_note}', f'别名: {alias_str}' if aliases else '') \
            .replace('{all_names}', all_names) \
            .replace('{materials_list}', '')  # 素材列表在分批时追加
        header_lines = header_text.split('\n')
    else:
        # 兜底：模板不存在时用硬编码
        header_lines = [
            f"# 素材质量评分任务",
            f"",
            f"人物: {person_name}{alias_note}",
            f"注意: 评分相关性时，该人物的所有别名都算相关：{', '.join([person_name] + aliases)}",
            f"素材总数: {len(report)}",
            f"来源分布: {json.dumps(type_counts)}",
            f"",
            f"## 评分标准",
            f"- **5分**: 高质量学术论文全文/核心著作，包含原创观点、数据或理论",
            f"- **4分**: 有实质内容的文章/书评/访谈，信息密度较高",
            f"- **3分**: 一般性介绍/概述，有一定参考价值但信息密度一般",
            f"- **2分**: 内容重复/转载/摘要页，信息量低",
            f"- **1分**: 无关内容/广告/错误页面/纯导航页",
            f"",
            f"## 特殊说明",
            f"- 书籍文件(字数>50000)只提供目录+前言+章节开头预览，不需要全文。只要确认是正确作者的著作即可给高分",
            f"- 只根据预览内容判断相关性和质量，不要因为缺少全文而扣分"
            f"",
            f"## 判断维度",
            f"1. **相关性**: 内容是否真正关于{person_name}（而非仅仅提到名字）",
            f"2. **信息密度**: 是否包含具体观点、数据、分析，还是泛泛而谈",
            f"3. **学术价值**: 是否包含可被蒸馏的深层逻辑或独特见解",
            f"4. **原创性**: 是否为原创内容（非转载/重复/洗稿）",
            f"",
            f"## 输出格式",
            f"严格输出JSON数组，不要输出任何其他文字：",
            f"[",
            f'  {{"filename": "xxx.txt", "score": 4, "reason": "简短理由", "action": "keep"}},',
            f'  {{"filename": "yyy.txt", "score": 1, "reason": "无关内容", "action": "discard"}}',
            f"]",
            f"",
            f"注意: score<=2 的设为 discard，score>=3 的设为 keep",
        ]

    # 分批
    batches = []
    for i in range(0, len(report), batch_size):
        batch = report[i:i+batch_size]
        batch_lines = header_lines + [
            f"",
            f"## 本批次 ({i+1}-{min(i+batch_size, len(report))}/{len(report)})",
            f"",
        ]
        for j, item in enumerate(batch):
            batch_lines.append(f"### [{i+j+1}] {item['filename']}")
            batch_lines.append(f"- 来源: {item['source_type']} | 大小: {item['size']}B | 字数: {item['chars']}")
            batch_lines.append(f"- 预览: {item['preview']}")
            batch_lines.append("")

        batch_num = i // batch_size
        prompt_path = person_dir / "materials" / f"_quality_prompt_{batch_num}.md"
        prompt_path.write_text('\n'.join(batch_lines), encoding='utf-8')
        batches.append(prompt_path)
        print(f"Batch {batch_num}: {len(batch)} entries → {prompt_path}", flush=True)

    print(f"Total: {len(batches)} batches for {len(report)} entries", flush=True)
    return batches


def normalize_filename(name: str) -> str:
    """Normalize filename for fuzzy matching: replace Chinese/smart quotes, \xa0, etc."""
    import unicodedata
    # Replace smart/Chinese quotes with ASCII quotes
    for ch in ['\u201c', '\u201d', '\u2018', '\u2019', '\u300c', '\u300d']:
        name = name.replace(ch, '"')
    # Replace Chinese dash with ASCII dash
    name = name.replace('\u2014', '-').replace('\u2013', '-')
    # Replace non-breaking space
    name = name.replace('\xa0', ' ')
    # Normalize unicode
    name = unicodedata.normalize('NFC', name)
    return name


def find_file_fuzzy(filename: str, search_dirs: list) -> Path | None:
    """Find file with exact match first, then normalized match, then prefix match."""
    # Exact match
    for d in search_dirs:
        if d.exists():
            p = d / filename
            if p.exists():
                return p
    # Normalized match
    norm_target = normalize_filename(filename)
    for d in search_dirs:
        if d.exists():
            for f in d.iterdir():
                if normalize_filename(f.name) == norm_target:
                    return f
    # Prefix match (WSL truncates filenames at 255 bytes)
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    norm_stem = normalize_filename(stem)
    for d in search_dirs:
        if d.exists():
            for f in d.iterdir():
                if f.suffix == suffix and normalize_filename(f.stem).startswith(norm_stem):
                    return f
    return None


def apply_scores(person_dir: Path, scores: list) -> dict:
    """根据LLM评分结果移除低质量素材，写入数据库"""
    mat_dir = person_dir / "materials"
    discard_dir = mat_dir / "_discarded"
    discard_dir.mkdir(parents=True, exist_ok=True)

    stats = {"kept": 0, "discarded": 0, "missing": 0, "errors": []}
    now = datetime.now().isoformat()

    # Update DB
    db_path = person_dir / "_db.sqlite"
    conn = sqlite3.connect(str(db_path)) if db_path.exists() else None

    # 合并已有评分（多批次追加）— still write JSON for backwards compat
    scores_path = mat_dir / "_quality_scores.json"
    existing = []
    if scores_path.exists():
        with open(scores_path, 'r', encoding='utf-8') as f:
            existing = json.load(f)
    all_scores = {s['filename']: s for s in existing}
    for s in scores:
        all_scores[s['filename']] = s
    merged = list(all_scores.values())

    for item in scores:
        filename = item.get("filename", "")
        action = item.get("action", "keep")
        score = item.get("score", 3)

        # Look in materials/raw/, materials/, and papers/raw/ with fuzzy match
        search_dirs = [mat_dir / "raw", mat_dir, person_dir / "papers" / "raw"]
        fpath = find_file_fuzzy(filename, search_dirs)
        if not fpath:
            # Check if already in _discarded/
            discarded_dir = mat_dir / "_discarded"
            if discarded_dir.exists():
                discarded_files = set(f.name for f in discarded_dir.iterdir() if f.is_file())
                if filename in discarded_files or normalize_filename(filename) in {normalize_filename(n) for n in discarded_files}:
                    continue  # already discarded, skip
            stats["missing"] += 1
            stats["errors"].append(f"{filename}: file not found")
            continue

        if action == "discard" or score <= 2:
            shutil.move(str(fpath), str(discard_dir / filename))
            stats["discarded"] += 1
            stats["errors"].append(f"{filename}: discarded (score={score}, {item.get('reason', '')})")
            if conn:
                conn.execute("UPDATE items SET status='discarded', quality_score=?, quality_checked=1, quality_check_time=?, discard_reason=? WHERE filename=?",
                             (score, now, item.get('reason', ''), filename))
        else:
            stats["kept"] += 1
            if conn:
                conn.execute("UPDATE items SET status='quality_checked', quality_score=?, quality_checked=1, quality_check_time=? WHERE filename=?",
                             (score, now, filename))

    # 保存合并后的全部评分（兼容）
    scores_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding='utf-8')

    if conn:
        conn.commit()
        conn.close()

    return stats


def main():
    import argparse
    parser = argparse.ArgumentParser(description="LLM素材质量评分")
    parser.add_argument("person_dir", help="人物目录路径")
    parser.add_argument("--apply-from-file", action="store_true",
                        help="从_quality_scores.json应用评分结果")
    parser.add_argument("--apply", metavar="JSON_STR",
                        help="直接应用JSON评分字符串（从LLM返回的结果）")
    args = parser.parse_args()

    person_dir = Path(args.person_dir)
    if not person_dir.exists():
        print(f"ERROR: {person_dir} not found", file=sys.stderr)
        sys.exit(1)

    if args.apply_from_file:
        scores_path = person_dir / "materials" / "_quality_scores.json"
        if not scores_path.exists():
            print(f"ERROR: {scores_path} not found", file=sys.stderr)
            sys.exit(1)
        with open(scores_path, 'r', encoding='utf-8') as f:
            scores = json.load(f)
        stats = apply_scores(person_dir, scores)
        print(f"Applied: kept={stats['kept']}, discarded={stats['discarded']}, missing={stats['missing']}")
        for e in stats['errors']:
            print(f"  {e}")

        # After apply, regenerate report and prompts (files may have been moved to _discarded)
        if stats['discarded'] > 0:
            try:
                import subprocess
                pp_script = str(Path(__file__).parent / '_preprocess.py')
                for p in list((person_dir / 'materials').glob('_quality_prompt_*.md')):
                    p.unlink()
                report_path = person_dir / 'materials' / '_quality_report.json'
                if report_path.exists():
                    report_path.unlink()
                subprocess.run([sys.executable, pp_script, str(person_dir), '--quality'],
                             capture_output=True, timeout=60)
                print(f"Regenerated report after apply.")
            except Exception as e:
                print(f"  Warning: could not regenerate report: {e}")

    elif args.apply:
        try:
            # LLM可能返回markdown包裹的JSON，提取纯JSON
            json_str = args.apply.strip()
            if json_str.startswith("```"):
                lines = json_str.split('\n')
                lines = [l for l in lines if not l.strip().startswith("```")]
                json_str = '\n'.join(lines).strip()
            scores = json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"ERROR: Failed to parse JSON: {e}", file=sys.stderr)
            sys.exit(1)
        stats = apply_scores(person_dir, scores)
        print(f"Applied: kept={stats['kept']}, discarded={stats['discarded']}, missing={stats['missing']}")
        for e in stats['errors']:
            print(f"  {e}")

    else:
        batches = generate_prompt(person_dir)
        print(f"\nGenerated {len(batches)} batch prompt files.")
        print(f"\nNext step (sessions_spawn调LLM处理):")
        print(f"For each batch file, spawn a sub-session to:")
        print(f"  1. Read the batch prompt file")
        print(f"  2. Score each material as JSON array")
        print(f"  3. Return the JSON result")
        print(f"Then apply each batch result:")
        print(f"  python3 quality_check_llm.py {person_dir} --apply '<json>'")
        print(f"Or apply all saved scores:")
        print(f"  python3 quality_check_llm.py {person_dir} --apply-from-file")


if __name__ == "__main__":
    main()
