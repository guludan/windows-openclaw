# MEMORY.md - 小狗蛋的长期记忆

## 黄老板
- 称呼：黄老板
- 项目方向：① **人格智库** (think-tank) ② **人格还原** (person-replication)
- GitHub：guludan

## 智库项目（2026-04-27）

### 数据目录
- 数据根：`/mnt/d/data/think-tank-data/`
- 向量库：`_chromadb/lacan_db/`（89MB，133人已入库）
- 报告：`reports/`
- 人物映射：`personalities/name_map.json`（148人）

### 人员：150人
- military 25 + psychologists 31 + scholars 77（含17新增）
- 蒸馏进度：133/133 V3 ✅，17新增人待蒸馏
- 部分人素材不足，正在补采集中

### 新增分类（2026-04-26）
- geopolitical-finance: Bremmer, Roubini, Friedman, Zeihan
- sanctions-finance: Zarate, Drezner, Economy
- information-warfare: DiResta, Rid, Singer
- indo-pacific: Mastro, Bush, Campbell
- tech-geopolitics: Miller, Mazzucato
- political-forecasting: Tetlock, Silver

## 📦 模块索引（任务→模块→函数 映射）

**每次执行任务前，先查这张表，找到对应模块，再读SKILL.md。禁止凭记忆瞎搞。**

| 任务 | 脚本 | 关键函数/命令 | SKILL.md |
|------|------|--------------|----------|
| 通用搜索/新闻/中文搜索 | `skills/data-search/scripts/data_search.py` | `search()`, `search_general()`, `search_chinese()` | skills/data-search/SKILL.md |
| 学术论文搜索（arXiv/SS/PubMed） | 同上 | `search_academic()`, `_search_semantic_scholar()`, `_search_arxiv()` | 同上 |
| 论文全文下载（Sci-Hub） | 同上 | `download_scihub_pdf(doi, output_dir)`, `download_papers()` | 同上 |
| 著作下载（libgen.com.im→archive.org） | 同上 | `download_book(title, output_dir, isbn, proxies)` | 同上 |
| 素材质检（LLM，蒸馏前） | `skills/think-tank/scripts/quality_check_llm.py` + `_preprocess.py` | `--quality`生成报告→生成prompt→GLM评分→`--apply`清理 | skills/think-tank/SKILL.md |
| DOI搜索→全文下载完整链路 | 同上 | SS搜索拿DOI → `download_scihub_pdf(doi)` → PyMuPDF提文本 | 同上 |
| B站视频采集+转录 | `skills/data-search/scripts/bilibili_transcribe.py` | `search --keyword`, `download --bv` | 同上 |
| 人物素材采集 | `skills/data-search/scripts/data_search.py` | CLI: `person "姓名"` | 同上 |
| 入库（向量库） | `skills/think-tank/scripts/ingest_v3.py` | 入库4个集合 | skills/think-tank/SKILL.md |
| 全量蒸馏 | `skills/think-tank/scripts/_batch_distill.py` | `--per-file` 逐篇 | 同上 |
| 增量蒸馏 | `skills/think-tank/scripts/incremental_distill.py` | `--auto` 检测新素材 | 同上 |
| 报告生成 | `skills/think-tank/scripts/think_tank_pipeline_v3.py` | | 同上 |
| PDF文本提取 | `skills/extract-pdf-text/` | | skills/extract-pdf-text/SKILL.md |
| 知乎爬取 | `skills/zhihu-keyword-content-search/` | | skills/zhihu-keyword-content-search/SKILL.md |
| 网站抓取（绕CF） | `skills/web-scraper-jina/` 或 `skills/agent-stealth-scraper/` | | 对应SKILL.md |

## 🔄 完整管线流程映射

