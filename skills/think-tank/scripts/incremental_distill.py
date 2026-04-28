#!/usr/bin/env python3
"""
incremental_distill.py — 增量蒸馏工具（逐篇蒸馏模式）

功能:
1. 生成逐篇增量蒸馏prompts (基于已有insights + 新素材)
2. 应用LLM返回的增量结果 (从 _per_file_results/ 合并新insights到已有JSON)
3. 自动追踪已蒸馏素材（_distilled_files.json）

用法:
    # 生成逐篇增量蒸馏prompts
    python3 incremental_distill.py <person_dir> --new-materials file1.txt file2.txt ...
    python3 incremental_distill.py <person_dir> --since "2026-04-26"  # 按修改时间自动选新素材
    python3 incremental_distill.py <person_dir> --auto  # 自动找出未蒸馏的新素材（推荐）

    # 应用LLM返回的增量结果（从 _per_file_results/ 读取逐篇结果）
    python3 incremental_distill.py <person_dir> --apply

注意:
- 本脚本不调LLM，只生成prompts和合并结果
- 实际蒸馏由 AI 助手逐个执行 prompts
- 逐篇蒸馏模式和 _batch_distill.py --per-file 共用 _per_file_prompts/ 和 _per_file_results/ 目录
- 增量蒸馏的 prompt 文件名前缀为 _prompt_inc_（区别于全量蒸馏的 _prompt_）
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 导入公共切分函数
from _distill_utils import split_material_into_chunks


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
    """读取素材文件内容，支持 Path 对象和字符串"""
    materials = []
    for file_path in file_paths:
        path = Path(file_path)
        if path.is_absolute() and path.exists():
            pass
        else:
            found = None
            fname = path.name if hasattr(path, 'name') else str(file_path)
            for subdir in ["materials/raw", "papers/raw"]:
                candidate = Path(person_dir) / subdir / fname
                if candidate.exists():
                    found = candidate
                    break
            if not found:
                candidate = Path(person_dir) / "materials" / fname
                if candidate.exists():
                    found = candidate
            if not found and path.exists():
                found = path
            if found:
                path = found
            else:
                print(f"警告: 素材文件不存在: {file_path}", file=sys.stderr)
                continue
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            materials.append({
                "filename": path.name,
                "content": content
            })
    return materials


def get_all_material_files(person_dir):
    """扫描 materials/raw/ 和 papers/raw/ 目录下所有 .txt 和 .md 文件"""
    files = []
    for subdir in ["materials/raw", "papers/raw"]:
        raw_dir = Path(person_dir) / subdir
        if raw_dir.exists():
            for file_path in sorted(raw_dir.iterdir()):
                if file_path.is_file() and file_path.suffix in ('.txt', '.md') and not file_path.name.startswith("_"):
                    files.append(file_path)
    return files


def load_distilled_files_record(person_dir):
    """读取 _distilled_files.json"""
    record_path = Path(person_dir) / "_distilled_files.json"
    if not record_path.exists():
        return None
    with open(record_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_distilled_files_record(person_dir, distilled_files):
    """保存 _distilled_files.json"""
    record_path = Path(person_dir) / "_distilled_files.json"
    record = {
        "last_distill_time": datetime.now().isoformat(),
        "distilled_files": sorted(distilled_files)
    }
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(f"已更新蒸馏记录: {record_path}")


def find_new_materials_auto(person_dir):
    """自动查找未蒸馏的新素材，优先从DB查询"""
    db_path = Path(person_dir) / "_db.sqlite"

    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute(
                "SELECT filename, file_path FROM items WHERE distilled=0 AND status != 'discarded'"
            ).fetchall()
            conn.close()
            new_files = []
            for filename, file_path in rows:
                fp = Path(person_dir) / file_path if file_path else None
                if fp and fp.exists():
                    new_files.append(fp)
                else:
                    for subdir in ["materials/raw", "papers/raw"]:
                        candidate = Path(person_dir) / subdir / filename
                        if candidate.exists():
                            new_files.append(candidate)
                            break
            print(f"从DB查询到 {len(new_files)} 个未蒸馏素材")
            return new_files
        except Exception as e:
            print(f"  ⚠ DB查询失败: {e}，fallback到json记录", file=sys.stderr)

    # Fallback: 旧逻辑
    record = load_distilled_files_record(person_dir)
    if record is None:
        print("错误: 未找到 _db.sqlite 或 _distilled_files.json", file=sys.stderr)
        print("首次蒸馏请使用 --new-materials 或 --since 指定素材", file=sys.stderr)
        sys.exit(1)

    distilled_set = set(record.get("distilled_files", []))
    last_time = record.get("last_distill_time", "")

    all_files = get_all_material_files(person_dir)
    new_files = [f for f in all_files if f.name not in distilled_set]

    if last_time:
        print(f"上次蒸馏时间: {last_time}")
        print(f"已蒸馏文件数: {len(distilled_set)}")

    return new_files


def find_new_materials_since(person_dir, since_date):
    """按修改时间查找新素材文件（扫描 materials/raw/ 和 papers/raw/）"""
    try:
        since = datetime.strptime(since_date, "%Y-%m-%d")
    except ValueError:
        print(f"错误: 日期格式应为 YYYY-MM-DD，得到: {since_date}", file=sys.stderr)
        sys.exit(1)
    new_files = []
    for subdir in ["materials/raw", "papers/raw"]:
        raw_dir = Path(person_dir) / subdir
        if not raw_dir.exists():
            continue
        for file_path in sorted(raw_dir.iterdir()):
            if file_path.is_file():
                mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                if mtime >= since:
                    new_files.append(file_path)
    return new_files


def generate_per_file_prompts(person_dir, new_materials, pv3_data):
    """生成逐篇增量蒸馏prompts

    为每篇新素材生成独立的蒸馏prompt，保存到 _per_file_prompts/ 目录
    大文件会自动按段落切分成多个chunks（每个chunk不超过15000字符）
    """
    person_name = pv3_data.get("brief", "未知人物").split()[0] if pv3_data.get("brief") else Path(person_dir).name

    # 准备已有insights摘要（用于避免重复）
    existing_insights = pv3_data.get("insights_with_evidence", [])
    existing_summary = []
    for i, ins in enumerate(existing_insights):
        claim = ins.get("claim", "")
        # 取前50字作为签名
        claim_sig = claim[:50] + ("..." if len(claim) > 50 else "")
        dim = ins.get("dim", "")
        ev_count = len(ins.get("evidence", []))
        existing_summary.append(f"{i+1}. [{dim}] {claim_sig} (证据{ev_count}条)")

    # 创建输出目录
    prompts_dir = Path(person_dir) / "_per_file_prompts"
    prompts_dir.mkdir(exist_ok=True)

    # 为每篇新素材生成prompt
    print(f"生成逐篇增量蒸馏prompts: {len(new_materials)} 个文件")
    prompt_index = 0
    for i, mat in enumerate(new_materials, 1):
        material_filename = mat["filename"]
        material_content = mat["content"]

        # 按段落切分（大文件自动切分）
        chunks = split_material_into_chunks(material_content, material_filename, max_chars=15000, overlap_paragraphs=2)

        # 打印切分统计
        if len(chunks) > 1:
            print(f"  [{i}/{len(new_materials)}] {material_filename}: 切分为 {len(chunks)} 个chunks (共{len(material_content):,}字符)")
        else:
            print(f"  [{i}/{len(new_materials)}] {material_filename}: {len(material_content):,}字符")

        # 为每个chunk生成prompt
        for chunk in chunks:
            prompt_index += 1

            # 构建chunk说明（如果有切分）
            chunk_info = ""
            if chunk["total_chunks"] > 1:
                chunk_info = f"\n**说明**: 本文件较大，已切分为第 {chunk['index']}/{chunk['total_chunks']} 部分，chunk之间有2个段落重叠以保持上下文连贯性。"
                result_filename = chunk["result_filename"]
            else:
                result_filename = f"{material_filename}.json"

            # 构建prompt
            prompt = f"""# 增量蒸馏 Prompt（逐篇蒸馏模式）

