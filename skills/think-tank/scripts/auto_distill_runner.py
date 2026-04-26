#!/usr/bin/env python3
"""
auto_distill_runner.py — 自动批量蒸馏（管理并发）

每批5个子session，每个处理一个人的1篇素材。
完成后自动发下一篇。一个人完成后自动合并结果。

用法:
    python3 auto_distill_runner.py --run
"""
import json, os, sys, subprocess, time, glob
from pathlib import Path
from datetime import datetime

BASE = Path("/mnt/d/data/think-tank-data/personalities")
SCRIPTS = Path("~/.openclaw/workspace/skills/think-tank/scripts").expanduser()
MAX_CONCURRENT = 5

def get_redistill_dirs():
    """获取有prompts但未完成的目录"""
    dirs = []
    for prompts_dir in sorted(BASE.rglob("_per_file_prompts")):
        pdir = prompts_dir.parent
        results_dir = pdir / "_per_file_results"
        
        # 检查是否有prompts
        prompts = sorted(prompts_dir.glob("_prompt_*.md"))
        if not prompts:
            continue
        
        # 检查是否已完成
        if results_dir.exists():
            done = set(f.name.replace(".json", "") for f in results_dir.glob("*.json"))
        else:
            done = set()
        
        # 计算待处理的
        pending = []
        for p in prompts:
            # 从prompt文件名提取对应的结果文件名
            # _prompt_NNN_filename.md -> filename.json
            # _prompt_NNN_filename_chunkN.md -> filename_chunkN.json
            base_name = p.stem  # remove .md
            # 去掉 _prompt_NNN_ 前缀
            parts = base_name.split("_", 3)
            if len(parts) >= 4:
                result_name = parts[3]  # filename or filename_chunkN
            else:
                continue
            if result_name not in done:
                pending.append((str(p), result_name))
        
        if pending:
            total = len(prompts)
            completed = total - len(pending)
            dirs.append((pdir, pending, completed, total))
    
    dirs.sort(key=lambda x: -x[3])  # 按素材总量排序
    return dirs

def main():
    if "--status" in sys.argv:
        dirs = get_redistill_dirs()
        print(f"待蒸馏: {len(dirs)} 人\n")
        for pdir, pending, completed, total in dirs:
            name = pdir.name
            print(f"  {name}: {completed}/{total} 完成")
        return
    
    dirs = get_redistill_dirs()
    print(f"待蒸馏: {len(dirs)} 人")
    print(f"将逐批处理，每批{MAX_CONCURRENT}个子session")
    
    # 生成任务清单
    tasks = []
    for pdir, pending, completed, total in dirs:
        results_dir = pdir / "_per_file_results"
        results_dir.mkdir(exist_ok=True)
        for prompt_path, result_name in pending:
            result_path = str(results_dir / f"{result_name}.json")
            tasks.append({
                "person_dir": str(pdir),
                "person_name": pdir.name,
                "prompt_path": prompt_path,
                "result_path": result_path,
            })
    
    print(f"总任务数: {len(tasks)}")
    
    # 保存任务清单
    task_file = Path("/tmp/distill_tasks.json")
    task_file.write_text(json.dumps(tasks, ensure_ascii=False, indent=2))
    print(f"任务清单已保存: {task_file}")
    
    # 打印前10个任务
    print(f"\n前10个任务:")
    for t in tasks[:10]:
        print(f"  {t['person_name']}: {Path(t['prompt_path']).stem}")

if __name__ == "__main__":
    main()
