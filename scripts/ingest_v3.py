#!/usr/bin/env python3
"""
ingest_v3.py — 从 personality_v3.json 重建向量库（单人）
用法: python3 ingest_v3.py <person_name> <person_dir>
       python3 ingest_v3.py 毛泽东 /mnt/d/data/think-tank-data/personalities/military/毛泽东
Embedding: 本地 Ollama bge-m3 (1024维, 中英文最优)
DB路径: D盘 /mnt/d/data/think-tank-data/_chromadb/lacan_db
"""
import json, sys, os, gc, time
from pathlib import Path

import chromadb
import requests

# ============ 配置 ============
DB_PATH = Path("/mnt/d/data/think-tank-data/_chromadb/lacan_db")
OLLAMA_URL = "http://172.26.0.1:11434/api/embed"
OLLAMA_MODEL = "bge-m3:latest"

COLLECTION_SPECS = {
    "unconscious":   "Lacan无意识语言模式 — 素材切片",
    "repetition":    "Lacan重复行为模式 — 素材切片",
    "transference":  "Lacan转移关系矩阵 — 素材切片",
    "drive":         "Lacan冲动驱动 — 素材切片",
    "core_profile":  "人格核心摘要 (brief+school+domains+evolution)",
    "tension":       "二元张力评分 (intuition×logic / art×commerce)",
    "insights":      "深层逻辑 — 从personality_v3提取(信念/认知/框架/决策/溯源)",
    "materials":     "原始资料语义索引",
}

COLLECTIONS_DIM4 = ["unconscious", "repetition", "transference", "drive"]

def embed_texts(texts):
    """向量化 via Ollama bge-m3（批量API）"""
    if not texts:
        return []
    for attempt in range(3):
        try:
            r = requests.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "input": texts},
                              timeout=120)
            if r.status_code == 200:
                return r.json()["embeddings"]
            else:
                print(f"  Embed API {r.status_code}, retry {attempt+1}...", flush=True)
                time.sleep(3)
        except Exception as e:
            print(f"  Embed error: {e}, retry {attempt+1}...", flush=True)
            time.sleep(5)
    return []

