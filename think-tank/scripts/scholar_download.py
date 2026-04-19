#!/usr/bin/env python3
"""
论文全文下载：OpenAlex查DOI+OA → Unpaywall查免费PDF → 直接下载 → Sci-Hub兜底
用法：
  python3 scholar_download.py --group scholars --person "Mearsheimer" --max 10
  python3 scholar_download.py --group all --max 15 --delay 3
"""
import argparse, json, time, re, tempfile
from pathlib import Path
import requests

BASE = Path(__file__).parent.parent / "personalities"
EMAIL = "tearsyu@gmail.com"
UA = f"Mailto:{EMAIL} (ThinkTank Project)"

def search_openalex_author(name):
    """通过OpenAlex找到作者的ID"""
    url = "https://api.openalex.org/authors"
    params = {"search": name, "per_page": 1}
    r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=15)
    r.raise_for_status()
    results = r.json().get("results", [])
    if results:
        return results[0]["id"], results[0].get("display_name", name), results[0].get("works_count", 0)
    return None, name, 0

def get_openalex_papers(author_id, max_papers=20):
    """获取作者的全部论文，按引用数排序"""
    url = "https://api.openalex.org/works"
    params = {
        "filter": f"author.id:{author_id}",
        "sort": "cited_by_count:desc",
        "per_page": min(max_papers, 100),
        "select": "id,title,doi,publication_year,cited_by_count,open_access,primary_location,type",
    }
    r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    papers = []
    for item in r.json().get("results", []):
        doi = (item.get("doi") or "").replace("https://doi.org/", "")
        oa_url = item.get("open_access", {}).get("oa_url", "")
        # primary_location 可能有 pdf_url
        pl = item.get("primary_location") or {}
        pdf_url = pl.get("pdf_url", "") or oa_url
        papers.append({
            "title": item.get("title", ""),
            "year": item.get("publication_year", ""),
            "citations": item.get("cited_by_count", 0),
            "doi": doi,
            "oa_url": pdf_url,
            "type": item.get("type", ""),
        })
    return papers

def check_unpaywall(doi):
    """查Unpaywall获取免费PDF链接"""
    if not doi:
        return ""
    try:
        url = f"https://api.unpaywall.org/v2/{doi}?email={EMAIL}"
        r = requests.get(url, headers={"User-Agent": UA}, timeout=10)
        if r.status_code == 200:
            d = r.json()
            if d.get("is_oa"):
                loc = d.get("best_oa_location") or {}
                return loc.get("url_for_pdf") or loc.get("url") or ""
    except:
        pass
    return ""

def download_pdf(url, save_path):
    """下载PDF并提取文本"""
    import fitz
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=60, stream=True)
        if r.status_code != 200 or len(r.content) < 1000:
            return False
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(r.content)
            tmp_path = tmp.name
        try:
            doc = fitz.open(tmp_path)
            text = "\n".join(page.get_text() for page in doc)
            doc.close()
        except:
            text = ""
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        if text and len(text.strip()) > 500:
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(text)
            return True
        # 可能是HTML而不是PDF
        if r.headers.get("Content-Type", "").startswith("text/html") and len(r.content) > 2000:
            text = r.content.decode("utf-8", errors="replace")
            # 简单去HTML标签
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 1000:
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(text)
                return True
    except Exception as e:
        print(f"    [ERR] Download: {e}")
    return False

