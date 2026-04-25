#!/usr/bin/env python3
"""
37人素材批量采集脚本 v3
主要用 OpenAlex (免费无限制) + Exa (访谈/书籍)
Semantic Scholar 当前429限流，跳过
"""
import os, sys, json, time, re, subprocess
from pathlib import Path
from urllib.parse import quote

BASE = Path("/mnt/d/data/think-tank-data/personalities")
EXA_KEY = "7be7a599-fa7b-4107-9ef1-ad96646156c2"
EXA_BACKUP = "68d64b2b-0c48-4fd4-a1fb-74fb71f3f4ba"

PEOPLE = {
    "scholars/economics": {
        "Paul_Krugman": {"query": "Paul Krugman", "exa": ["Paul Krugman New Economic Geography trade theory","Paul Krugman Nobel Prize lecture","Paul Krugman currency crisis","Paul Krugman inequality economics","Paul Krugman Conscience of a Liberal"]},
        "Joseph_Stiglitz": {"query": "Joseph Stiglitz", "exa": ["Joseph Stiglitz information asymmetry economics","Joseph Stiglitz Globalization Discontents","Joseph Stiglitz inequality market failures","Joseph Stiglitz Eurozone crisis","Joseph Stiglitz Nobel Prize lecture"]},
        "Kenneth_Rogoff": {"query": "Kenneth Rogoff", "exa": ["Kenneth Rogoff This Time Is Different financial crisis","Kenneth Rogoff exchange rate economics","Kenneth Rogoff curse of cash","Kenneth Rogoff growth debt threshold"]},
        "Milton_Friedman": {"query": "Milton Friedman", "exa": ["Milton Friedman permanent income hypothesis","Milton Friedman monetary history United States","Milton Friedman free to choose capitalism","Milton Friedman negative income tax","Milton Friedman optimal quantity of money"]},
        "John_Maynard_Keynes": {"query": "John Maynard Keynes", "exa": ["Keynes General Theory employment interest money","Keynes fiscal policy multiplier economics","Keynes Treatise on Probability","Keynes Bretton Woods monetary system","Keynes beauty contest animal spirits"]},
        "Friedrich_Hayek": {"query": "Friedrich Hayek", "exa": ["Hayek Road to Serfdom","Hayek knowledge problem price system","Hayek spontaneous order","Hayek Nobel Prize pretence of knowledge","Hayek Sensory Order psychology"]},
        "林毅夫": {"query": "Justin Yifu Lin", "exa": ["林毅夫 新结构经济学 比较优势","Justin Yifu Lin new structural economics","林毅夫 中国经济 发展战略","Justin Lin Beyond Keynesianism"]},
        "黄奇帆": {"query": "Huang Qifan economics", "exa": ["黄奇帆 重庆经济改革 地票制度","黄奇帆 结构性改革","黄奇帆 房地产税 改革","黄奇帆 金融开放 资本市场","黄奇帆 制造业 产业链"]},
    },
    "scholars/financial-engineering": {
        "Emanuel_Derman": {"query": "Emanuel Derman", "exa": ["Emanuel Derman models behaving badly","Emanuel Derman volatility smile implied tree","Emanuel Derman quantitative finance Goldman Sachs","Emanuel Derman My Life as a Quant"]},
        "Myron_Scholes": {"query": "Myron Scholes", "exa": ["Myron Scholes Black-Scholes option pricing","Myron Scholes Nobel Prize derivatives","Myron Scholes Long-Term Capital Management","Myron Scholes continuous time finance"]},
        "Fischer_Black": {"query": "Fischer Black", "exa": ["Fischer Black Black-Scholes option pricing","Fischer Black CAPM capital asset pricing","Fischer Black noise financial markets","Fischer Black Exploring General Equilibrium"]},
        "Edward_Thorp": {"query": "Edward Thorp", "exa": ["Edward Thorp Beat the Dealer card counting","Edward Thorp Beat the Market warrant","Edward Thorp Kelly criterion","Edward Thorp Man for All Markets"]},
        "Jim_Simons": {"query": "James Simons mathematics", "exa": ["Jim Simons Renaissance Technologies Medallion","James Simons Chern-Simons theory","Jim Simons Man Who Solved the Market","Jim Simons quantitative trading"]},
        "David_Shaw": {"query": "David Shaw computational finance", "exa": ["David Shaw D.E. Shaw computational finance","David Shaw high frequency trading","David Shaw Anton supercomputer molecular dynamics","David Shaw quantitative hedge fund"]},
        "Cliff_Asness": {"query": "Cliff Asness", "exa": ["Cliff Asness AQR momentum factor investing","Cliff Asness value momentum everywhere","Cliff Asness Expected Returns book","Cliff Asness liquidity risk premium"]},
        "Aaron_Brown": {"query": "Aaron Brown finance risk", "exa": ["Aaron Brown Poker Face Wall Street","Aaron Brown Red Blooded Risk","Aaron Brown risk management quantitative finance","Aaron Brown AQR risk strategy"]},
    },
    "scholars/financial-data": {
        "Marcos_Lopez_de_Prado": {"query": "Marcos Lopez de Prado", "exa": ["Marcos Lopez de Prado Advances Financial Machine Learning","Marcos Lopez de Prado hierarchical risk parity","Marcos Lopez de Prado quantitative portfolio","Marcos Lopez de Prado triple barrier labeling"]},
        "Marcos_Carreira": {"query": "Marcos Carreira derivatives", "exa": ["Marcos Carreira Brazilian Derivatives","Marcos Carreira quantitative finance Brazil","Marcos Carreira risk management derivatives"]},
        "Yves_Hilpisch": {"query": "Yves Hilpisch Python finance", "exa": ["Yves Hilpisch Python for Finance","Yves Hilpisch Derivatives Analytics Python","Yves Hilpisch Artificial Intelligence Finance","Yves Hilpisch The Python Quants"]},
        "Steve_Shreve": {"query": "Steve Shreve stochastic calculus", "exa": ["Steve Shreve Stochastic Calculus for Finance","Steven Shreve optimal investment consumption","Steve Shreve Carnegie Mellon mathematical finance"]},
    },
    "psychologists/neuropsychology": {
        "Antonio_Damasio": {"query": "Antonio Damasio", "exa": ["Antonio Damasio Descartes Error emotion reason","Antonio Damasio somatic marker hypothesis","Antonio Damasio Self Comes to Mind","Antonio Damasio Strange Order of Things homeostasis"]},
        "Joseph_LeDoux": {"query": "Joseph LeDoux", "exa": ["Joseph LeDoux Emotional Brain amygdala","Joseph LeDoux Anxious fear anxiety neuroscience","Joseph LeDoux Synaptic Self","Joseph LeDoux consciousness survival circuits"]},
        "VS_Ramachandran": {"query": "Vilayanur Ramachandran", "exa": ["Ramachandran Phantoms in the Brain","Ramachandran mirror box phantom limb","Ramachandran Tell Tale Brain","Ramachandran synesthesia neuroscience"]},
        "Oliver_Sacks": {"query": "Oliver Sacks neurology", "exa": ["Oliver Sacks Man Who Mistook Wife for Hat","Oliver Sacks Awakenings L-DOPA","Oliver Sacks Musicophilia music brain","Oliver Sacks Hallucinations"]},
    },
    "psychologists/evolutionary-psychology": {
        "Robert_Sapolsky": {"query": "Robert Sapolsky", "exa": ["Robert Sapolsky Behave biology humans","Robert Sapolsky Zebras Don't Get Ulcers stress","Robert Sapolsky testosterone behavior","Robert Sapolsky Determined free will"]},
        "Steven_Pinker": {"query": "Steven Pinker", "exa": ["Steven Pinker Better Angels of Our Nature","Steven Pinker Blank Slate human nature","Steven Pinker How the Mind Works","Steven Pinker Enlightenment Now"]},
        "David_Buss": {"query": "David Buss evolutionary psychology", "exa": ["David Buss Evolutionary Psychology textbook","David Buss sexual strategies theory","David Buss dangerous passion jealousy","David Buss murder evolutionary psychology"]},
    },
    "scholars/media-studies": {
        "Marshall_McLuhan": {"query": "Marshall McLuhan media", "exa": ["Marshall McLuhan medium is the message","Marshall McLuhan Understanding Media extensions man","Marshall McLuhan global village","Marshall McLuhan Gutenberg Galaxy"]},
        "Neil_Postman": {"query": "Neil Postman media ecology", "exa": ["Neil Postman Amusing Ourselves to Death","Neil Postman Technopoly","Neil Postman Disappearance of Childhood","Neil Postman Huxley vs Orwell"]},
        "Edward_Bernays": {"query": "Edward Bernays propaganda", "exa": ["Edward Bernays Propaganda public opinion","Edward Bernays Engineering of Consent","Edward Bernays torches of freedom","Edward Bernays Freud nephew PR"]},
        "Walter_Lippmann": {"query": "Walter Lippmann public opinion", "exa": ["Walter Lippmann Public Opinion stereotypes","Walter Lippmann phantom public democracy","Walter Lippmann manufacturing consent media","Walter Lippmann Good Society"]},
        "Eli_Pariser": {"query": "Eli Pariser filter bubble", "exa": ["Eli Pariser Filter Bubble algorithm","Eli Pariser Facebook personalization democracy","Eli Pariser MoveOn online activism","Eli Pariser new public sphere digital"]},
    },
    "scholars/actuarial-science": {
        "Harry_Markowitz": {"query": "Harry Markowitz portfolio", "exa": ["Harry Markowitz portfolio selection mean variance","Markowitz Modern Portfolio Theory","Harry Markowitz Nobel Prize lecture","Markowitz efficient frontier"]},
        "William_Sharpe": {"query": "William Sharpe CAPM", "exa": ["William Sharpe CAPM capital asset pricing","Sharpe ratio risk adjusted return","William Sharpe Investors and Markets","William Sharpe Nobel Prize lecture"]},
        "Robert_Merton": {"query": "Robert Merton finance option", "exa": ["Robert Merton continuous time finance","Robert Merton Nobel Prize option pricing","Robert Merton intertemporal CAPM","Robert Merton pension finance lifecycle"]},
        "John_Hull": {"query": "John Hull options derivatives", "exa": ["John Hull Options Futures Other Derivatives","John Hull Risk Management Financial Institutions","John Hull credit risk modeling","John Hull numerical methods derivatives"]},
        "Eugene_Fama": {"query": "Eugene Fama efficient market", "exa": ["Eugene Fama efficient market hypothesis","Fama French three factor model","Eugene Fama Nobel Prize market efficiency","Fama French five factor model"]},
    },
}


