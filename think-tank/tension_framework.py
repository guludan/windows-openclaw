#!/usr/bin/env python3
"""
tension_framework.py - 统一二元张力框架 v2

统一命名范式：
  think-tank/{category}/{group}/{person}/
    ├── meta.json
    ├── personality.json        (v1旧版，保留)
    └── personality_v2.json     (v2当前版)

向量库：
  think-tank/_chromadb/
    ├── designers/              (设计师ChromaDB)
    └── lacan_db/               (联参+学者 Lacan四维)
      集合: unconscious, repetition, transference, drive, core_profile, tension

张力维度：
  设计师: art_score × commerce_score → 艺术 vs 商业
  联参/学者: intuition_score × logic_score → 信念 vs 理性
"""
import json, glob, time, requests as req
from pathlib import Path

THINK_TANK = Path(__file__).parent
CHROMA_DIR = THINK_TANK / "_chromadb"

SF_KEY = "sk-tnaasdpeozarapwsxndsewyobdlvmwauwoemzmwcvltbapmj"
SF_URL = "https://api.siliconflow.cn/v1/embeddings"
EMBED_MODEL = "Qwen/Qwen3-Embedding-8B"

# ─── Embedding ───
def embed(text):
    for a in range(3):
        try:
            r = req.post(SF_URL, headers={"Authorization": f"Bearer {SF_KEY}"},
                json={"model": EMBED_MODEL, "input": text[:2000]}, timeout=30)
            if r.status_code == 200:
                return r.json()["data"][0]["embedding"]
            elif r.status_code == 429:
                time.sleep(10)
            else:
                time.sleep(3)
        except:
            time.sleep(3)
    return None

# ─── 统一查找 personality_v2.json ───
def find_all_personality_v2(category):
    """在 category 目录下递归查找所有 personality_v2.json"""
    cat_dir = THINK_TANK / category
    results = []
    for p in sorted(cat_dir.rglob("personality_v2.json")):
        person_name = p.parent.name
        results.append((person_name, p))
    return results

# ─── 设计师张力评分 ───
def score_designer_tension(pv, visual_items):
    pv_str = json.dumps(pv, ensure_ascii=False)
    
    art = 5
    art_kw = {
        '完美主义': 2, '工匠': 2, '手工': 2, '匠人': 2, '艺术': 1.5,
        '诗意': 2, '哲学': 1, '反叛': 1.5, '挑衅': 1, '实验': 1,
        '概念': 1, '情感': 1, '自然': 0.5, '有机': 0.5, '侘寂': 2,
        '先锋': 1.5, 'avant-garde': 1.5, '解构': 1.5, '直觉': 1,
        '本能': 1, '灵魂': 1.5, '精神': 1, '纯粹': 1.5, '抵抗': 1,
        '独立': 1, '刺绣': 0.5, 'saddle': 1, '植鞣': 0.5,
    }
    for kw, w in art_kw.items():
        if kw in pv_str:
            art += w
    # 视觉分析加分
    for item in visual_items:
        vs = json.dumps(item, ensure_ascii=False)
        for kw in ['手工缝', '封边', '一片式', '侘寂', '手工']:
            if kw in vs: art += 0.5
    
    commerce = 5
    com_kw = {
        '商业': 2, '市场': 1.5, '品牌': 1, '扩张': 1.5, '销售': 2,
        '利润': 2, '增长': 1, '消费者': 1.5, '零售': 1, '渠道': 1,
        '大众': 1, '量产': 1.5, 'logo': 1, '爆款': 1.5, '趋势': 1,
        '快时尚': 2, '收购': 1.5, '集团': 1, '上市': 1.5, '数字化': 1,
        '电商': 1, '联名': 0.5, '合作': 0.5,
    }
    for kw, w in com_kw.items():
        if kw in pv_str:
            commerce += w
    
    art = max(1, min(10, round(art)))
    commerce = max(1, min(10, round(commerce)))
    diff = abs(art - commerce)
    
    if art >= 7 and commerce >= 7: ttype = "融合大师"
    elif art >= 7 and commerce <= 4: ttype = "纯粹艺术家"
    elif commerce >= 7 and art <= 4: ttype = "商业设计师"
    elif diff >= 4: ttype = "挣扎者"
    elif art > commerce: ttype = "艺术偏重型"
    else: ttype = "商业偏重型"
    
    return {"art_score": art, "commerce_score": commerce,
            "tension_type": ttype, "tension_magnitude": diff,
            "tension_label": "高" if diff >= 4 else "中" if diff >= 2 else "低"}

