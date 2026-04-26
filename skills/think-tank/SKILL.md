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
3. 保存：`{person}/materials/paper_{title}.txt`（有DOI时命名为 `paper_{title}_DOI_{doi}.txt`）

⚠️ **素材数量红线：每人学术论文至少30-50篇，有多少找多少，不是10-15篇。10-15篇远远不够。**

⚠️ **采集必须用data_search模块（skills/data-search/scripts/data_search.py）**，不要自己重写搜索脚本。data_search已有L1通用搜索(Exa/SerpAPI/Tavily)+L2中文深度(知乎/搜狗/财联社)+L3学术(Semantic Scholar)+L4宏观数据。中英文必须分开搜。采集脚本import data_search的函数，不要重复造轮子。

**网页：**
- 优先级：`web_fetch` → `web-scraper-jina` → `playwright-scraper`
- 跳过 <500B 文件

⚠️ **论文元数据：文件名必须包含论文标题，有DOI时加DOI后缀。入库时自动提取title和doi存入向量库metadata，按DOI去重。**

⚠️ **搜索渠道：用data_search模块，中英文分开搜。采集脚本用collect_via_datasearch.py（基于data_search模块）。禁止自己重写搜索脚本。**
- Sci-Hub/任何外部网站**禁止批量循环下载**，必须逐篇下载，每次间隔**60秒+**
- Exa/Tavily等API每次请求间隔**5-10秒**
- 违反会封IP，后果严重

### Step 2: 预处理 + 质量检测（必须两步都做，不能只跑预处理）

```bash
# Step 2a: 预处理 + 机械去重 + 低质量过滤 + 生成报告
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/_preprocess.py <person_dir> --quality
```

⚠️ **--quality 是必须参数，不要省略。** 只跑预处理不做质量检测等于没做。

```bash
# Step 2b: LLM质量评分（sessions_spawn调GLM-4.7，分批处理）
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/quality_check_llm.py <person_dir>
# 生成 _quality_prompt_0.md, _quality_prompt_1.md ...（每批20篇）

# 对每个batch文件，spawn子session读prompt并让GLM评分，返回JSON
# 然后逐批apply：
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/quality_check_llm.py <person_dir> --apply '<json>'
```

⚠️ **预处理和质量检测是绑定流程，采集完素材后必须执行完整流程。**

### Step 3: V3 蒸馏（逐篇模式）

⚠️ **蒸馏必须逐篇处理，禁止一次性塞给LLM。** 素材量大时会超时或幻觉。

**全量蒸馏（首次）：**
```bash
# 生成逐篇蒸馏prompts
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/_batch_distill.py <person_dir> --per-file
# → 生成 _per_file_prompts/_prompt_{index}_{filename}.md

# 逐个spawn子session处理每个prompt，结果保存到 _per_file_results/{filename}.json

# 全部完成后合并
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/_batch_distill.py <person_dir> --merge-per-file
```

**增量蒸馏（补充新素材后）：**
```bash
# 自动检测未蒸馏的新素材，生成逐篇prompts
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/incremental_distill.py <person_dir> --auto
# → 生成 _per_file_prompts/_prompt_inc_{index}_{filename}.md（包含已有insights摘要避免重复）

# 逐个spawn子session处理，结果保存到 _per_file_results/{filename}.json

# 应用增量结果
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/incremental_distill.py <person_dir> --apply
```

- 每次蒸馏完成后自动记录 `_distilled_files.json`（已蒸馏的素材文件列表）

**逐篇蒸馏模式**（素材过多时LLM上下文不够）：

```bash
# Step 3a: 生成逐篇蒸馏prompts
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/_batch_distill.py <person_dir> --per-file
# 生成 person_dir/_per_file_prompts/_prompt_001_xxx.md ...

# Step 3b: 在 AI 助手中逐个执行这些 prompts
# 将每个结果保存到 person_dir/_per_file_results/{filename}.json

# Step 3c: 合并逐篇蒸馏结果
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/_batch_distill.py <person_dir> --merge-per-file
# 自动合并 insights_with_evidence（相同claim的合并evidence）+ 更新蒸馏记录

# 清理（可选）
rm -rf <person_dir>/_per_file_prompts
rm -rf <person_dir>/_per_file_results
```