def clean_fn(s, n=80):
    s = re.sub(r'[^\w\s\-]', '', s)[:n].strip()
    return s.replace(' ', '_') or 'untitled'


def openalex_search(query, per_page=50, cursor="*"):
    url = f"https://api.openalex.org/works?search={quote(query)}&per_page={per_page}&sort=cited_by_count:desc&cursor={cursor}"
    try:
        r = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout)
    except:
        return {}


def openalex_author_search(name_query):
    """Search for author ID first"""
    url = f"https://api.openalex.org/authors?search={quote(name_query)}&per_page=3"
    try:
        r = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=20)
        data = json.loads(r.stdout)
        results = data.get("results", [])
        if results:
            return results[0].get("id")  # e.g. https://openalex.org/A5023888391
    except:
        pass
    return None


def exa_search(query, num=8, key=None):
    k = key or EXA_KEY
    payload = json.dumps({"query": query, "numResults": num, "type": "auto", "contents": {"text": {"maxCharacters": 12000}}})
    try:
        r = subprocess.run(["curl", "-s", "https://api.exa.ai/search", "-H", f"x-api-key: {k}", "-H", "Content-Type: application/json", "-d", payload], capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout).get("results", [])
    except:
        return []


def reconstruct_abstract(inverted_index):
    if not inverted_index:
        return ""
    words = []
    for word, positions in inverted_index.items():
        for pos in positions:
            words.append((pos, word))
    words.sort()
    return " ".join(w for _, w in words)


