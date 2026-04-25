#!/usr/bin/env python3
"""
37人素材批量采集脚本 v2
- Semantic Scholar API（免费，论文元数据+DOI）
- Exa API（访谈/演讲/书籍）
- OpenAlex API（补充论文）
- Sci-Hub全文下载（逐篇，60s间隔）
- web_fetch补充（访谈/演讲全文）

API限流：每次间隔5-10s，Sci-Hub 60s+
"""
import os, sys, json, time, re, subprocess, hashlib
from pathlib import Path
from urllib.parse import quote

BASE = Path("/mnt/d/data/think-tank-data/personalities")

# ===== 人物配置 =====
PEOPLE = {
    "scholars/economics": {
        "Paul_Krugman": {
            "ss_query": "Paul Krugman",
            "exa_queries": [
                "Paul Krugman New Economic Geography trade theory",
                "Paul Krugman Nobel Prize lecture increasing returns",
                "Paul Krugman currency crisis target zones",
                "Paul Krugman New York Times columns economic analysis",
                "Paul Krugman The Conscience of a Liberal",
            ],
        },
        "Joseph_Stiglitz": {
            "ss_query": "Joseph Stiglitz",
            "exa_queries": [
                "Joseph Stiglitz information asymmetry economics",
                "Joseph Stiglitz Globalization and Its Discontents",
                "Joseph Stiglitz Nobel Prize lecture information economics",
                "Joseph Stiglitz inequality market failures",
                "Joseph Stiglitz Euro Eurozone crisis critique",
            ],
        },
        "Kenneth_Rogoff": {
            "ss_query": "Kenneth Rogoff",
            "exa_queries": [
                "Kenneth Rogoff This Time Is Different financial crisis",
                "Kenneth Rogoff exchange rate economics",
                "Kenneth Rogoff central bank independence",
                "Kenneth Rogoff curse of cash paper money",
                "Kenneth Rogoff growth debt threshold 90 percent",
            ],
        },
        "Milton_Friedman": {
            "ss_query": "Milton Friedman",
            "exa_queries": [
                "Milton Friedman permanent income hypothesis consumption",
                "Milton Friedman monetary history United States",
                "Milton Friedman free to choose capitalism freedom",
                "Milton Friedman negative income tax",
                "Milton Friedman Friedman rule optimal quantity of money",
            ],
        },
        "John_Maynard_Keynes": {
            "ss_query": "John Maynard Keynes",
            "exa_queries": [
                "Keynes General Theory employment interest money",
                "Keynesian economics fiscal policy multiplier",
                "John Maynard Keynes Treatise on Probability",
                "Keynes Bretton Woods international monetary system",
                "Keynes beauty contest animal spirits investment",
            ],
        },
        "Friedrich_Hayek": {
            "ss_query": "Friedrich Hayek",
            "exa_queries": [
                "Friedrich Hayek Road to Serfdom",
                "Hayek knowledge problem price system",
                "Hayek spontaneous order catallaxy",
                "Hayek Nobel Prize lecture pretence of knowledge",
                "Hayek Sensory Order theoretical psychology",
            ],
        },
        "林毅夫": {
            "ss_query": "Justin Yifu Lin",
            "exa_queries": [
                "林毅夫 新结构经济学 比较优势",
                "Justin Yifu Lin new structural economics",
                "林毅夫 中国经济 发展战略",
                "Justin Lin Beyond Keynesianism global crisis",
                "林毅夫 北京大学 国家发展研究院",
            ],
        },
        "黄奇帆": {
            "ss_query": "Huang Qifan",
            "exa_queries": [
                "黄奇帆 重庆经济改革 地票制度",
                "黄奇帆 结构性改革 经济分析",
                "黄奇帆 房地产税 改革思路",
                "黄奇帆 金融开放 资本市场",
                "黄奇帆 制造业 产业链 重构",
            ],
        },
    },
    "scholars/financial-engineering": {
        "Emanuel_Derman": {
            "ss_query": "Emanuel Derman",
            "exa_queries": [
                "Emanuel Derman models behaving badly",
                "Emanuel Derman volatility smile implied tree",
                "Emanuel Derman quantitative finance Goldman Sachs",
                "Emanuel Derman My Life as a Quant",
                "Emanuel Derman Black-Derman-Toy model",
            ],
        },
        "Myron_Scholes": {
            "ss_query": "Myron Scholes",
            "exa_queries": [
                "Myron Scholes Black-Scholes option pricing",
                "Myron Scholes Nobel Prize lecture derivatives",
                "Myron Scholes Long-Term Capital Management",
                "Myron Scholes tax planning option pricing",
                "Myron Scholes continuous time finance",
            ],
        },
        "Fischer_Black": {
            "ss_query": "Fischer Black",
            "exa_queries": [
                "Fischer Black Black-Scholes option pricing model",
                "Fischer Black CAPM capital asset pricing",
                "Fischer Black noise in financial markets",
                "Fischer Black Exploring General Equilibrium",
                "Fischer Black business cycles general equilibrium",
            ],
        },
        "Edward_Thorp": {
            "ss_query": "Edward Thorp",
            "exa_queries": [
                "Edward Thorp Beat the Dealer card counting",
                "Edward Thorp Beat the Market warrant hedging",
                "Edward Thorp Princeton Newport Partners",
                "Edward Thorp Kelly criterion money management",
                "Edward Thorp A Man for All Markets autobiography",
            ],
        },
        "Jim_Simons": {
            "ss_query": "James Simons",
            "exa_queries": [
                "Jim Simons Renaissance Technologies Medallion Fund",
                "James Simons mathematics Chern-Simons theory",
                "Jim Simons quantitative trading strategy",
                "Jim Simons Man Who Solved the Market",
                "James Simons pattern recognition financial markets",
            ],
        },
        "David_Shaw": {
            "ss_query": "David Shaw",
            "exa_queries": [
                "David Shaw D.E. Shaw computational finance",
                "David Shaw high frequency trading pioneer",
                "David Shaw computational biochemistry",
                "David Shaw quantitative hedge fund",
                "David Shaw Anton supercomputer molecular dynamics",
            ],
        },
        "Cliff_Asness": {
            "ss_query": "Cliff Asness",
            "exa_queries": [
                "Cliff Asness AQR momentum factor investing",
                "Cliff Asness value momentum everywhere",
                "Cliff Asness liquidity risk premium",
                "Cliff Asness Expected Returns book",
                "Cliff Asness hedge fund replication",
            ],
        },
        "Aaron_Brown": {
            "ss_query": "Aaron Brown finance risk",
            "exa_queries": [
                "Aaron Brown Poker Face of Wall Street",
                "Aaron Brown Red Blooded Risk",
                "Aaron Brown risk management quantitative finance",
                "Aaron Brown AQR risk strategy",
                "Aaron Brown financial risk management Monte Carlo",
            ],
        },
    },
    "scholars/financial-data": {
        "Marcos_Lopez_de_Prado": {
            "ss_query": "Marcos Lopez de Prado",
            "exa_queries": [
                "Marcos Lopez de Prado Advances Financial Machine Learning",
                "Marcos Lopez de Prado quantitative portfolio management",
                "Marcos Lopez de Prado hierarchical risk parity",
                "Marcos Lopez de Prado triple barrier labeling",
                "Marcos Lopez de Prado financial data science",
            ],
        },
        "Marcos_Carreira": {
            "ss_query": "Marcos Carreira",
            "exa_queries": [
                "Marcos Carreira Brazilian Derivatives",
                "Marcos Carreira quantitative finance Brazil",
                "Marcos Carreira risk management derivatives",
                "Marcos Carreira BMF Bovespa",
                "Marcos Carreira financial engineering emerging markets",
            ],
        },
        "Yves_Hilpisch": {
            "ss_query": "Yves Hilpisch",
            "exa_queries": [
                "Yves Hilpisch Python for Finance",
                "Yves Hilpisch Derivatives Analytics with Python",
                "Yves Hilpisch Artificial Intelligence in Finance",
                "Yves Hilpisch quantitative finance Python",
                "Yves Hilpisch The Python Quants",
            ],
        },
        "Steve_Shreve": {
            "ss_query": "Steve Shreve",
            "exa_queries": [
                "Steve Shreve Stochastic Calculus for Finance",
                "Steven Shreve optimal investment consumption",
                "Steve Shreve Carnegie Mellon mathematical finance",
                "Steven Shreve binomial model continuous time",
                "Steve Shreve stochastic differential equations finance",
            ],
        },
    },
    "psychologists/neuropsychology": {
        "Antonio_Damasio": {
            "ss_query": "Antonio Damasio",
            "exa_queries": [
                "Antonio Damasio Descartes Error emotion reason",
                "Antonio Damasio somatic marker hypothesis",
                "Antonio Damasio Self Comes to Mind consciousness",
                "Antonio Damasio Feeling of What Happens",
                "Antonio Damasio Strange Order of Things homeostasis",
            ],
        },
        "Joseph_LeDoux": {
            "ss_query": "Joseph LeDoux",
            "exa_queries": [
                "Joseph LeDoux Emotional Brain amygdala fear",
                "Joseph LeDoux Synaptic Self",
                "Joseph LeDoux Anxious fear anxiety neuroscience",
                "Joseph LeDoux memory emotional conditioning",
                "Joseph LeDoux consciousness survival circuits",
            ],
        },
        "VS_Ramachandran": {
            "ss_query": "Vilayanur Ramachandran",
            "exa_queries": [
                "V.S. Ramachandran Phantoms in the Brain",
                "Ramachandran mirror box phantom limb",
                "Ramachandran Tell Tale Brain neuroscience",
                "Ramachandran synesthesia brain",
                "Ramachandran emerging mind Reith lectures",
            ],
        },
        "Oliver_Sacks": {
            "ss_query": "Oliver Sacks",
            "exa_queries": [
                "Oliver Sacks Man Who Mistook His Wife for a Hat",
                "Oliver Sacks Awakenings L-DOPA",
                "Oliver Sacks Musicophilia music brain",
                "Oliver Sacks Hallucinations",
                "Oliver Sacks Gratitude autobiography",
            ],
        },
    },
    "psychologists/evolutionary-psychology": {
        "Robert_Sapolsky": {
            "ss_query": "Robert Sapolsky",
            "exa_queries": [
                "Robert Sapolsky Behave biology of humans",
                "Robert Sapolsky Why Zebras Don't Get Ulcers stress",
                "Robert Sapolsky testosterone behavior aggression",
                "Robert Sapolsky Determined free will science",
                "Robert Sapolsky Stanford neuroscience lectures",
            ],
        },
        "Steven_Pinker": {
            "ss_query": "Steven Pinker",
            "exa_queries": [
                "Steven Pinker Better Angels of Our Nature violence decline",
                "Steven Pinker Blank Slate human nature",
                "Steven Pinker How the Mind Works",
                "Steven Pinker Enlightenment Now reason science",
                "Steven Pinker language instinct cognitive science",
            ],
        },
        "David_Buss": {
            "ss_query": "David Buss",
            "exa_queries": [
                "David Buss Evolutionary Psychology textbook",
                "David Buss murder jealousy evolutionary psychology",
                "David Buss sexual strategies theory",
                "David Buss desire for mating partner preferences",
                "David Buss dangerous passion jealousy infidelity",
            ],
        },
    },
    "scholars/media-studies": {
        "Marshall_McLuhan": {
            "ss_query": "Marshall McLuhan",
            "exa_queries": [
                "Marshall McLuhan medium is the message",
                "Marshall McLuhan Understanding Media extensions of man",
                "Marshall McLuhan global village electronic age",
                "Marshall McLuhan Gutenberg Galaxy typography",
                "Marshall McLuhan hot and cool media theory",
            ],
        },
        "Neil_Postman": {
            "ss_query": "Neil Postman",
            "exa_queries": [
                "Neil Postman Amusing Ourselves to Death television",
                "Neil Postman Technopoly surrender of culture to technology",
                "Neil Postman Disappearance of Childhood",
                "Neil Postman media ecology education",
                "Neil Postman Huxley vs Orwell entertainment",
            ],
        },
        "Edward_Bernays": {
            "ss_query": "Edward Bernays",
            "exa_queries": [
                "Edward Bernays Propaganda public opinion manipulation",
                "Edward Bernays Engineering of Consent",
                "Edward Bernays Crystallizing Public Relations",
                "Edward Bernays torches of freedom campaign",
                "Edward Bernays Freud nephew public relations",
            ],
        },
        "Walter_Lippmann": {
            "ss_query": "Walter Lippmann",
            "exa_queries": [
                "Walter Lippmann Public Opinion stereotypes",
                "Walter Lippmann phantom public democracy",
                "Walter Lippmann media journalism manufacturing consent",
                "Walter Lippmann Good Society",
                "Walter Lippmann cold war foreign policy columns",
            ],
        },
        "Eli_Pariser": {
            "ss_query": "Eli Pariser",
            "exa_queries": [
                "Eli Pariser Filter Bubble algorithm personalization",
                "Eli Pariser Facebook filter bubble democracy",
                "Eli Pariser MoveOn.org online activism",
                "Eli Pariser internet privacy surveillance",
                "Eli Pariser new public sphere digital age",
            ],
        },
    },
    "scholars/actuarial-science": {
        "Harry_Markowitz": {
            "ss_query": "Harry Markowitz",
            "exa_queries": [
                "Harry Markowitz portfolio selection mean variance",
                "Markowitz Modern Portfolio Theory diversification",
                "Harry Markowitz Nobel Prize lecture portfolio theory",
                "Markowitz efficient frontier risk return",
                "Harry Markowitz sparse matrix methods",
            ],
        },
        "William_Sharpe": {
            "ss_query": "William Sharpe",
            "exa_queries": [
                "William Sharpe CAPM capital asset pricing model",
                "William Sharpe Nobel Prize lecture",
                "Sharpe ratio risk-adjusted return",
                "William Sharpe Investors and Markets portfolio theory",
                "William Sharpe gradient strategy portfolio optimization",
            ],
        },
        "Robert_Merton": {
            "ss_query": "Robert Merton finance",
            "exa_queries": [
                "Robert Merton continuous time finance",
                "Robert Merton Nobel Prize option pricing",
                "Robert Merton intertemporal CAPM",
                "Robert Merton pension finance lifecycle",
                "Robert Merton functional perspective financial intermediation",
            ],
        },
        "John_Hull": {
            "ss_query": "John Hull options futures",
            "exa_queries": [
                "John Hull Options Futures and Other Derivatives",
                "John Hull Risk Management Financial Institutions",
                "John Hull numerical methods derivatives pricing",
                "John Hull credit risk modeling",
                "John Hull Machine Learning in Finance",
            ],
        },
        "Eugene_Fama": {
            "ss_query": "Eugene Fama",
            "exa_queries": [
                "Eugene Fama efficient market hypothesis",
                "Eugene Fama three factor model size value",
                "Eugene Fama Nobel Prize lecture market efficiency",
                "Fama French five factor model",
                "Eugene Fama random walks stock prices",
            ],
        },
    },
}

