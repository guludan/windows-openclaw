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

import lancedb, requests
sys.path.insert(0, str(Path(__file__).parent))
from historical_context import search_historical_events, get_redis_status

DB_PATH = "/mnt/d/data/think-tank-data/personalities/_lacandb/lacan_db"
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


# ============ 深度RAG（含evidence） ============

def deep_rag_search(question: str, sub_queries: dict = None, n_per_col: int = 6) -> dict:
    """
    深度RAG搜索，返回按人聚合的数据（v4.2: 按人分组，insights不截断）
    返回: {person_name: {"profile": str, "insights": [str], "evidence": [str], "tensions": [str]}}
    """
    db = lancedb.connect(DB_PATH)
    queries = sub_queries if sub_queries else {"default": question}
    # v6: 修正collection名称，匹配实际6个collection
    COLLECTIONS = ["personas", "cognitive", "claims", "evidence", "tracing", "tension"]
    # personas和cognitive权重更高（核心身份和思维模式）
    HIGH_WEIGHT_COLS = {"personas", "cognitive"}

    # 按人聚合: {person: {"profile": "", "insights": [], "evidence": [], "tensions": []}}
    persons = {}

    for topic, query in queries.items():
        emb = embed(query)
        if emb is None:
            continue
        seen = set()

        for col_name in COLLECTIONS:
            try:
                table = db.open_table(col_name)
                n = 3 if col_name == "evidence" else n_per_col
                results = table.search(emb).limit(n).to_list()
                if not results:
                    continue
                for row in results:
                    person = row.get("person", row.get("personality_name", "?"))
                    doc = row.get("document", row.get("text", ""))
                    key = f"{person}|{col_name}"
                    if key in seen:
                        continue
                    seen.add(key)
                    if person not in persons:
                        persons[person] = {"profile": "", "insights": [], "evidence": [], "tensions": []}
                    text = doc if col_name != "evidence" else doc[:800]
                    if col_name in ("personas", "cognitive"):
                        # personas/cognitive → profile（核心身份+思维模式）
                        if not persons[person]["profile"]:
                            persons[person]["profile"] = text
                        else:
                            persons[person]["profile"] += "\n" + text[:500]
                    elif col_name == "tension":
                        persons[person]["tensions"].append(text)
                    elif col_name == "evidence":
                        persons[person]["evidence"].append(text)
                    elif col_name == "tracing":
                        # tracing → evidence补充
                        persons[person]["evidence"].append(text[:800])
                    else:
                        # claims → insights
                        persons[person]["insights"].append(text)
                    # 高权重collection重复检索
                    if col_name in HIGH_WEIGHT_COLS:
                        extra_n = 3
                        extra = table.search(emb).limit(n + extra_n).to_list()
                        for j in range(n, min(n + extra_n, len(extra))):
                            row_e = extra[j]
                            e_person = row_e.get("person", row_e.get("personality_name", "?"))
                            e_doc = row_e.get("document", row_e.get("text", ""))
                            e_key = f"{e_person}|{col_name}|extra_{j}"
                            if e_key in seen: continue
                            seen.add(e_key)
                            if e_person not in persons:
                                persons[e_person] = {"profile": "", "insights": [], "evidence": [], "tensions": []}
                            if col_name == "personas" and not persons[e_person]["profile"]:
                                persons[e_person]["profile"] = e_doc
                            elif col_name == "cognitive":
                                persons[e_person]["insights"].append(e_doc)
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
        if data.get("evidence"):
            lines.append("📄 原文证据：")
            for m in data["evidence"][:2]:
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

    # v6: 如果rag_persons太少，从personas collection补充
    if len(rag_persons) < 10:
        try:
            db = lancedb.connect(DB_PATH)
            table = db.open_table("personas")
            emb = embed(question)
            if emb:
                results = table.search(emb).limit(30).to_list()
                for row in results:
                    person = row.get("person", row.get("personality_name", "?"))
                    doc = row.get("document", row.get("text", ""))
                    if person not in rag_persons:
                        rag_persons.add(person)
                        if person not in rag_data:
                            rag_data[person] = {"profile": doc[:500], "insights": [], "evidence": [], "tensions": []}
        except Exception:
            pass

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


