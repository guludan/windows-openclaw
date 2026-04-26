#!/usr/bin/env python3
"""
_batch_distill.py — 批量V3蒸馏（子session调用）

用法:
    python3 _batch_distill.py <person_dir>              # 蒸馏单人物
    python3 _batch_distill.py --list                    # 列出缺v3的人物
    python3 _batch_distill.py --all                     # 蒸馏所有缺v3的人物
    python3 _batch_distill.py --gen-record <person_dir> # 生成蒸馏记录文件
    python3 _batch_distill.py <person_dir> --per-file   # 生成逐篇蒸馏prompt
    python3 _batch_distill.py <person_dir> --merge-per-file  # 合并逐篇蒸馏结果

注意:
    - 大素材自动分段（>60000字符）
    - 模板: skills/think-tank/templates/distill_prompt_v3.md
    - 逐篇蒸馏模式用于素材过多时LLM上下文不够的场景
"""

import json
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Dict, Any

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


def generate_per_file_prompts(person_dir: Path) -> None:
    """生成逐篇蒸馏prompt

    为每个素材文件生成独立的蒸馏prompt，保存到 _per_file_prompts/ 目录
    """
    # 读取模板
    if not TEMPLATE.exists():
        print(f"错误: 模板文件不存在: {TEMPLATE}", file=sys.stderr)
        sys.exit(1)

    template_content = TEMPLATE.read_text(encoding='utf-8')

    # 获取素材文件
    materials = get_materials(person_dir)
    if not materials:
        print(f"错误: {person_dir} 没有素材文件", file=sys.stderr)
        sys.exit(1)

    # 创建输出目录
    prompts_dir = person_dir / "_per_file_prompts"
    prompts_dir.mkdir(exist_ok=True)

    # 读取人物名
    person_name = person_dir.name

    # 为每个素材文件生成prompt
    print(f"生成逐篇蒸馏prompts: {len(materials)} 个文件")
    for i, material_file in enumerate(materials, 1):
        # 读取素材内容
        material_content = material_file.read_text(encoding='utf-8', errors='ignore')

        # 生成prompt
        prompt = f"""# 人格蒸馏 Prompt v3（逐篇蒸馏模式）

你是人格蒸馏专家。根据提供的人物素材，提取结构化的 insights_with_evidence。

## 任务说明

这是逐篇蒸馏模式：你只需要从这一篇素材中提取 insights_with_evidence，不需要生成完整的人格模型。

## 输出格式

只输出 insights_with_evidence 的 JSON 数组，格式如下：

```json
[
  {{
    "claim": "观点表述（100-300字，蒸馏后的核心论断）",
    "dim": "维度分类（如: stance, cognitive, method, decision, communication, logical_tracing, tension, situational）",
    "evidence": [
      {{
        "excerpt": "原文中支撑此观点的关键段落（100-500字，必须是原文原文原文，不是转述）",
        "source_file": "{material_file.name}",
        "location": "在文件中的位置描述（如：第3段、第12-15行）"
      }}
    ]
  }}
]
```

## 提取指南

### insights_with_evidence 字段说明

这是本模板最重要的输出字段，直接决定后续RAG检索质量。必须从原文中精确提取，excerpt一字不改。

- **claim**: 观点表述（100-300字）。不是简单的摘录，而是蒸馏后的核心论断。
- **dim**: 维度分类。复用现有维度：stance, cognitive, method, decision, communication, logical_tracing, tension, situational。
- **evidence**: 支撑该观点的原文证据。
  - **excerpt**: 原文原文原文（100-500字），一字不改，直接摘录。
  - **source_file**: 素材文件名（必须是 {material_file.name}）。
  - **location**: 在文件中的位置描述（如：第3段、第12-15行）。

### 多段原文处理

如果同一篇素材中有多段原文表述同一个观点，归入同一个 insight 下作为多条 evidence。

## 人物素材

**人物**: {person_name}
**素材文件**: {material_file.name}
**字符数**: {len(material_content):,}

--- 素材开始 ---
{material_content}
--- 素材结束 ---

## 输出要求

1. 只输出 insights_with_evidence 的 JSON 数组，不要输出其他字段
2. excerpt 必须是原文原文原文，一字不改
3. 如果素材中信息不足，可以输出空数组 []
4. 输出必须是合法 JSON 数组
5. 每个insight至少1条evidence，重要观点2-5条
"""

        # 保存prompt
        prompt_filename = f"_prompt_{i:03d}_{material_file.name}.md"
        prompt_path = prompts_dir / prompt_filename
        prompt_path.write_text(prompt, encoding='utf-8')

        print(f"  [{i}/{len(materials)}] {prompt_filename}")

    print(f"\n已生成 {len(materials)} 个prompts到: {prompts_dir}")
    print(f"\n下一步：")
    print(f"  1. 在 AI 助手中逐个执行这些 prompts")
    print(f"  2. 将每个结果保存到 {person_dir}/_per_file_results/{{filename}}.json")
    print(f"  3. 执行: python3 _batch_distill.py {person_dir} --merge-per-file")