# Exa API
EXA_KEY = "7be7a599-fa7b-4107-9ef1-ad96646156c2"
EXA_BACKUP = "68d64b2b-0c48-4fd4-a1fb-74fb71f3f4ba"

# SerpAPI
SERPAPI_KEY = "848c4ec87cac7776b9404f0af1aeb36fce936d1aa3aebe899664eaa43e67ccc2"


def semantic_scholar_search(query, limit=100, offset=0):
    """Semantic Scholar API - free, no key needed"""
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={quote(query)}&limit={limit}&offset={offset}&fields=title,abstract,year,externalIds,url,citationCount,authors"
    try:
        r = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
        return data.get("data", [])
    except Exception as e:
        print(f"  S2 error: {e}")
        return []


def semantic_scholar_author_papers(author_id, limit=100):
    """Get papers by author ID"""
    url = f"https://api.semanticscholar.org/graph/v1/author/{author_id}/papers?limit={limit}&fields=title,abstract,year,externalIds,url,citationCount"
    try:
        r = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
        return data.get("data", [])
    except Exception as e:
        print(f"  S2 author error: {e}")
        return []


def openalex_search(query, per_page=50):
    """OpenAlex API - free, no key needed"""
    url = f"https://api.openalex.org/works?search={quote(query)}&per_page={per_page}&sort=cited_by_count:desc"
    try:
        r = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
        return data.get("results", [])
    except Exception as e:
        print(f"  OpenAlex error: {e}")
        return []


