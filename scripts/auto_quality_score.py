#!/usr/bin/env python3
"""自动质检评分编排器：收集所有prompt文件，输出执行清单供主session spawn"""
import json
from pathlib import Path

BASE = Path("/mnt/d/data/think-tank-data/personalities")
PYTHON = "~/miniconda3/envs/tt/bin/python3"
QC_SCRIPT = "~/.openclaw/workspace/skills/think-tank/scripts/quality_check_llm.py"

# 收集所有prompt文件，按人物分组
prompts_by_person = {}
for p in BASE.rglob("_quality_prompt_*.md"):
    person_dir = p.parent.parent  # materials -> person_dir
    key = str(person_dir)
    if key not in prompts_by_person:
        prompts_by_person[key] = []
    prompts_by_person[key].append(p)

# 生成执行清单
manifest = []
for person_dir, prompts in sorted(prompts_by_person.items()):
    manifest.append({
        "person_dir": person_dir,
        "prompts": [str(p) for p in sorted(prompts)],
        "batch_count": len(prompts)
    })

# 输出统计
total_prompts = sum(m["batch_count"] for m in manifest)
print(f"Total persons: {len(manifest)}")
print(f"Total batches: {total_prompts}")
print()

# 保存manifest
manifest_path = Path("/tmp/quality_score_manifest.json")
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
print(f"Manifest saved to {manifest_path}")

# 按batch_count排序输出前20
for m in sorted(manifest, key=lambda x: -x["batch_count"])[:20]:
    print(f"  {m['batch_count']} batches | {Path(m['person_dir']).name}")
