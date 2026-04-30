#!/usr/bin/env python3
"""
批量LLM质量评分执行器
读取prompt文件列表，通过GLM API评分，自动apply结果。

用法:
    export GLM_API_KEY=xxx
    python3 batch_quality_score.py --prompts /tmp/all_prompts.txt --batch-size 3 --delay 10
"""
import json
import sys
import time
import os
import re
import subprocess
from pathlib import Path

SCRIPT = Path.home() / ".openclaw/workspace/skills/think-tank/scripts/quality_check_llm.py"
# 不直接调API，而是生成spawn命令列表

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompts", required=True, help="prompt文件列表")
    parser.add_argument("--output", default="/tmp/batch_qc_commands.sh", help="输出命令脚本")
    args = parser.parse_args()
    
    with open(args.prompts) as f:
        prompt_files = [l.strip() for l in f if l.strip()]
    
    # 按人物分组
    persons = {}
    for pf in prompt_files:
        # 从路径提取person_dir
        parts = Path(pf).parts
        for i, p in enumerate(parts):
            if p == "materials":
                person_dir = str(Path(*parts[:i+1]).parent)
                break
        else:
            continue
        persons.setdefault(person_dir, []).append(pf)
    
    # 生成spawn任务列表
    tasks = []
    for person_dir, prompts in persons.items():
        name = Path(person_dir).name
        task_prompts = " ".join(prompts)
        tasks.append({
            "person_dir": person_dir,
            "name": name,
            "prompts": prompts,
            "count": len(prompts)
        })
    
    print(f"Total: {len(tasks)}人, {sum(t['count'] for t in tasks)} batches")
    json.dump(tasks, open("/tmp/batch_qc_tasks.json", "w"), ensure_ascii=False, indent=2)
    print(f"Task list saved to /tmp/batch_qc_tasks.json")

if __name__ == "__main__":
    main()