```
Phase 1: 素材采集
  ├─ data_search.py person "全名+领域词" → 通用+中文+学术搜索 → materials/
  ├─ data_search.py academic "姓名" → SS搜论文拿DOI → papers/raw/
  ├─ download_scihub_pdf(doi) → Sci-Hub下载PDF → PyMuPDF提文本
  ├─ download_book(title, dir, proxies) → libgen.li→libgen.com.im→archive.org → materials/book_书名.md + materials/books/书名.pdf
  ├─ bilibili_transcribe.py → B站视频转文本 → materials/
  └─ 著作: 通用搜索找书名 → download_book() → materials/book_书名.md
  ⚠️ libgen下载后必须验证内容是否正确作者的著作（同名书很多）
  ⚠️ 常见名搜索必须加领域关键词（如"Christopher Miller semiconductor"）

Phase 2: 注册+质检
  ├─ init_db.py --person → 扫描materials/+papers/ → _db.sqlite
  ├─ _preprocess.py --quality → 去重+低质量检测+报告
  └─ quality_check_llm.py → 生成prompt → spawn GLM评分 → --apply 清理低分素材

Phase 3: 蒸馏（逐篇模式，唯一模式）
  ├─ _batch_distill.py <person_dir> --per-file → 预生成所有prompt到 _per_file_prompts/
  ├─ 大素材(>15000字符)自动按段落切chunk，每chunk≤15000字符，相邻chunk 2段重叠
  ├─ 手动逐个sessions_spawn子session，每个子session读1个prompt处理1个chunk
  ├─ ⚠️ 一人一个子session，禁止多人塞一个
  ├─ ⚠️ 超过10万字符就需分多个子session分批处理
  ├─ 结果保存到 _per_file_results/
  ├─ incremental_distill.py --auto → 增量蒸馏（检测新素材）
  ├─ --merge-per-file → 合并所有chunk的insights（相同claim合并evidence）
  └─ regenerate_personality.py → 基于insights重新生成顶层字段
  └─ 输出: personality_v3.json

Phase 4: 人格完善
  └─ regenerate_personality.py → 基于insights重新生成顶层字段

Phase 5: 入库
  └─ ingest_v3.py → chromadb（严禁并发）

Phase 6: 报告
  └─ think_tank_pipeline_v3.py
```

### 批量操作脚本（⚠️ 严格控制频率，避免被封）
- **补论文全文**：`scripts/fix_small_papers.py`（遍历小文件→提取DOI→Sci-Hub下载→fitz提文本，15秒间隔）
- **素材采集**：逐人手动调 `data_search.py person "姓名"`，不要批量循环
- 环境变量：`export EXA_API_KEY=... TAVILY_API_KEY=... SERPAPI_KEY=...`
- **禁止批量循环请求外部API** — 会被封，逐个慢慢来

### 前置条件
- 新人物采集前：先注册到 `name_map.json`
- Python环境：`~/miniconda3/envs/tt/bin/python3`
- API Keys：见 TOOLS.md，脚本里需export

### 质检评分批量执行方案（已验证）
- **模型**: glm-5-turbo（并发高，不阻塞；glm-5.1容易429）
- **执行方式**: sessions_spawn，每人1个子session，同时3个并发
- **子session任务**: read prompt文件 → LLM评分 → exec apply → 返回结果
- **不要多人塞进1个子session**（context会爆）
- **不要直接调GLM API**（违规+花钱），必须走sessions_spawn
- **已完成的标志**: `materials/_quality_scores.json` 存在
- **进度追踪**: 定期扫描已完成的 `_quality_scores.json` 文件数
- **之前失败的重跑**: 删除该人的 `_quality_scores.json` 再重新spawn

### 蒸馏注意事项
- 蒸馏必须逐篇逐chunk处理，禁止一次性塞给LLM
- **一人一个子session**，多人塞一个必定超出上下文
- **超百万字符需分多个子session分批处理**
- **蒸馏prompt是预生成到文件的**（`_per_file_prompts/`），不是实时生成
- 蒸馏必须实事求是，有观点才提取，没观点输出空数组，禁止编造
- 逐篇蒸馏的JSON结果保留在 `_per_file_results/` 目录，用于溯源和灾备
- 并发限制：GLM-5-turbo 3个，GLM-5.1 2个，总计5个人

