#!/usr/bin/env python3
"""将博弈论专家materials/*.txt全部入库chromadb materials集合"""
import os, json, time, re, hashlib
import chromadb, requests

DB_PATH = "/mnt/d/data/think-tank-data/_chromadb/lacan_db"
OLLAMA_URL = 'http://172.26.0.1:11434/api/embed'
OLLAMA_MODEL = 'bge-m3:latest'
BASE = os.path.expanduser("~/.openclaw/workspace/think-tank/personalities/scholars/game-theory")
CHUNK_SIZE = 1500  # 字符
CHUNK_OVERLAP = 200

EXPERTS = ["John_Nash","John_von_Neumann","Thomas_Schelling","Robert_Aumann",
           "Reinhard_Selten","John_Harsanyi","Roger_Myerson","Drew_Fudenberg",
           "Ken_Binmore","Avinash_Dixit"]

def embed_batch(texts, batch_size=8):
    """批量embed"""
    all_embs = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        for attempt in range(3):
            try:
                r = requests.post(OLLAMA_URL, json={'model':OLLAMA_MODEL,'input':batch}, timeout=60)
                embs = r.json()['embeddings']
                all_embs.extend(embs)
                break
            except:
                time.sleep(3)
        else:
            all_embs.extend([[0]*1024]*len(batch))
        time.sleep(0.3)
    return all_embs

def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """分块"""
    if len(text) <= chunk_size:
        return [text] if text.strip() else []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        # 尝试在句号/换行处断开
        if end < len(text):
            for sep in ['\n\n', '。', '.', '\n', ' ']:
                idx = text.rfind(sep, start, end)
                if idx > start + chunk_size // 2:
                    end = idx + len(sep)
                    break
        chunks.append(text[start:end])
        start = end - overlap
    return chunks

def main():
    client = chromadb.PersistentClient(path=DB_PATH)
    col = client.get_or_create_collection("materials")
    
    total_files = 0
    total_chunks = 0
    total_new = 0
    
    for name in EXPERTS:
        mat_dir = os.path.join(BASE, name, "materials")
        if not os.path.isdir(mat_dir):
            print(f"  ✗ {name}: no materials dir")
            continue
        
        files = [f for f in os.listdir(mat_dir) if f.endswith('.txt')]
        file_count = 0
        chunk_count = 0
        
        all_texts = []
        all_ids = []
        all_metas = []
        
        for fname in sorted(files):
            fpath = os.path.join(mat_dir, fname)
            text = open(fpath, encoding='utf-8', errors='ignore').read().strip()
            if len(text) < 100:
                continue
            
            chunks = chunk_text(text)
            if not chunks:
                continue
            
            for ci, chunk in enumerate(chunks):
                doc_id = f"gt_{name}_{fname}_{ci}"
                all_ids.append(doc_id)
                all_texts.append(chunk)
                all_metas.append({
                    "person": name,
                    "category": "game-theory",
                    "source_file": fname,
                    "chunk_index": ci
                })
            
            file_count += 1
            chunk_count += len(chunks)
        
        if not all_texts:
            print(f"  - {name}: {len(files)} files, no valid chunks")
            continue
        
        # batch embed + upsert
        embs = embed_batch(all_texts)
        col.upsert(ids=all_ids, documents=all_texts, embeddings=embs, metadatas=all_metas)
        
        total_files += file_count
        total_chunks += chunk_count
        total_new += len(all_texts)
        print(f"  ✓ {name}: {file_count} files → {chunk_count} chunks")
        time.sleep(0.5)
    
    print(f"\n✅ 入库完成: {total_files} files → {total_new} chunks")
    print(f"  materials集合总数: {col.count()}")

if __name__ == "__main__":
    main()
