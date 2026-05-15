#!/usr/bin/env python3
"""
batch_distill_daemon.py — 后台守护批量蒸馏（通过subprocess调DeepSeek API）
用法: nohup python3 batch_distill_daemon.py --people "Kenneth_Rogoff,契克森米哈赖" > /tmp/distill.log 2>&1 &
"""
import json, os, sys, time, re, subprocess
import requests
from pathlib import Path

API_KEY = "sk-5a1bbc0258ce413ab30b5590efda23ac"
API_URL = "https://api.deepseek.com/v1/chat/completions"
BASE = Path("/mnt/d/data/think-tank-data/personalities")
BATCH_SIZE = 3

def find_all_names():
    """Find all person directory names recursively"""
    names = []
    for root, dirs, files in os.walk(str(BASE)):
        if '_per_file_prompts' in dirs:
            names.append(os.path.basename(root))
            dirs[:] = []
    return names

def find_person_dir(name):
    for root, dirs, files in os.walk(str(BASE)):
        if os.path.basename(root) == name:
            return Path(root)
    return None

def count_missing(person_dir):
    pd = person_dir / "_per_file_prompts"
    rd = person_dir / "_per_file_results"
    if not pd.exists():
        return []
    results = set(os.listdir(rd)) if rd.exists() else set()
    missing = []
    for f in sorted(os.listdir(pd)):
        if not f.endswith('.md'):
            continue
        rn = os.path.splitext(f)[0] + '.json'
        if rn not in results:
            missing.append((f, rn))
    return missing

def count_tok(text):
    c = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    return int(c * 1.5 + (len(text) - c) / 4)

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
                print(f"  [API] 429, waiting {w}s", flush=True)
                time.sleep(w)
            else:
                print(f"  [API] HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
                time.sleep(10)
        except Exception as e:
            print(f"  [API] {e}", flush=True)
            time.sleep(10)
    return None

def build_task(person_dir, batch):
    system = "你是人格蒸馏专家。根据提供的人物素材，提取结构化的 insights_with_evidence。只输出JSON数组，不要markdown包裹。"
    user_parts = [f"处理 {len(batch)} 个独立蒸馏任务。"]
    user_parts.append("""
规则：
1. 每个任务用 "===== TASK: <文件名> =====" 分隔
2. 对每个任务输出insights_with_evidence JSON数组
3. 学术论文：提取对人物思想的分析，无关则输出[]
4. 传记/百科/叙事：提取决策模式、背景信息
5. excerpt必须原文逐字摘录(50-500字)，claim 100-300字
6. dim: stance/cognitive/method/decision/communication/logical_tracing/tension/situational

输出格式：
===== RESULT: <文件名> =====
[JSON数组]
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
        results[en] = []
    return results

def process_person(person_dir, batch_size=3, delay=1.5):
    name = person_dir.name
    missing = count_missing(person_dir)
    if not missing:
        print(f"{name}: ✅ 全部完成", flush=True)
        return 0, 0
    
    total = len(missing)
    rd = person_dir / "_per_file_results"
    rd.mkdir(parents=True, exist_ok=True)
    
    print(f"\n{name}: {total}个缺失，开始处理", flush=True)
    batches = [missing[i:i+batch_size] for i in range(0, total, batch_size)]
    
    ok, fail = 0, 0
    for i, batch in enumerate(batches):
        sys_msg, user_msg = build_task(person_dir, batch)
        ti = count_tok(sys_msg + user_msg)
        messages = [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}]
        
        res = call_api(messages, max_tokens=min(16384, len(batch) * 4096))
        if not res:
            print(f"  [{name}] 批{i+1} ❌ API失败", flush=True)
            fail += len(batch)
            continue
        
        rt = res['choices'][0]['message']['content']
        to = count_tok(rt)
        parsed = parse_response(rt, [r for _, r in batch])
        saved = 0
        for rn, data in parsed.items():
            (rd / rn).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            saved += 1
        
        ok += saved
        ci = ti / 1e6 * 0.27
        co = to / 1e6 * 1.10
        print(f"  [{name}] 批{i+1}/{len(batches)} ✅ {saved}/{len(batch)} (in={ti:,} ${ci:.4f} out={to:,} ${co:.4f})", flush=True)
        
        if i < len(batches) - 1:
            time.sleep(delay)
    
    cost = (sum(len(p[0]) for p in missing) / 1000) * 0.01  # rough
    print(f"{name}: ✅ {ok}完成, {fail}失败", flush=True)
    return ok, fail

from concurrent.futures import ThreadPoolExecutor, as_completed

def process_person_wrapper(pd_bs_delay):
    """Wrapper for ThreadPoolExecutor"""
    pd, bs, delay = pd_bs_delay
    return process_person(pd, bs, delay)

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true", help="自动处理所有人")
    ap.add_argument("--people", help="人物名列表，逗号分隔")
    ap.add_argument("--skip-list", help="跳过列表")
    ap.add_argument("--batch-size", type=int, default=3)
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--concurrent", type=int, default=3, help="并发处理人数")
    args = ap.parse_args()
    
    bs = args.batch_size
    
    if args.people:
        names = [n.strip() for n in args.people.split(',')]
    elif args.auto:
        names = find_all_names()
    else:
        ap.print_help()
        return
    
    skip = set()
    if args.skip_list:
        skip = set(n.strip() for n in args.skip_list.split(','))
    
    people_info = []
    for name in names:
        if name in skip:
            continue
        pd = find_person_dir(name)
        if pd is None:
            print(f"{name}: ❌ 未找到", flush=True)
            continue
        miss = count_missing(pd)
        if miss:
            people_info.append((pd, len(miss)))
        else:
            print(f"{name}: ✅ 已全部完成", flush=True)
    
    people_info.sort(key=lambda x: -x[1])
    print(f"\n共 {len(people_info)} 人待处理，共 {sum(x[1] for x in people_info)} 个prompt", flush=True)
    
    # 并行处理：用ThreadPoolExecutor同时处理多人
    max_workers = min(args.concurrent, len(people_info))
    print(f"并发 {max_workers} 人，每批{bs}个prompt\n", flush=True)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_person_wrapper, (pd, bs, args.delay)): (pd.name, cnt) for pd, cnt in people_info}
        for future in as_completed(futures):
            name, cnt = futures[future]
            try:
                ok, fail = future.result()
                print(f"{name}: ✅ {ok}完成, {fail}失败", flush=True)
            except Exception as e:
                print(f"{name}: ❌ 异常: {e}", flush=True)

if __name__ == "__main__":
    main()
