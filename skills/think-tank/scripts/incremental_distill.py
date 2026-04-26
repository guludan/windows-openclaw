#!/usr/bin/env python3
"""
incremental_distill.py — 增量蒸馏工具

功能:
1. 生成增量蒸馏prompt (基于已有insights + 新素材)
2. 应用LLM返回的增量结果 (合并新insights到已有JSON)

用法:
    # 生成增量蒸馏prompt
    python3 incremental_distill.py <person_dir> --new-materials file1.txt file2.txt ...
    python3 incremental_distill.py <person_dir> --since "2026-04-26"  # 按修改时间自动选新素材

    # 应用LLM返回的增量结果
    python3 incremental_distill.py <person_dir> --apply

注意: 本脚本不调LLM，只生成prompt和合并结果。实际蒸馏由 sessions_spawn 调LLM执行。
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def load_personality_v3(person_dir):
    """读取现有 personality_v3.json"""
    pv3_path = Path(person_dir) / "personality_v3.json"
    if not pv3_path.exists():
        print(f"错误: 找不到 {pv3_path}", file=sys.stderr)
        sys.exit(1)
    with open(pv3_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_personality_v3(person_dir, data):
    """保存 personality_v3.json（带备份）"""
    pv3_path = Path(person_dir) / "personality_v3.json"
    # 备份
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = Path(person_dir) / f"personality_v3.json.bak.{timestamp}"
    if pv3_path.exists():
        import shutil
        shutil.copy2(pv3_path, backup_path)
        print(f"已备份原文件到: {backup_path.name}")
    # 保存
    with open(pv3_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"已保存: {pv3_path}")


def read_material_files(file_paths, person_dir):
    """读取素材文件内容"""
    materials = []
    for file_path in file_paths:
        path = Path(file_path)
        # 如果是相对路径，尝试从 person_dir/materials/ 解析
        if not path.is_absolute() and not path.exists():
            full_path = Path(person_dir) / "materials" / file_path
            if full_path.exists():
                path = full_path
        if not path.exists():
            print(f"警告: 素材文件不存在: {file_path}", file=sys.stderr)
            continue
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            materials.append({
                "filename": path.name,
                "content": content
            })
    return materials


def find_new_materials_since(person_dir, since_date):
    """按修改时间查找新素材文件"""
    materials_dir = Path(person_dir) / "materials"
    if not materials_dir.exists():
        print(f"警告: materials目录不存在: {materials_dir}", file=sys.stderr)
        return []
    # 解析日期
    try:
        since = datetime.strptime(since_date, "%Y-%m-%d")
    except ValueError:
        print(f"错误: 日期格式应为 YYYY-MM-DD，得到: {since_date}", file=sys.stderr)
        sys.exit(1)
    # 查找修改时间晚于 since_date 的文件
    new_files = []
    for file_path in sorted(materials_dir.iterdir()):
        if file_path.is_file():
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
            if mtime >= since:
                new_files.append(file_path)
    return new_files


def generate_incremental_prompt(person_dir, new_materials, pv3_data):
    """生成增量蒸馏prompt"""
    person_name = pv3_data.get("brief", "未知人物").split()[0] if pv3_data.get("brief") else Path(person_dir).name

    # 准备已有insights摘要
    existing_insights = pv3_data.get("insights_with_evidence", [])
    existing_summary = []
    for i, ins in enumerate(existing_insights):
        claim = ins.get("claim", "")
        # 取前50字作为签名
        claim_sig = claim[:50] + ("..." if len(claim) > 50 else "")
        dim = ins.get("dim", "")
        ev_count = len(ins.get("evidence", []))
        existing_summary.append(f"{i+1}. [{dim}] {claim_sig} (证据{ev_count}条)")

    # 准备新素材
    materials_text = ""
    for mat in new_materials:
        materials_text += f"\n## 素材文件: {mat['filename']}\n{mat['content']}\n"

    # 构建prompt
    prompt = f"""# 增量蒸馏任务

