# WORKFLOWS.md - 定型工作流

> 最后更新：2026-04-21

## 唯一工作流

```
素材采集 → 预处理(_preprocess.py) → 下载全文(scholar_download.py) → 预处理(_preprocess.py) → chunk入库(ingest_v3.py) → V3蒸馏(sessions_spawn) → 结构化入库(ingest_v3.py)
```

⚠️ **预处理必须做，不能跳过。** 素材入库前必须跑 `_preprocess.py` 清理噪音。

## 一、人格蒸馏 V3

### Step 1: 素材采集

### Step 1: 素材采集

**论文（学者/心理学家）：**
1. 元数据：`skills/think-tank/scripts/collect_new_scholars.py`（SerpAPI Google Scholar）
2. 全文下载：`skills/think-tank/scripts/batch_game_theory_collect.py`（OpenAlex→Unpaywall→Sci-Hub+Playwright）
3. 保存：`{person}/materials/paper_{title}.txt`

**网页（联参等）：**
- 优先级：`web_fetch` → `web-scraper-jina`(r.jina.ai) → `playwright-scraper`
- 保存：`materials/exa_{title}.txt` / `materials/serp_{title}.txt`
- 跳过 <500B 文件

### Step 2: 预处理

**必须执行，不可跳过。**

```bash
# 单人
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/_preprocess.py <人物目录>
# 全量
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/_preprocess.py --all
# 预览
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/_preprocess.py --all --dry-run
```

功能：删除极小文件(<200B)、纯404页面、清理导航/页脚噪音。⚠️ marxists.org 等原始文献需区分正文和网页垃圾。

### Step 3: V3 蒸馏（sessions_spawn）

用 `sessions_spawn` 启动子session，不直接调API。

```bash
# 查看缺v3的人物
cd ~/.openclaw/workspace && ~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/_batch_distill.py --list

# 生成任务prompt（调试用）
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/_batch_distill.py <人物目录> --print-task
```

子session任务：
- 模板：`skills/think-tank/templates/distill_prompt_v3.md`
- 输入：模板 + 素材（>60000字符自动分段，逐文件读取）
- 输出：`{person}/personality_v3.json`

**V3 logical_tracing 6维度：**
lineage / turning_points / grounding / collaborations / renunciations / adversaries

红线：source_ref 必须指向真实素材，不足用 `[]`

### Step 4: 向量入库

**唯一脚本：`skills/think-tank/scripts/ingest_v3.py`**（通用，所有体系）

```bash
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/ingest_v3.py <人物名> <人物目录>
```

一个脚本两层入库：
- **materials**：原始素材分800字chunk embed
- **7个结构化集合**：core_profile / insights / unconscious / repetition / transference / drive / tension

## 二、报告生成 Pipeline v5.0

```bash
~/miniconda3/envs/tt/bin/python3 skills/think-tank/scripts/think_tank_pipeline_v3.py "议题"
```

```
议题 → 子议题拆解 → 时事搜索 → RAG(按人聚合) → 框架对抗 → 条件情景树 → 预-mortem → 拼接
```

**v5改动（心理学家认知评审共识）：**
- 去概率数字，改为条件判断树（若X→则Y）
- 红蓝对抗→框架对抗（分析框架的对抗，不是人的对抗）
- 4章: 因果图+历史类比 | 框架对抗+关键分歧 | 认知偏差审计(带方向) | 条件情景树+尾部风险
- 引用格式: 来源+原文+适用边界+因果连接，禁[RAG: XX]标签

**v4.2 RAG架构：**
- RAG按学者分组（profile+insights+tensions+materials），insights不截断
- tension独立集合，关联表source_ref→material_id

## 三、人员体系

| 体系 | 子领域 | 人数 |
|------|--------|------|
| 军事(military) | — | 25 |
| 学者(scholars) | game-theory(10), economics(8), ir-theory(8), middle-east(5), central-asia(4), ir-security(4), persian-islamic(4), religious-studies(4) | 47 |
| 心理学家(psychologists) | behavioral(7), cognitive(6), psychoanalysis(6), social-engineering(5) | 24 |

注：联参体系已独立管理；designers体系已删除。

## 四、环境

- **Python**: `~/miniconda3/envs/tt/bin/python3`（3.13 + chromadb 1.5.8 + playwright + PyMuPDF）
- **Chromium**: `/snap/bin/chromium`
- **Embedding**: Ollama bge-m3 `http://172.26.0.1:11434/api/embed`（1024维）
- **向量库**: `/mnt/d/data/think-tank-data/_chromadb/lacan_db/`

## 五、已废弃

| 项目 | 原因 |
|------|------|
| `distill_v2.py`, `_extract_one.py`, `incremental_vecdb.py` | 不存在，被sessions_spawn + ingest_v3.py替代 |
| `batch_scholar_papers.py`, `scholar_download.py` | 不存在，已被 collect_new_scholars.py + batch_game_theory_collect.py 替代 |
| `designers`体系 | 已删除，不符合项目需求 |
| 联参在智库中 | 已独立管理 |
| Semantic Scholar | 429限流 |
| SiliconFlow | 要钱，禁止 |
| 本地脚本调智谱API | 改用sessions_spawn |
