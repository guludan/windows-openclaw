#!/usr/bin/env python3
"""
regenerate_personality.py — 基于已蒸馏insights重新生成完整人格模型

用法:
    python3 regenerate_personality.py <person_dir>                    # 生成prompt
    python3 regenerate_personality.py <person_dir> --apply            # 应用结果

流程:
    1. 读取 personality_v3.json 中的 insights_with_evidence
    2. 生成重新生成的prompt（insights作为输入）
    3. sessions_spawn调LLM生成完整人格模型
    4. --apply: 替换顶层字段，保留原insights_with_evidence
"""
import json, sys
from pathlib import Path
from datetime import datetime

TEMPLATE = Path("~/.openclaw/workspace/skills/think-tank/templates/regenerate_from_insights.md").expanduser()

def generate_prompt(person_dir):
    person_dir = Path(person_dir)
    pv3_path = person_dir / "personality_v3.json"
    if not pv3_path.exists():
        print(f"ERROR: {pv3_path} not found", file=sys.stderr)
        sys.exit(1)
    
    pv3 = json.loads(pv3_path.read_text(encoding="utf-8"))
    iwe = pv3.get("insights_with_evidence", [])
    
    if not iwe:
        print(f"ERROR: {pv3_path} has no insights_with_evidence", file=sys.stderr)
        sys.exit(1)
    
    template = TEMPLATE.read_text(encoding="utf-8")
    
    # 准备insights摘要（不包含完整evidence，控制prompt大小）
    insights_summary = []
    for i, ins in enumerate(iwe):
        entry = {
            "index": i + 1,
            "claim": ins.get("claim", ""),
            "dim": ins.get("dim", ""),
            "evidence_count": len(ins.get("evidence", [])),
            "source_files": list(set(ev.get("source_file", "") for ev in ins.get("evidence", [])))
        }
        insights_summary.append(entry)
    
    prompt = template.replace("{count}", str(len(iwe)))
    prompt = prompt.replace("{insights_json}", json.dumps(insights_summary, ensure_ascii=False, indent=2))
    
    prompt_path = person_dir / "_regenerate_prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    
    print(f"Prompt saved: {prompt_path}")
    print(f"  {len(iwe)} insights as input")
    print(f"  Prompt size: {len(prompt)} chars")
    print(f"\nNext: spawn子session处理此prompt，输出到 {person_dir}/_regenerate_result.json")
    print(f"Then: python3 regenerate_personality.py {person_dir} --apply")


def apply_result(person_dir):
    person_dir = Path(person_dir)
    result_path = person_dir / "_regenerate_result.json"
    pv3_path = person_dir / "personality_v3.json"
    
    if not result_path.exists():
        print(f"ERROR: {result_path} not found", file=sys.stderr)
        sys.exit(1)
    
    with open(result_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 尝试解析JSON（处理markdown包裹）
    import re
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
    candidate = m.group(1) if m else content
    
    try:
        new_data = json.loads(candidate)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse JSON: {e}", file=sys.stderr)
        sys.exit(1)
    
    # 读取现有V3
    pv3 = json.loads(pv3_path.read_text(encoding="utf-8"))
    
    # 备份
    backup_path = pv3_path.parent / f"personality_v3.json.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    pv3_path.rename(backup_path)
    print(f"Backup: {backup_path}")
    
    # 合并：用新数据替换顶层字段，insights用LLM的merged_from做代码级evidence合并
    original_iwe = pv3.get("insights_with_evidence", [])
    new_iwe_raw = new_data.get("insights_with_evidence", [])
    
    # LLM输出包含merged_from字段时，用原始insights的evidence合并
    merged_iwe = []
    used_indices = set()
    
    for new_ins in new_iwe_raw:
        merged_from = new_ins.get("merged_from", [])
        if merged_from and original_iwe:
            # 收集所有被合并的原始insights的evidence
            all_evidence = []
            for idx in merged_from:
                if 0 < idx <= len(original_iwe):
                    orig = original_iwe[idx - 1]  # 1-indexed
                    for ev in orig.get("evidence", []):
                        key = (ev.get("excerpt", ""), ev.get("source_file", ""))
                        if not any((e.get("excerpt", ""), e.get("source_file", "")) == key for e in all_evidence):
                            all_evidence.append(ev)
                    used_indices.add(idx)
            merged_iwe.append({
                "claim": new_ins.get("claim", ""),
                "dim": new_ins.get("dim", ""),
                "evidence": all_evidence
            })
        else:
            # LLM没有merged_from（旧格式），直接使用
            merged_iwe.append(new_ins)
    
    # 检查是否有遗漏的原始insights
    if original_iwe and used_indices:
        all_expected = set(range(1, len(original_iwe) + 1))
        missed = all_expected - used_indices
        if missed:
            for idx in missed:
                orig = original_iwe[idx - 1]
                merged_iwe.append(orig)
            print(f"  ⚠️ {len(missed)} insights未被LLM合并，已原样保留")
    
    new_data["insights_with_evidence"] = merged_iwe
    new_data["_distill_meta"] = pv3.get("_distill_meta", {})
    new_data["_distill_meta"]["version"] = "v3-regenerated"
    new_data["_distill_meta"]["regenerated_at"] = datetime.now().isoformat()
    new_data["_distill_meta"]["pre_merge_count"] = len(original_iwe)
    new_data["_distill_meta"]["post_merge_count"] = len(merged_iwe)
    
    # 保存
    pv3_path.write_text(json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    print(f"Applied: {pv3_path}")
    dedup_count = len(original_iwe) - len(merged_iwe)
    print(f"  Insights: {len(original_iwe)} → {len(merged_iwe)} (merged {dedup_count})")
    print(f"  Updated: brief, school, domains, stance_anchors, cognitive_patterns, etc.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="基于insights重新生成完整人格模型")
    parser.add_argument("person_dir", help="人物目录路径")
    parser.add_argument("--apply", action="store_true", help="应用_regenerate_result.json")
    args = parser.parse_args()
    
    if args.apply:
        apply_result(args.person_dir)
    else:
        generate_prompt(args.person_dir)


if __name__ == "__main__":
    main()
