#!/home/tearsyu/miniconda3/envs/tt/bin/python3
"""
数据获取管线 — 爬取新闻联播 + 财联社快讯 + 生成事件列表

用法:
  # 阶段1: 批量抓取新闻联播历史数据
  python3 data_acquisition.py --cctv --start 20190101 --end 20241231
  
  # 阶段2: 增量更新（每日cron）
  python3 data_acquisition.py --cctv --latest
  
  # 阶段3: 财联社快讯归档
  python3 data_acquisition.py --cls --archive
  
  # 阶段4: 生成可导入event_list的事件
  python3 data_acquisition.py --build-events --from cctv --dry-run

依赖: akshare, asyncpg, jieba (同conda tt环境)
"""

import asyncio, json, sys, os, argparse, logging, re, hashlib
from pathlib import Path
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('data_acquisition')

PG_DSN = "postgresql://guludan:toto@192.168.3.14:5432/guludan"

# ─── 缓存目录（防止重复抓取）───
CACHE_DIR = Path("/home/tearsyu/.openclaw/workspace/cache/news_cctv")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ─── 政策关键词（用于筛选政策相关事件）───
POLICY_KEYWORDS = [
    "国务院", "常务会议", "政策", "通知", "意见", "方案", "规划",
    "印发", "发布", "实施", "促进", "支持", "推动", "加强",
    "扩大内需", "降准", "降息", "减税", "降费", "补贴",
    "改革", "开放", "创新", "数字化", "绿色", "碳中和",
    "制造业", "新能源", "半导体", "人工智能", "数字经济",
    "民营经济", "中小企业", "资本市场", "注册制",
    "乡村振兴", "一带一路", "长江经济带", "粤港澳",
    "房地产", "保障房", "城中村", "集采", "医保",
    "国防", "军工", "科技自立",
]

# ═══════════════════════════════════════
#   1. 新闻联播数据抓取
# ═══════════════════════════════════════

def fetch_cctv_day(date_str: str) -> Optional[List[Dict]]:
    """抓取某一天的新闻联播文字稿
    
    Args:
        date_str: "YYYYMMDD" 格式
    
    Returns:
        [{"title": ..., "content": ..., "index": ...}, ...] 或 None
    """
    import akshare as ak
    
    try:
        df = ak.news_cctv(date=date_str)
    except Exception as e:
        log.warning(f"  {date_str}: 抓取失败 - {e}")
        return None
    
    if df is None or df.empty:
        return None
    
    items = []
    for i, row in df.iterrows():
        items.append({
            "title": str(row.get("title", "")).strip(),
            "content": str(row.get("content", "")).strip(),
            "index": i + 1,
        })
    
    return items


def get_trade_dates_for_cctv(cctv_date_str: str) -> List[str]:
    """给定新闻联播日期，返回可用于匹配的交易日字符串列表
    （一般是当天，如果当天非交易日则往后找到最近一个交易日）
    这里简单返回当天，trade_date匹配在event_study阶段做
    """
    return [cctv_date_str]


def is_policy_related(title: str, content: str) -> Tuple[bool, float]:
    """判断是否与政策/资本市场相关
    
    改进规则：
    - 标题含经济金融类词 → 高分
    - 纯外事/军事/体育 → 排除
    - 国务院常务会议等 → 优先标记
    
    Returns:
        (is_relevant, relevance_score)
    """
    text = (title + " " + content[:500]).lower()
    score = 0.0
    
    # === 硬性包含规则（只要有就标记）===
    high_priority = [
        "国务院常务会议", "国常会", "降准", "降息", "减税",
        "特别国债", "专项债", "积极财政", "稳健货币",
        "注册制", "资本市场改革", "中长期资金",
        "新能源", "人工智能", "数字经济", "半导体",
        "扩大内需", "以旧换新", "房地产调控",
        "保交楼", "城中村改造", "新型工业化",
        "民营经济", "中小企业", "减税降费",
        "集采", "创新药", "乡村振兴",
    ]
    for kw in high_priority:
        if kw.lower() in text:
            return True, 3.0 + len(kw) * 0.1
    
    # === 低位词匹配 ===
    for kw in POLICY_KEYWORDS:
        if kw.lower() in text:
            score += 1.0
        if len(kw) >= 4 and kw.lower() in text:
            score += 0.5  # 长词更高权
    
    # === 排除列表 ===
    exclude_patterns = [
        "会见.*总统", "会见.*主席", "致贺信", "贺电",
        "举行仪式", "欢迎仪式", "陪同.*检阅",
        "军乐团奏", "鸣礼炮", "军事行动", "打击.*目标",
        "联播快讯", "国际联播快讯",
        "文艺", "书画", "展览", "考古",
        "气象", "天气", "河湖", "防汛",
        "龙舟", "体育赛事", "运动会",
    ]
    exclusion_score = 0.0
    for pat in exclude_patterns:
        if re.search(pat, title):
            exclusion_score += 3.0
    
    # === 纯外事类（会见外宾等，除非内容含具体政策）===
    diplomatic_keywords = ["会见", "会谈", "会晤", "访问", "接见", "礼节性"]
    is_diplomatic = any(kw in title for kw in diplomatic_keywords)
    has_policy_content = any(kw.lower() in content[:500].lower() 
                             for kw in ["合作", "协议", "经贸", "投资", "一带一路", "基础设施"])
    
    if is_diplomatic and not has_policy_content:
        exclusion_score += 2.0
    
    net_score = score - exclusion_score
    return net_score >= 1.5, net_score


