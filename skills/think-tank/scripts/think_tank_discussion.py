#!/home/tearsyu/miniconda3/envs/tt/bin/python3
"""
🏛️ 人格圆桌讨论系统 v2 — 秽土转生版
让154个已入库的人格"活过来"，通过统一的认知引擎驱动差异化的思维推演。

核心改造(v1→v2):
 - 不再仅从claims抽取观点，而是加载完整的13维思维档案(cognitive)
 - 注入tension内在矛盾和tracing思想谱系，让发言带有动态张力
 - 发言遵循"思维管线"：认知参数→角度切入→论据支撑→矛盾浮现

Usage:
    python3 think_tank_discussion.py "台湾局势如何？"
    python3 think_tank_discussion.py "议题" --dry-run          # 只看人选+思维档案
    python3 think_tank_discussion.py "议题" --no-news           # 不检索时事
"""

import json, sys, os, time, re, hashlib, requests
from pathlib import Path
from datetime import date
from collections import defaultdict

# ============ 配置 ============
DB_PATH = "/mnt/d/data/think-tank/personalities/_lacandb/lacan_db"
REPORT_DIR = "/mnt/d/data/think-tank/reports"
OLLAMA_URL = "http://172.26.0.1:11434/api/embed"
OLLAMA_MODEL = "bge-m3:latest"

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "848c4ec87cac7776b9404f0af1aeb36fce936d1aa3aebe899664eaa43e67ccc2")

PYTHON = os.path.expanduser("~/miniconda3/envs/tt/bin/python3")

# 心理学家名单（独立审计层）
PSYCHOLOGISTS = {"丹尼尔·卡尼曼", "乔纳森·海特", "欧文·亚隆", "罗伯特·西奥迪尼",
                 "所罗门·阿希", "阿尔伯特·班杜拉", "保罗·艾克曼", "丹·阿瑞利",
                 "古斯塔夫·勒庞", "菲利普·泰特洛克"}

# ============ cognitive 13维 → 自然语言指令 翻译表 ============
COG_DIM_TRANSLATIONS = {
    "thinking_style": "你的思维风格",
    "reasoning": "你的推理方式",
    "bias": "你容易出现的认知偏差",
    "blindspot": "你的认知盲区",
    "strength": "你的核心优势（分析时擅长的方面）",
    "decision_risk_tolerance": "你的决策-风险偏好",
    "decision_information_processing": "你的决策-信息处理方式",
    "decision_temporal_orientation": "你的决策-时间取向",
    "decision_conflict_resolution": "你的决策-冲突解决倾向",
    "framework": "你常用的分析框架",
    "comm_style": "你的沟通风格",
    "persuasion": "你的说服策略",
    "aphorism": "你的标志性话语",
}

# 关键指令维度（发言质量影响最大的）
HIGH_IMPACT_DIMS = {
    "reasoning", "thinking_style", "decision_risk_tolerance",
    "decision_conflict_resolution", "comm_style"
}


# ============ 工具函数 ============

def embed(query):
    """BGE-M3 embedding"""
    for attempt in range(3):
        try:
            r = requests.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "input": [query]}, timeout=60)
            return r.json()["embeddings"][0]
        except Exception:
            if attempt < 2:
                time.sleep(1)
    return None


def load_db():
    import lancedb
    return lancedb.connect(DB_PATH)


def search_news_cache(sub_queries: list) -> list:
    """从Redis缓存检索相关时事"""
    try:
        import redis
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        all_keywords = set()
        for q in sub_queries:
            for w in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]+", q):
                all_keywords.add(w)
        seen = {}
        for kw in list(all_keywords)[:10]:
            tag_key = f"cls:tag:{kw}"
            members = r.smembers(tag_key)
            for md5key in members:
                if md5key not in seen:
                    item = r.hget("cls:all", md5key)
                    if item:
                        seen[md5key] = json.loads(item)
        articles = sorted(seen.values(), key=lambda x: x.get("pub_time", ""), reverse=True)
        seen_titles = set()
        unique = []
        for a in articles:
            t = a.get("title", "")[:40]
            if t not in seen_titles:
                seen_titles.add(t)
                unique.append(a)
        print(f"  📰 时事缓存命中: {len(unique)}条")
        return unique[:10]
    except Exception as e:
        print(f"  ⚠️ Redis缓存失败: {e}")
        return []


