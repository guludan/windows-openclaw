#!/usr/bin/env python3
"""
regenerate_personality.py — 基于已蒸馏insights重新生成完整人格模型（v2: 两阶段）

用法:
    python3 regenerate_personality.py <person_dir>                    # 生成prompt
    python3 regenerate_personality.py <person_dir> --apply            # 应用结果
    python3 regenerate_personality.py --all                           # 批量生成所有prompt
    python3 regenerate_personality.py --all --apply                   # 批量apply

两阶段流程（insights > 100时自动启用）:
    阶段1: 本地按dim分组 + claim关键词去重 → 压缩到80-120条
    阶段2: 用压缩后的insights一次性生成完整人格模型

单阶段流程（insights <= 100）:
    直接用全部insights生成完整人格模型
"""
import json, sys, re, hashlib
from pathlib import Path
from datetime import datetime
from collections import defaultdict

TEMPLATE = Path("~/.openclaw/workspace/skills/think-tank/templates/regenerate_from_insights.md").expanduser()
PERSONALITIES_ROOT = Path("/mnt/d/data/think-tank-data/personalities")

# --- 工具函数 ---

def token_overlap(a, b):
    """两个字符串的token重叠度 (0-1)"""
    ta = set(a.lower().split())
    tb = set(b.lower().split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta | tb), 1)

def cluster_by_similarity(claims, threshold=0.55):
    """简单贪心聚类：将相似claim归为一簇"""
    clusters = []  # list of lists of (index, claim_text)
    used = set()
    
    for i, c in enumerate(claims):
        if i in used:
            continue
        cluster = [(i, c)]
        used.add(i)
        for j, c2 in enumerate(claims):
            if j in used:
                continue
            if token_overlap(c, c2) >= threshold:
                cluster.append((j, c2))
                used.add(j)
        clusters.append(cluster)
    return clusters

def compact_insights(iwe, target_max=100):
    """
    本地压缩insights：
    1. 按dim分组
    2. 同dim内按claim相似度聚类
    3. 每簇选最长claim作为代表，记录merged_indices
    返回压缩后的insights列表（含_merged_from字段）
    """
    # 按dim分组
    by_dim = defaultdict(list)
    for i, ins in enumerate(iwe):
        dim = ins.get("dim", "unknown")
        by_dim[dim].append((i, ins))
    
    compacted = []
    
    for dim, items in by_dim.items():
        claims = [ins.get("claim", "") for _, ins in items]
        clusters = cluster_by_similarity(claims, threshold=0.55)
        
        for cluster in clusters:
            # 选最长claim作为代表
            best_idx = max(cluster, key=lambda x: len(x[1]))[0]
            original_indices = [items[idx][0] for idx, _ in cluster]
            
            rep_ins = {
                "index": items[best_idx][0] + 1,  # 1-indexed
                "claim": items[best_idx][1].get("claim", ""),
                "dim": dim,
                "evidence_count": sum(len(items[idx][1].get("evidence", [])) for idx, _ in cluster),
                "_merged_from": [oi + 1 for oi in original_indices],  # 1-indexed
                "_is_cluster": len(cluster) > 1,
                "_cluster_size": len(cluster)
            }
            compacted.append(rep_ins)
    
    # 按dim排序
    dim_order = {"stance": 0, "cognitive": 1, "method": 2, "decision": 3, 
                 "communication": 4, "logical_tracing": 5, "tension": 6, "situational": 7}
    compacted.sort(key=lambda x: (dim_order.get(x.get("dim", ""), 9), -x.get("_cluster_size", 1)))
    
    # 如果压缩后仍然超过target，按比例截断确保每个dim都有代表
    if len(compacted) > target_max:
        # 按比例分配名额给每个dim
        from collections import Counter
        dim_counts = Counter(c.get("dim", "unknown") for c in compacted)
        total = len(compacted)
        dim_quota = {}
        remaining = target_max
        # 每个dim至少保留1个（如果有的话），然后按比例分配剩余
        for dim in dim_counts:
            min_quota = 1
            dim_quota[dim] = min_quota
            remaining -= min_quota
        # 按比例分配剩余名额
        if remaining > 0:
            for dim, cnt in dim_counts.items():
                extra = max(0, round(remaining * cnt / total))
                dim_quota[dim] += extra
        
        # 按quota从每个dim取前N条
        final = []
        dim_used = Counter()
        for c in compacted:
            dim = c.get("dim", "unknown")
            if dim_used[dim] < dim_quota.get(dim, 0):
                final.append(c)
                dim_used[dim] += 1
        # 如果还有空位，补上
        if len(final) < target_max:
            used_ids = set(id(c) for c in final)
            for c in compacted:
                if id(c) not in used_ids and len(final) < target_max:
                    final.append(c)
        compacted = final
    
    return compacted