你是人格蒸馏专家。以下是 **{person_name}** 已有的insights列表摘要，以及 **一篇** 新增素材。

## 已有insights摘要（用于避免重复）

"""
            if existing_summary:
                prompt += "\n".join(existing_summary)
            else:
                prompt += "（无已有insights）"

            prompt += f"""

## 任务说明

这是逐篇增量蒸馏模式：你只需要从这一篇新素材中提取：
1. **新的 insights**：不同于已有insights的观点
2. **补充证据**：如果新素材为已有insight提供了补充证据{chunk_info}

## 输出格式

只输出以下JSON格式（不要有任何其他文字）：

```json
{{
  "new_insights": [
    {{
      "claim": "观点表述（100-300字，蒸馏后的核心论断）",
      "dim": "维度分类（stance/cognitive/method/decision/communication/logical_tracing/tension/situational等）",
      "evidence": [
        {{
          "excerpt": "原文中支撑此观点的关键段落（100-500字，必须是原文原文原文，不是转述）",
          "source_file": "{material_filename}",
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
          "source_file": "{material_filename}",
          "location": "位置描述"
        }}
      ]
    }}
  ]
}}
```

## 提取指南

### new_insights（完全新增的观点）
- **claim**: 观点表述（100-300字），蒸馏后的核心论断
- **dim**: 维度分类（stance, cognitive, method, decision, communication, logical_tracing, tension, situational 等）
- **evidence**: 支撑该观点的原文证据
  - **excerpt**: 原文原文原文（100-500字），一字不改
  - **source_file**: 必须是 "{material_filename}"
  - **location**: 在文件中的位置描述

