---
name: Web Scraper (r.jina.ai)
description: Bypass Cloudflare and scrape any website using r.jina.ai API. Works on sites with strong protection like Truth Social, Cloudflare Turnstile, etc.
version: "1.0.0"
author: Liuge
tags:
  - web
  - scraper
  - cloudflare
  - bypass
  - jina
---

# Web Scraper using r.jina.ai

Bypass Cloudflare and scrape any website using free r.jina.ai API.

## 触发条件

- web_fetch 被 Cloudflare/WAF 拦截
- 用户提到 "绕过 Cloudflare"、"反爬"
- Truth Social、Bitget 等强保护站点
- 快速获取页面 Markdown（比 Playwright 轻）

## 操作步骤

1. 构造 URL：`https://r.jina.ai/<目标URL>`
2. `web_fetch "https://r.jina.ai/https://target.com"`
3. 或 curl：`curl -s "https://r.jina.ai/https://target.com"`

## 优先级：web_fetch > r.jina.ai > Playwright

## Features

- Bypass Cloudflare, Turnstile, and other protections
- Works on Truth Social, Bitget, and other protected sites
- Returns clean Markdown content
- Free to use

## Usage

Simply prepend `https://r.jina.ai/` to any URL:

```
https://r.jina.ai/https://truthsocial.com/@realDonaldTrump
https://r.jina.ai/https://bitget.com/events/poolx
```

## Examples

### Get Trump Truth Social posts:
```
curl -s "https://r.jina.ai/https://truthsocial.com/@realDonaldTrump"
```

### Get any protected page:
```
curl -s "https://r.jina.ai/https://example.com"
```

## In Code

```python
import requests

def scrape(url):
    return requests.get(f"https://r.jina.ai/{url}").text
```

## Use Cases

- Scrape Truth Social, Gab, Gettr
- Bypass Cloudflare protected sites
- Extract content from news articles
- Monitor competitors
