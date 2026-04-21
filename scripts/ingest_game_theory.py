#!/usr/bin/env python3
"""博弈论专家向量入库"""
import json, os, sys, time
import chromadb
import requests

DB_PATH = "/mnt/d/data/think-tank-data/_chromadb/lacan_db"
OLLAMA_URL = 'http://172.26.0.1:11434/api/embed'
OLLAMA_MODEL = 'bge-m3:latest'
BASE = os.path.expanduser("~/.openclaw/workspace/think-tank/personalities/scholars/game-theory")

EXPERTS = ["John_Nash","John_von_Neumann","Thomas_Schelling","Robert_Aumann",
           "Reinhard_Selten","John_Harsanyi","Roger_Myerson","Drew_Fudenberg",
           "Ken_Binmore","Avinash_Dixit"]

COLLECTIONS = ["core_profile","insights","unconscious","repetition","transference","drive","tension"]

def embed(texts):
    for attempt in range(3):
        try:
            r = requests.post(OLLAMA_URL, json={'model':OLLAMA_MODEL,'input':texts}, timeout=60)
            return r.json()['embeddings']
        except:
            time.sleep(2)
    return None

def texts_from_profile(name, data):
    """从personality_v3.json提取各集合文本"""
    texts = {c: [] for c in COLLECTIONS}
    meta = {"person": name, "category": "game-theory"}
    
    ci = data.get("core_identity", {})
    sa = data.get("stance_anchors", [])
    dp = data.get("decision_patterns", [])
    ka = data.get("key_aphorisms", [])
    cp = data.get("cognitive_patterns", [])
    af = data.get("analytical_frameworks", [])
    lt = data.get("logical_tracing", {})
    comms = data.get("communication_profile", {})
    
    # core_profile
    sa_str = json.dumps(sa, ensure_ascii=False)[:200] if isinstance(sa, dict) else '; '.join(str(x) for x in sa[:3])
    dp_str = json.dumps(dp, ensure_ascii=False)[:200] if isinstance(dp, dict) else json.dumps(dp[:3], ensure_ascii=False)
    cp_text = f"{name}: {json.dumps(ci, ensure_ascii=False)}\n立场: {sa_str}\n决策模式: {dp_str}\n名言: {'; '.join(str(x) for x in ka[:3])}"
    texts["core_profile"].append({"text": cp_text, "meta": {**meta, "type": "core"}})
    
    # insights - 每个认知模式/框架一条
    # insights - cognitive_patterns and analytical_frameworks
    if isinstance(cp, dict):
        for k, v in list(cp.items())[:5]:
            texts["insights"].append({"text": f"{name}认知模式-{k}: {json.dumps(v, ensure_ascii=False)[:300]}", "meta": {**meta, "type": "cognitive"}})
    else:
        for item in cp[:5]:
            texts["insights"].append({"text": f"{name}认知模式: {json.dumps(item, ensure_ascii=False)}", "meta": {**meta, "type": "cognitive"}})
    
    # logical_tracing各维度
    lineage = lt.get("lineage", {})
    for ltype in ["inherited","transformed","ruptured","converged"]:
        items = lineage.get(ltype, [])
        if items:
            t = f"{name}思想谱系-{ltype}: " + "; ".join(str(x) for x in items[:3])
            texts["unconscious"].append({"text": t, "meta": {**meta, "type": "lineage", "subtype": ltype}})
    
    for tp in lt.get("turning_points", [])[:3]:
        texts["drive"].append({"text": f"{name}转折点: {tp.get('title','')} - {tp.get('narrative','')[:200]}", "meta": {**meta, "type": "turning_point"}})
    
    for g_type in ["embodied","contextual"]:
        items = lt.get("grounding", {}).get(g_type, [])
        if items:
            t = f"{name}扎根经验-{g_type}: " + "; ".join(str(x)[:100] for x in items[:3])
            texts["repetition"].append({"text": t, "meta": {**meta, "type": "grounding", "subtype": g_type}})
    
    for col in lt.get("collaborations", [])[:3]:
        texts["transference"].append({"text": f"{name}合作: {col.get('with','')} ({col.get('nature','')})", "meta": {**meta, "type": "collaboration"}})
    
    for adv in lt.get("adversaries", [])[:3]:
        texts["transference"].append({"text": f"{name}论敌: {adv.get('who','')} - {adv.get('dynamic','')[:100]}", "meta": {**meta, "type": "adversary"}})
    
    for ren in lt.get("renunciations", [])[:3]:
        texts["drive"].append({"text": f"{name}拒绝: {ren.get('what','')} - {ren.get('reason','')[:100]}", "meta": {**meta, "type": "renunciation"}})
    
    te_val = lt.get("tension_explanation", "")
    if isinstance(te_val, dict):
        te_str = te_val.get('tracing', te_val.get('note', json.dumps(te_val, ensure_ascii=False)))
    elif te_val:
        te_str = str(te_val)
    else:
        te_str = ""
    if te_str:
        texts["tension"].append({"text": f"{name}张力溯源: {te_str[:300]}", "meta": {**meta, "type": "tension_explanation"}})
    
    # 补充repetition
    if isinstance(dp, dict):
        for k, v in list(dp.items())[:3]:
            texts["repetition"].append({"text": f"{name}决策模式-{k}: {json.dumps(v, ensure_ascii=False)[:200]}", "meta": {**meta, "type": "decision"}})
    
    return texts

if __name__ == "__main__":
    client = chromadb.PersistentClient(path=DB_PATH)
    collections = {}
    for c in COLLECTIONS:
        collections[c] = client.get_or_create_collection(c)
    
    total_added = 0
    for name in EXPERTS:
        pf = os.path.join(BASE, name, "personality_v3.json")
        if not os.path.exists(pf):
            print(f"  ✗ {name}: no personality_v3.json")
            continue
        
        data = json.load(open(pf))
        texts = texts_from_profile(name, data)
        
        added = 0
        for col_name, items in texts.items():
            if not items:
                continue
            # batch embed
            batch_texts = [i["text"] for i in items]
            batch_meta = [i["meta"] for i in items]
            batch_ids = [f"{name}_{col_name}_{j}" for j in range(len(items))]
            
            embs = embed(batch_texts)
            if not embs:
                print(f"  ✗ {name}/{col_name}: embed failed")
                continue
            
            collections[col_name].upsert(
                ids=batch_ids,
                documents=batch_texts,
                embeddings=embs,
                metadatas=batch_meta
            )
            added += len(items)
            time.sleep(0.3)
        
        print(f"  ✓ {name}: +{added}条")
        total_added += added
    
    print(f"\n✅ 总计入库: {total_added}条")
    print("各集合数量:")
    for c in COLLECTIONS:
        print(f"  {c}: {collections[c].count()}")
