#!/usr/bin/env python3
"""
智库分析Pipeline v2
问题拆解 → 历史参照(Redis) → 深度RAG(子议题拆分) → 子session报告生成 → D盘保存

改进点(v1→v2):
- RAG按子议题拆分搜索，每人每议题取多条
- 概率自动归一化
- 内置红队反驳机制
- 报告自动保存到D盘

Usage:
    python think_tank_pipeline_v2.py "美国出兵伊朗前会在亚洲哪个方向挑起紧张"
"""

import json, sys, os, subprocess, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.historical_context import search_historical_events, get_redis_status
import chromadb, requests

DB_PATH = "/mnt/d/data/think-tank-data/_chromadb/lacan_db"
OLLAMA_URL = 'http://172.26.0.1:11434/api/embed'
OLLAMA_MODEL = 'bge-m3:latest'
REPORT_DIR = "/mnt/d/data/think-tank-data/reports"


def embed(query):
    for attempt in range(3):
        try:
            r = requests.post(OLLAMA_URL, json={'model': OLLAMA_MODEL, 'input': [query]}, timeout=60)
            return r.json()['embeddings'][0]
        except:
            continue
    return None


def decompose_question(question: str) -> dict:
    """问题拆解：识别维度、资产、子议题"""
    DIMENSION_RULES = {
        "地缘冲突": {
            "keywords": ["战争", "军事", "冲突", "制裁", "封锁", "入侵", "打击", "攻击", "袭击"],
            "sub_dimensions": ["冲突模式", "地理范围", "持续时间", "升级路径", "核风险"],
        },
        "能源": {
            "keywords": ["石油", "原油", "天然气", "LNG", "油", "气", "能源"],
            "sub_dimensions": ["供应中断", "替代供应", "储备释放", "运输通道", "产能弹性"],
        },
        "金融": {
            "keywords": ["价格", "汇率", "利率", "美元", "通胀", "金价", "股", "债"],
            "sub_dimensions": ["避险资产", "货币流向", "利率环境", "通胀预期", "风险溢价"],
        },
        "政治": {
            "keywords": ["制裁", "禁运", "结盟", "联合国", "北约", "谈判"],
            "sub_dimensions": ["国际联盟", "制裁力度", "外交空间", "国内政治"],
        },
        "贸易": {
            "keywords": ["贸易", "关税", "出口", "进口", "供应链"],
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
    }

    question_lower = question.lower()
    triggered = []
    for dim_name, rules in DIMENSION_RULES.items():
        for kw in rules["keywords"]:
            if kw.lower() in question_lower:
                triggered.append(dim_name)
                break
    if not triggered:
        triggered = ["地缘冲突", "金融"]

    relevant_assets = set()
    for ak, assets in ASSET_MAP.items():
        if ak in question:
            relevant_assets.update(assets)
    if "能源" in triggered:
        relevant_assets.update(["brent_crude", "WTI"])
    if "地缘冲突" in triggered:
        relevant_assets.update(["gold_xau", "usd_index"])
    if "粮食" in triggered:
        relevant_assets.update(["wheat", "corn"])

    return {
        "original_question": question,
        "triggered_dimensions": triggered,
        "sub_dimensions": {d: DIMENSION_RULES[d]["sub_dimensions"] for d in triggered if d in DIMENSION_RULES},
        "relevant_assets": list(relevant_assets),
    }


def deep_rag_search(question: str, sub_queries: dict = None, n_per_col: int = 6) -> dict:
    """
    深度RAG搜索：
    - 如果提供sub_queries，按子议题分别搜索
    - 每个collection取n_per_col条
    - 按人去重
    """
    client = chromadb.PersistentClient(path=DB_PATH)
    results = {}

    queries = sub_queries if sub_queries else {"default": question}

    for topic, query in queries.items():
        emb = embed(query)
        if emb is None:
            continue
        topic_results = []
        seen = set()
        for col_name in ["insights", "core_profile"]:
            try:
                col = client.get_collection(col_name)
                if col.count() == 0: continue
                res = col.query(query_embeddings=[emb], n_results=min(n_per_col, col.count()))
                for i, doc in enumerate(res["documents"][0]):
                    meta = res["metadatas"][0][i]
                    person = meta.get("person", meta.get("personality_name", "?"))
                    key = f"{person}|{col_name}"
                    if key in seen: continue
                    seen.add(key)
                    topic_results.append({
                        "person": person,
                        "col": col_name,
                        "text": doc,
                        "dist": res["distances"][0][i],
                    })
            except Exception as e:
                print(f"  [{topic}] {col_name} error: {e}")

        results[topic] = sorted(topic_results, key=lambda x: x["dist"])

    return results


def match_historical_events(question: str, dimensions: list) -> list:
    TAG_MAP = {
        "地缘冲突": ["中东战争", "军事冲突", "封锁", "制裁", "北约", "核威慑"],
        "能源": ["石油", "油价", "油价冲击", "天然气", "霍尔木兹海峡", "能源危机", "能源武器化"],
        "金融": ["美元", "黄金", "滞胀", "避险"],
        "政治": ["制裁", "禁运", "联盟战争", "联合国授权"],
        "贸易": ["海上通道", "运河封锁", "供应链"],
        "粮食": ["粮食危机", "小麦"],
    }
    tags = set()
    for dim in dimensions:
        tags.update(TAG_MAP.get(dim, []))
    return search_historical_events(question, list(tags))


def format_rag_for_prompt(rag_data: dict) -> str:
    """格式化RAG数据为prompt文本"""
    sections = []
    for topic, items in rag_data.items():
        lines = [f"**{topic}:**"]
        seen = set()
        for item in items[:8]:  # 每个topic最多8条
            person = item["person"]
            if person in seen: continue
            seen.add(person)
            text = item["text"][:300].replace("\n", " ")
            lines.append(f"- [RAG: {person}] {text}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def format_historical_for_prompt(events: list) -> str:
    """格式化历史事件为prompt文本"""
    lines = []
    for ev in events:
        impacts = ""
        for asset, imp in ev.get("market_impacts", {}).items():
            impacts += f"{asset}: pre={imp.get('pre_event','?')}→peak={imp.get('peak','?')}→post={imp.get('post_event_6m','?')} {imp.get('unit','')} | "
        lessons = "; ".join(ev.get("lessons", [])[:3])
        lines.append(f"- **{ev['name']}**({ev['date_range']}): {ev['description'][:100]}... | 市场: {impacts} | 教训: {lessons}")
    return "\n".join(lines)


def build_report_task(question: str, decomp: dict, events: list, rag_data: dict) -> str:
    """构建报告生成的task prompt（学术化，避免GLM 400）"""
    rag_text = format_rag_for_prompt(rag_data)
    hist_text = format_historical_for_prompt(events)

    task = f"""你是一个学术战略研究分析引擎。请对以下议题进行深度多视角分析，以学术论文和政策研究报告的语调撰写。

## 核心问题
{question}

## 触发维度：{', '.join(decomp['triggered_dimensions'])}
## 相关资产：{', '.join(decomp['relevant_assets'])}

## 要求
1. 使用RAG数据中的具体观点，每个判断标注[RAG: 人格名]
2. 心理学家分析要深刻具体（认知偏差、群体决策、道德脱离、社会认同等）
3. 历史类比严谨
4. 所有情景概率必须归一化（总和=100%）
5. 中文，6000-8000字
6. 结构：问题解构→历史参照矩阵→候选方案深度分析（含心理画像+集体决策偏差）→跨学科交叉验证→情景推演与投资影响→自优化建议

## RAG参考数据
{rag_text}

## 历史参照
{hist_text}"""

    return task


def run_pipeline(question: str, sub_queries: dict = None, auto_save: bool = True):
    """完整Pipeline"""
    print("=" * 60)
    print("🧠 智库分析Pipeline v2")
    print("=" * 60)

    # Redis
    status = get_redis_status()
    print(f"\n📊 Redis: {status['connected']} | 历史事件: {status['historical_events']}条")

    # Step 1: 问题拆解
    print(f"\n📋 Step 1: 问题拆解")
    decomp = decompose_question(question)
    print(f"  维度: {decomp['triggered_dimensions']}")
    print(f"  资产: {decomp['relevant_assets']}")

    # Step 2: 历史参照
    print(f"\n📚 Step 2: 历史参照")
    events = match_historical_events(question, decomp["triggered_dimensions"])
    print(f"  匹配 {len(events)} 个历史事件")

    # Step 3: 深度RAG
    print(f"\n🔍 Step 3: 深度RAG检索")
    if sub_queries:
        print(f"  子议题: {len(sub_queries)}个")
    else:
        # 自动生成子议题：按维度拆分
        sub_queries = {}
        for dim in decomp["triggered_dimensions"]:
            sub_queries[f"{dim}_学者军事"] = f"{question} {dim} 战略分析 国际关系"
            sub_queries[f"{dim}_心理"] = f"{question} 决策偏差 群体心理 风险感知 道德脱离"
    rag_data = deep_rag_search(question, sub_queries, n_per_col=6)
    total_rag = sum(len(v) for v in rag_data.values())
    print(f"  总计: {total_rag}条RAG结果")

    # Step 4: 构建task
    task = build_report_task(question, decomp, events, rag_data)

    # 保存框架JSON
    framework = {
        "question": question,
        "decomposition": decomp,
        "historical_events": len(events),
        "rag_total": total_rag,
        "rag_topics": list(rag_data.keys()),
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    os.makedirs("/tmp", exist_ok=True)
    with open("/tmp/think_tank_v2_framework.json", "w") as f:
        json.dump(framework, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 框架就绪: {total_rag}条RAG + {len(events)}个历史事件")
    print(f"  Task长度: {len(task)}字符")

    if auto_save:
        print(f"\n📄 报告将保存到: {REPORT_DIR}/")

    return task, framework


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "美国出兵伊朗前会在亚洲哪个方向挑起紧张"

    # 自定义子议题（按候选方向拆分）
    custom_queries = {
        "台湾_战略": "台湾海峡 中国核心利益 红线 美国干预 半导体",
        "台湾_心理": "台海 决策偏差 道德脱离 群体极化 损失厌恶",
        "日本_同盟": "美日同盟 和平宪法 民众反战 东海",
        "日本_历史": "日本 战争历史 资源困境 山本五十六",
        "印度_边境": "中印边境 实控线 有限战争 高原",
        "印度_合作": "美印合作 莫迪 产业替代 Make in India",
        "伊朗_战略": "美国 中东 军事行动 代理人",
        "心理_群体决策": "群体决策 从众效应 集体思维 华盛顿",
        "心理_认知偏差": "认知偏差 风险感知 支持信心 预期影响",
        "心理_权力道德": "权力 攻击性 道德脱离 权威服从 平庸之恶",
        "心理_身份创伤": "民族主义 身份认同 历史创伤 集体记忆",
    }

    task, framework = run_pipeline(q, sub_queries=custom_queries)
    print(f"\n💡 Task已生成。请用sessions_spawn提交给子session处理。")
