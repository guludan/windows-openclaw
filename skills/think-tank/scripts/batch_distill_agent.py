#!/usr/bin/env python3
"""
batch_distill_agent.py — 分批蒸馏辅助脚本

为子agent提供「分批蒸馏」的能力：
1. 扫描缺失的result
2. 每批只输出N个prompt路径（默认10个）
3. 子agent处理完这批后，再次调用本脚本获取下一批

用法:
    # 列出下一批需要处理的prompt
    python3 batch_distill_agent.py <person_dir> --batch-size 10

    # 查看进度
    python3 batch_distill_agent.py <person_dir> --status

    # 合并所有已完成的result
    python3 batch_distill_agent.py <person_dir> --merge
"""

import json
import sys
import os
from pathlib import Path
from typing import List, Tuple

BATCH_SIZE = 10


def get_missing_prompts(person_dir: Path) -> List[Tuple[str, str]]:
    """返回 (prompt_file, expected_result_name) 的列表"""
    prompts_dir = person_dir / "_per_file_prompts"
    results_dir = person_dir / "_per_file_results"

    if not prompts_dir.exists():
        print(f"错误: prompts目录不存在: {prompts_dir}", file=sys.stderr)
        sys.exit(1)

    results_dir.mkdir(exist_ok=True)

    # 已完成的result文件名（去.json后缀）
    done = set()
    for f in results_dir.glob("*.json"):
        done.add(f.stem)

    missing = []
    for pf in sorted(prompts_dir.glob("_prompt_*")):
        if not pf.name.endswith(".md"):
            continue
        # 从prompt文件名解析result名: _prompt_NNN_{result_name}.md
        # parts: ['', 'prompt', 'NNN', rest]
        parts = pf.stem.split("_", 2)  # ['', 'prompt', 'NNN_rest']
        if len(parts) < 3:
            continue
        rest = parts[2]  # NNN_rest
        idx_end = rest.find("_")
        if idx_end < 0:
            continue
        result_name = rest[idx_end + 1:]

        if result_name not in done:
            missing.append((str(pf), result_name))

    return missing


def cmd_status(person_dir: Path):
    prompts_dir = person_dir / "_per_file_prompts"
    results_dir = person_dir / "_per_file_results"

    total_prompts = len(list(prompts_dir.glob("_prompt_*.md"))) if prompts_dir.exists() else 0
    total_results = len(list(results_dir.glob("*.json"))) if results_dir.exists() else 0

    print(f"人物: {person_dir.name}")
    print(f"总prompts: {total_prompts}")
    print(f"已完成: {total_results}")
    print(f"剩余: {total_prompts - total_results}")

    if total_results > 0:
        # 统计已有insights
        total_ins = 0
        for rf in results_dir.glob("*.json"):
            try:
                data = json.loads(rf.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    total_ins += len(data)
            except:
                pass
        print(f"已提取insights: {total_ins}")


def cmd_next_batch(person_dir: Path, batch_size: int):
    missing = get_missing_prompts(person_dir)
    if not missing:
        print("所有prompt已完成！运行 --merge 合并结果。")
        return

    batch = missing[:batch_size]
    print(f"本批次: {len(batch)} 个 (剩余 {len(missing)} 个)")
    print()
    for prompt_path, result_name in batch:
        print(f"PROMPT: {prompt_path}")
        print(f"RESULT: {result_name}.json")
        print(f"SAVE_TO: {person_dir / '_per_file_results' / (result_name + '.json')}")
        print()


def cmd_merge(person_dir: Path):
    """调用 _batch_distill.py 的 merge"""
    import subprocess
    script = Path(__file__).parent / "_batch_distill.py"
    result = subprocess.run(
        [sys.executable, str(script), str(person_dir), "--merge-per-file"],
        capture_output=False
    )
    sys.exit(result.returncode)


def main():
    if len(sys.argv) < 3:
        print("用法: python3 batch_distill_agent.py <person_dir> --status|--next-batch|--merge [--batch-size N]")
        sys.exit(1)

    person_dir = Path(sys.argv[1])
    if not person_dir.exists():
        print(f"错误: 目录不存在: {person_dir}", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[2]

    if cmd == "--status":
        cmd_status(person_dir)
    elif cmd == "--next-batch":
        bs = BATCH_SIZE
        for i, a in enumerate(sys.argv):
            if a == "--batch-size" and i + 1 < len(sys.argv):
                bs = int(sys.argv[i + 1])
        cmd_next_batch(person_dir, bs)
    elif cmd == "--merge":
        cmd_merge(person_dir)
    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
