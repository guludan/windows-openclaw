#!/usr/bin/env python3
"""
将缓存文件写入PG event_list 表

一次性回填所有历史缓存数据到 event_list 表，
包括：新闻联播、财联社快讯、政策文件。

用法：
  python3 cache2event_list.py                     # 全部写入
  python3 cache2event_list.py --source cctv       # 仅新闻联播
  python3 cache2event_list.py --source cls        # 仅财联社
  python3 cache2event_list.py --source policy     # 仅政策文件
  python3 cache2event_list.py --dedup-only        # 仅去重，不插
  python3 cache2event_list.py --dry-run           # 预览，不写入
"""

import json, sys, os, argparse, logging
from pathlib import Path
from datetime import datetime, date
from typing import List, Dict, Optional

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('cache2event')

BASE_DIR = Path(__file__).resolve().parent.parent
CCTV_DIR = BASE_DIR / 'cache' / 'news_cctv'
CLS_FILE = BASE_DIR / 'cache' / 'cls_telegraph_archive.json'
POLICY_INDEX = BASE_DIR / 'cache' / 'gov_policies_index.json'

PG_DSN = dict(host='192.168.3.14', port=5432, user='guludan', password='toto', dbname='guludan')

UNIQUE_FIELDS = ['event_date', 'source', 'title']  # 去重字段


def source_from_filename(fname: str) -> str:
    """判断来源"""
    if fname.startswith('10jqka_'):
        return 'ths_telegraph'
    return 'news_cctv'


def cctv_date_from_filename(fname: str) -> str:
    """从文件名提取日期"""
    stem = Path(fname).stem
    return f"{stem[:4]}-{stem[4:6]}-{stem[6:8]}"


def load_existing_events(cur) -> set:
    """加载已存在的 (event_date, source, title) 集合"""
    cur.execute("""
        SELECT event_date, source, title FROM event_list
    """)
    rows = cur.fetchall()
    existing = set()
    for r in rows:
        key = (str(r[0]), str(r[1]), str(r[2]))
        existing.add(key)
    return existing


def batch_insert(conn, cur, events: List[Dict], existing: set,
                 batch_size: int = 200) -> int:
    """批量插入事件，跳过已存在的"""
    inserted = 0
    skipped = 0
    batch = []

    for evt in events:
        key = (evt['event_date'], evt['source'], evt['title'])
        if key in existing:
            skipped += 1
            continue

        batch.append(evt)

        if len(batch) >= batch_size:
            _do_batch_insert(cur, batch)
            conn.commit()
            inserted += len(batch)
            log.info(f"  已插入 {inserted} 条, 跳过 {skipped}")
            batch = []

    if batch:
        _do_batch_insert(cur, batch)
        conn.commit()
        inserted += len(batch)

    return inserted, skipped


