# 素材采集到入库 — 完整流程文档

## 流程概览

```
① 素材采集(data_search.py) → ② 注册(init_db.py) → ③ 预处理(_preprocess.py --quality) → ④ LLM质量评分(quality_check_llm.py) → ⑤ 蒸馏(_batch_distill.py --per-file) → ⑥ 合并(--merge-per-file) → ⑦ 重新生成(regenerate_personality.py) → ⑧ 入库(ingest_v3.py)
```

## 模板文件（skills/think-tank/templates/）

| 模板 | 用途 |
|------|------|
| `distill_prompt_v3.md` | 蒸馏模板（人格模型JSON结构+逻辑溯源提取指南） |
| `quality_score_prompt.md` | LLM质量评分prompt模板（精简版~350B，5分制+判断维度） |
| `regenerate_from_insights.md` | 基于insights重新生成人格模型模板 |

⚠️ 模板独立保存，脚本从文件读取，不硬编码。修改模板不需要改代码。

---

## ① 素材采集 — `data_search.py`

**位置**: `skills/data-search/scripts/data_search.py`

### 普通搜索

```bash
# 多源搜索
python3 data_search.py search "中美贸易战" --layers general,chinese,academic --max 5

# 学术搜索（可下载PDF）
python3 data_search.py academic "game theory" --source arxiv --download --output-dir ./pdfs

# 按DOI下载
python3 data_search.py download --doi 10.1038/xxx --output-dir ./pdfs
```

### 人物搜索（核心功能）

```bash
# 自动中英文双语搜索
python3 data_search.py person "林毅夫"
python3 data_search.py person "Paul Krugman" --output-dir /path/to/person_dir
```

**工作原理**：
1. 读取 `name_map.json`，解析输入姓名的中英文变体
2. 用每个名称变体分别调用 `search()` 搜索
3. 合并去重，按所有名称变体评分取最高分
4. **自动保存**：搜索缓存→`_search_cache/`，有正文的结果→`materials/raw/`（.md格式）

**新人物（不在映射中）**：
- 主session启动子session调LLM搜索推断中英文名（`resolve_new_person()`生成任务prompt）
- 子session返回中英文名+所属领域
- 自动更新 `name_map.json`
- 然后再用 `search_person()` 正常搜索

### 名称映射文件

**位置**: `/mnt/d/data/think-tank-data/personalities/name_map.json`

```json
{
  "林毅夫": {"en": "Justin Yifu Lin", "group": "scholars/economics"},
  "Paul_Krugman": {"en": "Paul Krugman", "group": "scholars/economics"},
  "切格瓦拉_Che_Guevara": {"zh": "切格瓦拉", "en": "Che Guevara", "group": "military"}
}
```

**新增人物时**：往这个JSON里加一条记录即可。

### 四层搜索架构

| 层 | 数据源 | 用途 |
|----|--------|------|
| L1 通用 | SearXNG/DuckDuckGo/Exa/Tavily/SerpAPI（并行） | 国际新闻、英文资讯 |
| L2 中文 | 知乎/搜狗/头条/秘塔(Playwright)/财联社 | 中文深度内容 |
| L3 学术 | arXiv/Semantic Scholar/PubMed | 论文、学术引用 |
| L4 宏观 | World Bank/AKShare/FRED | GDP/CPI/汇率等宏观数据 |

---

## ② 注册 — `init_db.py`

**位置**: `skills/think-tank/scripts/init_db.py`

```bash
python3 init_db.py <person_dir> [--reset]
```

- 扫描 `materials/raw/` 和 `papers/raw/` 下的 `.md`/`.txt` 文件
- **不注册 `.json` 文件**
- 排除 `_` 开头文件、`_discarded/`、`_duplicates/`、`_search_cache/` 目录
- 自动 Jaccard 去重（>0.85 移到 `_duplicates/`）

---

## ③ 预处理 — `_preprocess.py`

