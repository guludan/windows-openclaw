#!/usr/bin/env python3
"""
Data Search — 统一数据搜索与采集模块

按议题定向从多源拉取数据，自动去重、审计质量、降级处理。
四层搜索架构：通用搜索、中文深度、学术支撑、宏观数据。
增强功能：PubMed搜索、arXiv/SS增强搜索、PDF下载(arXiv+Sci-Hub)、多格式输出。

Author: TDD Implementation
Date: 2026-04-22
"""

import os
import sys
import re
import json
import subprocess
from typing import List, Dict, Any, Optional, Set
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from urllib.parse import quote, urlencode
import time
import re
from pathlib import Path

# Optional dependencies for enhanced academic search
try:
    import arxiv as arxiv_lib
except ImportError:
    arxiv_lib = None

try:
    from semanticscholar import SemanticScholar as SemanticScholarLib
except ImportError:
    SemanticScholarLib = None

try:
    from Bio import Entrez
except ImportError:
    Entrez = None


# =============================================================================
# Configuration
# =============================================================================

ZHIAHU_COOKIE_PATH = os.path.expanduser("~/.openclaw/workspace/.cookies/zhihu_cookies.txt")
MACRO_DATA_PATH = "/mnt/d/data/conflict-data/market/"

# Key轮换：主key失败自动切换备用key（支持 _BACKUP, _BACKUP2 后缀）
_key_cache = {}
def _get_working_key(env_name: str) -> Optional[str]:
    """获取可用的API key，主key失效时自动轮换备用key。"""
    if env_name in _key_cache:
        return _key_cache[env_name]
    for suffix in ["", "_BACKUP", "_BACKUP2"]:
        key = os.environ.get(f"{env_name}{suffix}", "")
        if key:
            _key_cache[env_name] = key
            return key
    _key_cache[env_name] = None
    return None

def _mark_key_failed(env_name: str):
    """标记当前key失效，下次调用轮换到下一个。"""
    current = _key_cache.get(env_name)
    if not current:
        return
    for i, suffix in enumerate(["", "_BACKUP", "_BACKUP2"]):
        candidate = os.environ.get(f"{env_name}{suffix}", "")
        if candidate == current:
            for s2 in ["_BACKUP", "_BACKUP2"]:
                nxt = os.environ.get(f"{env_name}{s2}", "")
                if nxt and nxt != current:
                    _key_cache[env_name] = nxt
                    print(f"[key轮换] {env_name}: 切换备用key", flush=True)
                    return
            _key_cache[env_name] = None
            return

# Source credibility weights (0-1)
SOURCE_CREDIBILITY = {
    # Academic sources
    "arXiv": 1.0,
    "Semantic Scholar": 1.0,
    "PubMed": 1.0,
    # Official/News sources
    "World Bank": 0.9,
    "FRED": 0.9,
    "Reuters": 0.85,
    "Bloomberg": 0.85,
    "财联社": 0.8,
    "新华社": 0.8,
    # General search (prioritized)
    "SearXNG": 0.8,
    "DuckDuckGo": 0.75,
    "Tavily": 0.7,
    "Exa": 0.7,
    "SerpAPI": 0.7,
    # Forums/Social
    "知乎": 0.6,
    "搜狗": 0.6,
    "头条": 0.6,
    "秘塔": 0.65,
    # Others default to 0.5
}

# Macro keyword to indicator mapping
MACRO_KEYWORD_MAP = {
    "通胀": ["CPI", "inflation"],
    "CPI": ["CPI", "inflation"],
    "物价": ["CPI", "inflation"],
    "利率": ["interest_rate", "LPR"],
    "LPR": ["LPR"],
    "加息": ["interest_rate", "federal_funds"],
    "降息": ["interest_rate"],
    "美元": ["dollar_index", "exchange_rate"],
    "汇率": ["exchange_rate"],
    "人民币": ["cny_usd"],
    "股市": ["stock_index"],
    "A股": ["sse Composite Index", "csi300"],
    "上证": ["sse Composite Index"],
    "港股": ["hang_seng"],
    "恒生": ["hang_seng"],
    "黄金": ["gold_price"],
    "金价": ["gold_price"],
    "石油": ["oil_price"],
    "油价": ["oil_price"],
    "原油": ["oil_price", "brent"],
    "失业": ["unemployment_rate"],
    "GDP": ["GDP"],
    "VIX": ["VIX"],
}

# =============================================================================
# Utility Functions
# =============================================================================

def dedup_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    基于标题前40字符去重，保留信息量最大的版本。

    Args:
        results: 搜索结果列表

    Returns:
        去重后的结果列表
    """
    if not results:
        return []

    seen: Dict[str, Dict[str, Any]] = {}

    for item in results:
        title = item.get("title", "")
        # Use first 40 characters as deduplication key
        key = title[:40] if title else ""

        if not key:
            # No title, keep it
            seen[key] = item
            continue

        if key in seen:
            # Keep the one with longer text (more information)
            existing_text = seen[key].get("text", "") or seen[key].get("abstract", "")
            new_text = item.get("text", "") or item.get("abstract", "")

            if len(new_text) > len(existing_text):
                seen[key] = item
        else:
            seen[key] = item

    return list(seen.values())


def score_result(result: Dict[str, Any], query: str) -> float:
    """
    对搜索结果进行质量评分 (0-1)。

    评分维度:
    - 关键词匹配度 (0-0.3)
    - 内容长度 (0-0.3)
    - 来源可信度 (0-0.2)
    - 时效性 (0-0.2)

    Args:
        result: 搜索结果字典
        query: 搜索查询

    Returns:
        质量评分 (0-1)
    """
    score = 0.0

    # 1. Keyword matching (0-0.3)
    title = result.get("title", "").lower()
    text = (result.get("text", "") or result.get("abstract", "")).lower()
    content = title + " " + text

    query_terms = query.lower().split()
    matches = sum(1 for term in query_terms if term in content)
    keyword_score = min(matches / max(len(query_terms), 1), 1.0) * 0.3
    score += keyword_score

    # 2. Content length (0-0.3)
    content_length = len(result.get("text", "") or result.get("abstract", ""))
    # Favor longer content, cap at 500 chars
    length_score = min(content_length / 500, 1.0) * 0.3
    score += length_score

    # 3. Source credibility (0-0.2)
    source = result.get("source", "Unknown")
    credibility = SOURCE_CREDIBILITY.get(source, 0.5)
    score += credibility * 0.2

    # 4. Recency (0-0.2)
    pub_date = result.get("pub", "") or result.get("date", "") or result.get("publishedDate", "") or result.get("year", "")
    recency_score = 0.0

    if pub_date:
        try:
            # Handle different date formats
            if isinstance(pub_date, str) and len(pub_date) >= 4:
                year = int(pub_date[:4])
                current_year = datetime.now().year
                age = current_year - year
                # Newer content gets higher score
                recency_score = max(0, 1 - age / 10) * 0.2
            elif isinstance(pub_date, int):
                current_year = datetime.now().year
                age = current_year - pub_date
                recency_score = max(0, 1 - age / 10) * 0.2
        except (ValueError, TypeError):
            pass

    score += recency_score

    return round(min(score, 1.0), 3)


# =============================================================================
# L1: General Search (SearXNG, DuckDuckGo, Exa, Tavily, SerpAPI)
# =============================================================================

def _search_searxng(query: str, max_results: int = 5, instance_url: str = "http://localhost:8888") -> List[Dict[str, Any]]:
    """
    Search using SearXNG instance (self-hosted metasearch engine).

    Args:
        query: Search query
        max_results: Maximum number of results
        instance_url: SearXNG instance URL (default: localhost:8888)

    Returns:
        List of search results
    """
    try:
        url = f"{instance_url}/search"
        params = {
            "q": query,
            "format": "json",
            "engines": "google,bing,duckduckko",
            "results": max_results
        }

        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()

        results = []
        for item in response.json().get("results", []):
            results.append({
                "title": item.get("title", ""),
                "text": item.get("content", ""),
                "url": item.get("url", ""),
                "pub": "",
                "source": "SearXNG"
            })

        return results[:max_results]

    except Exception:
        return []


def _search_duckduckgo(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search using DuckDuckGo HTML version (requests-based scraping).

    Args:
        query: Search query
        max_results: Maximum number of results

    Returns:
        List of search results
    """
    try:
        url = "https://html.duckduckgo.com/html/"
        params = {"q": query}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()

        # Parse HTML response
        from html.parser import HTMLParser

        class DuckDuckGoParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.results = []
                self.current_result = {}
                self.in_result = False
                self.in_title = False
                self.in_snippet = False
                self.capture_data = False

            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                class_name = attrs_dict.get("class", "")

                if tag == "a" and "result__a" in class_name:
                    self.in_result = True
                    self.current_result = {"url": attrs_dict.get("href", "")}
                    self.in_title = True
                    self.capture_data = True

                if tag == "a" and class_name.startswith("result__snippet"):
                    self.in_snippet = True
                    self.capture_data = True

            def handle_endtag(self, tag):
                if tag == "a" and self.in_title:
                    self.in_title = False
                    self.capture_data = False

                if tag == "a" and self.in_snippet:
                    self.in_snippet = False
                    self.capture_data = False
                    # Finalize result
                    if self.current_result.get("title"):
                        self.current_result["source"] = "DuckDuckGo"
                        self.results.append(self.current_result)
                        self.current_result = {}

            def handle_data(self, data):
                if self.capture_data:
                    data = data.strip()
                    if data:
                        if self.in_title:
                            self.current_result["title"] = self.current_result.get("title", "") + data
                        elif self.in_snippet:
                            self.current_result.setdefault("text", "")

        parser = DuckDuckGoParser()
        parser.feed(response.text)

        return parser.results[:max_results]

    except Exception:
        return []

