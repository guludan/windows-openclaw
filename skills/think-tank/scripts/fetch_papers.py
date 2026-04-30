#!/usr/bin/env python3
"""
fetch_papers.py — 为指定人物搜索并下载论文全文

使用 Semantic Scholar API 搜索论文 → 下载PDF → 提取文本 → 存入 papers/raw/
同时更新 _db.sqlite

用法:
    python3 fetch_papers.py --person <dir> [--max-papers 10] [--offset 0]
    python3 fetch_papers.py --list-empty              # 列出需要补论文的人
    python3 fetch_papers.py --all-empty [--max-papers 10]  # 批量处理

注意:
    - Semantic Scholar API 免费但有速率限制（100次/5分钟）
    - 下载间隔 >= 5秒
    - PDF下载可能失败（paywall等），会fallback到文本摘要
"""

import argparse
import json
import sqlite3
import time
import os
import re
import subprocess
import hashlib
from pathlib import Path
from datetime import datetime

BASE_DIR = Path("/mnt/d/data/think-tank-data/personalities")

# 人物英文名映射（用于Semantic Scholar搜索）
# key = 目录名, value = 搜索用的英文名
PERSON_NAMES = {
    # psychologists/social-engineering
    "克里斯·哈德纳吉": "Christopher Hadnagy",
    # scholars/economics
    "John_Maynard_Keynes": "John Maynard Keynes",
    "Joseph_Stiglitz": "Joseph Stiglitz",
    "Paul_Krugman": "Paul Krugman",
    "卢麒元": "卢麒元",
    # scholars/financial-engineering
    "David_Shaw": "David E. Shaw",
    "Edward_Thorp": "Edward O. Thorp",
    "Fischer_Black": "Fischer Black",
    "Jim_Simons": "James Simons",
    # scholars/ir-security
    "沈逸": "沈逸",
    "艾跃进": "艾跃进",
    # scholars/ir-theory
    "瞿东升": "瞿东升",
    # scholars/media-studies
    "Walter_Lippmann": "Walter Lippmann",
    # scholars/middle-east
    "殷罡": "殷罡",
    # scholars/religious-studies
    "Huston_Smith": "Huston Smith",
    "金宜久": "金宜久",
    # === 第二批：没有papers目录的scholars ===
    "Carmen_Reinhart": "Carmen Reinhart",
    "Charles_Kindleberger": "Charles Kindleberger",
    "Hyman_Minsky": "Hyman Minsky",
    "Nassim_Taleb": "Nassim Taleb",
    "Ray_Dalio": "Ray Dalio",
    "Avinash_Dixit": "Avinash Dixit",
    "John_Nash": "John Nash",
    "John_von_Neumann": "John von Neumann",
    "Ken_Binmore": "Ken Binmore",
    "Reinhard_Selten": "Reinhard Selten",
    "Robert_Aumann": "Robert Aumann",
    "Roger_Myerson": "Roger Myerson",
    "Thomas_Schelling": "Thomas Schelling",
    "George_Friedman": "George Friedman",
    "Ian_Bremmer": "Ian Bremmer",
    "Nouriel_Roubini": "Nouriel Roubini",
    "Peter_Zeihan": "Peter Zeihan",
    "Kurt_Campbell": "Kurt Campbell",
    "Oriana_Mastro": "Oriana Mastro",
    "Richard_Bush": "Richard Bush",
    "William_Blum": "William Blum",
    "John_Gaddis": "John Gaddis",
    "Nate_Silver": "Nate Silver",
    "Philip_Tetlock": " Philip Tetlock",
    "Chris_Miller": "Chris Miller",
    "Mariana_Mazzucato": "Mariana Mazzucato",
    # === psychologists with empty papers ===
    "David_Buss": "David Buss",
    "Robert_Sapolsky": "Robert Sapolsky",
    "Steven_Pinker": "Steven Pinker",
    "Antonio_Damasio": "Antonio Damasio",
    "Joseph_LeDoux": "Joseph LeDoux",
    "Oliver_Sacks": "Oliver Sacks",
    # === scholars with papers dir already having content ===
    "Eugene_Fama": "Eugene Fama",
    "Harry_Markowitz": "Harry Markowitz",
    "John_Hull": "John Hull",
    "Robert_Merton": "Robert Merton",
    "William_Sharpe": "William Sharpe",
    "Friedrich_Hayek": "Friedrich Hayek",
    "Milton_Friedman": "Milton Friedman",
    "Kenneth_Rogoff": "Kenneth Rogoff",
    "林毅夫": "林毅夫",
    "温铁军": "温铁军",
    "黄奇帆": "黄奇帆",
    "Marcos_Carreira": "Marcos Carreira",
    "Marcos_Lopez_de_Prado": "Marcos Lopez de Prado",
    "Steve_Shreve": "Steve Shreve",
    "Yves_Hilpisch": "Yves Hilpisch",
    "Aaron_Brown": "Aaron Brown",
    "Cliff_Asness": "Cliff Asness",
    "Emanuel_Derman": "Emanuel Derman",
    "Myron_Scholes": "Myron Scholes",
    "Chomsky": "Noam Chomsky",
    "Edward_Bernays": "Edward Bernays",
    "Eli_Pariser": "Eli Pariser",
    "Marshall_McLuhan": "Marshall McLuhan",
    "Neil_Postman": "Neil Postman",
}


