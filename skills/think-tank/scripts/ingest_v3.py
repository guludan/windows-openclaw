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
    "materials":     "原始资料语义索引",
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
        add(f"[{person_name}] 张力溯源: {te['tracing']}", 'tension', 'logical_tracing')

    return chunks


def ingest_materials(person_name, person_dir, cols):
    """素材文件分块入库到 materials 集合，过滤噪音"""
    mat_dir = Path(person_dir) / "materials"
    if not mat_dir.exists():
        return 0

    mat_col = cols["materials"]
    # 删除旧数据
    try:
        old = mat_col.get(where={"person": person_name}, include=[])
        if old['ids']:
            mat_col.delete(ids=old['ids'])
    except: pass

    count = 0
    embed_batch = []
    texts_batch = []
    ids_batch = []
    metadatas_batch = []

    for f in sorted(mat_dir.glob("*.txt")):
        if f.stat().st_size < 500:
            continue
        try:
            content = f.read_text(encoding='utf-8', errors='ignore')[:8000]
            # 快速噪音检测
            lines = content.split('\n')
            link_lines = sum(1 for l in lines if 'http' in l and len(l.strip()) < 100)
            if len(lines) > 5 and link_lines / len(lines) > 0.3:
                continue  # 跳过导航密集的噪音文件

            chunks = [content[i:i+800] for i in range(0, len(content), 800)]
            for ci, chunk in enumerate(chunks):
                texts_batch.append(f"[{person_name}] {f.stem}[{ci}]: {chunk}")
                ids_batch.append(f"{person_name}_mat_{f.stem}_{ci}")
                metadatas_batch.append({"person": person_name, "source": f.name, "chunk": ci,
                                          "material_id": f"{person_name}_mat_{hashlib.md5(f.stem.encode()).hexdigest()[:8]}"})
                count += 1

                if len(texts_batch) >= 8:
                    embeds = embed_texts(texts_batch)
                    if embeds and len(embeds) == len(texts_batch):
                        mat_col.add(documents=texts_batch, embeddings=embeds,
                                   ids=ids_batch, metadatas=metadatas_batch)
                    texts_batch = []
                    ids_batch = []
                    metadatas_batch = []
                    gc.collect()
        except Exception as e:
            print(f"  Error {f.name}: {e}", flush=True)

    if texts_batch:
        embeds = embed_texts(texts_batch)
        if embeds and len(embeds) == len(texts_batch):
            mat_col.add(documents=texts_batch, embeddings=embeds,
                       ids=ids_batch, metadatas=metadatas_batch)

    print(f"  materials: {count} chunks", flush=True)
    return count


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

    # 3. materials
    mat_count = ingest_materials(person_name, person_dir, cols)

    # 汇总
    print(f"  DONE — core:1 + insights:{len(ins_embeds) if insight_chunks else 0} + tension:{len(ten_embeds) if insight_chunks else 0} + materials:{mat_count}", flush=True)


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
