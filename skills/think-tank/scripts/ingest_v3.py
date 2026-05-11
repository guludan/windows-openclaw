#!/usr/bin/env python3
"""
ingest_v3.py — 从 personality_v3.json 入库到向量库（单人/全部）

集合架构（v5）:
  personas   — 人物匹配（按领域/维度分条，避免跨学科混合）
  cognitive  — 认知模式（思维风格/推理/偏差/决策模式）
  claims     — 具体观点（insights_with_evidence.claim + evidence_ids）
  evidence   — 原文证据摘录
  tracing    — 思想谱系（lineage/turning_points/grounding/collaborations/renunciations/adversaries）
  tension    — 内在矛盾

用法:
    python3 ingest_v3.py <person_name> <person_dir>
    python3 ingest_v3.py --all
"""

import json, sys, os, gc, time, re, hashlib
from pathlib import Path

import chromadb
import requests

# ============ 配置 ============
BASE_DIR = Path("/mnt/d/data/think-tank-data/personalities")
DB_PATH = Path("/mnt/d/data/think-tank-data/personalities/_chromadb/lacan_db")
OLLAMA_URL = "http://172.26.0.1:11434/api/embed"
OLLAMA_MODEL = "bge-m3:latest"

COLLECTION_NAMES = [
    "personas", "cognitive", "claims", "evidence", "tracing", "tension"
]

BATCH_SIZE = 4  # 减小批次防OOM和ollama 500


def embed_texts(texts):
    """向量化 via Ollama bge-m3，含NaN容错"""
    if not texts:
        return []
    
    # 先尝试批量embed
    result = _try_embed(texts)
    if result is not None:
        return result
    
    # 批量失败（可能某条触发了NaN），逐条embed
    if len(texts) > 1:
        print(f"  Batch embed failed, falling back to single-item embed...", flush=True)
        embeds = []
        for t in texts:
            e = _try_embed([t])
            if e is None:
                # 单条也失败，用零向量
                print(f"  WARN: NaN embedding, using zero vector: {t[:50]}...", flush=True)
                embeds.append([0.0] * 1024)
            else:
                embeds.append(e[0])
        return embeds
    
    # 单条也失败
    print(f"  WARN: Embed failed, using zero vector: {texts[0][:50]}...", flush=True)
    return [[0.0] * 1024]


def _try_embed(texts):
    """尝试批量embed，返回None表示失败"""
    for attempt in range(2):
        try:
            r = requests.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "input": texts}, timeout=120)
            if r.status_code == 200:
                embeds = r.json()["embeddings"]
                # NaN检查
                clean = []
                for emb in embeds:
                    if any(x != x for x in emb):
                        return None  # 触发fallback
                    clean.append(emb)
                return clean
            if r.status_code == 500:
                return None  # NaN导致的500，不要重试
            print(f"  Embed API {r.status_code}, retry {attempt+1}...", flush=True)
            time.sleep(10 * (attempt + 1))
        except Exception as e:
            print(f"  Embed error: {e}, retry {attempt+1}...", flush=True)
            time.sleep(10 * (attempt + 1))
    return None


def is_meaningful(text, min_len=30):
    """检查文本是否有实际意义"""
    if not text or len(text.strip()) < min_len:
        return False
    stripped = text.strip()
    if stripped.endswith(':') or stripped.endswith('：'):
        return False
    if re.match(r'^\[.*?\]\s+\S+:\s*[-—]?\s*$', stripped):
        return False
    return True


def safe_json_load(path):
    """加载JSON，容错"""
    raw = path.read_text(encoding='utf-8', errors='replace')
    raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
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


def _batch_add(collection, texts, ids, metas):
    """分批embed+写入集合"""
    total = 0
    for bi in range(0, len(texts), BATCH_SIZE):
        batch_t = texts[bi:bi+BATCH_SIZE]
        embeds = embed_texts(batch_t)
        if embeds and len(embeds) == len(batch_t):
            collection.add(
                documents=batch_t,
                embeddings=embeds,
                ids=ids[bi:bi+BATCH_SIZE],
                metadatas=metas[bi:bi+BATCH_SIZE]
            )
            total += len(embeds)
        del embeds
        gc.collect()
        time.sleep(3)  # 让ollama喘息
    return total


