#!/usr/bin/env python3
"""
智库分析Pipeline v5.0
问题拆解 → 时事搜索 → RAG(按人聚合) → 框架对抗 → 条件情景树 → 预-mortem → 拼接

v5改动(心理学家评审共识):
- 去掉概率数字，改为条件判断树（若X→则Y）
- 红蓝对抗→框架对抗（不是人对抗，而是分析框架对抗）
- ch01因果图+历史类比库（不是简单的问题解构）
- ch04条件情景树+尾部风险+鲁棒性测试（禁止概率）
- 引用格式要求来源+原文+适用边界+因果连接
- ch03偏差审计要求标注偏差方向+去偏差操作

Usage:
    python3 think_tank_pipeline_v3.py "议题"
    python3 think_tank_pipeline_v3.py "议题" --no-affairs
"""

import json, sys, os, subprocess, time, re, hashlib
from pathlib import Path

import chromadb, requests
sys.path.insert(0, str(Path(__file__).parent))
from historical_context import search_historical_events, get_redis_status

DB_PATH = "/mnt/d/data/think-tank-data/_chromadb/lacan_db"
OLLAMA_URL = 'http://172.26.0.1:11434/api/embed'
OLLAMA_MODEL = 'bge-m3:latest'
REPORT_DIR = "/mnt/d/data/think-tank-data/reports"
EXA_API_KEY = os.environ.get('EXA_API_KEY', '')
TAVILY_API_KEY = os.environ.get('TAVILY_API_KEY', '')
SERPAPI_KEY = os.environ.get('SERPAPI_KEY', '')
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
    深度RAG搜索，返回按人聚合的数据（v4.2: 按人分组，insights不截断）
    返回: {person_name: {"profile": str, "insights": [str], "materials": [str], "tensions": [str]}}
    """
    client = chromadb.PersistentClient(path=DB_PATH)
    queries = sub_queries if sub_queries else {"default": question}
    COLLECTIONS = ["insights", "core_profile", "materials", "tension"]

    # 按人聚合: {person: {"profile": "", "insights": [], "materials": [], "tensions": []}}
    persons = {}

    for topic, query in queries.items():
        emb = embed(query)
        if emb is None:
            continue
        seen = set()

        for col_name in COLLECTIONS:
            try:
                col = client.get_collection(col_name)
                if col.count() == 0:
                    continue
                n = min(3 if col_name == "materials" else n_per_col, col.count())
                res = col.query(query_embeddings=[emb], n_results=n)
                for i, doc in enumerate(res["documents"][0]):
                    meta = res["metadatas"][0][i]
                    person = meta.get("person", meta.get("personality_name", "?"))
                    key = f"{person}|{col_name}"
                    if key in seen:
                        continue
                    seen.add(key)
                    if person not in persons:
                        persons[person] = {"profile": "", "insights": [], "materials": [], "tensions": []}
                    # insights和tension不截断，materials截断800字
                    text = doc if col_name != "materials" else doc[:800]
                    if col_name == "core_profile":
                        persons[person]["profile"] = text
                    elif col_name == "tension":
                        persons[person]["tensions"].append(text)
                    elif col_name == "materials":
                        persons[person]["materials"].append(text)
                    else:
                        persons[person]["insights"].append(text)
            except Exception as e:
                pass

    return persons


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
    """格式化RAG数据为prompt文本（v4.2: 按学者分组，insights完整呈现）"""
    if not rag_data:
        return "（无RAG数据）"
    sections = []
    for person, data in rag_data.items():
        lines = [f"## 学者 {person}"]
        if data.get("profile"):
            lines.append(f"**核心立场**：{data['profile'].replace(chr(10), ' ')[:200]}")
        if data.get("insights"):
            lines.append("**观点与论证**：")
            for ins in data["insights"][:5]:
                lines.append(f"- 「{ins.replace(chr(10), ' ')}」")
        if data.get("tensions"):
            lines.append("**内在张力**：")
            for t in data["tensions"][:2]:
                lines.append(f"- {t.replace(chr(10), ' ')}")
        if data.get("materials"):
            lines.append("**原文参考**：")
            for m in data["materials"][:2]:
                lines.append(f"- {m.replace(chr(10), ' ')}")
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
1. 使用RAG数据中的具体观点，【引用纪律】每个关键论点必须直接引用下方学者的具体观点，格式：「[学者名]在其[来源]中指出/论证道：「[引用insight原文，用引号包裹]」」。严禁纯转述（如「XX认为…」而不给出具体内容）。引用insights中的原文即可，这些是蒸馏后的核心逻辑表述。当学者存在内在张力时，必须呈现这种张力。
2. 心理学家分析要深刻具体（认知偏差、群体决策、道德脱离、社会认同、损失厌恶等）
3. 历史类比严谨，注意区分不同历史情境的异同
4. 所有情景概率必须归一化（总和=100%）
5. 中文，6000-8000字
6. 结构：问题解构→历史参照矩阵→候选方案深度分析（含心理画像+集体决策偏差）→跨学科交叉验证→情景推演与投资影响→自优化建议
7. 💡标记的是深层逻辑观点，📄标记的是原始素材参考，都要充分利用
8. 每个关键判断必须呈现红蓝双方的**对立观点**——不能只有红方声音
9. A股/港股数据：如果涉及中国市场，必须分析上证指数、沪深300、创业板指的走势影响
10. 提供具体的军事能力数据（舰艇数量、兵力规模、装备代差）而非纯定性描述
11. 心理学家（丹尼尔·卡尼曼/乔纳森·海特/罗伯特·西奥迪尼/所罗门·阿希/阿尔伯特·班杜拉/保罗·艾克曼）必须有实质性引用

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
}

# 心理学家独立于红蓝对抗（v4：偏差审计层）
PSYCHOLOGISTS = ["丹尼尔·卡尼曼", "乔纳森·海特", "欧文·亚隆", "罗伯特·西奥迪尼", "所罗门·阿希", "阿尔伯特·班杜拉", "保罗·艾克曼", "丹·阿瑞利"]


def red_blue_match(question: str, rag_data: dict) -> dict:
    """从RAG结果中筛选红蓝对抗阵容（不含心理学家），独立匹配心理学家人格"""
    rag_persons = set(rag_data.keys())

    # 红蓝阵营（不含心理学家）
    selection = {}
    for team, members in RED_BLUE_TEAMS.items():
        available = [m for m in members if m in rag_persons]
        selection[team] = available[:3]
        print(f"  {team}: {len(selection[team])}人 → {selection[team]}")

    # 心理学家独立匹配
    psych_available = [m for m in PSYCHOLOGISTS if m in rag_persons]
    selection["心理学家"] = psych_available[:4]
    print(f"  心理学家(独立): {len(selection['心理学家'])}人 → {selection['心理学家']}")

    return selection


def format_red_blue_prompt(selection: dict) -> str:
    """格式化红蓝对抗prompt（心理学家标注为独立审计层）"""
    role_map = {
        "红方军事鹰派": "进攻视角，主张行动",
        "红方战略家": "中国决策层视角，权衡利弊",
        "红方学者": "中国精英分析视角",
        "蓝方美国": "美国/西方视角，强调风险和威慑",
        "蓝方经济": "经济风险警示视角，强调代价",
        "蓝方历史": "历史教训视角，强调失败案例",
        "心理学家": "独立偏差审计层（不参与红蓝对抗）",
    }
    lines = ["## 框架对抗阵容（不同分析框架看到不同的因果路径）"]
    for team, persons in selection.items():
        if team == "心理学家":
            lines.append(f"- **{team}**（独立偏差审计层）: {', '.join(persons)}")
        else:
            lines.append(f"- **{team}**: {', '.join(persons)}")
    lines.append("")
    lines.append("**要求：不是人对抗，而是框架对抗。同一事件用不同理论框架分析会看到不同因果路径。"
                 "标注每个框架的推理链和盲区。心理学家（丹尼尔·卡尼曼/乔纳森·海特/罗伯特·西奥迪尼/所罗门·阿希/阿尔伯特·班杜拉/保罗·艾克曼）必须有实质性引用。**")
    return "\n".join(lines)


def build_chapter_tasks(question: str, decomp: dict, events: list, rag_data: dict, selection: dict, affairs_data: dict = None) -> dict:
    """构建报告章节任务（v5: 条件判断+框架对抗+因果推理）"""
    dims = ', '.join(decomp['triggered_dimensions'])
    ents = ', '.join(decomp.get('matched_entities', ['通用']))
    assets = ', '.join(decomp['relevant_assets'])

    affairs_text = format_affairs_for_prompt(affairs_data) if affairs_data else ""
    affairs_block = f"\n\n{affairs_text}\n" if affairs_text else ""
    rag_text = format_rag_for_prompt(rag_data)
    hist_text = format_historical_for_prompt(events)
    rb_text = format_red_blue_prompt(selection)

    common = f"\n## 核心问题\n{question}{affairs_block}\n\n{rb_text}\n"

    quote_rule = '【引用纪律】每个关键论点必须直接引用下方学者的具体观点。格式要求：先给出来源（学者名+作品/框架名+年份），再引用原文核心论断，最后说明该论点的适用边界和与当前问题的因果连接。严禁：纯转述（「XX认为」而无原文）、[RAG: XX]式标签（不可验证不可挑战）、无边界条件的引用。当学者存在内在张力时必须呈现。'

    # v5: 4章 — 因果图→框架对抗→偏差审计→条件情景树
    chapters = {
        "ch01": {"title": "因果图与历史类比库", "words": "1500-1800",
            "instruction": f"你是因果分析引擎。撰写第1章：因果图与历史类比库（1500-1800字）。{common}\n## 维度：{dims} | 实体：{ents} | 资产：{assets}\n\n### 第一部分：因果变量图（800-1000字）\n识别当前局势中的关键因果变量及其相互关系。要求：\n1. 列出5-8个关键变量（如：地缘冲突烈度、能源供给中断风险、通胀传导链条、技术替代速度等）\n2. 标注变量间的因果方向（A→B）和相互作用（A↔B）\n3. 标注每个变量的不确定性来源（已知/部分已知/未知）\n4. 区分已确认事实与推测性判断\n\n禁止：空洞过渡句、重复数据、编造因果链。{quote_rule}\n\n### 第二部分：历史类比库（500-700字）\n从历史中提取2-3个最相关的类比事件。每个类比必须包含：\n1. 事件概述（时间/背景/结果）\n2. 与当前局势的相似性\n3. 与当前局势的关键差异\n4. 该类比的推理启示（不是「历史会重演」，而是「这个变量在那个场景下是怎么演化的」）\n\n## 历史参照\n{hist_text}\n\n## RAG参考\n{rag_text}"},
        "ch02": {"title": "框架对抗与关键分歧", "words": "1500-2000",
            "instruction": f"你是学术辩论裁判。撰写第2章：框架对抗与关键分歧（1500-2000字）。{common}\n\n核心理念：不是红方vs蓝方的人对抗，而是**分析框架之间的对抗**。同一事件，用不同理论框架看会得到不同的因果路径。\n\n写作结构：\n1. 识别2-3个关键分歧点（不是「结论不同」，而是「底层因果假设不同」）\n2. 对每个分歧点，呈现不同框架的推理路径：\n   - 现实主义框架看到的是什么因果链\n   - 经济学框架看到的是什么因果链\n   - 历史类比框架看到的是什么因果链\n3. 交锋：各框架的盲区在哪里？哪个框架的因果链在当前条件下更可能成立？\n4. 收敛：不同框架是否在某些中间结论上达成一致？\n\n禁止：并列式罗列（「A认为X，B认为Y」不是交锋）、车轱辘话、用概率替代推理。{quote_rule}\n\n## RAG参考\n{rag_text}"},
        "ch03": {"title": "认知偏差审计", "words": "1200-1500",
            "instruction": f"你是认知偏差审计专家。撰写第3章：认知偏差审计（1200-1500字）。{common}\n\n你不是红蓝任何一方，而是独立元分析者。你的任务是：标注本报告各关键判断可能受哪些认知偏差影响，并给出去偏差的具体操作。\n\n审计框架（8位心理学家，每人至少一个实质性应用）：\n1. 丹尼尔·卡尼曼：前景理论/损失厌恶/系统双过程/框架效应\n2. 所罗门·阿希：从众效应（区分信息性vs规范性影响）\n3. 菲利普·津巴多：情境力量/去个性化\n4. 乔纳森·海特：道德基础理论——各方在哪些道德维度上不可通约\n5. 罗伯特·西奥迪尼：影响力原则——互惠/权威/社会认同/稀缺/承诺与一致/喜好\n6. 阿尔伯特·班杜拉：道德脱离/三元交互决定论——螺旋升级机制\n7. 保罗·艾克曼：情绪动力学——恐惧和愤怒的阶段变化、情绪劫持临界点\n8. 丹·阿瑞利：锚定效应/任意一致性/默认偏差\n\n每个偏差必须：\na) 引用具体理论/实验名称和学者的insight原文\nb) 标注本报告中**哪个具体判断**可能受此偏差影响\nc) 标注偏差方向（该偏差使我们高估还是低估了X）\nd) 给出去偏差的具体操作（不是「要注意」，而是「为对抗锚定效应，我们从历史基准率出发重新评估……」）\n\n禁止：对称补刀式「双方都有偏差」（要分析不对称性）、罗列理论而不锚定到本报告的具体判断。{quote_rule}\n\n## RAG参考\n{rag_text}"},
        "ch04": {"title": "条件情景树与鲁棒性测试", "words": "1500-1800",
            "instruction": f"你是战略推理引擎。撰写第4章：条件情景树与鲁棒性测试（1500-1800字）。{common}\n\n⚠️ 绝对禁止：给任何事件赋概率数字（如30%、45%、25%）。概率数字是认知暴力——它给读者虚假精确感，实际上只是LLM的随机输出。\n\n### 第一部分：条件情景树（800-1000字）\n用「若X条件满足→则Y更可能发生」的条件判断替代概率。\n\n结构：\n- 情景A：触发条件=[具体可观测的事件/指标] → 推演=[基于哪个学者的框架，因果链是什么] → 核心指标变化=[具体数字区间+时间框架] → 持续时间\n- 情景B：同上\n- 情景C：同上\n- 每个情景必须标注：判断依据（历史基准率或学者框架）、该判断在什么条件下需要更新\n\n### 第二部分：尾部风险清单（300-400字）\n列出2-3个不可量化但需关注的黑天鹅场景。这些是「我们无法预测但必须设想」的情况。每个包含：场景描述+为什么不可量化+最坏后果。\n\n### 第三部分：鲁棒性测试与投资启示（300-400字）\n本报告的核心建议在什么条件下会失效？给出具体的失效条件检测指标。\n投资启示（如果适用）：不预测方向，而是设计从波动中获益的结构（反脆弱思路）。具体标的+仓位逻辑，但必须标注「这是基于X假设的，若Y则失效」。\n\n{quote_rule}\n\n## 资产：{assets}\n## RAG参考\n{rag_text}\n\n## 历史参照\n{hist_text}"},
    }

    premortem = {"title": "预-mortem偏差审计", "words": "800-1000",
        "instruction": f"你是魔鬼代言人。假设以下报告发布6个月后，被专业分析师评为「垃圾报告」。请回溯最可能的3个失败原因。{common}\n\n审计维度：1.核心假设盲区：最可能被证伪的3个假设及替代假设 2.因果链断点：哪些因果推理链最脆弱 3.遗漏变量：忽略了什么关键变量 4.框架偏差：哪个分析框架的盲区最可能导致误判 5.条件情景漏洞：什么未被设想的情景会使全部推演失效\n\n每个失败原因：具体描述+置信度（高/中/低）+补救建议。直接输出正文。"}
    return chapters, premortem


def _search_exa(queries: list, start_date: str) -> list:
    """Exa搜索（付费API，失败时静默跳过）"""
    if not EXA_API_KEY:
        return []
    try:
        articles = []
        for q in queries[:3]:
            r = requests.post("https://api.exa.ai/search", json={
                "query": q, "num_results": 5, "start_published_date": start_date,
                "type": "auto", "contents": {"text": {"maxCharacters": 500}}
            }, headers={"x-api-key": EXA_API_KEY}, timeout=15)
            if r.status_code != 200:
                continue
            for a in r.json().get("results", []):
                articles.append({"title": a.get("title", ""),
                    "text": a.get("text", "")[:500],
                    "url": a.get("url", ""),
                    "pub": a.get("publishedDate", ""),
                    "source": "exa"})
            time.sleep(0.3)
        return articles
    except Exception:
        return []


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
    
    # 三源搜索（Exa做fallback）
    all_articles = []

    print(f"  🌐 [1/3] 财联社缓存...")
    all_articles.extend(_search_cls_cache(question, decomp, days))

    print(f"  🌐 [2/3] Exa搜索...")
    exa_results = _search_exa(queries, start_date)
    if exa_results:
        all_articles.extend(exa_results)
        print(f"    Exa: {len(exa_results)}条")
    else:
        print(f"    Exa: 失败，跳过")

    print(f"  🌐 [3/3] Tavily搜索...")
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
    total_rag = len(rag_data)
    total_insights = sum(len(d.get('insights',[])) for d in rag_data.values())
    total_tensions = sum(len(d.get('tensions',[])) for d in rag_data.values())
    print(f"  学者: {total_rag}人 | insights: {total_insights}条 | tensions: {total_tensions}条")

    # Step 3.5: 时事自动搜索
    affairs_data = {}
    if enable_affairs:
        print(f"\n🌐 Step 3.5: 时事自动搜索（多源：Exa+Tavily+SerpAPI）")
        affairs_data = search_current_affairs(question, decomp, days=14)
        # 自动入库高质量素材
        auto_ingest_materials(affairs_data, question)
    affairs_text = format_affairs_for_prompt(affairs_data)
    
    # Step 4: 红蓝对抗匹配
    print(f"\n⚔️  Step 4: 红蓝对抗匹配（心理学家独立）")
    selection = red_blue_match(question, rag_data)
    rb_total = sum(len(v) for k, v in selection.items() if k != "心理学家")
    psych_total = len(selection.get("心理学家", []))
    print(f"  红蓝阵营: {rb_total}人 | 心理学家(独立): {psych_total}人")

    # Step 5: 构建分段task（v4: 4章 + 预-mortem）
    print(f"\n📝 Step 5: 构建分段报告任务（4章 + 预-mortem偏差审计）")
    chapters, premortem = build_chapter_tasks(question, decomp, events, rag_data, selection, affairs_data)
    os.makedirs("/tmp", exist_ok=True)
    topic_hash = hashlib.md5(question.encode()).hexdigest()[:4]
    for ch_id, ch in chapters.items():
        with open(f"/tmp/{topic_hash}_{ch_id}_task.txt", "w") as f:
            f.write(ch["instruction"])
        print(f"  {ch_id} {ch['title']} ({ch['words']}字): {len(ch['instruction'])}字符")
    # 预-mortem task
    with open(f"/tmp/{topic_hash}_premortem_task.txt", "w") as f:
        f.write(premortem["instruction"])
    print(f"  premortem {premortem['title']} ({premortem['words']}字): {len(premortem['instruction'])}字符")

    # 保存框架JSON
    framework = {
        "question": question,
        "decomposition": decomp,
        "historical_events": len(events),
        "rag_total": total_rag,
        "rag_col_counts": {"insights": total_insights, "tension": total_tensions, "materials": sum(len(d.get('materials',[])) for d in rag_data.values())},
        "red_blue_selection": {k: v for k, v in selection.items()},
        "chapters": {k: {"title": v["title"], "words": v["words"]} for k, v in chapters.items()},
        "premortem": {"title": premortem["title"], "words": premortem["words"]},
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    with open("/tmp/think_tank_v3_framework.json", "w") as f:
        json.dump(framework, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Pipeline就绪: {total_rag}条RAG + {len(events)}个历史事件 + {rb_total}人红蓝对抗 + {psych_total}人心理学家(独立)")
    if auto_save:
        print(f"\n📄 报告将保存到: {REPORT_DIR}/")
    print(f"\n💡 各章节task已保存到 /tmp/{topic_hash}_ch0X_task.txt + _premortem_task.txt")
    print(f"   生成顺序：ch01-ch04并行 → premortem审计 → 拼接最终报告")

    return chapters, premortem, framework


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "中国收复台湾的战略窗口期是什么时候？必要条件是什么？"
    chapters, premortem, framework = run_pipeline(q)