def download_scihub(doi, save_path):
    """用Playwright绕CAPTCHA从Sci-Hub下载PDF"""
    import fitz
    from playwright.sync_api import sync_playwright

    if not doi:
        return False

    for mirror in ["https://sci-hub.se", "https://sci-hub.st", "https://sci-hub.ru"]:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path='/snap/bin/chromium')
                ctx = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
                page = ctx.new_page()
                page.goto(f"{mirror}/{doi}", timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)

                # 找PDF链接
                pdf_link = None
                # iframe/embed
                for el in page.query_selector_all("iframe, embed"):
                    src = el.get_attribute("src") or ""
                    if "pdf" in src.lower() or "download" in src.lower():
                        pdf_link = src
                        if not pdf_link.startswith("http"):
                            pdf_link = mirror + pdf_link
                        break

                # 如果没找到iframe，找带pdf的链接
                if not pdf_link:
                    for a in page.query_selector_all("a[href*='pdf'], a[href*='download']"):
                        pdf_link = a.get_attribute("href")
                        if pdf_link and not pdf_link.startswith("http"):
                            pdf_link = mirror + pdf_link
                        break

                # 检查captcha
                content = page.content()
                if "captcha" in content.lower() and not pdf_link:
                    # 等待用户手动过验证（headless不行，但试试）
                    page.wait_for_timeout(3000)
                    for a in page.query_selector_all("a[href*='pdf'], a[href*='download'], iframe, embed"):
                        src = a.get_attribute("src") or a.get_attribute("href") or ""
                        if "pdf" in src.lower():
                            pdf_link = src
                            if not pdf_link.startswith("http"):
                                pdf_link = mirror + pdf_link
                            break

                browser.close()

                if not pdf_link:
                    continue

                # 下载PDF
                headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
                r = requests.get(pdf_link, headers=headers, timeout=60, stream=True)
                if r.status_code != 200 or len(r.content) < 1000:
                    continue

                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(r.content)
                    tmp_path = tmp.name
                try:
                    doc = fitz.open(tmp_path)
                    text = "\n".join(pg.get_text() for pg in doc)
                    doc.close()
                except:
                    text = ""
                finally:
                    Path(tmp_path).unlink(missing_ok=True)

                if text and len(text.strip()) > 500:
                    with open(save_path, "w", encoding="utf-8") as f:
                        f.write(text)
                    return True
        except Exception as e:
            print(f"    [WARN] Sci-Hub {mirror}: {e}")
            continue
    return False


def sanitize(name, max_len=60):
    return re.sub(r'[\\/:*?"<>|]', '_', name)[:max_len].strip()

