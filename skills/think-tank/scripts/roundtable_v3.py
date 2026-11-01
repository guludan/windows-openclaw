#!/usr/bin/env python3
"""
🏛️ 智库圆桌会议 v3.2 — 完整结构化产出（去口水版）

v3.2 改动（去口水 + 分工，2026-10-14）:
  1. 两位心理审计**分工**：卡尼曼=个体认知偏差，勒庞=群体动力学，禁止重复逐人诊断
  2. **统一去概率数字**：学者prompt/综合prompt/决策树全部改为条件/触发信号表达
     （与 SKILL.md v5 “去掉概率数字”对齐；旧版脚本仍要“30%概率”是文档与实现脱节）
  3. 学者prompt从7节压缩为5节，删掉“跟其他学派对话”节（报告聚合层已做），要求1200-1800字
  4. 附录不再堆叠发言全文，只保留“核心判断+条件链推演”节选
  5. 报告正文目标 2500-4000 字（原 4000-6000），明确“不在正文重复学者发言”

v3.1 遗留（保留）:
  1. 报告为结构化产出（核心判断→交锋→假设→情景→偏差→决策树）
  2. 集成debate_engine对抗机制、DEBATE_CAMPS阵营预设
  3. 心理学家审计渗透进“可信度修正”

Usage:
    python3 roundtable_v3.py --prepare "议题描述"
    python3 roundtable_v3.py --prepare "议题" --scholars 6 --no-news
    python3 roundtable_v3.py --report /tmp/rt_scholars/ /tmp/rt_psychs/
"""

import json, sys, os, re, hashlib
from pathlib import Path
from datetime import date, datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============ 配置 ============
WORKSPACE = os.path.expanduser("~/.openclaw/workspace")
PERSONALITIES_DIR = "/mnt/d/data/think-tank/personalities"
REPORT_DIR = "/mnt/d/data/a-stock/reports"
EXA_API_KEY = "7be7a599-fa7b-4107-9ef1-ad96646156c2"
SERPAPI_KEY = "848c4ec87cac7776b9404f0af1aeb36fce936d1aa3aebe899664eaa43e67ccc2"

SUB_MODEL = "deepseek/deepseek-chat"
SUB_TIMEOUT = 300
MAX_RETRIES = 2

# ============ 中英双语桥梁 ============
ZH_EN_BRIDGE = {
    "恐慌": ["panic", "fear", "crash", "contagion", "flight to safety"],
    "危机": ["crisis", "recession", "depression", "systemic"],
    "崩盘": ["crash", "collapse", "meltdown", "bear market"],
    "衰退": ["recession", "contraction", "downturn"],
    "房地产": ["real estate", "housing", "property", "mortgage", "land"],
    "楼市": ["housing market", "property", "real estate"],
    "股市": ["stock", "equity", "market", "share"],
    "A股": ["china stock", "a-share", "chinese market"],
    "利率": ["interest rate", "monetary policy", "federal reserve"],
    "通胀": ["inflation", "consumer price", "cpi"],
    "通缩": ["deflation", "disinflation"],
    "货币": ["currency", "monetary", "money supply", "fiat"],
    "债务": ["debt", "leverage", "borrowing", "sovereign"],
    "杠杆": ["leverage", "margin", "gearing"],
    "流动性": ["liquidity", "cash flow", "funding"],
    "银行": ["bank", "banking", "financial institution"],
    "贸易战": ["trade war", "tariff", "protectionism", "sanctions"],
    "脱钩": ["decoupling", "deglobalization", "supply chain"],
    "制裁": ["sanctions", "embargo", "economic warfare"],
    "人口": ["demographics", "population", "aging", "fertility"],
    "老龄化": ["aging", "demographic decline", "old age"],
    "战争": ["war", "conflict", "military", "invasion"],
    "地缘": ["geopolitics", "geography", "territorial"],
    "台湾": ["taiwan", "strait", "cross-strait"],
    "南海": ["south china sea", "maritime dispute"],
    "AI": ["artificial intelligence", "ai", "machine learning", "automation"],
    "芯片": ["semiconductor", "chip", "microchip", "foundry"],
    "半导体": ["semiconductor", "chip", "supply chain"],
    "稀土": ["rare earth", "mineral", "mining"],
    "能源": ["energy", "oil", "gas", "petroleum"],
    "新能源": ["renewable energy", "solar", "wind", "ev", "battery"],
    "泡沫": ["bubble", "speculation", "mania"],
    "黑天鹅": ["black swan", "tail risk", "extreme event"],
    "尾部风险": ["tail risk", "extreme", "kurtosis", "fat tail"],
    "量化": ["quantitative", "algorithm", "systematic trading"],
    "因子": ["factor", "smart beta", "risk premium"],
    "消费": ["consumption", "consumer", "retail"],
    "投资": ["investment", "capital allocation", "portfolio"],
    "失业": ["unemployment", "job loss", "labor market"],
    "财政": ["fiscal", "government spending", "deficit"],
    "汇率": ["exchange rate", "currency", "forex"],
    "美元": ["dollar", "usd", "reserve currency"],
    "人民币": ["rmb", "yuan", "renminbi"],
    "黄金": ["gold", "precious metal", "safe haven"],
    "比特币": ["bitcoin", "crypto", "digital currency"],
    "供应链": ["supply chain", "manufacturing", "logistics"],
    "数字化转型": ["digital transformation", "technology adoption"],
    "碳中和": ["carbon neutral", "climate", "emission"],
}

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
    "人口|老龄化|少子化|劳动力": ["Peter_Zeihan", "Carmen_Reinhart"],
    "股市|A股|牛市|熊市|股票|投资": ["Ray_Dalio", "Nassim_Taleb", "Hyman_Minsky"],
    "通胀|通缩|物价|货币|利率": ["Carmen_Reinhart", "Nouriel_Roubini", "Joseph_Stiglitz"],
    "战争|冲突|地缘|台湾|南海|中东": ["Thomas_Schelling", "Peter_Zeihan", "Nouriel_Roubini"],
    "AI|人工智能|科技|芯片|半导体": ["Peter_Zeihan", "Joseph_Stiglitz"],
    "稀土|矿产|资源|能源": ["Peter_Zeihan", "Nouriel_Roubini"],
    "泡沫|黑天鹅|尾部|风险": ["Nassim_Taleb", "Hyman_Minsky", "Ray_Dalio"],
    "量化|因子|策略|回测": ["James_Simons", "Cliff_Asness", "Edward_Thorp"],
    "空仓|入场|底部|抄底|时机": ["Nassim_Taleb", "George_Soros", "Edward_Thorp", "Ray_Dalio"],
}