def extract_theory_skeleton(rag_data: dict) -> dict:
    """v6: 从RAG数据提取学者理论骨架，识别分歧点，输出章节结构建议"""
    if not rag_data:
        return {"scholarly_skeleton": "（无RAG数据）", "divergence_points": [], "chapter_suggestion": ""}

    scholar_frameworks = []
    for person, data in rag_data.items():
        profile = data.get("profile", "")
        insights = data.get("insights", [])
        tensions = data.get("tensions", [])
        if not insights and not profile:
            continue
        scholar_frameworks.append({
            "person": person,
            "profile": profile[:300],
            "core_insights": [ins[:200] for ins in insights[:3]],
            "tensions": [t[:150] for t in tensions[:2]],
        })

    # 构建LLM prompt提取理论框架
    scholar_text_parts = []
    for sf in scholar_frameworks[:20]:  # 最多20人
        part = f"### {sf['person']}\n"
        if sf['profile']:
            part += f"身份: {sf['profile']}\n"
        part += "核心观点: " + " | ".join(sf['core_insights']) + "\n"
        if sf['tensions']:
            part += "内在张力: " + " | ".join(sf['tensions']) + "\n"
        scholar_text_parts.append(part)
    scholar_text = "\n".join(scholar_text_parts)

    # 用本地LLM提取骨架（直接调用Ollama）
    prompt = f"""你是学术分析引擎。从以下学者数据中：
1. 提取每位学者的理论框架（前提假设→因果路径→核心预测→盲区），每人2-3行
2. 识别框架间的2-4个根本分歧点（因果路径不同、预测方向相反、假设前提冲突）
3. 建议以分歧点为骨架的章节组织方式

输出格式：
## 学者框架
（每人2-3行精炼摘要）
## 根本分歧点
（每个分歧点：对立框架+分歧本质）
## 章节结构建议
（基于分歧点组织）

学者数据：
{scholar_text}"""

    skeleton = ""
    # ⚠️ 理论骨架提取必须走子session（sessions_spawn），不允许在脚本中直接调用本地模型

    return {
        "scholarly_skeleton": skeleton,
        "divergence_points": [],  # LLM输出中已包含
        "chapter_suggestion": "",
        "scholar_frameworks": scholar_frameworks,
    }


def generate_quant_output(question: str, decomp: dict, scenarios: str, market_data: dict) -> dict:
    """v6: 从情景树提取量化信号，生成结构化JSON输出"""
    assets = decomp.get('relevant_assets', [])
    if not assets and not market_data:
        return {}

    prompt = f"""你是量化分析师。基于以下情景分析和市场数据，生成量化交易信号。

议题: {question}
相关资产: {', '.join(assets)}
市场数据: {json.dumps(market_data, ensure_ascii=False)[:1500]}
情景分析:
{scenarios[:3000]}

请输出严格JSON（不要markdown代码块）：
{{
  "probability_distribution": {{
    "scenario_bull": 0.0,
    "scenario_base": 0.0,
    "scenario_bear": 0.0,
    "scenario_tail": 0.0
  }},
  "price_levels": [
    {{"asset": "", "direction": "long/short", "entry": 0.0, "target": 0.0, "stop": 0.0, "trigger_condition": ""}}
  ],
  "cross_asset_correlation": {{
    "note": "条件相关性矩阵，情景触发时的相关性变化",
    "pairs": [{{"pair": "A/B", "normal": 0.0, "stress": 0.0}}]
  }},
  "kelly_sizing": [
    {{"asset": "", "edge": 0.0, "odds": 0.0, "kelly_fraction": 0.0, "recommended_pct": 0.0}}
  ]
}}"""
    # ⚠️ 量化输出生成必须走子session（sessions_spawn），不允许在脚本中直接调用本地模型
    return {}



