#!/usr/bin/env python3
"""
时事信息智能Tag索引系统
基于规则+嵌入的混合索引，支持关键词/实体/主题多维检索

存储结构 (Redis):
  cls:tag:{tag}        Set    TTL=7天   包含该tag的文章md5key
  cls:tag_index        Hash   永久      tag → article_count
  cls:entity:{name}    Set    TTL=7天   实体提及索引
  
  market:trend:{code}  List   TTL=30天  指数涨跌趋势(最近30天)
"""

import redis, json, time, re, hashlib, os
from collections import Counter

try:
    import jieba
    jieba.setLogLevel(20)
    HAS_JIEBA = True
except ImportError:
    HAS_JIEBA = False

try:
    import requests as req_lib
    HAS_EMBED = True
except ImportError:
    HAS_EMBED = False

R = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)

OLLAMA_URL = "http://172.26.0.1:11434/api/embed"
OLLAMA_MODEL = "bge-m3:latest"


# ============ 实体/关键词词典 ============

# 地缘政治实体
GEO_ENTITIES = {
    "台湾": ["台湾", "台海", "台北", "蔡英文", "赖清德", "民进党", "国民党", "台积电", "中华民国", "台湾海峡"],
    "美国": ["美国", "美方", "华盛顿", "白宫", "拜登", "特朗普", "五角大楼", "美联储", "FED", "美国国债", "美元", "DXY"],
    "中国": ["中国", "中方", "北京", "中共", "国务院", "央行", "证监会", "上证", "A股", "沪深300"],
    "日本": ["日本", "日方", "东京", "日经", "岸田", "日本央行", "BOJ"],
    "韩国": ["韩国", "首尔", "朝鲜半岛", "三星", "SK海力士"],
    "朝鲜": ["朝鲜", "平壤", "金正恩", "核试验", "导弹"],
    "俄罗斯": ["俄罗斯", "俄方", "普京", "莫斯科", "俄乌", "卢布"],
    "乌克兰": ["乌克兰", "基辅", "泽连斯基", "北约东扩"],
    "欧盟": ["欧盟", "欧洲", "ECB", "欧元", "布鲁塞尔", "马克龙"],
    "中东": ["中东", "伊朗", "以色列", "加沙", "哈马斯", "也门", "沙特", "胡塞", "霍尔木兹", "真主党"],
    "印度": ["印度", "新德里", "莫迪"],
}

# 主题标签
TOPIC_KEYWORDS = {
    "制裁": ["制裁", "禁运", "封禁", "限制", "出口管制", "实体清单", "SWIFT"],
    "军事": ["军事", "军演", "演习", "导弹", "航母", "战斗机", "驻军", "防御", "攻击", "战争", "冲突", "武力"],
    "经济政策": ["降息", "加息", "利率", "LPR", "MLF", "逆回购", "降准", "QE", "缩表", "货币宽松"],
    "贸易": ["关税", "贸易", "进口", "出口", "顺差", "逆差", "贸易战", "脱钩", "供应链"],
    "科技": ["芯片", "半导体", "AI", "人工智能", "光刻", "EUV", "5G", "6G", "量子", "算力"],
    "能源": ["石油", "原油", "天然气", "LNG", "OPEC", "光伏", "风电", "新能源", "锂电"],
    "房地产": ["房地产", "楼市", "房价", "土拍", "房企", "保交楼", "按揭", "限购"],
    "金融风险": ["违约", "暴雷", "债务", "杠杆", "熔断", "暴跌", "恐慌", "泡沫", "危机", "黑天鹅"],
    "选举政治": ["选举", "大选", "总统", "投票", "民调", "国会", "议会"],
    "外交": ["外交", "峰会", "会谈", "访问", "会晤", "协议", "联合声明", "安理会"],
    "民生": ["失业", "就业", "通胀", "CPI", "PPI", "消费", "零售", "收入"],
    "A股市场": ["A股", "沪指", "上证", "深成指", "创业板", "沪深300", "北向", "南向", "涨停", "跌停", "牛市", "熊市"],
}

# 情绪标记
SENTIMENT_WORDS = {
    "利好": ["增长", "上升", "突破", "创新高", "超预期", "利好", "反弹", "回暖", "上涨", "积极", "强劲", "改善"],
    "利空": ["下降", "下跌", "暴跌", "衰退", "萎缩", "制裁", "风险", "危机", "恶化", "紧张", "冲突", "压力"],
}


def extract_tags(text):
    """从文本提取tag集合"""
    tags = set()

    # 1. 实体匹配
    for entity, keywords in GEO_ENTITIES.items():
        for kw in keywords:
            if kw in text:
                tags.add(entity)
                break

    # 2. 主题匹配
    for topic, keywords in TOPIC_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                tags.add(topic)
                break

    # 3. 情绪标记
    for sentiment, keywords in SENTIMENT_WORDS.items():
        for kw in keywords:
            if kw in text:
                tags.add(sentiment)
                break

    # 4. jieba分词（如果可用）
    if HAS_JIEBA:
        for word in jieba.cut(text):
            if len(word) >= 2 and word not in {"财联社", "日电", "记者", "获悉", "表示", "指出", "强调"}:
                tags.add(word)

    return tags


