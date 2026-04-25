#!/usr/bin/env python3
"""
ingest_v3.py — 从 personality_v3.json 重建向量库（单人）

用法:
    python3 ingest_v3.py <person_name> <person_dir>
    python3 ingest_v3.py 毛泽东 /mnt/d/data/think-tank-data/personalities/military/毛泽东

Embedding: 本地 Ollama bge-m3 (1024维)
DB路径: /mnt/d/data/think-tank-data/_chromadb/lacan_db
"""

import json, sys, os, gc, time, re, hashlib
from pathlib import Path

import chromadb
import requests

# ============ 配置 ============
DB_PATH = Path("/mnt/d/data/think-tank-data/_chromadb/lacan_db")
OLLAMA_URL = "http://172.26.0.1:11434/api/embed"
OLLAMA_MODEL = "bge-m3:latest"

COLLECTION_SPECS = {
    "core_profile":  "人格核心摘要 (brief+school+domains+evolution)",
    "insights":      "深层逻辑 (信念/认知/框架/决策/溯源)",
    "evidence":      "被蒸馏标注的原文证据片段",
}

# Lacan 维度集合（暂时不用，保留结构）
# "unconscious", "repetition", "transference", "drive", "tension"


def embed_texts(texts):
    """向量化 via Ollama bge-m3"""
    if not texts:
        return []
    for attempt in range(3):
        try:
            r = requests.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "input": texts}, timeout=120)
            if r.status_code == 200:
                return r.json()["embeddings"]
            print(f"  Embed API {r.status_code}, retry {attempt+1}...", flush=True)
            time.sleep(3)
        except Exception as e:
            print(f"  Embed error: {e}, retry {attempt+1}...", flush=True)
            time.sleep(5)
    return []


def is_meaningful(text, min_len=30):
    """检查文本是否有实际意义（过滤空值/纯标记/噪音）"""
    if not text or len(text.strip()) < min_len:
        return False
    # 过滤纯标记行（如 "论敌交锋: -"）
    stripped = text.strip()
    if stripped.endswith(':') or stripped.endswith('：'):
        return False
    if stripped.endswith(': -') or stripped.endswith(': —') or stripped.endswith('：—'):
        return False
    # 过滤纯结构无内容
    if re.match(r'^\[.*?\]\s+\S+:\s*[-—]?\s*$', stripped):
        return False
    return True