**逐篇蒸馏模式说明**：
- 每个素材文件生成独立的蒸馏prompt
- LLM只需要从单篇素材中提取 insights_with_evidence（不需要生成完整人格模型）
- 输出为JSON数组（只包含insights_with_evidence部分）
- 合并时自动去重（相同claim的合并evidence，避免重复）
- 适用于素材数量过多、单次LLM上下文不够的场景

### Step 4: 向量入库

**唯一脚本：`skills/think-tank/scripts/ingest_v3.py`**（通用）

```bash
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/ingest_v3.py <人物名> <人物目录>
```

- 入库前自动删除该人物的旧数据（按 `person` + `category` 双条件匹配，同名不同人不会误删）
- 所有向量 metadata 包含 `person`（人物名）和 `category`（子领域路径，如 scholars/economics）
- 两层合一：materials chunk + 7个结构化集合

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

## 当前状态（2026-04-25）

### 人员体系（108人）

| 体系 | 子领域 | 人数 |
|------|--------|------|
| 军事(military) | — | 25 |
| 学者(scholars) | economics(8), financial-engineering(9), financial-data(5), actuarial-science(5), game-theory(10), ir-theory(8), ir-security(4), middle-east(5), central-asia(4), persian-islamic(4), religious-studies(4), media-studies(5) | 67 |
| 心理学家(psychologists) | behavioral(7), cognitive(6), psychoanalysis(6), social-engineering(5), neuropsychology(3), evolutionary-psychology(1) | 28 |

### 质量检测状态
- 全部108人均已完成LLM质量评分（quality_check_llm.py + sessions_spawn）
- 规则评分脚本已废弃（batch_quality_score.py等），只允许LLM评分
- B站转录文件被误discard后已恢复

### 向量库状态
- materials: ~15,739条（⚠️ 包含评分前旧数据，需清空重建）
- insights/tension/core_profile: 需基于新素材重新蒸馏入库

---

## 项目结构

```
skills/think-tank/
├── SKILL.md                      ← 你正在这里
├── scripts/                      ← 全部流程脚本
│   ├── ingest_v3.py              ⭐ 单人入库（素材chunk+结构化），自动删除旧数据
│   ├── incremental_distill.py    ⭐ 增量蒸馏（新素材补充insights+合并evidence+蒸馏记录追踪）
│   ├── _batch_distill.py         全量蒸馏辅助（子session任务生成+--gen-record+--per-file逐篇蒸馏+--merge-per-file合并）
│   ├── _preprocess.py            素材预处理（去重+低质量过滤+质量报告）
│   ├── quality_check_llm.py      LLM质量评分（生成prompt→sessions_spawn评分→apply）
│   ├── think_tank_pipeline_v3.py ⭐ 报告生成 Pipeline v4.0（4章+预-mortem）
│   ├── cache_news.py / cache_news_tags.py  新闻缓存
│   ├── fetch_acled.py / fetch_ucdp.py / fetch_cls.py / fetch_market.py  数据抓取
│   └── historical_context.py     历史事件引擎(Redis)
└── templates/
    └── distill_prompt_v3.md      V3蒸馏模板（含逻辑溯源+情境触发）
```

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
- **质量评分只用LLM，禁止用规则评分脚本**
- **蒸馏必须分批逐篇读素材**，一次读全部会导致幻觉。每篇读完提取观点+标注source_ref，再读下一篇
- 禁止用SiliconFlow API（要钱）
- 分析必须基于RAG素材，禁止凭空编造
- source_ref 必须指向真实素材
- 每份报告末尾标注"AI模拟"