你是人格蒸馏专家。以下是 **{person_name}** 已有的insights列表（共{len(existing_insights)}条观点的简短摘要），以及 **{len(new_materials)}** 篇新增素材。

## 已有insights摘要（用于避免重复）

"""
    if existing_summary:
        prompt += "\n".join(existing_summary)
    else:
        prompt += "（无已有insights）"

    prompt += f"""

## 新增素材（{len(new_materials)}篇）

{materials_text}

## 任务要求

请从新素材中提取 **新的** insights，参考已有insights避免重复。

1. **新增insights**: 只提取新素材中独有的、不同于已有insights的观点
2. **合并证据**: 如果新素材提供了已有insight的补充证据，标记为merge

## 输出格式

请严格按照以下JSON格式输出（不要有任何其他文字）:

```json
{{
  "new_insights": [
    {{
      "claim": "观点表述（100-300字，蒸馏后的核心论断）",
      "dim": "维度分类（stance/cognitive/method/decision/communication/logical_tracing/tension/situational等）",
      "evidence": [
        {{
          "excerpt": "原文中支撑此观点的关键段落（100-500字，必须是原文原文原文，不是转述）",
          "source_file": "素材文件名",
          "location": "在文件中的位置描述（如：第3段、第12-15行）"
        }}
      ]
    }}
  ],
  "merge_into": [
    {{
      "claim_signature": "已有insight的claim前50字（用于匹配）",
      "additional_evidence": [
        {{
          "excerpt": "原文中补充证据段落",
          "source_file": "素材文件名",
          "location": "位置描述"
        }}
      ]
    }}
  ]
}}
```

## 注意事项

- claim_signature: 取已有insight的claim前50字，用于匹配目标insight
- new_insights: 完全新增的观点，每个至少1条evidence
- merge_into: 新素材为已有观点补充证据的情况
- excerpt必须是原文原文原文，不能改写
- dim分类复用: stance, cognitive, method, decision, communication, logical_tracing, tension, situational 等