## 🔴 红线
- **犯错/被纠正时必须用 self-improvement skill 记录教训**
- **用skill前先读SKILL.md**，不要凭印象用
- **执行任务前先查MEMORY.md模块索引表**，有现成模块就用，禁止重写
- **chromadb 严禁并发** — WSL 9p 下会 segfault 损坏数据库
- **SiliconFlow 禁止** — 未经黄老板许可不得使用
- **Sci-Hub 下载间隔15秒**，禁止高频请求
- **任何外部API 间隔至少5-10秒**
- **蒸馏必须逐篇逐chunk处理** — 一人一个子session，禁止多人塞一个
- **超过10万字符就需分多个子session** — 分批处理
- **蒸馏必须实事求是** — 有观点才提取，没观点输出空数组，禁止编造
- **蒸馏prompt预生成到文件** — `_per_file_prompts/`，不是实时生成
- **GLM调用走sessions_spawn** — 直接调API会被限流
- **质量评分只用LLM** — 禁止规则评分脚本
- **质量评分batch_size=5** — 20→200KB prompt太大，5→70KB可处理
- **质量评分每子session最多3个prompt** — 超过3个必须拆成多个子session（>3个会context overflow）
- **质量评分5并发** — glm-5-turbo + glm-4.7混用，gateway超时立即重试
- **质量评分子session内apply** — --apply-from-file在子session内执行
- **scores JSON扩展名必须同步** — .txt→.md重命名后scores也要同步，否则apply全部file not found
- **质检输入规则** — <2k字全文；2k-50k字前5000字符；>50k字书籍智能采样10000字符（目录+前言+首章）
- **libgen著作下载必须验证内容** — 同名书很多，下载后检查是否正确作者的著作
- **搜索用全名+领域关键词** — 常见名必须加领域词区分同名不同人
- **init_db.py扫描materials/根目录** — 不只是materials/raw/，17个新人的素材直接在materials/下

## 2026-04-29 质检管线修复

### 修复的问题
1. **幽灵文档** — prompt包含磁盘不存在的文件（旧report快照）。修复：`generate_prompt`加磁盘存在性检查
2. **文件名特殊字符** — 14个文件名含`\n`/`\t`/`\r`导致匹配失败。修复：`_preprocess.py`生成report时自动sanitize文件名
3. **apply的fuzzy match** — 中文引号`""`/破折号`——`/`\xa0`导致missing。修复：`normalize_filename`+`find_file_fuzzy`
4. **注册时自动去重** — `init_db.py`新增Jaccard去重（>0.85移到`_duplicates/`），注册即检查

### 修改的文件
- `quality_check_llm.py` — 加了`normalize_filename`、`find_file_fuzzy`、磁盘存在性过滤
- `_preprocess.py` — report生成时sanitize含`\n`/`\t`的文件名
- `init_db.py` — 新增`jaccard_similarity`、`check_dup_against_existing`、注册时自动去重

### 质检进度（16:15）
- 总素材4021，已评分2284+丢弃662=2946，待评分1075
- 完成率73.3%，93人有待评分，162个prompt batch

## 关键经验
- **素材采集必须包含著作原文** — `download_book(title, dir, proxies=...)` → libgen.li→libgen.com.im→archive.org → materials/book_书名.md + materials/books/书名.pdf
- **著作下载需代理** — `proxies={'https':'http://172.26.0.1:10808'}`
- **著作下载回退链** — libgen.li(优先) → libgen.com.im(备) → archive.org(兜底)
- **著作下载必须验证内容** — 已发现7本错误下载（同名论文/其他作者的书）
- **libgen.li下载流程** — 搜索index.php?req={title} → ads.php?md5={md5} → get.php下载
- **搜索用全名+领域关键词** — 常见名（Chris Miller、Nate Silver等）必须加领域词
- **同名误匹配** — 论文搜索时同名不同人会被误收录（如化学领域的Ledoux vs 神经科学家LeDoux）
- **init_db.py扫描materials/根目录** — 不只是raw/，排除_开头文件
- **质量评分已验证的流程**：_preprocess --quality → quality_check_llm.py(bat=5) → spawn子session(≤5个prompt/session, glm-5-turbo, 5并发) → --apply-from-file
- **质量评分batch_size=5**：经验证20→200KB太大，5→70KB最佳
- **质量评分每子session最多3个prompt**：>3个会context overflow，必须分多个子session
- **质量评分5并发**：glm-5-turbo + glm-4.7，GLM API本身支持20并发
- **质量评分子session模板**：read prompt → JSON评分 → write scores → exec --apply-from-file
- **gateway超时是常态**：spawn失败立即重试，不要空转等
- 中文冒号导致JSON parse失败
- Sci-Hub镜像：只保留 .ru 和 .in（.se已挂，.st会403）
- **蒸馏输出文件名是 `personality_v3.json`**，不是 distillate.json
- 代码任务交给Claude Code，不手写
- **data_search.py 是统一入口**，不要在外面重写脚本，直接import或CLI调用
- **API Key需export环境变量**，否则 `_get_working_key` 返回None，付费搜索全部跳过
- **Tavily API key两个都已过期(401)** — 需要黄老板更新

