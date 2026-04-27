#!/usr/bin/env python3
"""Batch download full-text from Sci-Hub for papers with only metadata."""

import sys, os, re, time, glob, fitz

sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/skills/data-search/scripts')
from data_search import download_scihub_pdf

PERSONALITY_DIRS = [
    "/mnt/d/data/think-tank-data/personalities/psychologists/neuropsychology/V.S._Ramachandran",
    "/mnt/d/data/think-tank-data/personalities/scholars/economics/Kenneth_Rogoff",
    "/mnt/d/data/think-tank-data/personalities/psychologists/neuropsychology/Joseph_LeDoux",
    "/mnt/d/data/think-tank-data/personalities/scholars/actuarial-science/Robert_Merton",
    "/mnt/d/data/think-tank-data/personalities/scholars/financial-data/Marcos_Carreira",
    "/mnt/d/data/think-tank-data/personalities/scholars/actuarial-science/Eugene_Fama",
    "/mnt/d/data/think-tank-data/personalities/scholars/media-studies/Neil_Postman",
    "/mnt/d/data/think-tank-data/personalities/scholars/economics/Milton_Friedman",
    "/mnt/d/data/think-tank-data/personalities/scholars/actuarial-science/William_Sharpe",
    "/mnt/d/data/think-tank-data/personalities/psychologists/evolutionary-psychology/Robert_Sapolsky",
]

PDF_DIR = "/tmp/scihub_pdfs"
os.makedirs(PDF_DIR, exist_ok=True)

stats = {"success": 0, "fail": 0, "skip_no_doi": 0}
details = {"fail": [], "skip_no_doi": []}

for pdir in PERSONALITY_DIRS:
    person = os.path.basename(pdir)
    raw_dir = os.path.join(pdir, "papers", "raw")
    if not os.path.isdir(raw_dir):
        print(f"\n[{person}] no papers/raw dir, skipping")
        continue
    
    txts = sorted(glob.glob(os.path.join(raw_dir, "*.txt")))
    small = [f for f in txts if os.path.getsize(f) < 2048]
    print(f"\n[{person}] {len(small)} small files / {len(txts)} total")
    
    for txt_path in small:
        fname = os.path.basename(txt_path)
        with open(txt_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        if "===== FULLTEXT =====" in content:
            continue
        
        # Extract DOI
        m = re.search(r'# DOI:\s*(10\.\S+)', content) or re.search(r'DOI:\s*(10\.\S+)', content)
        if not m:
            print(f"  [NO DOI] {fname}")
            stats["skip_no_doi"] += 1
            details["skip_no_doi"].append(f"{person}/{fname}")
            continue
        
        doi = m.group(1).strip().rstrip('.')
        print(f"  [TRY] {fname} -> {doi}")
        
        try:
            pdf_path = download_scihub_pdf(doi, PDF_DIR)
            if not pdf_path or not os.path.exists(pdf_path):
                print(f"    [FAIL] download returned None")
                stats["fail"] += 1
                details["fail"].append(f"{person}/{fname} ({doi})")
                time.sleep(15)
                continue
            
            doc = fitz.open(pdf_path)
            fulltext = "\n".join(page.get_text() for page in doc)
            doc.close()
            
            if len(fulltext.strip()) < 200:
                print(f"    [FAIL] text too short ({len(fulltext)} chars)")
                stats["fail"] += 1
                details["fail"].append(f"{person}/{fname} (too short)")
            else:
                new_content = content.rstrip() + "\n\n===== FULLTEXT =====\n\n" + fulltext
                with open(txt_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f"    [OK] {len(fulltext)} chars")
                stats["success"] += 1
            
            os.remove(pdf_path)
            
        except Exception as e:
            print(f"    [ERROR] {e}")
            stats["fail"] += 1
            details["fail"].append(f"{person}/{fname} ({doi}): {e}")
        
        time.sleep(15)

print("\n" + "="*60)
print(f"SUCCESS: {stats['success']}")
print(f"FAIL: {stats['fail']}")
print(f"SKIP (no DOI): {stats['skip_no_doi']}")
print("="*60)
if details["fail"]:
    print("\nFailed:")
    for d in details["fail"]:
        print(f"  - {d}")
if details["skip_no_doi"]:
    print(f"\nSkipped (no DOI): {len(details['skip_no_doi'])} files")
    for d in details["skip_no_doi"][:20]:
        print(f"  - {d}")
