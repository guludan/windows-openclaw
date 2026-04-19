#!/usr/bin/env python3
"""
批量拉取学术论文 metadata：用 SerpAPI Google Scholar 搜索学者论文。
用法：
  python3 batch_scholar_papers.py --group scholars --person "Mearsheimer" --max 15
  python3 batch_scholar_papers.py --group scholars --max 15
  python3 batch_scholar_papers.py --group psychologists --max 15
  python3 batch_scholar_papers.py --group all --max 15
"""
import argparse, json, os, sys, time, re
from pathlib import Path

BASE = Path(__file__).parent.parent / "personalities"
SERP_KEY = "848c4ec87cac7776b9404f0af1aeb36fce936d1aa3aebe899664eaa43e67ccc2"
# 备用: Tavily
TAVILY_KEY = "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"

import requests

def search_google_scholar(name, max_results=20, start=0):
    """Search Google Scholar via SerpAPI for author's papers."""
    url = "https://serpapi.com/search"
    params = {
        "engine": "google_scholar",
        "q": f'author:"{name}"',
        "api_key": SERP_KEY,
        "num": min(max_results, 20),
        "start": start,
        "sort": "relevance"
    }
    
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [ERROR] Scholar search failed: {e}")
        return []
    
    papers = []
    for org in data.get("organic_results", []):
        title = org.get("title", "")
        pub_info = org.get("publication_info", {}).get("summary", "")
        snippet = org.get("snippet", "")
        link = org.get("link", "")
        
        # Extract year
        year = ""
        ym = re.search(r"\b(19|20)\d{2}\b", pub_info)
        if ym:
            year = ym.group()
        
        # Extract citation count
        cited = org.get("inline_links", {}).get("cited_by", {}).get("total", 0)
        
        # Extract PDF link if available
        pdf_link = ""
        il = org.get("inline_links", {})
        for res_type in ["pdf", "html"]:
            for item in il.get(res_type, []):
                pdf_link = item.get("link", "")
                if pdf_link:
                    break
            if pdf_link:
                break
        
        papers.append({
            "title": title,
            "year": year,
            "publication_info": pub_info,
            "snippet": snippet,
            "citations": cited,
            "link": link,
            "pdf_link": pdf_link,
            "source": "google_scholar"
        })
    
    return papers


def get_all_persons(group):
    """Get all person directories for a group."""
    group_dir = BASE / group
    persons = []
    
    if group in ("psychologists", "scholars"):
        for cat_dir in sorted(group_dir.iterdir()):
            if cat_dir.is_dir() and not cat_dir.name.startswith("_"):
                for person_dir in sorted(cat_dir.iterdir()):
                    if person_dir.is_dir() and not person_dir.name.startswith("_"):
                        persons.append((group, cat_dir.name, person_dir.name, person_dir))
    elif group in ("military", "designers"):
        for person_dir in sorted(group_dir.iterdir()):
            if person_dir.is_dir() and not person_dir.name.startswith("_"):
                persons.append((group, "", person_dir.name, person_dir))
    
    return persons


