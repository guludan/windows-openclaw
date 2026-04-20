#!/usr/bin/env python3
"""批量重建旧学者materials向量库（不重建core_profile/insights）"""
import sys, os, gc, time, json
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import chromadb
import requests

DB_PATH = Path("/mnt/d/data/think-tank-data/_chromadb/lacan_db")
OLLAMA_URL = "http://172.26.0.1:11434/api/embed"
OLLAMA_MODEL = "bge-m3:latest"
BASE = Path("/mnt/d/data/think-tank-data/personalities")

# 已完成materials入库的7个新学者
SKIP = {"Ray_Dalio", "Hyman_Minsky", "Charles_Kindleberger", "Carmen_Reinhart",
        "John_Gaddis", "Nassim_Taleb", "William_Blum"}


def embed_texts(texts):
    if not texts:
        return []
    for attempt in range(3):
        try:
            r = requests.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "input": texts}, timeout=120)
            if r.status_code == 200:
                return r.json()["embeddings"]
            time.sleep(3)
        except Exception as e:
            print(f"  Embed err: {e}, retry {attempt+1}", flush=True)
            time.sleep(5)
    return []


def ingest_one(person_name, person_dir, mat_col):
    mat_dir = Path(person_dir) / "materials"
    if not mat_dir.exists():
        return 0

    # 删除旧
    try:
        old = mat_col.get(where={"person": person_name}, include=[])
        if old['ids']:
            mat_col.delete(ids=old['ids'])
    except: pass

    count = 0
    embed_batch, texts_batch, ids_batch, metas_batch = [], [], [], []

    for f in sorted(mat_dir.glob("*.txt")):
        if f.stat().st_size < 500:
            continue
        try:
            content = f.read_text(encoding='utf-8', errors='ignore')[:8000]
            chunks = [content[i:i+800] for i in range(0, len(content), 800)]
            for ci, chunk in enumerate(chunks):
                text = f"[{person_name}] {f.stem}[{ci}]: {chunk}"
                texts_batch.append(text)
                ids_batch.append(f"{person_name}_mat_{f.stem}_{ci}")
                metas_batch.append({"person": person_name, "source": f.name, "chunk": ci})
                embed_batch.append(text)
                count += 1

                if len(embed_batch) >= 8:
                    embeds = embed_texts(embed_batch)
                    if embeds and len(embeds) == len(embed_batch):
                        mat_col.add(documents=embed_batch, embeddings=embeds,
                                   ids=ids_batch[-len(embeds):], metadatas=metas_batch[-len(embeds):])
                    embed_batch = []
                    gc.collect()
        except Exception as e:
            print(f"  Err {f.name}: {e}", flush=True)

    if embed_batch:
        embeds = embed_texts(embed_batch)
        if embeds and len(embeds) == len(embed_batch):
            mat_col.add(documents=embed_batch, embeddings=embeds,
                       ids=ids_batch[-len(embeds):], metadatas=metas_batch[-len(embeds):])

    return count


def main():
    client = chromadb.PersistentClient(path=str(DB_PATH))
    mat_col = client.get_or_create_collection(name="materials", metadata={"desc": "原始资料语义索引"})

    # 收集所有需要重建的人物
    persons = []
    for category_dir in sorted(BASE.glob("*/")):
        for person_dir in sorted(category_dir.glob("*/")):
            if not person_dir.is_dir():
                continue
            name = person_dir.name
            if name in SKIP:
                continue
            if not (person_dir / "personality_v3.json").exists():
                continue
            if not (person_dir / "materials").exists():
                continue
            persons.append((name, person_dir))

    print(f"共 {len(persons)} 人待重建materials", flush=True)
    t0 = time.time()
    total_chunks = 0

    for idx, (name, pdir) in enumerate(persons):
        print(f"[{idx+1}/{len(persons)}] {name}...", end=" ", flush=True)
        t1 = time.time()
        cnt = ingest_one(name, pdir, mat_col)
        total_chunks += cnt
        elapsed = time.time() - t1
        print(f"✅ {cnt} chunks ({elapsed:.1f}s)", flush=True)
        time.sleep(0.5)

    print(f"\n{'='*50}")
    print(f"完成: {len(persons)}人, {total_chunks} chunks, 总耗时 {time.time()-t0:.1f}s")
    print(f"materials总数: {mat_col.count()}")


if __name__ == "__main__":
    main()
