# MEMORY.md - 小狗蛋的长期记忆

## 黄老板
- 称呼：黄老板
- 项目方向：
  1. **人格智库** (think-tank) — 学者多视角评论，独立观点，自然碰撞
  2. **人格还原** (person-replication) — 还原真实人物的决策逻辑，预测其在新事件中的判断和决策
- GitHub 账号：guludan
- 服务器：阿里云 Linux (Alinux 8), 2核 3.5G 内存

## 智库项目现状（2026-04-25）

### 工作目录
- **数据根**: `/mnt/d/data/think-tank-data/`（D盘）
- **personalities**: `/mnt/d/data/think-tank-data/personalities/`（workspace 下 symlink）
- **向量库**: `/mnt/d/data/think-tank-data/_chromadb/lacan_db/`
- **报告**: `/mnt/d/data/think-tank-data/reports/`

### 人员体系（三套，96人）

| 体系 | 子领域 | 人数 | 素材量 |
|------|--------|------|--------|
| 军事(military) | — | 25 | 25人 ✅ |
| 学者(scholars) | game-theory(10), actuarial-science(5), financial-engineering(7), financial-data(4), economics(13+中文), ir-theory(7), middle-east(5+中文), central-asia(2+中文), ir-security(3+中文), persian-islamic(3+中文), religious-studies(3+中文), media-studies(5) | 74 | 74人 ✅ |
| 心理学家(psychologists) | behavioral(7), cognitive(6), psychoanalysis(6), social-engineering(5), evolutionary-psychology(3), neuropsychology(4) | 31 | 31人 ✅ |
| **合计** | | **130** | **130人** |

⚠️ 全员需要重新蒸馏（v5架构，新增insights_with_evidence字段）
⚠️ 向量库已删除，蒸馏完成后重建

### 向量库（lacan_db, v5架构 — 待重建）

见下方"向量库集合"部分

### 唯一工作流（v5 架构）

```
① 素材采集(data_search.py) → ② 预处理(_preprocess.py) → ③ V3蒸馏(sessions_spawn)
→ ④ 入库(ingest_v3.py: insights+evidence双集合)
```

⚠️ 预处理必须做，不能跳过（去噪、删404、删极小文件）
⚠️ 蒸馏必须按模板输出 insights_with_evidence 字段（观点+原文证据片段）
⚠️ 原文txt文件保留在磁盘，向量库只存被标注的关键段落

### 向量库集合（lacan_db，待重建）

| 集合 | 说明 | 内容来源 |
|------|------|----------|
| core_profile | 人格摘要 | personality_v3.json |
| insights | 观点（每个claim一条，metadata含evidence_ids关联） | insights_with_evidence.claim |
| evidence | 原文证据片段（每个excerpt一条，metadata含insight_id反关联） | insights_with_evidence.evidence |
| tension | 思想内在矛盾 | personality_v3.json |

**insights ↔ evidence 双向关联**：
- insight.metadata.evidence_ids → 关联的evidence ID列表
- evidence.metadata.insight_id → 反向关联的insight ID
- 原文全文件 → evidence.metadata.source_file → 磁盘txt路径

### 核心脚本（全部在 skills/think-tank/scripts/ 下）

| 脚本 | 用途 |
|------|------|
| `skills/think-tank/scripts/ingest_v3.py` | ⭐ 入库（insights+evidence+core_profile+tension） |
| `skills/think-tank/scripts/_preprocess.py` | 素材预处理（去噪/删垃圾/删404） |
| `skills/think-tank/scripts/_batch_distill.py` | 蒸馏辅助（生成子session任务，大素材分段） |
| `skills/think-tank/scripts/think_tank_pipeline_v3.py` | 报告生成v5（查询insights+evidence） |
| `skills/think-tank/scripts/historical_context.py` | 历史事件引擎(Redis) |
| `skills/think-tank/scripts/cache_news.py` | 财联社/A股/FRED缓存 |

### 搜索与采集

唯一入口：**`skills/data-search/scripts/data_search.py`** — 统一综合调度器
- L1通用搜索（SearXNG/DuckDuckGo/Exa/Tavily/SerpAPI）
- L2中文深度（知乎/搜狗/头条/秘塔/财联社）
- L3学术（arXiv/PubMed/Semantic Scholar + Sci-Hub PDF下载）
- L4宏观（World Bank/AKShare/FRED）
- 去重+质量评分+多格式输出（json/text/markdown/bibtex/ris）