**位置**: `skills/think-tank/scripts/_preprocess.py`

```bash
# 处理单人物
python3 _preprocess.py /mnt/d/data/think-tank-data/personalities/scholars/economics/林毅夫

# 处理所有人物
python3 _preprocess.py --all

# 预览不写入
python3 _preprocess.py <person_dir> --dry-run
```

**功能**：
1. 删<200B极小文件 / 删404错误页 / 清理HTML导航噪音
2. Jaccard相似度>0.85的移到 `_duplicates/`
3. 有效文本<30%或纯索引页移到 `_low_quality/`
4. 生成 `_quality_report.json`（每篇素材的元信息+预览+来源类型）

**带质量检测的完整pipeline**：
```bash
python3 _preprocess.py <person_dir> --quality   # 去重+低质量检测+生成质量报告
```

---

## ③ LLM质量评分 — `quality_check_llm.py`

**位置**: `skills/think-tank/scripts/quality_check_llm.py`

### 步骤

```bash
# Step 1: 先跑预处理生成质量报告（如果还没跑）
python3 _preprocess.py <person_dir> --quality

# Step 2: 生成分批评分prompt
python3 quality_check_llm.py <person_dir>

# Step 3: 用sessions_spawn调GLM评分每批prompt
# （主session通过sessions_spawn调子agent处理每个_quality_prompt_N.md）

# Step 4: 应用评分结果
python3 quality_check_llm.py <person_dir> --apply '<json_array>'
# 或从文件应用
python3 quality_check_llm.py <person_dir> --apply-from-file
```

### 工作原理

1. 读 `_quality_report.json`，按每8篇分批生成 `_quality_prompt_N.md`（每prompt≤10KB）
2. **自动跳过已评分文件**：读 `_quality_scores.json`，已评分的不重复评分
3. **自动注入人物别名**：从 `name_map.json` 读取中英文名，在prompt中标注"所有别名都算相关"
4. LLM按5分制评分（1=无关，5=高质量），≤2分标记discard
5. discard的素材移到 `_discarded/`，评分保存到 `_quality_scores.json`
6. **discard>0时自动重新生成report和prompt**（防止幽灵文件）

### 评分标准

| 分数 | 含义 | 动作 |
|------|------|------|
| 5 | 高质量论文全文/核心著作 | keep |
| 4 | 有实质内容的文章/访谈 | keep |
| 3 | 一般性介绍/概述 | keep |
| 2 | 重复/转载/信息量低 | discard |
| 1 | 无关/广告/错误页面 | discard |

### 为什么用LLM不用规则

规则评分无法：
- 区分同名不同人（如"张伟"可能是任何张伟）
- 判断内容是否真正相关（标题可能只是提到名字）
- 评估信息密度和学术价值

---

## ④ 蒸馏 — `_batch_distill.py` + `distill_prompt_v3.md`

### 素材来源

蒸馏脚本 `_batch_distill.py` 从 `materials/raw/` 和 `papers/raw/` 读取素材文件。

### 查看待蒸馏

```bash
python3 _batch_distill.py --list
```

### 蒸馏单个人物

通过 `sessions_spawn` 调子agent，任务prompt参考 `_batch_distill.py --print-task` 的输出。

**核心输出字段**：
- `insights_with_evidence`：15-30个观点，每个配原文excerpt（最重要）
- `logical_tracing`：思想谱系（继承/改造/断裂/融合）+ 转折点
- `situational_triggers`：情境触发条件矩阵

### 约束

- 素材>60000字符自动分段
- **逐篇读素材，不能一次全读**（会幻觉）
- excerpt必须是原文，一字不改
- 输出合法JSON，用Python脚本写文件（不用write工具写大JSON）

---

## ⑤ 入库 — `ingest_v3.py`

**位置**: `skills/think-tank/scripts/ingest_v3.py`

