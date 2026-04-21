---
name: playwright-scraper
description: "Playwright 浏览器自动化采集。用于 JS 重渲染页面、反爬网站（B站、知乎等）、需要登录态的页面抓取。当 web_fetch 失败或页面需要 JS 执行时使用。触发词：Playwright、浏览器采集、反爬、JS渲染、B站抓取、登录态抓取、动态页面。"
---

# Playwright Scraper — 浏览器自动化采集

当 `web_fetch` 搞不定的页面用这个：JS 重渲染、反爬检测、需要登录态、B 站视频页等。

## 触发条件

- web_fetch 返回空内容或被 WAF 拦截
- 用户提到 "Playwright"、"浏览器采集"、"反爬"
- B 站视频/音频提取（yt-dlp 会 412，必须用 Playwright）
- 知乎/微博等需要 Cookie 的页面
- SPA/React 等纯前端渲染页面

## 环境

- **Python**: `~/miniconda3/envs/tt/bin/python3`（自带 playwright + PyMuPDF）
- **Chromium**: `/snap/bin/chromium`（v147）
- **脚本**: `skills/playwright-scraper/scripts/scrape.py`

## 使用步骤

### 1. 基础抓取（返回 HTML）

```bash
~/miniconda3/envs/tt/bin/python3 skills/playwright-scraper/scripts/scrape.py \
  "https://example.com" \
  --output /tmp/result.html
```

### 2. 提取纯文本（自动去 HTML 标签）

```bash
~/miniconda3/envs/tt/bin/python3 skills/playwright-scraper/scripts/scrape.py \
  "https://example.com" \
  --extract-text \
  --output /tmp/result.txt
```

### 3. 带 Cookie 抓取（登录态）

```bash
~/miniconda3/envs/tt/bin/python3 skills/playwright-scraper/scripts/scrape.py \
  "https://www.zhihu.com/question/xxx" \
  --cookie-file ~/.openclaw/workspace/.cookies/zhihu_cookies.txt \
  --extract-text \
  --output /tmp/zhihu.txt
```

### 4. 截图

```bash
~/miniconda3/envs/tt/bin/python3 skills/playwright-scraper/scripts/scrape.py \
  "https://example.com" \
  --screenshot /tmp/page.png
```

### 5. CSS 选择器提取（只抓某部分）

```bash
~/miniconda3/envs/tt/bin/python3 skills/playwright-scraper/scripts/scrape.py \
  "https://example.com" \
  --selector "article.content" \
  --output /tmp/article.txt
```

### 6. 自定义等待（慢加载页面）

```bash
~/miniconda3/envs/tt/bin/python3 skills/playwright-scraper/scripts/scrape.py \
  "https://slow-site.com" \
  --wait 8000 \
  --timeout 60000 \
  --extract-text
```

### 7. 调试模式（有头浏览器，能看到页面）

```bash
~/miniconda3/envs/tt/bin/python3 skills/playwright-scraper/scripts/scrape.py \
  "https://example.com" \
  --no-headless \
  --wait 5000
```

## 参数一览

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `url` | ✅ | — | 目标 URL |
| `-o/--output` | | stdout | 输出文件路径 |
| `--cookie-file` | | — | Cookie 文件（Netscape 或 JSON 格式）|
| `--screenshot` | | — | 截图保存路径（full_page）|
| `--extract-text` | | false | 提取纯文本（去掉 HTML 标签）|
| `--selector` | | — | CSS 选择器，仅提取匹配元素 |
| `--wait` | | 3000 | 页面加载后额外等待（ms）|
| `--timeout` | | 30000 | 页面加载超时（ms）|
| `--user-agent` | | Chrome 131 | 自定义 UA |
| `--no-headless` | | false | 有头模式（调试用）|

## 特性

- **自动滚动**：页面加载后自动滚到底部，触发懒加载
- **Stealth 注入**：覆盖 `navigator.webdriver`、伪造 `chrome.runtime`，降低被检测概率
- **Cookie 支持**：兼容 Netscape cookie 文件和 JSON 数组格式
- **懒加载友好**：默认 3s 等待 + 自动滚动，可调长
- **无额外依赖**：不需要 Node.js，纯 Python

## 抓取优先级

```
web_fetch（最快最轻）→ web-scraper-jina（绕 Cloudflare）→ Playwright（万能但最重）
```

先试 web_fetch，被拦了再试 r.jina.ai，都不行才上 Playwright。

## 与其他 Skill 的配合

- 抓到的内容需归档 → `agent-knowledge` 的 `know add`
- 抓到的素材需入库 → `think-tank` 的 `ingest_v3.py`
- B 站音频提取 → 先 Playwright 抓页面拿音频 URL，再 ffmpeg 下载