def _do_batch_insert(cur, batch: List[Dict]):
    """批量写入"""
    import psycopg2.extras
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO event_list 
            (event_date, trade_date, source, source_subtype, title, category, 
             direction, full_text, is_clean)
        VALUES %s
        ON CONFLICT DO NOTHING
        """,
        [(e['event_date'], e.get('trade_date') or None, e['source'],
          e.get('source_subtype'), e['title'], e.get('category'),
          e.get('direction', 0), e.get('full_text'), e.get('is_clean', False))
         for e in batch],
        template='(%s::date, %s::date, %s, %s, %s, %s, %s::smallint, %s, %s::boolean)'
    )


# ═══════════════════════════════════════
#  新闻联播
# ═══════════════════════════════════════

def collect_cctv_events(dry_run: bool) -> List[Dict]:
    """从新闻联播缓存收集事件"""
    import sys
    sys.path.insert(0, str(BASE_DIR / 'scripts'))
    from data_acquisition import get_trade_dates_for_cctv, is_policy_related

    files = sorted(CCTV_DIR.glob('*.json'))
    log.info(f"扫描 {len(files)} 个新闻联播缓存文件...")

    events = []
    for f in files:
        try:
            data = json.load(open(f, encoding='utf-8'))
        except:
            continue
        if not isinstance(data, list):
            data = [data]

        cctv_date = cctv_date_from_filename(f.name)

        # 找最近交易日
        trade_dates = get_trade_dates_for_cctv(cctv_date)
        trade_date = trade_dates[0] if trade_dates else ''

        for item in data:
            title = item.get('title', '').strip()
            content = item.get('content', '').strip()
            if not title:
                continue

            is_policy, relevance = is_policy_related(title, content)

            # 归类
            if is_policy:
                if any(k in title for k in ['习近平', '总书记', '中央']):
                    category = 'leadership'
                elif any(k in title for k in ['经济', '金融', '发展', '改革']):
                    category = 'economy'
                elif any(k in title for k in ['外交', '访问', '会谈', '会见']):
                    category = 'diplomacy'
                elif any(k in title for k in ['科技', '创新', '数字']):
                    category = 'technology'
                elif any(k in title for k in ['民生', '扶贫', '就业', '社保']):
                    category = 'social'
                else:
                    category = 'policy_general'

                direction = item.get('direction', 1 if relevance >= 2.0 else 0)
            else:
                category = 'general'
                direction = 0

            events.append({
                'event_date': cctv_date,
                'trade_date': trade_date,
                'source': 'news_cctv',
                'source_subtype': category,
                'title': title,
                'category': category,
                'direction': direction,
                'full_text': content,
                'is_clean': True,
            })

    log.info(f"新闻联播: 共 {len(events)} 条事件")
    return events


# ═══════════════════════════════════════
#  财联社快讯
# ═══════════════════════════════════════

def collect_cls_events(dry_run: bool) -> List[Dict]:
    """从财联社归档文件收集事件"""
    if not CLS_FILE.exists():
        log.warning("财联社缓存不存在")
        return []

    raw = json.load(open(CLS_FILE, encoding='utf-8'))
    log.info(f"财联社归档: {len(raw)} 条原始记录")

    # 判断格式
    if isinstance(raw, dict):
        items = list(raw.values())
    else:
        items = raw

    events = []
    for item in items:
        date_str = item.get('date', '')
        if not date_str or len(date_str) < 10:
            continue

        title = item.get('title', '').strip()
        content = item.get('content', '').strip()
        if not title and not content:
            continue

        # 提取事件日期（YYYY-MM-DD）
        evt_date = date_str[:10]

        # 归类（简单关键词）
        full_text = title + ' ' + content
        if any(k in full_text for k in ['经济', '金融', '股市', '央行']):
            cat = 'economy'
        elif any(k in full_text for k in ['科技', 'AI', '芯片']):
            cat = 'technology'
        elif any(k in full_text for k in ['政策', '国常会', '政治局']):
            cat = 'policy_finance'
        elif any(k in full_text for k in ['外交', '国际']):
            cat = 'diplomacy'
        else:
            cat = 'market_news'

        events.append({
            'event_date': evt_date,
            'trade_date': '',
            'source': 'cls_telegraph',
            'source_subtype': cat,
            'title': title or content[:80],
            'category': cat,
            'direction': 1 if item.get('sentiment', 0) > 0 else 0,
            'full_text': content or title,
            'is_clean': True,
        })

    log.info(f"财联社快讯: 共 {len(events)} 条事件")
    return events


# ═══════════════════════════════════════
#  政策文件
# ═══════════════════════════════════════

def collect_policy_events(dry_run: bool) -> List[Dict]:
    """从政策文件索引收集事件"""
    if not POLICY_INDEX.exists():
        log.warning("政策文件索引不存在")
        return []

    idx = json.load(open(POLICY_INDEX, encoding='utf-8'))
    log.info(f"政策文件索引: {len(idx)} 条")

    events = []
    for url, meta in idx.items():
        title = meta.get('title', '').strip()
        if not title:
            continue

        pub_date = meta.get('pub_date', '')
        if not pub_date:
            continue

        # 补齐日期：只有年月则用1日，只有年则用1月1日
        pd_clean = pub_date.strip()
        if len(pd_clean) == 7:  # YYYY-MM
            pd_clean += '-01'
        elif len(pd_clean) == 4:  # YYYY
            pd_clean += '-01-01'

        if len(pd_clean) < 10:
            continue

        doc_type = meta.get('doc_type', 'policy')
        if doc_type in ('意见', '指导'):
            cat = 'opinion'
        elif doc_type in ('办法', '规定', '条例'):
            cat = 'regulation'
        elif doc_type in ('通知', '公告', '通告'):
            cat = 'notice'
        elif doc_type in ('报告', '方案', '计划'):
            cat = 'report'
        else:
            cat = 'policy_general'

        events.append({
            'event_date': pd_clean[:10],
            'trade_date': '',
            'source': 'gov_policy',
            'source_subtype': cat,
            'title': title,
            'category': cat,
            'direction': 0,
            'full_text': meta.get('summary', ''),
            'is_clean': True,
        })

    log.info(f"政策文件: 共 {len(events)} 条事件")
    return events


def sync_gov_policy():
    """增量同步gov_policy缓存到event_list（供data_acquisition.py --gov调用）"""
    import psycopg2, hashlib
    from pathlib import Path
    
    POLICY_DIR = Path(CACHE_DIR) / "gov_policies"
    index_file = Path(CACHE_DIR) / "gov_policies_index.json"
    if not index_file.exists():
        log.warning("gov_policies_index.json 不存在")
        return 0
    
    idx = json.loads(index_file.read_text(encoding='utf-8'))
    
    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    
    # 查询已存在的（event_date, title）
    cur.execute("SELECT event_date, title FROM event_list WHERE source='gov_policy'")
    existing = set()
    for d, t in cur.fetchall():
        existing.add((str(d), t))
    
    inserted = 0
    for url, meta in idx.items():
        title = meta.get('title', '')
        pub_date = meta.get('pub_date', '')
        if not pub_date or not title:
            continue
        sig = (pub_date[:10], title[:500])
        if sig in existing:
            continue
        
        h = hashlib.md5(url.encode()).hexdigest()[:12]
        cache_file = POLICY_DIR / f"{h}.json"
        if not cache_file.exists():
            continue
        data = json.loads(cache_file.read_text(encoding='utf-8'))
        text = data.get('text', '')
        if len(text) < 50:
            continue
        try:
            cur.execute("""
                INSERT INTO event_list (event_date, source, source_subtype, title, url, full_text, direction, is_clean)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (pub_date[:10], 'gov_policy', 'policy', title[:500], url, text[:50000], 0, True))
            inserted += 1
        except Exception:
            conn.rollback()
            cur = conn.cursor()
    
    conn.commit()
    log.info(f"gov_policy同步: 新增 {inserted} 条")
    cur.close()
    conn.close()
    return inserted


