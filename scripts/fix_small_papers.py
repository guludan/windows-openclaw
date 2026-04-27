#!/usr/bin/env python3
"""补全小论文全文：遍历所有 papers/raw/*.txt <2KB，提取DOI，调用 download_scihub_pdf"""
import sys, os, re, time, glob, fitz
sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/skills/data-search/scripts'))

# API Keys
os.environ['EXA_API_KEY'] = '7be7a599-fa7b-4107-9ef1-ad96646156c2'
os.environ['TAVILY_API_KEY'] = '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'
os.environ['SERPAPI_KEY'] = '848c4ec87cac7776b9404f0af1aeb36fce936d1aa3aebe899664eaa43e67ccc2'

from data_search import download_scihub_pdf

BASE = "/mnt/d/data/think-tank-data/personalities"
PDF_DIR = "/tmp/scihub_pdfs"
os.makedirs(PDF_DIR, exist_ok=True)

small_files = []
for root, dirs, files in os.walk(BASE):
    if not root.endswith("/papers/raw"):
        continue
    for f in files:
        if f.endswith(".txt"):
            fp = os.path.join(root, f)
            if os.path.getsize(fp) < 2048:
                small_files.append(fp)

print(f"待处理: {len(small_files)} 篇")
success = fail = skip = 0

for fp in small_files:
    with open(fp, 'r', encoding='utf-8') as f:
        content = f.read()
    if "===== FULLTEXT =====" in content:
        skip += 1; continue
    m = re.search(r'# DOI:\s*(\S+)', content)
    if not m:
        skip += 1; continue
    doi = m.group(1)
    person = fp.split("/personalities/")[1].split("/")[1] if "/personalities/" in fp else "?"
    print(f"[{person}] {doi[:50]}...", flush=True)
    pdf = download_scihub_pdf(doi, PDF_DIR)
    if pdf and os.path.exists(pdf) and os.path.getsize(pdf) > 1000:
        doc = fitz.open(pdf)
        text = "\n".join(p.get_text() for p in doc)
        doc.close()
        os.remove(pdf)
        if len(text.strip()) > 200:
            with open(fp, 'w', encoding='utf-8') as f:
                f.write(content.rstrip() + "\n\n===== FULLTEXT =====\n\n" + text)
            success += 1
            print(f"  ✓ {len(text)} chars", flush=True)
        else:
            fail += 1; print(f"  ✗ too short", flush=True)
    else:
        fail += 1; print(f"  ✗ download failed", flush=True)
    time.sleep(15)

print(f"\n完成: success={success} fail={fail} skip={skip}")
