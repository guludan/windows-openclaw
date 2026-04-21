#!/usr/bin/env python3
"""
博弈论专家素材批量采集脚本
Exa搜索 → Sci-Hub全文下载 → 保存到think-tank目录
"""
import os, sys, json, time, re, subprocess, textwrap
from pathlib import Path

EXA_API_KEY = "7be7a599-fa7b-4107-9ef1-ad96646156c2"
EXA_BACKUP = "68d64b2b-0c48-4fd4-a1fb-74fb71f3f4ba"
SCIHUB_MIRRORS = ["https://sci-hub.st", "https://sci-hub.ru", "https://sci-hub.se"]
BASE_DIR = Path.home() / ".openclaw/workspace/think-tank/personalities/scholars/game-theory"

# 每位专家的搜索配置
EXPERTS = {
    "John_Nash": {
        "queries": [
            "John Nash non-cooperative games equilibrium 1950",
            "John Nash bargaining problem 1950 Econometrica",
            "John Nash game theory contributions Nobel lecture",
            "Nash equilibrium applications strategic analysis",
            "John Nash two-person cooperative games",
        ],
        "dois": ["10.1016/0012-365X(79)90039-9", "10.2307/1907351", "10.1007/BF01769585"],
    },
    "John_von_Neumann": {
        "queries": [
            "John von Neumann theory of games economic behavior",
            "von Neumann minimax theorem 1928 zero-sum games",
            "von Neumann Morgenstern expected utility theory",
            "von Neumann computer and the brain",
            "von Neumann game theory foundations mathematical",
        ],
        "dois": ["10.1007/BF01448847"],
    },
    "Thomas_Schelling": {
        "queries": [
            "Thomas Schelling strategy of conflict 1960",
            "Thomas Schelling arms and influence nuclear deterrence",
            "Thomas Schelling micromotives macrobehavior segregation model",
            "Schelling focal point coordination game theory",
            "Thomas Schelling Nobel Prize lecture game theory",
        ],
        "dois": [],
    },
    "Robert_Aumann": {
        "queries": [
            "Robert Aumann agreeing to disagree 1976",
            "Robert Aumann repeated games incomplete information",
            "Aumann correlated equilibrium game theory",
            "Robert Aumann Nobel Prize lecture game theory",
            "Aumann cooperation and bounded rationality",
        ],
        "dois": ["10.1214/aos/1176343456"],
    },
    "Reinhard_Selten": {
        "queries": [
            "Reinhard Selten subgame perfect equilibrium 1965",
            "Selten trembling hand perfect equilibrium 1975",
            "Selten oligopoly game theory experiment",
            "Reinhard Selten Nobel Prize lecture",
            "Selten bounded rationality game theory",
        ],
        "dois": [],
    },
    "John_Harsanyi": {
        "queries": [
            "John Harsanyi Bayesian games incomplete information 1967",
            "Harsanyi cardinal welfare utility interpersonal comparisons",
            "Harsanyi utilitarianism rule utilitarianism game theory",
            "Harsanyi Nash bargaining solution welfare economics",
            "John Harsanyi Nobel Prize lecture game theory",
        ],
        "dois": [],
    },
    "Roger_Myerson": {
        "queries": [
            "Roger Myerson mechanism design revelation principle",
            "Myerson optimal auction design 1981",
            "Myerson game theory analysis of conflict",
            "Roger Myerson political game theory",
            "Roger Myerson Nobel Prize lecture mechanism design",
        ],
        "dois": ["10.1016/0022-0531(81)90146-9"],
    },
    "Drew_Fudenberg": {
        "queries": [
            "Drew Fudenberg game theory Tirole textbook",
            "Fudenberg Levine learning in games",
            "Fudenberg Kreps reputation and imperfect information",
            "Fudenberg dynamic games theory applications",
            "Fudenberg Tirole perfect Bayesian equilibrium",
        ],
        "dois": [],
    },
    "Ken_Binmore": {
        "queries": [
            "Ken Binmore game theory social contract",
            "Binmore natural justice evolution of social norms",
            "Binmore playing fair game theory and social contract",
            "Ken Binmore evolutionary game theory bargaining",
            "Binmore Nash bargaining evolutionary foundations",
        ],
        "dois": [],
    },
    "Avinash_Dixit": {
        "queries": [
            "Avinash Dixit thinking strategically Nalebuff",
            "Dixit Nalebuff art of strategy",
            "Dixit game theory political science applications",
            "Avinash Dixit economic theory oligopoly",
            "Dixit Stiglitz monopolistic competition 1977",
        ],
        "dois": ["10.2307/1912415"],
    },
}


def exa_search(query, num_results=10, depth=False):
    """Exa搜索，返回结果列表"""
    url = "https://api.exa.ai/search" if not depth else "https://api.exa.ai/search"
    payload = {
        "query": query,
        "num_results": num_results,
        "contents": {"text": {"maxCharacters": 15000}},
        "type": "auto" if depth else "neural",
    }
    headers = {"x-api-key": EXA_API_KEY, "Content-Type": "application/json"}
    try:
        r = subprocess.run(
            ["curl", "-s", url, "-H", f"x-api-key: {EXA_API_KEY}",
             "-H", "Content-Type: application/json", "-d", json.dumps(payload)],
            capture_output=True, text=True, timeout=30
        )
        return json.loads(r.stdout).get("results", [])
    except Exception as e:
        print(f"  Exa error: {e}")
        return []