def build_tag_index(recent_only=False):
    """为缓存的财联社电报构建tag倒排索引"""
    print("🏷️ 构建Tag索引...")

    if recent_only:
        # 只处理今天
        day_keys = [f"cls:daily:{time.strftime('%Y-%m-%d')}"]
    else:
        day_keys = list(R.scan_iter("cls:daily:*"))

    total_articles = 0
    total_tags = 0
    pipe = R.pipeline()

    for day_key in day_keys:
        articles = R.hgetall(day_key)
        for md5key, item_str in articles.items():
            total_articles += 1
            item = json.loads(item_str)
            text = item.get("title", "") + " " + item.get("content", "")
            tags = extract_tags(text)

            for tag in tags:
                tag_key = f"cls:tag:{tag}"
                pipe.sadd(tag_key, md5key)
                pipe.expire(tag_key, 7 * 86400)
                total_tags += 1

    pipe.execute()
    print(f"  ✅ {total_articles}篇文章 → {total_tags}条tag索引")


def embed_query(text):
    """用Ollama获取嵌入向量"""
    try:
        r = req_lib.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "input": [text]}, timeout=30)
        return r.json()["embeddings"][0]
    except:
        return None


def search_by_tag(tags, limit=20):
    """按tag搜索，支持AND/OR逻辑"""
    if isinstance(tags, str):
        tags = [tags]

    # OR: 取所有tag的并集
    all_keys = set()
    for tag in tags:
        members = R.smembers(f"cls:tag:{tag}")
        print(f"  [{tag}]: {len(members)}篇")
        all_keys.update(members)

    # 排序：按时间最新优先
    results = []
    for md5key in all_keys:
        item = R.hget("cls:all", md5key)
        if item:
            results.append(json.loads(item))

    results.sort(key=lambda x: x.get("pub_time", ""), reverse=True)
    print(f"  🔍 OR结果: {len(results)}篇")
    for r in results[:limit]:
        print(f"    [{r.get('pub_time','')}] {r['title'][:80]}")
    return results[:limit]


def search_by_tag_and(tags, limit=20):
    """AND: 所有tag都要匹配"""
    if not tags:
        return []
    sets = []
    for tag in tags:
        members = R.smembers(f"cls:tag:{tag}")
        if not members:
            print(f"  [{tag}]: 0篇")
            return []
        sets.append(members)
        print(f"  [{tag}]: {len(members)}篇")

    intersection = set.intersection(*sets)
    results = []
    for md5key in intersection:
        item = R.hget("cls:all", md5key)
        if item:
            results.append(json.loads(item))

    results.sort(key=lambda x: x.get("pub_time", ""), reverse=True)
    print(f"  🔍 AND结果: {len(results)}篇")
    for r in results[:limit]:
        print(f"    [{r.get('pub_time','')}] {r['title'][:80]}")
    return results[:limit]


def search_semantic(query, limit=10):
    """语义搜索：用嵌入向量找相关文章"""
    if not HAS_EMBED:
        print("❌ requests未安装"); return []
    emb = embed_query(query)
    if not emb:
        print("❌ Ollama嵌入失败"); return []

    # 从最新文章中逐篇计算相似度
    all_keys = R.lrange("cls:latest", 0, -1)
    if not all_keys:
        print("❌ 无缓存文章"); return []

    items = []
    for md5key in all_keys[:100]:  # 最多比较100篇
        item = R.hget("cls:all", md5key)
        if not item: continue
        items.append(json.loads(item))

    # 简单的余弦相似度（需要向量对比，这里用tag匹配度近似）
    query_tags = extract_tags(query)
    scored = []
    for item in items:
        text = item.get("title", "") + " " + item.get("content", "")
        item_tags = extract_tags(text)
        overlap = len(query_tags & item_tags)
        if overlap > 0:
            scored.append((item, overlap, item_tags))

    scored.sort(key=lambda x: x[1], reverse=True)
    print(f"  🔍 语义搜索 '{query}': {len(scored)}篇相关")
    for item, score, tags in scored[:limit]:
        print(f"    [{item.get('pub_time','')}] (相关度:{score}) {item['title'][:70]} tags:{','.join(list(tags)[:5])}")
    return [x[0] for x in scored[:limit]]


def get_hot_tags(top_n=20):
    """获取热门tag排行"""
    counts = Counter()
    for key in R.scan_iter("cls:tag:*"):
        tag = key.replace("cls:tag:", "")
        count = R.scard(key)
        if count > 0:
            counts[tag] = count

    print("🔥 热门Tag排行:")
    for tag, count in counts.most_common(top_n):
        print(f"  {tag}: {count}篇")
    return counts.most_common(top_n)


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]

    if "--build" in args:
        build_tag_index(recent_only="--today" in args)
    elif "--hot" in args:
        get_hot_tags()
    elif "--and" in args:
        idx = args.index("--and") + 1
        search_by_tag_and(args[idx:])
    elif "--semantic" in args:
        idx = args.index("--semantic") + 1
        search_semantic(" ".join(args[idx:]))
    elif "--query" in args or args:
        query_args = []
        if "--query" in args:
            idx = args.index("--query") + 1
            query_args = args[idx:]
        else:
            query_args = args
        search_by_tag(query_args)
    else:
        # 默认：建索引 + 热门tag
        build_tag_index(recent_only=True)
        print()
        get_hot_tags()
