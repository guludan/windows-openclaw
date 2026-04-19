#!/usr/bin/env python3
"""中国政策/金融数据采集管道 — 多源抓取+去重+JSON输出"""
import requests, re, json, os, time, hashlib, argparse
from datetime import datetime, timedelta

STATE_DIR = os.path.join(os.path.dirname(__file__), ".state")

def _load_seen(source):
    f = os.path.join(STATE_DIR, f"seen_{source}.json")
    try: return set(json.load(open(f)))
    except: return set()

def _save_seen(source, seen):
    f = os.path.join(STATE_DIR, f"seen_{source}.json")
    open(f,"w").write(json.dumps(list(seen)))

def _uid(source, title):
    return hashlib.md5(f"{source}:{title}".encode()).hexdigest()[:12]

# ==================== 央行 (requests直接解析) ====================

def scrape_pbc():
    """央行: 货币政策+金融统计数据"""
    seen = _load_seen("pbc")
    items = []
    urls = [
        ("http://www.pbc.gov.cn/diaochatongjisi/116219/116225/index.html", "金融统计数据"),
        ("http://www.pbc.gov.cn/zhengcehuobisi/125207/125227/index.html", "货币政策"),
        ("http://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html", "新闻发布会"),
    ]
    for url, category in urls:
        try:
            r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
            r.encoding = 'utf-8'
            matches = re.findall(r'<a[^>]*href="([^"#]*)"[^>]*>([^<]{8,})</a>', r.text)
            base = url.rsplit("/", 1)[0]
            for href, title in matches:
                title = title.strip()
                uid = _uid("pbc", title)
                if uid in seen: continue
                full_url = base + "/" + href.lstrip("./") if href.startswith(".") else href
                if not full_url.startswith("http"):
                    full_url = "http://www.pbc.gov.cn" + full_url
                items.append({
                    "source": "央行", "category": category, "title": title,
                    "url": full_url, "id": uid, "time": datetime.now().isoformat()
                })
                seen.add(uid)
        except Exception as e:
            items.append({"source": "央行", "category": category, "error": str(e)})
    _save_seen("pbc", seen)
    return items

# ==================== 财联社 (Playwright) ====================

def scrape_cls():
    """财联社电报快讯 — API直接获取"""
    seen = _load_seen("cls")
    items = []
    try:
        url = "https://www.cls.cn/nodeapi/updateTelegraphList?app=CailianpressWeb&os=web&rn=30&sv=8.4.6&lastTime=0"
        r = requests.get(url, headers={
            "User-Agent":"Mozilla/5.0","Referer":"https://www.cls.cn/telegraph"
        }, timeout=10)
        d = r.json()
        for item in d.get("data",{}).get("roll_data",[]):
            title = item.get("title","") or item.get("content","")
            # 提取摘要（content可能很长，截取前100字）
            content = item.get("content","")
            summary = content[:150].replace("【"+title+"】","").strip() if content else ""
            title = title.strip()
            if not title and summary: title = summary[:80]
            if not title or len(title) < 5: continue
            uid = _uid("cls", item.get("id","") or title)
            if uid in seen: continue
            ctime = item.get("ctime",0)
            dt = datetime.fromtimestamp(int(ctime)).strftime("%Y-%m-%d %H:%M") if ctime else datetime.now().strftime("%Y-%m-%d %H:%M")
            items.append({
                "source": "财联社", "category": "电报快讯",
                "title": title, "summary": summary,
                "url": f"https://www.cls.cn/telegraph/{item.get('id','')}",
                "id": uid, "time": dt
            })
            seen.add(uid)
    except Exception as e:
        items.append({"source": "财联社", "error": str(e)})
    _save_seen("cls", seen)
    return items

# ==================== 证监会 (Playwright) ====================

def scrape_csrc():
    """证监会: 政策法规+监管动态"""
    seen = _load_seen("csrc")
    items = []
    urls = [
        ("http://www.csrc.gov.cn/csrc/c100028/common_list.shtml", "要闻"),
        ("http://www.csrc.gov.cn/csrc/c100029/common_list.shtml", "政策法规"),
    ]
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            for url, category in urls:
                try:
                    page.goto(url, timeout=15000)
                    page.wait_for_selector("li, .list-item, a", timeout=8000)
                    # Try to find news links
                    links = page.query_selector_all("a")
                    for link in links[:30]:
                        try:
                            title = link.inner_text().strip()
                            href = link.get_attribute("href") or ""
                            if not title or len(title) < 8 or len(title) > 100: continue
                            if "javascript" in href or "#" == href: continue
                            uid = _uid("csrc", title)
                            if uid in seen: continue
                            items.append({
                                "source": "证监会", "category": category,
                                "title": title, "url": href, "id": uid,
                                "time": datetime.now().isoformat()
                            })
                            seen.add(uid)
                        except: continue
                except: continue
            browser.close()
    except Exception as e:
        items.append({"source": "证监会", "error": str(e)})
    _save_seen("csrc", seen)
    return items

# ==================== 国家统计局 ====================

def scrape_stats():
    """国家统计局: 经济数据发布"""
    seen = _load_seen("stats")
    items = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            page.goto("http://www.stats.gov.cn/sj/zxfb/", timeout=15000)
            page.wait_for_selector("a, li", timeout=8000)
            links = page.query_selector_all("a")
            for link in links[:30]:
                try:
                    title = link.inner_text().strip()
                    href = link.get_attribute("href") or ""
                    if not title or len(title) < 8: continue
                    if "javascript" in href or "mailto" in href: continue
                    uid = _uid("stats", title)
                    if uid in seen: continue
                    if not href.startswith("http"):
                        href = "http://www.stats.gov.cn" + href
                    items.append({
                        "source": "统计局", "category": "统计数据",
                        "title": title, "url": href, "id": uid,
                        "time": datetime.now().isoformat()
                    })
                    seen.add(uid)
                except: continue
            browser.close()
    except Exception as e:
        items.append({"source": "统计局", "error": str(e)})
    _save_seen("stats", seen)
    return items

# ==================== Pipeline ====================

SOURCES = {
    "pbc": ("央行", scrape_pbc),
    "cls": ("财联社", scrape_cls),
    "csrc": ("证监会", scrape_csrc),
    "stats": ("统计局", scrape_stats),
}

def run(sources=None, output=None):
    """运行采集管道, 返回新条目"""
    src_list = sources or list(SOURCES.keys())
    all_items = []
    for key in src_list:
        if key not in SOURCES: continue
        name, fn = SOURCES[key]
        try:
            items = fn()
            new = [i for i in items if "error" not in i]
            errs = [i for i in items if "error" in i]
            if new: print(f"[{name}] 新增 {len(new)} 条")
            if errs:
                for e in errs: print(f"[{name}] 错误: {e['error']}")
            all_items.extend(new)
        except Exception as e:
            print(f"[{name}] 抓取失败: {e}")
    
    result = {
        "time": datetime.now().isoformat(),
        "count": len(all_items),
        "items": all_items
    }
    if output:
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
        open(output,"w").write(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"\n结果已保存: {output} ({len(all_items)} 条新内容)")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return all_items

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="中国政策金融数据采集管道")
    p.add_argument("--sources", nargs="+", choices=list(SOURCES.keys()), default=["pbc","cls"])
    p.add_argument("--output", "-o", help="输出JSON文件路径")
    p.add_argument("--all", action="store_true", help="采集全部数据源")
    a = p.parse_args()
    if a.all: a.sources = list(SOURCES.keys())
    run(a.sources, a.output)