def extract_v3_insights(pv3, person_name):
    """从 personality_v3.json 提取深层逻辑片段 → insights集合"""
    chunks = []

    # 立场锚点
    sa = pv3.get('stance_anchors', {})
    if isinstance(sa, dict):
        if sa.get('core_belief'):
            chunks.append({'text': f"[{person_name}] 核心信念: {sa['core_belief']}", 'source': 'core_belief', 'dim': 'stance'})
        if sa.get('methodology'):
            chunks.append({'text': f"[{person_name}] 方法论: {sa['methodology']}", 'source': 'methodology', 'dim': 'method'})
        for i, d in enumerate(sa.get('key_debates', [])):
            chunks.append({'text': f"[{person_name}] 核心争议: {d}", 'source': 'key_debate', 'dim': 'stance'})
        for i, v in enumerate(sa.get('value_hierarchy', [])):
            chunks.append({'text': f"[{person_name}] 价值排序{i+1}: {v}", 'source': 'value_hierarchy', 'dim': 'stance'})

    # 认知模式
    cp = pv3.get('cognitive_patterns', {})
    if isinstance(cp, dict):
        if cp.get('thinking_style'):
            chunks.append({'text': f"[{person_name}] 思维风格: {cp['thinking_style']}", 'source': 'thinking_style', 'dim': 'cognitive'})
        if cp.get('reasoning_approach'):
            chunks.append({'text': f"[{person_name}] 推理方式: {cp['reasoning_approach']}", 'source': 'reasoning', 'dim': 'cognitive'})
        for i, b in enumerate(cp.get('typical_biases', [])):
            chunks.append({'text': f"[{person_name}] 典型认知偏差: {b}", 'source': 'bias', 'dim': 'cognitive'})
        for i, s in enumerate(cp.get('intellectual_strengths', [])):
            chunks.append({'text': f"[{person_name}] 知识优势: {s}", 'source': 'strength', 'dim': 'cognitive'})
        for i, b in enumerate(cp.get('intellectual_blindspots', [])):
            chunks.append({'text': f"[{person_name}] 认知盲区: {b}", 'source': 'blindspot', 'dim': 'cognitive'})

    # 分析框架
    for i, af in enumerate(pv3.get('analytical_frameworks', [])):
        if isinstance(af, dict):
            text = f"[{person_name}] 分析框架: {af.get('name','')} | 组成: {', '.join(af.get('components',[]))} | 应用: {af.get('application','')}"
        else:
            text = f"[{person_name}] 分析框架: {af}"
        chunks.append({'text': text, 'source': 'framework', 'dim': 'method'})

    # 决策模式
    dp = pv3.get('decision_patterns', {})
    if isinstance(dp, dict):
        for key in ['risk_tolerance', 'information_processing', 'temporal_orientation', 'conflict_resolution']:
            if dp.get(key):
                chunks.append({'text': f"[{person_name}] 决策-{key}: {dp[key]}", 'source': f'decision_{key}', 'dim': 'decision'})

    # 沟通风格
    csp = pv3.get('communication_profile', {})
    if isinstance(csp, dict):
        if csp.get('style'):
            chunks.append({'text': f"[{person_name}] 沟通风格: {csp['style']}", 'source': 'comm_style', 'dim': 'communication'})
        for i, t in enumerate(csp.get('persuasion_tactics', [])):
            chunks.append({'text': f"[{person_name}] 说服策略: {t}", 'source': 'persuasion', 'dim': 'communication'})
        for i, r in enumerate(csp.get('rhetorical_devices', [])):
            chunks.append({'text': f"[{person_name}] 修辞手法: {r}", 'source': 'rhetoric', 'dim': 'communication'})

    # 核心格言
    for i, a in enumerate(pv3.get('key_aphorisms', [])):
        chunks.append({'text': f"[{person_name}] 核心格言: {a}", 'source': 'aphorism', 'dim': 'communication'})

    # 思想演变
    ev = pv3.get('evolution_trajectory', '')
    if ev:
        chunks.append({'text': f"[{person_name}] 思想演变: {ev}", 'source': 'evolution', 'dim': 'stance'})

    # V3新增：logical_tracing
    lt = pv3.get('logical_tracing', {})
    if isinstance(lt, dict):
        if lt.get('summary'):
            chunks.append({'text': f"[{person_name}] 逻辑溯源概要: {lt['summary']}", 'source': 'lt_summary', 'dim': 'logical_tracing'})

        for lineage in lt.get('lineage', []):
            if isinstance(lineage, dict):
                text = f"[{person_name}] 思想谱系-{lineage.get('type','')}: {lineage.get('source','')} → {lineage.get('influence','')}"
            else:
                text = f"[{person_name}] 思想谱系: {lineage}"
            chunks.append({'text': text, 'source': 'lt_lineage', 'dim': 'logical_tracing'})

        for i, tp in enumerate(lt.get('turning_points', [])):
            if isinstance(tp, dict):
                text = f"[{person_name}] 思想转折{i+1}: {tp.get('period','')} - {tp.get('description','')}"
            else:
                text = f"[{person_name}] 思想转折{i+1}: {tp}"
            chunks.append({'text': text, 'source': 'lt_turning_point', 'dim': 'logical_tracing'})

        for g in lt.get('grounding', []):
            if isinstance(g, dict):
                text = f"[{person_name}] 扎根经验-{g.get('type','')}: {g.get('experience','')}"
            else:
                text = f"[{person_name}] 扎根经验: {g}"
            chunks.append({'text': text, 'source': 'lt_grounding', 'dim': 'logical_tracing'})

        for c in lt.get('collaborations', []):
            if isinstance(c, dict):
                text = f"[{person_name}] 关键合作: {c.get('partner','')} - {c.get('nature','')}"
            else:
                text = f"[{person_name}] 关键合作: {c}"
            chunks.append({'text': text, 'source': 'lt_collaboration', 'dim': 'logical_tracing'})

        for r in lt.get('renunciations', []):
            if isinstance(r, dict):
                text = f"[{person_name}] 拒绝与放弃: {r.get('what','')} - {r.get('reason','')}"
            else:
                text = f"[{person_name}] 拒绝与放弃: {r}"
            chunks.append({'text': text, 'source': 'lt_renunciation', 'dim': 'logical_tracing'})

        for a in lt.get('adversaries', []):
            if isinstance(a, dict):
                text = f"[{person_name}] 论敌交锋: {a.get('adversary','')} - {a.get('issue','')}"
            else:
                text = f"[{person_name}] 论敌交锋: {a}"
            chunks.append({'text': text, 'source': 'lt_adversary', 'dim': 'logical_tracing'})

        if lt.get('tension_explanation'):
            chunks.append({'text': f"[{person_name}] 张力溯源解释: {lt['tension_explanation']}", 'source': 'lt_tension', 'dim': 'logical_tracing'})

    return chunks


def ingest_materials(person_name, person_dir, client, cols):
    """素材文件分块入库到 materials 集合"""
    mat_dir = Path(person_dir) / "materials"
    if not mat_dir.exists():
        print(f"  No materials dir", flush=True)
        return 0

    mat_col = cols["materials"]
    # 删除旧数据
    try:
        old = mat_col.get(where={"person": person_name}, include=[])
        if old['ids']:
            mat_col.delete(ids=old['ids'])
            print(f"  Deleted {len(old['ids'])} old materials", flush=True)
    except: pass

    count = 0
    embed_batch = []
    texts_batch = []
    ids_batch = []
    metas_batch = []

    for f in sorted(mat_dir.glob("*.txt")):
        if f.stat().st_size < 500:
            continue
        try:
            content = f.read_text(encoding='utf-8', errors='ignore')[:8000]  # 每文件取前8000字
            chunks = [content[i:i+800] for i in range(0, len(content), 800)]  # 800字一块
            for ci, chunk in enumerate(chunks):
                texts_batch.append(f"[{person_name}] {f.stem}[{ci}]: {chunk}")
                ids_batch.append(f"{person_name}_mat_{f.stem}_{ci}")
                metas_batch.append({"person": person_name, "source": f.name, "chunk": ci})
                embed_batch.append(f"[{person_name}] {f.stem}[{ci}]: {chunk}")
                count += 1

                # embed每8条
                if len(embed_batch) >= 8:
                    embeds = embed_texts(embed_batch)
                    if embeds and len(embeds) == len(embed_batch):
                        mat_col.add(documents=embed_batch, embeddings=embeds,
                                   ids=ids_batch[-len(embeds):], metadatas=metas_batch[-len(embeds):])
                    embed_batch = []
                    gc.collect()
        except Exception as e:
            print(f"  Error {f.name}: {e}", flush=True)

    # 剩余
    if embed_batch:
        embeds = embed_texts(embed_batch)
        if embeds and len(embeds) == len(embed_batch):
            mat_col.add(documents=embed_batch, embeddings=embeds,
                       ids=ids_batch[-len(embeds):], metadatas=metas_batch[-len(embeds):])

    print(f"  materials: {count} chunks from {len(list(mat_dir.glob('*.txt')))} files", flush=True)
    return count


