#!/usr/bin/env python3
"""Playwright 网页采集脚本 — 支持 JS 渲染、Cookie、截图、文本提取"""

import argparse
import json
import sys
import os
import http.cookiejar
from pathlib import Path

from playwright.sync_api import sync_playwright

CHROMIUM_PATH = "/snap/bin/chromium"

# Headsup: stealth doesn't exist for Python playwright natively,
# but we can apply common anti-detection measures via CDP.
STEALTH_JS = """
() => {
  // Overwrite the 'webdriver' property
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

  // Overwrite Chrome runtime
  window.chrome = { runtime: {} };

  // Override permissions query
  const originalQuery = window.navigator.permissions.query;
  window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters);

  // Plugin array (looks more real)
  Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5],
  });

  // Languages
  Object.defineProperty(navigator, 'languages', {
    get: () => ['zh-CN', 'zh', 'en-US', 'en'],
  });
}
"""


def load_cookies(cookie_file: str) -> list[dict]:
    """Parse cookie file into Playwright cookie format."""
    if not cookie_file or not os.path.exists(cookie_file):
        return []

    cookies = []
    text = Path(cookie_file).read_text(encoding="utf-8").strip()

    # Try JSON format first
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # Parse Netscape cookie format
    cj = http.cookiejar.MozillaCookieJar()
    try:
        cj.load(cookie_file, ignore_discard=True, ignore_expires=True)
        for c in cj:
            cookies.append({
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path or "/",
                "secure": c.secure,
                "httpOnly": False,
            })
    except Exception:
        pass

    return cookies


def extract_text(html: str) -> str:
    """Rough HTML→text stripping (no bs4 dependency)."""
    import re
    # Remove script/style
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Convert <br>/<p>/<div> to newlines
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</?(p|div|h[1-6]|li|tr)[^>]*>', '\n', text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # Decode entities
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'")
    # Collapse whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def main():
    parser = argparse.ArgumentParser(description="Playwright 网页采集")
    parser.add_argument("url", help="目标 URL")
    parser.add_argument("-o", "--output", help="输出文件路径（默认 stdout）")
    parser.add_argument("--cookie-file", help="Cookie 文件路径")
    parser.add_argument("--screenshot", help="截图保存路径")
    parser.add_argument("--extract-text", action="store_true", help="提取纯文本")
    parser.add_argument("--wait", type=int, default=3000, help="页面加载等待 ms（默认 3000）")
    parser.add_argument("--timeout", type=int, default=30000, help="超时 ms（默认 30000）")
    parser.add_argument("--headless", action="store_true", default=True, help="无头模式（默认开启）")
    parser.add_argument("--no-headless", dest="headless", action="store_false", help="有头模式（调试用）")
    parser.add_argument("--selector", help="仅提取指定 CSS 选择器的内容")
    parser.add_argument("--user-agent", help="自定义 User-Agent")
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=args.headless,
            executable_path=CHROMIUM_PATH,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )

        context = browser.new_context(
            user_agent=args.user_agent or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )

        # Inject stealth scripts
        context.add_init_script(STEALTH_JS)

        # Load cookies
        cookies = load_cookies(args.cookie_file)
        if cookies:
            context.add_cookies(cookies)

        page = context.new_page()

        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout)
            page.wait_for_timeout(args.wait)

            # Scroll to bottom for lazy-loaded content
            page.evaluate("""
                async () => {
                    const delay = ms => new Promise(r => setTimeout(r, ms));
                    const height = document.body.scrollHeight;
                    const step = Math.max(500, Math.floor(height / 10));
                    for (let y = 0; y < height; y += step) {
                        window.scrollTo(0, y);
                        await delay(300);
                    }
                    window.scrollTo(0, 0);
                    await delay(500);
                }
            """)
            page.wait_for_timeout(1000)

            # Get content
            if args.selector:
                content = page.locator(args.selector).first.inner_text(timeout=5000)
            elif args.extract_text:
                html = page.content()
                content = extract_text(html)
            else:
                content = page.content()

            # Screenshot
            if args.screenshot:
                page.screenshot(path=args.screenshot, full_page=True)
                print(f"[截图] {args.screenshot}", file=sys.stderr)

            # Output
            if args.output:
                Path(args.output).write_text(content, encoding="utf-8")
                print(f"[输出] {args.output} ({len(content)} 字符)", file=sys.stderr)
            else:
                print(content)

            print(f"[状态] 成功", file=sys.stderr)

        except Exception as e:
            print(f"[错误] {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