```
python data_search.py search "query" --layers general,chinese,academic,macro --max 5
python data_search.py academic "query" --source arxiv --download --output-dir ./pdfs
python data_search.py download --doi 10.1038/xxx --output-dir ./pdfs
```



### 已废弃
- ❌ `distill_v2.py`, `_extract_one.py`, `incremental_vecdb.py` — 不存在
- ❌ `batch_scholar_papers.py`, `scholar_download.py` — 不存在
- ❌ `batch_game_theory_collect.py` — 批量搜索导致限流，已删除
- ❌ `research.py`（academic-research-hub）— 已合并入 data_search.py
- ❌ materials集合 — 已改为evidence集合（只存蒸馏标注的原文片段，不再全文分块）
- ❌ 全文PDF下载已统一到 data_search.py（academic子命令+download子命令）
- ❌ `designers`体系 — 已删除
- ❌ 联参体系 — 已从智库独立
- ❌ SiliconFlow — 要钱，禁止
- ⚠️ chromadb **严禁并发** — WSL 9p 下多进程写入会导致 segfault 和数据库损坏
- ❌ 本地脚本调智谱API蒸馏 — 改用sessions_spawn

### 已生成报告
- `亚洲热点研判v1_2026-04-20.md` (初版)
- `亚洲热点研判v3_2026-04-20.md` (完整版)
- `台湾战略窗口期研判v3.1_2026-04-21.md`

## 技能体系
核心技能：self-improving-agent, colleague-skill, agent-knowledge, knowledge-capture, agent-council, agent-autopilot, adaptive-reasoning
采集技能：playwright-scraper, extract-pdf-text, data-search
工具技能：github-cli, api-gateway, summarize-pro, docker-essentials, task-status
专业技能：think-tank, stock-query, policy-monitor, hotspot-monitor, zhihu-crawler, web-scraper-jina

## 本地服务
- **STT**: http://127.0.0.1:9977 (faster-whisper, conda stt)
- **Embedding**: Ollama bge-m3 (`http://172.26.0.1:11434`)
- **Python**: miniconda3 (base=3.13, tt=3.13+chromadb)
- **Chromium**: /snap/bin/chromium

## 工作流约定
- **黄老板说"分析"→必须调用智库**：用think-tank pipeline跑多视角专家分析
- **GLM调用必须走sessions_spawn**，直接调API会被rate limit且花钱
- **marxists.org 原始文献是核心素材**，预处理时区分正文和网页垃圾
- **B站反爬用 yt-dlp 会 412，必须用 Playwright**
- **read SKILL.md全文再判断功能**，不要只读前半就说没有
- **先读已有skill再写脚本**，避免重复造轮子
- **代码任务交给Claude Code**，我不手写
- **不要自作主张截断/跳过数据**，大素材必须全文分块处理
- **报告用RAG驱动+领域边界+`[RAG: dim/source]`标记，不带版本号**
- **每份报告末尾标注"AI模拟"**
- **未经黄老板明确许可，不得使用 SiliconFlow API**

## 重要经验
- **质量评分只用LLM，禁止用规则评分脚本** — 规则评分无法区分同名不同人、无法判断内容相关性，已经被证明不可靠。所有人物一律走sessions_spawn调LLM评分。
- Sci-Hub镜像优先级：.in > .st > .ru > .se（.se被GFW屏蔽）
- **Sci-Hub禁止批量循环下载** — 一篇一篇下载，每次间隔60s+，绝不能for循环批量请求
- **著作采集规则** — 优先下载著作全文PDF（Open Library/Archive.org/libgen等公开来源），有全文下全文，没有就找高质量书评/章节摘要
- **任何外部API/网站禁止高频循环请求** — 每次间隔至少5-10s
- DNS改8.8.8.8后GitHub/SSH/pip全通
- Playwright需要atk, cups-libs, libXdamage等系统库
- ClawHub安装限流：每个请求间隔30s+
- 素材>60000字符需截断再蒸馏
- **蒸馏必须分批读素材，逐篇提取，不能一次读全部** — 一次读全部会导致幻觉（编造无出处观点）。每篇读完提取关键观点+标注source_ref，再读下一篇，最后合并
- 蒸馏后必须检查domains是否被覆盖，手动恢复
- 中文冒号导致JSON parse失败（不是屏蔽）
- **子agent经常空跑不干活**（5秒就结束），重要任务优先用脚本执行而非靠子agent
