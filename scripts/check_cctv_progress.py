#!/usr/bin/env python3
"""检查新闻联播抓取进度"""
import json
from pathlib import Path

cache_dir = Path("/home/tearsyu/.openclaw/workspace/cache/news_cctv")
files = sorted(cache_dir.glob("*.json"))

total_items = 0
total_policy = 0
by_year = {}

for f in files:
    year = f.stem[:4]
    by_year.setdefault(year, {"days": 0, "items": 0, "policy": 0})
    by_year[year]["days"] += 1

    try:
        with open(f, 'r', encoding='utf-8') as fh:
            items = json.load(fh)
        total_items += len(items)
        by_year[year]["items"] += len(items)
        policy_in = sum(1 for it in items if it.get('_is_policy', False))
        total_policy += policy_in
        by_year[year]["policy"] += policy_in
    except:
        pass

print(f"📺 新闻联播抓取进度")
print(f"{'='*50}")
print(f"总缓存文件: {len(files)}")
print(f"总条目: {total_items}")
print(f"政策相关: {total_policy}")
print()
print(f"{'年份':<8} {'天数':<8} {'条目':<8} {'政策':<8} {'覆盖率':<10}")
print("-" * 42)

for year in sorted(by_year.keys()):
    d = by_year[year]
    expected = 366 if year in ('2020', '2024') else 365
    coverage = d['days'] / expected * 100
    print(f"{year:<8} {d['days']:<8} {d['items']:<8} {d['policy']:<8} {coverage:.1f}%")

print(f"\n日志: tail -f /tmp/cctv_crawl.log")
