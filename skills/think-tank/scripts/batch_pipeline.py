#!/usr/bin/env python3
"""
batch_pipeline.py — 批量跑完整个新V3流程

用法:
    python3 batch_pipeline.py merge <person_dir>         # merge单个人
    python3 batch_pipeline.py dedup <person_dir>         # dedup+regen单个人
    python3 batch_pipeline.py batch-merge <queue.json>   # 批量merge
    python3 batch_pipeline.py batch-dedup <queue.json>   # 批量dedup+regen
    
分批策略: 每个nohep进程跑一批，WSL限制最多4进程
"""

import json, sys, os, subprocess, time
from pathlib import Path

SCRIPTS = Path(__file__).parent
MERGE_SCRIPT = SCRIPTS / "_batch_distill.py"
DEDUP_SCRIPT = SCRIPTS / "batch_dedup_regen.py"


def merge_one(path):
    """单个merge"""
    v3_path = os.path.join(path, "personality_v3.json")
    if os.path.exists(v3_path):
        os.remove(v3_path)
    r = subprocess.run(
        ["python3", str(MERGE_SCRIPT), str(path), "--merge-per-file"],
        capture_output=True, text=True, timeout=120
    )
    return r.returncode == 0


def dedup_one(path):
    """单个dedup+regen"""
    r = subprocess.run(
        ["python3", str(DEDUP_SCRIPT), str(path)],
        capture_output=True, text=True, timeout=600
    )
    return r.returncode == 0


def batch_merge(queue_path):
    """批量merge所有pending"""
    queue = json.loads(open(queue_path, 'r', encoding='utf-8').read())
    pending = [p for p in queue if p['status'] == 'pending']
    total = len(pending)
    print(f"⌛ 批量merge: {total}人", flush=True)
    
    for idx, p in enumerate(pending):
        path = p['path']
        name = p['name']
        v3_path = os.path.join(path, "personality_v3.json")
        
        # 删旧v3
        if os.path.exists(v3_path):
            os.remove(v3_path)
        
        r = subprocess.run(
            ["python3", str(MERGE_SCRIPT), str(path), "--merge-per-file"],
            capture_output=True, text=True, timeout=120
        )
        if r.returncode != 0:
            print(f"  [{idx+1}/{total}] {name}: ❌ merge失败")
            continue
        
        # 解析insight数
        for line in r.stdout.split('\n'):
            if '合并后 insights:' in line:
                n = line.split(':')[-1].strip()
                print(f"  [{idx+1}/{total}] {name}: ✅ {n} insights")
                break
        else:
            print(f"  [{idx+1}/{total}] {name}: ✅")
        
        time.sleep(0.5)
    
    print(f"\n✅ 批量merge完成")


def batch_dedup(queue_path, start=0, end=None):
    """批量dedup+regen（分批运行）"""
    queue = json.loads(open(queue_path, 'r', encoding='utf-8').read())
    pending = [p for p in queue if p['status'] == 'pending']
    batch = pending[start:end]
    total = len(batch)
    print(f"⌛ 批量dedup+regen: {total}人 (索引{start}-{end})", flush=True)
    
    for idx, p in enumerate(batch):
        path = p['path']
        name = p['name']
        print(f"\n[{idx+1}/{total}] {name} ...", flush=True)
        
        r = subprocess.run(
            ["python3", str(DEDUP_SCRIPT), str(path)],
            capture_output=True, text=True, timeout=600
        )
        if r.returncode != 0:
            print(f"  ❌ failed: {r.stderr[-200:]}")
            continue
        
        # 解析结果
        for line in r.stdout.split('\n'):
            if '去重完成' in line:
                print(f"  ✅ {line.strip()[:60]}")
            if 'brief=' in line:
                print(f"  ✅ {line.strip()[:60]}")
            if 'school=' in line:
                print(f"  ✅ {line.strip()[:60]}")

    print(f"\n✅ 批次完成 (index {start}-{end})")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    
    cmd = sys.argv[1]
    target = sys.argv[2]
    
    if cmd == 'merge':
        merge_one(target)
    elif cmd == 'dedup':
        dedup_one(target)
    elif cmd == 'batch-merge':
        batch_merge(target)
    elif cmd == 'batch-dedup':
        start = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        end = int(sys.argv[4]) if len(sys.argv) > 4 else None
        batch_dedup(target, start, end)
    else:
        print(f"未知命令: {cmd}")