def run(person_name, person_dir):
    person_dir = Path(person_dir)
    pv3_path = person_dir / "personality_v3.json"
    if not pv3_path.exists():
        print(f"ERROR: {pv3_path} not found", flush=True)
        return

    # JSON修复容错
    import re as _re
    raw = open(pv3_path, encoding='utf-8').read()
    # 移除控制字符(保留\n\t)
    raw = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)
    try:
        pv3 = json.loads(raw)
    except json.JSONDecodeError:
        # 用json5风格容错：尝试把值内未转义的引号转义
        chars = list(raw)
        in_str = False
        for i, ch in enumerate(chars):
            if not in_str:
                if ch == '"': in_str = True
            else:
                if ch == '\\' and i+1 < len(chars):
                    pass  # skip next
                elif ch == '"':
                    j = i + 1
                    while j < len(chars) and chars[j] in ' \t': j += 1
                    if j < len(chars) and chars[j] in ',]}:':
                        in_str = False
                    else:
                        chars[i] = '\\"'
        pv3 = json.loads(''.join(chars))
    print(f"Ingesting {person_name} v3... ({pv3.get('_distill_meta',{}).get('source_count','?')} sources)", flush=True)

    # 初始化DB（D盘）
    os.makedirs(DB_PATH, exist_ok=True)
    client = chromadb.PersistentClient(path=str(DB_PATH))
    cols = {}
    for name, desc in COLLECTION_SPECS.items():
        cols[name] = client.get_or_create_collection(name=name, metadata={"desc": desc})

    # 1. core_profile
    core_text = f"[{person_name}] {pv3.get('brief','')} | 学派: {pv3.get('school','')} | 领域: {', '.join(pv3.get('domains',[]))} | 演变: {pv3.get('evolution_trajectory','')}"
    core_id = f"{person_name}_core"
    core_col = cols["core_profile"]
    try: core_col.delete(ids=[core_id])
    except: pass
    embeds = embed_texts([core_text])
    if embeds:
        core_col.add(documents=[core_text], embeddings=embeds, ids=[core_id],
                    metadatas=[{"person": person_name, "source": "personality_v3"}])
    print(f"  core_profile: 1 vector", flush=True)

    # 2. insights（v3全部深层逻辑）
    insight_chunks = extract_v3_insights(pv3, person_name)
    ins_col = cols["insights"]
    try:
        old = ins_col.get(where={"person": person_name}, include=[])
        if old['ids']:
            ins_col.delete(ids=old['ids'])
    except: pass

    if insight_chunks:
        all_texts = [c['text'] for c in insight_chunks]
        all_ids = [f"{person_name}_ins_{i}" for i in range(len(insight_chunks))]
        all_metas = [{"person": person_name, "source": c['source'], "dim": c['dim']} for c in insight_chunks]
        ins_embeds = []
        # batch embed (bge-m3支持批量)
        for bi in range(0, len(all_texts), 8):
            batch = all_texts[bi:bi+8]
            e = embed_texts(batch)
            if e:
                ins_embeds.extend(e)
            gc.collect()
        if ins_embeds:
            ins_col.add(documents=all_texts[:len(ins_embeds)], embeddings=ins_embeds,
                       ids=all_ids[:len(ins_embeds)], metadatas=all_metas[:len(ins_embeds)])
        print(f"  insights: {len(ins_embeds)} vectors", flush=True)
    gc.collect()

    # 3. materials（原始素材分块）
    mat_count = ingest_materials(person_name, person_dir, client, cols)

    # 汇总
    print(f"\n=== {person_name} 入库完成 ===", flush=True)
    for name, col in cols.items():
        cnt = col.count()
        try:
            pc = len(col.get(where={"person": person_name}, include=[])['ids'])
        except:
            pc = 0
        if pc > 0:
            print(f"  {name}: {pc}/{cnt} total", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 ingest_v3.py <person_name> <person_dir>")
        sys.exit(1)
    run(sys.argv[1], sys.argv[2])