def openalex_author_works(author_id, per_page=100):
    """Get works by OpenAlex author ID"""
    url = f"https://api.openalex.org/works?filter=author.id:{author_id}&per_page={per_page}&sort=cited_by_count:desc"
    try:
        r = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
        return data.get("results", [])
    except Exception as e:
        print(f"  OpenAlex author error: {e}")
        return []


def exa_search(query, num=10, key=None):
    """Exa search for web content"""
    k = key or EXA_KEY
    payload = json.dumps({
        "query": query,
        "numResults": num,
        "type": "auto",
        "contents": {"text": {"maxCharacters": 12000}},
    })
    try:
        r = subprocess.run(
            ["curl", "-s", "https://api.exa.ai/search",
             "-H", f"x-api-key: {k}",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=60
        )
        data = json.loads(r.stdout)
        return data.get("results", [])
    except Exception as e:
        print(f"  Exa error: {e}")
        return []


def clean_filename(s, max_len=80):
    s = re.sub(r'[^\w\s\-]', '', s)[:max_len].strip()
    return s.replace(' ', '_') if s else 'untitled'


def save_paper_metadata(materials_dir, papers):
    """Save paper metadata (title, abstract, DOI, year, citations) as txt"""
    saved = 0
    existing = set(f.name for f in Path(materials_dir).glob("paper_*.txt"))
    
    for p in papers:
        title = p.get("title", "")
        if not title:
            continue
        
        abstract = p.get("abstract", "") or ""
        year = p.get("year", "")
        citations = p.get("citationCount", 0)
        
        ext_ids = p.get("externalIds", {}) or {}
        doi = ext_ids.get("DOI", "")
        
        authors_list = p.get("authors", [])
        authors_str = ", ".join([a.get("name", "") for a in (authors_list or [])[:5]])
        
        fname = f"paper_{clean_filename(title)}.txt"
        if fname in existing:
            continue
        
        content = f"""# {title}
# Authors: {authors_str}
# Year: {year}
# DOI: {doi}
# Citations: {citations}
# Source: Semantic Scholar / OpenAlex

{abstract}
"""
        if len(content) < 200 and not abstract:
            continue
            
        fpath = Path(materials_dir) / fname
        fpath.write_text(content, encoding="utf-8")
        existing.add(fname)
        saved += 1
    
    return saved


def save_openalex_metadata(materials_dir, works):
    """Save OpenAlex work metadata"""
    saved = 0
    existing = set(f.name for f in Path(materials_dir).glob("paper_*.txt"))
    
    for w in works:
        title = w.get("title", "")
        if not title:
            continue
        
        abstract_inverted = w.get("abstract_inverted_index")
        abstract = ""
        if abstract_inverted:
            # Reconstruct abstract from inverted index
            word_pos = []
            for word, positions in abstract_inverted.items():
                for pos in positions:
                    word_pos.append((pos, word))
            word_pos.sort()
            abstract = " ".join([w for _, w in word_pos])
        
        year = w.get("publication_year", "")
        doi = (w.get("doi") or "").replace("https://doi.org/", "")
        citations = w.get("cited_by_count", 0)
        
        authorships = w.get("authorships", [])
        authors_str = ", ".join([a.get("author", {}).get("display_name", "") for a in authorships[:5]])
        
        fname = f"paper_{clean_filename(title)}.txt"
        if fname in existing:
            continue
        
        content = f"""# {title}
# Authors: {authors_str}
# Year: {year}
# DOI: {doi}
# Citations: {citations}
# Source: OpenAlex

{abstract}
"""
        if len(content) < 200 and not abstract:
            continue
        
        fpath = Path(materials_dir) / fname
        fpath.write_text(content, encoding="utf-8")
        existing.add(fname)
        saved += 1
    
    return saved


def save_exa_content(materials_dir, results, prefix="web"):
    """Save Exa search results"""
    saved = 0
    existing = set(f.name for f in Path(materials_dir).glob("*.txt"))
    
    for i, item in enumerate(results):
        text = item.get("text", "")
        title = item.get("title", "")
        url = item.get("url", "")
        
        if not text or len(text) < 300:
            continue
        
        fname = f"{prefix}_{clean_filename(title)}.txt"
        if fname in existing:
            continue
        
        content = f"# {title}\n# Source: {url}\n\n{text}"
        fpath = Path(materials_dir) / fname
        fpath.write_text(content, encoding="utf-8")
        existing.add(fname)
        saved += 1
    
    return saved


def collect_person(category, name, config):
    """Collect materials for one person"""
    materials_dir = BASE / category / name / "materials"
    materials_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n{'='*60}")
    print(f"📥 {category}/{name}")
    
    total_saved = 0
    ss_query = config["ss_query"]
    
    # ===== Phase 1: Semantic Scholar - paper metadata =====
    print(f"  [1/4] Semantic Scholar: {ss_query}")
    papers = semantic_scholar_search(ss_query, limit=100)
    saved = save_paper_metadata(materials_dir, papers)
    total_saved += saved
    print(f"    → {saved} papers (search)")
    time.sleep(3)
    
    # If fewer than 30, try broader queries
    if saved < 30:
        # Try with more specific queries
        extra_queries = [
            f"{ss_query} theory",
            f"{ss_query} model",
            f"{ss_query} analysis",
        ]
        for eq in extra_queries:
            more = semantic_scholar_search(eq, limit=50)
            s2 = save_paper_metadata(materials_dir, more)
            total_saved += s2
            print(f"    → {s2} more papers ({eq[:40]})")
            time.sleep(3)
            if saved + s2 >= 30:
                break
    
    # ===== Phase 2: OpenAlex - additional papers =====
    print(f"  [2/4] OpenAlex: {ss_query}")
    works = openalex_search(ss_query, per_page=50)
    saved = save_openalex_metadata(materials_dir, works)
    total_saved += saved
    print(f"    → {saved} papers (OpenAlex)")
    time.sleep(2)
    
    # ===== Phase 3: Exa - interviews/speeches/books =====
    print(f"  [3/4] Exa web content")
    exa_key = EXA_KEY
    for i, q in enumerate(config.get("exa_queries", [])):
        results = exa_search(q, num=8, key=exa_key)
        if not results:
            exa_key = EXA_BACKUP
            results = exa_search(q, num=8, key=exa_key)
        s = save_exa_content(materials_dir, results, prefix=f"exa_{i}")
        total_saved += s
        if s > 0:
            print(f"    → {s} web ({q[:50]})")
        time.sleep(6)  # 5-10s between Exa calls
    
    # ===== Phase 4: DOIs collected → try full text =====
    dois_found = []
    for f in Path(materials_dir).glob("paper_*.txt"):
        content = f.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'# DOI: (10\.\S+)', content)
        if m:
            dois_found.append(m.group(1))
    
    if dois_found:
        print(f"  [4/4] Found {len(dois_found)} DOIs (not downloading full text in batch - use batch_game_theory_collect.py separately)")
        # Save DOI list for later batch download
        doi_file = Path(materials_dir) / "_dois.txt"
        doi_file.write_text("\n".join(dois_found), encoding="utf-8")
    
    # Summary
    total_files = len(list(Path(materials_dir).glob("*.txt")))
    print(f"  ✅ {name}: +{total_saved} files, total {total_files}")
    return total_saved, total_files


def main():
    total_new = 0
    total_all = 0
    results_log = []
    
    # Optionally limit to specific person via CLI arg
    target = sys.argv[1] if len(sys.argv) > 1 else None
    
    for category, people in PEOPLE.items():
        for name, config in people.items():
            if target and target not in name:
                continue
            try:
                new, total = collect_person(category, name, config)
                total_new += new
                total_all += total
                results_log.append((category, name, new, total))
            except Exception as e:
                print(f"  ❌ {name}: ERROR - {e}")
                results_log.append((category, name, 0, "ERROR"))
    
    print(f"\n{'='*60}")
    print(f"📊 总计: +{total_new} files, {total_all} total")
    print(f"\n各人统计:")
    for cat, name, new, total in results_log:
        print(f"  {cat}/{name}: +{new}, total={total}")


if __name__ == "__main__":
    main()