### merge_into（补充已有观点的证据）
- **claim_signature**: 取已有insight的claim前50字，用于匹配目标insight
- **additional_evidence**: 新素材中补充的原文证据

## 新增素材

**人物**: {person_name}
**素材文件**: {material_filename}
**字符数**: {chunk['char_count']:,}

--- 素材开始 ---
{chunk['content']}
--- 素材结束 ---

## 输出要求

1. 只输出上述JSON格式，不要输出其他字段
2. excerpt 必须是原文原文原文，一字不改
3. 如果素材中没有新观点也没有补充证据，输出 `{{"new_insights": [], "merge_into": []}}`
4. 输出必须是合法 JSON
"""

            # 保存prompt（使用 _prompt_inc_ 前缀区分增量蒸馏）
            # 如果是chunk，文件名格式: _prompt_inc_{index}_{filename}_chunk{N}.md
            chunk_suffix = chunk["chunk_suffix"]
            prompt_filename = f"_prompt_inc_{prompt_index:03d}_{material_filename}{chunk_suffix}.md"
            prompt_path = prompts_dir / prompt_filename
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(prompt)

            if chunk["total_chunks"] > 1:
                print(f"    - chunk {chunk['index']}/{chunk['total_chunks']}: {prompt_filename}")

    # 保存本次使用的素材文件列表（供 --apply 时使用）
    material_files = [mat["filename"] for mat in new_materials]
    materials_list_path = Path(person_dir) / "_incremental_materials.json"
    with open(materials_list_path, "w", encoding="utf-8") as f:
        json.dump({"materials": material_files}, f, ensure_ascii=False, indent=2)

    print(f"\n已生成 {prompt_index} 个prompts到: {prompts_dir}")
    print(f"  - 已有insights: {len(existing_insights)}条")
    print(f"  - 新素材: {len(new_materials)}篇")
    print(f"\n下一步：")
    print(f"  1. 在 AI 助手中逐个执行这些 prompts")
    print(f"  2. 将每个结果保存到 {person_dir}/_per_file_results/{{filename}}.json")
    print(f"     （如果文件被切分，结果文件名会带有 _chunk{N} 后缀）")
    print(f"  3. 执行: python3 incremental_distill.py {person_dir} --apply")


def apply_incremental_result(person_dir, pv3_data):
    """应用LLM返回的增量结果（从 _per_file_results/ 读取逐篇结果）"""
    results_dir = Path(person_dir) / "_per_file_results"
    if not results_dir.exists():
        print(f"错误: 结果目录不存在: {results_dir}", file=sys.stderr)
        print(f"请先生成逐篇蒸馏prompts并执行蒸馏", file=sys.stderr)
        print(f"提示: 执行 python3 incremental_distill.py {person_dir} --auto", file=sys.stderr)
        sys.exit(1)

    # 读取所有结果文件
    result_files = sorted(results_dir.glob("*.json"))
    if not result_files:
        print(f"错误: {results_dir} 中没有 JSON 文件", file=sys.stderr)
        print(f"请先在 AI 助手中执行蒸馏prompts，并将结果保存到 {results_dir}/", file=sys.stderr)
        sys.exit(1)

    print(f"读取逐篇增量蒸馏结果: {len(result_files)} 个文件")

    # 合并所有结果中的 new_insights 和 merge_into
    all_new_insights = []
    all_merge_into = []
    for result_file in result_files:
        try:
            result = json.loads(result_file.read_text(encoding='utf-8'))
            if isinstance(result, dict):
                new_insights = result.get("new_insights", [])
                merge_into = result.get("merge_into", [])
                if isinstance(new_insights, list):
                    all_new_insights.extend(new_insights)
                if isinstance(merge_into, list):
                    all_merge_into.extend(merge_into)
                print(f"  ✓ {result_file.name}: {len(new_insights)} new insights, {len(merge_into)} merge_into")
            else:
                print(f"  ⚠ {result_file.name}: 不是字典格式，跳过")
        except Exception as e:
            print(f"  ✗ {result_file.name}: 读取失败 - {e}")

    if not all_new_insights and not all_merge_into:
        print("警告: 所有增量结果均为空（无新insights也无合并）")
        return

    print(f"\n总计: {len(all_new_insights)} new insights, {len(all_merge_into)} merge_into")

    # 读取本次蒸馏使用的素材文件列表
    materials_list_path = Path(person_dir) / "_incremental_materials.json"
    distilled_files = []
    if materials_list_path.exists():
        with open(materials_list_path, "r", encoding="utf-8") as f:
            materials_data = json.load(f)
            distilled_files = materials_data.get("materials", [])

    # 获取已有insights
    existing_insights = pv3_data.get("insights_with_evidence", [])

    # 处理 merge_into
    merged_count = 0
    added_evidence_count = 0
    for merge_item in all_merge_into:
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
    added_insights_count = 0
    for ins in all_new_insights:
        # 验证格式
        if not ins.get("claim"):
            print(f"警告: 跳过无claim的insight")
            continue
        existing_insights.append(ins)
        added_insights_count += 1

    # 更新元数据
    if "_distill_meta" not in pv3_data:
        pv3_data["_distill_meta"] = {}
    meta = pv3_data["_distill_meta"]
    meta["timestamp"] = datetime.now().isoformat()
    meta["source_count"] = meta.get("source_count", 0) + added_insights_count  # 粗略估计

    # 保存
    save_personality_v3(person_dir, pv3_data)

    # 更新DB中的蒸馏状态
    db_path = Path(person_dir) / "_db.sqlite"
    db_conn = None
    try:
        if db_path.exists():
            db_conn = sqlite3.connect(str(db_path))
            now_iso = datetime.now().isoformat()
            for filename in distilled_files:
                distillate_path = None
                for cat in ["materials", "papers"]:
                    candidate = Path(person_dir) / cat / "distillates" / f"{filename}.json"
                    if candidate.exists():
                        distillate_path = str(candidate.relative_to(person_dir))
                        break
                db_conn.execute(
                    "UPDATE items SET distilled=1, status='distilled', distillate_path=?, distill_time=? WHERE filename=?",
                    (distillate_path, now_iso, filename)
                )
            for result_file in result_files:
                try:
                    result_data = json.loads(result_file.read_text(encoding='utf-8'))
                    insights = result_data.get("new_insights", []) if isinstance(result_data, dict) else (result_data if isinstance(result_data, list) else [])
                    source_file = ""
                    if isinstance(result_data, dict):
                        ni = result_data.get("new_insights", [])
                        if ni:
                            source_file = ni[0].get("evidence", [{}])[0].get("source_file", "")
                    elif isinstance(result_data, list) and result_data:
                        source_file = result_data[0].get("evidence", [{}])[0].get("source_file", "")
                    if source_file:
                        db_conn.execute(
                            "UPDATE items SET insight_count=? WHERE filename=?",
                            (len(insights), source_file)
                        )
                except Exception:
                    pass
            db_conn.execute(
                "INSERT INTO processing_log (person, action, details) VALUES (?, 'incremental_distill', ?)",
                (Path(person_dir).name, f"new_insights={added_insights_count}, merged={merged_count}, evidence_added={added_evidence_count}")
            )
            db_conn.commit()
            db_conn.close()
            db_conn = None
    except Exception as e:
        print(f"  ⚠ DB更新失败: {e}", file=sys.stderr)
    finally:
        if db_conn:
            try:
                db_conn.close()
            except Exception:
                pass

    # 打印统计
    print("\n增量蒸馏完成:")
    print(f"  - 新增insights: {added_insights_count}条")
    print(f"  - 合并已有insights: {merged_count}条")
    print(f"  - 追加evidence: {added_evidence_count}条")
    print(f"  - 当前总insights: {len(existing_insights)}条")

    print(f"\n清理建议（可选）：")
    print(f"  rm -rf {person_dir}/_per_file_prompts")
    print(f"  rm -rf {person_dir}/_per_file_results")
    print(f"  rm {person_dir}/_incremental_materials.json")


def main():
    parser = argparse.ArgumentParser(
        description="增量蒸馏工具（逐篇蒸馏模式） — 生成prompts或应用结果",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 指定新素材文件生成逐篇prompts
  python3 incremental_distill.py /path/to/person/dir --new-materials file1.txt file2.txt

  # 按修改时间自动选新素材（2026-04-26之后修改的）
  python3 incremental_distill.py /path/to/person/dir --since "2026-04-26"

  # 自动找出未蒸馏的新素材（推荐）
  python3 incremental_distill.py /path/to/person/dir --auto

  # 应用LLM返回的增量结果（从 _per_file_results/ 读取）
  python3 incremental_distill.py /path/to/person/dir --apply

工作流程:
  1. 执行 --auto 生成逐篇蒸馏prompts到 _per_file_prompts/
  2. 在 AI 助手中逐个执行这些prompts
  3. 将每个结果保存到 _per_file_results/{filename}.json
  4. 执行 --apply 合并结果到 personality_v3.json
        """
    )

    parser.add_argument("person_dir", help="人物目录路径（包含personality_v3.json）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--new-materials", nargs="+", metavar="FILE",
                       help="新增素材文件名列表（相对materials目录或绝对路径）")
    group.add_argument("--since", metavar="DATE",
                       help="按修改时间筛选新素材（格式: YYYY-MM-DD）")
    group.add_argument("--auto", action="store_true",
                       help="自动找出未蒸馏的新素材并生成逐篇prompts（推荐，需要已有_distilled_files.json）")
    group.add_argument("--apply", action="store_true",
                       help="应用_per_file_results/中的逐篇增量结果")

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
        elif args.auto:
            # 自动模式：找出未蒸馏的素材
            new_files = find_new_materials_auto(person_dir)
            if not new_files:
                print("没有未蒸馏的新素材")
                sys.exit(0)
            print(f"找到 {len(new_files)} 个未蒸馏的新素材文件:")
            for f in new_files:
                print(f"  - {f.name}")
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

        generate_per_file_prompts(person_dir, new_materials, pv3_data)


if __name__ == "__main__":
    main()