```bash
# 入库单人物
python3 ingest_v3.py <person_name> <person_dir>

# 入库全部
python3 ingest_v3.py --all
```

### 向量库集合（lacan_db）

| 集合 | 内容 | 来源 |
|------|------|------|
| core_profile | 人格摘要（1条/人） | brief+school+domains |
| insights | 观点（按dim分类） | stance/cognitive/framework/decision/logical_tracing |
| evidence | 原文证据片段 | insights_with_evidence.evidence |
| tension | 思想内在矛盾 | tension相关片段 |

### 关联机制

```
insight.metadata.evidence_ids → [ev_001, ev_002, ...]
evidence.metadata.insight_id → "ins_003"
evidence.metadata.source_file → 磁盘上的txt路径
```

### Embedding

- 模型：Ollama bge-m3（1024维）
- 地址：`http://172.26.0.1:11434/api/embed`
- 批量：8条一组

### ⚠️ 严禁并发写入 LanceDB

LanceDB 不支持并发写入，必须串行。

---

## 新人物完整操作清单

假设要添加新学者 "张三"：

1. **搜索人物**: `search_person("张三")` → 自动检测不在映射中
2. **推断别名**: 调 `resolve_new_person("张三")` 生成任务prompt → `sessions_spawn` → 子session搜索推断中英文名
3. **更新映射**: `update_name_map(result)` 自动写入 `name_map.json`
4. **创建目录**: `mkdir -p /mnt/d/data/think-tank-data/personalities/scholars/xxx/Zhang_San/materials`
5. **采集素材**: `search_person("张三", output_dir=...)` 双语搜索保存
5. **注册**: `python3 init_db.py <person_dir>`
6. **预处理**: `python3 _preprocess.py <person_dir> --quality`
7. **质量评分**: `python3 quality_check_llm.py <person_dir>` → sessions_spawn → apply
8. **蒸馏**: `python3 _batch_distill.py <person_dir> --per-file` → sessions_spawn逐篇 → `--merge-per-file`
9. **重新生成**: `python3 regenerate_personality.py <person_dir>` → sessions_spawn → `--apply`
10. **入库**: `python3 ingest_v3.py <person_name> <person_dir>`

---

## 文件结构

```
/mnt/d/data/think-tank-data/personalities/
├── name_map.json                          # 150人中英文映射
├── scholars/
│   └── economics/
│       ├── 林毅夫/
│       │   ├── _db.sqlite                # 素材元数据
│       │   ├── personality_v3.json       # 蒸馏结果
│       │   ├── materials/
│       │   │   ├── raw/                  # ✅ 质量通过的素材（.md/.txt）
│       │   │   ├── _discarded/           # ❌ 评分丢弃
│       │   │   ├── _search_cache/        # 搜索缓存（.json）
│       │   │   ├── _quality_scores.json  # LLM评分结果
│       │   │   └── _quality_report.json   # 质检报告
│       │   └── papers/
│       │       ├── raw/                  # 论文文本（.md）
│       │       ├── pdfs/                 # PDF原件
│       │       └── _meta.json            # 论文元数据
│       └── Paul_Krugman/
└── ...

skills/data-search/scripts/
└── data_search.py                         # 搜索模块（含person子命令）

skills/think-tank/scripts/
├── init_db.py                         # 素材注册
├── _preprocess.py                     # 预处理
├── quality_check_llm.py               # LLM质量评分
├── _batch_distill.py                  # 蒸馏辅助
├── incremental_distill.py             # 增量蒸馏
├── regenerate_personality.py          # 重新生成人格模型
├── ingest_v3.py                       # 入库
├── fetch_papers.py                    # 论文搜索+下载
└── think_tank_pipeline_v3.py          # 报告生成

skills/think-tank/templates/
├── distill_prompt_v3.md               # 蒸馏模板
├── quality_score_prompt.md            # 质量评分模板
└── regenerate_from_insights.md        # 重新生成模板
```
