#!/usr/bin/env python3
"""
智库分析Pipeline v3
问题拆解(智能) → 历史参照(Redis) → 深度RAG(insights+materials) → 子session报告生成 → D盘保存

v2→v3改进:
- 智能子议题生成（基于问题实体+维度自动构建）
- RAG搜索加入materials集合（5900+条原始素材）
- 更丰富的维度关键词（台海/半导体/科技/地区安全）
- 历史事件补充台海相关
- 子议题去重+质量过滤

Usage:
    python think_tank_pipeline_v3.py "中国收复台湾的战略窗口期是什么时候？必要条件是什么？"
"""

"""
智库分析Pipeline v3.2
问题拆解(智能) → 时事搜索(四源: 财联社+Exa+Tavily+SerpAPI) → 历史参照(Redis) → 深度RAG(insights+materials) → 子session报告生成 → D盘保存

v3.2: 财联社Redis缓存整合 + 多源时事搜索
v3.1: Exa+Tavily+SerpAPI多源时事搜索 + 自动入库
v3: 智能子议题生成 + RAG materials集合 + 历史事件补充

Usage:
    ~/miniconda3/bin/python3 skills/think-tank/scripts/think_tank_pipeline_v3.py "议题"
    ~/miniconda3/bin/python3 skills/think-tank/scripts/think_tank_pipeline_v3.py "议题" --no-affairs  # 禁用时事搜索
"""

import json, sys, os, subprocess, time, re
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.historical_context import search_historical_events, get_redis_status
import chromadb, requests

DB_PATH = "/mnt/d/data/think-tank-data/_chromadb/lacan_db"
OLLAMA_URL = 'http://172.26.0.1:11434/api/embed'
OLLAMA_MODEL = 'bge-m3:latest'
REPORT_DIR = "/mnt/d/data/think-tank-data/reports"
EXA_API_KEY = '7be7a599-fa7b-4107-9ef1-ad96646156c2'
TAVILY_API_KEY = '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'
SERPAPI_KEY = '848c4ec87cac7776b9404f0af1aeb36fce936d1aa3aebe899664eaa43e67ccc2'
PYTHON = os.path.expanduser('~/miniconda3/bin/python3')


def embed(query):
    for attempt in range(3):
        try:
            r = requests.post(OLLAMA_URL, json={'model': OLLAMA_MODEL, 'input': [query]}, timeout=60)
            return r.json()['embeddings'][0]
        except:
            continue
    return None


# ============ 智能问题拆解 ============

DIMENSION_RULES = {
    "地缘冲突": {
        "keywords": ["战争", "军事", "冲突", "制裁", "封锁", "入侵", "打击", "攻击", "袭击",
                      "收复", "统一", "台海", "海峡", "南海", "东海", "边境", "领土", "主权",
                      "武力", "核武", "威慑", "军演", "航母", "导弹", "登陆"],
        "sub_dimensions": ["冲突模式", "地理范围", "持续时间", "升级路径", "核风险"],
    },
    "能源": {
        "keywords": ["石油", "原油", "天然气", "LNG", "油", "气", "能源", "霍尔木兹"],
        "sub_dimensions": ["供应中断", "替代供应", "储备释放", "运输通道", "产能弹性"],
    },
    "金融": {
        "keywords": ["价格", "汇率", "利率", "美元", "通胀", "金价", "股", "债", "资本",
                      "避险", "货币", "金融", "降息", "加息", "汇率"],
        "sub_dimensions": ["避险资产", "货币流向", "利率环境", "通胀预期", "风险溢价"],
    },
    "政治": {
        "keywords": ["制裁", "禁运", "结盟", "联合国", "北约", "谈判", "外交", "盟友",
                      "同盟", "大选", "选举", "政权", "独立", "主权", "红蓝", "两党"],
        "sub_dimensions": ["国际联盟", "制裁力度", "外交空间", "国内政治"],
    },
    "贸易": {
        "keywords": ["贸易", "关税", "出口", "进口", "供应链", "芯片", "半导体", "制裁",
                      "封锁", "科技", "技术", "台积电"],
        "sub_dimensions": ["贸易路线", "替代供应商", "物流成本", "产业链转移"],
    },
    "粮食": {
        "keywords": ["粮食", "小麦", "玉米", "大豆"],
        "sub_dimensions": ["主产区", "库存水平", "出口限制", "替代来源"],
    },
}

ASSET_MAP = {
    "石油": ["brent_crude", "WTI"], "原油": ["brent_crude", "WTI"],
    "天然气": ["henry_hub", "TTF", "JKM"], "黄金": ["gold_xau"],
    "油价": ["brent_crude", "WTI"], "美元": ["usd_index", "eur_usd"],
    "股市": ["s_p_500", "stoxx"], "小麦": ["wheat"], "铜": ["copper"],
    "芯片": ["sox_index"], "半导体": ["sox_index"], "人民币": ["usd_cny"],
    "台股": ["taiex"], "日股": ["nikkei"],
}