# --- 阶段1: 生成prompt ---

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
    
    needs_compaction = len(iwe) > 100
    
    if needs_compaction:
        # 两阶段：先本地压缩
        compacted = compact_insights(iwe, target_max=90)
        
        # 生成摘要（不含evidence，控制大小）
        insights_summary = []
        for ins in compacted:
            entry = {
                "index": ins["index"],
                "claim": ins["claim"][:300],
                "dim": ins["dim"],
                "evidence_count": ins["evidence_count"],
            }
            if ins.get("_is_cluster"):
                entry["note"] = f"此条由{ins['_cluster_size']}条相似insight合并"
                entry["merged_from"] = ins["_merged_from"]
            insights_summary.append(entry)
        
        prompt = template.replace("{count}", str(len(compacted)))
        prompt = prompt.replace("{insights_json}", json.dumps(insights_summary, ensure_ascii=False, indent=2))
        
        # 保存压缩映射供apply使用
        merge_map = {}
        for ins in compacted:
            for orig_idx in ins.get("_merged_from", [ins["index"]]):
                merge_map[orig_idx] = ins["index"]
        merge_map_path = person_dir / "_regenerate_merge_map.json"
        merge_map_path.write_text(json.dumps({
            "compacted_count": len(compacted),
            "original_count": len(iwe),
            "merge_map": merge_map,
            "compacted": compacted
        }, ensure_ascii=False), encoding="utf-8")
        
        print(f"[两阶段模式] {person_dir.name}")
        print(f"  原始insights: {len(iwe)} → 压缩后: {len(compacted)}")
    else:
        # 单阶段：直接用全部
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
        
        print(f"[单阶段模式] {person_dir.name}")
        print(f"  insights: {len(iwe)}")
    
    prompt_path = person_dir / "_regenerate_prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    
    print(f"  Prompt size: {len(prompt)//1024}KB")
    print(f"  Prompt saved: {prompt_path}")

# --- 阶段2: 应用结果 ---