def _ingest_collection(col, chunks, person_name, category, id_prefix, meta_fields):
    """流式入库：逐批提取+embed+写入，避免大数据人物OOM"""
    if not chunks:
        return 0
    total = 0
    for bi in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[bi:bi+BATCH_SIZE]
        texts = [c['text'] for c in batch]
        ids = [f"{person_name}_{id_prefix}_{bi+i:03d}" for i in range(len(batch))]
        metas = []
        for c in batch:
            m = {"person": person_name, "category": category}
            for f in meta_fields:
                m[f] = str(c.get(f, ''))
            metas.append(m)
        embeds = embed_texts(texts)
        if embeds and len(embeds) == len(texts):
            col.add(documents=texts, embeddings=embeds, ids=ids, metadatas=metas)
            total += len(embeds)
        else:
            print(f"  WARN: {id_prefix} batch {bi//BATCH_SIZE} embed failed, skipping", flush=True)
        del batch, texts, embeds
        gc.collect()
        time.sleep(3)
    return total


# ============================================================
# 集合1: personas — 按领域/维度分条存储
# ============================================================
def extract_personas(pv3, person_name, category):
    """
    从personality_v3.json提取人物匹配信息。
    不做一条大汇总，按领域/维度分条避免跨学科幻觉。
    """
    chunks = []
    school = pv3.get('school', '')
    evolution = pv3.get('evolution_trajectory', '')

    def add(text, domain_label):
        if is_meaningful(text, min_len=20):
            chunks.append({
                'text': text,
                'domain': domain_label,
            })

    # 1. 基础定位（brief+school）
    brief = pv3.get('brief', '')
    if brief and school:
        add(f"[{person_name}] {brief} | 学派: {school}", 'overview')

    # 2. 每个领域单独一条
    for dom in pv3.get('domains', []):
        # 从stance_anchors找该领域相关的信念
        sa = pv3.get('stance_anchors', {})
        if not isinstance(sa, dict):
            sa = {}
        parts = [f"[{person_name}] 领域: {dom}"]
        if school:
            parts.append(f"学派: {school}")
        if sa.get('core_belief'):
            parts.append(f"核心信念: {sa['core_belief']}")
        if sa.get('methodology'):
            parts.append(f"方法论: {sa['methodology']}")
        if evolution:
            parts.append(f"演变: {evolution}")
        add(' | '.join(parts), dom)

    # 3. 思想演变单独一条（如果有实质内容）
    if evolution and is_meaningful(evolution, min_len=40):
        add(f"[{person_name}] 思想演变轨迹: {evolution}", 'evolution')

    # 4. 从logical_tracing提取关键时期/事件（按turning_point拆）
    lt = pv3.get('logical_tracing', {})
    if isinstance(lt, dict):
        for tp in lt.get('turning_points', []):
            if not isinstance(tp, dict):
                continue
            parts = []
            if tp.get('title'): parts.append(tp['title'])
            if tp.get('period'): parts.append(f"({tp['period']})")
            if tp.get('narrative'): parts.append(tp['narrative'])
            text = ' '.join(parts)
            if is_meaningful(text, min_len=30):
                domain_label = tp.get('period', 'turning_point')
                add(f"[{person_name}] 人生转折: {text}", domain_label)

    # 5. 从grounding提取关键经历
    grounding = lt.get('grounding', {}) if isinstance(lt, dict) else {}
    if isinstance(grounding, dict):
        for cat in ['embodied', 'contextual']:
            for item in grounding.get(cat, []):
                if not isinstance(item, dict):
                    continue
                ctx = item.get('context', '')
                role = item.get('role', '')
                impact = item.get('impact', '')
                text = ' | '.join(p for p in [ctx, role, impact] if p)
                if is_meaningful(text, min_len=30):
                    add(f"[{person_name}] 经历: {text}", ctx or 'experience')

    return chunks