# 问题实体识别 → 自动构建子议题
ENTITY_PATTERNS = {
    "台湾": {
        "queries": {
            "台湾_军事窗口": "台海战争 战略窗口期 军事优势 登陆作战 反介入/区域拒止 A2AD",
            "台湾_必要条件": "统一台湾 必要条件 经济实力 军事准备 国际环境 内部稳定",
            "台湾_美国干预": "台海冲突 美国干预 太平洋威慑 台湾关系法 武力介入 第七舰队",
            "台湾_半导体": "台湾半导体 台积电 全球供应链 芯片制裁 技术封锁",
            "台湾_日本": "美日同盟 冲绳基地 日本介入 西太平洋安全 和平宪法",
            "台湾_心理_大陆": "统一决策 民族复兴 大国崛起 认知偏差 损失厌恶",
            "台湾_心理_台湾": "台湾认同 身份政治 恐惧诉求 群体极化 道德脱离",
            "台湾_心理_美国": "华盛顿决策 集体思维 修昔底德陷阱 风险感知",
            "台湾_历史类比": "金门炮战 台海危机 历史类比 冷战博弈 福克兰战争",
        },
        "assets": ["taiex", "sox_index", "usd_cny", "nikkei", "sh000001", "sh000300", "sz399001", "sz399006"],
    },
    "伊朗": {
        "queries": {
            "伊朗_军事": "美国 伊朗 军事行动 空袭 石油设施 代理人",
            "伊朗_能源": "霍尔木兹海峡 石油运输 天然气 供应中断",
            "伊朗_地缘": "中东 什叶派 以色列 代理人战争 核协议",
            "伊朗_经济影响": "油价冲击 通胀 避险资产 黄金",
        },
        "assets": ["brent_crude", "gold_xau"],
    },
    "朝鲜": {
        "queries": {
            "朝鲜_军事": "朝鲜半岛 核试验 导弹 驻韩美军",
            "朝鲜_地缘": "中朝关系 韩朝统一 半岛局势",
            "朝鲜_经济": "韩国经济 首尔 半岛风险 避险",
        },
        "assets": ["kospi", "usd_krw"],
    },
    "乌克兰": {
        "queries": {
            "乌克兰_军事": "俄乌冲突 战场态势 北约援助 军事装备",
            "乌克兰_能源": "欧洲天然气 俄罗斯制裁 能源替代",
            "乌克兰_经济": "欧洲经济 粮食出口 难民 制裁效果",
        },
        "assets": ["eur_usd", "brent_crude", "stoxx"],
    },
}


def decompose_question(question: str) -> dict:
    """智能问题拆解：识别维度、实体、资产、自动生成子议题"""
    question_lower = question.lower()

    # 1. 维度识别
    triggered = []
    for dim_name, rules in DIMENSION_RULES.items():
        for kw in rules["keywords"]:
            if kw.lower() in question_lower:
                triggered.append(dim_name)
                break
    if not triggered:
        triggered = ["地缘冲突", "金融"]

    # 2. 实体识别 + 子议题生成
    auto_queries = {}
    matched_entities = []
    for entity, config in ENTITY_PATTERNS.items():
        if entity in question or entity in question_lower:
            matched_entities.append(entity)
            auto_queries.update(config["queries"])

    # 如果没有匹配到预定义实体，按维度自动生成通用子议题
    if not auto_queries:
        for dim in triggered:
            auto_queries[f"{dim}_战略分析"] = f"{question} {dim} 战略分析 国际关系 安全形势"
            auto_queries[f"{dim}_心理分析"] = f"{question} 决策偏差 群体心理 风险感知 道德脱离"

    # 始终加入心理维度（核心差异点）
    auto_queries.setdefault("心理_群体决策", f"{question} 群体决策 从众效应 集体思维 战争决策")
    auto_queries.setdefault("心理_认知偏差", f"{question} 认知偏差 风险感知 预期 支持信心")
    auto_queries.setdefault("心理_权力道德", f"{question} 权力 攻击性 道德脱离 权威服从")

    # 3. 资产识别
    relevant_assets = set()
    for ak, assets in ASSET_MAP.items():
        if ak in question:
            relevant_assets.update(assets)
    for entity in matched_entities:
        relevant_assets.update(ENTITY_PATTERNS.get(entity, {}).get("assets", []))
    if "能源" in triggered:
        relevant_assets.update(["brent_crude", "WTI"])
    if "地缘冲突" in triggered:
        relevant_assets.update(["gold_xau", "usd_index"])

    return {
        "original_question": question,
        "triggered_dimensions": triggered,
        "matched_entities": matched_entities,
        "sub_dimensions": {d: DIMENSION_RULES[d]["sub_dimensions"] for d in triggered if d in DIMENSION_RULES},
        "relevant_assets": list(relevant_assets),
        "auto_queries": auto_queries,
    }


