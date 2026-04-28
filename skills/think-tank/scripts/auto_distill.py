#!/usr/bin/env python3
"""
auto_distill.py — 自动逐篇蒸馏（5个并发人物）

用法:
    python3 auto_distill.py --generate  # 为76人生成逐篇prompts
    python3 auto_distill.py --status    # 查看进度
    python3 auto_distill.py --list      # 列出待蒸馏人物
"""
import json, os, sys
from pathlib import Path
from datetime import datetime

BASE = Path("/mnt/d/data/think-tank-data/personalities")
SCRIPTS = Path("~/.openclaw/workspace/skills/think-tank/scripts").expanduser()
sys.path.insert(0, str(SCRIPTS))

def get_coverage(pv3_path):
    """计算覆盖率"""
    pdir = pv3_path.parent
    mat_dir = pdir / "materials"
    all_files = set(f.name for f in mat_dir.glob("*.[tm][xd]t") if not f.name.startswith("_"))
    if not all_files:
        return 100, 0, 0
    
    pv3 = json.loads(pv3_path.read_text(encoding="utf-8"))
    referenced = set()
    for ins in pv3.get("insights_with_evidence", []):
        for ev in ins.get("evidence", []):
            sf = ev.get("source_file", "")
            if sf:
                referenced.add(sf)
    
    def extract_refs(obj):
        refs = set()
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "source_ref" and isinstance(v, str) and v:
                    refs.add(v)
                else:
                    refs.update(extract_refs(v))
        elif isinstance(obj, list):
            for item in obj:
                refs.update(extract_refs(item))
        return refs
    
    all_refs = extract_refs(pv3) | referenced
    covered = len(all_refs & all_files)
    return int(covered / len(all_files) * 100), len(all_files), covered

def get_redistill_list():
    """获取需要重新蒸馏的人物列表"""
    result = []
    for pv3_path in sorted(BASE.rglob("personality_v3.json")):
        pct, total, covered = get_coverage(pv3_path)
        if pct < 50:
            result.append((pv3_path.parent, pv3_path.parent.name, total, covered, pct))
    result.sort(key=lambda x: -x[2])
    return result

if __name__ == "__main__":
    if "--list" in sys.argv:
        items = get_redistill_list()
        print(f"需要重新蒸馏: {len(items)} 人\n")
        for pdir, name, total, covered, pct in items:
            print(f"  {name}: {total}篇素材, 覆盖{pct}%")
    
    elif "--status" in sys.argv:
        items = get_redistill_list()
        done = 0
        for pdir, name, total, covered, pct in items:
            results_dir = pdir / "_per_file_results"
            if results_dir.exists():
                done_files = len(list(results_dir.glob("*.json")))
                print(f"  {name}: {done_files}/{total} 篇已完成")
            else:
                print(f"  {name}: 未开始")
        print(f"\n待处理: {len(items)} 人")
    
    elif "--generate" in sys.argv:
        # 导入 _batch_distill 的逐篇生成功能
        from _batch_distill import generate_per_file_prompts
        items = get_redistill_list()
        print(f"为 {len(items)} 人生成逐篇prompts...\n")
        for pdir, name, total, covered, pct in items:
            prompts_dir = pdir / "_per_file_prompts"
            if prompts_dir.exists() and len(list(prompts_dir.glob("*.md"))) > 0:
                existing = len(list(prompts_dir.glob("*.md")))
                print(f"  {name}: 已有{existing}个prompts，跳过")
                continue
            generate_per_file_prompts(pdir)
            new_count = len(list(prompts_dir.glob("*.md")))
            print(f"  {name}: 生成{new_count}个prompts ✅")