## 关键文件说明（2026-04-27 重建后）

### _db.sqlite（每个人物目录下）
- **用途**：素材元数据管理（注册、状态追踪、质量评分、蒸馏进度）
- **创建**：`init_db.py --person <dir>` 或 `init_db.py --all`
- **表结构**：items（素材注册+状态）、distillate_insights（蒸馏索引）、processing_log（操作日志）
- **生命周期**：采集→raw → 质检→quality_checked/discarded → 蒸馏→distilled
- **重建方法**：`init_db.py --all`（扫描materials和papers目录重新注册）
- ⚠️ **不是蒸馏产物**，是中间状态管理数据库

### personality_v3.json（每个人物目录下）
- **用途**：蒸馏产出的人格模型（最终结果）
- **生成**：`_batch_distill.py --per-file` → spawn逐篇蒸馏 → `--merge-per-file` 合并

### chromadb 向量库（/mnt/d/data/think-tank-data/_chromadb/lacan_db/）
- **用途**：RAG检索用，存储素材chunks + 结构化数据（insights/tension/core_profile等）
- **入库**：`ingest_v3.py <人名> <人物目录>`
- ⚠️ 严禁并发写入

### 数据流
```
素材文件(txt/pdf) → _preprocess.py --quality → _db.sqlite(注册+状态)
  → quality_check_llm.py → _db.sqlite(质量评分)
  → _batch_distill.py --per-file → personality_v3.json(蒸馏结果)
  → ingest_v3.py → chromadb(向量入库)
```

## 2026-04-27 事故记录
- 误删133人_db.sqlite + 17人目录，数据已从Windows恢复但sqlite损坏
- 133人_db.sqlite已用init_db.py重建（3277条素材注册）
- personality_v3.json已移到trash，全部需重新蒸馏
- 17人需重新采集素材（目录+name_map已被删）
- **备份脚本**：scripts/auto_backup.sh（D:\data → D:\data2）
- **教训**：删东西必须先确认，用trash不用rm，先读文档再操作

## 本地服务
- **STT**: http://127.0.0.1:9977 (faster-whisper, conda stt)
- **Embedding**: Ollama bge-m3 (`http://172.26.0.1:11434`)
- **Python**: miniconda3 (base=3.13, tt=3.13+chromadb)
- **Chromium**: /snap/bin/chromium

## 已废弃（不要再提）
旧脚本（distill_v2, scholar_download, collect_v2, batch_game_theory_collect等）全部统一到 data_search.py
materials集合→evidence集合 | designers体系已删 | 联参已独立 | 本地调智谱API→sessions_spawn
academic-research-hub skill 已删除，功能在 data_search.py 里

## 补采完成（2026-04-27）
- 9人严重不足（<5篇）全部补采完成
- Peter Zeihan: 0→10（2著作）, Roubini: 1→11（1著作）, Cooley: 2→4, 牛新春: 2→4
- Peyrouse: 4→7, 曾向红: 4→8, 时殷弘: 4→8, 阎学通: 4→8, Arjomand: 4→7
- 补采流程验证：data_search搜索 → _preprocess --quality → quality_check_llm.py生成prompt → spawn评分 → apply
- **补采必须三源齐发**：search_general + search_chinese + search_academic（论文不能缺）+ download_book（著作）
- 著作下载：libgen→archive.org回退链正常
- 注意：_preprocess只生成report，还需跑quality_check_llm.py（不带--apply）才生成prompt
- **学术论文搜索max_results应设15**（之前设5太少），回补时统一调大
- **学术搜索同名误匹配常见**：Fawaz Gerges→等离子物理，Hu Shisheng→数学，需质检discarded