def process_person(person_dir, person_name, search_name, max_papers=10, delay=3):
    """处理一个人的论文下载"""
    materials = person_dir / "materials"
    materials.mkdir(parents=True, exist_ok=True)
    meta_path = materials / "_papers_meta.json"
    
    existing = set(f.name for f in materials.glob("paper_*.txt"))
    
    # Step 1: OpenAlex搜索作者
    author_id, real_name, works_count = search_openalex_author(search_name)
    if not author_id:
        print(f"  ⚠️ OpenAlex找不到: {search_name}")
        return 0
    
    print(f"  📚 {real_name} ({works_count} works)")
    time.sleep(1)
    
    # Step 2: 获取论文列表
    papers = get_openalex_papers(author_id, max_papers * 2)  # 多取一些，因为有些下载不了
    if not papers:
        print(f"  ⚠️ 无论文: {search_name}")
        return 0
    
    # Step 3: 逐篇下载
    downloaded = 0
    for paper in papers:
        if downloaded >= max_papers:
            break
        
        title = paper.get("title", "")
        if not title:
            continue
        
        txt_name = f"paper_{sanitize(title)}.txt"
        if txt_name in existing:
            continue
        
        doi = paper.get("doi", "")
        
        # 优先用OA链接
        pdf_url = paper.get("oa_url", "")
        
        # 没有OA链接就查Unpaywall
        if not pdf_url and doi:
            pdf_url = check_unpaywall(doi)
            time.sleep(1)
        
        save_path = materials / txt_name
        print(f"  📥 {title[:55]}... (cited:{paper.get('citations',0)})")
        ok = False

        # 1. 先试OA链接
        if pdf_url:
            ok = download_pdf(pdf_url, save_path)
            if ok:
                print(f"    ✓ OA下载成功")

        # 2. OA失败，有DOI就试Sci-Hub
        if not ok and doi:
            ok = download_scihub(doi, save_path)
            if ok:
                print(f"    ✓ Sci-Hub下载成功")

        if ok:
            downloaded += 1
            existing.add(txt_name)
        else:
            print(f"    ✗ 下载失败 (doi:{doi[:30] if doi else '无'})")
        
        time.sleep(delay)
    
    # 更新元数据
    all_meta = []
    if meta_path.exists():
        try:
            all_meta = json.load(open(meta_path))
        except:
            pass
    # 去重合并
    existing_titles = {p.get("title","") for p in all_meta}
    for p in papers:
        if p["title"] not in existing_titles:
            all_meta.append(p)
    all_meta.sort(key=lambda x: x.get("citations",0), reverse=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(all_meta, f, ensure_ascii=False, indent=2)
    
    print(f"  📊 {real_name}: +{downloaded}下载 (共{len(papers)}篇)")
    return downloaded

# 人物名映射：目录名 → OpenAlex搜索名
NAME_MAP = {
    # 学者 IR Theory
    "John_Mearsheimer": "John Mearsheimer",
    "Graham_Allison": "Graham Allison",
    "阎学通": "Yan Xuetong",
    "金灿荣": "Jin Canrong",
    "瞿东升": "Qu Dongsheng",
    "时殷弘": "Shi Yinhong",
    "潘忠岐": "Pan Zhongqi",
    # 学者 IR Security
    "Chomsky": "Noam Chomsky",
    "沈逸": "Shen Yi",
    "艾跃进": "Ai Yuejin",
    # 学者 Economics
    "卢麒元": "Lu Qiyuan",
    "张维迎": "Zhang Weiying",
    "温铁军": "Wen Tiejun",
    # 学者 Middle East
    "Fawaz_Gerges": "Fawaz Gerges",
    "Vali_Nasr": "Vali Nasr",
    "殷罡": "Yin Gang",
    "牛新春": "Niu Xinchun",
    "田文林": "Tian Wenlin",
    # 学者 Central Asia
    "Alexander_Cooley": "Alexander Cooley",
    "Sebastien_Peyrouse": "Sebastien Peyrouse",
    "曾向红": "Zeng Xianghong",
    "胡仕胜": "Hu Shisheng",
    # 学者 Persian/Islamic
    "Bernard_Lewis": "Bernard Lewis",
    "Hamid_Dabashi": "Hamid Dabashi",
    "Said_Amir_Arjomand": "Said Amir Arjomand",
    "殷之光": "Yin Zhiguang",
    # 学者 Religious Studies
    "Huston_Smith": "Huston Smith",
    "Karen_Armstrong": "Karen Armstrong",
    "吴云贵": "Wu Yungui",
    "金宜久": "Jin Yijiu",
    # 心理学家 Cognitive
    "阿莫斯·特沃斯基": "Amos Tversky",
    "乔纳森·海特": "Jonathan Haidt",
    "阿尔伯特·班杜拉": "Albert Bandura",
    "马丁·塞利格曼": "Martin Seligman",
    "丹尼尔·卡尼曼": "Daniel Kahneman",
    "唐纳德·诺曼": "Donald Norman",
    "基思·斯坦诺维奇": "Keith Stanovich",
    "米哈里·契克森米哈赖": "Mihaly Csikszentmihalyi",
    # 心理学家 Behavioral
    "斯坦利·米尔格拉姆": "Stanley Milgram",
    "菲利普·津巴多": "Philip Zimbardo",
    "理查德·塞勒": "Richard Thaler",
    "丹·阿瑞利": "Dan Ariely",
    "所罗门·阿希": "Solomon Asch",
    # 心理学家 Psychoanalysis
    "R·D·莱恩": "R.D. Laing",
    "苏珊·福沃德": "Susan Forward",
    "卡尔·罗杰斯": "Carl Rogers",
    "欧文·亚隆": "Irvin Yalom",
    "维吉尼亚·萨提亚": "Virginia Satir",
    "阿尔伯特·埃利斯": "Albert Ellis",
    # 心理学家 Social Engineering
    "古斯塔夫·勒庞": "Gustave Le Bon",
    "保罗·艾克曼": "Paul Ekman",
    "凯文·米特尼克": "Kevin Mitnick",
    "罗伯特·西奥迪尼": "Robert Cialdini",
    "克里斯·哈德纳吉": "Chris Hadnagy",
}

def main():
    parser = argparse.ArgumentParser(description="OpenAlex+Unpaywall论文下载")
    parser.add_argument("--group", choices=["scholars", "psychologists", "all"], required=True)
    parser.add_argument("--person", type=str, help="过滤人物名")
    parser.add_argument("--category", type=str, help="过滤子分类")
    parser.add_argument("--max", type=int, default=10, help="每人最多下载篇数")
    parser.add_argument("--delay", type=float, default=3.0, help="API调用间隔(秒)")
    args = parser.parse_args()
    
    groups = ["scholars", "psychologists"] if args.group == "all" else [args.group]
    total = 0
    
    for group in groups:
        group_dir = BASE / group
        for cat_dir in sorted(group_dir.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name.startswith("_"):
                continue
            for person_dir in sorted(cat_dir.iterdir()):
                if not person_dir.is_dir() or person_dir.name.startswith("_"):
                    continue
                name = person_dir.name
                if args.person and args.person not in name:
                    continue
                if args.category and cat_dir.name != args.category:
                    continue
                
                search_name = NAME_MAP.get(name, name.replace("_", " "))
                print(f"\n[{group.upper()}] {cat_dir.name}/{name}")
                n = process_person(person_dir, name, search_name, args.max, args.delay)
                total += n
    
    print(f"\n{'='*50}")
    print(f"总计下载: {total} 篇")

if __name__ == "__main__":
    main()