# ============================================================
# 集合2: cognitive — 认知模式
# ============================================================
def extract_cognitive(pv3, person_name):
    """提取思维风格、推理方式、偏差、决策模式等"""
    chunks = []

    def add(text, dim):
        if is_meaningful(text):
            chunks.append({'text': text, 'dim': dim})

    cp = pv3.get('cognitive_patterns', {})
    if isinstance(cp, dict):
        add(f"[{person_name}] 思维风格: {cp.get('thinking_style', '')}", 'thinking_style')
        add(f"[{person_name}] 推理方式: {cp.get('reasoning_approach', '')}", 'reasoning')
        for b in cp.get('typical_biases', []):
            add(f"[{person_name}] 典型偏差: {b}", 'bias')
        for s in cp.get('intellectual_strengths', []):
            add(f"[{person_name}] 核心优势: {s}", 'strength')
        for b in cp.get('intellectual_blindspots', []):
            add(f"[{person_name}] 认知盲区: {b}", 'blindspot')

    # 决策模式
    dp = pv3.get('decision_patterns', {})
    if isinstance(dp, dict):
        for key, label in [('risk_tolerance','风险偏好'), ('information_processing','信息处理'),
                           ('temporal_orientation','时间取向'), ('conflict_resolution','冲突解决')]:
            if dp.get(key):
                add(f"[{person_name}] 决策-{label}: {dp[key]}", f'decision_{key}')

    # 分析框架
    for af in pv3.get('analytical_frameworks', []):
        if isinstance(af, dict) and af.get('name'):
            comps = ', '.join(af.get('components', []))
            app = af.get('application', '')
            add(f"[{person_name}] 框架「{af['name']}」: {comps} | 应用: {app}", 'framework')

    # 沟通风格
    csp = pv3.get('communication_profile', {})
    if isinstance(csp, dict):
        add(f"[{person_name}] 沟通风格: {csp.get('style', '')}", 'comm_style')
        for t in csp.get('persuasion_tactics', []):
            add(f"[{person_name}] 说服策略: {t}", 'persuasion')

    # 核心格言
    for a in pv3.get('key_aphorisms', []):
        add(f"[{person_name}] 格言: {a}", 'aphorism')

    return chunks


# ============================================================
# 集合3: claims — insights_with_evidence.claim
# ============================================================
def extract_claims(pv3, person_name, category):
    """提取具体观点，带dim和evidence_ids"""
    iwe = pv3.get('insights_with_evidence', [])
    if not iwe:
        return []

    # 先建立evidence ID映射（与ingest_evidence一致）
    chunks = []
    for i, ins in enumerate(iwe):
        if not isinstance(ins, dict):
            continue
        claim = ins.get('claim', '')
        dim = ins.get('dim', '')
        evidences = ins.get('evidence', [])
        if not claim or len(claim.strip()) < 20:
            continue

        # 收集evidence IDs
        ev_ids = []
        for ei, ev in enumerate(evidences):
            if isinstance(ev, dict) and ev.get('excerpt', ''):
                ev_ids.append(f"{person_name}_ev_{len(ev_ids):03d}")

        chunks.append({
            'text': claim,
            'dim': dim,
            'evidence_count': len(evidences),
            'evidence_ids': ','.join(ev_ids) if ev_ids else '',
        })

    return chunks


# ============================================================
# 集合4: evidence — 原文摘录
# ============================================================
def extract_evidence(pv3, person_name, category):
    """提取原文证据片段"""
    iwe = pv3.get('insights_with_evidence', [])
    if not iwe:
        return []

    chunks = []
    global_ev_idx = 0
    for i, ins in enumerate(iwe):
        if not isinstance(ins, dict):
            continue
        dim = ins.get('dim', '')
        evidences = ins.get('evidence', [])
        for ei, ev in enumerate(evidences):
            if not isinstance(ev, dict):
                continue
            excerpt = ev.get('excerpt', '')
            if not excerpt or len(excerpt.strip()) < 20:
                continue
            source_file = ev.get('source_file', '')
            location = ev.get('location', '')
            # 检测语言
            lang = 'en'
            if any('\u4e00' <= c <= '\u9fff' for c in excerpt[:100]):
                lang = 'zh'
            chunks.append({
                'text': excerpt,
                'ev_id': f"{person_name}_ev_{global_ev_idx:03d}",
                'dim': dim,
                'source_file': source_file,
                'location': location,
                'insight_idx': i,
                'language': lang,
            })
            global_ev_idx += 1

    return chunks


