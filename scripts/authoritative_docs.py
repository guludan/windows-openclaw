#!/usr/bin/env python3
"""
定调文件档案库 — 五年规划 + 白皮书 + 政府工作报告

稳定信源矩阵 (黄老板 2026-05-22):
  1. 五年规划纲要   → ndrc.gov.cn 规划司专栏 + 新华社受权播发
  2. 白皮书         → scio.gov.cn (Cloudflare) → 走Playwright/r.jina.ai
  3. 政府工作报告   → gov.cn 每年3月全文发布

文件统一存储: cache/authoritative_docs/
索引:           cache/authoritative_docs_index.json

用法:
  # 回填所有
  python3 authoritative_docs.py --backfill
  
  # 只抓五年规划
  python3 authoritative_docs.py --plans
  
  # 只抓白皮书
  python3 authoritative_docs.py --white-papers
  
  # 抓政府工作报告
  python3 authoritative_docs.py --work-reports
  
  # 监控是否有新发布（cron用）
  python3 authoritative_docs.py --monitor
"""

import json, re, sys, os, hashlib, argparse, logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('authoritative_docs')

# ─── 缓存目录 ───
CACHE_DIR = Path("/home/tearsyu/.openclaw/workspace/cache/authoritative_docs")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
INDEX_FILE = CACHE_DIR / "index.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ─── 信源定义 ───
# 注意：gov.cn 2025年改版后URL模式变更
# 旧路径: /xinwen/ /premier/ /guowuyuan/ /gongbao/
# 新路径: /zhengce/ /yaowen/liebiao/
DOC_SOURCES = {
    # ── 五年规划 ──
    "five_year_plan": [
        {
            "name": "十四五规划+2035远景",
            "year": 2021,
            "url": "https://www.gov.cn/xinwen/2021-03/13/content_5592681.htm",
            "source": "gov.cn 新华社受权播发",
        },
        {
            "name": "十三五规划纲要",
            "year": 2016,
            "url": "https://www.gov.cn/xinwen/2016-03/17/content_5054992.htm",
            "source": "gov.cn 新华社受权播发",
        },
        {
            "name": "十一五规划纲要",
            "year": 2006,
            "url": "https://www.gov.cn/gongbao/content/2006/content_268766.htm",
            "source": "gov.cn 政府公报",
        },
        # TODO: 十二五规划(2011) — gov.cn改版/2011lh/路径失效
    ],
    # ── 白皮书 ──
    "white_paper": [
        {"name": "中国的北极政策", "year": 2018, "url": "https://www.gov.cn/zhengce/2018-01/26/content_5260891.htm", "source": "gov.cn"},
        {"name": "携手构建网络空间命运共同体", "year": 2022, "url": "https://www.gov.cn/xinwen/2022-11/07/content_5725118.htm", "source": "gov.cn"},
        # TODO: 中国的能源转型(2024) — scio.gov.cn需Playwright
        # TODO: 新时代的中国国防(2024) — 待确认URL
    ],
    # ── 政府工作报告 ──
    "gov_work_report": [
        {"name": "2026年政府工作报告", "year": 2026, "url": "https://www.gov.cn/yaowen/liebiao/202603/content_7062625.htm", "source": "gov.cn"},
        {"name": "2025年政府工作报告", "year": 2025, "url": "https://www.gov.cn/yaowen/liebiao/202503/content_7013163.htm", "source": "gov.cn"},
        {"name": "2024年政府工作报告", "year": 2024, "url": "https://www.gov.cn/yaowen/liebiao/202403/content_6939153.htm", "source": "gov.cn"},
        {"name": "2023年政府工作报告", "year": 2023, "url": "https://www.gov.cn/xinwen/2023-03/05/content_5744736.htm", "source": "gov.cn"},
        {"name": "2017年政府工作报告", "year": 2017, "url": "https://www.gov.cn/premier/2017-03/16/content_5177940.htm", "source": "gov.cn"},
        {"name": "2015年政府工作报告", "year": 2015, "url": "https://www.gov.cn/guowuyuan/2015-03/16/content_2835101.htm", "source": "gov.cn"},
    ],
}


