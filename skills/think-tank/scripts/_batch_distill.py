#!/usr/bin/env python3
"""
_batch_distill.py — 批量V3蒸馏（子session调用）

用法:
    python3 _batch_distill.py <person_dir>              # 蒸馏单人物
    python3 _batch_distill.py --list                    # 列出缺v3的人物
    python3 _batch_distill.py --all                     # 蒸馏所有缺v3的人物
    python3 _batch_distill.py --gen-record <person_dir> # 生成蒸馏记录文件

注意:
    - 大素材自动分段（>60000字符）
    - 模板: skills/think-tank/templates/distill_prompt_v3.md
"""

import json
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

BASE_DIR = Path("/mnt/d/data/think-tank-data/personalities")
TEMPLATE = Path("~/.openclaw/workspace/skills/think-tank/templates/distill_prompt_v3.md").expanduser()
MAX_CHARS = 60000


def get_materials(person_dir: Path) -> List[Path]:
    """获取素材文件列表，按大小排序（排除 _ 开头的文件和 _discarded/ 目录）"""
    mat_dir = person_dir / "materials"
    if not mat_dir.exists():
        return []
    files = sorted(mat_dir.glob("*.txt"), key=lambda f: f.stat().st_size)
    # 排除 _ 开头的文件
    return [f for f in files if f.stat().st_size >= 200 and not f.name.startswith("_")]


def generate_distilled_record(person_dir: Path):
    """为已蒸馏的人物生成 _distilled_files.json"""
    # 检查是否存在 personality_v3.json
    pv3_path = person_dir / "personality_v3.json"
    if not pv3_path.exists():
        print(f"错误: 找不到 {pv3_path}", file=sys.stderr)
        sys.exit(1)

    # 获取所有素材文件
    materials = get_materials(person_dir)
    if not materials:
        print(f"警告: {person_dir} 没有素材文件")
        return

    # 生成记录
    record_path = person_dir / "_distilled_files.json"
    record = {
        "last_distill_time": datetime.now().isoformat(),
        "distilled_files": sorted([f.name for f in materials])
    }

    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    print(f"已生成蒸馏记录: {record_path}")
    print(f"  - 素材文件数: {len(materials)}")
    print(f"  - 蒸馏时间: {record['last_distill_time']}")


def estimate_total_chars(files: List[Path]) -> int:
    return sum(f.stat().st_size for f in files)


def build_distill_task(person_name: str, person_dir: Path, person_domain: str = "") -> str:
    """构建蒸馏任务prompt（给sessions_spawn用）"""

    # 读取模板
    template = TEMPLATE.read_text(encoding='utf-8') if TEMPLATE.exists() else ""
    template_ref = "读取模板: ~/.openclaw/workspace/skills/think-tank/templates/distill_prompt_v3.md"

    # 读取素材
    files = get_materials(person_dir)
    if not files:
        return f"ERROR: {person_name} 无素材文件"

    total = estimate_total_chars(files)

    # 构建素材引用
    if total <= MAX_CHARS:
        # 小素材：直接合并
        parts = []
        for f in files:
            content = f.read_text(encoding='utf-8', errors='ignore')
            parts.append(f"[{f.name}]\n{content}")
        materials_text = "\n\n---\n\n".join(parts)

        task = f"""你是人格蒸馏专家。完成以下任务：

1. {template_ref}
2. 以下是{person_name}的全部素材（{total}字符，{len(files)}篇）：
--- 素材开始 ---
{materials_text}
--- 素材结束 ---

3. 按照模板格式，生成 personality_v3.json
4. 用 write 工具保存到：{person_dir}/personality_v3.json

关键要求：
- 学派/领域根据素材内容判断，预填: {person_domain}
- source_ref 必须指向素材文件名
- 信息不足用空数组 []，绝不编造
- 输出必须是合法JSON
- _distill_meta.model 填 "glm-5-turbo"，_distill_meta.version 填 "v3-logical-tracing" """
    else:
        # 大素材：分段处理
        chunks = []
        current = ""
        for f in files:
            content = f.read_text(encoding='utf-8', errors='ignore')
            if len(current) + len(content) > MAX_CHARS - 2000:
                chunks.append(current)
                current = f"[{f.name}]\n{content}\n\n"
            else:
                current += f"[{f.name}]\n{content}\n\n"
        if current:
            chunks.append(current)

        task = f"""你是人格蒸馏专家。{person_name}的素材较大（{total}字符，{len(files)}篇，分{len(chunks)}段），需要分段处理。

1. {template_ref}
2. 逐段读取素材文件并理解：

素材文件列表：
{chr(10).join(f'  - {f}' for f in files)}

分段策略：素材总量超过{MAX_CHARS}字符，你必须：
  a) 先读取前1/3素材，提取关键信息
  b) 再读取中间1/3素材，补充信息
  c) 最后读取后1/3素材，完善细节
  d) 综合所有信息，生成完整的 personality_v3.json

每次读取用 read 工具读取具体的素材文件路径，不要一次性全部读入。

3. 综合所有素材，按照模板格式生成 personality_v3.json
4. 用 write 工具保存到：{person_dir}/personality_v3.json

关键要求：
- 学派/领域根据素材内容判断，预填: {person_domain}
- source_ref 必须指向素材文件名
- 信息不足用空数组 []，绝不编造
- 输出必须是合法JSON
- _distill_meta.model 填 "glm-5-turbo"，_distill_meta.version 填 "v3-logical-tracing" """

    return task


