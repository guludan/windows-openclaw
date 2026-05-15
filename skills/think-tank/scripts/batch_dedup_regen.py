#!/usr/bin/env python3
"""
batch_dedup_regen.py — 人格insights去重合并 + 顶层字段生成

流程:
  Step 1: 按dim分组，每批claims交给LLM去重合并（只看claim文本）
  Step 2: 所有dim合并完成后，汇总insights+evidence交给LLM生成顶层字段

用法:
    python3 batch_dedup_regen.py <person_dir>         # 处理单人物
    python3 batch_dedup_regen.py --all                 # 处理所有人
    python3 batch_dedup_regen.py --people "name1,name2"  # 指定多人
"""

import json, sys, os, time, re, requests
from datetime import datetime
from pathlib import Path
from collections import OrderedDict, defaultdict

API_KEY = "sk-5a1bbc0258ce413ab30b5590efda23ac"
API_URL = "https://api.deepseek.com/v1/chat/completions"
BASE = Path("/mnt/d/data/think-tank-data/personalities")

VALID_DIMS = {'cognitive', 'communication', 'decision', 'logical_tracing',
              'method', 'situational', 'stance', 'tension', 'background', 'evidence'}

DIM_ALIASES = {
    'background': 'situational',
    'evidence': 'method',
    'definition': 'cognitive',
    'discriminant': 'cognitive',
    'overview': 'cognitive',
    'introduction': 'cognitive',
}


def call_api(messages, max_tokens=4096, temperature=0.1):
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    data = {"model": "deepseek-chat", "messages": messages, "max_tokens": max_tokens, "temperature": temperature, "stream": False}
    for attempt in range(3):
        try:
            resp = requests.post(API_URL, headers=headers, json=data, timeout=120)
            if resp.status_code == 200:
                return resp.json()['choices'][0]['message']['content']
            print(f"  [API] HTTP {resp.status_code}, retry {attempt+1}/3", flush=True)
            time.sleep(5)
        except Exception as e:
            print(f"  [API] {e}, retry {attempt+1}/3", flush=True)
            time.sleep(5)
    return None


def count_tok(text):
    return len(text) // 2


def standardize_dim(dim):
    """合并同义词dim到标准dim"""
    if not dim:
        return 'cognitive'
    dim = dim.strip().lower()
    return DIM_ALIASES.get(dim, dim if dim in VALID_DIMS else 'cognitive')


def find_all_people():
    """Find all people with personality_v3.json (new format, has _per_file_prompts)"""
    for root, dirs, files in os.walk(str(BASE)):
        if '_per_file_prompts' in dirs:
            pd = Path(root)
            v3_path = pd / 'personality_v3.json'
            if v3_path.exists():
                yield pd


