#!/usr/bin/env python3
"""
时事信息采集 → Redis缓存 + 长效化存储
支持: 财联社(AKShare)、A股行情、FRED宏观数据

存储结构 (Redis DB0):
  cls:daily:{date}     Hash  TTL=30天  当日电报 {md5(title12): json}
  cls:all              Hash  永久       全量去重电报
  cls:latest           List  永久       最新500条id
  market:daily:{date}  Hash  TTL=90天  当日市场快照 {index_code: json}
  market:latest        Hash  永久       最新行情快照
  fred:{series_id}     Hash  永久       FRED观测值 {date: value}

Usage:
    python cache_news.py              # 抓取所有源
    python cache_news.py --cls        # 仅财联社
    python cache_news.py --market     # 仅市场行情
    python cache_news.py --fred       # 仅FRED
    python cache_news.py --query 关键词  # 搜索缓存
"""

import redis, json, time, hashlib, sys, re, os
from datetime import datetime, timedelta

try:
    import akshare as ak
except ImportError:
    ak = None

try:
    import requests
except ImportError:
    requests = None

R = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)

# === 配置 ===
FRED_KEY = os.environ.get("FRED_API_KEY", "")
AKSHARE_INDICES = {
    "sh000001": "上证指数", "sh000300": "沪深300",
    "sz399001": "深证成指", "sz399006": "创业板指",
    "sh000016": "上证50", "sh000905": "中证500", "sh000852": "中证1000",
}
FRED_SERIES = {
    "SP500": "标普500", "DGS10": "10年美债", "DTWEXBGS": "美元指数",
    "DCOILBRENTEU": "布伦特原油", "VIXCLS": "VIX恐慌指数",
    "DEXCHUS": "美元人民币", "NIKKEI225": "日经225",
    "CPIAUCSL": "美国CPI", "GDP": "美国GDP", "UNRATE": "失业率",
    "GFDEBTN": "美国国债",
}

def md5k(s):
    return hashlib.md5(s.encode()).hexdigest()[:12]