def _search_exa(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search using Exa API (long-form content focused).

    Args:
        query: Search query
        max_results: Maximum number of results

    Returns:
        List of search results
    """
    api_key = _get_working_key("EXA_API_KEY")
    if not api_key:
        return []

    try:
        url = "https://api.exa.ai/search"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key
        }
        data = {
            "query": query,
            "numResults": max_results,
            "useAutoprompt": True,
            "type": "keyword",
            "contents": {
                "text": True
            }
        }

        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()

        results = []
        for item in response.json().get("results", []):
            results.append({
                "title": item.get("title", ""),
                "text": item.get("text", ""),
                "url": item.get("url", ""),
                "pub": item.get("publishedDate", ""),
                "source": "Exa"
            })

        return results[:max_results]

    except Exception as e:
        return []


def _search_tavily(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search using Tavily API (deep search mode).

    Args:
        query: Search query
        max_results: Maximum number of results

    Returns:
        List of search results
    """
    api_key = _get_working_key("TAVILY_API_KEY")
    if not api_key:
        return []

    try:
        url = "https://api.tavily.com/search"
        headers = {"Content-Type": "application/json"}
        data = {
            "api_key": api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
            "include_answer": False
        }

        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()

        results = []
        for item in response.json().get("results", []):
            results.append({
                "title": item.get("title", ""),
                "text": item.get("content", ""),
                "url": item.get("url", ""),
                "pub": item.get("date", ""),
                "source": "Tavily"
            })

        return results[:max_results]

    except Exception as e:
        return []


def _search_serpapi(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search using SerpAPI (Google search results).

    Args:
        query: Search query
        max_results: Maximum number of results

    Returns:
        List of search results
    """
    api_key = _get_working_key("SERPAPI_KEY")
    if not api_key:
        return []

    try:
        url = "https://serpapi.webscraper.io/search"
        params = {
            "engine": "google",
            "q": query,
            "api_key": api_key,
            "num": max_results
        }

        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()

        results = []
        for item in response.json().get("organic_results", []):
            results.append({
                "title": item.get("title", ""),
                "text": item.get("snippet", ""),
                "url": item.get("link", ""),
                "pub": "",
                "source": "SerpAPI"
            })

        return results[:max_results]

    except Exception as e:
        return []


def search_general(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    L1通用搜索 - 并行调用SearXNG、DuckDuckGo、Exa、Tavily、SerpAPI。

    优先级: SearXNG → DuckDuckGo → Exa → Tavily → SerpAPI
    全部并行执行，能通就用。

    Args:
        query: 搜索查询
        max_results: 每源最多结果数

    Returns:
        合并的搜索结果列表
    """
    all_results = []
    search_funcs = [_search_searxng, _search_duckduckgo, _search_exa, _search_tavily, _search_serpapi]

    # Parallel search with ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(func, query, max_results): func
            for func in search_funcs
        }

        for future in as_completed(futures):
            try:
                results = future.result()
                all_results.extend(results)
            except Exception:
                pass  # Silently skip failed sources

    return all_results


# =============================================================================
# L2: Chinese Deep Search (Zhihu, Sogou, Toutiao, Metaso, Cailian)
# =============================================================================

def _search_zhihu(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search Zhihu using API with cookie authentication.

    Args:
        query: Search query
        max_results: Maximum number of results

    Returns:
        List of search results
    """
    try:
        # Check if cookie file exists
        if not os.path.exists(ZHIAHU_COOKIE_PATH):
            raise Exception("Cookie file not found")

        # Read cookie
        with open(ZHIAHU_COOKIE_PATH, 'r') as f:
            cookie = f.read().strip()

        url = "https://www.zhihu.com/api/v4/search_v3"
        headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0"
        }
        params = {
            "t": "general",
            "q": query,
            "correction": 1,
            "limit": max_results
        }

        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()

        results = []
        for item in response.json().get("data", []):
            obj = item.get("object", {})
            results.append({
                "title": obj.get("title", ""),
                "text": obj.get("excerpt", "") or obj.get("content", ""),
                "url": f"https://zhihu.com{obj.get('url', '')}",
                "pub": "",
                "source": "知乎"
            })

        return results[:max_results]

    except Exception as e:
        raise Exception(f"Zhihu API failed: {e}")


def _playwright_search_base(url_template: str, query: str, source_name: str, max_results: int = 5, selectors: Dict[str, str] = None) -> List[Dict[str, Any]]:
    """
    Base Playwright search function for Chinese sources.

    Args:
        url_template: URL template with {query} placeholder
        query: Search query
        source_name: Name of the source
        max_results: Maximum number of results
        selectors: CSS selectors for title, text, url

    Returns:
        List of search results
    """
    if selectors is None:
        selectors = {
            "result": ".result",
            "title": "h3 a",
            "text": ".c-abstract",
            "url": "h3 a"
        }

    try:
        encoded_query = quote(query)
        url = url_template.format(query=encoded_query)

        # Playwright script as a single command
        script = f"""
const {{ chromium }} = require('playwright-core');
(async () => {{
  const browser = await chromium.launch('/snap/bin/chromium', {{ headless: true }});
  const page = await browser.newPage();
  await page.goto('{url}');
  await page.waitForTimeout(2000);

  const results = await page.evaluate((selectors) => {{
    const items = [];
    document.querySelectorAll(selectors.result).forEach(el => {{
      const titleEl = el.querySelector(selectors.title);
      const textEl = el.querySelector(selectors.text);
      const urlEl = el.querySelector(selectors.url);
      const title = titleEl?.textContent || '';
      const text = textEl?.textContent || '';
      const url = urlEl?.href || '';
      if (title) items.push({{ title, text, url }});
    }});
    return items;
  }}, {json.dumps(selectors)});

  console.log(JSON.stringify(results));
  await browser.close();
}})();
"""

        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=60,
            cwd="/home/tearsyu/.openclaw/workspace/skills/data-search"
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            results = []
            for item in data[:max_results]:
                results.append({
                    "title": item.get("title", ""),
                    "text": item.get("text", ""),
                    "url": item.get("url", ""),
                    "pub": "",
                    "source": source_name
                })
            return results

        return []

    except Exception:
        return []


def _search_sogou(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search Sogou using Playwright.

    Args:
        query: Search query
        max_results: Maximum number of results

    Returns:
        List of search results
    """
    return _playwright_search_base(
        url_template="https://www.sogou.com/web?query={query}",
        query=query,
        source_name="搜狗",
        max_results=max_results,
        selectors={
            "result": ".results > div",
            "title": "h3 a",
            "text": ".str_info",
            "url": "h3 a"
        }
    )


def _search_toutiao(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search Toutiao using Playwright.

    Args:
        query: Search query
        max_results: Maximum number of results

    Returns:
        List of search results
    """
    return _playwright_search_base(
        url_template="https://so.toutiao.com/search?keyword={query}",
        query=query,
        source_name="头条",
        max_results=max_results,
        selectors={
            "result": ".result",
            "title": "a",
            "text": ".abstract",
            "url": "a"
        }
    )


def _search_metaso(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search Metaso (秘塔AI搜索) using Playwright.

    Args:
        query: Search query
        max_results: Maximum number of results

    Returns:
        List of search results with AI summaries
    """
    return _playwright_search_base(
        url_template="https://metaso.cn/?q={query}",
        query=query,
        source_name="秘塔",
        max_results=max_results,
        selectors={
            "result": ".search-result",
            "title": "h3",
            "text": ".summary",
            "url": "a"
        }
    )


def _search_cls(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search Cailian Press using AKShare.

    Args:
        query: Search query (not used, returns latest headlines)
        max_results: Maximum number of results

    Returns:
        List of search results
    """
    try:
        import akshare as ak

        df = ak.stock_info_global_cls()
        results = []

        for _, row in df.head(max_results).iterrows():
            pub_val = row.get("发布时间", row.get("datetime", ""))
            if hasattr(pub_val, 'strftime'):
                pub_val = pub_val.strftime("%Y-%m-%d %H:%M:%S")
            elif not isinstance(pub_val, str):
                pub_val = str(pub_val)
            results.append({
                "title": str(row.get("新闻标题", row.get("title", ""))),
                "text": str(row.get("新闻内容", row.get("content", ""))),
                "url": str(row.get("新闻链接", row.get("url", ""))),
                "pub": pub_val,
                "source": "财联社"
            })

        return results

    except Exception:
        return []


def search_chinese(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    L2中文深度搜索 - 知乎 → 搜狗(Playwright) → 头条(Playwright) → 秘塔(Playwright) → 财联社。

    降级策略:
    1. 知乎API: 有cookie直接调，401则降级
    2. Playwright源（搜狗/头条/秘塔）: 并行执行，统一无头浏览器实例
    3. 财联社: AKShare免费接口兜底

    Args:
        query: 搜索查询
        max_results: 每源最多结果数

    Returns:
        合并的搜索结果列表
    """
    all_results = []

    # Try Zhihu first
    try:
        results = _search_zhihu(query, max_results)
        all_results.extend(results)
    except Exception:
        pass  # Zhihu failed, continue to Playwright sources

    # Parallel execution of Playwright sources and Cailian
    playwright_funcs = [_search_sogou, _search_toutiao, _search_metaso, _search_cls]

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(func, query, max_results): func
            for func in playwright_funcs
        }

        for future in as_completed(futures):
            try:
                results = future.result()
                all_results.extend(results)
            except Exception:
                pass  # Silently skip failed sources

    return all_results


# =============================================================================
# L3: Academic Search (arXiv, Semantic Scholar, PubMed)
# =============================================================================

def _search_arxiv(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search arXiv for academic papers.

    Args:
        query: Search query
        max_results: Maximum number of results

    Returns:
        List of paper results
    """
    try:
        url = "http://export.arxiv.org/api/query"
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results
        }

        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()

        # Parse XML response
        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.text)

        # Namespace for arXiv
        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

        results = []
        for entry in root.findall("atom:entry", ns):
            title = entry.find("atom:title", ns).text.strip()
            summary = entry.find("atom:summary", ns).text.strip()
            url = entry.find("atom:id", ns).text
            published = entry.find("atom:published", ns).text[:10] if entry.find("atom:published", ns) is not None else ""

            # Extract authors
            authors = []
            for author in entry.findall("atom:author", ns):
                name = author.find("atom:name", ns)
                if name is not None:
                    authors.append(name.text)

            results.append({
                "title": title,
                "abstract": summary,
                "text": summary,  # For consistency
                "url": url,
                "authors": authors,
                "year": published[:4] if published else "",
                "pub": published,
                "source": "arXiv"
            })

        return results[:max_results]

    except Exception:
        return []




def _search_arxiv_enhanced(
    query: str, max_results: int = 10, category: Optional[str] = None,
    author: Optional[str] = None, year: Optional[int] = None,
    start_date: Optional[str] = None, end_date: Optional[str] = None,
    sort_by: str = "relevance"
) -> List[Dict[str, Any]]:
    """Search arXiv using arxiv library (enhanced, requires pip install arxiv)."""
    if arxiv_lib is None:
        return []

    search_query = query
    if category:
        search_query = f"cat:{category} AND {query}"
    if author:
        search_query = f"{search_query} AND au:{author}"

    sort_order = arxiv_lib.SortCriterion.Relevance
    if sort_by == "date":
        sort_order = arxiv_lib.SortCriterion.SubmittedDate

    try:
        search = arxiv_lib.Search(query=search_query, max_results=max_results, sort_by=sort_order)
        results = []
        for paper in search.results():
            pub_date = paper.published.date()
            if year and pub_date.year != year:
                continue
            if start_date:
                start = datetime.strptime(start_date, "%Y-%m-%d").date()
                if pub_date < start:
                    continue
            if end_date:
                end = datetime.strptime(end_date, "%Y-%m-%d").date()
                if pub_date > end:
                    continue
            results.append({
                "title": paper.title,
                "authors": [a.name for a in paper.authors],
                "published": paper.published.strftime("%Y-%m-%d"),
                "updated": paper.updated.strftime("%Y-%m-%d"),
                "arxiv_id": paper.entry_id.split("/")[-1],
                "categories": paper.categories,
                "abstract": paper.summary,
                "text": paper.summary,
                "url": paper.entry_id,
                "pdf_url": paper.pdf_url,
                "doi": paper.doi,
                "year": paper.published.strftime("%Y"),
                "pub": paper.published.strftime("%Y-%m-%d"),
                "source": "arXiv"
            })
        return results
    except Exception:
        return []





def _search_semantic_scholar(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search Semantic Scholar for academic papers.

    Args:
        query: Search query
        max_results: Maximum number of results

    Returns:
        List of paper results
    """
    try:
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {
            "query": query,
            "limit": max_results,
            "fields": "title,abstract,url,year,authors"
        }

        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()

        results = []
        for item in response.json().get("data", []):
            authors = [a.get("name", "") for a in item.get("authors", [])]

            results.append({
                "title": item.get("title", ""),
                "abstract": item.get("abstract", ""),
                "text": item.get("abstract", ""),
                "url": item.get("url", ""),
                "authors": authors,
                "year": str(item.get("year", "")),
                "pub": str(item.get("year", "")),
                "source": "Semantic Scholar"
            })

        return results[:max_results]

    except Exception:
        return []


def _search_semantic_enhanced(
    query: str, max_results: int = 10, year: Optional[int] = None,
    min_citations: Optional[int] = None, author: Optional[str] = None,
    sort_by: str = "relevance"
) -> List[Dict[str, Any]]:
    """Search Semantic Scholar using semanticscholar library (enhanced)."""
    if SemanticScholarLib is None:
        return []

    def _safe_attr(obj, attr, default=None):
        if hasattr(obj, attr):
            val = getattr(obj, attr, default)
            if hasattr(val, 'name'):
                return val.name
            return val
        return default

    try:
        sch = SemanticScholarLib()
        fields = ["title", "authors", "year", "citationCount", "influentialCitationCount",
                  "fieldsOfStudy", "abstract", "externalIds", "openAccessPdf", "url"]
        results = list(sch.search_paper(query, limit=max_results, fields=fields))

        papers = []
        for paper in results:
            py = _safe_attr(paper, 'year')
            if year and py != year:
                continue
            cite = _safe_attr(paper, 'citationCount') or 0
            if min_citations and cite < min_citations:
                continue
            authors_list = _safe_attr(paper, 'authors') or []
            if author and authors_list:
                if not any(author.lower() in (_safe_attr(a, 'name') or '').lower() for a in authors_list):
                    continue

            authors = [_safe_attr(a, 'name') for a in authors_list]
            ext_ids = _safe_attr(paper, 'externalIds') or {}
            doi = ext_ids.get('DOI', '') if isinstance(ext_ids, dict) else ''
            oa_pdf = _safe_attr(paper, 'openAccessPdf')
            pdf_url = oa_pdf.get('url') if isinstance(oa_pdf, dict) else None

            papers.append({
                "title": _safe_attr(paper, 'title') or '',
                "authors": authors,
                "published": str(py) if py else "Unknown",
                "paper_id": _safe_attr(paper, 'paperId') or '',
                "citations": cite,
                "influential_citations": _safe_attr(paper, 'influentialCitationCount') or 0,
                "fields": _safe_attr(paper, 'fieldsOfStudy') or [],
                "abstract": _safe_attr(paper, 'abstract') or '',
                "text": _safe_attr(paper, 'abstract') or '',
                "doi": doi,
                "arxiv_id": ext_ids.get('ArXiv') if isinstance(ext_ids, dict) else None,
                "url": _safe_attr(paper, 'url') or f"https://www.semanticscholar.org/paper/{_safe_attr(paper, 'paperId')}",
                "pdf_url": pdf_url,
                "year": str(py) if py else "",
                "pub": str(py) if py else "",
                "source": "Semantic Scholar"
            })

        if sort_by == "citations":
            papers.sort(key=lambda p: p["citations"], reverse=True)
        elif sort_by == "date":
            papers.sort(key=lambda p: p["published"], reverse=True)

        return papers[:max_results]
    except Exception:
        return []


def _search_pubmed(
    query: str, max_results: int = 10,
    start_date: Optional[str] = None, end_date: Optional[str] = None,
    publication_type: Optional[str] = None, author: Optional[str] = None,
    email: str = "user@example.com"
) -> List[Dict[str, Any]]:
    """Search PubMed using biopython Entrez (optional, requires pip install biopython)."""
    if Entrez is None:
        return []

    Entrez.email = email
    search_query = query
    if publication_type:
        search_query = f"{search_query} AND {publication_type}[Publication Type]"
    if author:
        search_query = f"{search_query} AND {author}[Author]"

    date_filter = ""
    if start_date and end_date:
        date_filter = f"{start_date}:{end_date}[Date - Publication]"
    elif start_date:
        date_filter = f"{start_date}:3000[Date - Publication]"
    elif end_date:
        date_filter = f"1900:{end_date}[Date - Publication]"
    if date_filter:
        search_query = f"{search_query} AND {date_filter}"

    try:
        handle = Entrez.esearch(db="pubmed", term=search_query, retmax=max_results)
        record = Entrez.read(handle)
        handle.close()
        pmids = record["IdList"]
        if not pmids:
            return []

        results = []
        for pmid in pmids:
            try:
                handle = Entrez.efetch(db="pubmed", id=pmid, rettype="abstract", retmode="xml")
                rec = Entrez.read(handle)
                handle.close()
                article = rec["PubmedArticle"][0]["MedlineCitation"]["Article"]

                authors = []
                if "AuthorList" in article:
                    for a in article["AuthorList"]:
                        if "LastName" in a and "Initials" in a:
                            authors.append(f"{a['LastName']} {a['Initials']}")

                abstract = ""
                if "Abstract" in article and "AbstractText" in article["Abstract"]:
                    abstract = " ".join(str(t) for t in article["Abstract"]["AbstractText"])

                pub_date = ""
                if "Journal" in article and "JournalIssue" in article["Journal"]:
                    pd = article["Journal"]["JournalIssue"].get("PubDate", {})
                    pub_date = f"{pd.get('Year', '')}-{pd.get('Month', '')}-{pd.get('Day', '')}".strip("-")

                doi = ""
                if "ELocationID" in article:
                    for eid in article["ELocationID"]:
                        if eid.attributes.get("EIdType") == "doi":
                            doi = str(eid)

                results.append({
                    "title": str(article.get("ArticleTitle", "No title")),
                    "authors": authors,
                    "journal": str(article.get("Journal", {}).get("Title", "")),
                    "published": pub_date,
                    "pub": pub_date,
                    "pmid": pmid,
                    "doi": doi,
                    "abstract": abstract,
                    "text": abstract,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    "year": pub_date[:4] if pub_date else "",
                    "source": "PubMed"
                })
            except Exception:
                continue
        return results[:max_results]
    except Exception:
        return []


def search_academic(query: str, max_results: int = 5, **kwargs) -> List[Dict[str, Any]]:
    """
    L3学术搜索 - 并行调用arXiv、Semantic Scholar、PubMed。
    增强版（有库）降级到简单版（HTTP API）。

    Args:
        query: 搜索查询
        max_results: 每源最多结果数
        **kwargs: 传递给增强搜索的额外参数(category, author, year, etc.)

    Returns:
        合并的论文结果列表
    """
    all_results = []

    # arXiv: try enhanced first, fallback to simple
    def _arxiv_search():
        enhanced = _search_arxiv_enhanced(query, max_results, **{k: v for k, v in kwargs.items()
                      if k in ('category', 'author', 'year', 'start_date', 'end_date', 'sort_by')})
        return enhanced if enhanced else _search_arxiv(query, max_results)

    # SS: try enhanced first, fallback to simple
    def _ss_search():
        enhanced = _search_semantic_enhanced(query, max_results, **{k: v for k, v in kwargs.items()
                      if k in ('year', 'min_citations', 'author', 'sort_by')})
        return enhanced if enhanced else _search_semantic_scholar(query, max_results)

    # PubMed: only available with biopython
    search_funcs = [_arxiv_search, _ss_search]
    if Entrez is not None:
        search_funcs.append(lambda: _search_pubmed(query, max_results,
            **{k: v for k, v in kwargs.items() if k in ('start_date', 'end_date', 'publication_type', 'author')}))

    with ThreadPoolExecutor(max_workers=len(search_funcs)) as executor:
        futures = {executor.submit(func): func for func in search_funcs}
        for future in as_completed(futures):
            try:
                results = future.result()
                all_results.extend(results)
            except Exception:
                pass

    return all_results


# =============================================================================
# L4: Macro Data Search (World Bank, AKShare, FRED, Local)
# =============================================================================

def _map_keywords_to_indicators(keywords: str) -> List[str]:
    """
    将关键词映射到宏观数据指标。

    Args:
        keywords: 关键词字符串

    Returns:
        匹配的指标列表
    """
    indicators = set()

    for keyword, mapping in MACRO_KEYWORD_MAP.items():
        if keyword.lower() in keywords.lower():
            indicators.update(mapping)

    return list(indicators)


def _search_world_bank(indicators: List[str], max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search World Bank for macro indicators.

    Args:
        indicators: List of indicator codes or names
        max_results: Maximum number of results

    Returns:
        List of macro data points
    """
    try:
        results = []

        for indicator in indicators[:max_results]:
            url = f"https://api.worldbank.org/v2/country/all/indicator/{indicator}"
            params = {
                "format": "json",
                "per_page": 1,
                "date": "2023:2024"
            }

            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            if len(data) > 1 and data[1]:
                item = data[1][0]
                results.append({
                    "indicator": item.get("indicator", {}).get("value", indicator),
                    "date": item.get("date", ""),
                    "value": str(item.get("value", "")),
                    "source": "World Bank"
                })

        return results[:max_results]

    except Exception:
        return []


def _search_akshare(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search AKShare for Chinese market data.

    Args:
        query: Search keywords
        max_results: Maximum number of results

    Returns:
        List of macro data points
    """
    try:
        import akshare as ak
        results = []

        # Map query to AKShare functions
        if any(kw in query.lower() for kw in ["lpr", "利率"]):
            df = ak.rate_interest_bank(china=True)
            for _, row in df.head(max_results).iterrows():
                results.append({
                    "indicator": "中国LPR",
                    "date": str(row.get("日期", "")),
                    "value": str(row.get("利率", "")),
                    "source": "AKShare"
                })

        elif any(kw in query.lower() for kw in ["上证", "a股", "股市"]):
            df = ak.stock_zh_index_daily(symbol="sh000001")
            for _, row in df.tail(max_results).iterrows():
                results.append({
                    "indicator": "上证指数",
                    "date": str(row.get("date", "")),
                    "value": str(row.get("close", "")),
                    "source": "AKShare"
                })

        elif any(kw in query.lower() for kw in ["恒生", "港股"]):
            df = ak.stock_hk_index_daily(symbol="HSI")
            for _, row in df.tail(max_results).iterrows():
                results.append({
                    "indicator": "恒生指数",
                    "date": str(row.get("date", "")),
                    "value": str(row.get("close", "")),
                    "source": "AKShare"
                })

        return results[:max_results]

    except Exception:
        return []


def _search_fred(indicators: List[str], max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search FRED (Federal Reserve Economic Data) for US macro indicators.

    Args:
        indicators: List of indicator keywords
        max_results: Maximum number of results

    Returns:
        List of macro data points
    """
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        return []

    try:
        results = []

        for indicator in indicators[:max_results]:
            # Search for series
            url = "https://api.stlouisfed.org/fred/series/search"
            params = {
                "api_key": api_key,
                "search_text": indicator,
                "file_type": "json",
                "limit": 1
            }

            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            if data.get("seriess"):
                series = data["seriess"][0]
                series_id = series["id"]

                # Get latest observation
                obs_url = "https://api.stlouisfed.org/fred/series/observations"
                obs_params = {
                    "api_key": api_key,
                    "series_id": series_id,
                    "file_type": "json",
                    "limit": 1,
                    "sort_order": "desc"
                }

                obs_response = requests.get(obs_url, params=obs_params, timeout=30)
                obs_response.raise_for_status()

                obs_data = obs_response.json()
                if obs_data.get("observations"):
                    obs = obs_data["observations"][0]
                    results.append({
                        "indicator": series["title"],
                        "date": obs["date"],
                        "value": obs["value"],
                        "source": "FRED"
                    })

        return results[:max_results]

    except Exception:
        return []


def _search_macro_local(indicators: List[str], max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search local JSON files for cached macro data.

    Args:
        indicators: List of indicator keywords
        max_results: Maximum number of results

    Returns:
        List of macro data points
    """
    try:
        results = []

        # Check if macro data directory exists
        if not os.path.exists(MACRO_DATA_PATH):
            return results

        # Search for matching JSON files
        for filename in os.listdir(MACRO_DATA_PATH):
            if not filename.endswith(".json"):
                continue

            filepath = os.path.join(MACRO_DATA_PATH, filename)

            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Look for matching indicators
            for indicator in indicators:
                if indicator.lower() in filename.lower() or indicator.lower() in str(data).lower():
                    # Extract data points
                    if isinstance(data, dict):
                        for key, value in data.items():
                            if len(results) >= max_results:
                                break

                            if isinstance(value, dict) and "value" in value:
                                results.append({
                                    "indicator": indicator,
                                    "date": value.get("date", ""),
                                    "value": str(value.get("value", "")),
                                    "source": "Local"
                                })
                            elif isinstance(value, (int, float, str)) and key != "date":
                                results.append({
                                    "indicator": indicator,
                                    "date": data.get("date", ""),
                                    "value": str(value),
                                    "source": "Local"
                                })

            if len(results) >= max_results:
                break

        return results[:max_results]

    except Exception:
        return []


def search_macro(keywords: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    L4宏观数据搜索 - World Bank → AKShare → FRED → Local JSON。

    Args:
        keywords: 关键词字符串（用于映射指标）
        max_results: 每源最多结果数

    Returns:
        合并的宏观数据列表
    """
    all_results = []

    # Map keywords to indicators
    indicators = _map_keywords_to_indicators(keywords)

    if not indicators:
        # Try direct keyword matching
        indicators = keywords.split()

    # Try all sources in parallel
    search_funcs = [
        lambda inds=indicators: _search_world_bank(inds, max_results),
        lambda kw=keywords: _search_akshare(kw, max_results),
        lambda inds=indicators: _search_fred(inds, max_results),
        lambda inds=indicators: _search_macro_local(inds, max_results),
    ]

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(func): func for func in search_funcs}

        for future in as_completed(futures):
            try:
                results = future.result()
                all_results.extend(results)
            except Exception:
                pass

    return all_results


# =============================================================================
# PDF Download Functions
# =============================================================================

def download_arxiv_papers(papers: List[Dict[str, Any]], output_dir: str) -> int:
    """Download arXiv papers as PDFs. Returns count of downloaded papers."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    for paper in papers:
        pdf_url = paper.get("pdf_url") or f"https://arxiv.org/pdf/{paper.get('arxiv_id', '')}.pdf"
        if not pdf_url:
            continue
        arxiv_id = paper.get("arxiv_id", "unknown")
        title = paper.get("title", "")[:100]
        filename = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
        filepath = output_path / f"{arxiv_id}_{filename}.pdf"
        try:
            response = requests.get(pdf_url, timeout=30)
            response.raise_for_status()
            with open(filepath, "wb") as f:
                f.write(response.content)
            print(f"Downloaded: {filepath.name}", file=sys.stderr)
            downloaded += 1
        except Exception as e:
            print(f"Failed to download {arxiv_id}: {e}", file=sys.stderr)
    return downloaded


def download_scihub_pdf(doi: str, output_dir: str, mirrors: Optional[List[str]] = None) -> Optional[str]:
    """Download PDF from Sci-Hub mirrors given a DOI.
    Returns path to downloaded PDF if successful, None otherwise."""
    if mirrors is None:
        mirrors = ["https://sci-hub.ru", "https://sci-hub.in"]

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    clean_doi = re.sub(r'[^\w\.-]', '_', doi)
    filepath = output_path / f"{clean_doi}.pdf"

    for mirror in mirrors:
        try:
            url = f"{mirror}/{doi}"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
            response.raise_for_status()
            content_type = response.headers.get('Content-Type', '').lower()

            if 'application/pdf' in content_type and len(response.content) > 1000:
                with open(filepath, 'wb') as f:
                    f.write(response.content)
                print(f"Downloaded: {filepath.name}", file=sys.stderr)
                return str(filepath)

            if 'text/html' in content_type:
                pdf_match = re.search(r'citation_pdf_url.*?content="([^"]+\.pdf)"', response.text)
                if not pdf_match:
                    pdf_match = re.search(r'(?:src|href)="([^"]*\.pdf[^"]*)"', response.text)
                if pdf_match:
                    pdf_url = pdf_match.group(1)
                    if not pdf_url.startswith('http'):
                        pdf_url = f"{mirror}/{pdf_url.lstrip('/')}"
                    pdf_response = requests.get(pdf_url, headers=headers, timeout=30)
                    pdf_response.raise_for_status()
                    if len(pdf_response.content) > 1000 and b'%PDF' in pdf_response.content[:100]:
                        with open(filepath, 'wb') as f:
                            f.write(pdf_response.content)
                        print(f"Downloaded: {filepath.name}", file=sys.stderr)
                        return str(filepath)
        except Exception as e:
            print(f"Failed {mirror}: {e}", file=sys.stderr)
            continue

    print(f"Failed to download DOI {doi} from all mirrors", file=sys.stderr)
    return None


def download_book(title: str, output_dir: str, isbn: Optional[str] = None,
                  proxies: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Download book and extract text from Library Genesis or Internet Archive.
    
    Fallback chain: libgen.com.im → archive.org → None
    Downloads PDF then extracts text via PyMuPDF, saves as .txt
    
    Args:
        title: Book title to search
        output_dir: Directory to save .txt
        isbn: Optional ISBN for more precise search
        proxies: Optional proxy dict, e.g. {"https": "http://172.26.0.1:10808"}
    
    Returns:
        Path to extracted .txt if successful, None otherwise
    """
    try:
        import fitz
    except ImportError:
        print("PyMuPDF (fitz) required for book download", file=sys.stderr)
        return None
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r'[^\w\s.-]', '_', title)[:60]
    txt_path = output_path / f"book_{safe_title}.txt"
    
    if txt_path.exists() and txt_path.stat().st_size > 5000:
        print(f"Book already exists: {txt_path.name}", file=sys.stderr)
        return str(txt_path)
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    # --- libgen.com.im ---
    try:
        search_url = "https://libgen.com.im/search.php"
        params = {"q": isbn if isbn else title}
        
        resp = requests.get(search_url, params=params, headers=headers, timeout=15, proxies=proxies)
        resp.raise_for_status()
        
        # Extract all md5s, try each
        md5s = re.findall(r'download\.php\?md5=([a-f0-9]{32})', resp.text)
        for md5 in md5s[:3]:  # try top 3 results
            try:
                dl_url = f"https://libgen.com.im/download.php?md5={md5}"
                dl_resp = requests.get(dl_url, headers=headers, timeout=60,
                                        allow_redirects=True, proxies=proxies)
                if dl_resp.status_code != 200 or len(dl_resp.content) < 50000:
                    continue
                
                # Save temp PDF, extract text
                tmp_pdf = output_path / f"_tmp_{md5}.pdf"
                with open(tmp_pdf, 'wb') as f:
                    f.write(dl_resp.content)
                
                doc = fitz.open(str(tmp_pdf))
                text = "\n".join(page.get_text() for page in doc)
                doc.close()
                tmp_pdf.unlink()
                
                if len(text.strip()) > 1000:
                    with open(txt_path, 'w', encoding='utf-8') as f:
                        f.write(f"title: {title}\nsource: libgen\ndate_added: {time.strftime('%Y-%m-%d')}\n\n{text}")
                    print(f"Downloaded book: {txt_path.name} ({len(text)} chars)", file=sys.stderr)
                    return str(txt_path)
            except Exception as e:
                print(f"  md5 {md5[:8]}... failed: {e}", file=sys.stderr)
                continue
    except Exception as e:
        print(f"libgen.com.im failed: {e}", file=sys.stderr)
    
    # --- Internet Archive ---
    try:
        search_url = "https://archive.org/advancedsearch.php"
        params = {
            "q": f'title:"{title}"',
            "fl[]": "identifier",
            "output": "json",
            "rows": 3
        }
        resp = requests.get(search_url, params=params, timeout=15, proxies=proxies)
        resp.raise_for_status()
        docs = resp.json().get("response", {}).get("docs", [])
        
        for doc in docs:
            identifier = doc.get("identifier", "")
            if not identifier:
                continue
            pdf_url = f"https://archive.org/download/{identifier}/{identifier}.pdf"
            dl_resp = requests.get(pdf_url, headers=headers,
                                    timeout=60, allow_redirects=True, proxies=proxies)
            if dl_resp.status_code != 200 or len(dl_resp.content) < 50000:
                continue
            
            tmp_pdf = output_path / f"_tmp_{identifier}.pdf"
            with open(tmp_pdf, 'wb') as f:
                f.write(dl_resp.content)
            
            doc = fitz.open(str(tmp_pdf))
            text = "\n".join(page.get_text() for page in doc)
            doc.close()
            tmp_pdf.unlink()
            
            if len(text.strip()) > 1000:
                with open(txt_path, 'w', encoding='utf-8') as f:
                    f.write(f"title: {title}\nsource: archive.org\ndate_added: {time.strftime('%Y-%m-%d')}\n\n{text}")
                print(f"Downloaded book from archive.org: {txt_path.name}", file=sys.stderr)
                return str(txt_path)
    except Exception as e:
        print(f"archive.org failed: {e}", file=sys.stderr)
    
    print(f"Failed to download book: {title}", file=sys.stderr)
    return None


def download_papers(
    papers: Optional[List[Dict[str, Any]]] = None,
    doi: Optional[str] = None,
    output_dir: str = "downloads"
) -> List[Dict[str, Any]]:
    """Unified paper download: arXiv direct + Sci-Hub for others.

    Args:
        papers: List of paper dicts (with arxiv_id or doi)
        doi: Direct DOI to download via Sci-Hub
        output_dir: Directory for PDFs

    Returns:
        List of results with pdf_url added
    """
    results = []

    # Direct DOI download
    if doi:
        pdf_path = download_scihub_pdf(doi, output_dir)
        results.append({"doi": doi, "pdf_url": pdf_path, "source": "scihub"})
        return results

    if not papers:
        return results

    for paper in papers:
        result = dict(paper)
        # arXiv papers: download directly
        arxiv_id = paper.get("arxiv_id")
        if arxiv_id and not paper.get("pdf_url"):
            paper["pdf_url"] = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        if paper.get("pdf_url") and arxiv_id:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            filepath = output_path / f"{arxiv_id}.pdf"
            try:
                resp = requests.get(paper["pdf_url"], timeout=30)
                resp.raise_for_status()
                with open(filepath, "wb") as f:
                    f.write(resp.content)
                result["pdf_url"] = str(filepath)
                print(f"Downloaded: {filepath.name}", file=sys.stderr)
            except Exception as e:
                print(f"Failed arXiv {arxiv_id}: {e}", file=sys.stderr)
                result["pdf_url"] = None
        # Non-arXiv with DOI: try Sci-Hub
        elif paper.get("doi"):
            time.sleep(15)  # Rate limiting
            pdf_path = download_scihub_pdf(paper["doi"], output_dir)
            result["pdf_url"] = pdf_path

        results.append(result)

    return results


# =============================================================================
# Output Formatting Functions
# =============================================================================

def format_results(papers: List[Dict[str, Any]], fmt: str = "json", source: str = "") -> str:
    """Format search results in specified format.

    Args:
        papers: List of paper/result dicts
        fmt: One of 'text', 'json', 'bibtex', 'ris', 'markdown'
        source: Source hint for formatting

    Returns:
        Formatted string
    """
    if fmt == "json":
        return json.dumps(papers, indent=2, ensure_ascii=False)

    if fmt == "text":
        return _format_text(papers, source)
    if fmt == "markdown":
        return _format_markdown(papers, source)
    if fmt == "bibtex":
        return _format_bibtex(papers)
    if fmt == "ris":
        return _format_ris(papers)
    return json.dumps(papers, indent=2, ensure_ascii=False)


def _format_text(papers: List[Dict[str, Any]], source: str = "") -> str:
    if not papers:
        return "No results found."
    lines = [f"Search Results: {len(papers)} papers found\n"]
    for i, p in enumerate(papers, 1):
        lines.append(f"\n{i}. {p.get('title', 'N/A')}")
        if p.get("authors"):
            authors = ", ".join(p["authors"][:5])
            if len(p["authors"]) > 5:
                authors += " et al."
            lines.append(f"   Authors: {authors}")
        if p.get("published"):
            lines.append(f"   Published: {p['published']}")
        if p.get("arxiv_id"):
            lines.append(f"   arXiv ID: {p['arxiv_id']}")
        if p.get("pmid"):
            lines.append(f"   PMID: {p['pmid']}")
        if p.get("doi"):
            lines.append(f"   DOI: {p['doi']}")
        if p.get("citations"):
            lines.append(f"   Citations: {p['citations']}")
        if p.get("abstract"):
            lines.append(f"   Abstract: {p['abstract'][:300]}{'...' if len(p['abstract']) > 300 else ''}")
        if p.get("url"):
            lines.append(f"   URL: {p['url']}")
        if p.get("pdf_url"):
            lines.append(f"   PDF: {p['pdf_url']}")
    return "\n".join(lines)


def _format_markdown(papers: List[Dict[str, Any]], source: str = "") -> str:
    if not papers:
        return "# Search Results\n\nNo results found."
    lines = [f"# Search Results: {len(papers)} papers found\n"]
    for i, p in enumerate(papers, 1):
        lines.append(f"\n## {i}. {p.get('title', 'N/A')}\n")
        if p.get("authors"):
            lines.append(f"**Authors:** {', '.join(p['authors'][:5])}\n")
        if p.get("published"):
            lines.append(f"**Published:** {p['published']}\n")
        if p.get("doi"):
            lines.append(f"**DOI:** {p['doi']}\n")
        if p.get("abstract"):
            lines.append(f"**Abstract:** {p['abstract']}\n")
        if p.get("url"):
            lines.append(f"**URL:** {p['url']}\n")
    return "\n".join(lines)


def _format_bibtex(papers: List[Dict[str, Any]]) -> str:
    entries = []
    for p in papers:
        first_author = p.get("authors", ["Unknown"])[0].split()[-1].lower() if p.get("authors") else "unknown"
        year = str(p.get("year", p.get("published", "0000")))[:4]
        title_word = p.get("title", "").split()[0].lower() if p.get("title") else ""
        key = f"{first_author}{year}{title_word}"
        entry = f"@article{{{key},\n"
        entry += f"  title={{{p.get('title', 'No title')}}},\n"
        if p.get("authors"):
            entry += f"  author={{{' and '.join(p['authors'])}}},\n"
        entry += f"  year={{{year}}},\n"
        if p.get("doi"):
            entry += f"  doi={{{p['doi']}}},\n"
        if p.get("url"):
            entry += f"  url={{{p['url']}}},\n"
        entry = entry.rstrip(",\n") + "\n}\n"
        entries.append(entry)
    return "\n".join(entries)


def _format_ris(papers: List[Dict[str, Any]]) -> str:
    entries = []
    for p in papers:
        entry = "TY  - JOUR\n"
        entry += f"TI  - {p.get('title', 'No title')}\n"
        for a in p.get("authors", []):
            entry += f"AU  - {a}\n"
        year = str(p.get("year", p.get("published", "0000")))[:4]
        entry += f"PY  - {year}\n"
        if p.get("doi"):
            entry += f"DO  - {p['doi']}\n"
        if p.get("abstract"):
            entry += f"AB  - {p['abstract']}\n"
        if p.get("url"):
            entry += f"UR  - {p['url']}\n"
        entry += "ER  -\n\n"
        entries.append(entry)
    return "".join(entries)


# =============================================================================
# Material Quality Check — 素材质检（蒸馏前预处理）
# =============================================================================

def check_materials_quality(person_dir: str, person_name: str,
                           preview_chars: int = 500) -> Dict[str, Any]:
    """检查人物素材质量，返回问题文件列表。
    
    轻量质检：只读每个文件前N字符，检查空文件、不相关、同名误匹配。
    
    Args:
        person_dir: 人物目录路径（含materials/和papers/raw/）
        person_name: 人物姓名（用于相关性判断）
        preview_chars: 每个文件读取前N字符
    
    Returns:
        {
            "total": int,
            "ok": int,
            "issues": [{"file": str, "type": str, "reason": str}],
            "empty_files": [str],
            "irrelevant_files": [str],
            "suspicious_files": [str]
        }
    """
    import glob
    
    result = {
        "total": 0, "ok": 0,
        "issues": [],
        "empty_files": [], "irrelevant_files": [], "suspicious_files": []
    }
    
    person_path = Path(person_dir)
    
    # 收集所有素材文件
    files = []
    for pattern_dir in ["materials", "papers/raw"]:
        d = person_path / pattern_dir
        if d.exists():
            files.extend(glob.glob(str(d / "**/*.txt"), recursive=True))
            files.extend(glob.glob(str(d / "**/*.json"), recursive=True))
    
    # 排除汇总文件
    files = [f for f in files if not os.path.basename(f).startswith('_')]
    result["total"] = len(files)
    
    for fpath in files:
        fsize = os.path.getsize(fpath)
        fname = os.path.basename(fpath)
        rel_path = os.path.relpath(fpath, person_path)
        
        # 1. 空文件检查
        if fsize < 100:
            result["empty_files"].append(rel_path)
            result["issues"].append({"file": rel_path, "type": "empty", "reason": f"文件过小 ({fsize} bytes)"})
            continue
        
        # 2. 读取预览
        try:
            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                preview = f.read(preview_chars)
        except:
            result["issues"].append({"file": rel_path, "type": "unreadable", "reason": "无法读取"})
            continue
        
        # 3. 内容为空（只有元数据头）
        lines = [l.strip() for l in preview.split('\n') if l.strip() and not l.startswith('#')]
        if len(lines) < 3:
            result["empty_files"].append(rel_path)
            result["issues"].append({"file": rel_path, "type": "no_content", "reason": "只有元数据没有正文"})
            continue
        
        # 4. 同名误匹配检查（论文文件名含非相关领域关键词）
        if "paper_" in fname:
            # 检查DOI相关的学科领域不匹配
            irrelevant_keywords = {
                # 常见误匹配领域
                "soot": "材料科学", "catalytic": "化学", "optical": "光学", 
                "laser": "激光", "thermal": "热力学", "solar": "太阳能",
                "concrete": "建筑", "steel": "冶金", "algorithm": "计算机(非相关)",
            }
            for kw, field in irrelevant_keywords.items():
                if kw.lower() in preview.lower()[:200]:
                    # 进一步确认：如果人物名不在作者列表中，很大概率是误匹配
                    if person_name.lower().replace(" ", "") not in preview.lower().replace(" ", "")[:500]:
                        result["irrelevant_files"].append(rel_path)
                        result["issues"].append({"file": rel_path, "type": "irrelevant", "reason": f"疑似同名误匹配({field})"})
                        break
            else:
                result["ok"] += 1
        else:
            result["ok"] += 1
    
    return result


# =============================================================================
# Unified Search Entry Point
# =============================================================================

def detect_intent(question: str) -> List[str]:
    """根据查询内容自动判断应激活的搜索层。

    返回应激活的layer列表。规则：
    - 学者/人物/理论/学术关键词 → general + chinese + academic（跳过macro）
    - 金融/经济/股市/商品/宏观数据关键词 → general + chinese + macro（可跳过academic）
    - 混合议题 → 全部激活
    - 无明确意图 → 全部激活（安全降级）
    """
    q = question.lower()

    academic_signals = [
        "学者", "教授", "理论", "论文", "paper", "arxiv", "期刊", "著作",
        "学派", "思想家", "哲学家", "心理学家", "军事家", "战略家",
        "game theory", "博弈论", "nash", "schelling", "mearsheimer",
        "kahneman", "cialdini", "ekman", "zimbardo", "milgram",
        "approach", "framework", "methodology", "paradigm",
    ]
    macro_signals = [
        "cpi", "通胀", "利率", "lpr", "加息", "降息", "gdp", "pmi",
        "上证", "a股", "港股", "恒生", "股市", "股价", "大盘",
        "原油", "油价", "黄金", "金价", "石油", "天然气",
        "美元指数", "汇率", "人民币", "非农", "失业率",
        "vix", "fomc", "美联储", "央行", "联储",
        "brent", "wti", "铜价", "大豆", "小麦",
    ]

    has_academic = any(s in q for s in academic_signals)
    has_macro = any(s in q for s in macro_signals)

    if has_academic and not has_macro:
        return ["general", "chinese", "academic"]
    elif has_macro and not has_academic:
        return ["general", "chinese", "macro"]
    else:
        # 混合或无明确意图 → 全部激活
        return ["general", "chinese", "academic", "macro"]


# =============================================================================
# Person Search — 按人物名称自动中英文搜索
# =============================================================================

NAME_MAP_PATH = Path("/mnt/d/data/think-tank-data/personalities/name_map.json")


def load_name_map() -> Dict[str, Dict[str, str]]:
    """加载人物名称映射表"""
    if NAME_MAP_PATH.exists():
        with open(NAME_MAP_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def _is_chinese_name(name: str) -> bool:
    """判断名字是否包含中文字符"""
    return any('\u4e00' <= c <= '\u9fff' for c in name)


def resolve_person_names(query: str) -> List[str]:
    """
    根据输入的姓名，返回所有应该搜索的名称变体。
    
    规则：
    1. 先在 name_map 的 key 里精确匹配
    2. 再在 name_map 的 value.en 里匹配
    3. 都匹配不到时：
       - 中文名 → 自动追加英文搜索（原query + "{中文名} scholar/english name"）
       - 英文名 → 自动追加中文搜索（原query + 中文名搜索词）
    
    返回：名称变体列表（至少包含query本身）
    """
    name_map = load_name_map()
    names = [query]  # 始终包含原始查询
    found_in_map = False
    
    # 在key里匹配
    if query in name_map:
        entry = name_map[query]
        if entry.get("en") and entry["en"] not in names:
            names.append(entry["en"])
        if entry.get("zh") and entry["zh"] not in names:
            names.append(entry["zh"])
        found_in_map = True
    else:
        # 在value.en里匹配
        for key, entry in name_map.items():
            if entry.get("en", "").lower() == query.lower():
                if key not in names:
                    names.append(key)
                if entry.get("zh") and entry["zh"] not in names:
                    names.append(entry["zh"])
                found_in_map = True
                break
    
    # 未在映射中找到：启动子session推断
    if not found_in_map:
        print(f"[resolve_person_names] '{query}' 不在映射中，需要推断别名", flush=True)
        # 返回原始query，调用方应启动子session推断后再搜索
        names.append(f"__NEED_RESOLVE__:{query}")
    
    return names


def resolve_new_person(query: str) -> dict:
    """
    对映射中没有的新人物，返回子session任务prompt。
    主session调用 sessions_spawn 执行，让LLM搜索并推断中英文名。
    
    Returns:
        dict: {"task": "子session任务prompt", "query": query}
    """
    is_zh = _is_chinese_name(query)
    search_lang = "英文名/英文全名" if is_zh else "中文名"
    
    task = f"""你是人物名称研究专家。查找以下人物的{search_lang}。

人物: {query}

步骤:
1. 用 web_search 搜索 "{query}" 的相关信息
2. 从搜索结果中找到该人物的{search_lang}
3. 如果是学者，查找其在学术界的常用名
4. 如果是政治家/军事人物，查找其官方译名
5. 同时确定其所属领域（如：economics, game-theory, military等）

输出严格JSON（不要其他文字）:
```json
{{"original": "{query}", "zh": "中文名", "en": "English Name", "group": "scholars/economics"}}
```

如果搜索不到可靠信息，输出:
```json
{{"original": "{query}", "zh": null, "en": null, "group": null, "error": "找不到"}}
```"""
    
    return {"task": task, "query": query}


def update_name_map(new_entry: dict) -> bool:
    """将新人物写入name_map.json"""
    name_map = load_name_map()
    original = new_entry.get("original", "")
    if not original or original in name_map:
        return False
    
    entry = {"en": new_entry.get("en", "")}
    if new_entry.get("zh"):
        entry["zh"] = new_entry["zh"]
    if new_entry.get("group"):
        entry["group"] = new_entry["group"]
    
    name_map[original] = entry
    with open(NAME_MAP_PATH, 'w', encoding='utf-8') as f:
        json.dump(name_map, f, ensure_ascii=False, indent=2)
    print(f"[update_name_map] 已添加: {original} -> {entry}", flush=True)
    return True


def search_person(
    person_name: str,
    layers: Optional[List[str]] = None,
    max_per_source: int = 5,
    output_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    人物搜索 — 自动中英文双语搜索，结果合并。
    
    1. 读取 name_map.json 解析中英文名
    2. 分别搜索每个名称变体
    3. 合并去重
    4. 可选保存到指定目录
    
    Args:
        person_name: 人物姓名（中文或英文）
        layers: 搜索层级
        max_per_source: 每源结果数
        output_dir: 结果保存目录（可选）
    
    Returns:
        合并后的搜索结果
    """
    if layers is None:
        layers = ["general", "chinese", "academic"]
    
    names = resolve_person_names(person_name)
    
    # 检查是否需要先推断别名
    need_resolve = [n for n in names if n.startswith("__NEED_RESOLVE__:")]
    if need_resolve:
        print(f"[search_person] 需要先推断别名，请先调用 resolve_new_person() + sessions_spawn", flush=True)
        return {"error": "need_resolve", "message": f"{person_name} 不在映射中，请先推断别名", "resolve_info": resolve_new_person(person_name)}
    
    # 过滤掉标记，只留真实名称
    names = [n for n in names if not n.startswith("__")]
    print(f"[search_person] 搜索名称变体: {names}", flush=True)
    
    all_results = {}
    for layer in layers:
        all_results[layer] = []
    
    for name in names:
        result = search(
            question=name,
            layers=layers,
            dedup=False,
            max_per_source=max_per_source
        )
        for layer in layers:
            if layer in result:
                all_results[layer].extend(result.get(layer, []))
        time.sleep(2)  # 间隔防rate limit
    
    # 合并去重+评分
    for layer in layers:
        if all_results[layer]:
            all_results[layer] = dedup_results(all_results[layer])
            for item in all_results[layer]:
                # 用所有名称变体评分
                best_score = 0
                for name in names:
                    s = score_result(item, name)
                    best_score = max(best_score, s)
                item["score"] = best_score
            all_results[layer].sort(key=lambda x: x.get("score", 0), reverse=True)
    
    # 计算汇总
    total = sum(len(all_results.get(l, [])) for l in layers)
    all_results["summary"] = {
        "total": total,
        "searched_names": names,
        "sources": {}
    }
    for layer in layers:
        for item in all_results.get(layer, []):
            src = item.get("source", "Unknown")
            all_results["summary"]["sources"][src] = all_results["summary"]["sources"].get(src, 0) + 1
    all_results["detected_layers"] = layers
    
    # 可选：保存到文件
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        out_file = out / f"_search_results_{person_name}.json"
        with open(out_file, 'w', encoding='utf-8') as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        print(f"[search_person] 结果已保存: {out_file}", flush=True)
    
    return all_results


def search(
    question: str,
    layers: Optional[List[str]] = None,
    dedup: bool = True,
    max_per_source: int = 5
) -> Dict[str, Any]:
    """
    统一搜索入口 - 按议题从多源拉取数据。

    Args:
        question: 搜索问题/议题
        layers: 搜索层级列表，可选 ["general", "chinese", "academic", "macro"]。
                None时自动识别意图选择合适的层。
        dedup: 是否启用去重
        max_per_source: 每个搜索源最多结果数

    Returns:
        字典结构：只包含请求的layers
        {
            "general": [...],
            "chinese": [...],
            "academic": [...],
            "macro": [...],
            "summary": {"total": int, "sources": {str: int}},
            "detected_layers": [...]  # 自动检测到的意图层
        }
    """
    if layers is None:
        layers = detect_intent(question)

    result = {
        "summary": {"total": 0, "sources": {}},
        "detected_layers": layers
    }

    # Layer-specific search functions
    layer_functions = {
        "general": lambda q: search_general(q, max_per_source),
        "chinese": lambda q: search_chinese(q, max_per_source),
        "academic": lambda q: search_academic(q, max_per_source),
        "macro": lambda q: search_macro(q, max_per_source),
    }

    # Execute searches for requested layers
    for layer in layers:
        if layer not in layer_functions:
            continue

        result[layer] = []

        try:
            layer_results = layer_functions[layer](question)

            # Apply deduplication if requested
            if dedup and layer_results:
                layer_results = dedup_results(layer_results)

            # Add quality scores
            for item in layer_results:
                item["score"] = score_result(item, question)

            # Sort by score and limit results
            layer_results.sort(key=lambda x: x.get("score", 0), reverse=True)
            result[layer] = layer_results[:max_per_source]

            # Update summary
            for item in result[layer]:
                source = item.get("source", "Unknown")
                result["summary"]["sources"][source] = result["summary"]["sources"].get(source, 0) + 1

        except Exception:
            result[layer] = []

    # Calculate total
    result["summary"]["total"] = sum(len(result[layer]) for layer in layers)

    return result


# =============================================================================
# CLI Interface
# =============================================================================

def main():
    """Command line interface with subcommands: search, academic, download."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Data Search - 统一数据搜索模块")
    subparsers = parser.add_subparsers(dest="command")

    # --- search subcommand (original) ---
    sp = subparsers.add_parser("search", help="统一多源搜索")
    sp.add_argument("query", help="搜索查询")
    sp.add_argument("--layers", default="general,chinese,academic,macro", help="搜索层级（逗号分隔）")
    sp.add_argument("--max", type=int, default=5, help="每源最多结果数")
    sp.add_argument("--no-dedup", action="store_true", help="禁用去重")
    sp.add_argument("--format", choices=["json", "text", "markdown", "bibtex", "ris"], default="json", help="输出格式")

    # --- academic subcommand ---
    ap = subparsers.add_parser("academic", help="学术搜索（arXiv/PubMed/Semantic Scholar/Sci-Hub）")
    ap.add_argument("query", nargs="?", default="", help="搜索查询")
    ap.add_argument("--source", choices=["arxiv", "pubmed", "semantic", "scihub", "all"], default="all", help="搜索源")
    ap.add_argument("--max", type=int, default=10, help="最多结果数")
    ap.add_argument("--download", action="store_true", help="下载PDF")
    ap.add_argument("--output-dir", default="downloads", help="PDF输出目录")
    ap.add_argument("--format", choices=["json", "text", "markdown", "bibtex", "ris"], default="text", help="输出格式")
    ap.add_argument("--sort-by", choices=["relevance", "date", "citations"], default="relevance")
    ap.add_argument("--year", type=int, help="按年份过滤")
    ap.add_argument("--author", help="按作者过滤")
    ap.add_argument("--category", help="arXiv分类 (e.g. cs.AI)")
    ap.add_argument("--start-date", help="开始日期 (YYYY-MM-DD)")
    ap.add_argument("--end-date", help="结束日期 (YYYY-MM-DD)")
    ap.add_argument("--min-citations", type=int, help="最低引用数")
    ap.add_argument("--publication-type", help="PubMed出版类型")
    ap.add_argument("--doi", help="直接按DOI下载(Sci-Hub)")

    # --- person subcommand ---
    pp = subparsers.add_parser("person", help="人物搜索（自动中英文双语）")
    pp.add_argument("name", help="人物姓名（中文或英文）")
    pp.add_argument("--layers", default="general,chinese,academic", help="搜索层级")
    pp.add_argument("--max", type=int, default=5, help="每源最多结果数")
    pp.add_argument("--output-dir", help="结果保存目录")
    pp.add_argument("--format", choices=["json", "text"], default="json", help="输出格式")

    # --- download subcommand ---
    dp = subparsers.add_parser("download", help="PDF下载")
    dp.add_argument("--doi", help="按DOI下载")
    dp.add_argument("--output-dir", default="downloads", help="PDF输出目录")

    args = parser.parse_args()

    # Backwards compat: if no subcommand, treat as search with --query
    if not args.command:
        parser.print_help()
        return

    if args.command == "search":
        layers = args.layers.split(",")
        results = search(
            question=args.query,
            layers=layers,
            dedup=not args.no_dedup,
            max_per_source=args.max
        )
        # Flatten for formatting if not JSON
        if args.format != "json":
            all_papers = []
            for layer in layers:
                all_papers.extend(results.get(layer, []))
            print(format_results(all_papers, args.format))
        else:
            print(json.dumps(results, ensure_ascii=False, indent=2))

    elif args.command == "academic":
        papers = []
        src = args.source
        kwargs = dict(
            year=args.year, author=args.author, sort_by=args.sort_by,
            start_date=args.start_date, end_date=args.end_date,
            min_citations=args.min_citations, category=args.category,
            publication_type=args.publication_type
        )

        if args.doi:
            # Direct DOI download
            papers = download_papers(doi=args.doi, output_dir=args.output_dir)
        elif src == "arxiv":
            enhanced = _search_arxiv_enhanced(args.query, args.max, **{k: v for k, v in kwargs.items() if v})
            papers = enhanced if enhanced else _search_arxiv(args.query, args.max)
            if args.download:
                download_arxiv_papers(papers, args.output_dir)
        elif src == "pubmed":
            papers = _search_pubmed(args.query, args.max,
                **{k: v for k, v in kwargs.items() if k in ('start_date', 'end_date', 'publication_type', 'author') and v})
        elif src == "semantic":
            enhanced = _search_semantic_enhanced(args.query, args.max, **{k: v for k, v in kwargs.items() if v})
            papers = enhanced if enhanced else _search_semantic_scholar(args.query, args.max)
        elif src == "scihub":
            if not args.query and not args.doi:
                print("Error: query or --doi required for scihub", file=sys.stderr)
                return
            # Search via SS, download via Sci-Hub
            ss_results = _search_semantic_enhanced(args.query, args.max) or _search_semantic_scholar(args.query, args.max)
            for p in ss_results:
                p["source"] = "scihub"
            papers = ss_results
            if args.download:
                papers = download_papers(papers, output_dir=args.output_dir)
        else:  # all
            papers = search_academic(args.query, args.max, **{k: v for k, v in kwargs.items() if v})
            if args.download:
                download_papers(papers, output_dir=args.output_dir)

        print(format_results(papers, args.format, src))

    elif args.command == "person":
        layers = args.layers.split(",")
        results = search_person(
            person_name=args.name,
            layers=layers,
            max_per_source=args.max,
            output_dir=args.output_dir
        )
        if args.format == "json":
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            names = results.get("summary", {}).get("searched_names", [args.name])
            print(f"搜索: {names}")
            for layer in layers:
                items = results.get(layer, [])
                print(f"\n== {layer} ({len(items)}条) ==")
                for i, item in enumerate(items[:10]):
                    print(f"  [{i+1}] {item.get('title', 'N/A')[:60]} (score={item.get('score', 0):.2f}, {item.get('source', '')})")

    elif args.command == "download":
        if not args.doi:
            print("Error: --doi required for download", file=sys.stderr)
            return
        result = download_papers(doi=args.doi, output_dir=args.output_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
