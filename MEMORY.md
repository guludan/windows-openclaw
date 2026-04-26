# MEMORY.md - 小狗蛋的长期记忆

## 黄老板
- 称呼：黄老板
- 项目方向：① **人格智库** (think-tank) ② **人格还原** (person-replication)
- GitHub：guludan

## 智库项目（2026-04-25）

### 数据目录
- 数据根：`/mnt/d/data/think-tank-data/`
- 向量库：`_chromadb/lacan_db/`（待重建）
- 报告：`reports/`

### 人员：130人（military 25 + scholars 74 + psychologists 31）
- 全员素材已采集 ✅
- 蒸馏进度：15/77（scholars），military/psychologists 待重蒸馏
- 向量库已删除，蒸馏完成后重建

### 工作流
```
素材采集(data_search.py / bilibili_transcribe.py) → 预处理 → 蒸馏(sessions_spawn) → 入库(ingest_v3.py)
```

### 人员：150人（2026-04-26更新）
- military 25 + psychologists 31 + scholars 77（含17新增）
- 蒸馏进度：133/133 V3 ✅，17人待蒸馏
- 向量库：已入库133人（89MB）

### 新增分类（2026-04-26）
- geopolitical-finance: Bremmer, Roubini, Friedman, Zeihan
- sanctions-finance: Zarate, Drezner, Economy
- information-warfare: DiResta, Rid, Singer
- indo-pacific: Mastro, Bush, Campbell
- tech-geopolitics: Miller, Mazzucato
- political-forecasting: Tetlock, Silver

### 核心脚本
- `data_search.py`（skills/data-search/scripts/）— 统一搜索入口
- `bilibili_transcribe.py` — B站视频搜索+下载+转录（you-get+ffmpeg+faster-whisper）
- `ingest_v3.py` — 入库（insights+evidence+core_profile+tension），入库前自动删除旧数据，metadata含person+category防同名误删
- `_batch_distill.py` — 全量蒸馏（--per-file逐篇生成prompt → spawn逐个处理 → --merge-per-file合并）
- `_distill_utils.py` — 蒸馏公共工具（大文件切分15000字符/chunk）
- `incremental_distill.py` — 增量蒸馏（--auto自动检测新素材 → 逐篇prompt → --apply合并）
- `think_tank_pipeline_v3.py` — 报告生成
- ⚠️ 蒸馏必须逐篇处理，禁止一次性塞给LLM
- ⚠️ 逐篇蒸馏的JSON结果保留在_per_file_results/目录，用于溯源和灾备

### 并发限制
- GLM-5-turbo: 3个并发
- GLM-5.1: 2个并发
- GLM-5: 可额外开
- 总计可同时处理5个人

### 向量库集合（v5，已重建）
- core_profile / insights / evidence / tension
- 89MB，133人已入库

## 🔴 红线
- **犯错/被纠正时必须用 self-improvement skill 记录教训**
- **用skill前先读SKILL.md**，不要凭印象用
- **chromadb 严禁并发** — WSL 9p 下会 segfault 损坏数据库
- **SiliconFlow 禁止** — 未经黄老板许可不得使用
- **Sci-Hub 禁止批量下载** — 一篇一篇，间隔60s+
- **任何外部API 禁止高频请求** — 间隔至少5-10s
- **蒸馏必须逐篇读素材** — 一次全读会幻觉，逐篇提取+标注source_ref
- **蒸馏后检查domains覆盖** — 防止丢失
- **GLM调用走sessions_spawn** — 直接调API会被限流
- **质量评分只用LLM** — 禁止规则评分脚本
- **蒸馏红线：必须逐篇读素材，不能一次全读** — 每篇读完提取insights+标注source_ref，再读下一篇。一人10-60篇素材，需10-30分钟/人。不能设2分钟cron批量跑
- **执行任务前必须先读SKILL.md** — 不读就动手=瞎搞，红线写再多不看等于没有

## 关键经验
- 素材>60000字符需截断再蒸馏
- 中文冒号导致JSON parse失败
- Sci-Hub镜像优先级：.in > .st > .ru > .se
- 著作采集：优先全文PDF，没有就找高质量书评/摘要
- **蒸馏输出文件名是 `personality_v3.json`，不是 `distillate.json`** — 旧版的 distillate.json 是空壳已清理
- 代码任务交给Claude Code，不手写
- 先读已有skill再写脚本，避免重复造轮子

## 本地服务
- **STT**: http://127.0.0.1:9977 (faster-whisper, conda stt)
- **Embedding**: Ollama bge-m3 (`http://172.26.0.1:11434`)
- **Python**: miniconda3 (base=3.13, tt=3.13+chromadb)
- **Chromium**: /snap/bin/chromium

## 技能体系
核心：self-improving-agent, colleague-skill, agent-knowledge, knowledge-capture, agent-council, agent-autopilot, adaptive-reasoning
采集：playwright-scraper, extract-pdf-text, data-search
工具：github-cli, api-gateway, summarize-pro, docker-essentials, task-status
专业：think-tank, stock-query, policy-monitor, hotspot-monitor, zhihu-crawler, web-scraper-jina

## 已废弃（不要再提）
旧脚本（distill_v2, scholar_download, collect_v2, batch_game_theory_collect等）全部统一到 data_search.py
materials集合→evidence集合 | designers体系已删 | 联参已独立 | 本地调智谱API→sessions_spawn

## 已生成报告
- 亚洲热点研判 v1/v3（2026-04-20）
- 台湾战略窗口期研判 v3.1（2026-04-21）