# Name mapping: directory name -> searchable academic name
NAME_MAP = {
    # 学者 - IR Theory
    "John_Mearsheimer": "John Mearsheimer",
    "Graham_Allison": "Graham Allison",
    "阎学通": "Yan Xuetong",
    "金灿荣": "Jin Canrong",
    "瞿东升": "Qu Dongsheng",
    "时殷弘": "Shi Yinhong",
    "潘忠岐": "Pan Zhongqi",
    # 学者 - IR Security
    "Chomsky": "Noam Chomsky",
    "沈逸": "Shen Yi",
    "艾跃进": "Ai Yuejin",
    # 学者 - Economics
    "卢麒元": "Lu Qiyuan",
    "张维迎": "Zhang Weiying",
    "温铁军": "Wen Tiejun",
    # 学者 - Middle East
    "Fawaz_Gerges": "Fawaz Gerges",
    "Vali_Nasr": "Vali Nasr",
    "殷罡": "Yin Gang",
    "牛新春": "Niu Xinchun",
    "田文林": "Tian Wenlin",
    # 学者 - Central Asia
    "Alexander_Cooley": "Alexander Cooley",
    "Sebastien_Peyrouse": "Sebastien Peyrouse",
    "曾向红": "Zeng Xianghong",
    "胡仕胜": "Hu Shisheng",
    # 学者 - Persian/Islamic
    "Bernard_Lewis": "Bernard Lewis",
    "Hamid_Dabashi": "Hamid Dabashi",
    "Said_Amir_Arjomand": "Said Amir Arjomand",
    "殷之光": "Yin Zhiguang",
    # 学者 - Religious Studies
    "Huston_Smith": "Huston Smith",
    "Karen_Armstrong": "Karen Armstrong",
    "吴云贵": "Wu Yungui",
    "金宜久": "Jin Yijiu",
    # 心理学家 - Cognitive
    "阿莫斯·特沃斯基": "Amos Tversky",
    "乔纳森·海特": "Jonathan Haidt",
    "阿尔伯特·班杜拉": "Albert Bandura",
    "马丁·塞利格曼": "Martin Seligman",
    # 心理学家 - Behavioral
    "斯坦利·米尔格拉姆": "Stanley Milgram",
    "菲利普·津巴多": "Philip Zimbardo",
    "理查德·塞勒": "Richard Thaler",
    "丹·阿瑞利": "Dan Ariely",
    # 心理学家 - Psychoanalysis
    "R·D·莱恩": "RD Laing",
    "苏珊·福沃德": "Susan Forward",
    "卡尔·罗杰斯": "Carl Rogers",
    # 心理学家 - Social Engineering
    "古斯塔夫·勒庞": "Gustave Le Bon",
    "保罗·艾克曼": "Paul Ekman",
    "凯文·米特尼克": "Kevin Mitnick",
    "罗伯特·西奥迪尼": "Robert Cialdini",
    "克里斯·哈德纳吉": "Chris Hadnagy",
    "所罗门·阿希": "Solomon Asch",
    "唐纳德·诺曼": "Donald Norman",
    "基思·斯坦诺维奇": "Keith Stanovich",
    "米哈里·契克森米哈赖": "Mihaly Csikszentmihalyi",
    "欧文·亚隆": "Irvin Yalom",
    "维吉尼亚·萨提亚": "Virginia Satir",
    "阿尔伯特·埃利斯": "Albert Ellis",
}

def process_person(person_path, papers, dry_run=False):
    """Save paper metadata for a person."""
    materials = person_path / "materials"
    materials.mkdir(parents=True, exist_ok=True)
    
    if not papers:
        return 0
    
    meta_path = materials / "_papers_meta.json"
    existing_titles = set()
    existing = []
    
    if meta_path.exists():
        try:
            with open(meta_path, encoding="utf-8") as f:
                existing = json.load(f)
                existing_titles = {p["title"] for p in existing}
        except:
            pass
    
    new_papers = [p for p in papers if p["title"] not in existing_titles]
    
    if not new_papers:
        print(f"  No new papers (already have {len(existing_titles)})")
        return 0
    
    if dry_run:
        for p in new_papers[:3]:
            print(f"    → {p['title']} ({p.get('year','')}, cited:{p.get('citations',0)})")
        if len(new_papers) > 3:
            print(f"    ... and {len(new_papers)-3} more")
        return len(new_papers)
    
    all_papers = existing + new_papers
    all_papers.sort(key=lambda x: x.get("citations", 0), reverse=True)
    
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(all_papers, f, ensure_ascii=False, indent=2)
    
    print(f"  ✓ Saved {len(new_papers)} new (total: {len(all_papers)})")
    return len(new_papers)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=["scholars", "psychologists", "military", "designers", "all"], required=True)
    parser.add_argument("--person", type=str, help="Filter by person name substring")
    parser.add_argument("--category", type=str, help="Filter by category")
    parser.add_argument("--max", type=int, default=20, help="Max papers per person")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between API calls (seconds)")
    
    args = parser.parse_args()
    groups = ["scholars", "psychologists", "military", "designers"] if args.group == "all" else [args.group]
    
    total_new = 0
    total_persons = 0
    errors = []
    
    for group in groups:
        persons = get_all_persons(group)
        
        for g, cat, name, path in persons:
            if args.category and cat != args.category:
                continue
            if args.person and args.person not in name:
                continue
            
            label = f"{cat}/{name}" if cat else name
            print(f"\n[{g.upper()}] {label}")
            
            search_name = NAME_MAP.get(name, name.replace("_", " "))
            
            papers = search_google_scholar(search_name, args.max)
            
            if not papers:
                errors.append(label)
                print(f"  [WARN] No papers found for '{search_name}'")
                time.sleep(args.delay)
                continue
            
            n = process_person(path, papers, args.dry_run)
            total_new += n
            total_persons += 1
            time.sleep(args.delay)
    
    print(f"\n{'='*60}")
    print(f"Done! {total_persons} persons processed, {total_new} new papers")
    if errors:
        print(f"Errors/empty: {errors}")


if __name__ == "__main__":
    main()