def find_missing(base_dir: Path = BASE_DIR) -> List[Tuple[str, Path, str]]:
    """找出缺 personality_v3.json 的人物"""
    missing = []
    for group_dir in sorted(base_dir.iterdir()):
        if not group_dir.is_dir():
            continue
        # Check if this group has sub-directories or persons directly
        subdirs = sorted(d for d in group_dir.iterdir() if d.is_dir())
        for candidate in subdirs:
            v3 = candidate / "personality_v3.json"
            if v3.exists():
                continue
            # Skip non-person dirs (like 'materials' at group level)
            if not (candidate / "materials").exists() and not any((candidate / f).is_file() for f in ["personality_v3.json"]):
                # Might be a sub-category, check deeper
                for person_dir in sorted(d for d in candidate.iterdir() if d.is_dir()):
                    if (person_dir / "personality_v3.json").exists():
                        continue
                    name = person_dir.name
                    domain = f"{group_dir.name}/{candidate.name}"
                    missing.append((name, person_dir, domain))
            else:
                name = candidate.name
                domain = group_dir.name
                missing.append((name, candidate, domain))
    return missing


def main():
    import argparse
    parser = argparse.ArgumentParser(description="批量V3蒸馏")
    parser.add_argument("person_dir", nargs="?", help="人物目录")
    parser.add_argument("--list", action="store_true", help="列出缺v3的人物")
    parser.add_argument("--all", action="store_true", help="蒸馏所有缺v3的人物")
    parser.add_argument("--print-task", action="store_true", help="打印任务prompt（调试用）")
    parser.add_argument("--gen-record", metavar="DIR", help="为指定人物目录生成_distilled_files.json")
    args = parser.parse_args()

    if args.gen_record:
        pdir = Path(args.gen_record)
        if not pdir.is_dir():
            print(f"错误: 目录不存在: {pdir}", file=sys.stderr)
            sys.exit(1)
        generate_distilled_record(pdir)
        return

    if args.list:
        missing = find_missing()
        if not missing:
            print("所有人物已有 personality_v3.json ✅")
        else:
            print(f"缺 v3 的人物: {len(missing)}")
            for name, pdir, domain in missing:
                files = get_materials(pdir)
                total = estimate_total_chars(files)
                print(f"  {name} ({domain}): {len(files)}篇素材, {total:,}字符")
        return

    if args.all:
        missing = find_missing()
        if not missing:
            print("所有人物已有 personality_v3.json ✅")
            return
        print(f"需要蒸馏: {len(missing)} 人")
        print("请对每个人物执行 sessions_spawn，任务prompt如下：")
        print("=" * 60)
        for name, pdir, domain in missing:
            task = build_distill_task(name, pdir, domain)
            print(f"\n### {name} ({domain})")
            print(f"任务长度: {len(task):,} 字符")
            if args.print_task:
                print(task[:500] + "...")
            else:
                print(f"(加 --print-task 查看完整prompt)")
        return

    if args.person_dir:
        pdir = Path(args.person_dir)
        name = pdir.name
        task = build_distill_task(name, pdir)
        print(task)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