async def batch_fetch_cctv(start: str, end: str, batch_size: int = 30):
    """批量抓取新闻联播，存入缓存文件
    
    策略：每批 batch_size 天，中间sleep避免被封
    """
    import asyncpg
    
    start_dt = datetime.strptime(start, "%Y%m%d")
    end_dt = datetime.strptime(end, "%Y%m%d")
    
    # 生成日期列表
    all_dates = []
    d = start_dt
    while d <= end_dt:
        all_dates.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    
    log.info(f"新闻联播抓取: {len(all_dates)} 天 ({start} ~ {end})")
    
    total_fetched = 0
    total_policy = 0
    conn = await asyncpg.connect(PG_DSN) if '--no-db' not in sys.argv else None
    
    for batch_start in range(0, len(all_dates), batch_size):
        batch_dates = all_dates[batch_start:batch_start + batch_size]
        
        for date_str in batch_dates:
            # 缓存检查
            cache_file = CACHE_DIR / f"{date_str}.json"
            if cache_file.exists():
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                if isinstance(cache_data, list) and len(cache_data) > 0:
                    total_fetched += len(cache_data)
                    policy_items = [it for it in cache_data if it.get('_is_policy', False)]
                    total_policy += len(policy_items)
                    log.debug(f"  {date_str}: 命中缓存, {len(cache_data)}条, {len(policy_items)}条政策")
                    continue
            
            items = fetch_cctv_day(date_str)
            
            if items:
                # 政策相关性判断
                for item in items:
                    is_rel, score = is_policy_related(item['title'], item['content'])
                    item['_is_policy'] = is_rel
                    item['_relevance_score'] = score
                
                # 写缓存
                with open(cache_file, 'w', encoding='utf-8') as f:
                    json.dump(items, f, ensure_ascii=False, indent=2)
                
                total_fetched += len(items)
                policy_in_batch = sum(1 for it in items if it['_is_policy'])
                total_policy += policy_in_batch
                
                log.info(f"  {date_str}: {len(items)}条 (政策:{policy_in_batch})   累计: {total_fetched}条 政策:{total_policy}")
            else:
                log.warning(f"  {date_str}: 无数据")
        
        # 批间sleep
        if batch_start + batch_size < len(all_dates):
            sleep_s = 5
            log.info(f"  批次完成, sleep {sleep_s}s... (进度 {batch_start + batch_size}/{len(all_dates)})")
            await asyncio.sleep(sleep_s)
    
    if conn:
        await conn.close()
    
    log.info(f"\n✅ 新闻联播抓取完成")
    log.info(f"  总天数: {len(all_dates)}")
    log.info(f"  总条目: {total_fetched}")
    log.info(f"  政策相关: {total_policy}")
    
    return {
        "total_days": len(all_dates),
        "total_items": total_fetched,
        "policy_items": total_policy,
    }


def fetch_latest_cctv() -> List[Dict]:
    """抓取最新一天的新闻联播，返回 items（含 _actual_date 标记真实播出日期）"""
    today = datetime.now().strftime("%Y%m%d")
    items = fetch_cctv_day(today)
    actual_date = today
    if not items:
        # 试试昨天（如果今天还没播）
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        items = fetch_cctv_day(yesterday)
        actual_date = yesterday
    
    if items:
        for item in items:
            is_rel, score = is_policy_related(item['title'], item['content'])
            item['_is_policy'] = is_rel
            item['_relevance_score'] = score
            item['_date'] = actual_date
    
    return items or []


# ═══════════════════════════════════════
#   2. 财联社快讯归档
# ═══════════════════════════════════════