# ============ 财联社 ============
def fetch_cls():
    """抓取财联社快讯 → Redis"""
    if not ak:
        print("  ❌ akshare未安装"); return 0
    print("📡 财联社快讯...")
    try:
        df = ak.stock_info_global_cls()
    except Exception as e:
        print(f"  ❌ {e}"); return 0

    today = time.strftime("%Y-%m-%d")
    day_key = f"cls:daily:{today}"
    pipe = R.pipeline()
    added = 0

    for _, row in df.iterrows():
        title = str(row.get("标题", ""))
        if not title: continue
        content = str(row.get("内容", ""))
        pub_date = str(row.get("发布日期", ""))
        pub_time = str(row.get("发布时间", ""))

        item = {
            "source": "cls", "title": title, "content": content[:1000],
            "pub_date": pub_date, "pub_time": pub_time,
            "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        key = md5k(title)
        if R.hexists("cls:all", key): continue
        added += 1
        j = json.dumps(item, ensure_ascii=False, default=str)
        pipe.hset(day_key, key, j)
        pipe.hset("cls:all", key, j)
        pipe.lrem("cls:latest", 0, key)
        pipe.lpush("cls:latest", key)
        pipe.ltrim("cls:latest", 0, 499)

    pipe.expire(day_key, 30 * 86400)
    pipe.execute()
    print(f"  ✅ +{added}条 (全量: {R.hlen('cls:all')})")
    return added


# ============ A股行情 ============
def fetch_market():
    """A股行情快照 → Redis"""
    if not ak:
        print("  ❌ akshare未安装"); return 0
    print("📊 A股行情快照...")
    today = time.strftime("%Y-%m-%d")
    day_key = f"market:daily:{today}"
    pipe = R.pipeline()
    added = 0

    for code, name in AKSHARE_INDICES.items():
        try:
            df = ak.stock_zh_index_daily(symbol=code)
            if df.empty: continue
            last = df.iloc[-1]
            item = {
                "code": code, "name": name,
                "date": str(last["date"]),
                "open": float(last["open"]), "high": float(last["high"]),
                "low": float(last["low"]), "close": float(last["close"]),
                "volume": float(last["volume"]),
                "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            j = json.dumps(item, ensure_ascii=False)
            pipe.hset(day_key, code, j)
            pipe.hset("market:latest", code, j)
            added += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  {name}: {e}")

    # 港股
    try:
        hk = ak.stock_hk_index_daily_sina(symbol="HSI")
        if not hk.empty:
            last = hk.iloc[-1]
            item = {"code": "HSI", "name": "恒生指数",
                    "date": str(last.get("date", "")),
                    "open": float(last["open"]), "high": float(last["high"]),
                    "low": float(last["low"]), "close": float(last["close"]),
                    "volume": float(last.get("volume", 0)),
                    "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            j = json.dumps(item, ensure_ascii=False)
            pipe.hset(day_key, "HSI", j)
            pipe.hset("market:latest", "HSI", j)
            added += 1
    except Exception as e:
        print(f"  恒生: {e}")

    pipe.expire(day_key, 90 * 86400)
    pipe.execute()
    print(f"  ✅ {added}个指数")
    return added


# ============ FRED ============
def fetch_fred():
    """FRED宏观数据 → Redis"""
    if not requests:
        print("  ❌ requests未安装"); return 0
    print("🏛️ FRED宏观经济...")
    added = 0
    for sid, name in FRED_SERIES.items():
        try:
            url = (f"https://api.stlouisfed.org/fred/series/observations"
                   f"?series_id={sid}&api_key={FRED_KEY}&file_type=json"
                   f"&observation_start=2010-01-01&limit=100")
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                print(f"  {name}: HTTP {r.status_code}"); continue
            obs = r.json().get("observations", [])
            pipe = R.pipeline()
            for o in obs:
                date, val = o.get("date", ""), o.get("value", ".")
                if val == ".": continue
                pipe.hset(f"fred:{sid}", date, val)
            pipe.execute()
            print(f"  ✅ {name}({sid}): {len(obs)} obs")
            added += len(obs)
            time.sleep(0.2)
        except Exception as e:
            print(f"  {name}: {e}")
    return added


# ============ 搜索 ============
def query_cache(keyword, days=7):
    """搜索缓存的时事信息"""
    results = []
    keyword_lower = keyword.lower()

    # 搜索最新列表
    all_ids = R.lrange("cls:latest", 0, -1)
    for lid in all_ids:
        item = R.hget("cls:all", lid)
        if not item: continue
        item = json.loads(item)
        if keyword_lower in item.get("title", "").lower() or keyword_lower in item.get("content", "").lower():
            results.append(item)

    # 也搜天级
    today = datetime.now()
    for d in range(days):
        date = (today - timedelta(days=d)).strftime("%Y-%m-%d")
        day_key = f"cls:daily:{date}"
        if not R.exists(day_key): continue
        for lid, item_str in R.hgetall(day_key).items():
            item = json.loads(item_str)
            if keyword_lower in item.get("title", "").lower() or keyword_lower in item.get("content", "").lower():
                if not any(r["title"] == item["title"] for r in results):
                    results.append(item)

    print(f"🔍 '{keyword}' 近{days}天: {len(results)}条")
    for r in results[:20]:
        print(f"  [{r.get('pub_time','')}] {r['title'][:80]}")
    return results


# ============ 统计 ============
def stats():
    print("📊 Redis缓存统计:")
    print(f"  财联社全量: {R.hlen('cls:all')}条")
    print(f"  财联社最新列表: {R.llen('cls:latest')}条")
    for key in R.scan_iter("cls:daily:*"):
        print(f"  {key}: {R.hlen(key)}条")
    print(f"  市场最新: {R.hlen('market:latest')}个指数")
    for key in R.scan_iter("fred:*"):
        print(f"  {key}: {R.hlen(key)} obs")
    print(f"  Redis总key数: {R.dbsize()}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--query" in args:
        idx = args.index("--query") + 1
        query_cache(args[idx] if idx < len(args) else "")
    elif "--stats" in args:
        stats()
    elif "--cls" in args:
        fetch_cls()
    elif "--market" in args:
        fetch_market()
    elif "--fred" in args:
        fetch_fred()
    else:
        print("=" * 50)
        print("📰 时事信息缓存系统")
        print("=" * 50)
        fetch_cls()
        # 建tag索引
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from cache_news_tags import build_tag_index
        build_tag_index(recent_only=True)
        fetch_market()
        stats()