def save_openalex(materials_dir, works):
    saved = 0
    existing = {f.name for f in Path(materials_dir).glob("paper_*.txt")}
    for w in works:
        title = w.get("title", "")
        if not title:
            continue
        abstract = reconstruct_abstract(w.get("abstract_inverted_index"))
        year = w.get("publication_year", "")
        doi = (w.get("doi") or "").replace("https://doi.org/", "")
        cites = w.get("cited_by_count", 0)
        auths = ", ".join([a.get("author", {}).get("display_name", "") for a in (w.get("authorships") or [])[:5]])
        fname = f"paper_{clean_fn(title)}.txt"
        if fname in existing:
            continue
        content = f"# {title}\n# Authors: {auths}\n# Year: {year}\n# DOI: {doi}\n# Citations: {cites}\n# Source: OpenAlex\n\n{abstract}\n"
        if len(content) < 150:
            continue
        (Path(materials_dir) / fname).write_text(content, encoding="utf-8")
        existing.add(fname)
        saved += 1
    return saved


def save_exa(materials_dir, results, prefix="web"):
    saved = 0
    existing = {f.name for f in Path(materials_dir).glob("*.txt")}
    for item in results:
        text = item.get("text", "")
        title = item.get("title", "")
        url = item.get("url", "")
        if not text or len(text) < 300:
            continue
        fname = f"{prefix}_{clean_fn(title)}.txt"
        if fname in existing:
            continue
        content = f"# {title}\n# Source: {url}\n\n{text}"
        (Path(materials_dir) / fname).write_text(content, encoding="utf-8")
        existing.add(fname)
        saved += 1
    return saved


