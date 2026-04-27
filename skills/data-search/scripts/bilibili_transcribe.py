#!/usr/bin/env python3
"""
bilibili_transcribe.py — B站视频搜索+下载+转录

完整流程：
  1. 用Playwright搜索B站视频（关键词+UP主），获取视频列表
  2. 用Playwright获取B站cookie（解决412问题）
  3. 用yt-dlp+cookie下载音频
  4. 用faster-whisper转录为文本
  5. 保存为统一格式的 .txt

用法:
  python3 bilibili_transcribe.py search --keyword "沈逸" --max 5 --output-dir /path/to/materials
  python3 bilibili_transcribe.py download --bv "BV1xxxx" --output-dir /path/to/materials

依赖: yt-dlp, faster-whisper, ffmpeg, playwright-core (tt conda env)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# ============ 配置 ============
WHISPER_MODEL = "base"  # tiny/base/small/medium/large-v3
WHISPER_DEVICE = "cpu"  # cpu/cuda
WHISPER_COMPUTE = "int8"
CHROMIUM_PATH = "/snap/bin/chromium"
COOKIE_FILE = Path("/tmp/bilibili_cookies.json")
TEMP_DIR = Path("/tmp/bilibili_audio")


# ============ Playwright: 获取Cookie ============

def get_bilibili_cookies() -> bool:
    """用Playwright访问B站获取cookie"""
    if COOKIE_FILE.exists():
        age = time.time() - COOKIE_FILE.stat().st_mtime
        if age < 3600:  # 1小时内有效
            return True

    print("  Getting B站cookie via Playwright...", flush=True)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=CHROMIUM_PATH,
                headless=True,
                args=["--no-sandbox", "--disable-gpu"]
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            )
            page = ctx.new_page()
            page.goto("https://www.bilibili.com", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            cookies = ctx.cookies()
            with open(COOKIE_FILE, "w") as f:
                json.dump(cookies, f)

            browser.close()
            print(f"  Got {len(cookies)} cookies", flush=True)
            return True
    except Exception as e:
        print(f"  Cookie获取失败: {e}", flush=True)
        return False


# ============ Playwright: 搜索B站 ============

def search_bilibili(keyword: str, max_results: int = 10, up_name: str = None) -> list:
    """用Playwright搜索B站视频，返回BV号+标题列表"""
    print(f"  Searching B站: {keyword} (max={max_results})", flush=True)

    query = keyword
    if up_name:
        query += f" {up_name}"

    videos = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=CHROMIUM_PATH,
                headless=True,
                args=["--no-sandbox", "--disable-gpu"]
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            )
            # 加载cookie
            if COOKIE_FILE.exists():
                with open(COOKIE_FILE) as f:
                    ctx.add_cookies(json.load(f))

            page = ctx.new_page()

            # B站搜索页
            search_url = f"https://search.bilibili.com/all?keyword={query}&search_type=video"
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            # 提取视频链接
            elements = page.query_selector_all('a[href*="video/BV"]')
            seen = set()
            for el in elements[:max_results * 2]:  # 多取一些去重
                href = el.get_attribute("href") or ""
                match = re.search(r'(BV[\w]+)', href)
                if not match:
                    continue
                bvid = match.group(1)
                if bvid in seen:
                    continue
                seen.add(bvid)

                title_el = el.query_selector('.title, h3, [class*="title"]')
                title = title_el.inner_text().strip() if title_el else el.inner_text().strip()[:80]

                videos.append({
                    "bvid": bvid,
                    "title": title,
                    "url": f"https://www.bilibili.com/video/{bvid}",
                })

                if len(videos) >= max_results:
                    break

            browser.close()
    except Exception as e:
        print(f"  搜索失败: {e}", flush=True)

    print(f"  Found {len(videos)} videos", flush=True)
    return videos


# ============ 下载+转录 ============

def _json_to_netscape_cookies(json_path: Path, netscape_path: Path):
    """将Playwright JSON cookie转为Netscape格式"""
    with open(json_path) as f:
        cookies = json.load(f)
    with open(netscape_path, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for c in cookies:
            domain = c.get("domain", "")
            flag = "TRUE" if not domain.startswith(".") else "FALSE"
            path = c.get("path", "/")
            secure = "TRUE" if c.get("secure", False) else "FALSE"
            expires = int(c.get("expires", 0))
            if expires < 0:
                expires = int(time.time()) + 86400  # 1天有效期
            name = c.get("name", "")
            value = c.get("value", "")
            f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")


def download_audio(bvid: str) -> Optional[Path]:
    """用you-get下载B站视频，ffmpeg提取音频"""
    url = f"https://www.bilibili.com/video/{bvid}"
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    vid_dir = TEMP_DIR / bvid
    vid_dir.mkdir(parents=True, exist_ok=True)
    wav_path = vid_dir / f"{bvid}.wav"

    # 如果已有wav直接返回
    if wav_path.exists():
        print(f"  Audio exists: {wav_path.name}", flush=True)
        return wav_path

    print(f"  Downloading {bvid} via you-get...", flush=True)
    try:
        # you-get下载视频
        you_get = str(Path(sys.executable).parent / "you-get")
        subprocess.run(
            [you_get, "--format=dash-flv480-HEVC", "-o", str(vid_dir), "-O", bvid, url],
            capture_output=True, text=True, timeout=600
        )

        # 找到音频DASH文件（you-get DASH: 大的是音频流）
        mp4_files = list(vid_dir.glob("*.mp4"))
        if len(mp4_files) >= 2:
            # 有多个DASH流，取最大的（音频流）
            audio_file = max(mp4_files, key=lambda f: f.stat().st_size)
        elif mp4_files:
            audio_file = mp4_files[0]
        else:
            print(f"  No video file found in {vid_dir}", flush=True)
            return None

        print(f"  Extracting audio from {audio_file.name}...", flush=True)
        # ffmpeg提取音频为wav (16kHz mono，whisper最优)
        ffmpeg = str(Path(sys.executable).parent / "ffmpeg")
        subprocess.run(
            [ffmpeg, "-i", str(audio_file), "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(wav_path), "-y"],
            capture_output=True, text=True, timeout=120
        )

        if wav_path.exists():
            print(f"  Audio ready: {wav_path.name} ({wav_path.stat().st_size // 1024}KB)", flush=True)
            # 清理视频文件
            for f in vid_dir.glob("*.mp4"):
                f.unlink(missing_ok=True)
            return wav_path

        print(f"  ffmpeg failed", flush=True)
    except subprocess.TimeoutExpired:
        print(f"  Download timeout for {bvid}", flush=True)
    except Exception as e:
        print(f"  Download error: {e}", flush=True)
    return None


def transcribe_audio(audio_path: Path, language: str = "zh") -> str:
    """用faster-whisper转录音频"""
    from faster_whisper import WhisperModel

    print(f"  Transcribing {audio_path.name} (model={WHISPER_MODEL})...", flush=True)

    model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    text_parts = [seg.text.strip() for seg in segments]
    full_text = "\n".join(text_parts)
    print(f"  Transcribed: {len(full_text)} chars ({info.duration:.0f}s audio)", flush=True)
    return full_text


def save_transcript(bvid: str, title: str, text: str, output_dir: Path) -> Path:
    """保存转录结果"""
    fname = f"{bvid}_转录.txt"
    path = output_dir / fname

    header = f"""title: "{bvid} 转录"