def iter_person_dirs():
    """遍历所有人物目录"""
    for group_dir in sorted(BASE_DIR.iterdir()):
        if not group_dir.is_dir() or group_dir.name.startswith('.'):
            continue
        for sub_dir in sorted(group_dir.iterdir()):
            if not sub_dir.is_dir():
                continue
            if (sub_dir / "materials").exists() or (sub_dir / "papers").exists():
                yield sub_dir
                continue
            for person_dir in sorted(sub_dir.iterdir()):
                if person_dir.is_dir():
                    yield person_dir


def get_search_name(person_dir: Path) -> str:
    """获取搜索用的人名"""
    dirname = person_dir.name
    if dirname in PERSON_NAMES:
        return PERSON_NAMES[dirname]
    # 从目录名提取英文部分
    if '_' in dirname:
        return dirname.replace('_', ' ')
    return dirname


def search_papers_semantic(author_name: str, max_papers: int = 10, offset: int = 0) -> list:
    """使用 Semantic Scholar API 搜索论文"""
    try:
        from semanticscholar import SemanticScholar
        sch = SemanticScholar(timeout=30)
        
        # Search by author
        results = sch.search_author(author_name, limit=1)
        if not results:
            print(f"    未找到作者: {author_name}")
            return []
        
        author = results[0]
        papers = author.papers if hasattr(author, 'papers') else []
        
        if not papers:
            # Fallback: keyword search
            results = sch.search_paper(f"author:{author_name}", limit=max_papers, offset=offset)
            papers = list(results)
        
        output = []
        for paper in papers[offset:offset+max_papers]:
            info = {
                "title": getattr(paper, 'title', ''),
                "year": getattr(paper, 'year', None),
                "doi": getattr(paper, 'externalIds', {}).get('DOI', '') if hasattr(paper, 'externalIds') else '',
                "paper_id": getattr(paper, 'paperId', ''),
                "abstract": getattr(paper, 'abstract', '') or '',
                "citation_count": getattr(paper, 'citationCount', 0) or 0,
                "url": getattr(paper, 'url', ''),
                "open_access_pdf": None,
            }
            # Check for open access PDF
            oa = getattr(paper, 'openAccessPdf', None)
            if oa and isinstance(oa, dict):
                info["open_access_pdf"] = oa.get('url', '')
            output.append(info)
        
        return output
    except Exception as e:
        print(f"    Semantic Scholar搜索失败: {e}")
        return []