def build_chapter_tasks(question: str, decomp: dict, events: list, rag_data: dict, selection: dict, affairs_data: dict = None, market_data: dict = None, theory_skeleton: dict = None) -> dict:
    """构建报告章节任务（v5: 条件判断+框架对抗+因果推理+市场数据）"""
    dims = ', '.join(decomp['triggered_dimensions'])
    ents = ', '.join(decomp.get('matched_entities', ['通用']))
    assets = ', '.join(decomp['relevant_assets'])

    affairs_text = format_affairs_for_prompt(affairs_data) if affairs_data else ""
    affairs_block = f"\n\n{affairs_text}\n" if affairs_text else ""
    market_text = format_market_for_prompt(market_data) if market_data else ""
    market_block = f"\n\n{market_text}\n" if market_text else ""
    rag_text = format_rag_for_prompt(rag_data)
    hist_text = format_historical_for_prompt(events)
    rb_text = format_red_blue_prompt(selection)

    common = f"\n## 核心问题\n{question}{affairs_block}{market_block}\n\n{rb_text}\n"

    quote_rule = '【引用纪律】每个关键论点必须直接引用下方学者的具体观点。格式要求：先给出来源（学者名+作品/框架名+年份），再引用原文核心论断，最后说明该论点的适用边界和与当前问题的因果连接。严禁：纯转述（「XX认为」而无原文）、[RAG: XX]式标签（不可验证不可挑战）、无边界条件的引用。当学者存在内在张力时必须呈现。'

    # v6: 理论骨架文本
    skeleton_text = ""
    if theory_skeleton and theory_skeleton.get("scholarly_skeleton"):
        skeleton_text = theory_skeleton["scholarly_skeleton"]

    # v6: 6章结构 — 执行摘要→学者理论骨架→框架对抗→偏差审计→条件情景树→量化信号
    chapters = {
        "ch00": {"title": "执行摘要", "words": "300",
            "instruction": f"你是高级编辑。撰写执行摘要（300字以内）。{common}\n\n要求：\n1. 一句话概括核心问题\n2. 3-5个关键发现（每个一句话）\n3. 最重要的行动建议\n4. 最大的不确定性来源\n\n注意：这是最后写的章节，但放在最前面。等ch01-ch05完成后再写。"},
        "ch01": {"title": "学者理论骨架", "words": "1200-1500",
            "instruction": f"你是理论分析引擎。撰写第1章：学者理论骨架（1200-1500字）。{common}\n\n核心任务：展示RAG中各位学者的底层分析框架，而不是LLM自创因果图。\n\n{ skeleton_text}\n\n## 维度：{dims} | 实体：{ents} | 资产：{assets}\n\n写作结构：\n1. 按分歧点分组，每组呈现对立的学者框架\n2. 每个框架：前提假设→因果路径→核心预测→盲区\n3. 标注每个框架的适用边界条件\n\n禁止：LLM自创因果链、空洞总结。必须基于上方RAG数据。{quote_rule}\n\n## RAG参考\n{rag_text}"},
        "ch02": {"title": "框架对抗与关键分歧", "words": "1500-1800",
            "instruction": f"你是学术辩论裁判。撰写第2章：框架对抗与关键分歧（1500-1800字）。{common}\n\n核心理念：不是红方vs蓝方的人对抗，而是**分析框架之间的对抗**。同一事件，用不同理论框架看会得到不同的因果路径。\n\n写作结构：\n1. 识别2-3个关键分歧点（不是「结论不同」，而是「底层因果假设不同」）\n2. 对每个分歧点，呈现不同框架的推理路径\n3. 交锋：各框架的盲区在哪里？\n4. 收敛：不同框架是否在某些中间结论上达成一致？\n\n禁止：并列式罗列、车轱辘话。{quote_rule}\n\n## RAG参考\n{rag_text}"},
        "ch03": {"title": "认知偏差审计（Checklist）", "words": "800-1000",
            "instruction": f"你是认知偏差审计专家。撰写第3章：认知偏差审计（800-1000字，Checklist格式）。{common}\n\n使用Checklist格式，每个偏差一条：\n\n### [偏差名称] — [偏差方向：高估/低估X]\n- **学者**: [引用卡尼曼/海特/西奥迪尼/阿希/班杜拉/艾克曼/阿瑞利的理论和insight原文]\n- **影响判断**: 本报告中[哪个具体判断]可能受此偏差影响\n- **去偏差操作**: [具体操作，不是「要注意」]\n\n至少覆盖6位心理学家的核心偏差。禁止罗列理论而不锚定到本报告。{quote_rule}\n\n## RAG参考\n{rag_text}"},
        "ch04": {"title": "条件情景树（含量化触发指标）", "words": "1200-1500",
            "instruction": f"你是战略推理引擎。撰写第4章：条件情景树（1200-1500字）。{common}\n\n⚠️ 禁止给事件赋概率数字。用条件判断替代。\n\n结构：\n- 情景A：触发条件=[具体可观测指标/事件+量化阈值] → 推演=[基于哪个学者框架] → 核心指标变化=[数字区间+时间框架]\n- 情景B/C同上\n- 每个情景必须包含量化触发指标（如：布伦特原油>$120、VIX>35、美元指数突破105）\n\n尾部风险清单：2-3个黑天鹅场景。\n\n{quote_rule}\n\n## 资产：{assets}\n## RAG参考\n{rag_text}\n\n## 历史参照\n{hist_text}"},
        "ch05": {"title": "量化信号与交易建议", "words": "800-1000",
            "instruction": f"你是量化策略分析师。撰写第5章：量化信号与交易建议（800-1000字）。{common}\n\n基于前面章节的情景分析，生成：\n1. **关键价格水平与触发条件**：每个相关资产的具体入场/目标/止损位\n2. **跨资产条件相关性**：情景触发时相关性如何变化\n3. **Kelly Criterion仓位建议**：基于情景概率的仓位sizing\n4. **反脆弱结构**：如何从波动中获益（期权结构、对冲组合）\n5. **失效条件**：以上建议在什么条件下需要完全推翻\n\n所有建议必须标注假设前提和失效条件。不构成投资建议。\n\n## 资产：{assets}\n{market_block}"},
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


def _search_baidu(queries: list) -> list:
    """百度搜索（通过r.jina.ai绕反爬），返回 [(title, text, url, pub, source)]"""
    articles = []
    for q in queries[:2]:
        try:
            # 用r.jina.ai代理百度搜索
            search_url = f"https://www.baidu.com/s?wd={requests.utils.quote(q)}&rn=5"
            proxy_url = f"https://r.jina.ai/{search_url}"
            r = requests.get(proxy_url, headers={"Accept": "text/plain"}, timeout=15)
            if r.status_code != 200:
                continue
            text = r.text
            # 从纯文本中提取标题和摘要（jina返回markdown）
            lines = text.split('\n')
            current_title = ""
            current_url = ""
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # 标题通常是较短的行，且可能包含方括号编号
                if line.startswith('[') and ']' in line and len(line) < 150 and '...' not in line:
                    if current_title and len(current_title) > 5:
                        articles.append((current_title, current_url[:300], "", "", "百度"))
                    current_title = line.lstrip('[').split(']', 1)[-1].strip()
                    current_url = ""
                elif current_title and len(line) > 20:
                    current_url += line + " "
            if current_title and len(current_title) > 5:
                articles.append((current_title, current_url[:300], "", "", "百度"))
        except Exception as e:
            print(f"  ⚠️ 百度搜索异常: {e}")
        time.sleep(1)
    return articles


def _search_zhihu(queries: list) -> list:
    """知乎搜索，返回 [(title, text, url, pub, source)]"""
    cookie_path = os.path.expanduser("~/.openclaw/workspace/.cookies/zhihu_cookies.txt")
    if not os.path.exists(cookie_path):
        return []
    try:
        with open(cookie_path, 'r') as f:
            cookie_str = f.read().strip()
    except Exception:
        return []
    articles = []
    for q in queries[:2]:
        try:
            headers = {"User-Agent": "Mozilla/5.0", "Cookie": cookie_str}
            r = requests.get(
                "https://www.zhihu.com/api/v4/search_v3",
                params={"t": "general", "q": q, "correction": 1, "offset": 0, "limit": 5},
                headers=headers, timeout=10
            )
            if r.status_code != 200:
                continue
            data = r.json()
            for item in data.get("data", [])[:5]:
                obj = item.get("object", {}) or item
                title = obj.get("title") or obj.get("question", {}).get("title", "")
                text = obj.get("excerpt") or obj.get("content", "")[:500]
                url = f"https://www.zhihu.com/question/{obj.get('id', '')}"
                if title and text:
                    articles.append((title, text, url, "", "知乎"))
        except Exception:
            pass
        time.sleep(1)
    return articles


MARKET_DATA_DIR = "/mnt/d/data/conflict-data/market/"
MARKET_KEYWORD_MAP = {
    # (关键词列表): [(JSON文件名, 指标中文名, 数据类型)]
    ("通胀", "CPI", "物价"): [("fred_CPIAUCSL_美国CPI.json", "美国CPI", "fred")],
    ("利率", "LPR", "加息", "降息"): [("fred_DGS10.json", "美国10年期国债收益率", "fred")],
    ("美元", "汇率", "人民币"): [
        ("fred_DEXCHUS.json", "美元/人民币汇率", "fred"),
        ("fred_DTWEXBGS_美元指数.json", "美元指数", "fred"),
    ],
    ("黄金", "金价"): [("fred_VIXCLS.json", "VIX恐慌指数", "fred")],
    ("股市", "A股", "上证", "大盘"): [("akshare_sh000001_上证指数.json", "上证指数", "stock")],
    ("港股", "恒生"): [("akshare_hang_seng.json", "恒生指数", "stock")],
    ("石油", "油价", "原油", "能源"): [("fred_DCOILBRENTEU.json", "布伦特原油", "fred")],
    ("失业"): [("fred_UNRATE_失业率.json", "美国失业率", "fred")],
    ("GDP", "经济总量"): [("fred_GDP_美国GDP.json", "美国GDP", "fred")],
    ("日本", "日经"): [("fred_NIKKEI225.json", "日经225", "fred")],
    ("美股", "纳斯达克", "标普"): [
        ("fred_SP500.json", "标普500", "fred"),
        ("fred_NIKKEI225.json", "日经225", "fred"),
    ],
    ("国债", "债务"): [("fred_GFDEBTN_美国国债.json", "美国国债总额", "fred")],
}


def _fetch_market_data(question: str, decomp: dict) -> dict:
    """根据议题定向拉取相关市场数据，返回 {指标名: [最新5条]}"""
    # 构建搜索关键词
    keywords = set()
    question_lower = question.lower()
    for words in MARKET_KEYWORD_MAP:
        for w in words:
            if w.lower() in question_lower:
                keywords.add(words)
                break
    entities = decomp.get('matched_entities', [])
    for ent in entities:
        for words in MARKET_KEYWORD_MAP:
            for w in words:
                if w.lower() in ent.lower():
                    keywords.add(words)
                    break
    if not keywords:
        return {}

    results = {}
    for words in keywords:
        for fname, label, dtype in MARKET_KEYWORD_MAP[words]:
            fpath = os.path.join(MARKET_DATA_DIR, fname)
            if not os.path.exists(fpath):
                continue
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, list) or not data:
                    continue
                # 取最新5条
                latest = data[-5:] if dtype == "stock" else data[-5:]
                formatted = []
                for item in latest:
                    if dtype == "stock":
                        ts = item.get('date', 0)
                        close = item.get('close', 0)
                        if close <= 0:
                            continue
                        if isinstance(ts, (int, float)) and ts > 1e12:
                            from datetime import datetime
                            dt = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
                        else:
                            dt = str(ts)[:10]
                        formatted.append({"date": dt, "close": close})
                    else:
                        val_str = item.get('value', '')
                        if not val_str or val_str == '.':
                            continue
                        try:
                            float(val_str)
                        except (ValueError, TypeError):
                            continue
                        formatted.append({"date": item.get('date', '')[:10], "value": val_str})
                results[label] = formatted
            except Exception as e:
                print(f"  ⚠️ 读取市场数据失败 {fname}: {e}")
    return results


