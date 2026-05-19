#!/usr/bin/env python3
"""检查Redis缓存中的新闻和行情数据"""
import redis
import json

try:
    r = redis.Redis(host='127.0.0.1', port=6379, decode_responses=True)
    
    # 检查新闻数据
    news_keys = r.keys('news:cls:*')
    print(f'Redis连接正常，找到 {len(news_keys)} 个新闻条目')
    
    if news_keys:
        latest_key = 'news:cls:latest'
        if r.exists(latest_key):
            latest = json.loads(r.get(latest_key))
            print(f'最新新闻数: {len(latest)}')
            for i, item in enumerate(latest[:3]):
                print(f'{i+1}. [{item.get("time", "N/A")}] {item.get("title", "N/A")[:60]}...')
        else:
            print('最新新闻key不存在')
    else:
        print('没有找到新闻条目')
        
    # 检查行情数据
    stock_key = 'stock:index_quotes'
    if r.exists(stock_key):
        quotes = json.loads(r.get(stock_key))
        print(f'\n行情数据: {len(quotes)} 个指数')
        for q in quotes:
            if 'error' not in q:
                print(f'  {q.get("name", "N/A")}: {q.get("price", "N/A")} ({q.get("change_pct", "N/A")}%)')
            else:
                print(f'  错误: {q["error"]}')
    else:
        print('没有找到行情数据')
        
except Exception as e:
    print(f'连接Redis失败: {e}')