# ============ 深度RAG（含materials） ============

def deep_rag_search(question: str, sub_queries: dict = None, n_per_col: int = 6) -> dict:
    """
    深度RAG搜索：
    - insights: 深层逻辑（人格核心观点）
    - core_profile: 人格摘要
    - materials: 原始素材（背景知识、历史分析）
    """
    client = chromadb.PersistentClient(path=DB_PATH)
    results = {}

    queries = sub_queries if sub_queries else {"default": question}
    COLLECTIONS = ["insights", "core_profile", "materials"]

    for topic, query in queries.items():
        emb = embed(query)
        if emb is None:
            continue
        topic_results = []
        seen = set()

        for col_name in COLLECTIONS:
            try:
                col = client.get_collection(col_name)
                if col.count() == 0:
                    continue
                # materials取少一些（内容长），insights/core_profile多取
                n = min(3 if col_name == "materials" else n_per_col, col.count())
                res = col.query(query_embeddings=[emb], n_results=n)
                for i, doc in enumerate(res["documents"][0]):
                    meta = res["metadatas"][0][i]
                    person = meta.get("person", meta.get("personality_name", "?"))
                    key = f"{person}|{col_name}"
                    if key in seen:
                        continue
                    seen.add(key)
                    topic_results.append({
                        "person": person,
                        "col": col_name,
                        "text": doc[:400 if col_name == "materials" else 300],
                        "dist": res["distances"][0][i],
                    })
            except Exception as e:
                print(f"  [{topic}] {col_name} error: {e}")

        results[topic] = sorted(topic_results, key=lambda x: x["dist"])

    return results


# ============ 时事信息检索（Redis缓存） ============

def search_news_cache(sub_queries: dict) -> dict:
    """根据子议题从Redis缓存检索相关时事新闻"""
    import re
    redis_mod = __import__("redis")
    r = redis_mod.Redis(host="127.0.0.1", port=6379, decode_responses=True)
    
    # 从子议题中提取关键词
    all_keywords = set()
    for topic, query in sub_queries.items():
        # 提取中英文关键词
        words = re.findall(r'[\u4e00-\u9fff]{2,}|[A-Za-z]+', query)
        all_keywords.update(words)
    
    news_results = {}
    searched_articles = {}  # md5key -> item (去重)
    
    for topic, query in sub_queries.items():
        topic_articles = []
        words = re.findall(r'[\u4e00-\u9fff]{2,}|[A-Za-z]+', query)
        
        for kw in words[:5]:  # 每个子议题最多查5个关键词
            tag_key = f"cls:tag:{kw}"
            members = r.smembers(tag_key)
            for md5key in members:
                if md5key not in searched_articles:
                    item = r.hget("cls:all", md5key)
                    if item:
                        searched_articles[md5key] = __import__("json").loads(item)
                if md5key in searched_articles:
                    topic_articles.append(searched_articles[md5key])
        
        # 去重
        seen_titles = set()
        unique = []
        for a in topic_articles:
            if a["title"][:40] not in seen_titles:
                seen_titles.add(a["title"][:40])
                unique.append(a)
        
        if unique:
            news_results[topic] = unique[:5]  # 每个子议题最多5条新闻
    
    total = sum(len(v) for v in news_results.values())
    if total > 0:
        print(f"  📰 时事缓存命中: {total}条 (覆盖{len(news_results)}个子议题)")
    else:
        print(f"  📰 时事缓存: 0条命中")
    
    return news_results