PSYCHOLOGISTS = [
    # ⚠️ 分工：卡尼曼只做个体认知偏差，勒庞只做群体动力学，不重复
    ("Daniel_Kahneman", "behavioral", "个体认知偏差审计"),
    ("Gustave_LeBon", "social-engineering", "群体动力学监控"),
]

SCHOLAR_DOMAIN = {
    "Ray_Dalio": "economics", "Nouriel_Roubini": "economics",
    "Peter_Zeihan": "geopolitical-finance", "George_Soros": "economics",
    "Niall_Ferguson": "geopolitical-finance",
    "Hyman_Minsky": "economics", "Nassim_Taleb": "economics",
    "David_Einhorn": "economics", "James_Simons": "financial-engineering",
    "Cliff_Asness": "financial-engineering", "Edward_Thorp": "financial-engineering",
    "Carmen_Reinhart": "economics", "Joseph_Stiglitz": "economics",
    "Kenneth_Rogoff": "economics", "Thomas_Piketty": "economics",
    "John_Maynard_Keynes": "economics", "Friedrich_Hayek": "economics",
    "Milton_Friedman": "economics", "Paul_Krugman": "economics",
    "Thomas_Schelling": "game-theory", "John_Nash": "game-theory",
    "Robert_Axelrod": "game-theory",
    "Philip_Tetlock": "political-forecasting", "Nate_Silver": "political-forecasting",
    "Bruce_Bueno_de_Mesquita": "political-forecasting",
    "Ian_Bremmer": "tech-geopolitics", "Henry_Kissinger": "geopolitical-finance",
    "John_Mearsheimer": "geopolitical-finance",
    "Aaron_Brown": "financial-engineering", "Emanuel_Derman": "financial-engineering",
    "Fischer_Black": "financial-engineering", "Myron_Scholes": "financial-engineering",
    "David_Shaw": "financial-engineering",
    "Eugene_Fama": "economics", "Kenneth_French": "economics",
    "Daniel_Kahneman": "behavioral", "Richard_Thaler": "behavioral",
    "Robert_Shiller": "economics",
}

KNOWN_SCHOLARS = {
    "Ray_Dalio": "宏观周期", "Nouriel_Roubini": "崩盘预言", "Peter_Zeihan": "地缘人口",
    "George_Soros": "反身性金融", "Niall_Ferguson": "金融地缘",
    "Hyman_Minsky": "金融不稳定", "Nassim_Taleb": "反脆弱/黑天鹅",
    "David_Einhorn": "做空/价值", "James_Simons": "量化投资",
    "Cliff_Asness": "因子投资", "Edward_Thorp": "对冲/量化",
    "Carmen_Reinhart": "金融危机史", "Joseph_Stiglitz": "信息不对称",
    "Kenneth_Rogoff": "宏观/货币", "Thomas_Piketty": "不平等/财富",
    "Thomas_Schelling": "博弈论/临界点", "John_Nash": "博弈论/均衡",
    "Philip_Tetlock": "政治预测", "Nate_Silver": "数据预测",
    "Ian_Bremmer": "地缘政治风险", "Henry_Kissinger": "大国博弈/外交",
    "John_Mearsheimer": "进攻性现实主义",
    "Aaron_Brown": "风险管理", "Emanuel_Derman": "模型风险",
}

DEBATE_CAMPS = {
    "恐慌|危机|崩盘": (["Ray_Dalio", "Hyman_Minsky"], ["Nassim_Taleb", "Peter_Zeihan"], ["Thomas_Schelling"]),
    "衰退|萧条": (["Nouriel_Roubini", "Carmen_Reinhart"], ["Joseph_Stiglitz", "Ray_Dalio"], ["Nassim_Taleb"]),
    "房地产|楼市|房价": (["Ray_Dalio", "Peter_Zeihan"], ["Carmen_Reinhart", "Hyman_Minsky", "Thomas_Schelling"], ["Nouriel_Roubini"]),
    "AI|芯片|半导体|科技": (["Peter_Zeihan"], ["Joseph_Stiglitz", "Ray_Dalio"], ["Thomas_Schelling"]),
    "战争|冲突|台湾|南海": (["John_Mearsheimer", "Peter_Zeihan"], ["Thomas_Schelling", "Ian_Bremmer"], ["Nouriel_Roubini"]),
    "债务|杠杆|泡沫": (["Hyman_Minsky", "Nassim_Taleb"], ["Ray_Dalio", "Carmen_Reinhart"], ["Thomas_Schelling"]),
    "量化|因子|策略|回测": (["James_Simons", "Cliff_Asness"], ["Edward_Thorp", "Nassim_Taleb"], ["Emanuel_Derman"]),
    "空仓|入场|底部|时机": (["Nassim_Taleb", "George_Soros"], ["Edward_Thorp", "Ray_Dalio"], ["Cliff_Asness"]),
}

# ============ 双语insights匹配 ============

def extract_bilingual_keywords(topic):
    zh_keywords, en_keywords = set(), set()
    for zh_term, en_terms in ZH_EN_BRIDGE.items():
        if zh_term in topic:
            zh_keywords.add(zh_term)
            for en in en_terms:
                en_keywords.add(en.lower())
    for word in re.findall(r'[a-zA-Z]{2,}', topic):
        en_keywords.add(word.lower())
    return zh_keywords, en_keywords


def score_insight(insight, zh_kw, en_kw):
    claim = (insight.get("claim") or "").lower()
    dim = (insight.get("dim") or "").lower()
    evidence_text = " ".join([
        (e.get("excerpt") or "").lower()
        for e in insight.get("evidence", [])
    ])
    score = 0.0
    for kw in zh_kw:
        if kw in claim: score += 2.0
        if kw in dim: score += 1.0
    for kw in en_kw:
        if kw in evidence_text: score += 1.5
        if kw in claim: score += 1.0
    score += {"stance": 0.3, "decision": 0.5, "method": 0.2, "tension": 0.4}.get(insight.get("dim", ""), 0)
    return score


def match_insights(personality, topic, top_n=12):
    zh_kw, en_kw = extract_bilingual_keywords(topic)
    insights = personality.get("insights_with_evidence", [])
    if not insights:
        return []
    scored = [(ins, score_insight(ins, zh_kw, en_kw)) for ins in insights]
    scored.sort(key=lambda x: -x[1])
    if scored[0][1] == 0:
        return insights[:top_n]
    return [ins for ins, _ in scored[:top_n]]


def match_debate_camp(topic):
    for pattern, camps in DEBATE_CAMPS.items():
        if re.search(pattern, topic):
            return camps
    return None


