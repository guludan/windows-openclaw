"""
通用缓存模块 - 所有采集脚本共用
用法:
  from cache_utils import get_cache, cached_fetch, cached_tavily
  
  r = get_cache()
  urls = cached_tavily(r, "粟裕 战役", tavily_api_search)
  html = cached_fetch(r, "https://example.com")
"""
import hashlib, json, time, redis as redis_lib

def get_cache():
    """获取 Redis 连接"""
    return redis_lib.Redis(
        host='127.0.0.1', port=6379,
        password='fc80763c5c42977ed6b0fd7d27e1bb7f',
        decode_responses=True,
        socket_timeout=5,
    )

def _key(prefix, *parts):
    h = hashlib.md5(':'.join(str(p) for p in parts).encode()).hexdigest()[:12]
    return f"{prefix}:{h}"

# ============ Tavily 搜索缓存 ============

def cached_tavily(r, query, search_fn, ttl_days=7):
    """
    缓存 Tavily 搜索结果。
    search_fn(query) -> list[dict] 
    返回搜索结果列表，命中缓存时跳过 API 调用。
    """
    key = f"tavily:{hashlib.md5(query.encode()).hexdigest()}"
    cached = r.get(key)
    if cached:
        return json.loads(cached)
    results = search_fn(query)
    if results:
        r.setex(key, ttl_days * 86400, json.dumps(results))
    return results

# ============ 网页内容缓存 ============

def cached_fetch(r, url, fetch_fn, ttl_days=30):
    """
    缓存网页抓取内容。
    fetch_fn(url) -> str|None
    返回文本内容，命中缓存时跳过网络请求。
    """
    key = f"page:{hashlib.md5(url.encode()).hexdigest()}"
    cached = r.get(key)
    if cached:
        return cached
    text = fetch_fn(url)
    if text and len(text) >= 200:
        r.setex(key, ttl_days * 86400, text)
    return text

def cached_zhihu(r, url, fetch_fn, cookie, ttl_days=30):
    """缓存知乎文章（需要 cookie 的特殊版本）"""
    key = f"zhihu:{hashlib.md5(url.encode()).hexdigest()}"
    cached = r.get(key)
    if cached:
        return json.loads(cached)  # (title, text)
    result = fetch_fn(url, cookie)
    if result and result[1] and len(result[1]) >= 200:
        r.setex(key, ttl_days * 86400, json.dumps(result))
    return result

# ============ STT 转录缓存 ============

def cached_stt(r, audio_path, transcribe_fn):
    """
    缓存 STT 转录结果（基于文件路径+大小）。
    transcribe_fn(audio_path) -> str|None
    """
    import os
    size = os.path.getsize(audio_path) if os.path.exists(audio_path) else 0
    key = f"stt:{hashlib.md5(f'{audio_path}:{size}'.encode()).hexdigest()}"
    cached = r.get(key)
    if cached:
        return cached
    text = transcribe_fn(audio_path)
    if text and len(text) >= 50:
        r.set(key, text)  # STT 结果永久缓存
    return text

# ============ 统计信息 ============

def cache_stats(r):
    """查看缓存使用情况"""
    info = r.info()
    stats = {}
    for prefix in ['tavily', 'page', 'zhihu', 'stt', 'baike']:
        keys = r.keys(f"{prefix}:*")
        stats[prefix] = len(keys)
    return {
        'used_memory': info.get('used_memory_human', '?'),
        'keys_total': info.get('db_size', 0),
        'by_prefix': stats,
    }
