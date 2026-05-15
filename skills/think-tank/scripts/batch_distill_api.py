#!/usr/bin/env python3
"""
batch_distill_api.py — 通过DeepSeek API批量蒸馏缺失prompts

用法:
    python3 batch_distill_api.py <person_dir> [--batch-size 3]
    python3 batch_distill_api.py --auto [--batch-size 3] [--max-people 5]
    python3 batch_distill_api.py --people "Kenneth_Rogoff,John_Mearsheimer,Nate_Silver"
"""

import json, os, sys, time, re
import requests
from pathlib import Path
from datetime import datetime

API_KEY = "sk-5a1bbc0258ce413ab30b5590efda23ac"
API_URL = "https://api.deepseek.com/v1/chat/completions"
BASE = Path("/mnt/d/data/think-tank-data/personalities")
COST_IN = 0.27  # DeepSeek V3 per million input tokens
COST_OUT = 1.10  # per million output tokens

def get_missing_prompts(person_dir):
    pd = person_dir / "_per_file_prompts"
    rd = person_dir / "_per_file_results"
    if not pd.exists():
        return []
    results = set(os.listdir(rd)) if rd.exists() else set()
    missing = []
    for f in sorted(os.listdir(pd)):
        if not f.endswith('.md'):
            continue
        rname = os.path.splitext(f)[0] + '.json'
        if rname not in results:
            missing.append((f, rname))
    return missing

def call_api(messages, max_tokens=8192):
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    data = {"model": "deepseek-chat", "messages": messages, "max_tokens": max_tokens, "temperature": 0.3, "stream": False}
    for attempt in range(3):
        try:
            resp = requests.post(API_URL, headers=headers, json=data, timeout=300)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                w = 30 * (attempt + 1)
                print(f"  429, waiting {w}s...", flush=True)
                time.sleep(w)
            else:
                print(f"  HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
                time.sleep(10)
        except Exception as e:
            print(f"  Err: {e}", flush=True)
            time.sleep(10)
    return None

def count_tok(text):
    c = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    return int(c * 1.5 + (len(text) - c) / 4)

def build_task(person_dir, batch):
    """Build the system and user messages for a batch"""
    system = "你是人格蒸馏专家。根据提供的人物素材，提取结构化的 insights_with_evidence。只输出JSON数组，不要markdown包裹。"
    
    user_parts = [f"处理 {len(batch)} 个独立蒸馏任务。"]
    user_parts.append("""
规则：
1. 每个任务用 "===== TASK: <文件名> =====" 分隔
2. 对每个任务，提取insights_with_evidence，JSON数组
3. 学术论文：提取对人物思想的分析，无关则输出[]
4. 传记/百科/叙事：提取决策模式、背景信息
5. excerpt必须是原文逐字摘录(50-500字)
6. claim 100-300字，核心论断
7. dim: stance/cognitive/method/decision/communication/logical_tracing/tension/situational

输出格式（每个TASK对应一个RESULT）：
===== RESULT: <文件名> =====
[JSON数组]
===== RESULT: <文件名2> =====
...
""")
    
    for pf, rn in batch:
        pp = person_dir / "_per_file_prompts" / pf
        content = pp.read_text(encoding='utf-8', errors='ignore')
        
        ms = content.find("--- 素材开始 ---")
        if ms >= 0:
            mc = content[ms + len("--- 素材开始 ---"):]
            oq = mc.find("## 输出要求")
            if oq >= 0:
                mc = mc[:oq]
        else:
            mc = content
        
        user_parts.append(f"\n===== TASK: {rn} =====\n{mc.strip()}")
    
    user_parts.append("\n===== 输出要求 =====\n对每个TASK输出对应的JSON数组。没有观点输出[]，严禁编造。")
    
    return system, "\n\n".join(user_parts)

def parse_response(text, expected):
    results = {}
    for en in expected:
        m1 = text.find(f"===== RESULT: {en} =====")
        if m1 < 0:
            print(f"  ⚠️ 未找到 {en} 的结果标记，设为空数组")
            results[en] = []
            continue
        s = m1 + len(f"===== RESULT: {en} =====")
        m2 = text.find("===== RESULT:", s)
        jt = text[s:m2].strip() if m2 >= 0 else text[s:].strip()
        jt = re.sub(r'^```json\s*', '', jt)
        jt = re.sub(r'\s*```$', '', jt)
        try:
            d = json.loads(jt)
            if isinstance(d, list):
                results[en] = d
                continue
        except:
            pass
        am = re.search(r'\[[\s\S]*?\]', jt)
        if am:
            try:
                d = json.loads(am.group())
                if isinstance(d, list):
                    results[en] = d
                    continue
            except:
                pass
        print(f"  ⚠️ 无法解析 {en}，设为空数组")
        results[en] = []
    return results

def process_person(person_dir, batch_size=3, delay=1.5):
    name = person_dir.name
    missing = get_missing_prompts(person_dir)
    if not missing:
        print(f"{name}: ✅ 全部完成")
        return 0, 0
    
    total = len(missing)
    rd = person_dir / "_per_file_results"
    rd.mkdir(parents=True, exist_ok=True)
    
    print(f"\n{name}: 处理 {total} 个缺失prompt")
    batches = [missing[i:i+batch_size] for i in range(0, total, batch_size)]
    print(f"  分 {len(batches)} 批，每批{batch_size}个")
    
    ok, fail, tok_in, tok_out = 0, 0, 0, 0
    for i, batch in enumerate(batches):
        print(f"  批{i+1}/{len(batches)}: ", end="", flush=True)
        sys_msg, user_msg = build_task(person_dir, batch)
        ti = count_tok(sys_msg + user_msg)
        messages = [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}]
        
        res = call_api(messages, max_tokens=min(16384, len(batch) * 4096))
        if not res:
            print("❌ API失败")
            fail += len(batch)
            continue
        
        rt = res['choices'][0]['message']['content']
        to = count_tok(rt)
        tok_in += ti
        tok_out += to
        
        parsed = parse_response(rt, [r for _, r in batch])
        saved = 0
        for rn, data in parsed.items():
            (rd / rn).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            saved += 1
        
        ci = ti / 1e6 * COST_IN
        co = to / 1e6 * COST_OUT
        ok += saved
        print(f"✅ {saved}/{len(batch)} (in={ti:,}tok=${ci:.4f} out={to:,}tok=${co:.4f})", flush=True)
        if i < len(batches) - 1:
            time.sleep(delay)
    
    cost = tok_in / 1e6 * COST_IN + tok_out / 1e6 * COST_OUT
    print(f"{name}: ✅ {ok}完成, {fail}失败, 总成本${cost:.4f}")
    return ok, fail

