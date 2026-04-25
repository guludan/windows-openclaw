#!/usr/bin/env python3
"""
batch_redistill_v5.py — 批量V5蒸馏（insights_with_evidence）

生成蒸馏任务列表，供主session逐个spawn子agent执行。
"""
import json, sys
from pathlib import Path

BASE = Path("/mnt/d/data/think-tank-data/personalities")
TEMPLATE_REF = "~/.openclaw/workspace/skills/think-tank/templates/distill_prompt_v3.md"
MAX_CHARS = 60000

def get_materials(person_dir: Path) -> list:
    mat_dir = person_dir / "materials"
    if not mat_dir.exists():
        return []
    files = sorted(
        [f for f in mat_dir.glob("*.txt") if not f.name.startswith("_")],
        key=lambda f: f.stat().st_size
    )
    return [f for f in files if f.stat().st_size >= 200]

def find_todo(base: Path = BASE) -> list:
    """找出缺 personality_v3.json 的人物"""
    todo = []
    for group in sorted(base.iterdir()):
        if not group.is_dir():
            continue
        for sub in sorted(group.iterdir()):
            if not sub.is_dir():
                continue
            # Direct person dir
            if (sub / "materials").exists():
                files = get_materials(sub)
                if files and not (sub / "personality_v3.json").exists():
                    todo.append((sub.name, str(sub), len(files),
                                sum(f.stat().st_size for f in files)))
            # Nested (scholars/sub-category/person)
            if not (sub / "materials").exists():
                for person in sorted(sub.iterdir()):
                    if not person.is_dir():
                        continue
                    files = get_materials(person)
                    if files and not (person / "personality_v3.json").exists():
                        todo.append((person.name, str(person), len(files),
                                    sum(f.stat().st_size for f in files)))
    return todo

def build_task(name: str, person_dir: str) -> str:
    pdir = Path(person_dir)
    files = get_materials(pdir)
    total = sum(f.stat().st_size for f in files)
    
    # Determine domain hint from path
    parts = Path(person_dir).relative_to(BASE).parts
    domain = "/".join(parts[:-1]) if len(parts) > 1 else ""

    if total <= MAX_CHARS:
        return f"""你是人格蒸馏专家。完成{name}的人格蒸馏任务。

步骤：
1. 读取蒸馏模板：{TEMPLATE_REF}
2. 素材目录：{pdir}/materials/
3. 素材共{len(files)}篇约{total//1024}KB，逐篇读取
4. 综合生成 personality_v3.json

⚠️ **JSON格式严格要求**：
- 字符串值内不能有未转义换行（用\\n）、tab、中文引号""
- 输出后用 python3 -c "import json; json.load(open('{pdir}/personality_v3.json'))" 验证
- 验证失败则修复重存

insights_with_evidence必须15-30个insights，evidence.excerpt必须是原文。
保存到：{pdir}/personality_v3.json
_distill_meta.model="glm-5-turbo", version="v5-evidence", 领域预填: {domain}"""
    else:
        n_chunks = (total + MAX_CHARS - 1) // MAX_CHARS
        return f"""你是人格蒸馏专家。{name}的素材较大（{total//1024}KB，{len(files)}篇，分{n_chunks}段），分段处理。

步骤：
1. 读取蒸馏模板：{TEMPLATE_REF}
2. 逐段读取素材：
   - 前1/3：按文件大小排序，读最大的{len(files)//3}篇
   - 中1/3：接下来{len(files)//3}篇
   - 后1/3：剩余篇
3. 综合生成 personality_v3.json

⚠️ **JSON格式严格要求**：
- 字符串值内不能有未转义换行（用\\n）、tab、中文引号""
- 输出后用 python3 -c "import json; json.load(open('{pdir}/personality_v3.json'))" 验证
- 验证失败则修复重存

insights_with_evidence必须15-30个insights，evidence.excerpt必须是原文。
保存到：{pdir}/personality_v3.json
_distill_meta.model="glm-5-turbo", version="v5-evidence", 领域预填: {domain}"""

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--task-json", action="store_true", help="Output JSON array of tasks")
    parser.add_argument("--batch", type=int, default=0, help="Batch number (0-indexed)")
    parser.add_argument("--batch-size", type=int, default=5, help="Tasks per batch")
    args = parser.parse_args()

    todo = find_todo()
    
    if args.list:
        print(f"需要蒸馏: {len(todo)} 人")
        for name, path, nfiles, size in sorted(todo, key=lambda x: -x[3]):
            print(f"  {name}: {nfiles}files {size//1024}KB")
    
    elif args.task_json:
        # Output all tasks as JSON for batch processing
        tasks = []
        for name, path, nfiles, size in todo:
            tasks.append({
                "name": name,
                "path": path,
                "nfiles": nfiles,
                "size_kb": size // 1024,
                "task": build_task(name, path)
            })
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
    
    else:
        # Print single batch of task prompts
        start = args.batch * args.batch_size
        end = start + args.batch_size
        batch = todo[start:end]
        print(f"Batch {args.batch}: {len(batch)} tasks (from {start} to {start+len(batch)-1} of {len(todo)})")
        for name, path, nfiles, size in batch:
            print(f"\n{'='*60}")
            print(f"## {name} ({nfiles}files {size//1024}KB)")
            print(build_task(name, path))