def search_papers_arxiv(author_name: str, max_papers: int = 10) -> list:
    """使用 arXiv API 搜索论文"""
    try:
        import arxiv
        client = arxiv.Client(page_size=max_papers)
        search = arxiv.Search(
            query=f"au:{author_name}",
            max_results=max_papers,
            sort_by=arxiv.SortCriterion.SubmittedDate
        )
        output = []
        import signal
        def _arxiv_timeout_handler(signum, frame):
            raise TimeoutError("arXiv search timeout")
        signal.signal(signal.SIGALRM, _arxiv_timeout_handler)
        signal.alarm(30)
        try:
            for result in client.results(search):
                output.append({
                    "title": result.title,
                    "year": result.published.year if result.published else None,
                    "doi": result.doi or '',
                    "paper_id": result.entry_id,
                    "abstract": result.summary or '',
                    "citation_count": 0,
                    "url": result.entry_id,
                    "open_access_pdf": result.pdf_url,
                })
        finally:
            signal.alarm(0)
        return output
    except Exception as e:
        print(f"    arXiv搜索失败: {e}")
        return []


def download_pdf(url: str, output_path: Path) -> bool:
    """下载PDF文件"""
    import requests
    try:
        headers = {"User-Agent": "Mozilla/5.0 (research; mailto:research@example.com)"}
        r = requests.get(url, headers=headers, timeout=60, stream=True)
        if r.status_code == 200 and 'application/pdf' in r.headers.get('content-type', ''):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            return output_path.stat().st_size > 1000  # 至少1KB
    except Exception as e:
        print(f"      PDF下载失败: {e}")
    return False


