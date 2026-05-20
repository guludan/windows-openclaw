---
name: think-tank
description: "多视角人格智库系统（统一pipeline）。学者+心理学家，v4偏差审计。采集→蒸馏→入库→RAG报告（4章+预-mortem）。触发词：智库讨论、人格蒸馏、人格还原、战略报告、pipeline、逻辑溯源。"
metadata: {"openclaw":{"emoji":"🏛️","os":["linux","darwin","win32"]}}
---

# 🏛️ 人格智库系统

核心理念：**独立思考，真实还原**。每位人物保持独立视角，忠实于其真实立场。

## 唯一工作流

```
素材采集 → init_db.py → 预处理(_preprocess.py --quality) → LLM质量评分(quality_check_llm.py) → V3蒸馏(sessions_spawn逐篇) → 合并(--merge-per-file) → regenerate_personality.py → 入库(ingest_v3.py)
```

⚠️ **预处理必须做，不能跳过。**

⚠️ **报告生成 ≠ 人格蒸馏入库，是两个独立流程。**

| 工作流 | 目的 | 入口 | 产出 |
|--------|------|------|------|
| **A. 人格蒸馏入库** | 素材→蒸馏→向量入库 | sessions_spawn + `ingest_v3.py` | LanceDB 6集合 |
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

⚠️ **搜索关键词用全名+领域词**：常见名（Chris Miller、Nate Silver、Juan Zarate等）搜索时必须加领域关键词（如 "semiconductor"、"forecasting"、"terrorist financing"），否则搜出来全是同名不同人。

⚠️ **libgen著作下载必须验证内容**：libgen搜索只按标题匹配不带作者，同名书很多。下载后必须检查内容是否为正确作者的著作。已验证的错误案例：Flashpoints(Friedman)、Next 100 Years(Friedman)、J Curve(Bremmer)、Crisis Economics(Roubini)、Disunited Nations(Zeihan)、Active Measures(Rid)、Treasury War(Zarate)——下载到的全是无关论文或其他作者的书。

**网页：**
- 优先级：`web_fetch` → `web-scraper-jina` → `playwright-scraper`
- 跳过 <500B 文件

⚠️ **论文元数据：文件名必须包含论文标题，有DOI时加DOI后缀。入库时自动提取title和doi存入向量库metadata，按DOI去重。**

⚠️ **搜索渠道：用data_search模块，中英文分开搜。采集脚本用collect_via_datasearch.py（基于data_search模块）。禁止自己重写搜索脚本。**
- Sci-Hub/任何外部网站**禁止批量循环下载**，必须逐篇下载，每次间隔**60秒+**
- Exa/Tavily等API每次请求间隔**5-10秒**
- 违反会封IP，后果严重

### Step 2a: 素材注册（init_db）

```bash
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/init_db.py <person_dir> [--reset]
```

- 扫描 `materials/`（根目录+raw子目录）和 `papers/`（raw子目录+pdfs）
- 注册素材元数据到 `_db.sqlite`（文件名、大小、字数、状态等）
- `--reset` 删除旧库重建；不加则只注册新文件
- 排除 `_` 开头文件和 `_discarded/` 目录
- ⚠️ **init_db是质量评分和蒸馏的前提**，未注册的素材不会被处理

### Step 2b: 预处理 + 质量检测（必须两步都做，不能只跑预处理）

```bash
# Step 2a: 预处理 + 机械去重 + 低质量过滤 + 生成报告
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/_preprocess.py <person_dir> --quality
```

⚠️ **--quality 是必须参数，不要省略。** 只跑预处理不做质量检测等于没做。

```bash
# Step 2b: LLM质量评分（sessions_spawn调LLM，batch_size=5）
# 1. 生成评分prompt（batch_size=5，已内置）
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/quality_check_llm.py <person_dir>
# 生成 _quality_prompt_0.md, _quality_prompt_1.md ...（每批5篇，最大~70KB）

# 2. spawn子session评分（5并发，当前可用模型）
# - ≤3个prompt：1个子session读所有prompt并评分
# - >3个prompt：按3个一批分成多个子session（避免context overflow）
# - 子session任务：read prompt文件 → 评分JSON → 写入 _quality_scores.json → exec --apply-from-file
# - gateway超时率~50%，spawn失败后立即重试
# - 每个子session完成后立刻spawn下一批，保持流水线
# - **10并发**：当前可用模型

# 3. apply（子session内自动执行）
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/quality_check_llm.py <person_dir> --apply-from-file
```