def format_market_for_prompt(market_data: dict) -> str:
    """格式化市场数据为prompt文本"""
    if not market_data:
        return ""
    lines = ["## 📊 相关市场数据（最新）"]
    for label, points in market_data.items():
        if not points:
            continue
        latest = points[-1]
        val = latest.get('close', latest.get('value', '?'))
        dt = latest.get('date', '?')
        lines.append(f"- **{label}**: {dt} → {val}")
    return "\n".join(lines)


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

    print(f"  🌐 [5/6] 百度搜索...")
    baidu_results = _search_baidu(queries)
    all_articles.extend(baidu_results)
    if baidu_results:
        print(f"    百度: {len(baidu_results)}条")

    print(f"  🌐 [6/6] 知乎搜索...")
    zhihu_results = _search_zhihu(queries)
    all_articles.extend(zhihu_results)
    if zhihu_results:
        print(f"    知乎: {len(zhihu_results)}条")
    
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
    """时事素材入库到lancedb（不再明文保存）"""
    saved = 0
    
    for topic, articles in affairs.items():
        for a in articles:
            text = a.get('text', '')
            if len(text) < 2000:
                continue
            saved += 1
            
            # 入库到lancedb的evidence表
            try:
                db = lancedb.connect(DB_PATH)
                table = db.open_table("evidence")
                emb = embed(text[:500])
                if emb:
                    import hashlib
                    doc_id = hashlib.md5(text.encode()).hexdigest()[:16]
                    row = {
                        "vector": emb,
                        "document": text[:1000],
                        "person": "时事自动入库",
                        "source": a.get('source', 'unknown'),
                        "title": a['title'],
                        "url": a.get('url', ''),
                        "type": "current_affairs",
                    }
                    table.add([row])
            except Exception as e:
                print(f"  ⚠️ 自动入库失败: {e}")
    
    if saved:
        print(f"  📥 lancedb入库: {saved}条高质量素材")
    return saved


