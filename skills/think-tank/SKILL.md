---
name: think-tank
description: "多视角人格智库系统（统一pipeline）。学者+心理学家，v4偏差审计。采集→蒸馏→入库→RAG报告（4章+预-mortem）。触发词：智库讨论、人格蒸馏、人格还原、战略报告、pipeline、逻辑溯源。"
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
1. 元数据：`skills/think-tank/scripts/collect_new_scholars.py`（SerpAPI Google Scholar）
2. 全文下载：`skills/think-tank/scripts/batch_game_theory_collect.py`（OpenAlex→Unpaywall→Sci-Hub+Playwright）
3. 保存：`{person}/materials/paper_{title}.txt`

**网页：**
- 优先级：`web_fetch` → `web-scraper-jina` → `playwright-scraper`
- 跳过 <500B 文件

### Step 2: 预处理

```bash
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/_preprocess.py
```

### Step 3: V3 蒸馏（sessions_spawn）

- 模板：`skills/think-tank/templates/distill_prompt_v3.md`
- 输出：`{person}/personality_v3.json`
- 素材 >60000字符需截断

### Step 4: 向量入库

**唯一脚本：`skills/think-tank/scripts/ingest_v3.py`**（通用）

```bash
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/ingest_v3.py <人物名> <人物目录>
```

两层合一：materials chunk + 7个结构化集合。

---

## B. 报告生成 Pipeline v4.0

```bash
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/think_tank_pipeline_v3.py "议题"
```

```
议题 → 子议题拆解 → 四源时事搜索 → RAG → 红蓝对抗(心理学家独立) → 4章报告 → 预-mortem偏差审计 → 拼接
```

**v5改动（心理学家认知评审共识）：**
- **去掉概率数字**——改为条件判断树（若X→则Y），禁止「30%概率」式输出
- **红蓝对抗→框架对抗**——不是人对抗，而是分析框架对抗（现实主义vs经济学vs历史类比）
- ch01 **因果图+历史类比库**——识别因果变量和关系，不是简单的问题解构
- ch04 **条件情景树+尾部风险+鲁棒性测试**——每个情景有触发条件+因果链+失效检测指标
- **引用格式**——要求：来源（学者+作品+年份）+原文论断+适用边界+因果连接，禁[RAG: XX]式标签
- ch03 **偏差审计**——标注偏差方向（高估还是低估）+去偏差具体操作

**v4.2 RAG架构改造（心理学家圆桌共识）：**
- RAG数据**按学者分组传递**（不再按子议题），每人给profile+insights+tensions+materials
- **insights不截断**（蒸馏后的逻辑表述本身就是引用素材）
- **tension独立集合**（从insights中分离，不再被过滤）
- **关联表**：每条insight/tension通过source_ref→material_id关联到原文

**生成顺序：** ch01-ch04并行子session → premortem审计 → 拼接最终报告

---

## 当前状态（2026-04-21）

| 体系 | 子领域 | 人数 | 蒸馏 | 素材 |
|------|--------|------|------|------|
| 军事(military) | — | 25 | 25/25 ✅ | 680 |
| 学者(scholars) | game-theory(10), economics(8), ir-theory(8), middle-east(5), central-asia(4), ir-security(4), persian-islamic(4), religious-studies(4) | 47 | 47/47 ✅ | 1164 |
| 心理学家(psychologists) | behavioral(7), cognitive(6), psychoanalysis(6), social-engineering(5) | 24 | 23/24 | 445 |

缺蒸馏：克里斯·哈德纳吉（素材仅1条meta，无实际内容，需补采集）

向量库：4个活跃集合
| 集合 | 说明 | 关联 |
|------|------|------|
| insights | 蒸馏后的核心逻辑表述（按dim分类） | source_ref→material_id |
| tension | 思想内在矛盾（独立集合） | source_ref→material_id |
| materials | 原始素材分块（800字/块） | material_id |
| core_profile | 人格摘要（96人） | — |

关联表：每条insight/tension通过metadata.source_ref和material_id关联到materials集合的原文。

---

## 项目结构

```
skills/think-tank/
├── SKILL.md                      ← 你正在这里
├── scripts/                      ← 全部流程脚本
│   ├── ingest_v3.py              ⭐ 单人入库（素材+结构化）
│   ├── batch_ingest.py           批量串行入库
│   ├── _preprocess.py            素材去噪/清理
│   ├── _batch_distill.py         蒸馏辅助（子session任务生成）
│   ├── think_tank_pipeline_v3.py ⭐ 报告生成 Pipeline v4.0（4章+预-mortem）
│   ├── collect_new_scholars.py   论文元数据采集
│   ├── batch_game_theory_collect.py 全文下载
│   ├── historical_context.py     历史事件引擎(Redis)
│   ├── cache_news.py             新闻缓存
│   ├── fetch_acled.py / fetch_ucdp.py / fetch_cls.py / fetch_market.py
│   └── rebuild_all.sh            全量重建（严格串行）
└── templates/
    └── distill_prompt_v3.md      V3蒸馏模板（含情境触发条件矩阵）

/mnt/d/data/think-tank-data/              ← 数据目录（D盘）
├── _chromadb/lacan_db/                   ← 向量库（chromadb，严禁并发）
├── personalities/                        ← 96人素材+蒸馏
│   ├── military/                         ← 25人
│   ├── scholars/                         ← 47人（8子领域）
│   └── psychologists/                    ← 24人（4子领域）
└── reports/                              ← 生成的报告
```

## 红线

- **GLM调用必须走sessions_spawn**，禁止直接调API
- 禁止用SiliconFlow API（要钱）
- 分析必须基于RAG素材，禁止凭空编造
- source_ref 必须指向真实素材
- 每份报告末尾标注"AI模拟"