# ═══════════════════════════════════════
#  定调文件
# ═══════════════════════════════════════

def collect_authoritative_docs(dry_run: bool) -> List[Dict]:
    """从定调文件档案收集事件（五年规划/工作报告/白皮书）"""
    idx_file = BASE_DIR / 'cache' / 'authoritative_docs' / 'index.json'
    if not idx_file.exists():
        log.warning("定调文件索引不存在")
        return []

    idx = json.load(open(idx_file, encoding='utf-8'))
    log.info(f"定调文件索引: {len(idx)} 条")

    import re
    events = []
    for url, meta in idx.items():
        name = meta.get('name', '').strip()
        if not name:
            continue

        doc_type = meta.get('type', '')
        if 'five' in doc_type:
            cat = 'five_year_plan'
        elif 'work' in doc_type:
            cat = 'gov_work_report'
        elif 'white' in doc_type:
            cat = 'white_paper'
        else:
            cat = 'authoritative_doc'

        # 从URL提取日期：/2021-03/13/ → 2021-03-13
        pub_date = ''
        m = re.search(r'/(\d{4})-(\d{2})/content', url)
        if m:
            pub_date = f"{m.group(1)}-{m.group(2)}-01"  # 日不可靠，用01
        elif m := re.search(r'/(\d{4})/content', url):
            pub_date = f"{m.group(1)}-01-01"
        else:
            pub_date = f"{meta.get('year', 2000)}-01-01"

        # 读取全文 - 从缓存JSON读取
        full_text = meta.get('full_text', '')
        if not full_text:
            # 扫描缓存目录查找匹配的JSON
            for cf in idx_file.parent.glob('*.json'):
                if cf.name == 'index.json':
                    continue
                try:
                    cf_data = json.load(open(cf))
                    if isinstance(cf_data, dict) and cf_data.get('url') == url:
                        full_text = cf_data.get('full_text', '')[:20000]
                        break
                except: pass

        events.append({
            'event_date': pub_date[:10],
            'trade_date': '',
            'source': 'authoritative_doc',
            'source_subtype': cat,
            'title': name,
            'category': cat,
            'direction': 0,
            'full_text': full_text,
            'is_clean': True,
        })

    log.info(f"定调文件: 共 {len(events)} 条事件")
    return events


# ═══════════════════════════════════════
#  主流程
# ═══════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="缓存文件入库 → event_list")
    parser.add_argument('--source', choices=['cctv', 'cls', 'policy', 'auth_doc', 'all'],
                        default='all', help='数据来源')
    parser.add_argument('--dry-run', action='store_true', help='预览不写入')
    parser.add_argument('--batch-size', type=int, default=200, help='批量大小')
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("缓存文件入库 → event_list")
    log.info("=" * 60)

    # 收集事件
    all_events = []
    sources_to_collect = {
        'cctv': collect_cctv_events,
        'cls': collect_cls_events,
        'policy': collect_policy_events,
        'auth_doc': collect_authoritative_docs,
    }

    sources = ['cctv', 'cls', 'policy', 'auth_doc'] if args.source == 'all' else [args.source]
    for src in sources:
        log.info(f"\n--- {src} ---")
        evts = sources_to_collect[src](args.dry_run)
        all_events.extend(evts)

    if not all_events:
        log.warning("无事件可入库")
        return

    log.info(f"\n总计 {len(all_events)} 条事件")

    if args.dry_run:
        log.info("Dry-run 模式，不写入")
        log.info(f"\n前5条预览:")
        for e in all_events[:5]:
            print(f"  [{e['event_date']}] [{e['source']}] {e['title'][:60]}")
        return

    # 写入PG
    import psycopg2
    conn = psycopg2.connect(**PG_DSN)
    cur = conn.cursor()

    try:
        existing = load_existing_events(cur)
        log.info(f"已存在 {len(existing)} 条事件")

        inserted, skipped = batch_insert(conn, cur, all_events, existing, args.batch_size)

        log.info(f"\n✅ 完成: 插入 {inserted}, 跳过 {skipped}, 总计 {len(all_events)}")
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