def extract_text_from_pdf(pdf_path: Path) -> str:
    """从PDF提取文本（使用PyMuPDF）"""
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        return '\n\n'.join(text_parts)
    except ImportError:
        # Fallback to pdftotext
        try:
            result = subprocess.run(
                ['pdftotext', '-layout', str(pdf_path), '-'],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                return result.stdout
        except Exception:
            pass
    return ""


def sanitize_filename(title: str, max_len: int = 80) -> str:
    """将论文标题转为安全文件名"""
    # Remove HTML tags
    name = re.sub(r'<[^>]+>', '', title)
    # Normalize smart quotes/dashes
    name = name.replace('\u2018', "'").replace('\u2019', "'")
    name = name.replace('\u201c', '"').replace('\u201d', '"')
    name = name.replace('\u2013', '-').replace('\u2014', '-')
    # 移除不安全字符
    name = re.sub(r'[<>:"/\\|?*\n\r]', '', name)
    # 空格替换为下划线
    name = name.replace(' ', '_')
    # Collapse multiple underscores
    name = re.sub(r'_+', '_', name)
    # 截断
    if len(name) > max_len:
        name = name[:max_len]
    return name


def process_person(person_dir: Path, max_papers: int = 10, offset: int = 0) -> dict:
    """为单个人物搜索并下载论文"""
    name = get_search_name(person_dir)
    stats = {"searched": 0, "downloaded": 0, "extracted": 0, "errors": []}
    
    print(f"\n{'='*60}")
    print(f"处理: {person_dir.name} (搜索名: {name})")
    
    # 创建目录
    papers_raw = person_dir / "papers" / "raw"
    papers_pdfs = person_dir / "papers" / "pdfs"
    papers_raw.mkdir(parents=True, exist_ok=True)
    papers_pdfs.mkdir(parents=True, exist_ok=True)
    
    # 搜索论文（Semantic Scholar + arXiv）
    all_papers = []
    
    # Semantic Scholar
    print(f"  搜索 Semantic Scholar...")
    ss_papers = search_papers_semantic(name, max_papers=max_papers, offset=offset)
    all_papers.extend(ss_papers)
    print(f"    找到 {len(ss_papers)} 篇")
    stats["searched"] += len(ss_papers)
    time.sleep(2)  # 速率限制
    
    # arXiv (补充) - DISABLED: hangs for social science authors
    # if len(all_papers) < max_papers:
    #     print(f"  搜索 arXiv...")
    
    if not all_papers:
        stats["errors"].append("未找到任何论文")
        return stats
    
    # Deduplicate by title
    seen_titles = set()
    unique_papers = []
    for p in all_papers:
        title_key = p['title'].lower().strip()
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique_papers.append(p)
    all_papers = unique_papers[:max_papers]
    
    # 保存元数据
    meta = {}
    for i, paper in enumerate(all_papers):
        safe_name = sanitize_filename(paper['title'])
        filename = f"{i+1:03d}_{safe_name}"
        md_filename = filename + ".md"
        meta[md_filename] = {
            "title": paper['title'],
            "doi": paper.get('doi', ''),
            "year": paper.get('year'),
            "citation_count": paper.get('citation_count', 0),
            "paper_id": paper.get('paper_id', ''),
            "url": paper.get('url', ''),
        }
    
    meta_path = person_dir / "papers" / "_meta.json"
    # 合并已有meta
    existing_meta = {}
    if meta_path.exists():
        try:
            existing_meta = json.loads(meta_path.read_text(encoding='utf-8'))
        except Exception:
            pass
    existing_meta.update(meta)
    meta_path.write_text(json.dumps(existing_meta, ensure_ascii=False, indent=2), encoding='utf-8')
    
    # 下载PDF + 提取文本
    for i, paper in enumerate(all_papers):
        safe_name = sanitize_filename(paper['title'])
        txt_filename = f"{i+1+offset:03d}_{safe_name}.md"
        # 也检查旧格式 .txt
        txt_filename_old = f"{i+1+offset:03d}_{safe_name}.txt"
        pdf_filename = f"{i+1+offset:03d}_{safe_name}.pdf"
        
        txt_path = papers_raw / txt_filename
        txt_path_old = papers_raw / txt_filename_old
        pdf_path = papers_pdfs / pdf_filename
        
        # 跳过已存在的
        if (txt_path.exists() and txt_path.stat().st_size > 100) or (txt_path_old.exists() and txt_path_old.stat().st_size > 100):
            existing = txt_path if txt_path.exists() else txt_path_old
            print(f"  [{i+1}/{len(all_papers)}] 已存在: {existing.name}")
            stats["extracted"] += 1
            continue
        
        # 尝试下载PDF
        pdf_url = paper.get('open_access_pdf') or ''
        has_pdf = False
        
        if pdf_url:
            print(f"  [{i+1}/{len(all_papers)}] 下载PDF: {paper['title'][:50]}...")
            has_pdf = download_pdf(pdf_url, pdf_path)
            if has_pdf:
                stats["downloaded"] += 1
                time.sleep(5)  # 下载间隔
                
                # 提取文本
                text = extract_text_from_pdf(pdf_path)
                if text and len(text) > 200:
                    # 在文本头部加markdown front matter
                    header = f"---\n"
                    header += f'title: "{paper["title"]}"\n'
                    if paper.get('doi'):
                        header += f"doi: {paper['doi']}\n"
                    if paper.get('year'):
                        header += f"year: {paper['year']}\n"
                    header += f"---\n\n"
                    if paper.get('abstract'):
                        header += f"## Abstract\n\n{paper['abstract']}\n\n"
                    header += f"---\n\n"
                    
                    txt_path.write_text(header + text, encoding='utf-8')
                    stats["extracted"] += 1
                    print(f"    ✓ 提取文本: {len(text):,} 字符")
                else:
                    print(f"    ⚠ PDF文本提取为空")
            else:
                print(f"    ✗ PDF下载失败")
        
        # 如果没有PDF，用abstract + URL作为素材
        if not has_pdf:
            if paper.get('abstract'):
                content = f"---\n"
                content += f'title: "{paper["title"]}"\n'
                if paper.get('doi'):
                    content += f"doi: {paper['doi']}\n"
                if paper.get('year'):
                    content += f"year: {paper['year']}\n"
                content += f"url: {paper.get('url', '')}\n"
                content += f"---\n\n"
                content += f"## Abstract\n\n{paper['abstract']}\n"
                
                txt_path.write_text(content, encoding='utf-8')
                stats["extracted"] += 1
                print(f"    → 保存abstract: {len(paper['abstract']):,} 字符")
            else:
                stats["errors"].append(f"{paper['title'][:40]}: 无PDF无abstract")
        
        time.sleep(1)  # 请求间隔
    
    # 更新数据库
    db_path = person_dir / "_db.sqlite"
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            now = datetime.now().isoformat()
            
            for i, paper in enumerate(all_papers):
                safe_name = sanitize_filename(paper['title'])
                filename = f"{i+1+offset:03d}_{safe_name}.md"
                # 也检查旧格式
                filename_old = f"{i+1+offset:03d}_{safe_name}.txt"
                pdf_filename = f"{i+1+offset:03d}_{safe_name}.pdf"
                
                has_pdf_file = (papers_pdfs / pdf_filename).exists()
                txt_file = papers_raw / filename
                txt_file_old = papers_raw / filename_old
                
                if txt_file.exists() or txt_file_old.exists():
                    actual_file = txt_file if txt_file.exists() else txt_file_old
                    actual_filename = actual_file.name
                    conn.execute("""
                        INSERT OR IGNORE INTO items 
                        (person, category, filename, source_type, status, file_path, file_size, char_count, 
                         doi, year, title, has_pdf, added_time)
                        VALUES (?, 'papers', ?, 'paper', 'raw', ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """, (
                        person_dir.name, actual_filename, f"papers/raw/{actual_filename}",
                        actual_file.stat().st_size, actual_file.stat().st_size,
                        paper.get('doi', ''), paper.get('year'), paper['title'],
                        1 if has_pdf_file else 0
                    ))
            
            conn.execute("""
                INSERT INTO processing_log (person, action, details) 
                VALUES (?, 'fetch_papers', ?)
            """, (person_dir.name, json.dumps(stats, ensure_ascii=False)))
            
            conn.commit()
            conn.close()
            print(f"  数据库已更新")
        except Exception as e:
            stats["errors"].append(f"DB更新失败: {e}")
    
    return stats


def main():
    parser = argparse.ArgumentParser(description="为人物搜索并下载论文")
    parser.add_argument("--person", metavar="DIR", help="指定人物目录")
    parser.add_argument("--list-empty", action="store_true", help="列出需要补论文的人")
    parser.add_argument("--all-empty", action="store_true", help="批量处理所有空papers的人")
    parser.add_argument("--all-no-papers", action="store_true", help="处理完全没有papers目录的scholars")
    parser.add_argument("--max-papers", type=int, default=10, help="每人最多下载论文数")
    parser.add_argument("--offset", type=int, default=0, help="搜索偏移量")
    args = parser.parse_args()
    
    if args.list_empty:
        # 列出有papers/目录但为空的
        empty = []
        for person_dir in iter_person_dirs():
            db_path = person_dir / "_db.sqlite"
            if not db_path.exists():
                continue
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM items WHERE category='papers'")
            count = cur.fetchone()[0]
            conn.close()
            if count == 0:
                name = get_search_name(person_dir)
                rel = person_dir.relative_to(BASE_DIR)
                empty.append((str(rel), name))
        
        print(f"需要补论文的人物: {len(empty)}")
        for rel, name in empty:
            print(f"  {rel} → 搜索: {name}")
        return
    
    if args.all_empty:
        for person_dir in iter_person_dirs():
            db_path = person_dir / "_db.sqlite"
            if not db_path.exists():
                continue
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM items WHERE category='papers'")
            count = cur.fetchone()[0]
            conn.close()
            if count == 0:
                process_person(person_dir, max_papers=args.max_papers, offset=args.offset)
                time.sleep(3)
        return
    
    if args.all_no_papers:
        # 处理完全没有papers目录的scholars
        for person_dir in iter_person_dirs():
            rel = str(person_dir.relative_to(BASE_DIR))
            if 'scholar' not in rel and 'psychologist' not in rel:
                continue
            if (person_dir / "papers").exists():
                continue
            process_person(person_dir, max_papers=args.max_papers, offset=args.offset)
            time.sleep(3)
        return
    
    if args.person:
        person_dir = Path(args.person)
        if not person_dir.is_dir():
            candidates = list(BASE_DIR.rglob(args.person))
            person_dir = candidates[0] if candidates else Path(args.person)
        process_person(person_dir, max_papers=args.max_papers, offset=args.offset)
        return
    
    parser.print_help()


if __name__ == "__main__":
    main()
