#!/usr/bin/env python3
"""质检评分编排：生成133个子session任务清单"""
import json
from pathlib import Path

BASE = Path("/mnt/d/data/think-tank/personalities")

# 收集所有prompt文件按人分组
tasks = []
for p in sorted(BASE.rglob("_quality_prompt_*.md")):
    person_dir = str(p.parent.parent)
    prompt_path = str(p)
    tasks.append({"person_dir": person_dir, "prompt_path": prompt_path})

# 按人分组
by_person = {}
for t in tasks:
    key = t["person_dir"]
    if key not in by_person:
        by_person[key] = []
    by_person[key].append(t["prompt_path"])

print(json.dumps({
    "total_persons": len(by_person),
    "total_batches": len(tasks),
    "persons": {Path(k).name: v for k, v in sorted(by_person.items())}
}, ensure_ascii=False, indent=2))