# ============ 人格加载 ============

def find_personality_path(name):
    for category in ["scholars", "psychologists"]:
        base = os.path.join(PERSONALITIES_DIR, category)
        if not os.path.isdir(base):
            continue
        for sub in os.listdir(base):
            p = os.path.join(base, sub, name, "personality_v3.json")
            if os.path.exists(p):
                return p
    return None


def load_personality(name):
    p_path = find_personality_path(name)
    if not p_path:
        return None
    with open(p_path) as f:
        return json.load(f)


# ============ 增强版学者Prompt ============

def generate_scholar_prompt(name, personality, topic, news_data, all_scholars=None, camp=None):
    matched = match_insights(personality, topic, top_n=15)
    insights_lines = []
    for i, ins in enumerate(matched, 1):
        claim = ins.get("claim", "")
        dim = ins.get("dim", "")
        ev = ins.get("evidence", [])
        ev_text = ev[0].get("excerpt", "")[:300] if ev else ""
        insights_lines.append(f"### Insight {i} [{dim}]\n**核心观点**: {claim}\n**原文论据**: {ev_text}")

    brief = personality.get("brief", "")
    school = personality.get("school", "")
    sa = personality.get("stance_anchors", {})
    if isinstance(sa, dict):
        core_belief = sa.get("core_belief", "")
        methodology = sa.get("methodology", "")
        key_debates = sa.get("key_debates", [])
    else:
        core_belief = methodology = str(sa)
        key_debates = []

    cp = personality.get("cognitive_patterns", {})
    if isinstance(cp, dict):
        thinking = cp.get("thinking_style", "")
        reasoning = cp.get("reasoning_approach", "")
        blindspots = cp.get("intellectual_blindspots", [])
    else:
        thinking = reasoning = str(cp)
        blindspots = []

    af = personality.get("analytical_frameworks", [])
    frameworks = ""
    if isinstance(af, list) and af:
        frameworks = "\n".join([f"- **{f.get('name','')}**: {', '.join(f.get('components',[]))}" for f in af[:4]])

    news_text = ""
    if news_data:
        # 区分时事和历史案例，给出更丰富的内容
        news_bullets = []
        hist_bullets = []
        for n in news_data[:10]:
            title = n.get('title', '')
            text = n.get('text', '')[:500]  # 从200字提升到500字
            source = n.get('source', '')
            is_history = any(kw in title for kw in ['历史', '复盘', '回顾', 'precedent', 'lesson', '1985', '1930', '1997', '2008', '2018'])
            bullet = f"- **{title}** ({source}): {text}"
            if is_history:
                hist_bullets.append(bullet)
            else:
                news_bullets.append(bullet)

        parts = []
        if news_bullets:
            parts.append("## 📰 最新时事数据\n" + "\n".join(news_bullets))
        if hist_bullets:
            parts.append("## 📚 相关历史案例\n" + "\n".join(hist_bullets))
        news_text = "\n".join(parts)

    rival_section = ""
    if all_scholars and len(all_scholars) > 1:
        others = [s for s in all_scholars if s != name]
        other_labels = [f"{o}({KNOWN_SCHOLARS.get(o, o)})" for o in others[:4]]
        rival_section = f"""
## 🥊 辩论对抗（必须回应）
其他参与者：{', '.join(other_labels)}

请在分析中自然回应以下问题：
1. 你与哪位参与者在核心假设上存在根本分歧？分歧是什么？
2. 他们可能怎么反驳你？你的反制论据是什么？
3. 有没有你认同对方观点的地方？为什么？
4. 什么条件下你会改变立场转向对方？
"""

    camp_text = ""
    if camp == "pro":
        camp_text = "\n> **你的角色：支持/正向视角** — 请从你的框架出发，论证议题中的积极因素或可行性。"
    elif camp == "con":
        camp_text = "\n> **你的角色：质疑/反向视角** — 请从你的框架出发，质疑议题中的假设或论证风险。"
    elif camp == "observer":
        camp_text = "\n> **你的角色：中立观察员** — 评估正反双方观点，给出独立判断。"

    prompt = f"""# 你是 {name}

## 人格档案
- **简介**: {brief}
- **学派**: {school}
- **核心信念**: {core_belief}
- **方法论**: {methodology}
- **主要争论**: {'; '.join(key_debates) if key_debates else 'N/A'}

## 认知特征
- **思维风格**: {thinking}
- **推理方式**: {reasoning}
- **盲区**: {'; '.join(blindspots) if blindspots else '未知'}

## 分析框架
{frameworks if frameworks else 'N/A'}

## 核心Insights（按议题相关性排序）
{chr(10).join(insights_lines) if insights_lines else 'N/A'}
{news_text}
{camp_text}

---

# 议题: {topic}

## 任务

用你的真实人格、分析框架和insights，写一份**深度分析**（2000-3000字）。

**要求:**
1. **条件判断**：用"若X则Y"的因果关系链推进，不要只描述现象
2. **框架驱动**：必须使用你的标志性分析框架，不能泛泛而谈
3. **边界条件**：明确指出你的判断的适用范围（地域、时间尺度、政策假设），以及它在什么条件下会失效
4. **立场倾向**：清晰表明你认为的"最可能路径"，以及"次可能但高影响"的路径
5. **不要给概率数字**：禁止"30%概率"式输出，用触发条件与因果链表达不确定性（若X→则Y）

**输出格式（5节，务必精炼，全文控制在1200-1800字）：**

### ① 核心判断（1-2句）
你的中心论点。

### ② 分析框架的运用
解释你的标志性框架如何应用于本议题，推导出你的核心判断。不要复述框架定义，直接用它分析。

### ③ 条件链推演
```
若 [条件A] → 则 [结果X]
  若 [子条件A1] → 则 [子结果X1]
若 [条件B] → 则 [结果Y]
```

### ④ 关键不确定性与边界
- **我的判断可能在什么情况下错？** 依赖的核心假设是什么？假设错了会怎样？
- **与谁根本对立、为什么**（1-2句即可，不要展开对话）

### ⑤ 情景与观察指标
用条件表达，**不写概率数字**：

| 情景 | 触发条件/先行指标 | 因果路径 | 关键观察信号 |
|------|-----------------|---------|------------|
| 基准 | ...（什么条件成立→走这条路） | ... | ... |
| 乐观 | ... | ... | ... |
| 尾部风险 | ... | ... | ... |

末句：面向决策者的一句话行动建议。"""
    return prompt


# ============ 心理学家审计Prompt ============

