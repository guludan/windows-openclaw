#!/usr/bin/env python3
"""
regenerate_personality.py - 基于已蒸馏insights重新生成完整人格模型(v3: 多批次)

用法:
    python3 regenerate_personality.py <person_dir>                    # 生成prompt(s)
    python3 regenerate_personality.py <person_dir> --apply            # 应用结果
    python3 regenerate_personality.py --all                           # 批量生成所有prompt
    python3 regenerate_personality.py --all --apply                   # 批量apply

多批次流程(insights > 100时自动启用):
    1. 按dim分组，每组独立生成prompt
    2. 每批LLM合并该dim内的相似insights，输出merged_from
    3. 额外一个汇总prompt生成顶层字段(brief/school/domains等)
    4. apply阶段统一合并所有批次结果+回填evidence

单批次流程(insights <= 100):
    直接用全部insights一次性生成（走旧模板）

v3改动:
    - 不再compact_insights截断丢弃insights（之前的bug源）
    - 改为按dim分批，每批内部由LLM合并相似观点
    - 所有原始insights都参与，不丢弃
    - 汇总prompt用各dim的合并摘要生成顶层字段
"""
import json, sys, re, os
from pathlib import Path
from datetime import datetime
from collections import defaultdict, Counter

TEMPLATE_SINGLE = Path("~/.openclaw/workspace/skills/think-tank/templates/regenerate_from_insights.md").expanduser()
TEMPLATE_BATCH = Path("~/.openclaw/workspace/skills/think-tank/templates/regenerate_batch_dim.md").expanduser()
TEMPLATE_SUMMARY = Path("~/.openclaw/workspace/skills/think-tank/templates/regenerate_summary.md").expanduser()
PERSONALITIES_ROOT = Path("/mnt/d/data/think-tank-data/personalities")

# 每批最大insights数（超过此数则按dim再拆分子批）
BATCH_SIZE = 80

# --- 工具函数 ---

def token_overlap(a, b):
    """两个字符串的token重叠度 (0-1)"""
    ta = set(a.lower().split())
    tb = set(b.lower().split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta | tb), 1)

# --- 多批次: 生成prompts ---

def generate_prompts(person_dir):
    """
    生成多批次prompt:
    - _regenerate_batch_0.md, _regenerate_batch_1.md, ... (按dim分批，LLM合并insights)
    - _regenerate_summary.md (汇总prompt，生成顶层字段)
    - _regenerate_manifest.json (批次元数据，apply时用)
    """
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

    # 清理旧的临时文件
    for old in person_dir.glob("_regenerate_batch_*.md"):
        old.unlink()
    for old in person_dir.glob("_regenerate_batch_result_*.json"):
        old.unlink()
    for old in [person_dir / "_regenerate_summary.md",
                person_dir / "_regenerate_summary_result.json",
                person_dir / "_regenerate_manifest.json",
                person_dir / "_regenerate_merge_map.json",
                person_dir / "_regenerate_prompt.md",
                person_dir / "_regenerate_result.json"]:
        if old.exists():
            old.unlink()

    total_insights = len(iwe)

    if total_insights <= BATCH_SIZE:
        # 单批次模式：走旧模板
        return _generate_single_batch(person_dir, pv3, iwe)

    # 多批次模式：按dim分批
    return _generate_multi_batch(person_dir, pv3, iwe)


def _generate_single_batch(person_dir, pv3, iwe):
    """单批次：直接用旧模板"""
    template = TEMPLATE_SINGLE.read_text(encoding="utf-8")

    insights_summary = []
    for i, ins in enumerate(iwe):
        entry = {
            "index": i + 1,
            "claim": ins.get("claim", ""),
            "dim": ins.get("dim", ""),
            "evidence_count": len(ins.get("evidence", [])),
        }
        insights_summary.append(entry)

    prompt = template.replace("{count}", str(len(iwe)))
    prompt = prompt.replace("{insights_json}", json.dumps(insights_summary, ensure_ascii=False, indent=2))

    prompt_path = person_dir / "_regenerate_prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")

    # 写manifest标记单批次
    manifest = {
        "mode": "single",
        "total_insights": len(iwe),
        "batches": [],
        "created_at": datetime.now().isoformat()
    }
    (person_dir / "_regenerate_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[单批次模式] {person_dir.name}")
    print(f"  insights: {len(iwe)}, prompt: {len(prompt)//1024}KB")


