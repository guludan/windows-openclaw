#!/usr/bin/env python3
"""
batch_llm_quality.py — 对规则评分的人物重新做LLM评分
用法: python3 batch_llm_quality.py /tmp/rule_scored_dirs.txt
"""
import json, sys, os, subprocess
from pathlib import Path

CHECK_SCRIPT = "/home/tearsyu/.openclaw/workspace/skills/think-tank/scripts/quality_check_llm.py"

def main():
    dirs_file = sys.argv[1]
    with open(dirs_file) as f:
        dirs = [l.strip() for l in f if l.strip()]
    
    # 按~12 batches分组
    groups = []
    current_group = []
    current_batches = 0
    
    for d in dirs:
        mat = Path(d) / 'materials'
        prompts = sorted(mat.glob('_quality_prompt_*.md'))
        n = len(prompts)
        if current_batches + n > 12 and current_group:
            groups.append((current_group, current_batches))
            current_group = []
            current_batches = 0
        current_group.append(d)
        current_batches += n
    if current_group:
        groups.append((current_group, current_batches))
    
    print(f"总计 {len(dirs)}人, {len(groups)}组", flush=True)
    
    for i, (group_dirs, batch_count) in enumerate(groups):
        print(f"\n=== Group {i}/{len(groups)-1}: {len(group_dirs)}人, {batch_count} batches ===", flush=True)
        
        # 为每组生成一个合并prompt文件列表
        all_batches = []
        person_batches = {}  # person -> [batch_files]
        for d in group_dirs:
            mat = Path(d) / 'materials'
            prompts = sorted(mat.glob('_quality_prompt_*.md'))
            person_batches[d] = prompts
            all_batches.extend(prompts)
        
        # 输出每个batch文件路径，供子agent读取
        print(f"  Batch files:", flush=True)
        for d, prompts in person_batches.items():
            name = Path(d).name
            for p in prompts:
                print(f"    {p}", flush=True)

if __name__ == "__main__":
    main()