def generate_psych_prompt(name, personality, summary_text, role_hint):
    ins = personality.get("insights_with_evidence", [])
    top_insights = [i.get("claim", "")[:150] for i in ins[:8]]
    brief = personality.get("brief", "")
    cp = personality.get("cognitive_patterns", {})
    thinking = cp.get("thinking_style", "") if isinstance(cp, dict) else str(cp)

    # ⚠️ 分工：两位心理学家不再重复同一套四步法，避免报告重复
    if "群体" in role_hint or "LeBon" in name or "勒庞" in name:
        task_block = """## 审计任务（你的职责：只做群体/集体层面，**不要逐人诊断**）

⚠️ 有另一位心理学家负责“逐人认知偏差诊断”，你不要重复他的工作。你的战场是**集体层面**。

### 第一步：群体动力学诊断
从群体心理学视角识别这次圆桌的整体特征：
- 是否出现**群体极化**（个体观点在讨论后变得更极端）？
- 是否出现**虚假共识**（表面一致但实际基于不同甚至矛盾的假设）？
- 是否存在**情绪传染**（悲观/乐观情绪如何传导、自我强化）？
- **领袖效应**（谁的叙事主导了讨论，其他人是否在向其靠拢）？

### 第二步：集体盲区 / 房间里的大象
- 哪些假设被**全体学者共同接受但未被验证**？
- 有哪些重要的**反事实被集体忽略**？
- 群体是否共享了某种未经锚定的前提？

### 第三步：群体去偏差调整表
| 群体共识 | 群体心理根源 | 去偏差后调整 | 确信度变化 |
|---------|------------|------------|-----------|

### 第四步：被集体忽略的关键变量
- 有哪些议题相关的重要维度**无人在讨论中提及**？
- 加上这些维度后，结论会怎么变？"""
    else:
        task_block = """## 审计任务（你的职责：只做**个体认知偏差**，群体层面交给勒庞）

⚠️ 有另一位心理学家负责“群体动力学/集体盲区”，你不要替他去分析群体层次。你只盯**每个学者的个体认知偏差**。

### 第一步：逐人偏差诊断
为每位学者识别**1-2 个最显著**认知偏差（最多2个，不要堆砌），每条标注：
- 偏差名称 + 具体表现（**引用发言原文**）
- 该偏差如何影响其判断的可信度
- 去偏差后的具体修正（把定性偏差转成对结论的调整）

### 第二步：去偏差调整表
| 学者 | 识别出的偏差 | 偏差如何影响其判断 | 去偏差后的判断调整 | 修正后可信度变化 |
|------|------------|------------------|------------------|----------------|

### 第三步：你自己的偏差
- 你的框架可能让你系统性高估/低估什么？在下次审计中如何校正？"""

    prompt = f"""# 你是 {name}

## 人格档案
- **简介**: {brief}
- **思维风格**: {thinking}

## 你的核心洞见
{chr(10).join(['- ' + c for c in top_insights])}

## 你的角色: {role_hint}

## 审计材料
以下是多位学者的圆桌讨论发言。请用你的专业视角进行独立审计。

{summary_text}

---

{task_block}

## 格式要求
- 全文控制在 1200-1800 字，直接进入分析，不要写“写在前面”式的开场铺垫
- 引用学者原文时必须逐字摘录，禁止转述后冒充原文
- 不写概率数字，用定性判断（高/中/低确信）+ 条件表达"""
    return prompt


# ============ 学者匹配 ============

def match_scholars(topic, count=6):
    matched = set()
    for keyword, scholars in TOPIC_EXPERT_MAP.items():
        if re.search(keyword, topic):
            for s in scholars:
                matched.add(s)
    defaults = ["Ray_Dalio", "Hyman_Minsky", "Nouriel_Roubini",
                "Carmen_Reinhart", "Peter_Zeihan", "Joseph_Stiglitz",
                "Thomas_Schelling", "Nassim_Taleb"]
    for s in defaults:
        if len(matched) >= count:
            break
        matched.add(s)
    verified = []
    for name in list(matched)[:count + 4]:
        personality = load_personality(name)
        if personality and len(verified) < count:
            verified.append((name, personality))
            print(f"  ✅ {name}")
    return verified


# ============ 新闻搜索（多源并行） ============

def _search_exa_news(question, max_results=6):
    """Exa英文新闻"""
    import requests
    queries = [
        f"{question[:50]} 2026 analysis",
        f"{question[:50]} latest development",
    ]
    results = []
    seen = set()
    for q in queries:
        try:
            r = requests.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": EXA_API_KEY, "Content-Type": "application/json"},
                json={"query": q, "numResults": 3, "type": "auto", "text": True,
                      "startPublishedDate": "2025-12-01T00:00:00.000Z"},
                timeout=15
            )
            if r.status_code == 200:
                for item in r.json().get("results", []):
                    url = item.get("url", "")
                    if url not in seen:
                        seen.add(url)
                        results.append({"title": item.get("title", ""),
                                        "text": item.get("text", "")[:400],
                                        "url": url, "source": "Exa"})
                        print(f"  📰 [Exa] {item.get('title','')[:60]}")
        except Exception as e:
            print(f"  ⚠️ Exa搜索失败: {e}")
    return results[:max_results]


def _search_serpapi_news(question, max_results=6):
    """SerpAPI Google News（中英文）"""
    import requests
    results = []
    seen = set()
    queries = [
        question[:60],
        f"{question[:50]} latest news",
    ]
    for q in queries:
        try:
            params = {
                "engine": "google",
                "q": q,
                "api_key": SERPAPI_KEY,
                "num": 3,
                "tbm": "n",  # news
            }
            r = requests.get("https://serpapi.com/search", params=params, timeout=15)
            if r.status_code == 200:
                for item in r.json().get("news_results", r.json().get("organic_results", [])):
                    title = item.get("title", "")
                    url = item.get("link", "")
                    if url and url not in seen:
                        seen.add(url)
                        snippet = item.get("snippet", "")[:400]
                        results.append({"title": title, "text": snippet, "url": url, "source": "SerpAPI"})
                        print(f"  📰 [SerpAPI] {title[:60]}")
        except Exception as e:
            print(f"  ⚠️ SerpAPI搜索失败: {e}")
    return results[:max_results]


