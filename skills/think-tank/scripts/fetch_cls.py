#!/usr/bin/env python3
"""财联社电报抓取 - Playwright绕过WAF"""
import asyncio, json, time, os, sys, re
from pathlib import Path

OUT_DIR = "/mnt/d/data/conflict-data/cls"
CHROMIUM = "/usr/bin/chromium-browser"

async def scrape_cls(max_scroll=10, scroll_wait=1.5):
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=CHROMIUM)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        
        await page.goto("https://www.cls.cn/telegraph", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        
        for _ in range(max_scroll):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(scroll_wait)
        
        boxes = await page.query_selector_all(".telegraph-content-box")
        results = []
        seen = set()
        
        for box in boxes:
            time_el = await box.query_selector(".telegraph-time-box")
            time_text = (await time_el.inner_text()).strip() if time_el else ""
            
            parent = await box.query_selector(".c-34304b, .c-de0422")
            full_text = (await parent.inner_text()).strip() if parent else ""
            
            strong_el = await box.query_selector("strong")
            title = (await strong_el.inner_text()).strip() if strong_el else full_text[:80]
            
            if title[:40] not in seen:
                seen.add(title[:40])
                results.append({
                    "time": time_text,
                    "title": title,
                    "content": full_text[:800],
                    "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
        
        await browser.close()
        return results

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"📡 财联社电报抓取...")
    
    results = asyncio.run(scrape_cls())
    today = time.strftime("%Y-%m-%d")
    out_file = f"{OUT_DIR}/cls_telegraph_{today}.json"
    
    # Append to daily file
    existing = []
    if os.path.exists(out_file):
        with open(out_file, "r", encoding="utf-8") as f:
            existing = json.load(f)
    
    existing_titles = {r["title"][:40] for r in existing}
    new_count = 0
    for r in results:
        if r["title"][:40] not in existing_titles:
            existing.append(r)
            existing_titles.add(r["title"][:40])
            new_count += 1
    
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 新增 {new_count} 条，累计 {len(existing)} 条 → {out_file}")

if __name__ == "__main__":
    main()