def search_web(query: str, max_results: int = 5) -> list:
    """SerpAPI网络搜索用作时事备用"""
    try:
        params = {"engine": "google", "q": query, "api_key": SERPAPI_KEY, "num": max_results, "tbm": "nws", "hl": "zh-cn"}
        r = requests.get("https://serpapi.com/search", params=params, timeout=15)
        data = r.json()
        return [{
            "title": item.get("title", ""), "snippet": item.get("snippet", ""),
            "source": item.get("source", ""), "date": item.get("date", ""),
        } for item in data.get("news_results", [])[:max_results]]
    except Exception as e:
        print(f"  ⚠️ SerpAPI失败: {e}")
        return []


# ============ Step 1A: 加载完整思维档案（秽土转生核心） ============

def load_mind_profile(person_name: str) -> dict:
    """
    从 cognitive / tension / tracing 三个表加载人格的完整思维档案。
    返回: {
        "cognitive": {dim: text, ...},  # 13维参数
        "tensions": [str],               # 内在矛盾/核心争议
        "tracing": str,                  # 思想谱系
    }
    """
    db = load_db()
    profile = {"cognitive": {}, "tensions": [], "tracing": ""}

    # 1. cognitive: 13维思维参数
    try:
        t = db.open_table("cognitive").to_pandas()
        rows = t[t["person"] == person_name]
        for _, r in rows.iterrows():
            dim = r.get("dim", "")
            text = r.get("text", "")
            if dim and text:
                profile["cognitive"][dim] = text
    except Exception as e:
        print(f"    ⚠️ 加载cognitive失败: {e}")

    # 2. tension: 内在矛盾 + 核心争议
    try:
        t2 = db.open_table("tension").to_pandas()
        rows2 = t2[t2["person"] == person_name]
        for _, r in rows2.iterrows():
            text = r.get("text", "")
            if text:
                profile["tensions"].append(text)
    except Exception as e:
        print(f"    ⚠️ 加载tension失败: {e}")

    # 3. tracing: 思想谱系
    try:
        t3 = db.open_table("tracing").to_pandas()
        rows3 = t3[t3["person"] == person_name]
        if len(rows3) > 0:
            profile["tracing"] = rows3.iloc[0].get("text", "")
    except Exception:
        pass

    return profile