def merge_per_file_results(person_dir: Path) -> None:
    """合并逐篇蒸馏结果

    读取 _per_file_results/ 下所有 JSON 文件，合并 insights_with_evidence
    """
    results_dir = person_dir / "_per_file_results"
    if not results_dir.exists():
        print(f"错误: 结果目录不存在: {results_dir}", file=sys.stderr)
        print(f"请先生成逐篇蒸馏prompts并执行蒸馏", file=sys.stderr)
        sys.exit(1)

    # 读取所有结果文件
    result_files = sorted(results_dir.glob("*.json"))
    if not result_files:
        print(f"错误: {results_dir} 中没有 JSON 文件", file=sys.stderr)
        sys.exit(1)

    print(f"读取逐篇蒸馏结果: {len(result_files)} 个文件")

    # 读取所有insights
    all_insights = []
    for result_file in result_files:
        try:
            insights = json.loads(result_file.read_text(encoding='utf-8'))
            if isinstance(insights, list):
                all_insights.extend(insights)
                print(f"  ✓ {result_file.name}: {len(insights)} insights")
            else:
                print(f"  ⚠ {result_file.name}: 不是数组格式，跳过")
        except Exception as e:
            print(f"  ✗ {result_file.name}: 读取失败 - {e}")

    if not all_insights:
        print("错误: 没有有效的 insights 数据", file=sys.stderr)
        sys.exit(1)

    print(f"\n总计: {len(all_insights)} insights")

    # 读取或创建 personality_v3.json
    pv3_path = person_dir / "personality_v3.json"

    if pv3_path.exists():
        # 读取现有的 personality_v3.json
        existing_data = json.loads(pv3_path.read_text(encoding='utf-8'))
        existing_insights = existing_data.get("insights_with_evidence", [])
        print(f"现有 insights: {len(existing_insights)}")

        # 合并 insights（相同claim的合并evidence）
        merged_insights = merge_insights(existing_insights + all_insights)
        print(f"合并后 insights: {len(merged_insights)}")

        # 更新数据
        existing_data["insights_with_evidence"] = merged_insights
        existing_data["_distill_meta"]["timestamp"] = datetime.now().isoformat()
        if "source_count" in existing_data["_distill_meta"]:
            materials = get_materials(person_dir)
            existing_data["_distill_meta"]["source_count"] = len(materials)

        final_data = existing_data
    else:
        # 创建新的 personality_v3.json
        materials = get_materials(person_dir)
        final_data = {
            "brief": "",
            "school": "",
            "domains": [],
            "stance_anchors": {
                "core_belief": "",
                "methodology": "",
                "key_debates": [],
                "value_hierarchy": []
            },
            "cognitive_patterns": {
                "thinking_style": "",
                "reasoning_approach": "",
                "typical_biases": [],
                "intellectual_strengths": [],
                "intellectual_blindspots": []
            },
            "analytical_frameworks": [],
            "logical_tracing": {
                "summary": "",
                "lineage": {
                    "inherited": [],
                    "transformed": [],
                    "ruptured": [],
                    "converged": []
                },
                "turning_points": [],
                "grounding": {
                    "embodied": [],
                    "contextual": []
                },
                "collaborations": [],
                "renunciations": [],
                "adversaries": [],
                "tension_explanation": {
                    "note": "",
                    "tracing": ""
                }
            },
            "decision_patterns": {
                "risk_tolerance": "",
                "information_processing": "",
                "temporal_orientation": "",
                "conflict_resolution": ""
            },
            "situational_triggers": {
                "description": "",
                "triggers": [],
                "collapse_point": "",
                "default_behavior": "",
                "public_commitment_lock": ""
            },
            "communication_profile": {
                "style": "",
                "persuasion_tactics": [],
                "rhetorical_devices": [],
                "emotional_range": ""
            },
            "evolution_trajectory": "",
            "key_aphorisms": [],
            "insights_with_evidence": merge_insights(all_insights),
            "_distill_meta": {
                "source_count": len(materials),
                "model": "glm-5-turbo",
                "timestamp": datetime.now().isoformat(),
                "version": "v3-logical-tracing"
            }
        }

    # 保存 personality_v3.json
    with open(pv3_path, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)

    print(f"\n已保存: {pv3_path}")

    # 生成 _distilled_files.json
    generate_distilled_record(person_dir)

    print(f"\n合并完成！")
    print(f"\n清理建议（可选）：")
    print(f"  rm -rf {person_dir}/_per_file_prompts")
    print(f"  rm -rf {person_dir}/_per_file_results")