def process_dedup(person_dir, batch_size=40):
    """Step 1: 按dim去重合并"""
    v3_path = person_dir / "personality_v3.json"
    if not v3_path.exists():
        print(f"  ❌ 无personality_v3.json", flush=True)
        return False

    v3 = json.loads(v3_path.read_text(encoding='utf-8'))
    iwe = v3.get('insights_with_evidence', [])
    if not iwe:
        print(f"  ⚠️ 无insights", flush=True)
        return False

    # 数据清洗：修复evidence中的非标准结构
    cleaned = 0
    for ins in iwe:
        ev = ins.get('evidence', [])
        if isinstance(ev, list):
            filtered = [e for e in ev if isinstance(e, dict)]
            if len(filtered) < len(ev):
                cleaned += 1
            # 如果evidence被切分成单字符列表，重新拼接
            bad_evs = [e for e in filtered if isinstance(e.get('excerpt',''), str) and len(e['excerpt']) <= 5]
            ins['evidence'] = filtered
    if cleaned:
        print(f"  [清洗] 修复了{cleaned}条insight的evidence格式", flush=True)

    name = person_dir.name
    total = len(iwe)
    print(f"\n{name}: {total} insights, 开始去重...", flush=True)

    # 标准化dim
    for ins in iwe:
        ins['dim'] = standardize_dim(ins.get('dim', 'cognitive'))

    # 按dim分组
    by_dim = defaultdict(list)
    for idx, ins in enumerate(iwe):
        by_dim[ins['dim']].append((idx, ins))

    # 每dim内去重
    kept_indices = set()
    merged_map = {}  # idx_to_keep -> list_of_merged_idxs
    total_cost = 0

    for dim, items in sorted(by_dim.items()):
        if len(items) <= 1:
            kept_indices.add(items[0][0])
            continue

        # 分批次
        batches = [items[i:i+batch_size] for i in range(0, len(items), batch_size)]

        for bidx, batch in enumerate(batches):
            if len(batch) <= 1:
                for idx, _ in batch:
                    kept_indices.add(idx)
                continue

            claims_lines = "\n".join(f"[{idx}] {ins['claim'][:500]}" for idx, ins in batch)

            prompt = f"""你是人格分析专家。以下是一组关于同一人物的insights（同属于{dim}维度），请识别出表述同一观点的insights。

## 规则
1. 只看claim的语义，不看措辞差异
2. 同一观点只保留一条（选表述最完整、最清晰的那条）
3. 其他重复的insights标记为"合并到"保留的那条
4. 完全不重复的insights单独保留

## 输入
{claims_lines}

## 输出格式（严格JSON）
```json
{{"groups": [
  {{"keep": 0, "merge": [3, 7], "reason": "表述同一观点，保留0更完整"}},
  {{"keep": 1, "merge": [], "reason": "独立观点，不需合并"}},
  ...
]}}
```"""

            msg = [
                {"role": "system", "content": "你是一个人格分析专家，严格按输出格式返回JSON。"},
                {"role": "user", "content": prompt}
            ]

            result = call_api(msg, max_tokens=4096, temperature=0.1)
            if not result:
                print(f"  [API失败] {dim}批{bidx+1}/{len(batches)}", flush=True)
                continue

            # 解析JSON
            jt = result.strip()
            jt = re.sub(r'^```json\s*', '', jt)
            jt = re.sub(r'\s*```$', '', jt)
            try:
                parsed = json.loads(jt)
            except:
                m = re.search(r'\{[\s\S]*\}', jt)
                if m:
                    try:
                        parsed = json.loads(m.group())
                    except:
                        parsed = None

            if not parsed or 'groups' not in parsed:
                print(f"  [解析失败] {dim}批{bidx+1}", flush=True)
                # 保底：全部保留
                for idx, _ in batch:
                    kept_indices.add(idx)
                continue

            # 应用合并方案
            batch_idxs = {idx for idx, _ in batch}
            batch_kept = set()
            for g in parsed['groups']:
                keep = g.get('keep')
                merge = g.get('merge', [])
                if keep is not None and keep in batch_idxs:
                    batch_kept.add(keep)
                    if merge:
                        merged_map[keep] = merged_map.get(keep, []) + merge
                for m_idx in merge:
                    if m_idx in batch_idxs:
                        pass  # 被合并

            # 如果某条没被任何group覆盖（解析bug），保底保留
            for idx, _ in batch:
                if idx not in batch_kept and idx not in sum(merged_map.values(), []):
                    # 检查是否在任意group的merge列表里
                    in_any_merge = any(idx in g.get('merge', []) for g in parsed.get('groups', []))
                    if not in_any_merge:
                        batch_kept.add(idx)

            kept_indices.update(batch_kept)

            cost_in = count_tok(prompt) / 1e6 * 0.27
            cost_out = count_tok(result) / 1e6 * 1.10
            total_cost += cost_in + cost_out
            print(f"  [{dim}] 批{bidx+1}/{len(batches)} ✅ (${cost_in+cost_out:.4f})", flush=True)
            time.sleep(0.3)

    # 执行合并：被合并的insights的evidence追加到保留的insight上
    deduped = []
    for idx, ins in enumerate(iwe):
        if idx not in kept_indices:
            continue
        # 如果有被合并到这条的insights，收集它们的evidence
        merged = merged_map.get(idx, [])
        if merged:
            existing_excerpts = {e.get('excerpt', '') for e in ins.get('evidence', [])}
            for m_idx in merged:
                if m_idx < len(iwe):
                    for ev in iwe[m_idx].get('evidence', []):
                        exc = ev.get('excerpt', '')
                        if exc and exc not in existing_excerpts:
                            ins['evidence'].append(ev)
                            existing_excerpts.add(exc)
        deduped.append(ins)

    # 更新v3
    v3['insights_with_evidence'] = deduped
    v3['_dedup_meta'] = {
        'before': total,
        'after': len(deduped),
        'timestamp': datetime.now().isoformat()
    }
    v3_path.write_text(json.dumps(v3, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"  {name}: 去重完成 {total}→{len(deduped)} (合并{total-len(deduped)}条, ${total_cost:.4f})", flush=True)
    return True


def process_regen(person_dir):
    """Step 2: 基于去重后的insights+evidence生成顶层字段"""
    v3_path = person_dir / "personality_v3.json"
    v3 = json.loads(v3_path.read_text(encoding='utf-8'))
    iwe = v3.get('insights_with_evidence', [])
    name = person_dir.name
    
    # 数据清洗：修复evidence中的非dict元素（蒸馏阶段可能产生截断的evidence）
    for ins in iwe:
        ev = ins.get('evidence', [])
        if isinstance(ev, list):
            ins['evidence'] = [e for e in ev if isinstance(e, dict)]

    print(f"\n{name}: 生成顶层字段 ({len(iwe)} insights)...", flush=True)

    # 按dim分组，生成dim统计
    by_dim = defaultdict(list)
    for ins in iwe:
        by_dim[ins.get('dim', 'cognitive')].append(ins)

    # 每个dim挑最有代表性的insights（最多8条，每条带top-3 evidence）
    dim_samples = []
    total_evidence_chars = 0
    MAX_PROMPT_CHARS = 25000

    for dim, items in sorted(by_dim.items(), key=lambda x: -len(x[1])):
        # 按evidence数量排序取最多代表性的
        items_sorted = sorted(items, key=lambda i: -len(i.get('evidence', [])))
        dim_lines = []
        for ins in items_sorted[:8]:
            claim = ins['claim'][:400]
            ev_lines = []
            for ev in ins.get('evidence', [])[:3]:
                exc = ev.get('excerpt', '')[:300]
                if exc:
                    ev_lines.append(f'    >> "{exc}"')
            entry = f"  [claim] {claim}\n" + "\n".join(ev_lines)
            if total_evidence_chars + len(entry) > MAX_PROMPT_CHARS:
                break
            dim_lines.append(entry)
            total_evidence_chars += len(entry)

        if dim_lines:
            dim_samples.append(f"## {dim} ({len(items)}条)\n" + "\n---\n".join(dim_lines))

    summary_input = "\n\n".join(dim_samples)

    prompt = f"""你是人格心理学分析专家。根据以下人物的全部insights和evidence摘要，生成人格模型的顶层字段。

人物: {name}

## 维度分布总览
{', '.join(f'{d}:{len(items)}' for d, items in sorted(by_dim.items()))}

## 关键insights与evidence
{summary_input}

## 输出格式（严格JSON）
```json
{{
  "brief": "10-20字一句话定位",
  "school": "所属学派/流派/风格",
  "domains": ["核心领域1", "核心领域2"],
  "stance_anchors": {{
    "core_belief": "最核心信念",
    "methodology": "核心方法论",
    "key_debates": ["核心争论"],
    "value_hierarchy": ["价值优先级"]
  }},
  "cognitive_patterns": {{
    "thinking_style": "思维风格",
    "reasoning_approach": "推理方式",
    "typical_biases": ["典型偏见"],
    "intellectual_strengths": ["核心优势"],
    "intellectual_blindspots": ["核心盲区"]
  }},
  "analytical_frameworks": [
    {{"name": "框架名", "components": ["组件"], "application": "应用场景"}}
  ],
  "logical_tracing": {{
    "summary": "思想来源总述",
    "lineage": {{"inherited": [], "transformed": [], "ruptured": [], "converged": []}},
    "turning_points": [],
    "grounding": {{"embodied": [], "contextual": []}},
    "collaborations": [],
    "renunciations": [],
    "adversaries": [],
    "tension_explanation": {{"note": "", "tracing": ""}}
  }},
  "decision_patterns": {{
    "risk_tolerance": "",
    "information_processing": "",
    "temporal_orientation": "",
    "conflict_resolution": ""
  }},
  "situational_triggers": {{
    "description": "",
    "triggers": [],
    "collapse_point": "",
    "default_behavior": "",
    "public_commitment_lock": ""
  }},
  "communication_profile": {{
    "style": "",
    "persuasion_tactics": [],
    "rhetorical_devices": [],
    "emotional_range": ""
  }},
  "evolution_trajectory": "",
  "key_aphorisms": []
}}
```

## 关键要求
1. 信息不足的字段用空数组[]或空字符串，绝不编造
2. 所有判断必须基于提供的evidence，不能凭空推断
3. 字段名严格按上方模板，禁止自造字段名"""

    msg = [
        {"role": "system", "content": "你是一个人格心理学分析专家。严格按模板输出JSON。"},
        {"role": "user", "content": prompt}
    ]

    result = call_api(msg, max_tokens=4096, temperature=0.3)
    if not result:
        print(f"  ❌ 顶层字段生成失败", flush=True)
        return False

    jt = result.strip()
    jt = re.sub(r'^```json\s*', '', jt)
    jt = re.sub(r'\s*```$', '', jt)
    try:
        top_fields = json.loads(jt)
    except:
        m = re.search(r'\{[\s\S]*\}', jt)
        if m:
            try:
                top_fields = json.loads(m.group())
            except:
                top_fields = None

    if not top_fields:
        print(f"  ❌ 解析失败", flush=True)
        return False

    # 更新v3顶层字段
    for key in ['brief', 'school', 'domains', 'stance_anchors', 'cognitive_patterns',
                'analytical_frameworks', 'logical_tracing', 'decision_patterns',
                'situational_triggers', 'communication_profile', 'evolution_trajectory', 'key_aphorisms']:
        if key in top_fields:
            v3[key] = top_fields[key]

    v3['_regen_meta'] = {
        'timestamp': datetime.now().isoformat(),
        'model': 'deepseek-chat'
    }
    v3_path.write_text(json.dumps(v3, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"  {name}: ✅ 顶层字段生成完成", flush=True)
    print(f"    brief={v3.get('brief','')[:60]}", flush=True)
    print(f"    school={v3.get('school','')[:60]}", flush=True)
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description='去重合并+顶层字段生成')
    parser.add_argument('person_dir', nargs='?', help='人物目录')
    parser.add_argument('--all', action='store_true', help='处理所有人')
    parser.add_argument('--people', help='指定人物(逗号分隔)')
    parser.add_argument('--batch-size', type=int, default=40, help='每批次insights数')
    parser.add_argument('--dedup-only', action='store_true', help='只做去重不做regen')
    parser.add_argument('--regen-only', action='store_true', help='只做regen不做去重')
    parser.add_argument('--force', action='store_true', help='强制重新处理')
    args = parser.parse_args()

    if args.all:
        candidates = list(find_all_people())
        print(f"共 {len(candidates)} 人")
        for pd in candidates:
            if not args.force:
                v3 = json.loads(pd.read_text(encoding='utf-8'))
                if v3.get('_dedup_meta') and args.dedup_only:
                    print(f"  {pd.name}: 已去重，跳过", flush=True)
                    continue
                if v3.get('_regen_meta') and args.regen_only:
                    print(f"  {pd.name}: 已regen，跳过", flush=True)
                    continue
                if v3.get('_dedup_meta') and v3.get('_regen_meta') and not args.dedup_only and not args.regen_only:
                    print(f"  {pd.name}: 已完成，跳过", flush=True)
                    continue
            try:
                if not args.regen_only:
                    process_dedup(pd, args.batch_size)
                if not args.dedup_only:
                    process_regen(pd)
            except Exception as e:
                print(f"{pd.name}: ❌ {e}", flush=True)
    elif args.people:
        names = [n.strip() for n in args.people.split(',')]
        for name in names:
            r = os.popen(f'find {BASE} -maxdepth 4 -name "{name}" -type d').read()
            dirs = [l for l in r.strip().split('\n') if l]
            if not dirs:
                print(f"{name}: ❌ 未找到目录", flush=True)
                continue
            pd = Path(dirs[0])
            try:
                if not args.regen_only:
                    process_dedup(pd, args.batch_size)
                if not args.dedup_only:
                    process_regen(pd)
            except Exception as e:
                print(f"{pd.name}: ❌ {e}", flush=True)
    elif args.person_dir:
        pd = Path(args.person_dir)
        if not pd.exists():
            print(f"❌ 目录不存在: {pd}", file=sys.stderr)
            sys.exit(1)
        try:
            if not args.regen_only:
                process_dedup(pd, args.batch_size)
            if not args.dedup_only:
                process_regen(pd)
        except Exception as e:
            print(f"{pd.name}: ❌ {e}", flush=True)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