def _generate_multi_batch(person_dir, pv3, iwe):
    """多批次：按dim分组，每组一个prompt"""
    template_text = TEMPLATE_BATCH.read_text(encoding="utf-8") if TEMPLATE_BATCH.exists() else None
    summary_template_text = TEMPLATE_SUMMARY.read_text(encoding="utf-8") if TEMPLATE_SUMMARY.exists() else None

    if not template_text:
        print("ERROR: regenerate_batch_dim.md template not found", file=sys.stderr)
        sys.exit(1)
    if not summary_template_text:
        print("ERROR: regenerate_summary.md template not found", file=sys.stderr)
        sys.exit(1)

    # 按dim分组
    by_dim = defaultdict(list)
    for i, ins in enumerate(iwe):
        dim = ins.get("dim", "unknown")
        by_dim[dim].append((i + 1, ins))  # 1-indexed

    # 如果单个dim超过BATCH_SIZE，拆成子批
    batches = []
    batch_id = 0
    for dim, items in sorted(by_dim.items()):
        if len(items) <= BATCH_SIZE:
            batches.append({
                "id": batch_id,
                "dim": dim,
                "indices": [idx for idx, _ in items],
                "label": f"dim={dim}"
            })
            batch_id += 1
        else:
            # 拆分子批
            for sub_start in range(0, len(items), BATCH_SIZE):
                sub = items[sub_start:sub_start + BATCH_SIZE]
                batches.append({
                    "id": batch_id,
                    "dim": dim,
                    "indices": [idx for idx, _ in sub],
                    "label": f"dim={dim} part{(sub_start // BATCH_SIZE) + 1}"
                })
                batch_id += 1

    manifest = {
        "mode": "multi",
        "total_insights": len(iwe),
        "batches": batches,
        "summary_needed": True,
        "created_at": datetime.now().isoformat()
    }

    # 生成每批prompt
    for batch in batches:
        batch_insights = []
        for idx in batch["indices"]:
            ins = iwe[idx - 1]
            batch_insights.append({
                "index": idx,
                "claim": ins.get("claim", ""),
                "dim": ins.get("dim", ""),
                "evidence_count": len(ins.get("evidence", [])),
            })

        prompt = template_text.replace("{dim}", batch["label"])
        prompt = prompt.replace("{count}", str(len(batch_insights)))
        prompt = prompt.replace("{insights_json}", json.dumps(batch_insights, ensure_ascii=False, indent=2))

        prompt_path = person_dir / f"_regenerate_batch_{batch['id']}.md"
        prompt_path.write_text(prompt, encoding="utf-8")

    # 生成汇总prompt（用于生成顶层字段）
    dim_summaries = []
    for dim, items in sorted(by_dim.items()):
        dim_summaries.append({
            "dim": dim,
            "count": len(items),
            "sample_claims": [ins.get("claim", "")[:150] for _, ins in items[:5]]
        })

    summary_prompt = summary_template_text.replace("{dim_count}", str(len(by_dim)))
    summary_prompt = summary_prompt.replace("{total_insights}", str(len(iwe)))
    summary_prompt = summary_prompt.replace("{dim_summaries}", json.dumps(dim_summaries, ensure_ascii=False, indent=2))

    (person_dir / "_regenerate_summary.md").write_text(summary_prompt, encoding="utf-8")

    # 保存manifest
    (person_dir / "_regenerate_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[多批次模式] {person_dir.name}")
    print(f"  总insights: {len(iwe)}, dim数: {len(by_dim)}, 批次数: {len(batches)}")
    print(f"  dim分布: {', '.join(f'{d}={len(items)}' for d, items in sorted(by_dim.items()))}")


# --- 应用结果 ---

def apply_result(person_dir):
    person_dir = Path(person_dir)
    manifest_path = person_dir / "_regenerate_manifest.json"

    if not manifest_path.exists():
        # 兼容旧流程：检查单文件result
        old_result = person_dir / "_regenerate_result.json"
        if old_result.exists():
            _apply_single_legacy(person_dir)
        else:
            print(f"ERROR: no manifest or result found for {person_dir.name}", file=sys.stderr)
            sys.exit(1)
        return

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if manifest["mode"] == "single":
        _apply_single(person_dir, manifest)
    else:
        _apply_multi(person_dir, manifest)


def _apply_single(person_dir, manifest):
    """单批次apply"""
    result_path = person_dir / "_regenerate_result.json"
    if not result_path.exists():
        print(f"ERROR: {result_path} not found", file=sys.stderr)
        sys.exit(1)

    pv3_path = person_dir / "personality_v3.json"
    pv3 = json.loads(pv3_path.read_text(encoding="utf-8"))
    original_iwe = pv3.get("insights_with_evidence", [])

    new_data = _parse_json_file(result_path)
    if new_data is None:
        sys.exit(1)

    merged_iwe = _merge_insights(new_data.get("insights_with_evidence", []), original_iwe)
    _save_result(person_dir, pv3, new_data, merged_iwe, original_iwe)
    _cleanup(person_dir)