⚠️ **预处理和质量检测是绑定流程，采集完素材后必须执行完整流程。**
⚠️ **batch_size=5是经过验证的值**：20→200KB prompt（太大），5→70KB（子session可处理）
⚠️ **不要拆分split文件**：子session可以读多个prompt文件，只需按数量分session即可
⚠️ **每子session最多3个prompt**：>3个会context overflow，这是反复验证的结论

### Step 3: V3 蒸馏（逐篇模式，唯一模式）

⚠️ **蒸馏必须逐篇处理，禁止一次性塞给LLM。** 素材量大时会超时或幻觉。

⚠️ **一个人物一个子session，禁止多人塞进一个子session。** 多人素材总量一定会超出子session上下文限制。

⚠️ **超大型素材（含数本著作，总字符超过10万）需要分批多个子session处理。** 一个子session处理不完时，按素材文件分批，每批一个子session，全部完成后再合并。

#### Chunk切分机制

逐篇模式下，`--per-file` 会为每个素材文件生成蒸馏prompt：
- 单篇 ≤ **15000字符**：生成1个prompt，整篇喂给LLM
- 单篇 > **15000字符**：按段落边界自动切分成多个chunk（每chunk ≤15000字符），相邻chunk有2段重叠保持上下文连贯。每个chunk生成1个独立prompt
- 所以"逐篇"本质上是"逐chunk"——一个素材文件可能产生多个prompt

#### 全量蒸馏（首次）

```bash
# Step 3a: 生成逐篇蒸馏prompts（从数据库读取非废弃素材）
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/_batch_distill.py <person_dir> --per-file
# → 从 _db.sqlite 查询 status != 'discarded' 的素材，生成 _per_file_prompts/
# ⚠️ 必须在质量评分完成后执行，否则会包含废弃素材

# Step 3b: 逐个spawn子session处理每个prompt
# 每个子session只处理1个prompt（1个chunk），提取 insights_with_evidence
# 结果保存到 _per_file_results/{filename}.json 或 {filename}_chunk{N}.json
# ⚠️ 一个人物一个子session（内部逐chunk处理）
# ⚠️ 超过10万字符的人物需分批：先处理第1批prompts → 再开新子session处理第2批

# Step 3c: 全部prompt处理完成后合并
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/_batch_distill.py <person_dir> --merge-per-file
# 自动合并 insights_with_evidence（相同claim的合并evidence）+ 更新蒸馏记录
```

#### 增量蒸馏（补充新素材后）

```bash
# 自动检测未蒸馏的新素材，生成逐篇prompts
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/incremental_distill.py <person_dir> --auto
# → 生成 _per_file_prompts/_prompt_inc_{index}_{filename}.md（包含已有insights摘要避免重复）

# 逐个spawn子session处理，结果保存到 _per_file_results/{filename}.json

# 应用增量结果
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/incremental_distill.py <person_dir> --apply
```

#### 蒸馏注意事项

- 每次蒸馏完成后自动记录 `_distilled_files.json`（已蒸馏的素材文件列表）
- 逐篇蒸馏的JSON结果保留在 `_per_file_results/` 目录，**不要删除**（用于溯源和灾备）
- LLM只需从单chunk素材中提取 insights_with_evidence（不需要生成完整人格模型）
- 输出为JSON数组（只包含insights_with_evidence部分）
- 合并时自动去重（相同claim的合并evidence，避免重复）
- **素材可能包含大量口水话/闲聊/无关内容**，LLM必须实事求是，有观点才提取，没有就输出空数组，禁止为了凑数而编造观点

#### 提示词防幻觉要求