def _search_serpapi_chinese(question, max_results=4):
    """SerpAPI百度搜索（中文资讯）"""
    import requests
    results = []
    seen = set()
    try:
        params = {
            "engine": "baidu",
            "q": question[:60],
            "api_key": SERPAPI_KEY,
            "num": 4,
        }
        r = requests.get("https://serpapi.com/search", params=params, timeout=15)
        if r.status_code == 200:
            for item in r.json().get("organic_results", []):
                title = item.get("title", "")
                url = item.get("link", "")
                if url and url not in seen:
                    seen.add(url)
                    snippet = item.get("snippet", "")[:400]
                    results.append({"title": title, "text": snippet, "url": url, "source": "Baidu"})
                    print(f"  📰 [Baidu] {title[:60]}")
    except Exception as e:
        print(f"  ⚠️ Baidu搜索失败: {e}")
    return results[:max_results]


def _fetch_url_text(url, max_chars=10000):
    """抓取URL正文，用trafilatura提取正文+BeautifulSoup清洗fallback"""
    import re
    try:
        # 优先用trafilatura（专业正文提取）
        import trafilatura as tf
        downloaded = tf.fetch_url(url)
        if downloaded:
            text = tf.extract(downloaded, include_comments=False, include_tables=True, favor_precision=True)
            if text and len(text) > 50:
                return text[:2000]
        # fallback: BeautifulSoup手工提取
        import requests
        from bs4 import BeautifulSoup
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        r = requests.get(url, timeout=12, headers=headers, allow_redirects=True)
        if r.status_code != 200:
            return ""
        ct = r.headers.get('content-type', '')
        raw = None
        for enc in ['utf-8', 'gbk', 'gb2312', 'gb18030']:
            try:
                raw = r.content.decode(enc, errors='replace')[:max_chars]
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if raw is None:
            return ""
        # 验证页检测
        verify_marks = ['安全验证', '请稍后重试', '请输入验证码', '访问验证', '百度安全验证',
                        'robot check', 'captcha', 'Access Denied']
        for mark in verify_marks:
            if mark in raw[:2000]:
                return ""
        soup = BeautifulSoup(raw, 'html.parser')
        # 去除无用标签
        for tag in soup.find_all(['script', 'style', 'noscript', 'nav', 'footer', 'header', 'aside', 'iframe']):
            tag.decompose()
        # 优先提取article/main标签内容
        article = soup.find('article') or soup.find(attrs={'role': 'main'}) or soup.find('main')
        if article:
            text = article.get_text(separator=chr(10))
        else:
            # fallback: 找最大的文本块
            for tag in soup.find_all('div'):
                tag_text = tag.get_text()
                if len(tag_text) > 500:
                    text = tag_text
                    break
            else:
                text = soup.get_text(separator=chr(10))
        text = re.sub(r'\n\s*\n+', chr(10), text).strip()
        lines = [l.strip() for l in text.split(chr(10)) if l.strip()]
        return chr(10).join(lines)[:2000]
    except Exception:
        return ""


def search_historical_cases(question, max_results=6):
    """搜索与议题相关的历史案例（用于增强分析的实证基础）"""
    import requests
    results = []
    seen = set()

    # 提取关键词构建历史搜索query
    topic_short = question[:40]
    # 检测议题类型，匹配对应的历史案例关键词
    history_queries = []
    if re.search(r'关税|贸易|脱钩|制裁', question):
        history_queries = [
            "1930 Smoot-Hawley tariff impact stock market history",
            "1985 Plaza Accord Japan response history",
            "2018 Trump trade war China stock market impact",
            "trade war historical examples stock market",
            "美日贸易战历史 股市影响 复盘",
        ]
    elif re.search(r'房地产|楼市|泡沫', question):
        history_queries = [
            "Japan 1990s real estate bubble burst stock market",
            "2008 US housing crisis stock market impact",
            "中国房地产泡沫 日本对比",
        ]
    elif re.search(r'危机|崩盘|恐慌|衰退', question):
        history_queries = [
            "Asian financial crisis 1997 stock market recovery",
            "2008 financial crisis stock market timeline",
            "2020 COVID crash stock market recovery pattern",
            "历次金融危机 A股 表现 复盘",
        ]
    elif re.search(r'AI|人工智能|科技', question):
        history_queries = [
            "dot-com bubble 2000 stock market AI comparison",
            "technology revolution stock market historical cycles",
        ]
    else:
        # 通用：搜相关历史事件
        history_queries = [
            f"{topic_short} historical precedent stock market",
            f"{topic_short} 历史案例 复盘",
        ]

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = []
        for q in history_queries[:3]:
            futures.append(pool.submit(_search_serpapi_chinese, q, 2))
        futures.append(pool.submit(_search_exa_news, topic_short + " historical analysis", 3))
        for f in as_completed(futures):
            try:
                for item in f.result():
                    url = item.get("url", "")
                    if url not in seen:
                        seen.add(url)
                        results.append(item)
                        print(f"  📚 [历史] {item['title'][:55]}")
            except:
                pass

    # 深度抓取
    for item in results[:4]:
        url = item.get("url", "")
        if url:
            detail = _fetch_url_text(url, max_chars=3000)
            if detail and len(detail) > len(item.get("text", "")):
                item["text"] = detail
                item["fetched"] = True

    return results[:max_results]


def search_news(question, max_results=12):
    """多源并行新闻搜索：Exa + SerpAPI(Google News + Baidu)"""
    all_results = []
    seen_urls = set()

    # 并行搜索
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def dedup_add(items):
        for item in items:
            url = item.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append(item)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(_search_exa_news, question, 5),
            pool.submit(_search_serpapi_news, question, 5),
            pool.submit(_search_serpapi_chinese, question, 4),
        ]
        for f in as_completed(futures):
            try:
                dedup_add(f.result())
            except Exception as e:
                print(f"  ⚠️ 搜索线程异常: {e}")

    # 对所有结果做深度抓取（fetch完整文章）
    print(f"\n  📡 深度抓取 {len(all_results)} 条URL...")
    fetched_count = 0
    for item in all_results[:12]:
        url = item.get("url", "")
        if url:
            detail = _fetch_url_text(url)
            if detail and len(detail) > len(item.get("text", "")):
                item["text"] = detail
                item["fetched"] = True
                fetched_count += 1
                print(f"    ✅ {item['title'][:40]}... → {len(detail)}字")
    print(f"  📡 成功抓取 {fetched_count}/{len(all_results[:12])} 条")

    # 过滤掉抓取后内容为空的（验证页等），按信息量排序
    all_results = [x for x in all_results if len(x.get("text", "")) > 30]
    all_results.sort(key=lambda x: len(x.get("text", "")), reverse=True)
    return all_results[:max_results]

# ============ LLM综合分析Prompt生成 ============