def extract_insights(pv3, person_name):
    """从 personality_v3.json 提取有意义的深层逻辑片段

    目标：RAG检索能回答"这个人是怎么思考的"、"依据是什么"
    每条记录必须包含具体内容 + 来源
    """
    chunks = []

    def add(text, source, dim):
        if is_meaningful(text):
            chunks.append({'text': text, 'source': source, 'dim': dim})

    # === 立场锚点 ===
    sa = pv3.get('stance_anchors', {})
    if isinstance(sa, dict):
        add(f"[{person_name}] 核心信念: {sa.get('core_belief', '')}", 'core_belief', 'stance')
        add(f"[{person_name}] 方法论: {sa.get('methodology', '')}", 'methodology', 'stance')
        for d in sa.get('key_debates', []):
            add(f"[{person_name}] 核心争议: {d}", 'key_debate', 'stance')
        for i, v in enumerate(sa.get('value_hierarchy', [])):
            add(f"[{person_name}] 价值排序{i+1}: {v}", 'value_hierarchy', 'stance')

    # === 认知模式 ===
    cp = pv3.get('cognitive_patterns', {})
    if isinstance(cp, dict):
        add(f"[{person_name}] 思维风格: {cp.get('thinking_style', '')}", 'thinking_style', 'cognitive')
        add(f"[{person_name}] 推理方式: {cp.get('reasoning_approach', '')}", 'reasoning', 'cognitive')
        for b in cp.get('typical_biases', []):
            add(f"[{person_name}] 典型偏差: {b}", 'bias', 'cognitive')
        for s in cp.get('intellectual_strengths', []):
            add(f"[{person_name}] 核心优势: {s}", 'strength', 'cognitive')
        for b in cp.get('intellectual_blindspots', []):
            add(f"[{person_name}] 认知盲区: {b}", 'blindspot', 'cognitive')

    # === 分析框架 ===
    for af in pv3.get('analytical_frameworks', []):
        if isinstance(af, dict) and af.get('name'):
            comps = ', '.join(af.get('components', []))
            app = af.get('application', '')
            add(f"[{person_name}] 框架「{af['name']}」: {comps} | 应用: {app}", 'framework', 'method')
            if af.get('source_ref'):
                chunks[-1]['text'] += f" [来源: {af['source_ref']}]"

    # === 决策模式 ===
    dp = pv3.get('decision_patterns', {})
    if isinstance(dp, dict):
        for key, label in [('risk_tolerance','风险偏好'), ('information_processing','信息处理'),
                           ('temporal_orientation','时间取向'), ('conflict_resolution','冲突解决')]:
            if dp.get(key):
                add(f"[{person_name}] 决策-{label}: {dp[key]}", f'decision_{key}', 'decision')

    # === 沟通风格 ===
    csp = pv3.get('communication_profile', {})
    if isinstance(csp, dict):
        add(f"[{person_name}] 沟通风格: {csp.get('style', '')}", 'comm_style', 'communication')
        for t in csp.get('persuasion_tactics', []):
            add(f"[{person_name}] 说服策略: {t}", 'persuasion', 'communication')

    # === 核心格言 ===
    for a in pv3.get('key_aphorisms', []):
        add(f"[{person_name}] 格言: {a}", 'aphorism', 'communication')

    # === 思想演变 ===
    ev = pv3.get('evolution_trajectory', '')
    if ev:
        add(f"[{person_name}] 思想演变: {ev}", 'evolution', 'stance')

    # === logical_tracing（v3核心 — 回答"是什么让这个人这么想"）===
    lt = pv3.get('logical_tracing', {})
    if not isinstance(lt, dict):
        return chunks

    add(f"[{person_name}] 逻辑溯源: {lt.get('summary', '')}", 'lt_summary', 'logical_tracing')

    # lineage: dict {inherited, transformed, ruptured, converged}
    lineage = lt.get('lineage', {})
    if isinstance(lineage, dict):
        for category, label in [('inherited','继承'), ('transformed','改造'), ('ruptured','断裂'), ('converged','融合')]:
            items = lineage.get(category, [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                parts = [item.get('source', ''), item.get('nature', ''), item.get('description', '')]
                text = ' | '.join(p for p in parts if p)
                ref = item.get('source_ref', '')
                if text and ref:
                    add(f"[{person_name}] 谱系-{label}: {text} [来源: {ref}]", f'lineage_{category}', 'logical_tracing')
                elif text:
                    add(f"[{person_name}] 谱系-{label}: {text}", f'lineage_{category}', 'logical_tracing')

    # turning_points: list of dicts
    for i, tp in enumerate(lt.get('turning_points', [])):
        if not isinstance(tp, dict):
            continue
        parts = []
        if tp.get('title'): parts.append(tp['title'])
        if tp.get('period'): parts.append(f"({tp['period']})")
        if tp.get('narrative'): parts.append(tp['narrative'])
        if tp.get('catalyst'): parts.append(f"催化剂: {tp['catalyst']}")
        if tp.get('significance'): parts.append(f"意义: {tp['significance']}")
        text = ' '.join(parts)
        ref = tp.get('source_ref', '')
        if text and ref:
            add(f"[{person_name}] 转折{i+1}: {text} [来源: {ref}]", 'turning_point', 'logical_tracing')
        elif text:
            add(f"[{person_name}] 转折{i+1}: {text}", 'turning_point', 'logical_tracing')

    # grounding: dict {embodied, contextual}
    grounding = lt.get('grounding', {})
    if isinstance(grounding, dict):
        for category, label in [('embodied','肉身经历'), ('contextual','语境沉浸')]:
            items = grounding.get(category, [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                parts = [item.get('context', ''), item.get('role', ''), item.get('impact', '')]
                text = ' | '.join(p for p in parts if p)
                ref = item.get('source_ref', '')
                if text and ref:
                    add(f"[{person_name}] 扎根-{label}: {text} [来源: {ref}]", f'grounding_{category}', 'logical_tracing')
                elif text:
                    add(f"[{person_name}] 扎根-{label}: {text}", f'grounding_{category}', 'logical_tracing')

    # collaborations: list of dicts {with, nature, product, significance, source_ref}
    for item in lt.get('collaborations', []):
        if not isinstance(item, dict):
            continue
        parts = [f"与{item.get('with','')}", item.get('nature',''), item.get('product',''), item.get('significance','')]
        text = ' | '.join(p for p in parts if p and p != f"与")
        ref = item.get('source_ref', '')
        if text and ref:
            add(f"[{person_name}] 合作: {text} [来源: {ref}]", 'collaboration', 'logical_tracing')
        elif text:
            add(f"[{person_name}] 合作: {text}", 'collaboration', 'logical_tracing')

    # renunciations: list of dicts {what, reason, aftermath, source_ref}
    for item in lt.get('renunciations', []):
        if not isinstance(item, dict):
            continue
        parts = [item.get('what',''), item.get('reason',''), item.get('aftermath','')]
        text = ' | '.join(p for p in parts if p)
        ref = item.get('source_ref', '')
        if text and ref:
            add(f"[{person_name}] 拒绝: {text} [来源: {ref}]", 'renunciation', 'logical_tracing')
        elif text:
            add(f"[{person_name}] 拒绝: {text}", 'renunciation', 'logical_tracing')

    # adversaries: list of dicts {who, axis, dynamic, source_ref}
    for item in lt.get('adversaries', []):
        if not isinstance(item, dict):
            continue
        parts = [item.get('who',''), item.get('axis',''), item.get('dynamic','')]
        text = ' | '.join(p for p in parts if p)
        ref = item.get('source_ref', '')
        if text and ref:
            add(f"[{person_name}] 论敌: {text} [来源: {ref}]", 'adversary', 'logical_tracing')
        elif text:
            add(f"[{person_name}] 论敌: {text}", 'adversary', 'logical_tracing')

    # tension_explanation: dict {note, tracing}
    te = lt.get('tension_explanation', {})
    if isinstance(te, dict) and te.get('tracing'):
        add(f"[{person_name}] 张力溯源: {te['tracing']}", 'logical_tracing', 'tension')

    return chunks


def ingest_evidence(person_name, pv3, cols):
    """从 personality_v3.json 的 insights_with_evidence 字段入库到 insights 和 evidence 集合"""
    iwe = pv3.get('insights_with_evidence', [])
    if not iwe:
        print("  evidence: 0 (insights_with_evidence字段为空)", flush=True)
        return 0, 0

    evidence_col = cols["evidence"]

    # 删除旧数据
    for col_name in ["insights", "evidence"]:
        try:
            old = cols[col_name].get(where={"person": person_name}, include=[])
            if old['ids']:
                cols[col_name].delete(ids=old['ids'])
        except:
            pass

    # Phase 1: 先入库所有 evidence 条目，收集 ID 映射
    ev_texts, ev_ids, ev_metas = [], [], []
    # Map: (insight_index, ev_index_within_insight) -> ev_id
    ev_id_map = {}
    global_ev_idx = 0

    for i, ins in enumerate(iwe):
        if not isinstance(ins, dict):
            continue
        claim = ins.get('claim', '')
        dim = ins.get('dim', '')
        evidences = ins.get('evidence', [])
        for ei, ev in enumerate(evidences):
            if not isinstance(ev, dict):
                continue
            excerpt = ev.get('excerpt', '')
            if not excerpt or len(excerpt.strip()) < 20:
                continue
            ev_id = f"{person_name}_ev_{global_ev_idx:03d}"
            ev_id_map[(i, ei)] = ev_id
            source_file = ev.get('source_file', '')
            location = ev.get('location', '')
            ev_texts.append(excerpt)
            ev_ids.append(ev_id)
            ev_metas.append({
                "person": person_name,
                "source_file": source_file,
                "location": location,
                "insight_id": f"{person_name}_ins_{i:03d}",
                "material_id": f"{person_name}_mat_{hashlib.md5(source_file.encode()).hexdigest()[:8]}" if source_file else "",
            })
            global_ev_idx += 1

    # 批量 embed evidence
    for bi in range(0, len(ev_texts), 8):
        batch = ev_texts[bi:bi+8]
        embeds = embed_texts(batch)
        if embeds and len(embeds) == len(batch):
            evidence_col.add(
                documents=batch,
                embeddings=embeds,
                ids=ev_ids[bi:bi+8],
                metadatas=ev_metas[bi:bi+8]
            )
        gc.collect()

    # Phase 2: 入库 insights (每个 claim 一条，关联 evidence_ids)
    ins_texts, ins_ids, ins_metas = [], [], []
    for i, ins in enumerate(iwe):
        if not isinstance(ins, dict):
            continue
        claim = ins.get('claim', '')
        if not claim or len(claim.strip()) < 20:
            continue
        dim = ins.get('dim', '')
        evidences = ins.get('evidence', [])
        # 收集关联的 evidence IDs
        linked_ev_ids = []
        for ei in range(len(evidences)):
            if (i, ei) in ev_id_map:
                linked_ev_ids.append(ev_id_map[(i, ei)])
        first_source = evidences[0].get('source_file', '') if evidences else ''

        ins_id = f"{person_name}_ins_{i:03d}"
        ins_texts.append(claim)
        ins_ids.append(ins_id)
        ins_metas.append({
            "person": person_name,
            "dim": dim,
            "claim": claim[:200],
            "evidence_ids": ",".join(linked_ev_ids),
            "source_file": first_source,
        })

    # 批量 embed insights
    for bi in range(0, len(ins_texts), 8):
        batch = ins_texts[bi:bi+8]
        embeds = embed_texts(batch)
        if embeds and len(embeds) == len(batch):
            cols["insights"].add(
                documents=batch,
                embeddings=embeds,
                ids=ins_ids[bi:bi+8],
                metadatas=ins_metas[bi:bi+8]
            )
        gc.collect()

    print(f"  evidence: {len(ev_ids)} excerpts, {len(ins_ids)} insights", flush=True)
    return len(ins_ids), len(ev_ids)


def load_v3(pv3_path):
    """加载并修复 JSON"""
    import re as _re
    raw = pv3_path.read_text(encoding='utf-8')
    raw = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 容错：修复值内未转义的引号
        chars = list(raw)
        in_str = False
        for i, ch in enumerate(chars):
            if not in_str:
                if ch == '"': in_str = True
            else:
                if ch == '\\' and i+1 < len(chars):
                    pass
                elif ch == '"':
                    j = i + 1
                    while j < len(chars) and chars[j] in ' \t': j += 1
                    if j < len(chars) and chars[j] in ',]}:':
                        in_str = False
                    else:
                        chars[i] = '\\"'
        return json.loads(''.join(chars))


def run(person_name, person_dir):
    person_dir = Path(person_dir)
    pv3_path = person_dir / "personality_v3.json"
    if not pv3_path.exists():
        print(f"ERROR: {pv3_path} not found", flush=True)
        return

    pv3 = load_v3(pv3_path)
    meta = pv3.get('_distill_meta', {})
    print(f"Ingesting {person_name}... ({meta.get('source_count','?')} sources)", flush=True)

    os.makedirs(DB_PATH, exist_ok=True)
    client = chromadb.PersistentClient(path=str(DB_PATH))
    cols = {}
    for name, desc in COLLECTION_SPECS.items():
        cols[name] = client.get_or_create_collection(name=name, metadata={"desc": desc})

    # Also ensure Lacan collections exist (for compatibility)
    for name in ["unconscious", "repetition", "transference", "drive", "tension"]:
        cols[name] = client.get_or_create_collection(name=name)

    # 1. core_profile
    core_text = (
        f"[{person_name}] {pv3.get('brief','')} "
        f"| 学派: {pv3.get('school','')} "
        f"| 领域: {', '.join(pv3.get('domains',[]))} "
        f"| 演变: {pv3.get('evolution_trajectory','')}"
    )
    core_id = f"{person_name}_core"
    core_col = cols["core_profile"]
    try: core_col.delete(ids=[core_id])
    except: pass
    embeds = embed_texts([core_text])
    if embeds:
        core_col.add(documents=[core_text], embeddings=embeds, ids=[core_id],
                    metadatas=[{"person": person_name, "source": "personality_v3"}])
    print(f"  core_profile: 1", flush=True)

    # 2. insights
    insight_chunks = extract_insights(pv3, person_name)
    ins_col = cols["insights"]
    tension_col = cols["tension"]
    try:
        old = ins_col.get(where={"person": person_name}, include=[])
        if old['ids']: ins_col.delete(ids=old['ids'])
        old_t = tension_col.get(where={"person": person_name}, include=[])
        if old_t['ids']: tension_col.delete(ids=old_t['ids'])
    except: pass

    if insight_chunks:
        # v4.2: 按dim分流 — tension写入独立集合，其余写入insights
        ins_texts, ins_ids, ins_metas = [], [], []
        ten_texts, ten_ids, ten_metas = [], [], []
        for i, c in enumerate(insight_chunks):
            meta = {"person": person_name, "source": c['source'], "dim": c['dim'],
                    "source_ref": "", "material_id": ""}
            # 提取source_ref建立关联
            m = re.search(r'\[来源:\s*(\S+)\]', c['text'])
            if m:
                meta["source_ref"] = m.group(1)
                meta["material_id"] = f"{person_name}_mat_{hashlib.md5(m.group(1).encode()).hexdigest()[:8]}"
            if c['dim'] == 'tension':
                ten_texts.append(c['text'])
                ten_ids.append(f"{person_name}_ten_{len(ten_ids)}")
                ten_metas.append(meta)
            else:
                ins_texts.append(c['text'])
                ins_ids.append(f"{person_name}_ins_{len(ins_ids)}")
                ins_metas.append(meta)
        # 写入insights
        ins_embeds = []
        for bi in range(0, len(ins_texts), 8):
            e = embed_texts(ins_texts[bi:bi+8])
            if e: ins_embeds.extend(e)
            gc.collect()
        if ins_embeds:
            ins_col.add(documents=ins_texts[:len(ins_embeds)], embeddings=ins_embeds,
                       ids=ins_ids[:len(ins_embeds)], metadatas=ins_metas[:len(ins_embeds)])
        # 写入tension
        ten_embeds = []
        for bi in range(0, len(ten_texts), 8):
            e = embed_texts(ten_texts[bi:bi+8])
            if e: ten_embeds.extend(e)
            gc.collect()
        if ten_embeds:
            tension_col.add(documents=ten_texts[:len(ten_embeds)], embeddings=ten_embeds,
                           ids=ten_ids[:len(ten_embeds)], metadatas=ten_metas[:len(ten_embeds)])
        print(f"  insights: {len(ins_embeds)} | tension: {len(ten_embeds)} (原始{len(insight_chunks)})", flush=True)
    else:
        print(f"  insights: 0 (无有意义内容)", flush=True)
    gc.collect()

    # 3. evidence (from insights_with_evidence)
    ev_ins_count, ev_ev_count = ingest_evidence(person_name, pv3, cols)

    # 汇总
    print(f"  DONE — core:1 + insights:{len(ins_embeds) if insight_chunks else 0} + tension:{len(ten_embeds) if insight_chunks else 0} + evidence:{ev_ev_count} excerpts ({ev_ins_count} insights)", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 ingest_v3.py <person_name> <person_dir>")
        print("       python3 ingest_v3.py --all  # 重建全部")
        sys.exit(1)

    if sys.argv[1] == '--all':
        base = Path("/mnt/d/data/think-tank-data/personalities")
        for group in sorted(base.iterdir()):
            if not group.is_dir(): continue
            # Handle flat (military) and nested (scholars/sub/person) structures
            for candidate in sorted(group.iterdir()):
                if not candidate.is_dir(): continue
                if (candidate / "personality_v3.json").exists():
                    run(candidate.name, candidate)
                else:
                    for person in sorted(candidate.iterdir()):
                        if person.is_dir() and (person / "personality_v3.json").exists():
                            run(person.name, person)
    else:
        run(sys.argv[1], sys.argv[2])
