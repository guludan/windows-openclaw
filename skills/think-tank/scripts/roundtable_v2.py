#!/usr/bin/env python3
"""
🏛️ 智库圆桌会议 v2 — 完整自动化管线
替代手动操作流程：议题搜索 → 学者匹配 → 独立发言 → 心理审计 → 最终报告

Usage:
    python3 roundtable_v2.py "金融海啸前夜的投资方向"
    python3 roundtable_v2.py "议题" --scholars 6  # 指定学者人数
    python3 roundtable_v2.py "议题" --no-news     # 不搜索时事
    python3 roundtable_v2.py "议题" --dry-run     # 只看匹配结果
"""

import json, sys, os, time, re, hashlib
from pathlib import Path
from datetime import date
from collections import defaultdict

# ============ 配置 ============
WORKSPACE = os.path.expanduser("~/.openclaw/workspace")
PERSONALITIES_DIR = "/mnt/d/data/think-tank/personalities"
REPORT_DIR = "/mnt/d/data/a-stock/reports"
EXA_API_KEY = "7be7a599-fa7b-4107-9ef1-ad96646156c2"
SERPAPI_KEY = "848c4ec87cac7776b9404f0af1aeb36fce936d1aa3aebe899664eaa43e67ccc2"

# 学科→人格目录映射
DOMAIN_MAP = {
    "宏观周期": ("scholars", "geopolitical-finance"),
    "金融不稳定": ("scholars", "financial-engineering"),
    "反脆弱/黑天鹅": ("scholars", "financial-engineering"),
    "崩盘预言": ("scholars", "geopolitical-finance"),
    "金融危机史": ("scholars", "economics"),
    "地缘人口": ("scholars", "geopolitical-finance"),
    "信息不对称": ("scholars", "economics"),
    "博弈论/临界点": ("scholars", "game-theory"),
    "政治预测": ("scholars", "political-forecasting"),
    "技术地缘": ("scholars", "tech-geopolitics"),
}

# 议题→自动匹配学者关键词
TOPIC_EXPERT_MAP = {
    "金融海啸|衰退|危机|债务|崩盘|萧条|恐慌": [
        "Ray_Dalio", "Hyman_Minsky", "Nassim_Taleb", "Nouriel_Roubini",
        "Carmen_Reinhart", "Peter_Zeihan"
    ],
    "脱钩|关税|贸易战|制裁|封锁": [
        "Peter_Zeihan", "Nouriel_Roubini", "Joseph_Stiglitz", "Thomas_Schelling"
    ],
    "房地产|房价|楼市|地产|土地": [
        "Ray_Dalio", "Carmen_Reinhart", "Peter_Zeihan", "Thomas_Schelling"
    ],
    "深圳|广州|长三角|大湾区|城市": [
        "Peter_Zeihan", "Joseph_Stiglitz", "Thomas_Schelling"
    ],
    "人口|老龄化|少子化|劳动力": [
        "Peter_Zeihan", "Carmen_Reinhart"
    ],
    "股市|A股|牛市|熊市|股票|投资": [
        "Ray_Dalio", "Nassim_Taleb", "Hyman_Minsky"
    ],
    "通胀|通缩|物价|货币|利率": [
        "Carmen_Reinhart", "Nouriel_Roubini", "Joseph_Stiglitz"
    ],
    "战争|冲突|地缘|台湾|南海|中东": [
        "Thomas_Schelling", "Peter_Zeihan", "Nouriel_Roubini"
    ],
    "AI|人工智能|科技|芯片|半导体": [
        "Peter_Zeihan", "Joseph_Stiglitz"
    ],
    "稀土|矿产|资源|能源": [
        "Peter_Zeihan", "Nouriel_Roubini"
    ],
}

# 心理学家名单
PSYCHOLOGISTS = [
    "丹尼尔·卡尼曼",   # 认知偏差审计
    "古斯塔夫·勒庞",   # 群体思维监控
]

# 所有已知学者（排重，人名→领域）
KNOWN_SCHOLARS = {
    # geopolitical-finance
    "Ray_Dalio": "宏观周期", "Nouriel_Roubini": "崩盘预言", "Peter_Zeihan": "地缘人口",
    "George_Soros": "反身性金融", "Niall_Ferguson": "金融地缘",
    # financial-engineering
    "Hyman_Minsky": "金融不稳定", "Nassim_Taleb": "反脆弱/黑天鹅",
    "David_Einhorn": "做空/价值", "James_Simons": "量化投资",
    "Cliff_Asness": "因子投资", "Edward_Thorp": "对冲/量化",
    # economics
    "Carmen_Reinhart": "金融危机史", "Joseph_Stiglitz": "信息不对称",
    "Kenneth_Rogoff": "宏观/货币", "Thomas_Piketty": "不平等/财富",
    "John_Maynard_Keynes": "宏观经济学", "Friedrich_Hayek": "奥地利学派",
    "Milton_Friedman": "货币主义", "Paul_Krugman": "国际经济学",
    # game-theory
    "Thomas_Schelling": "博弈论/临界点",
    "John_Nash": "博弈论/均衡", "Robert_Axelrod": "合作演化",
    # political-forecasting
    "Philip_Tetlock": "政治预测", "Nate_Silver": "数据预测",
    "Bruce_Bueno_de_Mesquita": "理性选择/政治预测",
    # tech-geopolitics
    "Ian_Bremmer": "地缘政治风险",
    "Henry_Kissinger": "大国博弈/外交",
    "John_Mearsheimer": "进攻性现实主义",
}


