#!/usr/bin/env python3
"""
台湾议题报告生成器v3.1 — 通过openclaw sessions_spawn调用GLM（免费套餐）
不用直接调智谱API。

用法：
1. 先用pipeline v3生成task文件（已有 /tmp/ch01-ch06_task.txt）
2. python gen_taiwan_report_v31.py
3. 脚本会为每章输出一个subsession task文件，然后用openclaw CLI spawn生成
"""
import json, os, time, subprocess

TASK_DIR = "/tmp"
REPORT_DIR = "/mnt/d/data/think-tank-data/reports"
CHAPTERS = [f"ch0{i}_task.txt" for i in range(1, 7)]

def gen_chapter(task_file, output_file):
    """用openclaw sessions_spawn生成单章"""
    with open(os.path.join(TASK_DIR, task_file)) as f:
        task_content = f.read()
    
    # 提取章节号和标题
    ch_num = task_file.replace("ch", "").replace("_task.txt", "")
    
    prompt = f"""请根据以下task内容撰写第{ch_num}章。输出纯markdown正文，不要输出task内容本身。

{task_content}"""
    
    # 写入临时文件供subsession读取
    prompt_path = os.path.join(TASK_DIR, f"gen_ch{ch_num}_prompt.txt")
    with open(prompt_path, "w") as f:
        f.write(prompt)
    
    # 用openclaw exec spawn子session来生成（走coding plan免费额度）
    # 实际调用由外部orchestrator执行
    print(f"PROMPT_READY: ch{ch_num} -> {prompt_path}")
    print(f"OUTPUT: {output_file}")
    return prompt_path


if __name__ == "__main__":
    os.makedirs(REPORT_DIR, exist_ok=True)
    prompt_paths = []
    for ch in CHAPTERS:
        out = os.path.join(TASK_DIR, f"gen_{ch.replace('_task.txt', '_output.md')}")
        p = gen_chapter(ch, out)
        prompt_paths.append(p)
    
    print(f"\n{len(prompt_paths)}个章节准备就绪")
    print("需要逐章通过sessions_spawn生成")