def mind_profile_to_instructions(profile: dict, person_name: str) -> str:
    """
    将思维档案翻译为发言风格指令。
    不把13个维度全塞进prompt，而是转化为对LLM的行为约束。
    """
    cog = profile.get("cognitive", {})
    tensions = profile.get("tensions", [])
    tracing = profile.get("tracing", "")

    parts = []

    # 核心驱动力（从tracing提炼）
    if tracing:
        parts.append(f"【思想根基】{tracing[:300]}")

    # 思维风格+推理方式（最重要的两个维度）
    style = cog.get("thinking_style", "")
    reas = cog.get("reasoning", "")
    if style:
        parts.append(f"【思维风格】{style[:200]}")
    if reas:
        parts.append(f"【推理方式】{reas[:200]}")

    # 风险偏好（影响发言的激进/保守倾向）
    risk = cog.get("decision_risk_tolerance", "")
    if risk:
        short_risk = re.sub(rf"\[{re.escape(person_name)}]\s*", "", risk).strip()
        parts.append(f"【风险倾向】{short_risk[:150]}")

    # 信息处理方式（影响引用什么类型的论据）
    info = cog.get("decision_information_processing", "")
    if info:
        short_info = re.sub(rf"\[{re.escape(person_name)}]\s*", "", info).strip()
        parts.append(f"【论据偏好】{short_info[:150]}")

    # 冲突解决倾向（影响在交锋中的姿态）
    conflict = cog.get("decision_conflict_resolution", "")
    if conflict:
        parts.append(f"【交锋姿态】{conflict[:150]}")

    # 沟通风格
    comm = cog.get("comm_style", "")
    if comm:
        parts.append(f"【表达风格】{comm[:200]}")

    # 认知偏差和盲区（让发言带"人性"）
    biases = []
    for dim in ["bias", "blindspot"]:
        vals = [v for k, v in cog.items() if k == dim]
        for v in vals[:2]:
            short = re.sub(rf"\[{re.escape(person_name)}]\s*", "", v).strip()
            biases.append(short)
    if biases:
        parts.append(f"【认知倾向】你的分析容易{'；'.join(biases[:2])}")

    # 张力：内在矛盾（让发言不是机器人式的一贯，而是有挣扎感）
    tension_texts = []
    for t in tensions:
        if "张力" in t or "矛盾" in t or "争议" in t:
            tension_texts.append(t[:200])
    if tension_texts:
        parts.append(f"【内在张力】{' '.join(tension_texts[:2])}")

    # 格言（标志性语句）
    aphorisms = cog.get("aphorism", "")
    if aphorisms:
        parts.append(f"【标志性话语】{aphorisms[:200]}")

    return "\n".join(parts)


# ============ Step 1B: 智能人选（保留v1逻辑，新增思维档案加载） ============

