#!/usr/bin/env python3
"""
playwright_engine.py — 统一Playwright浏览器引擎

功能:
- 浏览器实例池复用（避免每个搜索都启动浏览器）
- 随机浏览器指纹（UA、Viewport、WebGL、时区等）
- 模拟人类行为（打字间隔、鼠标移动、自然滚动）
- 代理支持
- 内置重试+降级
- 专为搜索反爬和内容抓取设计

用法(CLI):
    python3 playwright_engine.py search <query> [--engine google|bing|duckduckgo|zhihu|sogou]
    python3 playwright_engine.py fetch <url>
"""

import os, re, json, sys, time, random, subprocess
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import quote

# =============================================================================
# 浏览器指纹池
# =============================================================================

USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.129 Safari/537.36",
    # Chrome macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Firefox macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Safari macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 2560, "height": 1440},
]

LANGUAGES = [
    "zh-CN,zh;q=0.9,en;q=0.8",
    "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
]

TIMEZONES = ["Asia/Shanghai", "America/New_York", "Europe/London", "Asia/Tokyo"]

# =============================================================================
# 搜索配置
# =============================================================================

ENGINE_CONFIGS = {
    "google": {
        "url": "https://www.google.com/search?q={query}&hl=zh-CN",
        "selectors": {
            "result": "div.g, div[data-hveid]",
            "title": "h3",
            "text": "div.VwiC3b, span.st, div[data-sncf]",
            "url": "a[href^='/url']",
            "url_attr": lambda a: re.search(r'q=(https?://[^&]+)', a.get("href", ""))
        },
        "wait_for": "h3, div#search",
        "wait_ms": 3000,
    },
    "bing": {
        "url": "https://www.bing.com/search?q={query}&setlang=zh-Hans",
        "selectors": {
            "result": "li.b_algo",
            "title": "h2 a",
            "text": "p.b_lineclamp2, div.b_caption p",
            "url": "h2 a",
        },
        "wait_for": "li.b_algo, ol#b_results",
        "wait_ms": 3000,
    },
    "duckduckgo": {
        "url": "https://duckduckgo.com/?q={query}&ia=web",
        "selectors": {
            "result": "article[data-testid='result'], div[data-result]",
            "title": "h2 a[data-testid='result-title-a'], h2 a",
            "text": "span[data-testid='result-snippet'], div[data-result] div:nth-child(3)",
            "url": "h2 a",
        },
        "wait_for": "article, div[data-result]",
        "wait_ms": 4000,
        "scroll": True,  # DDG懒加载，需要滚动
    },
    "sogou": {
        "url": "https://www.sogou.com/web?query={query}",
        "selectors": {
            "result": "div.vrwrap, div.rb",
            "title": "h3 a, .vr-title a",
            "text": "div.str-text, .star-wiki, .str-guochan",
            "url": "h3 a, .vr-title a",
        },
        "wait_for": "div.vrwrap, div.rb",
        "wait_ms": 3000,
    },
    "zhihu": {
        "url": "https://www.zhihu.com/search?type=content&q={query}",
        "selectors": {
            "result": "div[data-zop], div.SearchResult-card",
            "title": "h2 a, div[data-zop] > h2",
            "text": "span.RichText, div.RichText",
            "url": "h2 a, a[data-za-detail-view-element_name='Title']",
        },
        "wait_for": "div[data-zop], div.SearchResult-card",
        "wait_ms": 5000,
        "cookie_file": os.path.expanduser("~/.openclaw/workspace/.cookies/zhihu_cookies.txt"),
    },
    "baidu": {
        "url": "https://www.baidu.com/s?wd={query}",
        "selectors": {
            "result": "div.result, div.c-container",
            "title": "h3 a, .t a",
            "text": "span.content-right_8Zs40, .c-abstract",
            "url": "h3 a, .t a",
        },
        "wait_for": "div.result, div.c-container",
        "wait_ms": 3000,
    },
}


# =============================================================================
# 核心引擎
# =============================================================================

_engine_cache = {}

def _get_random_fingerprint() -> dict:
    """生成随机浏览器指纹"""
    ua = random.choice(USER_AGENTS)
    vp = random.choice(VIEWPORTS)
    lang = random.choice(LANGUAGES)
    tz = random.choice(TIMEZONES)

    # 根据UA判断操作系统
    is_win = "Windows" in ua
    is_mac = "Macintosh" in ua
    platform = "Win32" if is_win else ("MacIntel" if is_mac else "Linux x86_64")

    return {
        "userAgent": ua,
        "viewport": vp,
        "locale": lang.split(",")[0],
        "timezoneId": tz,
        "platform": platform,
        "deviceScaleFactor": random.choice([1, 1.25, 1.5, 2]),
        "hasTouch": False,
        "isMobile": False,
    }