def generate_synthesis_prompt(topic, today, speeches, audits, news):
    """
    生成综合分析prompt，让LLM真正做跨学者提炼，而非正则拼接。
    """
    # 组装学者发言摘要
    speech_blocks = []
    for s in speeches:
        speech_blocks.append(f"""
## {s['name']}（{s.get('brief', '')[:80]}）
{s['speech']}
""")
    speeches_text = "\n".join(speech_blocks)

    # 组装心理审计
    audit_text = ""
    if audits:
        audit_blocks = []
        for a in audits:
            audit_blocks.append(f"""
### {a['name']}（{a.get('role', '')}）
{a['audit']}
""")
        audit_text = "\n".join(audit_blocks)

    # 时事+历史案例背景（综合分析用完整数据）
    news_text = ""
    if news:
        context_parts = []
        current_news = []
        current_hist = []
        for n in news[:12]:
            title = n.get('title', '')
            text = n.get('text', '')[:800]  # 给综合分析更多内容
            source = n.get('source', '')
            is_history = any(kw in title for kw in ['历史', '复盘', '回顾', 'precedent', 'lesson', '1985', '1930', '1997', '2008', '2018'])
            block = f"### {title}\n来源: {source} | URL: {n.get('url','')}\n\n{text}"
            if is_history:
                current_hist.append(block)
            else:
                current_news.append(block)
        if current_news:
            context_parts.append("## 📰 最新时事数据\n\n" + "\n\n".join(current_news))
        if current_hist:
            context_parts.append("## 📚 相关历史案例\n\n" + "\n\n".join(current_hist))
        news_text = "\n\n".join(context_parts)

    nl = chr(10)
    participants_str = ', '.join([s['name'] for s in speeches])
    psych_str = (f'# 心理审计参与者{nl}{nl.join([a["name"] + "(" + a.get("role","") + ")" for a in audits])}') if audits else ''
    psych_audit_str = (f'# 心理审计原文{nl}{audit_text}') if audit_text else ''
    news_bg_str = (f'# 时事背景{nl}{news_text}') if news_text else ''

    prompt = f"""你是一位资深智库首席分析师。你的任务是对以下多学者圆桌讨论进行深度综合分析，产出一份专业级智库报告。

# 议题
{topic}

# 日期
{today}

# 参与者
{participants_str}

{psych_str}

---

# 学者发言原文
{speeches_text}

---

{psych_audit_str}

---

{news_bg_str}

---

# 你的任务：综合分析

请仔细阅读以上所有材料，然后写出完整的智库报告。**不要简单复述或拼接原文**——你必须做真正的跨学者提炼、交叉验证和结构化综合。

## 报告结构要求（严格按此结构输出）

### 一、执行摘要
- 300-500字的精炼摘要，覆盖核心共识、最大分歧、关键风险、行动建议
- 面向繁忙的决策者，读完这段就应该知道整体结论

### 二、背景与议题定义
- 议题的关键维度拆解（3-5个子问题）
- 时事背景综述（引用搜索到的新闻，标注来源）
- 本报告的分析边界（什么在范围内，什么不在）

### 三、核心判断聚合

#### 3.1 共识判断
用表格，每个共识判断一行：
| 共识判断 | 支持学者(人数) | 反对学者 | 置信度 | 核心论据摘要 |

**要求**：
- 只列出真正获得多数支持的判断（≥60%学者同意）
- 置信度分三档：高度一致(≥80%)、强共识(60-80%)、弱共识(50-60%)
- 核心论据摘要要提炼出跨学者的共同论证逻辑，不是简单罗列人名

#### 3.2 关键分歧
用表格，每个分歧一行：
| 分歧议题 | 观点A及论证逻辑 | 支持者 | 观点B及论证逻辑 | 支持者 | 分歧的根源（方法论/假设/价值观） |

**要求**：
- 必须解释分歧为什么存在（是底层假设不同？方法论差异？还是信息不对称？）
- 标注分歧是否可调和

#### 3.3 孤立观点（值得注意的少数派意见）
- 谁提出了独特的、其他人没提到的角度？
- 即使是少数派，该观点的价值是什么？

### 四、跨框架交叉论证

这是报告最有价值的部分。不要按学者逐一复述，而是按**议题维度**组织：

对每个子议题：
1. **各学派的分析路径对比** — 谁从什么框架出发，推导出了什么结论？（用对比，不是罗列）
2. **论点交锋** — 学者之间明确互相批评了什么？谁的反驳更有力？
3. **该子议题的综合裁定** — 基于交叉分析，你的判断是什么？理由是什么？

### 五、假设检验

| 假设 | 提出者 | 内容 | 可证伪条件 | 如果错误的影响 | 验证优先级 |

**要求**：
- 识别所有学者共同依赖但未验证的假设（"房间里的大象"）
- 识别每个学者独有的关键假设
- 标注哪些假设最容易在短期内被验证

### 六、情景分析

综合所有学者的情景预测，构建3-4个互斥且穷尽的情景。**用触发条件而非概率数字**（禁止"30%概率"式表述）：

| 情景 | 触发条件/先行指标 | 路径描述 | 各学者对此情景的预判差异 |
|------|-----------------|---------|----------------------|

**要求**：
- 用可观察指标/事件定义触发条件，不用概率数字
- 标注谁对哪个情景最乐观、谁最悲观
- 每个情景要有明确的"这个情景被证实的信号"

### 七、偏差审计整合

{f'基于{len(audits)}位心理学家的审计结果（一位负责个体认知偏差、一位负责群体动力学），做以下整合：' if audits else '（本次无心理审计）'}

1. **个体认知偏差修正** — 汇总心理学家识别的逐人偏差及去偏差修正
2. **群体心理诊断** — 群体极化/虚假共识/情绪传染/集体盲区的结论
3. **综合修正表** — 表格：
| 对象（学者或群体共识） | 识别出的偏差 | 对判断的影响 | 去偏差后调整 | 修正后可信度变化 |
4. **被集体忽略的维度** — 心理审计发现哪些重要方面未被讨论？加上后结论会怎么变？

### 八、决策树

基于以上全部分析，构建面向决策者的决策树：

```
关键不确定性1（最重要）
├── 情况A（触发信号：[具体可观察指标]）
│   ├── 子决策：选择A1 / A2
│   └── 关键监控：[具体指标]
├── 情况B（触发信号：[具体可观察指标]）
│   ├── 子决策：选择B1 / B2
│   └── 关键监控：[具体指标]
└── 情况C（尾部风险，触发信号：[具体可观察指标]）
    └── 保护措施：[具体行动]
```

然后用表格补充每个叶子节点的具体行动、仓位/资源建议、止损条件。

### 九、风险清单

| 风险 | 来源(哪位学者/哪个假设) | 触发条件 | 影响 | 监控指标 | 缓释措施 |

### 十、附录：学者完整发言

每位学者的完整发言放在折叠块（<details>）中，供需要时查阅。

---

# 格式要求
1. 全部使用中文
2. 表格必须填满，不允许空行
3. 每个论点都要标注出处（"Taleb提出"、"Dalio与Schelling一致认为"）
4. **不使用概率数字**——用触发条件/先行指标/条件链表达不确定性，禁止"20-30%"式伪精确
5. 决策建议必须具体可执行，不要写"谨慎观望"这种废话
6. **必须引用时事数据和历史案例**——以上提供的实时新闻和历史复盘是核心素材，你的分析必须建立在这些具体数据和事件上，不能只靠大模型的泛化知识。引用时标注来源（"据百度资讯报道"、"参考2018年贸易战复盘"等）
7. 报告正文总长度2500-4000字（不含附录）
8. **不在正文重复学者发言**——正文只做跨学者提炼与裁定，完整发言在附录可查"""

    return prompt