def apply_result(person_dir):
    person_dir = Path(person_dir)
    result_path = person_dir / "_regenerate_result.json"
    pv3_path = person_dir / "personality_v3.json"
    merge_map_path = person_dir / "_regenerate_merge_map.json"
    
    if not result_path.exists():
        print(f"ERROR: {result_path} not found", file=sys.stderr)
        sys.exit(1)
    
    with open(result_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 尝试解析JSON（处理markdown包裹）
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
    candidate = m.group(1) if m else content
    
    try:
        new_data = json.loads(candidate)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse JSON: {e}", file=sys.stderr)
        sys.exit(1)
    
    # 读取现有V3
    pv3 = json.loads(pv3_path.read_text(encoding="utf-8"))
    original_iwe = pv3.get("insights_with_evidence", [])
    
    # 备份
    backup_path = pv3_path.parent / f"personality_v3.json.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    import shutil
    shutil.copy2(pv3_path, backup_path)
    print(f"Backup: {backup_path}")
    
    # --- insights合并 ---
    new_iwe_raw = new_data.get("insights_with_evidence", [])
    used_indices = set()
    merged_iwe = []
    
    # 检查是否有两阶段的merge_map
    has_merge_map = merge_map_path.exists()
    compacted_info = None
    if has_merge_map:
        compacted_info = json.loads(merge_map_path.read_text(encoding="utf-8"))
    
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
            # dim: LLM输出优先，否则从原始insights取众数
            final_dim = new_ins.get("dim", "")
            if not final_dim and dims_from_orig:
                from collections import Counter
                final_dim = Counter(dims_from_orig).most_common(1)[0][0]
            merged_iwe.append({
                "claim": new_ins.get("claim", ""),
                "dim": final_dim,
                "evidence": all_evidence
            })
        else:
            # 无merged_from的insight（LLM新增或遗漏）
            # 如果没有evidence，标记为待清理
            item = {
                "claim": new_ins.get("claim", ""),
                "dim": new_ins.get("dim", ""),
                "evidence": new_ins.get("evidence", [])
            }
            if not item["evidence"]:
                item["_no_original_evidence"] = True
            merged_iwe.append(item)
    
    # 清理LLM编造的无evidence insights
    fabricated = sum(1 for i in merged_iwe if i.get("_no_original_evidence"))
    if fabricated > 0:
        merged_iwe = [i for i in merged_iwe if not i.get("_no_original_evidence")]
        print(f"  🧹 清理{fabricated}条LLM编造insight（无原始evidence）")
    
    # 如果有两阶段merge_map，将压缩映射中的原始indices也标记为已使用
    if has_merge_map and compacted_info:
        # LLM可能用压缩后的index引用，需要展开到原始indices
        compacted_list = compacted_info.get("compacted", [])
        expanded_used = set()
        for idx in list(used_indices):
            expanded_used.add(idx)
            # 检查这个idx是否是某个压缩代表
            for c in compacted_list:
                if c["index"] == idx:
                    for orig_idx in c.get("_merged_from", []):
                        expanded_used.add(orig_idx)
        used_indices = expanded_used
    
    # 检查遗漏
    if original_iwe and used_indices:
        if has_merge_map and compacted_info:
            # 两阶段模式：保留未合并的压缩代表，不展开回原始insights
            compacted_list = compacted_info.get("compacted", [])
            missed_compacted = [c for c in compacted_list if c["index"] not in used_indices]
            if missed_compacted:
                for c in missed_compacted:
                    # 保留压缩代表的claim，合并其包含的所有原始evidence
                    all_ev = []
                    for orig_idx in c.get("_merged_from", [c["index"]]):
                        if 0 < orig_idx <= len(original_iwe):
                            for ev in original_iwe[orig_idx - 1].get("evidence", []):
                                all_ev.append(ev)
                    merged_iwe.append({
                        "claim": c.get("claim", ""),
                        "dim": c.get("dim", ""),
                        "evidence": all_ev
                    })
                print(f"  ℹ️ {len(missed_compacted)} 个压缩代表未被LLM合并，已保留（含合并后evidence）")
        else:
            # 单阶段模式：原始insights未被合并的逐条保留
            all_expected = set(range(1, len(original_iwe) + 1))
            missed = all_expected - used_indices
            if missed:
                for idx in sorted(missed):
                    orig = original_iwe[idx - 1]
                    merged_iwe.append(orig)
                print(f"  ⚠️ {len(missed)} insights未被合并，已原样保留")
    
    new_data["insights_with_evidence"] = merged_iwe
    new_data["_distill_meta"] = pv3.get("_distill_meta", {})
    new_data["_distill_meta"]["version"] = "v3-regenerated"
    new_data["_distill_meta"]["regenerated_at"] = datetime.now().isoformat()
    new_data["_distill_meta"]["pre_merge_count"] = len(original_iwe)
    new_data["_distill_meta"]["post_merge_count"] = len(merged_iwe)
    
    # 保存
    pv3_path.write_text(json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    print(f"Applied: {person_dir.name}")
    print(f"  Insights: {len(original_iwe)} → {len(merged_iwe)}")
    
    # 清理临时文件
    for tmp in ["_regenerate_prompt.md", "_regenerate_result.json", "_regenerate_merge_map.json"]:
        p = person_dir / tmp
        if p.exists():
            p.unlink()

# --- 批量操作 ---

def find_all_person_dirs():
    """找到所有有personality_v3.json的目录"""
    dirs = set()
    # 匹配任意深度的personality_v3.json
    for pv3 in PERSONALITIES_ROOT.rglob("personality_v3.json"):
        dirs.add(pv3.parent)
    return sorted(dirs)

def batch_generate():
    dirs = find_all_person_dirs()
    print(f"Found {len(dirs)} personalities with v3")
    for d in dirs:
        try:
            generate_prompt(d)
        except Exception as e:
            print(f"ERROR {d.name}: {e}")
        print()

def batch_apply():
    dirs = find_all_person_dirs()
    applied = 0
    for d in dirs:
        result_path = d / "_regenerate_result.json"
        if not result_path.exists():
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
    parser = argparse.ArgumentParser(description="基于insights重新生成完整人格模型（两阶段）")
    parser.add_argument("person_dir", nargs="?", help="人物目录路径")
    parser.add_argument("--apply", action="store_true", help="应用_regenerate_result.json")
    parser.add_argument("--all", action="store_true", help="批量操作所有人物")
    parser.add_argument("--list", action="store_true", help="列出所有待regenerate的人物")
    args = parser.parse_args()
    
    if args.list:
        dirs = find_all_person_dirs()
        for d in dirs:
            pv3 = json.loads((d / "personality_v3.json").read_text())
            ins = pv3.get("insights_with_evidence", [])
            has_result = (d / "_regenerate_result.json").exists()
            meta = pv3.get("_distill_meta", {})
            is_regen = "regenerated" in meta.get("version", "")
            status = "✅已regen" if is_regen else ("⏳有result" if has_result else "⬜待处理")
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
        generate_prompt(args.person_dir)


if __name__ == "__main__":
    main()