def _apply_single_legacy(person_dir):
    """兼容旧的regenerate_result.json流程"""
    pv3_path = person_dir / "personality_v3.json"
    result_path = person_dir / "_regenerate_result.json"

    pv3 = json.loads(pv3_path.read_text(encoding="utf-8"))
    original_iwe = pv3.get("insights_with_evidence", [])

    new_data = _parse_json_file(result_path)
    if new_data is None:
        sys.exit(1)

    merged_iwe = _merge_insights(new_data.get("insights_with_evidence", []), original_iwe)
    _save_result(person_dir, pv3, new_data, merged_iwe, original_iwe)
    _cleanup(person_dir)


def _apply_multi(person_dir, manifest):
    """多批次apply"""
    pv3_path = person_dir / "personality_v3.json"
    pv3 = json.loads(pv3_path.read_text(encoding="utf-8"))
    original_iwe = pv3.get("insights_with_evidence", [])

    # 收集所有批次结果
    all_batch_insights = []
    used_indices = set()
    missing_batches = []

    for batch_info in manifest["batches"]:
        batch_id = batch_info["id"]
        result_path = person_dir / f"_regenerate_batch_result_{batch_id}.json"

        if not result_path.exists():
            missing_batches.append(batch_id)
            continue

        batch_data = _parse_json_file(result_path)
        if batch_data is None:
            missing_batches.append(batch_id)
            continue

        batch_iwe = batch_data.get("insights_with_evidence", [])
        if not batch_iwe:
            continue

        for ins in batch_iwe:
            merged_from = ins.get("merged_from", [])
            if merged_from:
                all_batch_insights.append(ins)
                used_indices.update(merged_from)

    if missing_batches:
        print(f"  ⚠️ 缺失批次: {missing_batches}，对应insights将原样保留")

    # 读取汇总结果（顶层字段）
    summary_path = person_dir / "_regenerate_summary_result.json"
    top_level_data = {}
    if summary_path.exists():
        top_level_data = _parse_json_file(summary_path) or {}

    # 合并evidence
    merged_iwe = []
    for ins in all_batch_insights:
        merged_from = ins.get("merged_from", [])
        all_evidence = []
        dims_from_orig = []
        for idx in merged_from:
            if 0 < idx <= len(original_iwe):
                orig = original_iwe[idx - 1]
                for ev in orig.get("evidence", []):
                    key = (ev.get("excerpt", ""), ev.get("source_file", ""))
                    if not any((e.get("excerpt", ""), e.get("source_file", "")) == key for e in all_evidence):
                        all_evidence.append(ev)
                if orig.get("dim"):
                    dims_from_orig.append(orig["dim"])

        final_dim = ins.get("dim", "")
        if not final_dim and dims_from_orig:
            final_dim = Counter(dims_from_orig).most_common(1)[0][0]

        merged_iwe.append({
            "claim": ins.get("claim", ""),
            "dim": final_dim,
            "evidence": all_evidence
        })

    # 未被任何批次覆盖的insights，尝试合并到已有insight
    all_expected = set(range(1, len(original_iwe) + 1))
    missed = all_expected - used_indices

    if missed:
        # 按dim建立已有insight索引
        dim_map = defaultdict(list)
        for mi, m_ins in enumerate(merged_iwe):
            dim_map[m_ins.get('dim', '')].append(mi)

        unmerged = []
        for idx in sorted(missed):
            orig = original_iwe[idx - 1]
            orig_claim = orig.get('claim', '')
            orig_dim = orig.get('dim', '')

            best_match = -1
            best_score = 0.0
            for mi in dim_map.get(orig_dim, []):
                score = token_overlap(orig_claim, merged_iwe[mi].get('claim', ''))
                if score > best_score:
                    best_score = score
                    best_match = mi

            if best_score >= 0.4 and best_match >= 0:
                existing_ev = merged_iwe[best_match].get('evidence', [])
                existing_keys = {(e.get('excerpt', ''), e.get('source_file', '')) for e in existing_ev}
                for ev in orig.get('evidence', []):
                    key = (ev.get('excerpt', ''), ev.get('source_file', ''))
                    if key not in existing_keys:
                        existing_ev.append(ev)
                merged_iwe[best_match]['evidence'] = existing_ev
            else:
                unmerged.append(orig)

        if unmerged:
            merged_iwe.extend(unmerged)

        merged_count = len(missed) - len(unmerged)
        print(f"  📦 {len(missed)} 条未覆盖insights: {merged_count}条合并, {len(unmerged)}条保留")

    # 构建最终数据
    final_data = {}
    # 顶层字段优先用summary结果，兜底用旧pv3
    for key in ["brief", "school", "domains", "stance_anchors", "cognitive_patterns",
                "analytical_frameworks", "logical_tracing", "decision_patterns",
                "situational_triggers", "communication_profile", "evolution_trajectory",
                "key_aphorisms"]:
        if top_level_data and top_level_data.get(key):
            final_data[key] = top_level_data[key]
        elif pv3.get(key):
            final_data[key] = pv3[key]
        # 缺失的字段不填，宁缺毋滥

    _save_result(person_dir, pv3, final_data, merged_iwe, original_iwe)
    _cleanup(person_dir)


