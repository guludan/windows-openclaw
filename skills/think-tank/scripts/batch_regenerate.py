#!/usr/bin/env python3
"""
batch_regenerate.py — 批量DeepSeek API regenerate人格模型
用法: nohup python3 batch_regenerate.py --all > /tmp/regenerate.log 2>&1 &
"""
import json, os, sys, time, re, requests
from pathlib import Path

API_KEY = "sk-5a1bbc0258ce413ab30b5590efda23ac"
API_URL = "https://api.deepseek.com/v1/chat/completions"
BASE = Path("/mnt/d/data/think-tank-data/personalities")

def find_all_people():
    """Find all people with personality_v3.json"""
    for root, dirs, files in os.walk(str(BASE)):
        if '_per_file_prompts' in dirs:
            pd = Path(root)
            v3_path = pd / 'personality_v3.json'
            if v3_path.exists():
                try:
                    v3 = json.loads(open(v3_path, 'r', encoding='utf-8').read())
                    yield pd
                except:
                    pass
        # Limit depth to 3 (domain/sub-domain/person)
        depth = root.replace(str(BASE), '').count(os.sep)
        if depth >= 3:
            dirs[:] = []

def count_tok(text):
    c = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    return int(c * 1.5 + (len(text) - c) / 4)

def call_api(messages, max_tokens=16384):
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    data = {"model": "deepseek-chat", "messages": messages, "max_tokens": max_tokens, "temperature": 0.3, "stream": False}
    for attempt in range(3):
        try:
            resp = requests.post(API_URL, headers=headers, json=data, timeout=300)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                time.sleep(30 * (attempt + 1))
            else:
                print(f"  [API] HTTP {resp.status_code}", flush=True)
                time.sleep(10)
        except Exception as e:
            print(f"  [API] {e}", flush=True)
            time.sleep(10)
    return None

def load_template(name):
    path = TEMPLATE_DIR / name
    if path.exists():
        return path.read_text(encoding='utf-8')
    return ""

def parse_insights(iwe):
    """Parse insights_with_evidence from v3 data"""
    return iwe.get('insights_with_evidence', iwe.get('insights', []))

def check_needs_regenerate(pd, threshold=0.4):
    """Check if person needs regenerate (single-evidence > threshold)"""
    try:
        v3 = json.loads(open(pd / 'personality_v3.json', 'r', encoding='utf-8').read())
        iwe = parse_insights(v3)
        if not iwe:
            return False, 0, []
        total = len(iwe)
        single_ev = sum(1 for i in iwe if len(i.get('evidence', i.get('evidences', []))) <= 1)
        pct = single_ev / total if total > 0 else 0
        return pct > threshold, total, iwe
    except:
        return False, 0, []

def needs_regen(pd):
    needs, total, iwe = check_needs_regenerate(pd)
    return needs, total, iwe