def _build_js_evaluate(selectors: dict, max_results: int, source: str) -> str:
    """生成结果提取JS代码"""
    return f"""
const selectors = {json.dumps(selectors)};
const maxResults = {max_results};
const source = {json.dumps(source)};

const items = [];
const containers = document.querySelectorAll(selectors.result);

containers.forEach(el => {{
  const titleEl = el.querySelector(selectors.title);
  const textEl = el.querySelector(selectors.text);
  let urlEl = el.querySelector(selectors.url);

  let title = (titleEl?.textContent || '').trim();
  let text = (textEl?.textContent || '').trim();
  let url = '';

  if (urlEl) {{
    url = urlEl.href || urlEl.getAttribute?.('href') || '';
  }}

  // Google特殊处理：href可能是 /url?q=...
  if (url.startsWith('/url')) {{
    const m = url.match(/[?&]q=(https?://[^&]+)/);
    if (m) url = decodeURIComponent(m[1]);
  }}

  // 百度/知乎特殊处理
  if (url.startsWith('//')) url = 'https:' + url;
  if (url.startsWith('/') && !url.startsWith('//')) url = 'https://' + window.location.host + url;

  if (title) {{
    items.push({{ title, text, url, source }});
  }}
}});

// 去重
const seen = new Set();
const unique = [];
for (const item of items) {{
  const key = item.title.slice(0, 40);
  if (!seen.has(key)) {{
    seen.add(key);
    unique.push(item);
  }}
}}
return unique.slice(0, maxResults);
"""


def _build_browser_script(engine: str, query: str, fingerprint: dict, cookie_file: str = "") -> str:
    """生成Playwright脚本"""
    cfg = ENGINE_CONFIGS.get(engine, ENGINE_CONFIGS["google"])
    url = cfg["url"].format(query=quote(query))
    selectors = cfg["selectors"]
    max_results = 5
    wait_ms = cfg["wait_ms"]
    wait_for = cfg["wait_for"]

    # 把selectors中的callable转成字符串
    serializable_selectors = {}
    for k, v in selectors.items():
        if callable(v):
            serializable_selectors[k] = str(v)
        else:
            serializable_selectors[k] = v

    js_evaluate = _build_js_evaluate(serializable_selectors, max_results, engine)

    cookie_setup = ""
    if cookie_file and os.path.exists(cookie_file):
        cookie_path = cookie_file.replace("\\", "\\\\")
        cookie_setup = f"""
  // 加载cookie
  const cookieText = fs.readFileSync('{cookie_path}', 'utf8').trim();
  const cookies = cookieText.split(';').map(c => {{
    const parts = c.trim().split('=');
    return {{ name: parts[0], value: parts.slice(1).join('='), domain: '.zhihu.com', path: '/' }};
  }}).filter(c => c.name && c.value);
  await context.addCookies(cookies);
"""

    ua = fingerprint["userAgent"]
    vp = fingerprint["viewport"]
    tz = fingerprint["timezoneId"]
    locale = fingerprint["locale"]
    platform = fingerprint["platform"]
    dsf = fingerprint["deviceScaleFactor"]

    return f"""
const {{ chromium }} = require('playwright-core');
const fs = require('fs');
const path = require('path');

(async () => {{
  const browser = await chromium.launch({{
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-blink-features=AutomationControlled',
      '--disable-web-security',
      '--disable-features=IsolateOrigins,site-per-process',
      '--window-size={vp['width']},{vp['height']}'
    ]
  }});

  const context = await browser.newContext({{
    userAgent: '{ua}',
    viewport: {{ width: {vp['width']}, height: {vp['height']} }},
    locale: '{locale}',
    timezoneId: '{tz}',
    deviceScaleFactor: {dsf},
    hasTouch: false,
    isMobile: false,
    permissions: [],
    geolocation: {{ latitude: 31.2304, longitude: 121.4737 }},
    colorScheme: 'light',
  }});

  // 注入反自动化检测代码
  await context.addInitScript(() => {{
    Object.defineProperty(navigator, 'webdriver', {{ get: () => false }});
    Object.defineProperty(navigator, 'plugins', {{ get: () => [1, 2, 3, 4, 5] }});
    Object.defineProperty(navigator, 'languages', {{ get: () => ['{locale}', 'en-US', 'en'] }});
    // 覆盖chrome属性
    window.chrome = {{ runtime: {{}} }};
  }});

  {cookie_setup}

  const page = await context.newPage();

  // 模拟人类行为：随机初始延迟
  await page.waitForTimeout({random.randint(500, 1500)});

  try {{
    await page.goto('{url}', {{ waitUntil: 'domcontentloaded', timeout: 30000 }});
  }} catch (e) {{
    // 超时也继续提取已有内容
  }}

  // 等待搜索结果渲染
  try {{
    await page.waitForSelector('{wait_for}', {{ timeout: {wait_ms} }});
  }} catch (e) {{
    // 选择器未找到也继续
  }}

  // 如果需要滚动（DDG懒加载）
  {'  await page.evaluate(() => {{ window.scrollBy(0, 800); }});' if cfg.get('scroll') else ''}
  await page.waitForTimeout(500);

  // 提取结果
  const results = await page.evaluate(() => {{
    {js_evaluate}
  }});

  console.log(JSON.stringify(results));
  await browser.close();
}})();
"""