# ============================================================
# 集合5: tracing — 思想谱系
# ============================================================
def extract_tracing(pv3, person_name):
    """提取logical_tracing全部内容"""
    lt = pv3.get('logical_tracing', {})
    if not isinstance(lt, dict):
        return []

    chunks = []

    def add(text, dim, period=''):
        if is_meaningful(text, min_len=30):
            chunks.append({'text': text, 'dim': dim, 'period': period})

    add(f"[{person_name}] 逻辑溯源: {lt.get('summary', '')}", 'lt_summary')

    # lineage
    lineage = lt.get('lineage', {})
    if isinstance(lineage, dict):
        for cat, label in [('inherited','继承'), ('transformed','改造'), 
                           ('ruptured','断裂'), ('converged','融合')]:
            for item in lineage.get(cat, []):
                if not isinstance(item, dict):
                    continue
                parts = [item.get('source', ''), item.get('nature', ''), item.get('description', '')]
                text = ' | '.join(p for p in parts if p)
                ref = item.get('source_ref', '')
                if ref:
                    text += f" [来源: {ref}]"
                add(f"[{person_name}] 谱系-{label}: {text}", f'lineage_{cat}')

    # turning_points
    for tp in lt.get('turning_points', []):
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
        if ref:
            text += f" [来源: {ref}]"
        period = tp.get('period', '')
        add(f"[{person_name}] 转折: {text}", 'turning_point', period)

    # grounding
    grounding = lt.get('grounding', {})
    if isinstance(grounding, dict):
        for cat, label in [('embodied','肉身经历'), ('contextual','语境沉浸')]:
            for item in grounding.get(cat, []):
                if not isinstance(item, dict):
                    continue
                parts = [item.get('context', ''), item.get('role', ''), item.get('impact', '')]
                text = ' | '.join(p for p in parts if p)
                ref = item.get('source_ref', '')
                if ref:
                    text += f" [来源: {ref}]"
                add(f"[{person_name}] 扎根-{label}: {text}", f'grounding_{cat}')

    # collaborations
    for item in lt.get('collaborations', []):
        if not isinstance(item, dict):
            continue
        parts = [f"与{item.get('with','')}", item.get('nature',''), item.get('product',''), item.get('significance','')]
        text = ' | '.join(p for p in parts if p and p != "与")
        ref = item.get('source_ref', '')
        if ref:
            text += f" [来源: {ref}]"
        add(f"[{person_name}] 合作: {text}", 'collaboration')

    # renunciations
    for item in lt.get('renunciations', []):
        if not isinstance(item, dict):
            continue
        parts = [item.get('what',''), item.get('reason',''), item.get('aftermath','')]
        text = ' | '.join(p for p in parts if p)
        ref = item.get('source_ref', '')
        if ref:
            text += f" [来源: {ref}]"
        add(f"[{person_name}] 拒绝: {text}", 'renunciation')

    # adversaries
    for item in lt.get('adversaries', []):
        if not isinstance(item, dict):
            continue
        parts = [item.get('who',''), item.get('axis',''), item.get('dynamic','')]
        text = ' | '.join(p for p in parts if p)
        ref = item.get('source_ref', '')
        if ref:
            text += f" [来源: {ref}]"
        add(f"[{person_name}] 论敌: {text}", 'adversary')

    return chunks


# ============================================================
# 集合6: tension
# ============================================================
def extract_tension(pv3, person_name):
    """提取张力/矛盾"""
    chunks = []
    lt = pv3.get('logical_tracing', {})
    if not isinstance(lt, dict):
        return chunks

    te = lt.get('tension_explanation', {})
    if isinstance(te, dict) and te.get('tracing'):
        chunks.append({
            'text': f"[{person_name}] 张力溯源: {te['tracing']}",
            'dim': 'tension_tracing',
        })
    if isinstance(te, dict) and te.get('note'):
        chunks.append({
            'text': f"[{person_name}] 内在矛盾: {te['note']}",
            'dim': 'tension_note',
        })

    # stance_anchors里的key_debates也是张力来源
    sa = pv3.get('stance_anchors', {})
    if isinstance(sa, dict):
        for d in sa.get('key_debates', []):
            if is_meaningful(d, min_len=10):
                chunks.append({
                    'text': f"[{person_name}] 核心争议: {d}",
                    'dim': 'debate',
                })

    # 从insights_with_evidence中提取dim=tension的claims
    # 这是系统bug修复：之前只提取顶层字段，遗漏了insights中的tension维度
    iwe = pv3.get('insights_with_evidence', [])
    if isinstance(iwe, list):
        for ins in iwe:
            if not isinstance(ins, dict):
                continue
            if ins.get('dim', '') == 'tension':
                claim = ins.get('claim', '')
                if claim and len(claim.strip()) >= 20:
                    chunks.append({
                        'text': f"[{person_name}] 张力: {claim}",
                        'dim': 'tension_claim',
                    })

    return chunks