source: bilibili
bvid: {bvid}
video_title: {title}
language: zh
date_added: "{time.strftime('%Y-%m-%d')}"

===== 转录内容 =====

"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + text)

    print(f"  Saved: {path.name} ({len(text)} chars)", flush=True)
    return path


def process_video(bvid: str, title: str, output_dir: Path) -> Optional[Path]:
    """下载+转录+保存单个视频"""
    # 检查是否已存在
    existing = output_dir / f"{bvid}_转录.txt"
    if existing.exists():
        print(f"  Skip {bvid}: already transcribed", flush=True)
        return existing

    audio_path = download_audio(bvid)
    if not audio_path:
        return None

    try:
        text = transcribe_audio(audio_path)
        if not text.strip():
            print(f"  Empty transcript for {bvid}", flush=True)
            return None
        return save_transcript(bvid, title, text, output_dir)
    finally:
        if audio_path.exists():
            audio_path.unlink()


# ============ 命令 ============

def cmd_search(args):
    """搜索+下载+转录"""
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 先获取cookie
    get_bilibili_cookies()

    # 搜索
    videos = search_bilibili(args.keyword, args.max, getattr(args, 'up', None))
    if not videos:
        print("No videos found.")
        return

    success = 0
    for i, v in enumerate(videos):
        print(f"\n[{i+1}/{len(videos)}] {v['bvid']}: {v['title'][:60]}", flush=True)
        result = process_video(v["bvid"], v["title"], output_dir)
        if result:
            success += 1
        if i < len(videos) - 1:
            time.sleep(5)  # 间隔避免被封

    print(f"\n=== Done: {success}/{len(videos)} transcribed ===")


def cmd_download(args):
    """直接下载指定BV号"""
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    get_bilibili_cookies()
    process_video(args.bv, args.bv, output_dir)


def main():
    parser = argparse.ArgumentParser(description="B站视频搜索+下载+转录")
    subparsers = parser.add_subparsers(dest="command")

    p_search = subparsers.add_parser("search", help="搜索并转录")
    p_search.add_argument("--keyword", "-k", required=True)
    p_search.add_argument("--max", "-n", type=int, default=5)
    p_search.add_argument("--up", "-u", help="UP主名")
    p_search.add_argument("--output-dir", "-o", required=True)

    p_dl = subparsers.add_parser("download", help="下载指定BV号")
    p_dl.add_argument("--bv", "-b", required=True)
    p_dl.add_argument("--output-dir", "-o", required=True)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    {"search": cmd_search, "download": cmd_download}[args.command](args)


if __name__ == "__main__":
    main()
