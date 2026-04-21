---
name: think-tank
description: "多视角人格智库系统（统一pipeline）。学者+心理学家，v3逻辑溯源。采集→蒸馏→入库→张力→RAG报告。触发词：智库讨论、人格蒸馏、人格还原、战略报告、pipeline、逻辑溯源。"
metadata: {"openclaw":{"emoji":"🏛️","os":["linux","darwin","win32"]}}
---

# 🏛️ 人格智库系统

核心理念：**独立思考，真实还原**。每位人物保持独立视角，忠实于其真实立场。

## 唯一工作流

```
素材采集 → 预处理(_preprocess.py) → 下载全文(batch_game_theory_collect.py) → 预处理(_preprocess.py) → chunk入库(ingest_v3.py) → V3蒸馏(sessions_spawn) → 结构化入库(ingest_v3.py)
```

⚠️ **预处理必须做，不能跳过。**

⚠️ **报告生成 ≠ 人格蒸馏入库，是两个独立流程。**

| 工作流 | 目的 | 入口 | 产出 |
|--------|------|------|------|
| **A. 人格蒸馏入库** | 素材→蒸馏→向量入库 | sessions_spawn + `ingest_v3.py` | chromadb 8集合 |
| **B. 报告生成** | 议题→多视角分析报告 | `think_tank_pipeline_v3.py` | Markdown报告 |

---

## A. 人格蒸馏入库

### Step 1: 素材采集

**论文（学者/心理学家）：**
1. 元数据：`scripts/collect_new_scholars.py`（SerpAPI Google Scholar）
2. 全文下载：`scripts/batch_game_theory_collect.py`（OpenAlex→Unpaywall→Sci-Hub+Playwright）
3. 保存：`{person}/materials/paper_{title}.txt`

**网页：**
- 优先级：`web_fetch` → `web-scraper-jina` → `playwright-scraper`
- 跳过 <500B 文件

### Step 2: 预处理

```bash
~/miniconda3/envs/tt/bin/python3 scripts/_preprocess.py
```

### Step 3: V3 蒸馏（sessions_spawn）

- 模板：`think-tank/templates/distill_prompt_v3.md`
- 输出：`{person}/personality_v3.json`
- 素材 >60000字符需截断

### Step 4: 向量入库

**唯一脚本：`scripts/ingest_v3.py`**（通用）

```bash
~/miniconda3/envs/tt/bin/python3 scripts/ingest_v3.py <人物名> <人物目录>
```

两层合一：materials chunk + 7个结构化集合。

---

## B. 报告生成 Pipeline v3.2

```bash
~/miniconda3/envs/tt/bin/python3 scripts/think_tank_pipeline_v3.py "议题"
```

子议题拆解 → 四源时事搜索 → RAG → 历史参照 → 红蓝选人 → 逐章sessions_spawn

---

## 当前状态（2026-04-21）

| 体系 | 子领域 | 人数 | 蒸馏 | 素材 |
|------|--------|------|------|------|
| 军事(military) | — | 25 | 25/25 ✅ | 680 |
| 学者(scholars) | game-theory(10), economics(8), ir-theory(8), middle-east(5), central-asia(4), ir-security(4), persian-islamic(4), religious-studies(4) | 47 | 47/47 ✅ | 1164 |
| 心理学家(psychologists) | behavioral(7), cognitive(6), psychoanalysis(6), social-engineering(5) | 24 | 23/24 | 445 |

缺蒸馏：克里斯·哈德纳吉（素材仅1条meta，无实际内容，需补采集）

向量库：17,217条（materials:11303, insights:5584, core_profile:105, tension:20, drive:46, repetition:85, transference:40, unconscious:34）

---

## 项目结构

```
/mnt/d/data/think-tank-data/              ← 工作目录（D盘）
├── _chromadb/lacan_db/                   ← 向量库
├── personalities/                        ← symlink ← workspace/think-tank/personalities
│   ├── military/                         ← 25人
│   ├── scholars/                         ← 47人（8子领域）
│   └── psychologists/                    ← 24人（4子领域）
└── reports/                              ← 生成的报告

~/.openclaw/workspace/think-tank/
├── personalities → /mnt/d/data/think-tank-data/personalities
├── templates/distill_prompt_v3.md
└── tension_analysis.json
```

## 红线

- **GLM调用必须走sessions_spawn**，禁止直接调API
- 禁止用SiliconFlow API（要钱）
- 分析必须基于RAG素材，禁止凭空编造
- source_ref 必须指向真实素材
- 每份报告末尾标注"AI模拟"