# ─── 联参/学者张力评分 ───
def score_thinker_tension(pv):
    pv_str = json.dumps(pv, ensure_ascii=False)
    
    intuition = 5
    int_kw = {
        '信仰': 2, '信念': 2, '主义': 1.5, '理想': 1.5, '使命': 2,
        '牺牲': 1.5, '忠诚': 1.5, '民族': 1, '精神': 1, '灵魂': 1,
        '传统': 1, '爱国': 2, '民族主义': 2, '意识形态': 1.5,
        '坚定': 1, '不屈': 1.5, '意志': 1, '革命': 1, '正义': 1, '道德': 1,
    }
    for kw, w in int_kw.items():
        if kw in pv_str: intuition += w
    
    logic = 5
    log_kw = {
        '数据': 2, '量化': 2, '统计': 1.5, '模型': 1.5, '实证': 2,
        '证据': 1, '推理': 1.5, '分析': 1, '计算': 1.5, '精确': 1,
        '逻辑': 1.5, '理性': 1.5, '客观': 1.5, '科学': 1.5, '方法': 1,
        '系统': 1, '规律': 1, '务实': 1, '实用': 1, '实事求是': 2,
        '谨慎': 0.5, '精细': 1, '推演': 1,
    }
    for kw, w in log_kw.items():
        if kw in pv_str: logic += w
    
    intuition = max(1, min(10, round(intuition)))
    logic = max(1, min(10, round(logic)))
    diff = abs(intuition - logic)
    
    if intuition >= 7 and logic >= 7: ttype = "辩证融合型"
    elif intuition >= 7 and logic <= 4: ttype = "信念优先型"
    elif logic >= 7 and intuition <= 4: ttype = "数据说服型"
    elif diff >= 4: ttype = "矛盾冲突型"
    elif intuition > logic: ttype = "直觉偏重型"
    else: ttype = "逻辑偏重型"
    
    return {"intuition_score": intuition, "logic_score": logic,
            "tension_type": ttype, "tension_magnitude": diff,
            "tension_label": "高" if diff >= 4 else "中" if diff >= 2 else "低"}

# ─── 提取Lacan相关字段 ───
def extract_context(pv_str, keywords, max_items=3):
    results = []
    for kw in keywords:
        idx = pv_str.find(kw)
        if idx != -1:
            ctx = pv_str[max(0, idx-20):idx+100].strip()
            results.append(ctx)
            if len(results) >= max_items:
                break
    return results if results else ["未提取到"]

# ─── 1. 设计师：重建ChromaDB ───
def build_designers():
    import chromadb
    
    designers_dir = THINK_TANK / "designers"
    va_path = designers_dir / "product_visual_analysis.json"
    visual_data = json.load(open(va_path)) if va_path.exists() else {}
    
    catalog = (designers_dir / "PRODUCT_CATALOG.md").read_text() if (designers_dir / "PRODUCT_CATALOG.md").exists() else ""
    papers = (designers_dir / "ACADEMIC_PAPERS.md").read_text() if (designers_dir / "ACADEMIC_PAPERS.md").exists() else ""
    
    persons = find_all_personality_v2("designers")
    print(f"设计师: 找到 {len(persons)} 人")
    
    docs, metas, ids, embeds = [], [], [], []
    
    for name, pv2_path in persons:
        pv = json.load(open(pv2_path))
        pv_str = json.dumps(pv, ensure_ascii=False)
        va_items = visual_data.get(name, [])
        tension = score_designer_tension(pv, va_items)
        
        # Lacan四维适配版
        lacan = {
            "unconscious": extract_context(pv_str, ["审美偏好", "设计哲学", "核心信念", "美学"]),
            "repetition": extract_context(pv_str, ["标志性", "签名", "一贯", "始终", "坚持"]),
            "transference": extract_context(pv_str, ["影响", "致敬", "师从", "对话", "反叛"]),
            "drive": extract_context(pv_str, ["驱动力", "动机", "使命", "追求", "执念"]),
        }
        
        # Build doc
        doc = f"[{name}] 人格档案\n{pv_str[:1500]}\n\n"
        doc += f"[张力] art={tension['art_score']} commerce={tension['commerce_score']} {tension['tension_type']}\n"
        for dim, ctx in lacan.items():
            doc += f"[Lacan-{dim}] {json.dumps(ctx, ensure_ascii=False)}\n"
        for i, va in enumerate(va_items):
            doc += f"\n[视觉-{i}] {json.dumps(va, ensure_ascii=False)}\n"
        
        docs.append(doc[:3000])
        metas.append({
            "source": "personality", "brand": name,
            "art_score": tension["art_score"], "commerce_score": tension["commerce_score"],
            "tension_type": tension["tension_type"],
            "tension_magnitude": tension["tension_magnitude"],
            "tension_label": tension["tension_label"],
        })
        ids.append(f"p_{name}")
    
    # References
    docs.append(f"[产品目录] {catalog}")
    metas.append({"source": "catalog", "art_score": 0, "commerce_score": 0, "tension_type": "ref", "tension_magnitude": 0, "tension_label": "无"})
    ids.append("catalog")
    docs.append(f"[学术论文] {papers}")
    metas.append({"source": "papers", "art_score": 0, "commerce_score": 0, "tension_type": "ref", "tension_magnitude": 0, "tension_label": "无"})
    ids.append("papers")
    
    # Embed
    print(f"Embedding {len(docs)} 条...")
    for i, doc in enumerate(docs):
        emb = embed(doc)
        embeds.append(emb if emb else [0.0] * 1024)
        time.sleep(1)
        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(docs)}]")
    
    # Rebuild ChromaDB
    db_path = CHROMA_DIR / "designers"
    client = chromadb.PersistentClient(path=str(db_path))
    try:
        client.delete_collection("designers")
    except:
        pass
    coll = client.get_or_create_collection("designers", metadata={"hnsw:space": "cosine"})
    coll.upsert(documents=docs, metadatas=metas, ids=ids, embeddings=embeds)
    print(f"设计师ChromaDB: {coll.count()} 条")
    
    # Collect results
    results = {}
    for name, pv2_path in persons:
        pv = json.load(open(pv2_path))
        tension = score_designer_tension(pv, visual_data.get(name, []))
        results[name] = tension
    return results

