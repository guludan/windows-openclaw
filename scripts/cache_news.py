#!/usr/bin/env python3
"""抓取财联社快讯 + A股行情摘要，缓存到 Redis。"""
import json, sys, traceback
from datetime import datetime, timezone, timedelta

import redis
import akshare as ak

CST = timezone(timedelta(hours=8))
r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)

def fetch_cls_news():
    """抓财联社全球快讯，返回新增条目列表"""
    try:
        df = ak.stock_info_global_cls()
    except Exception:
        traceback.print_exc()
        return []

    now = datetime.now(CST)
    key = "news:cls:latest_ids"  # Redis set of recent ids
    new_items = []

    for _, row in df.head(50).iterrows():
        title = str(row.get("标题", "") or row.get("title", "")).strip()
        content = str(row.get("内容", "") or row.get("content", "")).strip()
        ts = str(row.get("时间", "") or row.get("time", "")).strip()
        if not title and not content:
            continue
        item_id = f"{ts}:{title[:30]}"
        if r.sismember(key, item_id):
            continue
        item = {"title": title, "content": content, "time": ts, "fetched_at": now.isoformat()}
        # cache each item
        r.hset("news:cls:items", item_id, json.dumps(item, ensure_ascii=False))
        r.sadd(key, item_id)
        new_items.append(item)

    # keep set from growing forever - trim to last 500
    if r.scard(key) > 500:
        # remove oldest by just trimming the set
        members = r.smembers(key)
        oldest = sorted(members)[:len(members)-500]
        if oldest:
            r.srem(key, *oldest)

    return new_items

def fetch_market_summary():
    """抓A股主要指数行情"""
    indices = {
        "上证指数": "sh000001",
        "深证成指": "sz399001",
        "创业板指": "sz399006",
    }
    summary = {}
    try:
        df = ak.stock_zh_index_spot_em(symbol="上证系列指数")
        # just grab a few rows for display
        for _, row in df.head(5).iterrows():
            name = str(row.get("名称", ""))
            price = row.get("最新价", "")
            change_pct = row.get("涨跌幅", "")
            summary[name] = {"price": price, "change_pct": change_pct}
    except Exception:
        traceback.print_exc()

    if summary:
        r.set("market:summary:latest", json.dumps(summary, ensure_ascii=False))
    return summary

def main():
    print(f"[{datetime.now(CST).strftime('%H:%M:%S')}] 开始采集...")
    new_items = fetch_cls_news()
    summary = fetch_market_summary()
    print(f"财联社新增: {len(new_items)} 条")
    print(f"行情指数: {len(summary)} 个")

    # report important ones
    important = []
    keywords = ["央行", "降息", "降准", "IPO", "暂停", "关税", "制裁", "利好", "利空",
                "突发", "重磅", "紧急", "政策", "会议", "主席", "暴跌", "暴涨", "涨停",
                "跌停", "退市", "上市", "回购", "增持", "减持", "业绩", "财报", "违约"]
    for item in new_items:
        text = (item["title"] + item["content"]).upper()
        if any(k in text for k in keywords):
            important.append(item)

    if important:
        print(f"\n=== 重要信息 ({len(important)} 条) ===")
        for it in important[:10]:
            print(f"  [{it['time']}] {it['title']}")
            if it['content']:
                print(f"    {it['content'][:100]}")
    
    # output json for caller
    result = {"new_count": len(new_items), "important_count": len(important), "important": important[:10]}
    json_path = "/tmp/cache_news_result.json"
    with open(json_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {json_path}")

if __name__ == "__main__":
    main()