def search_news(question, max_results=8):
    """用Exa搜索时事数据"""
    import requests
    
    # 从议题提取搜索关键词
    keywords = question[:60].replace(" ", "")
    search_queries = [
        f"{keywords} 2026 分析",
        f"{keywords} 危机 预测",
    ]
    
    all_results = []
    for q in search_queries:
        try:
            r = requests.post(
                "https://api.exa.ai/search",
                headers={
                    "x-api-key": EXA_API_KEY,
                    "Content-Type": "application/json"
                },
                json={
                    "query": q,
                    "numResults": max_results // len(search_queries) + 1,
                    "type": "news",
                    "include_domains": [],
                    "text": True,
                    "start_published_date": "2025-12-01T00:00:00.000Z"
                },
                timeout=15
            )
            if r.status_code == 200:
                data = r.json()
                for item in data.get("results", []):
                    title = item.get("title", "")
                    text = item.get("text", "")[:300]
                    url = item.get("url", "")
                    all_results.append({"title": title, "text": text, "url": url})
                    print(f"  📰 {title[:60]}")
        except Exception as e:
            print(f"  ⚠️ Exa搜索失败: {e}")
    
    return all_results


def load_personality(name, domain):
    """加载人格文件"""
    if domain in DOMAIN_MAP:
        cat, sub = DOMAIN_MAP[domain]
    else:
        # 尝试自动定位
        for cat_name in ["scholars", "psychologists"]:
            for sub_dir in os.listdir(f"{PERSONALITIES_DIR}/{cat_name}"):
                p_path = f"{PERSONALITIES_DIR}/{cat_name}/{sub_dir}/{name}/personality_v3.json"
                if os.path.exists(p_path):
                    cat, sub = cat_name, sub_dir
                    break
    
    p_path = f"{PERSONALITIES_DIR}/{cat}/{sub}/{name}/personality_v3.json"
    if not os.path.exists(p_path):
        print(f"  ⚠️ {name} 人格文件不存在: {p_path}")
        return None
    
    with open(p_path) as f:
        return json.load(f)


def generate_prompt(name, personality, question, news_data):
    """生成学者独立发言prompt"""
    # 提取人格核心
    insights = personality.get("insights", [])
    top_insights = [i.get("excerpt", "")[:200] for i in insights[:10]]
    stance = personality.get("stance", "")
    methods = [m.get("excerpt", "")[:200] for m in insights if m.get("type") in ("method", "cognitive")][:5]
    
    news_text = ""
    if news_data:
        news_text = "\n## 时事数据\n" + "\n".join([
            f"- **{n['title']}**: {n['text'][:150]}"
            for n in news_data[:6]
        ])
    
    prompt = f"""你是 {name}（{personality.get('domain', '学者')}）。
{personality.get('core_brief', '')}

## 你的核心insights
{chr(10).join([f"- {ins}" for ins in top_insights])}

## 你的思维方式
{chr(10).join([f"- {m}" for m in methods])}

## 议题
{question}

{news_text}

请基于你的人格和专业知识，独立回答这个议题。不需要考虑其他学者的观点。
保持你真实的思维风格和判断框架。使用条件树（若X则Y），不用概率数字。

输出格式：
### Q1: [你的核心判断]
[论证]

### Q2: [第二个维度的判断]
[论证]

### Q3: [如果议题有此维度]
[论证]"""
    return prompt


def generate_psych_prompt(name, personality, summary_file):
    """生成心理学家prompt"""
    insights = personality.get("insights", [])
    top_insights = [i.get("excerpt", "")[:200] for i in insights[:8]]
    
    prompt = f"""你是 {name}（{personality.get('domain', '心理学家')}）。
{personality.get('core_brief', '')}

## 你的核心insights
{chr(10).join([f"- {ins}" for ins in top_insights])}

## 你的任务
请阅读 /tmp/roundtable_summary_for_psych.md 中的学者发言汇总。
你的角色是独立监控心理学偏差，不分析议题内容本身。

{"### 请识别以下群体心理特征：" if "勒庞" in name else "### 请识别以下认知偏差："}"""
    return prompt