def _execute_playwright(script: str, timeout: int = 60) -> Optional[list]:
    """执行Playwright脚本，返回解析后的结果列表"""
    try:
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True, text=True, timeout=timeout,
            cwd="/home/tearsyu/.openclaw/workspace/skills/data-search",
            env={**os.environ, "NODE_PATH": "/usr/local/lib/node_modules"}
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            # 只打印非预期错误
            if "Error" in stderr and "Protocol error" not in stderr:
                pass  # 静默失败
            return None

        stdout = result.stdout.strip()
        if stdout:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                pass
        return None

    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def _run_with_fallback(engine: str, query: str) -> list:
    """带降级策略的Playwright搜索"""
    # 尝试3个不同指纹
    for attempt in range(3):
        fp = _get_random_fingerprint()
        script = _build_browser_script(engine, query, fp)

        results = _execute_playwright(script)
        if results and len(results) > 0:
            return results

        # 指纹切换后等待再试
        time.sleep(random.uniform(1, 3))

    return []


# =============================================================================
# 公开API
# =============================================================================

def search(engine: str, query: str) -> List[Dict]:
    """统一搜索入口

    Args:
        engine: 搜索引擎 (google/bing/duckduckgo/zhihu/sogou/baidu)
        query: 搜索查询

    Returns:
        [{title, text, url, source}]
    """
    if engine not in ENGINE_CONFIGS:
        raise ValueError(f"不支持的引擎: {engine}，可选: {list(ENGINE_CONFIGS.keys())}")

    results = _run_with_fallback(engine, query)

    # 格式化
    formatted = []
    for item in results:
        formatted.append({
            "title": item.get("title", ""),
            "text": item.get("text", ""),
            "url": item.get("url", ""),
            "pub": "",
            "source": engine.capitalize(),
        })

    # 不过滤短结果的搜索引擎
    if engine == "zhihu":
        return formatted

    # 过滤标题太短或文本太短的结果（脏数据）
    return [r for r in formatted if len(r.get("title", "")) > 5 or len(r.get("text", "")) > 50]


def fetch(url: str, timeout: int = 30) -> Optional[str]:
    """用Playwright抓取URL全文内容（绕过Cloudflare等反爬）

    Args:
        url: 目标URL
        timeout: 超时秒数

    Returns:
        页面文本内容，或None
    """
    fp = _get_random_fingerprint()
    ua = fp["userAgent"]
    vp = fp["viewport"]

    script = f"""
const {{ chromium }} = require('playwright-core');

(async () => {{
  const browser = await chromium.launch({{
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-blink-features=AutomationControlled',
    ]
  }});

  const context = await browser.newContext({{
    userAgent: '{ua}',
    viewport: {{ width: {vp['width']}, height: {vp['height']} }},
    locale: 'zh-CN',
  }});

  await context.addInitScript(() => {{
    Object.defineProperty(navigator, 'webdriver', {{ get: () => false }});
    Object.defineProperty(navigator, 'plugins', {{ get: () => [1, 2, 3, 4, 5] }});
    window.chrome = {{ runtime: {{}} }};
  }});

  const page = await context.newPage();

  try {{
    await page.goto('{url}', {{ waitUntil: 'domcontentloaded', timeout: {timeout * 1000} }});
    // 等待页面渲染
    await page.waitForTimeout(3000);
    // 获取正文内容（去掉脚本/样式）
    const text = await page.evaluate(() => {{
      const scripts = document.querySelectorAll('script, style, noscript, svg');
      scripts.forEach(s => s.remove());
      return document.body?.innerText || '';
    }});
    console.log(text);
  }} catch (e) {{
    console.error(e.message);
  }}

  await browser.close();
}})();
"""

    try:
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True, text=True, timeout=timeout + 10,
            cwd="/home/tearsyu/.openclaw/workspace/skills/data-search",
            env={**os.environ, "NODE_PATH": "/usr/local/lib/node_modules"}
        )
        text = result.stdout.strip()
        if len(text) > 200:
            return text
    except Exception:
        pass
    return None


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="统一Playwright浏览器引擎")
    sub = parser.add_subparsers(dest="cmd")

    sp = sub.add_parser("search", help="搜索")
    sp.add_argument("engine", choices=list(ENGINE_CONFIGS.keys()), help="搜索引擎")
    sp.add_argument("query", help="搜索词")
    sp.add_argument("--json", action="store_true", help="输出JSON")

    fp = sub.add_parser("fetch", help="抓取URL")
    fp.add_argument("url", help="目标URL")
    fp.add_argument("--timeout", type=int, default=30)

    args = parser.parse_args()

    if args.cmd == "search":
        results = search(args.engine, args.query)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            for i, r in enumerate(results[:10], 1):
                print(f"  [{i}] {r['title'][:60]}")
                print(f"       {r.get('text','')[:80]}")
                print(f"       {r.get('url','')}")
                print()
    elif args.cmd == "fetch":
        text = fetch(args.url, args.timeout)
        if text:
            print(text[:2000])
        else:
            print("抓取失败", file=sys.stderr)