def collect_person(category, name, config):
    mdir = BASE / category / name / "materials"
    mdir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n{'='*60}\n📥 {category}/{name}")
    total = 0
    q = config["query"]
    
    # Phase 1: OpenAlex author search → get all works
    print(f"  [1/3] OpenAlex author search: {q}")
    author_id = openalex_author_search(q)
    time.sleep(1)
    
    if author_id:
        aid = author_id.split("/")[-1]
        # Page 1
        data = openalex_search(f"author.id:{aid}", per_page=100)
        works = data.get("results", [])
        saved = save_openalex(mdir, works)
        total += saved
        print(f"    → {saved} papers (author works p1)")
        time.sleep(2)
        
        # Page 2 if needed
        cursor = data.get("meta", {}).get("next_cursor")
        if cursor and saved < 50:
            data2 = openalex_search(f"author.id:{aid}", per_page=100, cursor=cursor)
            works2 = data2.get("results", [])
            s2 = save_openalex(mdir, works2)
            total += s2
            print(f"    → {s2} papers (author works p2)")
            time.sleep(2)
    else:
        # Fallback: keyword search
        print(f"    Author not found, keyword search")
        data = openalex_search(q, per_page=100)
        works = data.get("results", [])
        saved = save_openalex(mdir, works)
        total += saved
        print(f"    → {saved} papers (keyword)")
        time.sleep(2)
    
    # If still under 30, try broader keyword search
    current_count = len(list(Path(mdir).glob("paper_*.txt")))
    if current_count < 30:
        data2 = openalex_search(q, per_page=100)
        works2 = data2.get("results", [])
        s2 = save_openalex(mdir, works2)
        total += s2
        if s2:
            print(f"    → {s2} more (keyword broad)")
        time.sleep(2)
    
    # Phase 2: Exa web content
    print(f"  [2/3] Exa web content")
    ek = EXA_KEY
    for i, eq in enumerate(config.get("exa", [])):
        results = exa_search(eq, num=8, key=ek)
        if not results:
            ek = EXA_BACKUP
            results = exa_search(eq, num=8, key=ek)
        s = save_exa(mdir, results, prefix=f"exa_{i}")
        total += s
        if s:
            print(f"    → {s} web ({eq[:50]})")
        time.sleep(6)
    
    # Phase 3: Save DOI list
    dois = []
    for f in Path(mdir).glob("paper_*.txt"):
        content = f.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'# DOI: (10\.\S+)', content)
        if m:
            dois.append(m.group(1))
    if dois:
        (Path(mdir) / "_dois.txt").write_text("\n".join(dois), encoding="utf-8")
    
    # Summary
    total_files = len(list(Path(mdir).glob("*.txt")))
    print(f"  [3/3] DOIs: {len(dois)} | Total files: {total_files}")
    return total, total_files


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else None
    log = []
    
    for cat, people in PEOPLE.items():
        for name, cfg in people.items():
            if target and target not in name:
                continue
            try:
                new, tot = collect_person(cat, name, cfg)
                log.append((cat, name, new, tot))
            except Exception as e:
                print(f"  ❌ {name}: {e}")
                log.append((cat, name, 0, "ERR"))
    
    print(f"\n{'='*60}\n📊 Summary:")
    for cat, name, new, tot in log:
        print(f"  {cat}/{name}: +{new}, total={tot}")


if __name__ == "__main__":
    main()
