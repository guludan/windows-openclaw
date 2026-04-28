#!/usr/bin/env python3
"""
补下载空元数据论文全文

逐人逐篇处理，用data_search.py的download_scihub_pdf能力补下载。
每篇间隔5秒。

用法:
    python3 supplement_papers.py --list          # 列出需要补下载的人
    python3 supplement_papers.py --stats         # 统计
    python3 supplement_papers.py --person <dir>  # 补下载指定人物
    python3 supplement_papers.py --all           # 逐人补下载（慎用）
"""
import json, os, sys, time, re
from pathlib import Path
from datetime import datetime

BASE = Path("/mnt/d/data/think-tank-data/personalities")
sys.path.insert(0, str(Path("~/.openclaw/workspace/skills/data-search/scripts").expanduser()))

def is_meta_only(filepath):
    """判断文件是否只有元数据"""
    content = filepath.read_text(encoding='utf-8', errors='ignore').strip()
    non_meta = [l for l in content.split('\n') if l.strip() and not l.startswith('#')]
    return len(non_meta) <= 5 or len(content) <= 500

def extract_doi(filepath):
    """从元数据文件提取DOI"""
    content = filepath.read_text(encoding='utf-8', errors='ignore')
    for line in content.split('\n'):
        if line.startswith('# DOI:'):
            doi = line.replace('# DOI:', '').strip()
            if doi and doi != 'None' and len(doi) > 3:
                return doi
    return None

def extract_title(filepath):
    """从元数据文件提取标题"""
    content = filepath.read_text(encoding='utf-8', errors='ignore')
    for line in content.split('\n'):
        if line.startswith('#') and not line.startswith('# ') or line.startswith('# Effects') or line.startswith('# Authors'):
            continue
        if line.startswith('# ') and not line.startswith('# Authors') and not line.startswith('# Year') and not line.startswith('# DOI') and not line.startswith('# Citations'):
            return line[2:].strip()
    return None

def get_supplement_list():
    """获取需要补下载的人物列表"""
    results = []
    for mat_dir in sorted(BASE.rglob("materials")):
        if mat_dir.name != "materials":
            continue
        person_dir = mat_dir.parent
        
        meta_only_files = []
        for f in sorted(list(mat_dir.glob("paper_*.txt")) + list(mat_dir.glob("paper_*.md"))):
            if is_meta_only(f):
                doi = extract_doi(f)
                title = extract_title(f)
                meta_only_files.append({
                    "file": str(f),
                    "filename": f.name,
                    "doi": doi,
                    "title": title
                })
        
        if meta_only_files:
            with_doi = [m for m in meta_only_files if m["doi"]]
            results.append({
                "person_dir": str(person_dir),
                "person_name": person_dir.name,
                "total_meta_only": len(meta_only_files),
                "with_doi": len(with_doi),
                "files": meta_only_files
            })
    
    results.sort(key=lambda x: -x["total_meta_only"])
    return results

if __name__ == "__main__":
    if "--list" in sys.argv or "--stats" in sys.argv:
        items = get_supplement_list()
        total_files = sum(i["total_meta_only"] for i in items)
        total_doi = sum(i["with_doi"] for i in items)
        print(f"需要补下载: {len(items)} 人, {total_files} 篇 (其中 {total_doi} 篇有DOI)\n")
        for item in items:
            print(f"  {item['person_name']}: {item['total_meta_only']}篇缺全文 ({item['with_doi']}篇有DOI)")
    
    elif "--person" in sys.argv:
        idx = sys.argv.index("--person")
        if idx + 1 >= len(sys.argv):
            print("Usage: --person <person_dir>")
            sys.exit(1)
        
        person_dir = Path(sys.argv[idx + 1])
        mat_dir = person_dir / "materials"
        
        from data_search import download_scihub_pdf
        
        meta_files = [f for f in sorted(list(mat_dir.glob("paper_*.txt")) + list(mat_dir.glob("paper_*.md"))) if is_meta_only(f)]
        print(f"{person_dir.name}: {len(meta_files)} 篇需要补下载")
        
        success = 0
        failed = 0
        for i, f in enumerate(meta_files):
            doi = extract_doi(f)
            title = extract_title(f) or f.stem
            
            if not doi:
                print(f"  [{i+1}/{len(meta_files)}] {f.name}: 无DOI，跳过")
                failed += 1
                continue
            
            print(f"  [{i+1}/{len(meta_files)}] 下载 DOI:{doi} ...", end=" ", flush=True)
            try:
                pdf_path = download_scihub_pdf(doi, str(mat_dir))
                if pdf_path and Path(pdf_path).exists() and Path(pdf_path).stat().st_size > 1000:
                    # 提取PDF文本
                    import fitz
                    doc = fitz.open(pdf_path)
                    text = ""
                    for page in doc:
                        text += page.get_text()
                    doc.close()
                    
                    # 替换原元数据文件
                    new_content = f"# {title}\n# DOI: {doi}\n# Downloaded: {datetime.now().isoformat()}\n\n{text}"
                    f.write_text(new_content, encoding='utf-8')
                    
                    # 删除PDF
                    Path(pdf_path).unlink()
                    
                    print(f"OK ({len(text)} chars)")
                    success += 1
                else:
                    print("FAILED (no PDF)")
                    failed += 1
            except Exception as e:
                print(f"FAILED ({e})")
                failed += 1
            
            time.sleep(5)
        
        print(f"\n完成: 成功{success}, 失败{failed}")