def cmd_report(args):
    """
    报告阶段：收集发言 → 生成综合prompt → 输出spawn指令
    """
    orch_file = "/tmp/roundtable_orchestration.json"
    with open(orch_file) as f:
        orch = json.load(f)

    topic = orch["topic"]
    today = orch["date"]
    scholars_dir = args.scholars_dir
    psychs_dir = args.psychs_dir or "/tmp/rt_psychs"

    scholar_speeches = []
    for s in orch["scholars"]:
        name = s["name"]
        sf = os.path.join(scholars_dir, f"{name}.md")
        if os.path.exists(sf):
            with open(sf) as f:
                scholar_speeches.append({"name": name, "brief": s["brief"], "speech": f.read()})
            print(f"  📄 {name}")
        else:
            print(f"  ⚠️ 缺少 {name} 的发言文件")

    psych_audits = []
    for p in orch.get("psychologists", []):
        name = p["name"]
        af = os.path.join(psychs_dir, f"{name}.md")
        if os.path.exists(af):
            with open(af) as f:
                psych_audits.append({"name": name, "role": p["role"], "audit": f.read()})
            print(f"  🧠 {name}")

    if not scholar_speeches:
        print("❌ 无学者发言，无法生成报告")
        return

    print(f"\n📝 生成综合分析prompt...")
    all_news = orch.get("news", []) + orch.get("history", [])
    synthesis_prompt = generate_synthesis_prompt(topic, today, scholar_speeches, psych_audits, all_news)

    synth_file = "/tmp/rt_synthesis_prompt.md"
    with open(synth_file, "w") as f:
        f.write(synthesis_prompt)

    print(f"\n{'='*60}")
    print(f"✅ 综合分析prompt已生成: {synth_file}")
    print(f"   {len(synthesis_prompt)} 字 | {len(scholar_speeches)}位学者 | {len(psych_audits)}位审计")
    print(f"{'='*60}")
    print(f"""
🚀 下一步：spawn综合分析agent

将综合分析结果保存到: /tmp/rt_synthesis/analysis.md

sessions_spawn(
    label="智库-综合分析",
    model="deepseek/deepseek-chat",
    runTimeoutSeconds=600,
    task="Read /tmp/rt_synthesis_prompt.md and produce the full report as specified. Write output to /tmp/rt_synthesis/analysis.md"
)

综合分析完成后运行:
  python3 roundtable_v3.py finalize /tmp/rt_synthesis/analysis.md
""")


def cmd_finalize(args):
    """
    最终阶段：读取LLM综合输出，加上元数据头和附录，输出最终报告
    """
    orch_file = "/tmp/roundtable_orchestration.json"
    with open(orch_file) as f:
        orch = json.load(f)

    topic = orch["topic"]
    today = orch["date"]

    synthesis_file = args.synthesis_file
    with open(synthesis_file) as f:
        synthesis_body = f.read()

    # 收集学者发言作为附录（只保留“核心判断 + 条件链推演”，压缩体积）
    scholars_dir = args.scholars_dir if hasattr(args, 'scholars_dir') else "/tmp/rt_scholars"
    psychs_dir = args.psychs_dir if hasattr(args, 'psychs_dir') else "/tmp/rt_psychs"

    def _extract_sections(text, wanted_kws):
        """从学者发言中截取包含指定关键字的节（按 ### 分节）。
        若找不到任何匹配节，则回退到前1500字。"""
        lines = text.split("\n")
        blocks, cur = [], []
        for ln in lines:
            if re.match(r'^#{2,3}\s', ln):
                if cur:
                    blocks.append(cur)
                cur = [ln]
            else:
                cur.append(ln)
        if cur:
            blocks.append(cur)
        picked = ["\n".join(b) for b in blocks if any(kw in b[0] for kw in wanted_kws)]
        if picked:
            return "\n\n".join(picked)
        return text[:1500]

    # 如果综合输出里没有附录，自动补上
    if "附录" not in synthesis_body and "<details>" not in synthesis_body:
        appendix = "\n\n---\n\n## 附录：学者核心发言（节选：核心判断 + 条件链）\n"
        for s in orch["scholars"]:
            name = s["name"]
            sf = os.path.join(scholars_dir, f"{name}.md")
            if os.path.exists(sf):
                with open(sf) as f:
                    content = f.read()
                core = _extract_sections(content, ["核心判断", "条件链推演", "条件链"])
                appendix += f"\n<details>\n<summary><b>{name}</b> — {s.get('brief', '')[:80]}</summary>\n\n{core}\n\n</details>\n"
        # 心理审计（分工后的两篇，全文保留，因为不重复）
        for p in orch.get("psychologists", []):
            name = p["name"]
            af = os.path.join(psychs_dir, f"{name}.md")
            if os.path.exists(af):
                with open(af) as f:
                    content = f.read()
                appendix += f"\n<details>\n<summary><b>{name}</b> — {p.get('role', '')}</summary>\n\n{content}\n\n</details>\n"
        synthesis_body += appendix

    # 写入最终报告
    report_dir = f"{REPORT_DIR}/{today}"
    os.makedirs(report_dir, exist_ok=True)
    safe_topic = re.sub(r'[^\w\u4e00-\u9fff]', '', topic[:12])
    report_file = os.path.join(report_dir, f"圆桌_{safe_topic}_{today.replace('-','')}.md")

    with open(report_file, "w") as f:
        f.write(synthesis_body)

    print(f"\n✅ 最终报告已生成: {report_file}")
    print(f"   {len(synthesis_body)} 字")
    return report_file