def select_participants(question) -> tuple:
    """
    向量检索 + 多样性排序选出参与者。
    返回: (participants, psychologists)
      participants: [{"name", "domain", "profile", "claims", "evidence", "cognitive", "mind_instructions", "tensions", "tracing"}, ...]
    """
    db = load_db()
    emb = embed(question)
    if emb is None:
        print("  ⚠️ embedding失败")
        return [], []

    collections = ["personas", "claims", "cognitive"]
    raw = {}

    for col_name in collections:
        try:
            table = db.open_table(col_name)
            results = table.search(emb).limit(30).to_list()
            for row in results:
                person = row.get("person", "")
                if not person:
                    continue
                text = row.get("text", row.get("document", ""))
                domain = row.get("domain", row.get("subdomain", ""))
                score = row.get("_distance", 0.5)
                if person not in raw:
                    raw[person] = {"claims": [], "evidence": [], "cognitive": [], "scores": []}
                if col_name == "personas":
                    raw[person]["domain"] = domain
                    raw[person]["profile"] = text[:800]
                elif col_name == "claims":
                    raw[person]["claims"].append(text)
                    if not raw[person].get("domain"):
                        d = row.get("dim", "") or row.get("category", "")
                        if d:
                            raw[person]["domain"] = d
                elif col_name == "cognitive":
                    raw[person]["cognitive"].append(text)
                raw[person]["scores"].append(1 - score)
        except Exception as e:
            print(f"  ⚠️ 检索{col_name}失败: {e}")

    try:
        table = db.open_table("evidence")
        ev_results = table.search(emb).limit(20).to_list()
        for row in ev_results:
            person = row.get("person", "")
            text = row.get("text", "")
            if person and person in raw:
                raw[person]["evidence"].append(text[:500])
                raw[person]["scores"].append(0.3)
    except Exception:
        pass

    if not raw:
        return [], []

    scored = []
    for person, data in raw.items():
        avg_score = sum(data["scores"]) / max(len(data["scores"]), 1)
        scored.append((avg_score, person, data))
    scored.sort(key=lambda x: x[0], reverse=True)

    domain_groups = defaultdict(list)
    for score, person, data in scored:
        domain = data.get("domain", "未知") or "未知"
        if person in PSYCHOLOGISTS:
            continue
        domain_groups[domain].append((score, person, data))

    selected = []
    selected_names = set()
    domain_best = {}
    for domain, candidates in domain_groups.items():
        candidates.sort(key=lambda x: x[0], reverse=True)
        domain_best[domain] = candidates

    for domain, candidates in domain_best.items():
        if candidates and len(selected) < 8:
            selected.append(candidates[0])
            selected_names.add(candidates[0][1])

    if len(selected) < 8:
        for domain, candidates in domain_best.items():
            for c in candidates[1:]:
                if c[1] in selected_names:
                    continue
                if len(selected) >= 8:
                    break
                selected.append(c)
                selected_names.add(c[1])

    if len(selected) < 5:
        for score, person, data in scored:
            if person not in selected_names and person not in PSYCHOLOGISTS:
                if not data.get("claims") and not data.get("evidence"):
                    continue
                selected.append((score, person, data))
                selected_names.add(person)
                if len(selected) >= 8:
                    break

    # 加载思维档案
    participants = []
    for score, person, data in selected:
        domain = data.get("domain", "未知") or "未知"
        if not domain or domain == "未知":
            if data.get("claims"):
                for c in data["claims"]:
                    if "台湾" in c: domain = "台湾问题"; break
                    if "中美" in c: domain = "中美关系"; break
            if domain == "未知" and data.get("cognitive"):
                domain = data["cognitive"][0][:20]

        mind = load_mind_profile(person)
        instructions = mind_profile_to_instructions(mind, person)

        p = {
            "name": person,
            "domain": domain,
            "profile": (data.get("profile") or "")[:600],
            "claims": data.get("claims", [])[:8],
            "evidence": data.get("evidence", [])[:5],
            "cognitive": data.get("cognitive", [])[:5],
            "mind_instructions": instructions,
            "tensions": mind.get("tensions", [])[:3],
            "tracing": mind.get("tracing", ""),
        }
        participants.append(p)

    # 心理学家
    psychologists = []
    for score, person, data in scored:
        if person in PSYCHOLOGISTS:
            mind = load_mind_profile(person)
            instructions = mind_profile_to_instructions(mind, person)
            psychologists.append({
                "name": person, "domain": "心理学",
                "profile": (data.get("profile") or "")[:600],
                "mind_instructions": instructions,
                "tensions": mind.get("tensions", [])[:2],
            })
            if len(psychologists) >= 2:
                break

    print(f"  👥 选中参与者: {len(participants)}人 | 覆盖: {len(set(p['domain'] for p in participants))}个领域")
    for p in participants:
        print(f"    {p['name']:20s} ← {p['domain']}")
        if p["mind_instructions"]:
            has_cog = sum(1 for k in ["思维风格", "推理方式"] if k in p["mind_instructions"])
            has_tension = "内在张力" in p["mind_instructions"]
            has_bias = "认知倾向" in p["mind_instructions"]
            tags = []
            if has_cog: tags.append("🧠思维")
            if has_tension: tags.append("⚡张力")
            if has_bias: tags.append("👁偏差")
            if tags: print(f"    {'':20s} 已加载: {' '.join(tags)}")

    if psychologists:
        print(f"  🧠 心理学家: {', '.join(p['name'] for p in psychologists)}")
    return participants, psychologists


# ============ Step 2: 构建讨论Prompt（秽土转生版） ============