# ═══════════════════════════════════════
#   索引管理
# ═══════════════════════════════════════

def load_index() -> Dict[str, dict]:
    if INDEX_FILE.exists():
        with open(INDEX_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_index(idx: Dict[str, dict]):
    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════
#   抓取器
# ═══════════════════════════════════════

def fetch_text(url: str) -> Optional[str]:
    """抓取URL的纯文本内容"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.encoding = 'utf-8'
    except Exception as e:
        log.warning(f"  请求失败 {url[-40:]}: {e}")
        return None
    if r.status_code != 200:
        log.warning(f"  HTTP {r.status_code} {url[-40:]}")
        return None
    
    html = r.text
    # 去掉script/style
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '\n', text)
    # 去掉空行
    lines = [l.strip() for l in text.split('\n') if len(l.strip()) > 15]
    return '\n'.join(lines)


def extract_keywords(text: str, title: str = "") -> List[str]:
    """从定调文件提取关键词（章节标题+重复出现的关键概念）"""
    import jieba
    
    # 从正文提取章节标题行（短行、无标点）
    lines = text.split('\n')
    chapter_titles = set()
    for line in lines:
        line = line.strip()
        # 匹配"第X篇"、"第X章"、"一、"等
        if re.match(r'^第[一二三四五六七八九十\d]+[篇章节]', line):
            chapter_titles.add(line[:60])
        elif re.match(r'^[一二三四五六七八九十]+[、\.]', line):
            chapter_titles.add(line[:60])
        elif 5 < len(line) < 40 and any(kw in line for kw in [
            '创新', '产业', '经济', '发展', '改革', '开放', '科技',
            '数字', '绿色', '能源', '制造', '农业', '安全', '民生'
        ]):
            chapter_titles.add(line[:60])
    
    # 用jieba提取高频名词
    words = jieba.lcut(text[:5000])
    from collections import Counter
    freq = Counter(w for w in words if 2 <= len(w) <= 8 and not re.match(r'^[0-9\s,，。、；：！？""（）\(\)\[\]【】《》/\\|·@#¥%…—~·]+$', w))
    
    # 取top 50作为补充
    top_words = [w for w, c in freq.most_common(50) if c >= 3]
    
    return list(chapter_titles) + top_words[:30]


def fetch_doc(doc_info: dict, doc_type: str) -> Optional[dict]:
    """抓取一份定调文件"""
    url = doc_info['url']
    name = doc_info['name']
    year = doc_info['year']
    
    cache_key = hashlib.md5(url.encode()).hexdigest()[:12]
    cache_file = CACHE_DIR / f"{cache_key}.json"
    
    # 检查缓存
    if cache_file.exists():
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    
    log.info(f"  抓取 [{year}] {name} ...")
    
    text = fetch_text(url)
    if not text:
        return None
    
    # 提取关键词
    kws = extract_keywords(text, name)
    
    doc = {
        'name': name,
        'year': year,
        'type': doc_type,
        'source': doc_info['source'],
        'url': url,
        'fetched_at': datetime.now().isoformat(),
        'full_text': text[:30000],  # 限制长度
        'full_text_len': len(text),
        'keywords_extracted': kws,
        'chapter_count': len([k for k in kws if len(k) < 60]),
    }
    
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    
    log.info(f"    ✅ {name}: {len(text)}字, {doc['chapter_count']}个章节关键词")
    return doc


def backfill_docs(doc_types: List[str] = None):
    """回填指定类型的定调文件"""
    idx = load_index()
    total_new = 0
    
    start_time = datetime.now()
    
    for doc_type, doc_list in DOC_SOURCES.items():
        if doc_types and doc_type not in doc_types:
            continue
        
        log.info(f"\n{'='*50}")
        log.info(f"📂 {doc_type_map(doc_type)} ({len(doc_list)}份)")
        log.info(f"{'='*50}")
        
        for doc_info in doc_list:
            url = doc_info['url']
            name = doc_info['name']
            
            # 去重检查
            if url in idx:
                log.debug(f"  跳过 {name} (已缓存)")
                continue
            
            doc = fetch_doc(doc_info, doc_type)
            if doc:
                idx[url] = {
                    'name': name,
                    'year': doc_info['year'],
                    'type': doc_type,
                    'source': doc_info['source'],
                    'fetched_at': doc['fetched_at'],
                    'text_len': doc['full_text_len'],
                    'keywords_count': len(doc.get('keywords_extracted', [])),
                }
                total_new += 1
            else:
                log.warning(f"  ❌ {name} 抓取失败")
    
    save_index(idx)
    
    elapsed = (datetime.now() - start_time).total_seconds()
    log.info(f"\n{'='*50}")
    log.info(f"✅ 完成: 本次新增 {total_new} 份，总计 {len(idx)} 份")
    log.info(f"   耗时: {elapsed:.0f}s")
    return total_new


def doc_type_map(t: str) -> str:
    mapping = {
        'five_year_plan': '五年规划纲要',
        'white_paper': '白皮书',
        'gov_work_report': '政府工作报告',
    }
    return mapping.get(t, t)


# ═══════════════════════════════════════
#   监控（cron用：检查是否有新文件发布）
# ═══════════════════════════════════════

def monitor():
    """检查定调文件信源是否有新发布"""
    log.info("检查定调文件信源更新...")
    
    current_count = len(load_index())
    
    # 监控gov.cn五年规划专栏
    urls_to_check = [
        ("https://www.gov.cn/xinwen/2026-03/13/content_7064155.htm", "十五五规划"),
    ]
    
    for url, name in urls_to_check:
        try:
            r = requests.head(url, headers=HEADERS, timeout=5)
            if r.status_code == 200:
                log.info(f"  ✅ {name} 可访问")
            else:
                log.warning(f"  ⚠️ {name} HTTP {r.status_code}")
        except Exception as e:
            log.warning(f"  ❌ {name}: {e}")
    
    log.info(f"  当前存档: {current_count} 份")
    return current_count


# ═══════════════════════════════════════
#   统计
# ═══════════════════════════════════════

def show_stats():
    idx = load_index()
    log.info(f"\n📊 定调文件档案库")
    log.info(f"{'='*50}")
    
    by_type = {}
    for url, meta in idx.items():
        t = meta.get('type', 'unknown')
        by_type.setdefault(t, []).append(meta)
    
    for t in ['five_year_plan', 'white_paper', 'gov_work_report']:
        items = by_type.get(t, [])
        log.info(f"  {doc_type_map(t)}: {len(items)} 份")
        if items:
            for item in sorted(items, key=lambda x: x.get('year', 0), reverse=True)[:5]:
                log.info(f"    [{item['year']}] {item['name']} ({item.get('text_len',0)}字)")


# ═══════════════════════════════════════
#   CLI
# ═══════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="定调文件档案库")
    parser.add_argument('--backfill', action='store_true', help='回填所有定调文件')
    parser.add_argument('--plans', action='store_true', help='仅五年规划')
    parser.add_argument('--white-papers', action='store_true', help='仅白皮书')
    parser.add_argument('--work-reports', action='store_true', help='仅政府工作报告')
    parser.add_argument('--monitor', action='store_true', help='检查信源状态')
    parser.add_argument('--stats', action='store_true', help='统计存档')
    
    args = parser.parse_args()
    
    if args.monitor:
        monitor()
    elif args.stats:
        show_stats()
    elif args.plans:
        backfill_docs(['five_year_plan'])
    elif args.white_papers:
        backfill_docs(['white_paper'])
    elif args.work_reports:
        backfill_docs(['gov_work_report'])
    elif args.backfill:
        backfill_docs()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