def archive_cls_news():
    """抓取财联社快讯并追加到本地JSON文件"""
    import hashlib
    import requests as req_lib
    
    def _cls_sign(params):
        """财联社新版签名算法: 参数按key.toUpperCase排序后SHA-1"""
        keys = sorted(params.keys(), key=lambda x: x.upper())
        raw = '&'.join([f'{k}={params[k]}' for k in keys])
        return hashlib.sha1(raw.encode()).hexdigest()
    
    try:
        sess = req_lib.Session()
        sess.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.cls.cn/telegraph',
        })
        # 先访问首页获取cookie
        sess.get('https://www.cls.cn/telegraph', timeout=15)
        
        now = int(datetime.now().timestamp())
        params = {
            'os': 'web',
            'sv': '8.7.9',
            'app': 'CailianpressWeb',
            'rn': 50,
            'lastTime': now,
            'name': 'telegraph',
        }
        sign = _cls_sign(params)
        r = sess.get('https://www.cls.cn/api/cache',
                     params={**params, 'sign': sign},
                     timeout=15)
        data = r.json()
        items = data.get('data', {}).get('roll_data', [])
    except Exception as e:
        log.error(f"财联社抓取失败: {e}")
        return []
    
    # 存档文件
    archive_file = CACHE_DIR.parent / "cls_telegraph_archive.json"
    
    # 加载已有
    existing = []
    if archive_file.exists():
        with open(archive_file, 'r', encoding='utf-8') as f:
            existing = json.load(f)
    
    existing_titles = set()
    if existing:
        for item in existing:
            # 兼容新旧格式
            date_key = item.get('date', item.get('发布日期', ''))
            title_key = item.get('title', item.get('标题', ''))[:20]
            sig = f"{date_key}:{title_key}"
            existing_titles.add(sig)
    
    new_items = []
    for item in items:
        title = str(item.get("title", "") or "")
        content = str(item.get("content", "") or "")
        ctime = item.get("ctime", 0)
        pub_date = datetime.fromtimestamp(ctime).strftime("%Y-%m-%d")
        pub_time = datetime.fromtimestamp(ctime).strftime("%H:%M:%S")
        
        sig = f"{pub_date}:{title[:20]}"
        if sig in existing_titles:
            continue
        
        entry = {
            "title": title,
            "content": content,
            "date": pub_date,
            "time": pub_time,
            "level": str(item.get("level", "")),
            "timestamp": datetime.now().isoformat(),
        }
        new_items.append(entry)
        existing.append(entry)
    
    if new_items:
        with open(archive_file, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        log.info(f"财联社归档: {len(new_items)} 条新快讯 (总 {len(existing)} 条)")
    else:
        log.info("财联社: 无新快讯")
    
    return new_items


# ═══════════════════════════════════════
#   3a. 增量同步到 event_list
# ═══════════════════════════════════════

# 交易日映射功能
def _get_trade_dates_for_cctv(cctv_date_str: str) -> List[str]:
    """获取新闻联播日期对应的交易日"""
    return get_trade_dates_for_cctv(cctv_date_str)


def _classify_cctv_item(title: str, content: str) -> Tuple[str, int]:
    """分类新闻联播条目，返回 (category, direction)"""
    # 判断类别
    category = 'policy_general'
    if any(kw in title for kw in ["国务院常务会议", "国常会"]):
        category = "economy"
    elif any(kw in title for kw in ["中共中央", "政治局"]):
        category = "leadership"
    elif any(kw in title for kw in ["习近平", "总书记"]):
        category = "leadership"
    elif any(kw in title for kw in ["经济", "金融", "降准", "降息", "货币", "财政", "减税", "专项债"]):
        category = "economy"
    elif any(kw in title for kw in ["科技", "创新", "AI", "人工智能", "数字", "芯片", "半导体"]):
        category = "technology"
    elif any(kw in title for kw in ["外交", "访问", "会谈", "会见", "国际"]):
        category = "diplomacy"
    elif any(kw in title for kw in ["民生", "扶贫", "就业", "社保", "养老", "教育"]):
        category = "social"
    elif any(kw in title for kw in ["房地产", "住房", "保障房"]):
        category = "economy"
    
    # 方向判断
    direction = 0
    for kw in ["积极", "促进", "支持", "推动", "扩大", "鼓励", "提升", "利好"]:
        if kw in title:
            direction = 1
            break
    for kw in ["限制", "严格", "加强监管", "整治", "处罚", "遏制", "禁止"]:
        if kw in title or kw in content[:200]:
            direction = -1
            break
    
    return category, direction


def _sync_cctv_to_event_list(items: List[Dict], latest_only: bool = True):
    """将新闻联播条目增量写入event_list"""
    import psycopg2
    from datetime import date
    
    conn = psycopg2.connect(**{
        'host': '192.168.3.14', 'port': 5432,
        'user': 'guludan', 'password': 'toto', 'dbname': 'guludan'
    })
    cur = conn.cursor()
    
    # 获取已存在的 (event_date, source, title) 避免重复
    cur.execute("SELECT event_date, source, title FROM event_list WHERE source='news_cctv'")
    existing = {(str(r[0]), r[1], r[2]) for r in cur.fetchall()}
    
    inserted = 0
    for item in items:
        title = item.get('title', '').strip()
        content = item.get('content', '').strip()
        if not title:
            continue
        
        # 获取日期
        cctv_date = item.get('date', item.get('_date', ''))
        if not cctv_date:
            continue
        cctv_date_str = cctv_date[:10] if '-' in cctv_date else cctv_date
        
        event_date = cctv_date_str[:10]
        
        # 分类+方向
        category, direction = _classify_cctv_item(title, content)
        
        key = (event_date, 'news_cctv', title)
        if key in existing:
            continue
        
        try:
            cur.execute("""
                INSERT INTO event_list
                    (event_date, source, source_subtype, title, full_text,
                     category, direction, is_clean)
                VALUES (%s, 'news_cctv', %s, %s, %s, %s, %s, FALSE)
                ON CONFLICT DO NOTHING
            """, (event_date, category, title, content, category, direction))
            inserted += 1
        except Exception as e:
            log.warning(f"  插入失败: {e} - {title[:40]}")
    
    conn.commit()
    cur.close()
    conn.close()
    log.info(f"  event_list: 插入 {inserted} 条新闻联播")


def _sync_recent_to_event_list():
    """将最近缓存同步到 event_list（用于--daily模式）"""
    import psycopg2
    
    conn = psycopg2.connect(**{
        'host': '192.168.3.14', 'port': 5432,
        'user': 'guludan', 'password': 'toto', 'dbname': 'guludan'
    })
    cur = conn.cursor()
    
    # 获取已有事件
    cur.execute("SELECT event_date, source, title FROM event_list")
    existing = {(str(r[0]), r[1], r[2]) for r in cur.fetchall()}
    
    total = 0
    
    # 1. 新闻联播 - 最新缓存文件
    cache_files = sorted(CACHE_DIR.glob("*.json"))
    for cf in cache_files[-3:]:  # 最近3个文件
        try:
            with open(cf, encoding='utf-8') as f:
                items = json.load(f)
        except: continue
        
        date_str = cf.stem
        event_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        
        for item in items:
            title = item.get('title', '').strip()
            content = item.get('content', '').strip()
            if not title:
                continue
            
            key = (event_date, 'news_cctv', title)
            if key in existing:
                continue
            
            category, direction = _classify_cctv_item(title, content)
            try:
                cur.execute("""
                    INSERT INTO event_list
                        (event_date, source, source_subtype, title, full_text,
                         category, direction, is_clean)
                    VALUES (%s, 'news_cctv', %s, %s, %s, %s, %s, FALSE)
                    ON CONFLICT DO NOTHING
                """, (event_date, category, title, content, category, direction))
                total += 1
            except: pass
            existing.add(key)
    
    # 2. 财联社最新
    cls_file = Path('/home/tearsyu/.openclaw/workspace/cache/cls_telegraph_archive.json')
    if cls_file.exists():
        try:
            cls_data = json.load(open(cls_file))
            cls_items = list(cls_data.values()) if isinstance(cls_data, dict) else cls_data
            for item in cls_items[-10:]:
                title = item.get('title', '').strip() or item.get('content', '')[:80]
                date_str = item.get('date', '')[:10]
                if not date_str or not title:
                    continue
                key = (date_str, 'cls_telegraph', title)
                if key in existing:
                    continue
                try:
                    cur.execute("""
                        INSERT INTO event_list
                            (event_date, source, source_subtype, title, full_text,
                             category, direction, is_clean)
                        VALUES (%s, 'cls_telegraph', 'market_news', %s, %s, 'market_news', 0, FALSE)
                        ON CONFLICT DO NOTHING
                    """, (date_str, title, item.get('content', '')))
                    total += 1
                except: pass
                existing.add(key)
        except: pass
    
    conn.commit()
    cur.close()
    conn.close()
    log.info(f"  event_list: 插入 {total} 条新事件")


# ═══════════════════════════════════════
#   4. 构建事件列表（从新闻联播缓存 → event_list）
# ═══════════════════════════════════════

async def build_events_from_cctv(dry_run: bool = True, min_relevance: float = 1.0):
    """从缓存文件构建事件列表并写入event_list"""
    import asyncpg
    
    # 扫描所有缓存文件
    cache_files = sorted(CACHE_DIR.glob("*.json"))
    log.info(f"扫描缓存: {len(cache_files)} 个文件")
    
    conn = await asyncpg.connect(PG_DSN) if not dry_run else None
    
    # 获取已有事件日期（去重）
    existing_dates = set()
    if conn:
        rows = await conn.fetch("SELECT DISTINCT event_date::text FROM event_list")
        existing_dates = {str(r['event_date']) for r in rows}
    
    total_events = 0
    new_events = 0
    
    for cf in cache_files:
        date_str = cf.stem  # YYYYMMDD
        
        try:
            with open(cf, 'r', encoding='utf-8') as f:
                items = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            log.warning(f"  {date_str}: 读取失败 - {e}")
            continue
        
        for item in items:
            if not item.get('_is_policy', False):
                continue
            if item.get('_relevance_score', 0) < min_relevance:
                continue
            
            total_events += 1
            
            # 格式化为event_list行
            title = item['title']
            content = item['content']
            source = "新闻联播"
            source_subtype = ""
            
            # 判断类别
            category = "综合"
            if any(kw in title for kw in ["国务院常务会议", "国常会"]):
                category = "国务院常务会议"
            elif any(kw in title for kw in ["中共中央", "政治局"]):
                category = "中央会议"
            elif any(kw in title for kw in ["央", "降准", "降息", "货币"]):
                category = "货币政策"
                source_subtype = "央行"
            elif any(kw in title for kw in ["财政", "减税", "专项债"]):
                category = "财政政策"
                source_subtype = "财政部"
            elif any(kw in title for kw in ["房地产", "住房", "城中村", "保障房"]):
                category = "房地产"
            elif any(kw in title for kw in ["新能源", "光伏", "风电", "电动"]):
                category = "新能源产业"
            elif any(kw in title for kw in ["半导体", "芯片", "人工智能", "AI", "数字经济"]):
                category = "科技产业"
            
            # 方向判断（简单用关键词）
            direction = 0
            pos_kw = ["积极", "促进", "支持", "推动", "扩大", "鼓励", "提升", "利好"]
            neg_kw = ["限制", "严格", "加强监管", "整治", "处罚", "遏制", "禁止"]
            for kw in pos_kw:
                if kw in title or kw in content[:200]:
                    direction = 1
                    break
            for kw in neg_kw:
                if kw in title or kw in content[:200]:
                    direction = -1
                    break
            
            # 格式化日期为 YYYY-MM-DD
            event_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            
            if event_date in existing_dates and conn:
                continue  # 已存在
            
            if dry_run:
                log.info(f"  [{event_date}] {source} [{category}] {title[:60]} (方向:{direction})")
            else:
                # 写入event_list
                await conn.execute("""
                    INSERT INTO event_list
                        (event_date, source, source_subtype, title, full_text,
                         category, direction, is_clean)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE)
                    ON CONFLICT DO NOTHING
                """, event_date, source, source_subtype, title, content,
                    category, direction)
                new_events += 1
    
    if conn:
        await conn.close()
    
    log.info(f"\n✅ 事件构建完成")
    log.info(f"  符合政策条件的事件: {total_events}")
    if dry_run:
        log.info(f"  (dry-run模式, 未写入数据库)")
    else:
        log.info(f"  写入数据库: {new_events} 条")
    
    return {"total": total_events, "new": new_events}


# ═══════════════════════════════════════
#   4. 国务院政策文件爬虫（带URL去重+索引）
# ═══════════════════════════════════════
#
# 缓存结构:
#   cache/gov_policies_index.json  ← URL→元数据索引（方便去重+统计）
#   cache/gov_policies/<hash>.json ← 单篇详情
#
# 去重策略: 每个文件URL只抓一次（通过索引+缓存文件双重校验）
# 标题清洗: 去噪（_中国政府网、_政策解读等冗余后缀）

POLICY_INDEX_FILE = Path("/home/tearsyu/.openclaw/workspace/cache/gov_policies_index.json")
POLICY_DIR = Path("/home/tearsyu/.openclaw/workspace/cache/gov_policies")
POLICY_DIR.mkdir(parents=True, exist_ok=True)


def clean_policy_title(raw: str) -> str:
    """清洗标题，去除冗余后缀"""
    noise = [
        '_中国政府网', '_最新政策', '_政策解读',
        '_国务院办公厅政府信息公开指南（试行）', '_信息公开C330_政策',
        '_矿产', '_附件', '（国务院办公厅政府信息公开指南（试行））',
    ]
    for n in noise:
        raw = raw.replace(n, '')
    raw = re.sub(r'\s+', ' ', raw).strip()
    # 去掉末尾多余的空白分割
    raw = re.sub(r'[\s_]+$', '', raw)
    return raw


def _policy_cache_url(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def load_policy_index() -> Dict[str, dict]:
    """加载索引（key=URL）"""
    if POLICY_INDEX_FILE.exists():
        with open(POLICY_INDEX_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_policy_index(idx: Dict[str, dict]):
    with open(POLICY_INDEX_FILE, 'w', encoding='utf-8') as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)


def fetch_policy_detail(url: str) -> Optional[Dict]:
    """抓取单个政策文件详情页（静态HTML）
    
    提取字段:
    - title: 清洗后的标题
    - doc_number: 发文字号（国令第X号/国发〔202X〕X号/国办发〔202X〕X号）
    - pub_date: 发布日期
    - signed_date: 成文日期
    - doc_type: 文件类型（国务院令/国务院发文/国办发文/批复/通知/政策解读）
    - theme: 主题分类
    - content: 正文（~8000字）
    """
    import requests
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.encoding = 'utf-8'
    except Exception as e:
        log.warning(f"  HTTP失败 {url[-40:]}: {e}")
        return None
    if r.status_code != 200:
        return None
    
    html = r.text
    
    # --- 标题 ---
    title = ""
    m = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
    if m:
        title = clean_policy_title(m.group(1))
    if not title:
        m = re.search(r'<h[12][^>]*>\s*([^<]+?)\s*</h[12]>', html)
        if m:
            title = clean_policy_title(re.sub(r'<[^>]+>', '', m.group(1)))
    
    # --- 发文字号 ---
    doc_number = ""
    m = re.search(r'发文字号[：:]([^<]*?)(?:<|\s{2,})', html)
    if m:
        doc_number = m.group(1).strip()
    
    # --- 发布日期 ---
    pub_date = ""
    m = re.search(r'发布日期[：:]\s*(\d{4})[年\-](\d{1,2})[月\-](\d{1,2})', html)
    if m:
        pub_date = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    if not pub_date:
        m = re.search(r'/(\d{6})/', url)
        if m:
            pub_date = f"{m.group(1)[:4]}-{m.group(1)[4:6]}"
    
    # --- 成文日期 ---
    signed_date = ""
    m = re.search(r'成文日期[：:]\s*(\d{4})[年\-](\d{1,2})[月\-](\d{1,2})', html)
    if m:
        signed_date = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    
    # --- 主题分类 ---
    theme = ""
    m = re.search(r'主题分类[：:]([^<]*?)(?:<|\s{2,})', html)
    if m:
        theme = m.group(1).strip()
    
    # --- 文件类型 ---
    doc_type = "其他"
    if '国务院令' in html or '国令第' in html or doc_number.startswith('国令'):
        doc_type = "国务院令"
    elif '国发[' in html or '国发〔' in html:
        doc_type = "国务院发文"
    elif '国办发[' in html or '国办发〔' in html:
        doc_type = "国办发文"
    elif '批复' in title:
        doc_type = "国务院批复"
    elif '通知' in title:
        doc_type = "通知"
    elif '解读' in title or '问答' in title:
        doc_type = "政策解读"
    
    # --- 正文提取 ---
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '\n', text)
    lines = [l.strip() for l in text.split('\n') if len(l.strip()) > 20]
    main_content = '\n'.join(lines) if lines else text[:3000]
    
    return {
        'url': url,
        'title': title,
        'doc_type': doc_type,
        'doc_number': doc_number,
        'pub_date': pub_date,
        'signed_date': signed_date,
        'theme': theme,
        'content': main_content[:8000],
    }


def crawl_gov_policies(max_urls: int = 50) -> int:
    """爬取国务院最新政策文件（Playwright获取URL → 详情页）
    
    通过索引实现URL级去重：不会重复抓同一个URL
    
    Args:
        max_urls: 最大抓取URL数
    Returns:
        本次新增（去重后）的条数
    """
    idx = load_policy_index()
    existing_urls = set(idx.keys())
    log.info(f"政策文件索引: 已缓存 {len(existing_urls)} 条")
    
    # 从多个分类页面获取URL列表（requests静态爬取，无需Playwright）
    import requests as req_lib
    source_pages = [
        ("首页", "https://www.gov.cn/zhengce/"),
        ("最新政策", "https://www.gov.cn/zhengce/zuixin/"),
        ("中央文件", "https://www.gov.cn/zhengce/wenjian/zhongyang/"),
        ("信息公开", "https://www.gov.cn/zhengce/xxgk/"),
        ("政策解读", "https://www.gov.cn/zhengce/jiedu/"),
        ("企业政策", "https://www.gov.cn/zhengce/qiye/"),
    ]
    
    all_links = set()
    for label, page_url in source_pages:
        try:
            r = req_lib.get(page_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if r.status_code == 200:
                found = re.findall(r'href=[\'"]([^\'"]*content_\d+[^\'"]*\.htm)[\'"]', r.text)
                for l in found:
                    if l.startswith('./'):
                        l = 'https://www.gov.cn/zhengce/' + l[2:]
                    elif l.startswith('/'):
                        l = 'https://www.gov.cn' + l
                    elif not l.startswith('http'):
                        l = page_url.rstrip('/') + '/' + l
                    all_links.add(l)
        except Exception as e:
            log.warning(f"{label} 抓取失败: {e}")
    
    urls = sorted(all_links)[:max_urls]
    log.info(f"多页面获取 {len(urls)} 个唯一URL (来自{len(source_pages)}个分类页)")
    
    # 去重
    new_urls = [u for u in urls if u not in existing_urls]
    log.info(f"  其中 {len(new_urls)} 个新URL (跳过 {len(urls) - len(new_urls)} 个已有)")
    
    if not new_urls:
        log.info("✅ 已是最新，无需更新")
        return 0
    
    # 逐个抓取
    fetched = 0
    for url in new_urls:
        h = _policy_cache_url(url)
        cache_file = POLICY_DIR / f"{h}.json"
        
        if cache_file.exists():
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = fetch_policy_detail(url)
            if data:
                with open(cache_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False)
        
        if data:
            idx[url] = {
                'title': data.get('title', ''),
                'pub_date': data.get('pub_date', ''),
                'doc_number': data.get('doc_number', ''),
                'doc_type': data.get('doc_type', ''),
                'cache_file': f"{h}.json",
            }
            fetched += 1
            log.info(f"  [{data.get('pub_date','?')}] [{data.get('doc_type','?')}] {data.get('title','?')[:60]}")
        else:
            log.warning(f"  空结果 {url[-40:]}")
    
    save_policy_index(idx)
    log.info(f"✅ 政策文件: 本次新增 {fetched} 条, 总计 {len(idx)} 条")
    return fetched


def rebuild_gov_index():
    """从缓存文件重建索引（解决索引文件丢失/损坏）"""
    idx = {}
    for cf in sorted(POLICY_DIR.glob("*.json")):
        try:
            with open(cf, 'r', encoding='utf-8') as f:
                d = json.load(f)
            url = d.get('url', '')
            if url:
                idx[url] = {
                    'title': d.get('title', ''),
                    'pub_date': d.get('pub_date', ''),
                    'doc_number': d.get('doc_number', ''),
                    'doc_type': d.get('doc_type', ''),
                    'cache_file': cf.name,
                }
        except:
            pass
    save_policy_index(idx)
    log.info(f"索引重建: {len(idx)} 条")
    return len(idx)


# ═══════════════════════════════════════
#   CLI入口
# ═══════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="数据获取管线")
    
    # 新闻联播
    parser.add_argument('--cctv', action='store_true', help='抓取新闻联播')
    parser.add_argument('--start', type=str, default="20190101", help='起始日期 YYYYMMDD')
    parser.add_argument('--end', type=str, default="20241231", help='结束日期 YYYYMMDD')
    parser.add_argument('--latest', action='store_true', help='仅抓取最新一期')
    
    # 财联社
    parser.add_argument('--cls', action='store_true', help='财联社快讯归档')
    parser.add_argument('--archive', action='store_true', help='归档模式')
    
    # 事件列表
    parser.add_argument('--build-events', action='store_true', help='从缓存构建事件')
    parser.add_argument('--from', dest='from_source', default='cctv', help='事件来源')
    parser.add_argument('--dry-run', action='store_true', help='仅预览不写入')
    parser.add_argument('--min-relevance', type=float, default=1.5, help='最小相关性阈值')
    
    # 政策文件
    parser.add_argument('--gov', action='store_true', help='爬取国务院最新政策文件')
    parser.add_argument('--pages', type=int, default=3, help='翻页数')
    
    # 一键全量
    parser.add_argument('--daily', action='store_true', help='每日增量更新（新闻联播+财联社+政策文件）')
    
    # 其他
    parser.add_argument('--batch-size', type=int, default=30, help='批量大小')
    parser.add_argument('--no-db', action='store_true', help='不连数据库')
    
    args = parser.parse_args()
    
    if args.cls and args.archive:
        archive_cls_news()
    
    elif args.cctv and args.latest:
        items = fetch_latest_cctv()
        if items:
            log.info(f"最新新闻联播: {len(items)}条")
            for it in items[:5]:
                tag = "📊" if it['_is_policy'] else "  "
                log.info(f"  {tag} {it['title'][:60]}")
            # 先写本地缓存（防PG不可达时丢数据）
            today_str = items[0].get('date', '') or datetime.now().strftime('%Y%m%d')
            cache_file = CACHE_DIR / f"{today_str}.json"
            if not cache_file.exists():
                with open(cache_file, 'w', encoding='utf-8') as f:
                    json.dump(items, f, ensure_ascii=False, indent=2)
                log.info(f"  缓存已写入: {cache_file.name}")
            # 自动写入event_list
            if not args.no_db:
                try:
                    _sync_cctv_to_event_list(items, latest_only=True)
                except Exception as e:
                    log.warning(f"  PG同步失败（数据已缓存，PG恢复后可重跑补同步）: {e}")
        else:
            log.info("无最新数据")
    
    elif args.cctv:
        result = await batch_fetch_cctv(args.start, args.end, args.batch_size)
        print(f"\n结果: {json.dumps(result, ensure_ascii=False)}")
    
    elif args.gov:
        log.info("爬取国务院最新政策文件...")
        n = crawl_gov_policies(max_urls=80)
        if n:
            # 显示最新5条
            idx = load_policy_index()
            sorted_items = sorted(idx.items(), key=lambda x: x[1].get('pub_date',''), reverse=True)[:5]
            for url, meta in sorted_items:
                log.info(f"  [{meta.get('pub_date','?')}] [{meta.get('doc_type','?')}] {meta.get('title','?')[:60]}")
        # 同步到event_list
        if not args.no_db and n:
            log.info("同步gov_policy到event_list...")
            try:
                from scripts.cache2event_list import sync_gov_policy
                sync_gov_policy()
            except Exception as e:
                log.warning(f"  event_list同步失败: {e}")
    
    elif args.daily:
        log.info("=== 每日数据增量更新 ===")
        
        # 1. 财联社
        log.info("\n[1/4] 财联社快讯...")
        try:
            archive_cls_news()
        except Exception as e:
            log.error(f"  财联社失败: {e}")
        
        # 2. 新闻联播最新
        log.info("\n[2/4] 新闻联播...")
        try:
            items = fetch_latest_cctv()
            if items:
                actual_date = items[0].get('_date', datetime.now().strftime("%Y%m%d"))
                cache_file = CACHE_DIR / f"{actual_date}.json"
                if not cache_file.exists():
                    with open(cache_file, 'w', encoding='utf-8') as f:
                        json.dump(items, f, ensure_ascii=False, indent=2)
                    log.info(f"  获取 {len(items)} 条 (政策:{sum(1 for i in items if i['_is_policy'])}), 已缓存 {cache_file.name}")
                else:
                    log.info(f"  获取 {len(items)} 条, 缓存已存在 {cache_file.name}")
            else:
                log.warning("  新闻联播: 无数据")
        except Exception as e:
            log.error(f"  新闻联播失败: {e}")
        
        # 3. 政策文件
        log.info("\n[3/4] 国务院政策文件...")
        try:
            n = crawl_gov_policies(max_urls=50)
            log.info(f"  新增 {n} 条")
        except Exception as e:
            log.error(f"  政策文件失败: {e}")

        # 4. 同步到event_list（增量写入PG）
        log.info("\n[4/4] 写入event_list...")
        try:
            if not args.no_db:
                _sync_recent_to_event_list()
        except Exception as e:
            log.error(f"  event_list同步失败: {e}")
        
        log.info("\n=== 更新完成 ===")
    
    elif args.build_events:
        if args.from_source == 'cctv':
            await build_events_from_cctv(dry_run=args.dry_run, min_relevance=args.min_relevance)
        else:
            log.error(f"未知来源: {args.from_source}")
    
    else:
        parser.print_help()


if __name__ == '__main__':
    asyncio.run(main())
