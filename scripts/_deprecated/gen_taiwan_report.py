#!/usr/bin/env python3
"""台湾议题报告生成器 - 逐章生成拼接"""
import json, sys, os, time, requests

API_KEY = "be2ffb65ff4340879d38a41494b78187.bYrqhxaJQWwSnrEv"
URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODEL = "glm-5-turbo"
REPORT_DIR = "/mnt/d/data/think-tank-data/reports"

def call_glm(task_file, max_tokens=4000, retries=2):
    with open(task_file) as f:
        task = f.read()
    messages = [{"role": "user", "content": task}]
    for attempt in range(retries + 1):
        try:
            r = requests.post(URL, json={
                "model": MODEL, "messages": messages,
                "max_tokens": max_tokens, "temperature": 0.7,
            }, headers={"Authorization": f"Bearer {API_KEY}"}, timeout=300)
            if r.status_code == 400:
                print(f"  400错误，截断重试...")
                # 截断RAG部分
                if len(task) > 20000:
                    task = task[:task.rfind("\n## RAG", 0, -5000)]
                    if task:
                        messages = [{"role": "user", "content": task}]
                    continue
                return None
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"  尝试{attempt+1}失败: {e}")
            time.sleep(3)
    return None

if __name__ == "__main__":
    chapters = []
    task_files = sorted([f"/tmp/ch0{i}_task.txt" for i in range(1, 7)])
    
    # 加载框架
    with open("/tmp/think_tank_v3_framework.json") as f:
        fw = json.load(f)
    
    # 标题
    report = f"# {fw['question']}\n## 多视角智库深度分析报告\n\n"
    report += f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M')} | Pipeline v3.1 | GLM-5-TURBO\n\n"
    
    for tf in task_files:
        ch_num = os.path.basename(tf).replace("ch", "").replace("_task.txt", "")
        print(f"生成第{ch_num}章...")
        content = call_glm(tf)
        if content:
            chapters.append(content)
            print(f"  ✓ {len(content)}字")
        else:
            print(f"  ✗ 失败")
            chapters.append(f"[第{ch_num}章生成失败]")
        time.sleep(2)  # 避免rate limit
    
    report += "\n\n---\n\n".join(chapters)
    report += "\n\n---\n\n> *本报告由AI多视角智库系统生成，仅供参考，不构成投资建议。*\n"
    
    os.makedirs(REPORT_DIR, exist_ok=True)
    outpath = f"{REPORT_DIR}/台湾战略窗口期研判v3.1_{time.strftime('%Y-%m-%d')}.md"
    with open(outpath, "w") as f:
        f.write(report)
    
    print(f"\n✅ 报告已保存: {outpath}")
    print(f"   总字数: {len(report)}")