def format_news_for_prompt(news_data: dict) -> str:
    """格式化时事新闻为prompt文本"""
    if not news_data:
        return ""
    sections = []
    for topic, articles in news_data.items():
        lines = [f"**{topic} - 时事快讯:**"]
        for a in articles[:3]:
            lines.append(f"- 📰 [{a.get('pub_time','')}] {a['title']}")
            if a.get('content'):
                lines.append(f"  {a['content'][:150].replace(chr(10), ' ')}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def match_historical_events(question: str, dimensions: list) -> list:
    """历史事件匹配（增强版：自动识别问题实体）"""
    TAG_MAP = {
        "地缘冲突": ["中东战争", "军事冲突", "封锁", "制裁", "北约", "核威慑", "台海", "金门", "朝鲜", "海峡", "登岛", "两栖"],
        "能源": ["石油", "油价", "油价冲击", "天然气", "霍尔木兹海峡", "能源危机", "能源武器化"],
        "金融": ["美元", "黄金", "滞胀", "避险", "货币危机"],
        "政治": ["制裁", "禁运", "联盟战争", "联合国授权", "核协议"],
        "贸易": ["海上通道", "运河封锁", "供应链", "半导体"],
        "粮食": ["粮食危机", "小麦"],
    }
    tags = set()
    for dim in dimensions:
        tags.update(TAG_MAP.get(dim, []))
    return search_historical_events(question, list(tags))


def format_rag_for_prompt(rag_data: dict) -> str:
    """格式化RAG数据为prompt文本（区分insights和materials）"""
    sections = []
    for topic, items in rag_data.items():
        lines = [f"**{topic}:**"]
        seen_persons = set()

        # insights/core_profile 优先（每个person一条）
        for item in items:
            if item["col"] == "materials":
                continue
            person = item["person"]
            if person in seen_persons:
                continue
            seen_persons.add(person)
            tag = "💡" if item["col"] == "insights" else "👤"
            text = item["text"].replace("\n", " ")
            lines.append(f"- {tag} [RAG: {person}] {text}")

        # materials补充（最多2条，不同person）
        mat_count = 0
        for item in items:
            if item["col"] != "materials":
                continue
            person = item["person"]
            if person in seen_persons and mat_count >= 2:
                continue
            seen_persons.add(person)
            mat_count += 1
            text = item["text"].replace("\n", " ")
            lines.append(f"- 📄 [RAG: {person}/素材] {text}")
            if mat_count >= 3:
                break

        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def format_historical_for_prompt(events: list) -> str:
    """格式化历史事件为prompt文本"""
    if not events:
        return "（暂无匹配的历史事件）"
    lines = []
    for ev in events:
        impacts = ""
        for asset, imp in ev.get("market_impacts", {}).items():
            impacts += f"{asset}: pre={imp.get('pre_event','?')}→peak={imp.get('peak','?')}→post={imp.get('post_event_6m','?')} {imp.get('unit','')} | "
        lessons = "; ".join(ev.get("lessons", [])[:3])
        lines.append(f"- **{ev['name']}**({ev['date_range']}): {ev['description'][:100]}... | 市场: {impacts} | 教训: {lessons}")
    return "\n".join(lines)


def build_report_task(question: str, decomp: dict, events: list, rag_data: dict) -> str:
    """构建报告生成的task prompt"""
    rag_text = format_rag_for_prompt(rag_data)
    hist_text = format_historical_for_prompt(events)

    task = f"""你是一个学术战略研究分析引擎。请对以下议题进行深度多视角分析，以学术论文和政策研究报告的语调撰写。

## 核心问题
{question}

## 触发维度：{', '.join(decomp['triggered_dimensions'])}
## 匹配实体：{', '.join(decomp.get('matched_entities', ['通用']))}
## 相关资产：{', '.join(decomp['relevant_assets'])}

## 要求
1. 使用RAG数据中的具体观点，每个判断标注[RAG: 人格名]
2. 心理学家分析要深刻具体（认知偏差、群体决策、道德脱离、社会认同、损失厌恶等）
3. 历史类比严谨，注意区分不同历史情境的异同
4. 所有情景概率必须归一化（总和=100%）
5. 中文，6000-8000字
6. 结构：问题解构→历史参照矩阵→候选方案深度分析（含心理画像+集体决策偏差）→跨学科交叉验证→情景推演与投资影响→自优化建议
7. 💡标记的是深层逻辑观点，📄标记的是原始素材参考，都要充分利用
8. 每个关键判断必须呈现红蓝双方的**对立观点**——不能只有红方声音
9. A股/港股数据：如果涉及中国市场，必须分析上证指数、沪深300、创业板指的走势影响
10. 提供具体的军事能力数据（舰艇数量、兵力规模、装备代差）而非纯定性描述
11. 心理学家（卡尼曼/海特/西奥迪尼/阿希/班杜拉/艾克曼）必须有实质性引用

## RAG参考数据
{rag_text}

## 历史参照
{hist_text}"""

    return task


# ============ 红蓝对抗匹配 ============

RED_BLUE_TEAMS = {
    "红方军事鹰派": ["毛泽东", "粟裕", "彭德怀", "林彪", "徐向前", "叶剑英", "聂荣臻", "张国华", "朱可夫", "隆美尔_Rommel"],
    "红方战略家": ["周恩来", "邓小平", "陈云", "武元甲"],
    "红方学者": ["阎学通", "金灿荣", "沈逸", "艾跃进", "卢麒元", "温铁军", "时殷弘", "瞿东升", "潘忠岐", "牛新春", "殷罡", "田文林", "曾向红"],
    "蓝方美国": ["Graham_Allison", "John_Gaddis", "John_Mearsheimer", "William_Blum", "Vali_Nasr", "Fawaz_Gerges"],
    "蓝方经济": ["Ray_Dalio", "Nassim_Taleb", "Hyman_Minsky", "Carmen_Reinhart", "Charles_Kindleberger"],
    "蓝方历史": ["山本五十六_Yamamoto", "盖伦_Reinhard_Gehlen"],
    "心理学家": ["丹尼尔·卡尼曼", "乔纳森·海特", "欧文·亚隆", "罗伯特·西奥迪尼", "所罗门·阿希", "阿尔伯特·班杜拉", "保罗·艾克曼"],
}


def red_blue_match(question: str, rag_data: dict) -> dict:
    """从RAG结果中筛选红蓝对抗阵容，确保阵营平衡"""
    rag_persons = set()
    person_dists = {}
    for items in rag_data.values():
        for item in items:
            p = item["person"]
            rag_persons.add(p)
            person_dists[p] = min(person_dists.get(p, 999), item["dist"])

    selection = {}
    for team, members in RED_BLUE_TEAMS.items():
        available = sorted([m for m in members if m in rag_persons],
                           key=lambda p: person_dists.get(p, 999))
        selection[team] = available[:3]
        print(f"  {team}: {len(selection[team])}人 → {selection[team]}")
    return selection


def format_red_blue_prompt(selection: dict) -> str:
    """格式化红蓝对抗prompt指令"""
    role_map = {
        "红方军事鹰派": "进攻视角，主张行动",
        "红方战略家": "中国决策层视角，权衡利弊",
        "红方学者": "中国精英分析视角",
        "蓝方美国": "美国/西方视角，强调风险和威慑",
        "蓝方经济": "经济风险警示视角，强调代价",
        "蓝方历史": "历史教训视角，强调失败案例",
        "心理学家": "心理偏差分析，揭示认知盲区",
    }
    lines = ["## 红蓝对抗阵容（必须让双方充分辩论）"]
    for team, persons in selection.items():
        lines.append(f"- **{team}**（{role_map.get(team, '')}）: {', '.join(persons)}")
    lines.append("")
    lines.append("**要求：每个关键判断必须呈现红蓝双方对立观点。"
                 "心理学家（卡尼曼/海特/西奥迪尼/阿希/班杜拉/艾克曼）必须有实质性引用。**")
    return "\n".join(lines)


def build_chapter_tasks(question: str, decomp: dict, events: list, rag_data: dict, selection: dict, affairs_data: dict = None) -> dict:
    """构建分段报告任务（6章）"""
    rag_text = format_rag_for_prompt(rag_data)
    hist_text = format_historical_for_prompt(events)
    rb_text = format_red_blue_prompt(selection)
    dims = ', '.join(decomp['triggered_dimensions'])
    ents = ', '.join(decomp.get('matched_entities', ['通用']))
    assets = ', '.join(decomp['relevant_assets'])

    affairs_text = format_affairs_for_prompt(affairs_data) if affairs_data else ""
    affairs_block = f"\n\n{affairs_text}\n" if affairs_text else ""
    
    common = f"""\n## 核心问题\n{question}{affairs_block}\n\n{rb_text}\n"""

    chapters = {
        "ch01": {"title": "问题解构", "words": "800-1000",
            "instruction": f"你是学术战略研究分析引擎。撰写第1章：问题解构（800-1000字）。{common}\n## 维度：{dims} | 实体：{ents} | 资产：{assets}\n从五个维度（军事、政治、经济、技术、心理）解构问题，识别关键变量。**结合实时情报分析最新动态**。每个判断标注[RAG: 人格名]。直接输出正文。\n\n## RAG参考\n{rag_text}"},
        "ch02": {"title": "历史参照矩阵", "words": "800-1000",
            "instruction": f"你是学术战略研究分析引擎。撰写第2章：历史参照矩阵（800-1000字）。{common}提取可类比模式，注意区分相似性与差异性。蓝方历史视角强调失败案例。提炼3-5条核心教训。每个判断标注[RAG: 人格名]。直接输出正文。\n\n## 历史参照\n{hist_text}\n\n## RAG参考\n{rag_text}"},
        "ch03": {"title": "必要条件深度分析", "words": "1500-2000",
            "instruction": f"你是学术战略研究分析引擎。撰写第3章：必要条件深度分析（1500-2000字）。{common}从军事/政治/经济/技术四维度分析。**每个维度必须呈现红蓝双方对立观点**：红方强调能力增长和优势，蓝方强调差距和风险。军事要给具体数据。末尾四维联动。每个判断标注[RAG: 人格名]。直接输出正文。\n\n## RAG参考\n{rag_text}"},
        "ch04": {"title": "心理画像与集体决策偏差", "words": "1500-2000",
            "instruction": f"你是学术战略研究分析引擎。撰写第4章：心理画像与集体决策偏差（1500-2000字）。{common}\n**心理学家必须实质性引用（不能只用海特！）**：1.大陆决策层：损失厌恶[卡尼曼]、权威服从[阿希/班杜拉]、群体思维 2.台湾社会：社会认同[海特]、恐惧诉求[艾克曼]、群体极化 3.美国：信息茧房、修昔底德锁定 4.三方互动：道德脱离[班杜拉]、螺旋升级。每个心理机制引用具体理论/实验。每个判断标注[RAG: 人格名]。直接输出正文。\n\n## RAG参考\n{rag_text}"},
        "ch05": {"title": "跨学科交叉验证与窗口期判断", "words": "1500-2000",
            "instruction": f"你是学术战略研究分析引擎。撰写第5章：跨学科交叉验证与战略窗口期判断（1500-2000字）。{common}四维交叉验证：识别共识区间和核心分歧。三时段候选窗口（短期/中期/长期）+概率。窗口开启/关闭条件。**红蓝辩论**：军事派vs经济派vs心理学家充分碰撞。情景概率归一化（总和=100%）。每个判断标注[RAG: 人格名]。直接输出正文。\n\n## RAG参考\n{rag_text}\n\n## 历史参照\n{hist_text}"},
        "ch06": {"title": "情景推演与投资影响", "words": "1000-1500",
            "instruction": f"你是学术战略研究分析引擎。撰写第6章：情景推演与投资影响（1000-1500字）。{common}四个递进情景（基线/升级/高烈度/极端），概率归一化。**必须包含A股（上证/沪深300/创业板）和港股（恒生）**。参照历史数据。投资策略区分防御/进攻/对冲。用Taleb反脆弱框架审视。每个判断标注[RAG: 人格名]。直接输出正文。\n\n## 资产：{assets}\n## RAG参考\n{rag_text}\n\n## 历史参照\n{hist_text}"},
    }
    return chapters


# ============ 时事自动搜索（多源：Exa + Tavily + SerpAPI） ============

def _search_exa(queries: list, start_date: str) -> list:
    """Exa搜索，返回 [(title, text, url, pub, source)]"""
    articles = []
    for q in queries[:3]:
        try:
            payload = json.dumps({
                "query": q,
                "num_results": 8,
                "use_autoprompt": True,
                "startPublishedDate": start_date,
                "contents": {"text": {"maxCharacters": 3000}}
            })
            # Exa要求用curl，Python urllib会403
            cmd = [
                'curl', '-s', '-X', 'POST', 'https://api.exa.ai/search',
                '-H', f'x-api-key: {EXA_API_KEY}',
                '-H', 'Content-Type: application/json',
                '-d', payload, '--max-time', '30'
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
            if r.returncode != 0:
                print(f"  ⚠️ Exa curl失败: {r.returncode}")
                continue
            data = json.loads(r.stdout)
            for res in data.get("results", []):
                title = res.get("title", "")
                text = res.get("text", "")
                url = res.get("url", "")
                pub = res.get("publishedDate", "")
                if text and len(text) > 100:
                    articles.append((title, text, url, pub, "Exa"))
        except Exception as e:
            print(f"  ⚠️ Exa搜索异常: {e}")
        time.sleep(1)
    return articles


def _search_tavily(queries: list) -> list:
    """Tavily搜索，返回 [(title, text, url, pub, source)]"""
    articles = []
    for q in queries[:2]:
        try:
            payload = {
                "api_key": TAVILY_API_KEY,
                "query": q,
                "search_depth": "advanced",
                "max_results": 5,
                "include_answer": False,
            }
            r = requests.post("https://api.tavily.com/search", json=payload, timeout=30)
            if r.status_code != 200:
                print(f"  ⚠️ Tavily搜索失败: {r.status_code}")
                continue
            data = r.json()
            for res in data.get("results", []):
                title = res.get("title", "")
                text = res.get("content", "")
                url = res.get("url", "")
                pub = res.get("published_date", "") or ""
                if text and len(text) > 100:
                    articles.append((title, text, url, pub, "Tavily"))
        except Exception as e:
            print(f"  ⚠️ Tavily搜索异常: {e}")
        time.sleep(1)
    return articles


def _search_serpapi(queries: list) -> list:
    """SerpAPI搜索，返回 [(title, text, url, pub, source)]"""
    articles = []
    for q in queries[:2]:
        try:
            params = {
                "api_key": SERPAPI_KEY,
                "engine": "google",
                "q": q,
                "google_domain": "google.com",
                "num": 5,
                "tbs": "qdr:m",  # 最近一个月
            }
            r = requests.get("https://serpapi.com/search", params=params, timeout=30)
            if r.status_code != 200:
                print(f"  ⚠️ SerpAPI搜索失败: {r.status_code}")
                continue
            data = r.json()
            for res in data.get("organic_results", []):
                title = res.get("title", "")
                text = res.get("snippet", "")
                url = res.get("link", "")
                pub = ""
                if text and len(text) > 50:
                    articles.append((title, text, url, pub, "SerpAPI"))
        except Exception as e:
            print(f"  ⚠️ SerpAPI搜索异常: {e}")
        time.sleep(1)
    return articles


def _search_cls_cache(question: str, decomp: dict, days: int = 14) -> list:
    """搜索Redis缓存的财联社快讯"""
    try:
        import redis
        R = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
        if not R.ping():
            print("  📰 Redis不可用"); return []
    except Exception:
        print("  📰 Redis不可用"); return []
    
    # 基于实体构建关键词
    keywords = []
    entities = decomp.get('matched_entities', [])
    keywords.extend(entities)
    # 从问题中提取中文关键词（2-4字）
    cn_words = re.findall(r'[一-鿿]{2,4}', question)
    keywords.extend(cn_words[:5])
    keywords = list(set(keywords))
    
    results = []
    seen = set()
    for kw in keywords[:5]:
        try:
            ids = R.lrange("cls:latest", 0, 199)  # 最新200条
            for lid in ids:
                if lid in seen: continue
                seen.add(lid)
                item_str = R.hget("cls:all", lid)
                if not item_str: continue
                item = json.loads(item_str)
                title = item.get('title', '')
                content = item.get('content', '')
                if kw.lower() in title.lower() or kw.lower() in content.lower():
                    results.append({
                        "title": title,
                        "text": content[:500],
                        "url": "",
                        "pub": item.get('pub_date', '') + ' ' + item.get('pub_time', ''),
                        "source": "财联社"
                    })
        except Exception:
            pass
    print(f"  📰 财联社缓存: {len(results)}条")
    return results


def search_current_affairs(question: str, decomp: dict, days: int = 14) -> dict:
    """多源搜索近期时事，返回 {topic: [articles]} """
    from datetime import datetime, timedelta
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z")
    
    # 基于实体+维度构建搜索query
    entities = decomp.get('matched_entities', [])
    dims = decomp.get('triggered_dimensions', [])
    
    queries = [question[:60]]
    if entities:
        for ent in entities[:2]:
            queries.append(f"{ent} 最新动态 2025 2026")
    if '地缘冲突' in dims:
        queries.append("地缘政治 冲突 最新局势 2025 2026")
    
    # 四源搜索
    all_articles = []
    
    print(f"  🌐 [1/4] 财联社缓存...")
    all_articles.extend(_search_cls_cache(question, decomp, days))
    
    print(f"  🌐 [2/4] Exa搜索...")
    all_articles.extend(_search_exa(queries, start_date))
    
    print(f"  🌐 [3/4] Tavily搜索...")
    all_articles.extend(_search_tavily(queries))
    
    print(f"  🌐 [4/4] SerpAPI搜索...")
    all_articles.extend(_search_serpapi(queries))
    
    # 去重 by title前40字
    seen_titles = set()
    unique = []
    for title, text, url, pub, source in all_articles:
        key = title[:40]
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append({"title": title, "text": text, "url": url, "pub": pub, "source": source})
    
    results = {}
    if unique:
        results["时事搜索"] = unique[:20]
    
    src_counts = {}
    for a in unique:
        src_counts[a['source']] = src_counts.get(a['source'], 0) + 1
    print(f"  🌐 多源时事搜索: {len(unique)}条结果 → {src_counts}")
    return results


def format_affairs_for_prompt(affairs: dict) -> str:
    """格式化时事搜索为prompt文本"""
    if not affairs:
        return "（未搜到近期时事）"
    lines = ["## 🌐 实时情报（近期时事）"]
    for topic, articles in affairs.items():
        for a in articles[:8]:
            pub = a.get('pub', '')[:10]
            text = a['text'][:200].replace('\n', ' ')
            src = a.get('source', '?')
            lines.append(f"- 📰 [{pub}|{src}] {a['title']}\n  {text}")
    return "\n".join(lines)


def auto_ingest_materials(affairs: dict, question: str):
    """高质量长文自动保存为素材"""
    saved = 0
    materials_dir = os.path.join(REPORT_DIR, "_auto_materials")
    os.makedirs(materials_dir, exist_ok=True)
    
    for topic, articles in affairs.items():
        for a in articles:
            text = a.get('text', '')
            if len(text) < 2000:
                continue  # 只入库长文
            # 保存原始文本
            safe_title = re.sub(r'[^\w\u4e00-\u9fff]', '_', a['title'])[:50]
            ts = time.strftime('%Y%m%d_%H%M%S')
            path = os.path.join(materials_dir, f"{safe_title}_{ts}.txt")
            with open(path, 'w', encoding='utf-8') as f:
                f.write(f"Title: {a['title']}\nURL: {a.get('url', '')}\nPublished: {a.get('pub', '')}\nQuery: {question}\n\n{text}")
            saved += 1
            
            # 尝试入库到chromadb的materials集合
            try:
                client = chromadb.PersistentClient(path=DB_PATH)
                col = client.get_or_create_collection("materials", metadata={"hnsw:space": "cosine"})
                emb = embed(text[:500])
                if emb:
                    import hashlib
                    doc_id = hashlib.md5(text.encode()).hexdigest()[:16]
                    col.upsert(
                        ids=[f"auto_{doc_id}"],
                        embeddings=[emb],
                        documents=[text[:1000]],
                        metadatas=[{"person": "时事自动入库", "source": "exa", "title": a['title'], "url": a.get('url', ''), "type": "current_affairs"}]
                    )
            except Exception as e:
                print(f"  ⚠️ 自动入库失败: {e}")
    
    if saved:
        print(f"  📥 自动入库: {saved}条高质量素材")
    return saved


def run_pipeline(question: str, sub_queries: dict = None, auto_save: bool = True, enable_affairs: bool = True):
    """完整Pipeline v3 (红蓝对抗+分段生成+时事搜索)"""
    print("=" * 60)
    print("🧠 智库分析Pipeline v3 (红蓝对抗+分段生成+时事搜索)")
    print("=" * 60)

    # Redis
    try:
        status = get_redis_status()
        print(f"\n📊 Redis: {status['connected']} | 历史事件: {status.get('historical_events', '?')}条")
    except Exception as e:
        print(f"\n📊 Redis: 不可用 ({e})")

    # Step 1: 智能问题拆解
    print(f"\n📋 Step 1: 智能问题拆解")
    decomp = decompose_question(question)
    print(f"  维度: {decomp['triggered_dimensions']}")
    print(f"  实体: {decomp.get('matched_entities', ['通用'])}")
    print(f"  资产: {decomp['relevant_assets']}")
    auto_queries = decomp.get("auto_queries", {})

    # Step 2: 历史参照
    print(f"\n📚 Step 2: 历史参照")
    try:
        events = match_historical_events(question, decomp["triggered_dimensions"])
        print(f"  匹配 {len(events)} 个历史事件")
    except:
        events = []
        print(f"  匹配 0 个历史事件（Redis不可用）")

    # Step 3: 深度RAG（insights + core_profile + materials）
    print(f"\n🔍 Step 3: 深度RAG检索（含materials）")
    if sub_queries:
        print(f"  子议题: {len(sub_queries)}个（自定义）")
    else:
        print(f"  子议题: {len(auto_queries)}个（自动生成）")
        sub_queries = auto_queries

    rag_data = deep_rag_search(question, sub_queries, n_per_col=6)
    total_rag = sum(len(v) for v in rag_data.values())

    # 按collection统计
    col_counts = {}
    for items in rag_data.values():
        for item in items:
            col_counts[item["col"]] = col_counts.get(item["col"], 0) + 1
    print(f"  总计: {total_rag}条 → {col_counts}")

    # Step 3.5: 时事自动搜索
    affairs_data = {}
    if enable_affairs:
        print(f"\n🌐 Step 3.5: 时事自动搜索（多源：Exa+Tavily+SerpAPI）")
        affairs_data = search_current_affairs(question, decomp, days=14)
        # 自动入库高质量素材
        auto_ingest_materials(affairs_data, question)
    affairs_text = format_affairs_for_prompt(affairs_data)
    
    # Step 4: 红蓝对抗匹配
    print(f"\n⚔️  Step 4: 红蓝对抗匹配")
    selection = red_blue_match(question, rag_data)
    total_sel = sum(len(v) for v in selection.values())
    print(f"  总计: {total_sel}人/{len(RED_BLUE_TEAMS)}阵营")

    # Step 5: 构建分段task
    print(f"\n📝 Step 5: 构建分段报告任务")
    chapters = build_chapter_tasks(question, decomp, events, rag_data, selection, affairs_data)
    os.makedirs("/tmp", exist_ok=True)
    for ch_id, ch in chapters.items():
        with open(f"/tmp/{ch_id}_task.txt", "w") as f:
            f.write(ch["instruction"])
        print(f"  {ch_id} {ch['title']} ({ch['words']}字): {len(ch['instruction'])}字符")

    # 保存框架JSON
    framework = {
        "question": question,
        "decomposition": decomp,
        "historical_events": len(events),
        "rag_total": total_rag,
        "rag_col_counts": col_counts,
        "red_blue_selection": selection,
        "chapters": {k: {"title": v["title"], "words": v["words"]} for k, v in chapters.items()},
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    with open("/tmp/think_tank_v3_framework.json", "w") as f:
        json.dump(framework, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Pipeline就绪: {total_rag}条RAG + {len(events)}个历史事件 + {total_sel}人红蓝对抗")
    if auto_save:
        print(f"\n📄 报告将保存到: {REPORT_DIR}/")
    print(f"\n💡 各章节task已保存到 /tmp/ch0X_task.txt")
    print(f"   用GLM-5-TURBO子session逐章生成，然后拼接即可")

    return chapters, framework


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "中国收复台湾的战略窗口期是什么时候？必要条件是什么？"
    chapters, framework = run_pipeline(q)