def download_scihub(doi, output_path):
    """Sci-Hub下载论文全文"""
    for mirror in SCIHUB_MIRRORS:
        try:
            r = subprocess.run(
                ["curl", "-sL", "-A", "Mozilla/5.0", f"{mirror}/{doi}", "-o", "/tmp/schub_temp.html"],
                capture_output=True, text=True, timeout=20
            )
            content = Path("/tmp/schub_temp.html").read_text(errors="ignore")
            # 找PDF链接
            pdf_match = re.search(r'(?:src|href)=["\']([^"\']+\.pdf)["\']', content)
            if not pdf_match:
                pdf_match = re.search(r'(?:embed|iframe).*?src=["\']([^"\']+\.pdf)["\']', content)
            if pdf_match:
                pdf_url = pdf_match.group(1)
                if not pdf_url.startswith("http"):
                    pdf_url = mirror + pdf_url
                subprocess.run(
                    ["curl", "-sL", "-A", "Mozilla/5.0", pdf_url, "-o", str(output_path).replace(".txt", ".pdf")],
                    capture_output=True, text=True, timeout=60
                )
                # 转txt
                pdf_path = str(output_path).replace(".txt", ".pdf")
                if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
                    try:
                        import fitz
                        doc = fitz.open(pdf_path)
                        text = "\n".join([p.get_text() for p in doc])
                        Path(output_path).write_text(text, encoding="utf-8", errors="ignore")
                        os.remove(pdf_path)
                        return True
                    except ImportError:
                        # 用pdftotext
                        r = subprocess.run(["pdftotext", pdf_path, output_path], capture_output=True)
                        if r.returncode == 0 and os.path.exists(output_path):
                            os.remove(pdf_path)
                            return True
        except Exception as e:
            print(f"  Sci-Hub {mirror} failed: {e}")
            continue
    return False


def save_content(name, materials_dir, filename, content, min_chars=500):
    """保存内容到文件"""
    if len(content) < min_chars:
        return False
    path = materials_dir / filename
    path.write_text(content, encoding="utf-8", errors="ignore")
    print(f"  ✓ {filename} ({len(content)} chars)")
    return True


def collect_expert(name, config):
    """采集单个专家的素材"""
    materials_dir = BASE_DIR / name / "materials"
    materials_dir.mkdir(parents=True, exist_ok=True)
    
    existing = set(f.name for f in materials_dir.glob("*.txt"))
    count = 0
    api_key = EXA_API_KEY
    
    # Phase 1: Exa搜索
    print(f"\n{'='*60}\n{name} — Exa搜索")
    for i, query in enumerate(config["queries"]):
        results = exa_search(query, num_results=5)
        if not results:
            # 切换key重试
            old_key = EXA_API_KEY
            api_key = EXA_BACKUP
            results = exa_search(query, num_results=5)
            api_key = old_key
        
        for r in results:
            if not r.get("text"):
                continue
            title = re.sub(r'[^\w\s-]', '', r.get("title", f"result_{i}"))[:80].strip()
            title = title.replace(" ", "_")
            filename = f"exa2_{i}_{title}.txt"
            if filename in existing:
                continue
            if save_content(name, materials_dir, filename, r["text"]):
                existing.add(filename)
                count += 1
        time.sleep(0.5)  # 避免rate limit
    
    # Phase 2: Exa深度搜索（找长文/著作节选）
    print(f"{name} — Exa深度搜索")
    for i, query in enumerate(config["queries"][:3]):
        payload = {
            "query": query + " full text PDF paper",
            "num_results": 5,
            "contents": {"text": {"maxCharacters": 25000}},
            "type": "auto",
        }
        try:
            r = subprocess.run(
                ["curl", "-s", "https://api.exa.ai/search",
                 "-H", f"x-api-key: {api_key}", "-H", "Content-Type: application/json",
                 "-d", json.dumps(payload)],
                capture_output=True, text=True, timeout=30
            )
            results = json.loads(r.stdout).get("results", [])
            for result in results:
                if not result.get("text"):
                    continue
                title = re.sub(r'[^\w\s-]', '', result.get("title", f"deep_{i}"))[:80].strip().replace(" ", "_")
                filename = f"exa2_deep_{i}_{title}.txt"
                if filename not in existing and len(result["text"]) > 1000:
                    save_content(name, materials_dir, filename, result["text"])
                    existing.add(filename)
                    count += 1
        except Exception:
            pass
        time.sleep(0.5)
    
    # Phase 3: Sci-Hub下载（如有DOI）
    if config.get("dois"):
        print(f"{name} — Sci-Hub下载")
        for doi in config["dois"]:
            filename = f"scihub_{doi.replace('/', '_')[:30]}.txt"
            if filename in existing:
                continue
            if download_scihub(doi, materials_dir / filename):
                existing.add(filename)
                count += 1
                print(f"  ✓ {filename} (full text)")
            else:
                print(f"  ✗ {filename} (download failed)")
            time.sleep(1)
    
    total = len(list(materials_dir.glob("*.txt")))
    print(f"{name} — 新增{count}篇, 总计{total}篇")
    return count, total


if __name__ == "__main__":
    os.makedirs(BASE_DIR, exist_ok=True)
    total_new = 0
    grand_total = 0
    
    for name, config in EXPERTS.items():
        try:
            new, total = collect_expert(name, config)
            total_new += new
            grand_total += total
        except Exception as e:
            print(f"{name} — ERROR: {e}")
    
    print(f"\n{'='*60}")
    print(f"采集完成: 新增{total_new}篇, 10位专家总计{grand_total}篇")