def _merge_insights(new_iwe_raw, original_iwe):
    """单批次模式的insights合并（evidence回填）"""
    used_indices = set()
    merged_iwe = []

    for new_ins in new_iwe_raw:
        merged_from = new_ins.get("merged_from", [])
        if merged_from and original_iwe:
            all_evidence = []
            dims_from_orig = []
            for idx in merged_from:
                if 0 < idx <= len(original_iwe):
                    orig = original_iwe[idx - 1]
                    for ev in orig.get("evidence", []):
                        key = (ev.get("excerpt", ""), ev.get("source_file", ""))
                        if not any((e.get("excerpt", ""), e.get("source_file", "")) == key for e in all_evidence):
                            all_evidence.append(ev)
                    if orig.get("dim"):
                        dims_from_orig.append(orig["dim"])
                    used_indices.add(idx)

            final_dim = new_ins.get("dim", "")
            if not final_dim and dims_from_orig:
                final_dim = Counter(dims_from_orig).most_common(1)[0][0]

            merged_iwe.append({
                "claim": new_ins.get("claim", ""),
                "dim": final_dim,
                "evidence": all_evidence
            })
        else:
            item = {
                "claim": new_ins.get("claim", ""),
                "dim": new_ins.get("dim", ""),
                "evidence": new_ins.get("evidence", [])
            }
            if not item["evidence"]:
                item["_no_original_evidence"] = True
            merged_iwe.append(item)

    # 清理LLM编造的
    fabricated = sum(1 for i in merged_iwe if i.get("_no_original_evidence"))
    if fabricated > 0:
        merged_iwe = [i for i in merged_iwe if not i.get("_no_original_evidence")]
        print(f"  🧹 清理{fabricated}条LLM编造insight(无原始evidence)")

    # 未合并的原始insights处理
    if original_iwe and used_indices:
        all_expected = set(range(1, len(original_iwe) + 1))
        missed = all_expected - used_indices
        if missed:
            dim_map = defaultdict(list)
            for mi, m_ins in enumerate(merged_iwe):
                dim_map[m_ins.get('dim', '')].append(mi)

            unmerged = []
            for idx in sorted(missed):
                orig = original_iwe[idx - 1]
                orig_claim = orig.get('claim', '')
                orig_dim = orig.get('dim', '')

                best_match = -1
                best_score = 0.0
                for mi in dim_map.get(orig_dim, []):
                    score = token_overlap(orig_claim, merged_iwe[mi].get('claim', ''))
                    if score > best_score:
                        best_score = score
                        best_match = mi

                if best_score >= 0.4 and best_match >= 0:
                    existing_ev = merged_iwe[best_match].get('evidence', [])
                    existing_keys = {(e.get('excerpt', ''), e.get('source_file', '')) for e in existing_ev}
                    for ev in orig.get('evidence', []):
                        key = (ev.get('excerpt', ''), ev.get('source_file', ''))
                        if key not in existing_keys:
                            existing_ev.append(ev)
                    merged_iwe[best_match]['evidence'] = existing_ev
                else:
                    unmerged.append(orig)

            if unmerged:
                merged_iwe.extend(unmerged)

            merged_count = len(missed) - len(unmerged)
            print(f"  📦 {len(missed)} 条未覆盖insights: {merged_count}条合并, {len(unmerged)}条保留")

    return merged_iwe


def _parse_json_file(path):
    """解析JSON文件（处理markdown包裹）"""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
    candidate = m.group(1) if m else content

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse {path.name}: {e}", file=sys.stderr)
        return None