def merge_insights(insights: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """合并 insights，相同 claim 的合并 evidence

    Args:
        insights: insights 列表

    Returns:
        合并后的 insights 列表
    """
    # 按 claim 分组
    groups: Dict[str, Dict[str, Any]] = {}

    for insight in insights:
        claim = insight.get("claim", "").strip()
        if not claim:
            continue

        dim = insight.get("dim", "")
        evidence_list = insight.get("evidence", [])

        if claim not in groups:
            groups[claim] = {
                "claim": claim,
                "dim": dim,
                "evidence": []
            }

        # 合并 evidence（避免重复）
        existing_evidence = groups[claim]["evidence"]
        for evidence in evidence_list:
            # 用 excerpt 和 source_file 作为唯一标识
            key = (evidence.get("excerpt", ""), evidence.get("source_file", ""))
            if not any(
                (e.get("excerpt", ""), e.get("source_file", "")) == key
                for e in existing_evidence
            ):
                existing_evidence.append(evidence)

    return list(groups.values())


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
    parser.add_argument("--per-file", action="store_true", help="生成逐篇蒸馏prompts")
    parser.add_argument("--merge-per-file", action="store_true", help="合并逐篇蒸馏结果")
    args = parser.parse_args()

    if args.gen_record:
        pdir = Path(args.gen_record)
        if not pdir.is_dir():
            print(f"错误: 目录不存在: {pdir}", file=sys.stderr)
            sys.exit(1)
        generate_distilled_record(pdir)
        return

    if args.per_file:
        if not args.person_dir:
            print("错误: --per-file 需要指定人物目录", file=sys.stderr)
            sys.exit(1)
        pdir = Path(args.person_dir)
        if not pdir.is_dir():
            print(f"错误: 目录不存在: {pdir}", file=sys.stderr)
            sys.exit(1)
        generate_per_file_prompts(pdir)
        return

    if args.merge_per_file:
        if not args.person_dir:
            print("错误: --merge-per-file 需要指定人物目录", file=sys.stderr)
            sys.exit(1)
        pdir = Path(args.person_dir)
        if not pdir.is_dir():
            print(f"错误: 目录不存在: {pdir}", file=sys.stderr)
            sys.exit(1)
        merge_per_file_results(pdir)
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