- **excerpt必须是原文逐字摘录**，禁止转述、改写、概括后冒充原文
- **claim必须基于素材中的实际论断**，禁止推断、延伸、脑补
- 素材中只有描述没有观点时，不应强行提取insight
- 输出空数组 `[]` 是合法且正确的，比编造一条假的insight好一百倍

### Step 4: 向量入库

**唯一脚本：`skills/think-tank/scripts/ingest_v3.py`**（通用）

```bash
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/ingest_v3.py <人物名> <人物目录>
```

- 入库前自动删除该人物的旧数据（按 `person` + `category` 双条件匹配，同名不同人不会误删）
- 所有向量 metadata 包含 `person`（人物名）和 `category`（子领域路径，如 scholars/economics）
- 两层合一：materials chunk + 7个结构化集合

### Step 4b: 重新生成完整人格模型（基于insights）

逐篇蒸馏只生成 insights_with_evidence，顶层字段（brief, school, domains, logical_tracing等）可能过时。合并后需要重新生成：

**两阶段处理（insights > 100时自动启用）：**
- **阶段1（本地压缩）**：按dim分组 → claim关键词重叠度聚类（阈值0.55）→ 每簇选最长claim为代表 → 压缩到~90条
- **阶段2（LLM生成）**：用压缩后的insights一次性生成完整人格模型（insights ≤100时跳过阶段1）

**为什么需要两阶段：** insights超过100条时prompt会超过20KB（毛泽东615条=88KB），超出LLM有效处理范围。压缩后避免了分批处理导致的字段合并冲突问题。

**regenerate会做归纳合并**：LLM将同一论点的多条insight合并，输出 `merged_from` 索引，代码自动合并原始evidence。目标压缩到30-60%，evidence总量不变。

#### 三步流程

**Step 4b-1: 生成prompt**（代码层，不调LLM）
```bash
# 单人
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/regenerate_personality.py <person_dir>
# 批量（所有有v3的人物）
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/regenerate_personality.py --all
```
- 输入：`personality_v3.json` 中的 insights_with_evidence
- insights > 100：自动本地压缩（按dim分组+关键词聚类），保存 `_regenerate_merge_map.json` 供apply展开
- 输出：`_regenerate_prompt.md`（包含insights摘要，**不含excerpt**，控制prompt大小）

**Step 4b-2: spawn子session调LLM**
```
spawn子session → 读 _regenerate_prompt.md → 输出 _regenerate_result.json
```
- LLM任务：①归纳合并相似insights ②生成顶层字段（brief, school, domains等）
- LLM输出格式：每条insight包含 `merged_from` 数组（原始insight的index）
- ⚠️ `_regenerate_result.json` 是**中间产物**，不是最终人格文件，不含完整evidence

**Step 4b-3: apply合并**
```bash
# 单人
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/regenerate_personality.py <person_dir> --apply
# 批量
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/regenerate_personality.py --all --apply
```
- 读取 `_regenerate_result.json`（LLM的合并方案）
- 根据 `merged_from` 索引，从原始 `personality_v3.json` 中提取完整evidence合并到对应insight
- 两阶段模式：自动展开压缩映射，确保所有原始insights被覆盖
- 遗漏的原始insight自动原样保留
- 旧文件自动备份为 `personality_v3.json.bak.{timestamp}`
- 输出：更新后的 `personality_v3.json`（最终人格文件）
- 清理临时文件（prompt、result、merge_map）

#### 数据流图
```
personality_v3.json (原始，含insights+evidence)
        ↓ generate_prompt
_regenerate_prompt.md (insights摘要，不含evidence)
        ↓ spawn LLM
_regenerate_result.json (中间产物，含merged_from索引，不含完整evidence)
        ↓ --apply (用merged_from从原始文件提取evidence)
personality_v3.json (最终，合并后insights+完整evidence+新生成的顶层字段)
```

### 批量蒸馏流程