def run_pipeline(question: str, sub_queries: dict = None, auto_save: bool = True, enable_affairs: bool = True):
    """完整Pipeline v5 (框架对抗+条件情景+多源时事+市场数据)"""
    print("=" * 60)
    print("🧠 智库分析Pipeline v6 (理论骨架+量化输出+6章结构)")
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

    # Step 3: 深度RAG（insights + core_profile + evidence）
    print(f"\n🔍 Step 3: 深度RAG检索（含evidence）")
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

    # Step 3.5: 时事自动搜索（6源）
    affairs_data = {}
    if enable_affairs:
        print(f"\n🌐 Step 3.5: 时事自动搜索（多源：Exa+Tavily+SerpAPI+百度+知乎+财联社）")
        affairs_data = search_current_affairs(question, decomp, days=14)
        # 自动入库高质量素材
        auto_ingest_materials(affairs_data, question)
    affairs_text = format_affairs_for_prompt(affairs_data)

    # Step 3.6: 市场数据定向拉取
    market_data = {}
    print(f"\n📊 Step 3.6: 市场数据定向拉取")
    try:
        market_data = _fetch_market_data(question, decomp)
        if market_data:
            print(f"  匹配 {len(market_data)} 个相关指标")
        else:
            print(f"  未匹配到相关市场指标")
    except Exception as e:
        print(f"  ⚠️ 市场数据拉取失败: {e}")
    market_text = format_market_for_prompt(market_data)
    
    # Step 4: 红蓝对抗匹配
    print(f"\n⚔️  Step 4: 红蓝对抗匹配（心理学家独立）")
    selection = red_blue_match(question, rag_data)
    rb_total = sum(len(v) for k, v in selection.items() if k != "心理学家")
    psych_total = len(selection.get("心理学家", []))
    print(f"  红蓝阵营: {rb_total}人 | 心理学家(独立): {psych_total}人")

    # Step 5: 理论骨架提取（v6新增）
    print(f"\n🦴 Step 5: 理论骨架提取")
    theory_skeleton = extract_theory_skeleton(rag_data)
    n_frameworks = len(theory_skeleton.get("scholar_frameworks", []))
    print(f"  提取 {n_frameworks} 位学者的理论框架")

    # Step 6: 构建分段task（v6: 6章 + 预-mortem）
    print(f"\n📝 Step 6: 构建分段报告任务（6章 + 预-mortem偏差审计）")
    chapters, premortem = build_chapter_tasks(question, decomp, events, rag_data, selection, affairs_data, market_data, theory_skeleton)
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
        "rag_col_counts": {"insights": total_insights, "tension": total_tensions, "evidence": sum(len(d.get('evidence',[])) for d in rag_data.values())},
        "red_blue_selection": {k: v for k, v in selection.items()},
        "theory_skeleton": {"frameworks": n_frameworks},
        "chapters": {k: {"title": v["title"], "words": v["words"]} for k, v in chapters.items()},
        "premortem": {"title": premortem["title"], "words": premortem["words"]},
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "version": "v6",
    }
    with open("/tmp/think_tank_v3_framework.json", "w") as f:
        json.dump(framework, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Pipeline就绪: {total_rag}条RAG + {len(events)}个历史事件 + {rb_total}人红蓝对抗 + {psych_total}人心理学家(独立) + {n_frameworks}人理论骨架")
    if auto_save:
        print(f"\n📄 报告将保存到: {REPORT_DIR}/")
    print(f"\n💡 各章节task已保存到 /tmp/{topic_hash}_ch0X_task.txt + _premortem_task.txt")
    print(f"   生成顺序：ch01-ch05并行 → ch00(执行摘要，最后) → premortem审计 → 拼接最终报告")

    return chapters, premortem, framework


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "中国收复台湾的战略窗口期是什么时候？必要条件是什么？"
    chapters, premortem, framework = run_pipeline(q)