def process_batch_regenerate(person_dir, batch_size=15):
    """Process one person's regenerate in batches via DeepSeek API"""
    v3_path = person_dir / "personality_v3.json"
    try:
        v3 = json.loads(open(v3_path, 'r', encoding='utf-8').read())
    except:
        print(f"  ❌ 无法读取v3", flush=True)
        return False
    
    iwe = parse_insights(v3)
    if not iwe:
        print(f"  ℹ️  无insights", flush=True)
        return False
    
    total = len(iwe)
    name = person_dir.name
    print(f"\n{name}: {total} insights, 开始regenerate...", flush=True)
    
    # Backup original
    backup = v3_path.with_suffix('.json.bak')
    if not backup.exists():
        import shutil
        shutil.copy2(v3_path, backup)
    
    # Build batch prompts by dim
    by_dim = {}
    for idx, ins in enumerate(iwe):
        dim = ins.get('dim', 'general')
        by_dim.setdefault(dim, []).append((idx, ins))
    
    # Phase 1: Process each dim batch
    all_merged = {}
    for dim, items in sorted(by_dim.items()):
        dim_indices = [idx for idx, _ in items]
        # Split large dims
        if len(items) <= batch_size:
            sub_batches = [items]
        else:
            sub_batches = [items[i:i+batch_size] for i in range(0, len(items), batch_size)]
        
        dim_merged = {}
        for sub_idx, sub in enumerate(sub_batches):
            reps_str = json.dumps([
                {"idx": idx, "claim": ins.get("claim", "")[:500],
                 "evidence": [e.get("excerpt", "")[:300] for e in ins.get("evidence", ins.get("evidences", []))[:3]]}
                for idx, ins in sub
            ], ensure_ascii=False, indent=2)
            
            prompt = f"""你是一个人格心理学分析专家。

## 任务
对以下{dim}领域的{len(sub)}条insights进行合并和提炼。

## 输入
```json
{reps_str}
```

## 输出（JSON对象，key=idx，value=提炼内容）
每条输出包含：
- personality_traits: 从该insight提炼出的人格特质
- reasoning_depth: 推理深度（shallow/moderate/deep）
- evidence_strength: 证据强度（weak/moderate/strong）
- inconsistency_with_others: 与其他insights的矛盾之处（无则空字符串）

JSON格式示例：
{{
  "0": {{"personality_traits": [...], ..., "inconsistency_with_others": ""}},
  ...
}}

只有{len(sub)}条insights，请全部处理。"""
            
            messages = [
                {"role": "system", "content": "你是一个人格心理学分析专家。基于insights提取核心人格特征，输出JSON。"},
                {"role": "user", "content": prompt}
            ]
            
            res = call_api(messages, max_tokens=8192)
            if not res:
                print(f"  [{name}:{dim}] 批{sub_idx+1}/{len(sub_batches)} ❌", flush=True)
                continue
            
            content = res['choices'][0]['message']['content']
            
            # Parse JSON from response
            jt = content.strip()
            jt = re.sub(r'^```json\s*', '', jt)
            jt = re.sub(r'\s*```$', '', jt)
            
            try:
                parsed = json.loads(jt)
                if isinstance(parsed, dict):
                    dim_merged.update(parsed)
                elif isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and 'idx' in item:
                            dim_merged[str(item['idx'])] = item
            except:
                # Try to find JSON object in text
                m = re.search(r'\{[\s\S]*\}', jt)
                if m:
                    try:
                        parsed = json.loads(m.group())
                        if isinstance(parsed, dict):
                            dim_merged.update(parsed)
                    except:
                        pass
            
            tok_in = count_tok(prompt)
            tok_out = count_tok(content)
            cost_in = tok_in / 1e6 * 0.27
            cost_out = tok_out / 1e6 * 1.10
            print(f"  [{name}:{dim}] 批{sub_idx+1}/{len(sub_batches)} ✅ ({tok_in:,} ${cost_in:.4f} / {tok_out:,} ${cost_out:.4f})", flush=True)
            
            time.sleep(0.5)
        
        all_merged[dim] = dim_merged
    
    # Phase 2: Summary prompt for top-level fields
    dim_summaries = "\n".join(f"## {dim}\n共{len(items)}条" for dim, items in sorted(by_dim.items()))
    
    summary_sys = "你是一个人格心理学分析专家。根据人物维度分布和已合并的insights，生成顶层字段JSON。"
    summary_user = f"""# 人格模型顶层字段生成

人物: {name}
维度分布 ({len(by_dim)}个维度, {total}条insights):
{dim_summaries}

## 输出格式（严格JSON，不可包含insights字段）
```json
{{
  "brief": "10-20字定位",
  "school": "所属学派/流派",
  "domains": ["核心领域"],
  "stance_anchors": {{}},
  "cognitive_patterns": {{}},
  "analytical_frameworks": [],
  "logical_tracing": {{}},
  "decision_patterns": {{}},
  "situational_triggers": {{}},
  "communication_profile": {{}},
  "evolution_trajectory": "",
  "key_aphorisms": []
}}
```

信息不足用空数组，绝不编造。"""
    
    messages = [
        {"role": "system", "content": summary_sys},
        {"role": "user", "content": summary_user}
    ]
    
    res = call_api(messages, max_tokens=4096)
    summary_fields = {}
    if res:
        content = res['choices'][0]['message']['content']
        jt = content.strip()
        jt = re.sub(r'^```json\s*', '', jt)
        jt = re.sub(r'\s*```$', '', jt)
        try:
            summary_fields = json.loads(jt)
        except:
            m = re.search(r'\{[\s\S]*\}', jt)
            if m:
                try:
                    summary_fields = json.loads(m.group())
                except:
                    pass
    
    # Phase 3: Apply — update personality_v3.json
    # Merge cleaned insights back
    applied = 0
    kept_indices = set()
    for dim, merged in all_merged.items():
        for idx_str, data in merged.items():
            try:
                idx = int(idx_str)
            except ValueError:
                continue  # skip non-numeric keys
            if isinstance(data, dict):
                # Update the insight with regenerate fields
                for key, val in data.items():
                    if key in ['claim', 'dim', 'evidence', 'evidences', 'excerpt', 'source']:
                        continue  # Don't overwrite original data
                    iwe[idx][key] = val
                kept_indices.add(idx)
                applied += 1
    
    # Update top-level fields
    if 'brief' in summary_fields:
        v3['brief'] = summary_fields['brief']
    if 'school' in summary_fields:
        v3['school'] = summary_fields['school']
    if 'key_works' in summary_fields:
        v3['key_works'] = summary_fields['key_works']
    
    # Mark regenerate and write updated v3
    v3['_regenerated'] = True
    v3['_regen_date'] = time.strftime('%Y-%m-%d')
    v3_path.write_text(json.dumps(v3, ensure_ascii=False, indent=2), encoding='utf-8')
    
    print(f"  {name}: ✅ regenerate完成, 更新{applied}/{total} insights", flush=True)
    return True

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--people", help="人物名列表，逗号分隔")
    ap.add_argument("--all", action="store_true", help="所有需要regenerate的人")
    ap.add_argument("--batch-size", type=int, default=15, help="每批insights数")
    ap.add_argument("--threads", type=int, default=1, help="并发人数 (小心API限流)")
    ap.add_argument("--force", action="store_true", help="强制regenerate所有人")
    args = ap.parse_args()
    
    # Collect all people and check needs
    all_people = list(find_all_people())
    
    candidates = []
    for pd in all_people:
        name = pd.name
        if args.people:
            if name not in [n.strip() for n in args.people.split(',')]:
                continue
        
        needs, total, iwe = needs_regen(pd)
        if not needs and not args.force:
            if args.all:
                print(f"  {name:30s} ✅ {total:3d}ins 跳过(不需要)", flush=True)
            continue
        
        single_ev = sum(1 for i in iwe if len(i.get('evidence', i.get('evidences', []))) <= 1)
        pct = single_ev * 100 // total if total > 0 else 0
        candidates.append((pd, total, pct))
        print(f"  {name:30s} {total:3d}ins, 单evidence {pct}%", flush=True)
    
    candidates.sort(key=lambda x: -x[1])  # biggest first
    print(f"\n共 {len(candidates)} 人待regenerate", flush=True)
    
    if args.threads > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futures = {ex.submit(process_batch_regenerate, pd, args.batch_size): pd.name for pd, _, _ in candidates}
            for f in concurrent.futures.as_completed(futures):
                name = futures[f]
                try:
                    ok = f.result()
                    print(f"{name}: {'✅' if ok else '❌'} done", flush=True)
                except Exception as e:
                    print(f"{name}: ❌ {e}", flush=True)
    else:
        for pd, total, pct in candidates:
            process_batch_regenerate(pd, args.batch_size)

if __name__ == "__main__":
    main()