现在请输出增量蒸馏结果:
"""

    # 保存prompt
    prompt_path = Path(person_dir) / "_incremental_prompt.md"
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)
    print(f"已生成增量蒸馏prompt: {prompt_path}")
    print(f"  - 已有insights: {len(existing_insights)}条")
    print(f"  - 新素材: {len(new_materials)}篇")


def apply_incremental_result(person_dir, pv3_data):
    """应用LLM返回的增量结果"""
    result_path = Path(person_dir) / "_incremental_result.json"
    if not result_path.exists():
        print(f"错误: 找不到增量结果文件 {result_path}", file=sys.stderr)
        print("请先运行LLM生成 _incremental_result.json", file=sys.stderr)
        sys.exit(1)

    with open(result_path, "r", encoding="utf-8") as f:
        result = json.load(f)

    new_insights = result.get("new_insights", [])
    merge_into = result.get("merge_into", [])

    if not new_insights and not merge_into:
        print("警告: 增量结果为空（无新insights也无合并）")
        return

    # 获取已有insights
    existing_insights = pv3_data.get("insights_with_evidence", [])

    # 处理 merge_into
    merged_count = 0
    added_evidence_count = 0
    for merge_item in merge_into:
        claim_sig = merge_item.get("claim_signature", "")
        additional_evidence = merge_item.get("additional_evidence", [])

        if not claim_sig or not additional_evidence:
            continue

        # 查找匹配的已有insight
        matched_idx = None
        for i, ins in enumerate(existing_insights):
            existing_claim = ins.get("claim", "")
            # 模糊匹配：claim前50字包含或被包含
            if claim_sig in existing_claim or existing_claim[:50] in claim_sig:
                matched_idx = i
                break

        if matched_idx is None:
            print(f"警告: 无法匹配claim_signature: {claim_sig}")
            continue

        # 追加evidence（去重：按source_file + excerpt前30字）
        existing_evidence = existing_insights[matched_idx].setdefault("evidence", [])
        for ev in additional_evidence:
            excerpt = ev.get("excerpt", "")
            source_file = ev.get("source_file", "")
            if not excerpt:
                continue
            # 去重检查
            dup = False
            for existing_ev in existing_evidence:
                existing_excerpt = existing_ev.get("excerpt", "")
                existing_source = existing_ev.get("source_file", "")
                if (source_file == existing_source and
                    excerpt[:30] == existing_excerpt[:30]):
                    dup = True
                    break
            if not dup:
                location = ev.get("location", "")
                existing_evidence.append({
                    "excerpt": excerpt,
                    "source_file": source_file,
                    "location": location
                })
                added_evidence_count += 1

        merged_count += 1

    # 追加新insights
    added_insights_count = len(new_insights)
    for ins in new_insights:
        # 验证格式
        if not ins.get("claim"):
            print(f"警告: 跳过无claim的insight")
            continue
        existing_insights.append(ins)

    # 更新元数据
    if "_distill_meta" not in pv3_data:
        pv3_data["_distill_meta"] = {}
    meta = pv3_data["_distill_meta"]
    meta["timestamp"] = datetime.now().isoformat()
    meta["source_count"] = meta.get("source_count", 0) + len(new_insights)  # 粗略估计

    # 保存
    save_personality_v3(person_dir, pv3_data)

    # 打印统计
    print("\n增量蒸馏完成:")
    print(f"  - 新增insights: {added_insights_count}条")
    print(f"  - 合并已有insights: {merged_count}条")
    print(f"  - 追加evidence: {added_evidence_count}条")
    print(f"  - 当前总insights: {len(existing_insights)}条")


def main():
    parser = argparse.ArgumentParser(
        description="增量蒸馏工具 — 生成prompt或应用结果",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 指定新素材文件生成prompt
  python3 incremental_distill.py /path/to/person/dir --new-materials file1.txt file2.txt

  # 按修改时间自动选新素材（2026-04-26之后修改的）
  python3 incremental_distill.py /path/to/person/dir --since "2026-04-26"

  # 应用LLM返回的增量结果
  python3 incremental_distill.py /path/to/person/dir --apply
        """
    )

    parser.add_argument("person_dir", help="人物目录路径（包含personality_v3.json）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--new-materials", nargs="+", metavar="FILE",
                       help="新增素材文件名列表（相对materials目录或绝对路径）")
    group.add_argument("--since", metavar="DATE",
                       help="按修改时间筛选新素材（格式: YYYY-MM-DD）")
    group.add_argument("--apply", action="store_true",
                       help="应用_incremental_result.json中的增量结果")

    args = parser.parse_args()

    person_dir = Path(args.person_dir)
    if not person_dir.is_dir():
        print(f"错误: 目录不存在: {person_dir}", file=sys.stderr)
        sys.exit(1)

    if args.apply:
        # 应用模式
        pv3_data = load_personality_v3(person_dir)
        apply_incremental_result(person_dir, pv3_data)
    else:
        # 生成prompt模式
        pv3_data = load_personality_v3(person_dir)
        if args.new_materials:
            new_files = args.new_materials
        else:  # args.since
            new_files = find_new_materials_since(person_dir, args.since)
            if not new_files:
                print(f"未找到 {args.since} 之后修改的新素材文件")
                sys.exit(0)
            print(f"找到 {len(new_files)} 个新素材文件（修改时间 >= {args.since}）:")
            for f in new_files:
                print(f"  - {f.name}")

        new_materials = read_material_files(new_files, person_dir)
        if not new_materials:
            print("错误: 没有有效的素材文件", file=sys.stderr)
            sys.exit(1)

        generate_incremental_prompt(person_dir, new_materials, pv3_data)


if __name__ == "__main__":
    main()
