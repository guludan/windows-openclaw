#!/usr/bin/env python3
"""素材预处理脚本（辅助工具）
- 扫描 materials/ 目录
- 提取 Exa API JSON 中的纯文本
- 基础正则去噪（导航/页脚/广告）
- 生成待处理文件清单
- 实际 LLM 清洗和质量评估由子 session 执行

用法：
  python3 _preprocess.py scan <person_dir>        # 扫描并生成清单
  python3 _preprocess.py extract <person_dir>      # 提取Exa JSON + 基础去噪
  python3 _preprocess.py scan-all                  # 扫描所有人物
  python3 _preprocess.py extract-all               # 批量提取+去噪
"""

import argparse
import json
import os
import re
import sys
import time


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def extract_from_exa_json(raw_text: str) -> str:
    """如果是 Exa API JSON，提取每条结果中的 text 字段"""
    try:
        data = json.loads(raw_text)
        items = data.get("results", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        texts = [item["text"] for item in items if isinstance(item, dict) and "text" in item]
        if texts:
            return "\n\n---\n\n".join(texts)
    except (json.JSONDecodeError, TypeError):
        pass
    return raw_text


def basic_denoise(text: str) -> str:
    """基础正则去噪：去掉常见的网页垃圾"""
    # 去掉 Medium/博客站点的导航栏
    text = re.sub(r'(Sign up|Sign in|Open in app|Get app|Write|Search)\n', '', text)
    text = re.sub(r'\[Sitemap\].*?\n', '', text)
    text = re.sub(r'\[.*?Logo\].*?\n', '', text)
    # 去掉社交分享按钮
    text = re.sub(r'(Share|Tweet|Pin|Email|Print)\s*\n', '', text)
    text = re.sub(r'(Share on|Share to)\s+\w+\.?\s*\n', '', text)
    # 去掉版权/备案
    text = re.sub(r'©\s*\d{4}.*?\n', '', text)
    text = re.sub(r'(All rights reserved|版权所有|京ICP备).*?\n', '', text)
    # 去掉重复的空行（3+空行→2空行）
    text = re.sub(r'\n{4,}', '\n\n\n', text)
    # 去掉首尾空白
    text = text.strip()
    return text


def count_effective_chars(text: str) -> int:
    """计算有效字数（去掉空白和标点）"""
    cleaned = re.sub(r'\s+', '', text)
    cleaned = re.sub(r'[^\w\u4e00-\u9fff]', '', cleaned)
    return len(cleaned)


def resolve_materials_dir(arg_dir: str) -> tuple:
    """解析输入路径，返回 (materials_dir, person_name)"""
    d = os.path.normpath(arg_dir)
    if os.path.basename(d) == 'materials':
        return d, os.path.basename(os.path.dirname(d))
    else:
        md = os.path.join(d, 'materials')
        if os.path.isdir(md):
            return md, os.path.basename(d)
        return d, os.path.basename(d)


def find_materials_dirs(base_dir: str) -> list:
    """扫描所有 materials/ 目录"""
    dirs = []
    for root, subdirs, files in os.walk(base_dir):
        if os.path.basename(root) == "materials" and files:
            # 排除 _discarded 子目录
            if "_discarded" not in root:
                txt_files = [f for f in files if f.endswith('.txt') and not f.startswith('_')]
                if txt_files:
                    dirs.append(root)
    return sorted(dirs)


def extract_one(materials_dir: str) -> dict:
    """对单个 materials/ 目录执行 Exa JSON 提取 + 基础去噪"""
    person_name = os.path.basename(os.path.dirname(materials_dir))
    txt_files = sorted([
        f for f in os.listdir(materials_dir)
        if f.endswith('.txt') and not f.startswith('_')
    ])

    stats = {"person": person_name, "dir": materials_dir, "files": {}}

    for fname in txt_files:
        fpath = os.path.join(materials_dir, fname)
        with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
            raw = f.read()

        # 提取 Exa JSON
        extracted = extract_from_exa_json(raw)
        # 基础去噪
        cleaned = basic_denoise(extracted)
        eff_chars = count_effective_chars(cleaned)

        # 如果内容变了，覆盖写入
        if cleaned != raw:
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(cleaned)
            stats["files"][fname] = {"effective_chars": eff_chars, "changed": True}
        else:
            stats["files"][fname] = {"effective_chars": eff_chars, "changed": False}

        log(f"  {fname}: {eff_chars} 有效字数{' (已清洗)' if cleaned != raw else ''}")

    return stats


def scan_one(materials_dir: str) -> dict:
    """扫描单个 materials/ 目录，生成待处理清单"""
    person_name = os.path.basename(os.path.dirname(materials_dir))
    txt_files = sorted([
        f for f in os.listdir(materials_dir)
        if f.endswith('.txt') and not f.startswith('_')
    ])

    info = {
        "person": person_name,
        "dir": materials_dir,
        "file_count": len(txt_files),
        "files": []
    }

    for fname in txt_files:
        fpath = os.path.join(materials_dir, fname)
        size = os.path.getsize(fpath)
        with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
            raw = f.read(200)
        is_exa_json = raw.strip().startswith('{"requestId"') or raw.strip().startswith('{"results"')
        info["files"].append({
            "name": fname,
            "size": size,
            "effective_chars": count_effective_chars(open(fpath, 'r', encoding='utf-8', errors='replace').read()),
            "is_exa_json": is_exa_json
        })

    return info


def main():
    parser = argparse.ArgumentParser(description="素材预处理辅助工具")
    parser.add_argument("command", choices=["scan", "extract", "scan-all", "extract-all", "status"])
    parser.add_argument("person_dir", nargs="?", help="人物目录路径")
    args = parser.parse_args()

    base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "personalities")
    base_dir = os.path.normpath(base_dir)

    if args.command == "status":
        dirs = find_materials_dirs(base_dir)
        log(f"共 {len(dirs)} 个人物有待处理素材")
        for d in dirs:
            name = os.path.basename(os.path.dirname(d))
            count = len([f for f in os.listdir(d) if f.endswith('.txt') and not f.startswith('_')])
            has_report = os.path.exists(os.path.join(d, "_quality_report.json"))
            print(f"  {'✅' if has_report else '⬜'} {name}: {count} files")
        return

    if args.command in ("scan", "extract"):
        if not args.person_dir:
            log("需要指定 person_dir")
            sys.exit(1)
        d = args.person_dir if os.path.isabs(args.person_dir) else os.path.join(os.getcwd(), args.person_dir)
        materials_dir, person_name = resolve_materials_dir(d)

        if args.command == "scan":
            info = scan_one(materials_dir)
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            stats = extract_one(materials_dir)
            print(json.dumps(stats, ensure_ascii=False, indent=2))

    elif args.command == "scan-all":
        dirs = find_materials_dirs(base_dir)
        all_info = []
        for d in dirs:
            info = scan_one(d)
            all_info.append(info)
            has_report = os.path.exists(os.path.join(d, "_quality_report.json"))
            log(f"{'✅' if has_report else '⬜'} {info['person']}: {info['file_count']} files")
        report_path = os.path.join(base_dir, "_scan_report.json")
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(all_info, f, ensure_ascii=False, indent=2)
        log(f"扫描报告: {report_path}")

    elif args.command == "extract-all":
        dirs = find_materials_dirs(base_dir)
        for d in dirs:
            log(f"处理: {os.path.basename(os.path.dirname(d))}")
            extract_one(d)


if __name__ == "__main__":
    main()