1. 生成prompts: `auto_distill.py --generate`（或逐人 `--per-file`）
2. **每人一个子session**（内部逐chunk处理所有素材），超时设2-4小时
3. 超过10万字符的人物需分多个子session分批处理
4. 完成后合并: `_batch_distill.py --merge-per-file`
5. 重新生成人格模型: `regenerate_personality.py`
6. 入库: `ingest_v3.py`

⚠️ **已知问题：大量论文素材只有元数据（标题/DOI）没有正文**，采集时论文只抓了索引没下载全文。这些素材蒸馏出来是空的，需要补下载全文。

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

## 当前状态（2026-04-30）

### 人员体系（150人）

| 体系 | 子领域 | 人数 |
|------|--------|------|
| 军事(military) | — | 25 |
| 学者(scholars) | economics(8), financial-engineering(9), financial-data(5), actuarial-science(5), game-theory(5), ir-theory(8), ir-security(4), middle-east(5), central-asia(4), persian-islamic(4), religious-studies(4), media-studies(5), geopolitical-finance(4), sanctions-finance(3), information-warfare(3), indo-pacific(3), tech-geopolitics(2), political-forecasting(2) | 83 |
| 心理学家(psychologists) | behavioral(7), cognitive(6), psychoanalysis(6), social-engineering(5), neuropsychology(4), evolutionary-psychology(2) | 30 |

### 质量检测状态
- **全部150人已完成LLM质量评分** ✅ (Total: 3922, Kept: 3055, Discarded: 867)
- 规则评分脚本已废弃（batch_quality_score.py等），只允许LLM评分
- batch_size=8, preview 800字符, 每子session最多3个prompt

### 数据清理状态（2026-04-30）
- materials/根目录完全清零（所有素材在raw/或_discarded/里）
- json文件已全部清理（元数据→_search_cache/, 有正文→转md）
- init_db不再注册.json文件
- 文件名特殊字符已sanitize

### 向量库状态
- materials: ~15,739条（⚠️ 包含评分前旧数据，需清空重建）
- insights/tension/core_profile: 需基于新素材重新蒸馏入库
- 蒸馏进度：质检完成后待开始

---

## 项目结构

```
skills/think-tank/
├── SKILL.md                      ← 你正在这里
├── scripts/                      ← 全部流程脚本
│   ├── init_db.py                素材注册（扫描materials/raw/和papers/raw/，只注册.md/.txt）
│   ├── _preprocess.py            素材预处理（去重+低质量过滤+质量报告）
│   ├── quality_check_llm.py      LLM质量评分（生成prompt→sessions_spawn评分→apply）
│   ├── _batch_distill.py         全量蒸馏（--per-file逐篇蒸馏+--merge-per-file合并）
│   ├── _distill_utils.py         蒸馏工具函数（共用）
│   ├── incremental_distill.py    ⭐ 增量蒸馏（新素材补充insights+合并evidence）
│   ├── regenerate_personality.py 基于insights重新生成完整人格模型（多批次）
│   ├── ingest_v3.py              ⭐ 单人入库（素材chunk+结构化），自动删除旧数据
│   ├── auto_distill.py           批量自动蒸馏（管理多人流水线）
│   ├── fetch_papers.py           论文搜索+下载+PDF提取（SS+arXiv→Sci-Hub→PyMuPDF）
│   ├── think_tank_pipeline_v3.py ⭐ 报告生成 Pipeline v4.0（4章+预-mortem）
│   ├── cache_news.py             时事采集→Redis缓存
│   ├── cache_news_tags.py        时事Tag索引系统
│   ├── fetch_acled.py            ACLED冲突数据采集
│   ├── fetch_cls.py              财联社快讯采集（Playwright绕WAF）
│   ├── fetch_market.py           市场数据采集
│   ├── fetch_ucdp.py             UCDP冲突数据采集
│   └── historical_context.py     历史参照长效化引擎
├── templates/
│   ├── distill_prompt_v3.md      V3蒸馏模板（含逻辑溯源+情境触发）
│   ├── quality_score_prompt.md   LLM质量评分模板（精简版，~350B）
│   ├── regenerate_from_insights.md 单批次重新生成人格模板
│   ├── regenerate_batch_dim.md   多批次按dim合并模板
│   └── regenerate_summary.md     多批次汇总模板（顶层字段）
└── docs/
    ├── DIRECTORY_SPEC.md         目录结构规范
    └── pipeline.md               Pipeline说明
```

