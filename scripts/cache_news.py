#!/usr/bin/env python3
"""抓取财联社快讯 + A股行情，缓存到Redis。返回新增重要信息摘要。"""
import json, sys, traceback
from datetime import datetime, timedelta
import akshare as ak
import redis

r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)

def cache_cls_news():
    """抓取财联社快讯，缓存到Redis，返回新增条目"""
    key_prefix = "news:cls"
    df = ak.stock_info_global_cls()
    new_items = []
    for _, row in df.iterrows():
        rid = f"{key_prefix}:{row.get('时间', row.get('datetime', ''))}"
        if r.exists(rid):
            continue
        item = {
            "title": row.get("标题", row.get("title", "")),
            "content": row.get("内容", row.get("content", "")),
            "time": str(row.get("时间", row.get("datetime", ""))),
        }
        r.setex(rid, 86400, json.dumps(item, ensure_ascii=False))
        new_items.append(item)
    # 存最新列表key
    r.setex(f"{key_prefix}:latest", 3600, json.dumps(
        [{"title": row.get("标题", row.get("title", "")), "content": row.get("内容", row.get("content", "")), "time": str(row.get("时间", row.get("datetime", "")))} for _, row in df.head(20).iterrows()],
        ensure_ascii=False
    ))
    return new_items

def cache_stock_quotes():
    """抓取A股主要指数，缓存到Redis"""
    key = "stock:index_quotes"
    try:
        df = ak.stock_zh_index_spot_em()
        indices = {"上证指数": "000001", "深证成指": "399001", "创业板指": "399006"}
        results = []
        for _, row in df.iterrows():
            code = row.get("代码", "")
            if code in indices.values():
                results.append({
                    "name": row.get("名称", ""),
                    "code": code,
                    "price": str(row.get("最新价", "")),
                    "change_pct": str(row.get("涨跌幅", "")),
                })
        r.setex(key, 300, json.dumps(results, ensure_ascii=False))
        return results
    except Exception as e:
        return [{"error": str(e)}]

def is_important(item):
    """简单判断是否重要"""
    keywords = ["央行", "降息", "降准", "政策", "关税", "制裁", "战争", "地震", "习近平", "国务院", 
                "美联储", "加息", "暴涨", "暴跌", "涨停", "跌停", "IPO", "重组", "并购", "违约",
                "降税", "刺激", "经济", "GDP", "通胀", "通缩", "PMI", "贸易"]
    text = (item.get("title", "") + item.get("content", "")).lower()
    return any(k in text for k in keywords)

def main():
    print(f"[{datetime.now().isoformat()}] 开始采集...")
    
    # 财联社快讯
    try:
        new_items = cache_cls_news()
        print(f"财联社: 新增 {len(new_items)} 条")
    except Exception as e:
        print(f"财联社采集失败: {e}")
        traceback.print_exc()
        new_items = []
    
    # A股行情
    try:
        quotes = cache_stock_quotes()
        print(f"行情: {len(quotes)} 个指数")
        for q in quotes:
            if "error" not in q:
                print(f"  {q['name']}: {q['price']} ({q['change_pct']}%)")
    except Exception as e:
        print(f"行情采集失败: {e}")
        quotes = []
    
    # 判断是否需要汇报
    important = [item for item in new_items if is_important(item)]
    if len(important) > 5:
        print(f"\n⚠️ 重要信息 {len(important)} 条，需要汇报:")
        for item in important[:15]:
            print(f"  [{item['time']}] {item['title'][:60]}")
    else:
        print(f"\n重要信息 {len(important)} 条，无需打扰。")
    
    print("完成。")

if __name__ == "__main__":
    main()
