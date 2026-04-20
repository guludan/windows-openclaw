#!/usr/bin/env python3
"""批量入库所有人格的materials（后台自动跑）"""
import subprocess, time, os, json

BASE = "/mnt/d/data/think-tank-data/personalities"
INGEST_SCRIPT = "/home/tearsyu/.openclaw/workspace/scripts/ingest_v3.py"
PYTHON = "/home/tearsyu/miniconda3/envs/tt/bin/python3"
LOG = "/mnt/d/data/think-tank-data/ingest_batch.log"

def get_all_persons():
    """扫描所有有人格v3.json的目录"""
    persons = []
    # Military (flat)
    for d in os.listdir(f"{BASE}/military"):
        p = f"{BASE}/military/{d}"
        if os.path.isdir(p) and os.path.exists(f"{p}/personality_v3.json"):
            persons.append((d, p))
    # Scholars (nested)
    for cat in os.listdir(f"{BASE}/scholars"):
        cat_path = f"{BASE}/scholars/{cat}"
        if not os.path.isdir(cat_path): continue
        for d in os.listdir(cat_path):
            p = f"{cat_path}/{d}"
            if os.path.isdir(p) and os.path.exists(f"{p}/personality_v3.json"):
                persons.append((d, p))
    # Psychologists (nested)
    for cat in os.listdir(f"{BASE}/psychologists"):
        cat_path = f"{BASE}/psychologists/{cat}"
        if not os.path.isdir(cat_path): continue
        for d in os.listdir(cat_path):
            p = f"{cat_path}/{d}"
            if os.path.isdir(p) and os.path.exists(f"{p}/personality_v3.json"):
                persons.append((d, p))
    return persons

def main():
    persons = get_all_persons()
    total = len(persons)
    print(f"共 {total} 人待入库", flush=True)
    
    results = {"success": [], "fail": [], "skip": []}
    
    for i, (name, path) in enumerate(persons):
        # 检查materials是否已有数据
        try:
            import chromadb
            client = chromadb.PersistentClient(path="/mnt/d/data/think-tank-data/_chromadb/lacan_db")
            mat_col = client.get_collection("materials")
            existing = mat_col.get(where={"person": name}, limit=1)
            if existing["ids"]:
                # 检查素材文件数是否匹配
                mat_count = len([f for f in os.listdir(f"{path}/materials") if f.endswith(".txt")])
                if mat_count > 0:
                    print(f"[{i+1}/{total}] SKIP {name} (已有数据)", flush=True)
                    results["skip"].append(name)
                    continue
        except:
            pass
        
        print(f"[{i+1}/{total}] 入库 {name}...", flush=True)
        try:
            result = subprocess.run(
                [PYTHON, INGEST_SCRIPT, name, path],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                results["success"].append(name)
                print(f"  ✅ OK", flush=True)
            else:
                results["fail"].append(name)
                err = result.stderr[-200:] if result.stderr else result.stdout[-200:]
                print(f"  ❌ FAIL: {err}", flush=True)
        except subprocess.TimeoutExpired:
            results["fail"].append(name)
            print(f"  ❌ TIMEOUT", flush=True)
        
        # 间隔5秒，避免Ollama过载
        time.sleep(5)
    
    # 汇总
    print(f"\n{'='*40}", flush=True)
    print(f"成功: {len(results['success'])}", flush=True)
    print(f"跳过: {len(results['skip'])}", flush=True)
    print(f"失败: {len(results['fail'])}", flush=True)
    if results['fail']:
        print(f"失败列表: {results['fail']}", flush=True)
    
    # 保存结果
    with open(LOG, "w") as f:
        f.write(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"日志: {LOG}", flush=True)

if __name__ == "__main__":
    main()