数据根目录: /mnt/d/data/think-tank-data/（D盘，仅数据文件）
脚本目录: skills/think-tank/scripts/（已上GitHub，不含数据）
├── _lacandb/lacan_db/          ← 向量库（LanceDB，严禁并发写入）
├── personalities/               ← 人物素材+蒸馏（154人：25军+83学者+45心理）
└── reports/                     ← 智库报告
```

每个人物目录结构：
```
person_name/
├── _db.sqlite                          ← 素材元数据（状态追踪）
├── _distilled_files.json               ← 已蒸馏素材记录
├── personality_v3.json                 ← 蒸馏产出的人格模型（最终结果）
├── meta.json                           ← 人物元信息
├── _per_file_prompts/                  ← 蒸馏prompt（逐篇生成）
├── _per_file_results/                  ← ⭐ 蒸馏结果（唯一存放位置）
├── materials/
│   ├── raw/                            ← ✅ 素材来源①：网页采集素材（.md/.txt）
│   ├── _discarded/                     ← ❌ 质量未通过的素材
│   ├── _duplicates/                    ← 重复素材
│   ├── _search_cache/                  ← 搜索结果缓存（.json）
│   ├── _quality_scores.json            ← 质量评分结果
│   ├── _quality_report.json            ← 质检报告
│   └── _quality_prompt_*.md            ← 评分prompt
└── papers/
    ├── raw/                            ← ✅ 素材来源②：论文全文提取文本（.md）
    ├── pdfs/                           ← 论文PDF原件
    └── _meta.json                      ← 论文元数据
```

⚠️ **素材统计必须合计两处**：`materials/raw/` + `papers/raw/`，只算一处会严重低估素材量。

## 红线

- **LLM调用必须走sessions_spawn**，禁止直接调API
- **质量评分只用LLM，禁止用规则评分脚本**
- **蒸馏必须逐篇逐chunk处理**，一次读全部会导致幻觉。每篇读完提取观点+标注source_ref，再读下一篇
- **`_batch_distill.py --per-file` 从数据库查询非废弃素材**（status != 'discarded'），必须在Step 2b质量评分完成后执行
- **质量评分是5分制(1-5)**，用 `action=keep/discard` 和数据库 `status` 字段判断，不是 score>=6
- **论文下载必须验证作者身份**：同名不同人问题严重（hull=船壳、silver=金属），下载后必须检查内容是否为正确作者
- **一个人物一个子session**，多人塞一个session必定超出上下文
- **超过10万字符素材需分多个子session分批处理**
- **蒸馏必须实事求是**，有观点才提取，没观点输出空数组，禁止编造
- 禁止用SiliconFlow API（要钱）
- 分析必须基于RAG素材，禁止凭空编造
- source_ref 必须指向真实素材
- 每份报告末尾标注"AI模拟"


## 🆕 新V3流程（2026-05-15更新）

**弃用旧v3**：不再从旧 `personality_v3.json` 读取合并。所有人物统一走新流程：

### Step 0: Merge（生成空壳v3）
```bash
rm -f <person_dir>/personality_v3.json
python3 _batch_distill.py <person_dir> --merge-per-file
```
- 只从 `_per_file_results/` 读insights，顶层字段留空
- 过滤无evidence的insight，自动标准化dim名称

### Step 1+2: 去重+顶层字段生成
```bash
python3 batch_dedup_regen.py <person_dir>
python3 batch_dedup_regen.py --people "name1,name2"
python3 batch_dedup_regen.py --all --force
```
- Step 1: 按dim分组, LLM语义去重合并claims, evidence程序级合并
- Step 2: 基于去重后的insights+evidence, LLM生成顶层字段

### 选项
- `--batch-size 40` 每批insights数
- `--dedup-only` / `--regen-only` 分步执行
- `--force` 强制重新处理