# ─── 2/3. 联参+学者：回溯张力 ───
def backfill_thinkers():
    import chromadb
    
    db_path = CHROMA_DIR / "lacan_db"
    client = chromadb.PersistentClient(path=str(db_path))
    
    results = {}
    
    for category in ["joint-staff", "scholars"]:
        persons = find_all_personality_v2(category)
        print(f"{category}: 找到 {len(persons)} 人")
        
        for name, pv2_path in persons:
            pv = json.load(open(pv2_path))
            tension = score_thinker_tension(pv)
            results[f"{category}/{name}"] = tension
            
            # Add to tension collection
            try:
                tension_col = client.get_or_create_collection("tension", metadata={"hnsw:space": "cosine"})
            except:
                tension_col = client.get_collection("tension")
            
            t_text = (f"[{name}] 张力: 直觉={tension['intuition_score']} 逻辑={tension['logic_score']} "
                     f"类型={tension['tension_type']} 张力={tension['tension_label']}:{tension['tension_magnitude']}\n"
                     f"{json.dumps(pv, ensure_ascii=False)[:1500]}")
            t_emb = embed(t_text)
            
            tension_col.upsert(
                ids=[f"{name}:tension"],
                embeddings=[t_emb if t_emb else [0.0] * 1024],
                documents=[t_text[:2000]],
                metadatas=[{"person": name, "category": category, **tension}]
            )
            time.sleep(0.5)
    
    # Print collection stats
    try:
        tension_col = client.get_collection("tension")
        print(f"tension集合: {tension_col.count()} 条")
    except:
        print("tension集合: 创建失败")
    
    return results

# ─── Main ───
def main():
    print("=" * 60)
    print("统一命名范式 + 二元张力框架")
    print("=" * 60)
    
    print(f"\n向量库目录: {CHROMA_DIR}")
    print(f"  designers/: {list((CHROMA_DIR / 'designers').glob('*'))}")
    print(f"  lacan_db/: {[c.name for c in __import__('chromadb').PersistentClient(path=str(CHROMA_DIR / 'lacan_db')).list_collections()]}")
    
    print(f"\n{'='*60}")
    print("1/3 设计师: 张力评分 + 重建ChromaDB")
    print(f"{'='*60}")
    d_results = build_designers()
    
    sorted_d = sorted(d_results.items(), key=lambda x: x[1]["tension_magnitude"], reverse=True)
    print("\n设计师张力分布:")
    for name, t in sorted_d:
        bar_art = "🟣" * t["art_score"]
        bar_com = "🟢" * t["commerce_score"]
        print(f"  {name:20s} art={t['art_score']} {bar_art} | comm={t['commerce_score']} {bar_com} | {t['tension_type']} (张力{t['tension_label']})")
    
    print(f"\n{'='*60}")
    print("2/3+3/3 联参+学者: 张力回溯")
    print(f"{'='*60}")
    t_results = backfill_thinkers()
    
    for cat in ["joint-staff", "scholars"]:
        items = {k.split("/", 1)[1]: v for k, v in t_results.items() if k.startswith(cat)}
        sorted_items = sorted(items.items(), key=lambda x: x[1]["tension_magnitude"], reverse=True)
        print(f"\n{cat} 张力分布:")
        for name, t in sorted_items:
            bar_int = "🔴" * t["intuition_score"]
            bar_log = "🔵" * t["logic_score"]
            print(f"  {name:12s} int={t['intuition_score']} {bar_int} | log={t['logic_score']} {bar_log} | {t['tension_type']} (张力{t['tension_label']})")
    
    # Save
    out = THINK_TANK / "tension_analysis.json"
    all_results = {
        "designers": d_results,
        "joint_staff": {k.split("/", 1)[1]: v for k, v in t_results.items() if k.startswith("joint-staff")},
        "scholars": {k.split("/", 1)[1]: v for k, v in t_results.items() if k.startswith("scholars")},
        "schema_version": "v2",
        "naming_convention": "think-tank/{category}/{group}/{person}/{meta.json, personality_v2.json}",
        "chroma_path": "_chromadb/{designers, lacan_db}",
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    
    total = len(d_results) + len(t_results)
    print(f"\n✅ 完成: {total} 人, 结果 -> {out}")

if __name__ == "__main__":
    main()