# ============================================================
# 主入口
# ============================================================
def run(person_name, person_dir):
    person_dir = Path(person_dir)
    pv3_path = person_dir / "personality_v3.json"
    if not pv3_path.exists():
        print(f"ERROR: {pv3_path} not found", flush=True)
        return

    category = str(person_dir.parent.relative_to(BASE_DIR)) if person_dir.parent != BASE_DIR else ""
    pv3 = safe_json_load(pv3_path)
    meta = pv3.get('_distill_meta', {})
    print(f"Ingesting {person_name}... ({meta.get('source_count','?')} sources)", flush=True)

    os.makedirs(DB_PATH, exist_ok=True)
    client = chromadb.PersistentClient(path=str(DB_PATH))
    cols = {}
    for name in COLLECTION_NAMES:
        cols[name] = client.get_or_create_collection(name=name)

    # 删除该人物旧数据
    total_deleted = 0
    for col_name in COLLECTION_NAMES:
        try:
            col = cols[col_name]
            old = col.get(where={"person": person_name}, include=[])
            if old['ids']:
                col.delete(ids=old['ids'])
                total_deleted += len(old['ids'])
        except Exception:
            pass
    if total_deleted > 0:
        print(f"  删除旧数据: {total_deleted}条", flush=True)

    counts = {}

    # 1. personas
    persona_chunks = extract_personas(pv3, person_name, category)
    counts['personas'] = _ingest_collection(
        cols["personas"], persona_chunks, person_name, category,
        "persona", ["domain"])

    # 2. cognitive
    cog_chunks = extract_cognitive(pv3, person_name)
    counts['cognitive'] = _ingest_collection(
        cols["cognitive"], cog_chunks, person_name, category,
        "cog", ["dim"])

    # 3. evidence (先入库evidence，收集真实ID，供claims引用)
    ev_chunks = extract_evidence(pv3, person_name, category)
    if ev_chunks:
        # evidence用自带ev_id，需要特殊处理
        total = 0
        for bi in range(0, len(ev_chunks), BATCH_SIZE):
            batch = ev_chunks[bi:bi+BATCH_SIZE]
            texts = [c['text'] for c in batch]
            ids = [c['ev_id'] for c in batch]
            metas = []
            for c in batch:
                metas.append({
                    "person": person_name, "category": category,
                    "dim": c['dim'], "source_file": c['source_file'],
                    "location": c['location'], "language": c['language'],
                    "insight_idx": str(c['insight_idx']),
                })
            embeds = embed_texts(texts)
            if embeds and len(embeds) == len(texts):
                cols["evidence"].add(documents=texts, embeddings=embeds, ids=ids, metadatas=metas)
                total += len(embeds)
            del batch, texts, embeds
            gc.collect()
            time.sleep(3)  # ollama喘息
        counts['evidence'] = total
    else:
        counts['evidence'] = 0

    # 4. claims
    claim_chunks = extract_claims(pv3, person_name, category)
    if claim_chunks:
        total = 0
        for bi in range(0, len(claim_chunks), BATCH_SIZE):
            batch = claim_chunks[bi:bi+BATCH_SIZE]
            texts = [c['text'] for c in batch]
            ids = [f"{person_name}_claim_{bi+i:03d}" for i in range(len(batch))]
            metas = []
            for c in batch:
                metas.append({
                    "person": person_name, "category": category,
                    "dim": c['dim'], "evidence_count": str(c['evidence_count']),
                })
            embeds = embed_texts(texts)
            if embeds and len(embeds) == len(texts):
                cols["claims"].add(documents=texts, embeddings=embeds, ids=ids, metadatas=metas)
                total += len(embeds)
            del batch, texts, embeds
            gc.collect()
            time.sleep(3)  # ollama喘息
        counts['claims'] = total
    else:
        counts['claims'] = 0

    # 5. tracing
    trace_chunks = extract_tracing(pv3, person_name)
    counts['tracing'] = _ingest_collection(
        cols["tracing"], trace_chunks, person_name, category,
        "trace", ["dim", "period"])

    # 6. tension
    ten_chunks = extract_tension(pv3, person_name)
    counts['tension'] = _ingest_collection(
        cols["tension"], ten_chunks, person_name, category,
        "ten", ["dim"])

    summary = ' | '.join(f"{k}:{v}" for k, v in counts.items())
    print(f"  DONE — {summary}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 ingest_v3.py <person_name> <person_dir>")
        print("       python3 ingest_v3.py --all  # 入库全部")
        sys.exit(0)

    if sys.argv[1] == '--all':
        base = Path("/mnt/d/data/think-tank-data/personalities")
        for group in sorted(base.iterdir()):
            if not group.is_dir(): continue
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