def build_discussion_prompt(question, participants, psychologists, news=None):
    """构建圆桌讨论prompt。
    关键改动(v2)：注入cognitive思维档案 + tension内在矛盾作为发言约束。
    """
    lines = ["# 指令", "", f"你是一个智库圆桌讨论的主持人。你需要模拟一场关于以下议题的多角色圆桌讨论。", ""]
    lines.append(f"## 议题")
    lines.append(question)
    lines.append("")

    # 时事背景
    if news:
        lines.append("## 时事背景")
        for a in news[:5]:
            lines.append(f"- [{a.get('date','')}] {a.get('title','')}")
            if a.get("snippet"):
                lines.append(f"  {a['snippet'][:200]}")
        lines.append("")

    # ⭐ 参与者（思维档案版）
    lines.append("## 参与者")
    lines.append("")
    for i, p in enumerate(participants):
        lines.append(f"### {i+1}. {p['name']}（{p['domain']}）")
        if p.get("profile"):
            lines.append(f"背景: {p['profile']}")
        # 思维指令（核心改造）
        if p.get("mind_instructions"):
            lines.append(p["mind_instructions"])
        # 核心观点（作为论据库）
        if p.get("claims"):
            lines.append("可参考的核心观点：")
            for c in p["claims"][:5]:
                lines.append(f"- {c[:240]}")
        lines.append("")

    # 心理学家（如果存在）
    if psychologists:
        lines.append("## 心理学家（独立偏差审计层）")
        for p in psychologists:
            lines.append(f"### {p['name']}")
            lines.append(p.get("mind_instructions", ""))
            lines.append("")

    # 产出格式要求
    lines.append("## 发言规则（秽土转生原则）")
    lines.append("")
    lines.append("1. **统一认知引擎，参数化差异** — 所有参与者共享相同的人类思维底层架构（感知→判断→回应），但每个人的思维风格、推理方式、风险偏好、内在矛盾各不相同。这些差异决定了TA的发言角度、论据选择、语气以及情绪倾向。")
    lines.append("2. **思维推演，而非观点抽取** — 每个参与者不是从记忆中'复制粘贴'观点，而是基于TA的思维参数和当下议题实时推理。先确定切入角度，再调用相关论据，最后让内在矛盾在语态中自然浮现。")
    lines.append("3. **张力驱动辩论** — 每个参与者都有内在矛盾或核心争议。当其他参与者的观点触及其矛盾点时，TA的反应会更强烈、更情绪化，甚至出现犹豫或转折。这是'活人'与'机器人'的区别。")
    lines.append("4. **发言长度** — 第一轮立场声明300-500字，第二轮交锋200-300字。")
    lines.append("")

    lines.append("## 输出格式")
    lines.append("")
    lines.append("```markdown")
    lines.append(f"# 🏛️ 圆桌讨论：{question[:60]}")
    lines.append(f"")
    lines.append(f"**参与者**: {', '.join(p['name'] for p in participants)}")
    lines.append(f"")
    lines.append("## 第一轮：各自立场")
    lines.append("")
    lines.append("### {{姓名}}")
    lines.append("> {{基于其思维档案和内在矛盾的立场声明}}")
    lines.append("")
    lines.append("**核心论据：**")
    lines.append("- {{论据}}")
    lines.append("- {{论据}}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("（重复每个参与者）")
    lines.append("")
    lines.append("## 第二轮：观点交锋")
    lines.append("")
    lines.append("### {{姓名A}} 回应 {{姓名B}}")
    lines.append("> {{A对B的回应，注意让A的认知偏差、风险偏好、内在矛盾在回应中自然浮现}}")
    lines.append("")
    lines.append("### {{姓名C}} 回应 {{姓名D}}")
    lines.append("> ...")
    lines.append("")
    lines.append("（每个参与者至少回应1人，最多2人。回应不是复读自己的立场，而是针对性互动。）")
    lines.append("")
    lines.append("## 第三轮：分析与总结")
    lines.append("")
    lines.append("### 共识点")
    lines.append("1. ...")
    lines.append("")
    lines.append("### 分歧点")
    lines.append("1. ...")
    lines.append("")
    lines.append("### 关键不确定性")
    lines.append("1. ...")
    if psychologists:
        lines.append("")
        lines.append("### 🧠 心理学家观察")
        lines.append("1. {{识别出的认知偏差}}")
        lines.append("2. {{群体思维风险评估}}")
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


# ============ Step 3: 主流程 ============

def run_discussion(question, dry_run=False, no_news=False):
    """运行圆桌讨论的主流程"""

    print(f"\n{'='*50}")
    print(f"  🏛️ 秽土转生圆桌: {question[:60]}")
    print(f"{'='*50}")

    # Step 1: 智能人选 + 思维档案加载
    print(f"\n📌 Step 1: 选择参与者并加载思维档案...")
    participants, psychologists = select_participants(question)
    if not participants:
        print("  ❌ 未找到合适的参与者")
        return

    # Step 2: 时事
    news = []
    if not no_news:
        print(f"\n📰 检索时事...")
        sub_queries = [question]
        for p in participants:
            sub_queries.append(f"{question} {p['domain']}")
        news = search_news_cache(sub_queries)
        if len(news) < 3:
            web_news = search_web(question)
            news.extend(web_news)
        print(f"  📰 总计 {len(news)} 条时事")

    if dry_run:
        print(f"\n📋 Dry Run: participant思维档案")
        for p in participants:
            print(f"\n--- {p['name']} ({p['domain']}) ---")
            if p.get("mind_instructions"):
                print(f"  {p['mind_instructions'][:500]}")
                print(f"  ...（共{len(p['mind_instructions'])}字符）")
            else:
                print("  ⚠️ 无思维档案")
            if p.get("tensions"):
                print(f"  ⚡ 内在张力: {p['tensions'][0][:150]}")
            print(f"  💬 claims: {len(p.get('claims',[]))}条 | evidence: {len(p.get('evidence',[]))}条")
        return

    # Step 3: 构建prompt
    print(f"\n🎯 Step 3: 生成圆桌讨论...")
    prompt = build_discussion_prompt(question, participants, psychologists, news)
    print(f"  📝 Prompt: {len(prompt)}字符 | {len(participants)}参与者 + {len(psychologists)}心理学家")

    # 输出prompt + 写入占位文件
    print(f"\n{'='*50}")
    prompt_marker = "=== PROMPT_START ==="
    print(prompt_marker)
    print(prompt)
    print("=== PROMPT_END ===")
    print(f"{'='*50}")

    today = date.today().isoformat()
    safe_topic = re.sub(r"[^\w\u4e00-\u9fff]", "_", question[:20])
    filename = f"圆桌讨论_{safe_topic}_{today}.md"
    filepath = Path(REPORT_DIR) / filename

    Path(REPORT_DIR).mkdir(parents=True, exist_ok=True)
    Path(filepath).write_text(f"""# 🏛️ 圆桌讨论：{question}

> **状态**: 待生成 | **日期**: {today}
> **参与者**: {len(participants)}人 | **覆盖领域**: {len(set(p['domain'] for p in participants))}个
> **模式**: 秽土转生v2（含13维思维档案 + 内在张力）

## 参与者
{chr(10).join([f"- **{p['name']}**（{p['domain']}）" + (" ⚡有内在张力" if p.get("tensions") else "") for p in participants])}
{chr(10).join([f"- 🧠 **{p['name']}**（心理学家）" for p in psychologists]) if psychologists else ""}

## 时事检索
{chr(10).join([f"- [{a.get('date','')}] {a.get('title','')}" for a in (news or [])[:5]]) if news else "无"}

---

*思维Prompt已生成（{len(prompt)}字符），请由当前session生成讨论内容*
""", encoding="utf-8")

    print(f"\n  📄 占位文件: {filepath}")
    print(f"  💡 提示: 将以上Prompt发送给GLM或直接由当前模型生成讨论内容\n")
    return prompt, filepath


# ============ 命令行入口 ============

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="🏛️ 秽土转生圆桌 v2")
    parser.add_argument("question", nargs="*", help="讨论议题")
    parser.add_argument("--dry-run", action="store_true", help="只显示人选和思维档案")
    parser.add_argument("--no-news", action="store_true", help="不检索时事")

    args = parser.parse_args()

    if not args.question:
        question = "中国当前面临的主要战略挑战是什么？应该如何应对？"
        print(f"  未指定议题，使用默认: {question}")
    else:
        question = " ".join(args.question)

    run_discussion(question, dry_run=args.dry_run, no_news=args.no_news)