## 中等不足补采（5-8篇，2026-04-27）

### 已完成 21/23（学术论文搜索从第4批Dabashi开始加，前6人缺需回补）
| 人物 | 补采前 | 补采后 | 著作 | 论文 |
|------|--------|--------|------|------|
| 卡纳里斯 | 5 | 10 | ❌ | ❌缺 |
| 乔纳森·海特 | 5 | 10 | ✅Happiness | ❌缺 |
| 克里斯·哈德纳吉 | 5 | 9 | ✅2本 | ❌缺 |
| Bernard Lewis | 5 | 10 | ❌ | ❌缺 |
| 殷之光 | 5 | 10 | - | ❌缺 |
| 金宜久 | 5 | 11 | - | ❌缺 |
| 斯坦诺维奇 | 6 | 12 | ❌ | ✅5篇但被过滤 |
| 潘忠岐 | 6 | 11 | - | ❌缺 |
| Vali Nasr | 6 | 12 | ❌ | ❌缺 |
| Hamid Dabashi | 6 | 12 | ❌ | ✅5篇 |
| 契克森米哈赖 | 7 | 14 | ❌ | ✅5篇但被过滤 |
| 埃利斯 | 7 | 14 | ❌ | ✅5篇 |
| 胡仕胜 | 7 | 15 | - | ❌同名误匹配 |
| Joseph Stiglitz | 7 | 14 | ✅Inequality | ✅5篇 |
| Graham Allison | 7 | 11 | ✅2本 | ✅5篇 |
| 瞿东升 | 7 | 13 | - | ✅5篇 |
| 金灿荣 | 7 | 13 | - | ✅5篇 |
| 田文林 | 7 | 13 | - | ✅5篇 |
| 佐尔格 | 8 | 16 | ❌ | ✅5篇 |
| 班杜拉 | 8 | 14 | ✅SLT | ✅5篇但被过滤 |
| 张维迎 | 8 | 15 | - | ✅5篇 |
| Fawaz Gerges | 8 | 15 | ✅ISIS | ❌同名误匹配 |

### 全部完成 23/23 ✅
- Mearsheimer: 8→16, 著作❌, 论文✅5篇

### 论文全文回补进度
**已完成：**
- ✅ 海特：3篇全文
- ✅ 班杜拉：2篇全文
- ✅ Allison：6篇PDF
- ✅ Stiglitz：3篇PDF
- ✅ 契克森米哈赖：5篇全文

**失败待重试：**
- ❌ Mearsheimer：超时
- ❌ 埃利斯：gateway挂
- ❌ 哈德纳吉：gateway挂
- ❌ Dabashi：gateway超时

**未开始（待回补）：**
Zeihan, Roubini, Cooley, 牛新春, Peyrouse, 曾向红, 时殷弘, 阎学通, Arjomand, 卡纳里斯, Bernard Lewis, 殷之光, 金宜久, 潘忠岐, Vali Nasr, 瞿东升, 金灿荣, 田文林, 佐尔格, 张维迎, Fawaz Gerges, Stanovich, 胡仕胜

**注意：download_scihub_pdf没有proxies参数**，用export https_proxy环境变量即可

### 回补流程（必须全文，不只是abstract）
1. `search_academic('人物名', max_results=15)` → 获取论文列表+DOI
2. 过滤同名误匹配（检查authors字段）
3. `download_scihub_pdf(doi, output_dir)` → 下载全文PDF
4. PyMuPDF提取文本 → 保存到 materials/
5. `_preprocess.py --quality` → `quality_check_llm.py` → spawn评分 → apply

### 关键约束
- Sci-Hub镜像：.ru 和 .in，**间隔5秒**（30秒太慢）
- Sci-Hub对部分出版商DOI直接403（如APA 10.1037/*），无法绕过，跳过即可
- 403是出版商级别封锁，不是并发限流
- 论文搜索max_results=15（之前5太少）
- 每人1个subsession，3个并发
- 同名误匹配用authors字段过滤
- abstract-only的论文不保存，必须全文

## 已生成报告（原记录保留）
- 亚洲热点研判 v1/v3（2026-04-20）
- 台湾战略窗口期研判 v3.1（2026-04-21）