def match_scholars(question, count=8):
    """自动匹配学者"""
    matched = set()
    
    # 按关键词匹配
    for keyword, scholars in TOPIC_EXPERT_MAP.items():
        if re.search(keyword, question):
            for s in scholars:
                matched.add(s)
    
    # 如果不够，补充默认配置（金融+地产+宏观+地缘+博弈）
    defaults = ["Ray_Dalio", "Hyman_Minsky", "Nouriel_Roubini", 
                "Carmen_Reinhart", "Peter_Zeihan", "Joseph_Stiglitz",
                "Thomas_Schelling", "Nassim_Taleb"]
    
    for s in defaults:
        if len(matched) >= count:
            break
        matched.add(s)
    
    # 截取到指定数量
    matched_list = list(matched)[:count]
    
    # 验证人格文件存在
    verified = []
    for name in matched_list:
        domain = KNOWN_SCHOLARS.get(name, "未知")
        cat, sub = "scholars", "geopolitical-finance"
        for kw, (c, s) in DOMAIN_MAP.items():
            if domain == kw:
                cat, sub = c, s
                break
        
        p_path = f"{PERSONALITIES_DIR}/{cat}/{sub}/{name}/personality_v3.json"
        if not os.path.exists(f"{PERSONALITIES_DIR}/{cat}/{sub}/{name}/personality_v3.json"):
            # 尝试在其他子目录找到
            found = False
            for sub_dir in os.listdir(f"{PERSONALITIES_DIR}/{cat}"):
                test_path = f"{PERSONALITIES_DIR}/{cat}/{sub_dir}/{name}/personality_v3.json"
                if os.path.exists(test_path):
                    p_path = test_path
                    found = True
                    break
            if not found:
                print(f"  ⚠️ 跳过{name}（无人格文件）")
                continue
        
        personality = load_personality(name, domain)
        if personality:
            verified.append((name, domain, personality))
            print(f"  ✅ {name} ({domain})")
        else:
            print(f"  ⚠️ 跳过{name}（人格加载失败）")
    
    return verified


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="智库圆桌会议 v2")
    parser.add_argument("topic", help="议题描述")
    parser.add_argument("--scholars", type=int, default=6, help="学者人数（默认6）")
    parser.add_argument("--no-news", action="store_true", help="跳过时事搜索")
    parser.add_argument("--dry-run", action="store_true", help="仅显示匹配结果")
    args = parser.parse_args()
    
    today = date.today().strftime("%Y-%m-%d")
    
    print(f"\n{'='*60}")
    print(f"🏛️ 智库圆桌会议 v2")
    print(f"议题: {args.topic}")
    print(f"日期: {today}")
    print(f"{'='*60}")
    
    # Step 1: 搜索时事
    news_data = []
    if not args.no_news:
        print("\n📡 搜索时事数据...")
        news_data = search_news(args.topic)
        print(f"  获 {len(news_data)} 条结果")
    
    # Step 2: 匹配学者
    print("\n🎯 匹配学者...")
    scholars = match_scholars(args.topic, args.scholars)
    
    if not scholars:
        print("❌ 未匹配到任何学者，请检查人格文件路径")
        return
    
    print(f"\n📋 学者名单 ({len(scholars)}人):")
    for name, domain, _ in scholars:
        print(f"  - {name} ({domain})")
    
    if args.dry_run:
        print(f"\n✅ Dry-run完成，共{len(scholars)}位学者")
        return
    
    # Step 3: 写入议题数据
    topic_data = {
        "topic": args.topic,
        "date": today,
        "news": [{"title": n["title"], "text": n["text"][:300], "url": n["url"]} 
                 for n in news_data[:10]]
    }
    with open("/tmp/roundtable_data.json", "w") as f:
        json.dump(topic_data, f, ensure_ascii=False, indent=2)
    print(f"\n📝 议题数据已写入 /tmp/roundtable_data.json")
    
    # Step 4: 生成每位学者的prompt + spawn指令
    print(f"\n{'='*60}")
    print("🚀 生成调度指令")
    print(f"{'='*60}")
    
    for name, domain, personality in scholars:
        prompt = generate_prompt(name, personality, args.topic, news_data)
        p_path = f"/tmp/roundtable_prompt_{name}.md"
        with open(p_path, "w") as f:
            f.write(prompt)
        
        print(f"""
# {name}
cat /tmp/roundtable_prompt_{name}.md  # 内容已写入

sessions_spawn(
    label="智库-圆桌-{name}",
    model="deepseek/deepseek-chat",
    runTimeoutSeconds=300,
    task="[Subagent Task] Read the file at /tmp/roundtable_prompt_{name}.md. Then answer the question based on your personality and analysis framework. Output as a structured analysis using condition trees."
)
""")
    
    # Step 5: 心理学家审计准备
    print(f"\n{'='*60}")
    print("🧠 心理学家审计（等所有学者发言到齐后执行）")
    print(f"{'='*60}")
    print("""
1. 收集所有学者发言后，汇总到 /tmp/roundtable_summary_for_psych.md
2. 依次spawn两位心理学家：
   - sessions_spawn(label="智库-心理-卡尼曼", ...)
     task: "Read /tmp/roundtable_summary_for_psych.md. Identify cognitive biases."
   - sessions_spawn(label="智库-心理-勒庞", ...)
     task: "Read /tmp/roundtable_summary_for_psych.md. Identify groupthink patterns."
3. 心理学家返回后，汇总成最终报告

最终报告输出路径:
/mnt/d/data/a-stock/reports/{today}/圆桌_{args.topic[:12]}_{today.replace('-','')}.md
""")
    
    print(f"\n{'='*60}")
    print("✅ 准备就绪！请按以上调度指令执行学者spawn")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
