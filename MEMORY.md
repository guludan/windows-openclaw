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
  ├─ data_search.py person "姓名" → 通用+中文+学术搜索 → materials/_search_results_姓名.json
  ├─ data_search.py academic "姓名" → SS搜论文拿DOI → papers/raw/
  ├─ download_scihub_pdf(doi) → Sci-Hub下载PDF → PyMuPDF提文本
  ├─ download_book(title, output_dir, proxies) → libgen.com.im → archive.org → book_书名.txt
  ├─ bilibili_transcribe.py → B站视频转文本 → materials/
  └─ 著作: 通用搜索找书名 → download_book() → materials/book_书名.txt

Phase 2: 质检
  ├─ _preprocess.py --quality → _quality_report.json
  └─ quality_check_llm.py → 生成prompt → GLM评分 → --apply 清理低分素材

Phase 3: 蒸馏
  ├─ _batch_distill.py --per-file → 逐篇LLM提取 → _per_file_results/
  ├─ incremental_distill.py --auto → 检测新素材自动蒸馏
  └─ 输出: personality_v3.json（不是distillate.json）

Phase 4: 入库
  └─ ingest_v3.py → chromadb 4集合(core_profile/insights/evidence/tension)

Phase 5: 报告
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

### 蒸馏注意事项
- 蒸馏必须逐篇处理，禁止一次性塞给LLM
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
- **蒸馏必须逐篇读素材** — 不能一次全读，需10-30分钟/人
- **GLM调用走sessions_spawn** — 直接调API会被限流
- **质量评分只用LLM** — 禁止规则评分脚本

## 关键经验
- **素材采集必须包含著作原文** — `download_book(title, dir, proxies=...)` → libgen.com.im/archive.org → 自动PDF转txt → materials/book_书名.txt
- **著作下载需代理** — `proxies={'https':'http://172.26.0.1:10808'}`
- **著作下载回退链** — libgen.com.im(优先) → archive.org(回退)
- **搜索只用人物名字** — 不要带领域词（如"geopolitics"），会导致搜不到
- **同名误匹配** — 论文搜索时同名不同人会被误收录（如化学领域的Ledoux vs 神经科学家LeDoux）
- 素材>60000字符需截断再蒸馏
- 中文冒号导致JSON parse失败
- Sci-Hub镜像：只保留 .ru 和 .in（.se已挂，.st会403）
- 著作采集：优先全文PDF，没有就找高质量书评/摘要
- **蒸馏输出文件名是 `personality_v3.json`**，不是 distillate.json
- 代码任务交给Claude Code，不手写
- **data_search.py 是统一入口**，不要在外面重写脚本，直接import或CLI调用
- **API Key需export环境变量**，否则 `_get_working_key` 返回None，付费搜索全部跳过
- **data_search.py person 命令输出格式**：结果保存为 `_search_results_姓名.json`（汇总JSON，含general/chinese/academic各层结果），不自动拆txt。蒸馏时直接读JSON即可。

## 本地服务
- **STT**: http://127.0.0.1:9977 (faster-whisper, conda stt)
- **Embedding**: Ollama bge-m3 (`http://172.26.0.1:11434`)
- **Python**: miniconda3 (base=3.13, tt=3.13+chromadb)
- **Chromium**: /snap/bin/chromium

## 已废弃（不要再提）
旧脚本（distill_v2, scholar_download, collect_v2, batch_game_theory_collect等）全部统一到 data_search.py
materials集合→evidence集合 | designers体系已删 | 联参已独立 | 本地调智谱API→sessions_spawn
academic-research-hub skill 已删除，功能在 data_search.py 里

## 已生成报告
- 亚洲热点研判 v1/v3（2026-04-20）
- 台湾战略窗口期研判 v3.1（2026-04-21）