# ============ cmd_prepare（准备阶段） ============

def cmd_prepare(args):
    topic = args.topic
    today = date.today().strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"🏛️ 智库圆桌 v3.1 — 准备阶段")
    print(f"议题: {topic}")
    print(f"日期: {today}")
    print(f"{'='*60}")

    news_data = []
    history_data = []
    if not args.no_news:
        print("\n📡 搜索时事新闻...")
        news_data = search_news(topic)
        print(f"  获 {len(news_data)} 条时事")

        # 写入时事数据文件
        news_file = "/tmp/rt_context_news.md"
        with open(news_file, "w") as f:
            f.write(f"# 议题时事数据\n议题: {topic}\n日期: {today}\n\n")
            for i, n in enumerate(news_data, 1):
                f.write(f"## [{i}] {n.get('title','')}\n")
                f.write(f"来源: {n.get('source','')} | URL: {n.get('url','')}\n\n")
                f.write(f"{n.get('text','')}\n\n---\n\n")
        print(f"  📄 时事数据已写入 {news_file}")

        # 搜索历史案例
        print("\n📚 搜索历史案例...")
        history_data = search_historical_cases(topic)
        print(f"  获 {len(history_data)} 条历史案例")

        if history_data:
            history_file = "/tmp/rt_context_history.md"
            with open(history_file, "w") as f:
                f.write(f"# 相关历史案例\n议题: {topic}\n日期: {today}\n\n")
                for i, h in enumerate(history_data, 1):
                    f.write(f"## [{i}] {h.get('title','')}\n")
                    f.write(f"来源: {h.get('source','')} | URL: {h.get('url','')}\n\n")
                    f.write(f"{h.get('text','')}\n\n---\n\n")
            print(f"  📄 历史案例已写入 {history_file}")

    # 合并所有背景数据
    all_context = news_data + history_data

    print(f"\n🎯 匹配学者 (目标{args.scholars}人)...")
    scholars = match_scholars(topic, args.scholars)

    camps = match_debate_camp(topic)
    all_scholar_names = [name for name, _ in scholars]

    print(f"\n📝 生成增强版prompt...")
    orchestration = {
        "topic": topic,
        "date": today,
        "model": args.model,
        "timeout": args.timeout,
        "scholars": [],
        "news": news_data,
        "history": history_data,
        "news_file": "/tmp/rt_context_news.md" if news_data else None,
        "history_file": "/tmp/rt_context_history.md" if history_data else None,
        "camps": {"pro": camps[0], "con": camps[1], "observer": camps[2]} if camps and len(camps) > 2 else
                 {"pro": camps[0], "con": camps[1], "observer": []} if camps else None,
    }

    for name, personality in scholars:
        camp = None
        if camps:
            if name in camps[0]: camp = "pro"
            elif name in camps[1]: camp = "con"
            elif len(camps) > 2 and name in camps[2]: camp = "observer"

        prompt = generate_scholar_prompt(name, personality, topic, all_context,
                                          all_scholars=all_scholar_names, camp=camp)
        prompt_file = f"/tmp/rt_prompt_{name}.md"
        with open(prompt_file, "w") as f:
            f.write(prompt)

        matched_ins = match_insights(personality, topic, top_n=99)
        top_domains = defaultdict(int)
        for ins in matched_ins[:12]:
            top_domains[ins.get("dim", "?")] += 1
        zh_kw, en_kw = extract_bilingual_keywords(topic)

        orchestration["scholars"].append({
            "name": name,
            "domain": SCHOLAR_DOMAIN.get(name, "unknown"),
            "brief": personality.get("brief", ""),
            "prompt_file": prompt_file,
            "prompt_chars": len(prompt),
            "insights_matched": len(matched_ins[:12]),
            "insight_dims": dict(top_domains),
            "camp": camp,
        })
        print(f"  {name}: {len(prompt)}字, {len(matched_ins[:12])}条insights, 阵营:{camp or '自由'}")

    # 心理学家
    psych_specs = []
    for pname, pdomain, prole in PSYCHOLOGISTS:
        pp = load_personality(pname)
        spec = {"name": pname, "domain": pdomain, "role": prole, "has_personality": pp is not None}
        if pp:
            spec["brief"] = pp.get("brief", "")
        psych_specs.append(spec)
    orchestration["psychologists"] = psych_specs

    output_file = "/tmp/roundtable_orchestration.json"
    with open(output_file, "w") as f:
        json.dump(orchestration, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"✅ 编排文件已生成: {output_file}")
    print(f"   学者: {len(orchestration['scholars'])}人")
    print(f"   阵营: {'已配置' if camps else '自由发言'}")
    print(f"   心理学家: {len(psych_specs)}人待命")
    print(f"{'='*60}")

    return orchestration


# ============ main ============

def main():
    import argparse
    parser = argparse.ArgumentParser(description="智库圆桌会议 v3.1 — 结构化智库产出")
    sub = parser.add_subparsers(dest="command")

    p_prepare = sub.add_parser("prepare", help="准备阶段")
    p_prepare.add_argument("topic", help="议题描述")
    p_prepare.add_argument("--scholars", type=int, default=6, help="学者人数（默认6）")
    p_prepare.add_argument("--no-news", action="store_true", help="跳过时事搜索")
    p_prepare.add_argument("--model", default=SUB_MODEL)
    p_prepare.add_argument("--timeout", type=int, default=SUB_TIMEOUT)

    p_report = sub.add_parser("report", help="报告阶段：收集发言→生成综合prompt")
    p_report.add_argument("scholars_dir", help="学者发言目录")
    p_report.add_argument("--psychs-dir", default="/tmp/rt_psychs", help="心理学家审计目录")

    p_finalize = sub.add_parser("finalize", help="最终阶段：读取综合输出→生成报告")
    p_finalize.add_argument("synthesis_file", help="综合分析输出文件路径")
    p_finalize.add_argument("--scholars-dir", default="/tmp/rt_scholars", help="学者发言目录（用于补附录）")
    p_finalize.add_argument("--psychs-dir", default="/tmp/rt_psychs", help="心理学家审计目录")

    # 兼容v2：如果第一个参数不是子命令，当作议题处理
    import sys
    if len(sys.argv) > 1 and sys.argv[1] not in ("prepare", "report", "finalize", "-h", "--help"):
        # 自动加prepare
        sys.argv.insert(1, "prepare")

    args = parser.parse_args()
    if args.command == "prepare":
        cmd_prepare(args)
    elif args.command == "report":
        cmd_report(args)
    elif args.command == "finalize":
        cmd_finalize(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