def find_all_person_dirs():
    """递归查找所有人目录（含_per_file_prompts的目录）"""
    dirs = []
    for root, dirs_list, files in os.walk(str(BASE)):
        if '_per_file_prompts' in dirs_list:
            dirs.append(Path(root))
            dirs_list[:] = []  # 不递归进人物子目录
    return dirs

def find_person_dir(name):
    """按名字递归查找人物目录"""
    for root, dirs_list, files in os.walk(str(BASE)):
        if os.path.basename(root) == name:
            return Path(root)
    return None

def find_incomplete(limit=0):
    people = []
    for pd in find_all_person_dirs():
        miss = get_missing_prompts(pd)
        if miss:
            people.append((pd, len(miss)))
    people.sort(key=lambda x: -x[1])
    return people[:limit] if limit > 0 else people

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("person_dir", nargs="?")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--people")
    ap.add_argument("--batch-size", type=int, default=3)
    ap.add_argument("--max-people", type=int, default=0)
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()
    
    bs = args.batch_size
    
    if args.auto:
        people = find_incomplete(args.max_people)
        print(f"发现 {len(people)} 个未完成人物")
        for i, (pd, cnt) in enumerate(people[:10]):
            print(f"  {pd.name:30s} 缺{cnt}个")
        if len(people) > 10:
            print(f"  ...还有{len(people)-10}个")
        
        for i, (pd, _) in enumerate(people):
            print(f"\n[{i+1}/{len(people)}]")
            process_person(pd, bs, args.delay)
    
    elif args.people:
        for name in args.people.split(','):
            name = name.strip()
            pd = find_person_dir(name)
            if pd:
                process_person(pd, bs, args.delay)
            else:
                print(f"{name}: ❌ 未找到")
    
    elif args.person_dir:
        process_person(Path(args.person_dir), bs, args.delay)
    
    else:
        ap.print_help()
