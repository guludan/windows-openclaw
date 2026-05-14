# MEMORY.md - 小狗蛋的长期记忆

## 黄老板
- 称呼：黄老板
- 项目方向：① **人格智库** (think-tank) ② **人格还原** (person-replication)
- GitHub：guludan

## 智库项目状态（2026-05-12）

### 数据目录
- 数据根：`/mnt/d/data/think-tank-data/`
- 向量库：`personalities/_lacandb/lacan_db/`（6表架构，LanceDB）
- 报告：`reports/`
- 人物映射：`personalities/name_map.json`（153人）

### 人员：153人（注册）
- military 25 + scholars 83 + psychologists 45
- 新增领域：geopolitical-finance, sanctions-finance, information-warfare, indo-pacific, tech-geopolitics, political-forecasting

### 质检状态：100% ✅
- Total: 3922, Kept: 3055, Discarded: 867

### 蒸馏+入库状态（2026-05-14 修正）
- 6集合架构：personas/cognitive/claims/evidence/tracing/tension
- 向量总数：30,481（LanceDB）
- **蒸馏完成**：45人（所有prompt有对应result）
- **蒸馏未完成**：63人，共缺2057个prompt（详见 /tmp/distill_incomplete_final.json）
- **待蒸馏**：契克森米哈赖 0/230（唯一无personality_v3的）
- **regenerate问题**：110/151人insights未真正合并（单evidence占比>40%），需用新的多批次regenerate全员重跑
- **蒸馏结果唯一路径**：`_per_file_results/`（distillates已废弃并清理）

### 🔴 regenerate多批次重构（2026-05-14）
- v3：去掉compact_insights截断（之前截断丢弃导致evidence丢失的bug源）
- 改为按dim分批regenerate，每批独立LLM合并，最后统一apply
- 单dim > 80条自动拆子批，避免prompt过大
- 额外一个summary prompt生成顶层字段（brief/school等）
- 新模板：regenerate_batch_dim.md + regenerate_summary.md
- 兼容旧流程：单文件result仍可apply

### 数据备份
- `/mnt/d/data2/think-tank-backup-20260506/`（785MB）
- `/mnt/d/data2/think-tank-backup-20260510/`（926MB）
- 旧ChromaDB备份已清理，现用LanceDB

### LanceDB迁移（2026-05-12）
- ChromaDB在WSL/9p上写入静默失败（不报错但数据丢失）
- 迁移到LanceDB 0.30.2（嵌入式，跨平台好）
- DB路径：`_lacandb/lacan_db`（D盘）
- 6张表：personas(1843), cognitive(3041), claims(7292), evidence(14354), tension(992), tracing(2559)
- 全量核查+去重+补录：149人27,930条0重复（2026-05-13）

### 蒸馏流水线修复历史
- 巨型段落切分bug：单段>15KB按句子再切分
- 假excerpt过滤：标题/References自动过滤
- 两阶段regenerate压缩bug：截断丢弃insights（2026-05-12修）
- dim维度丢失：按比例截断确保每个dim有代表
- regenerate模板：禁止跨dim合并
- 6人假excerpt清理+重新入库：周恩来、徐向前、林彪、邓小平、马丁·塞利格曼、卡尔·罗杰斯

## 📦 模块索引（任务→模块→函数 映射）

| 任务 | 脚本 | 关键函数 |
|------|------|----------|
| 搜索/采集 | `skills/data-search/scripts/data_search.py` | `search()`, `search_person()`, `save_result()` |
| 网页内容抓取 | data_search.py | `fetch_urls_to_materials(urls, output_dir)` / CLI `fetch` 子命令 |
| 论文搜索+下载 | `skills/think-tank/scripts/fetch_papers.py` | `process_person()` |
| 论文下载(DOI) | data_search.py | `download_scihub_pdf()` |
| 著作下载 | data_search.py | `download_book()` |
| 注册 | `skills/think-tank/scripts/init_db.py` | 只注册.md/.txt |
| 预处理 | `skills/think-tank/scripts/_preprocess.py` | `--quality` |
| 质量评分 | `skills/think-tank/scripts/quality_check_llm.py` | batch_size=8 |
| 蒸馏 | `skills/think-tank/scripts/_batch_distill.py` | `--per-file` → `--merge-per-file` |
| 增量蒸馏 | `skills/think-tank/scripts/incremental_distill.py` | `--auto` |
| 重新生成人格 | `skills/think-tank/scripts/regenerate_personality.py` | `--all`批量, `--apply`, 多批次(>80ins按dim分批) |
| 入库 | `skills/think-tank/scripts/ingest_v3.py` | 严禁并发 |
| 报告生成 | `skills/think-tank/scripts/think_tank_pipeline_v3.py` | |

## 🔴 红线
- **质量评分是5分制(1-5)，用action=keep/discard判断，不是score>=6**
- **用skill前先读SKILL.md**
- **LanceDB 严禁并发写入**
- **蒸馏逐篇逐chunk，一人一个子session，>10万字符分批**
- **蒸馏实事求是，没观点输出空数组，禁止编造**
- **evidence.excerpt 必须原文逐字摘录，禁止转述/概括/改写，没evidence的claim=编造**
- **GLM调用走sessions_spawn**
- **SiliconFlow 禁止**
- **Sci-Hub 间隔15秒，外部API间隔5-10秒**
- **init_db不注册json，素材必须.md/.txt**
- **必须先质量评分再生成蒸馏prompts，顺序不能反**
- **regenerate多批次**：>80 insights按dim分批LLM合并+summary prompt生成顶层字段，每批prompt≤30KB
- **regenerate子session必须用Python json.dump写文件**：直接write大JSON会被截断
- **regenerate批次结果文件**：`_regenerate_batch_result_{id}.json`（每批insights合并）+ `_regenerate_summary_result.json`（顶层字段）
- **merged_from是regenerate的生命线**：没有它apply无法从原始insights回填evidence
- **大人物入库需注意timeout**：>200 insights的人需要更长timeout（600s+）
- **论文下载必须验证作者身份，同名不同人问题严重**

## 关键经验
- 蒸馏输出：`personality_v3.json`
- Python环境：`~/miniconda3/envs/tt/bin/python3`
- API Keys：见 TOOLS.md
- 代码任务交给Claude Code
- data_search.py是统一入口，不要重写
- Tavily API key已过期

## 本地服务
- STT: http://127.0.0.1:9977
- Embedding: Ollama bge-m3 (http://172.26.0.1:11434)
- Redis: 127.0.0.1:6379

## 已废弃（不要再提）
旧脚本（distill_v2, scholar_download, collect_v2, batch_game_theory_collect等）统一到data_search.py
