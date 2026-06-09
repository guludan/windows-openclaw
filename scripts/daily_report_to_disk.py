#!/usr/bin/env python3
"""
收盘后把报告复制到 D:/data/a-stock/reports/YYYY-MM-DD/
"""
import shutil, os, sys
from pathlib import Path
from datetime import date

today = date.today().isoformat()

WS = Path.home() / '.openclaw' / 'workspace'
DST = Path('/mnt/d/data/reports') / today

DST.mkdir(parents=True, exist_ok=True)

files = [
    WS / 'memory' / 'reports' / f'a-share-{today}.md',
    WS / 'memory' / 'reports' / f'v8_dashboard_{today}.txt',
    WS / 'memory' / 'reports' / f'neutral_monitor_{today}.md',
    WS / 'memory' / 'reports' / f'选股推荐_{today}.md',
]

copied = 0
for f in files:
    if f.exists():
        shutil.copy2(f, DST / f.name)
        copied += 1
        print(f"  ✅ {f.name}")
    else:
        print(f"  ⚠️ {f.name} 不存在")

print(f"\n已复制 {copied} 个文件到 {DST}")
