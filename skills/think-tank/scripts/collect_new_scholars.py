#!/usr/bin/env python3
"""
批量采集学者素材 via Exa API (curl subprocess)
每个学者多轮搜索，覆盖理论核心+应用场景
"""
import subprocess, json, os, time, re

EXA_KEY = "68d64b2b-0c48-4fd4-a1fb-74fb71f3f4ba"
EXA_URL = "https://api.exa.ai/search"
BASE = "/mnt/d/data/think-tank-data/personalities/scholars"

# 每个学者的搜索策略
SCHOLARS = {
    "economics/Ray_Dalio": [
        {"query": "Ray Dalio changing world order big cycle debt crisis analysis", "num": 10},
        {"query": "Ray Dalio principles for dealing with the changing world order China US", "num": 10},
        {"query": "Ray Dalio economic machine how economy works debt cycle", "num": 10},
        {"query": "Ray Dalio war timeline analysis geopolitical conflict 2024 2025", "num": 10},
    ],
    "economics/Hyman_Minsky": [
        {"query": "Hyman Minsky financial instability hypothesis explained", "num": 10},
        {"query": "Minsky moment financial crisis prediction application", "num": 10},
        {"query": "Minsky stages of investment hedge speculative Ponzi finance", "num": 8},
        {"query": "Minsky theory debt deflation Great Depression analysis", "num": 8},
    ],
    "economics/Charles_Kindleberger": [
        {"query": "Charles Kindleberger manias panics crashes financial crisis history", "num": 10},
        {"query": "Kindleberger bubble lifecycle model five stages", "num": 8},
        {"query": "Kindleberger lender of last resort international financial crisis", "num": 8},
        {"query": "Kindleberger economic hegemony stability theory", "num": 8},
    ],
    "economics/Carmen_Reinhart": [
        {"query": "Carmen Reinhart This Time Is Different eight centuries of financial folly", "num": 10},
        {"query": "Carmen Reinhart debt ceiling sovereign debt crisis thresholds", "num": 10},
        {"query": "Carmen Reinhart inflation crisis currency crisis historical patterns", "num": 8},
        {"query": "Carmen Reinhart COVID-19 debt crisis emerging markets", "num": 8},
    ],
    "ir-theory/John_Gaddis": [
        {"query": "John Lewis Gaddis grand strategy containment Cold War history", "num": 10},
        {"query": "John Lewis Gaddis On Grand Strategy book analysis", "num": 10},
        {"query": "Gaddis landscape of history surprise unpredictability foreign policy", "num": 8},
        {"query": "Gaddis US-China Cold War comparison strategy", "num": 8},
    ],
    "economics/Nassim_Taleb": [
        {"query": "Nassim Nicholas Taleb black swan antifragile skin in the game", "num": 10},
        {"query": "Taleb fragility prediction experts forecasting failure", "num": 10},
        {"query": "Taleb tail risk geopolitical conflict unpredictable events", "num": 8},
        {"query": "Taleb critique of economists risk management models", "num": 8},
    ],
    "ir-security/William_Blum": [
        {"query": "William Blum Rogue State America military interventions history", "num": 10},
        {"query": "William Blum Killing Hope CIA covert operations regime change", "num": 10},
        {"query": "William Blum America's deadliest export democracy imperialism analysis", "num": 8},
        {"query": "William Blum US foreign policy hypocrisy war profiteering", "num": 8},
    ],
}


def exa_search(query: str, num: int = 10) -> list:
    """Exa API搜索 via curl"""
    payload = json.dumps({
        "query": query,
        "numResults": num,
        "type": "auto",
        "contents": {"text": {"maxCharacters": 8000}},
    })
    
    try:
        result = subprocess.run(
            ["curl", "-s", "-X", "POST", EXA_URL,
             "-H", f"x-api-key: {EXA_KEY}",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("results", [])
        else:
            print(f"  curl error: {result.stderr[:100]}")
            return []
    except Exception as e:
        print(f"  search error: {e}")
        return []


def clean_text(text: str) -> str:
    """清理文本"""
    # 去除多余空白
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


def save_content(materials_dir: str, results: list, query: str):
    """保存搜索结果"""
    saved = 0
    for item in results:
        url = item.get("url", "")
        text = item.get("text", "")
        title = item.get("title", "")
        
        if not text or len(text) < 200:
            continue
        
        # 生成文件名
        safe_title = re.sub(r'[^\w\-_.]', '_', title[:60])
        filename = f"{safe_title}.txt"
        
        # 去重
        filepath = os.path.join(materials_dir, filename)
        if os.path.exists(filepath):
            # 追加query标记
            continue
        
        content = f"# {title}\n# Source: {url}\n# Query: {query}\n\n{text}"
        content = clean_text(content)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        saved += 1
    
    return saved


def main():
    total_saved = 0
    
    for scholar_path, queries in SCHOLARS.items():
        materials_dir = os.path.join(BASE, scholar_path, "materials")
        os.makedirs(materials_dir, exist_ok=True)
        scholar_name = scholar_path.split("/")[-1]
        
        print(f"\n{'='*60}")
        print(f"📥 {scholar_name} ({len(queries)}轮搜索)")
        
        scholar_saved = 0
        for i, q in enumerate(queries):
            query_text = q if isinstance(q, str) else q.get('query', q.get('text', ''))
            num = q.get('num', 10) if isinstance(q, dict) else 10
            print(f"  [{i+1}/{len(queries)}] {query_text[:60]}...")
            results = exa_search(query_text, num)
            saved = save_content(materials_dir, results, query_text)
            scholar_saved += saved
            print(f"    → {saved} 新文件")
            time.sleep(1.5)  # Exa限流
        
        # 统计
        existing = len([f for f in os.listdir(materials_dir) if f.endswith('.txt')])
        total_saved += scholar_saved
        print(f"  ✅ {scholar_name}: 本轮采集{scholar_saved}篇, 总计{existing}篇")
    
    print(f"\n{'='*60}")
    print(f"📊 总计新采集: {total_saved}篇")
    
    # 汇总
    print(f"\n各学者素材统计:")
    for scholar_path in SCHOLARS:
        materials_dir = os.path.join(BASE, scholar_path, "materials")
        count = len([f for f in os.listdir(materials_dir) if f.endswith('.txt')])
        print(f"  {scholar_path.split('/')[-1]}: {count}篇")


if __name__ == "__main__":
    main()
