#!/usr/bin/env python3
"""batch_ingest.py — 分批入库，避免OOM"""
import subprocess, sys, time
from pathlib import Path

BASE = Path("/mnt/d/data/think-tank-data/personalities")
SCRIPT = Path(__file__).parent / "ingest_v3.py"
PYTHON = "/home/tearsyu/miniconda3/envs/tt/bin/python3"

def find_people(base_dir):
    """遍历目录找到所有人（支持flat和nested）"""
    people = []
    for group in sorted(base_dir.iterdir()):
        if not group.is_dir():
            continue
        for candidate in sorted(group.iterdir()):
            if not candidate.is_dir():
                continue
            if (candidate / "personality_v3.json").exists():
                people.append((candidate.name, candidate))
            else:
                for person in sorted(candidate.iterdir()):
                    if person.is_dir() and (person / "personality_v3.json").exists():
                        people.append((person.name, person))
    return people

def run_one(name, path, max_retries=3):
    result = subprocess.run(
        [PYTHON, str(SCRIPT), name, str(path)],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        print(f"  ❌ {name} (exit {result.returncode}): {result.stderr[-200:] if result.stderr else 'no stderr'}", flush=True)
        return False
    # 过滤空行后打印
    for line in result.stdout.strip().split('\n'):
        if line.strip():
            print(f"  {line.strip()}", flush=True)
    return True

def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    
    if target == "all":
        people = find_people(BASE)
    elif target in ["military", "scholars", "psychologists"]:
        people = find_people(BASE / target)
    else:
        people = find_people(BASE)
        people = [(n, p) for n, p in people if target in n]
    
    print(f"共 {len(people)} 人待入库", flush=True)
    ok = 0
    fail = 0
    for i, (name, path) in enumerate(people):
        print(f"\n[{i+1}/{len(people)}] {name}", flush=True)
        if run_one(name, path, max_retries=3):
            ok += 1
        else:
            # segfault 重试
            for retry in range(2):
                print(f"  重试 {retry+1}/2...", flush=True)
                time.sleep(5)
                if run_one(name, path, max_retries=3):
                    ok += 1
                    fail -= 1
                    break
            else:
                fail += 1
        time.sleep(1)  # 给Ollama喘气
    
    print(f"\n完成: {ok} 成功, {fail} 失败 / {len(people)} 总计", flush=True)

if __name__ == "__main__":
    main()