def _save_result(person_dir, pv3, new_data, merged_iwe, original_iwe):
    """保存最终结果"""
    pv3_path = person_dir / "personality_v3.json"

    # 备份
    backup_path = pv3_path.parent / f"personality_v3.json.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    import shutil
    shutil.copy2(pv3_path, backup_path)
    print(f"  Backup: {backup_path.name}")

    # 合并顶层字段
    final = {}
    for key in ["brief", "school", "domains", "stance_anchors", "cognitive_patterns",
                "analytical_frameworks", "logical_tracing", "decision_patterns",
                "situational_triggers", "communication_profile", "evolution_trajectory",
                "key_aphorisms"]:
        if new_data.get(key):
            final[key] = new_data[key]

    final["insights_with_evidence"] = merged_iwe
    final["_distill_meta"] = pv3.get("_distill_meta", {})
    final["_distill_meta"]["version"] = "v3-regenerated"
    final["_distill_meta"]["regenerated_at"] = datetime.now().isoformat()
    final["_distill_meta"]["pre_merge_count"] = len(original_iwe)
    final["_distill_meta"]["post_merge_count"] = len(merged_iwe)

    pv3_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  ✅ Applied: {person_dir.name}")
    print(f"  Insights: {len(original_iwe)} → {len(merged_iwe)}")
    print(f"  Evidence: {sum(len(i.get('evidence',[])) for i in original_iwe)} → {sum(len(i.get('evidence',[])) for i in merged_iwe)}")


def _cleanup(person_dir):
    """清理临时文件"""
    for pattern in ["_regenerate_batch_*.md", "_regenerate_batch_result_*.json",
                    "_regenerate_summary.md", "_regenerate_summary_result.json",
                    "_regenerate_manifest.json", "_regenerate_merge_map.json",
                    "_regenerate_prompt.md", "_regenerate_result.json"]:
        for p in person_dir.glob(pattern):
            p.unlink()


# --- 批量操作 ---

def find_all_person_dirs():
    """找到所有有personality_v3.json的目录"""
    dirs = set()
    for pv3 in PERSONALITIES_ROOT.rglob("personality_v3.json"):
        # 排除备份文件所在目录的特殊情况
        d = pv3.parent
        if not d.name.startswith('.'):
            dirs.add(d)
    return sorted(dirs)


def batch_generate():
    dirs = find_all_person_dirs()
    print(f"Found {len(dirs)} personalities with v3")
    for d in dirs:
        try:
            generate_prompts(d)
        except Exception as e:
            print(f"ERROR {d.name}: {e}")
        print()


def batch_apply():
    dirs = find_all_person_dirs()
    applied = 0
    for d in dirs:
        # 有manifest或有旧result才apply
        has_manifest = (d / "_regenerate_manifest.json").exists()
        has_old_result = (d / "_regenerate_result.json").exists()
        if not has_manifest and not has_old_result:
            continue
        try:
            apply_result(d)
            applied += 1
        except Exception as e:
            print(f"ERROR {d.name}: {e}")
        print()
    print(f"Applied {applied}/{len(dirs)}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="基于insights重新生成完整人格模型(多批次)")
    parser.add_argument("person_dir", nargs="?", help="人物目录路径")
    parser.add_argument("--apply", action="store_true", help="应用regenerate结果")
    parser.add_argument("--all", action="store_true", help="批量操作所有人物")
    parser.add_argument("--list", action="store_true", help="列出所有待regenerate的人物")
    args = parser.parse_args()

    if args.list:
        dirs = find_all_person_dirs()
        for d in dirs:
            pv3 = json.loads((d / "personality_v3.json").read_text())
            ins = pv3.get("insights_with_evidence", [])
            meta = pv3.get("_distill_meta", {})
            is_regen = "regenerated" in meta.get("version", "")
            has_manifest = (d / "_regenerate_manifest.json").exists()
            has_result = (d / "_regenerate_result.json").exists()
            has_batch_results = list(d.glob("_regenerate_batch_result_*.json"))

            if is_regen:
                status = "✅已regen"
            elif has_batch_results:
                status = f"⏳{len(has_batch_results)}批结果"
            elif has_manifest:
                status = "⏳有manifest"
            elif has_result:
                status = "⏳有result"
            else:
                status = "⬜待处理"
            print(f"{status} {d.name}: {len(ins)} insights")
        return

    if args.all:
        if args.apply:
            batch_apply()
        else:
            batch_generate()
        return

    if not args.person_dir:
        parser.print_help()
        sys.exit(1)

    if args.apply:
        apply_result(args.person_dir)
    else:
        generate_prompts(args.person_dir)


if __name__ == "__main__":
    main()
