# MEMORY.md - 小狗蛋的长期记忆

## 黄老板
- 称呼：黄老板
- 项目方向：① **人格智库** (think-tank) ② **人格还原** (person-replication)
- GitHub：guludan

## 智库项目状态（2026-04-30）

### 数据目录
- 数据根：`/mnt/d/data/think-tank-data/`
- 向量库：`_chromadb/lacan_db/`（需清空重建）
- 报告：`reports/`
- 人物映射：`personalities/name_map.json`（150人）

### 人员：150人
- military 25 + scholars 83 + psychologists 30
- 新增领域：geopolitical-finance, sanctions-finance, information-warfare, indo-pacific, tech-geopolitics, political-forecasting

### 质检状态：100% ✅
- Total: 3922, Kept: 3055, Discarded: 867

### 蒸馏状态：待开始
- 质检完成后待蒸馏

### 数据清理（2026-04-30完成）
- materials/根目录清零，json全部清理，init_db不注册json
- 文件名特殊字符已sanitize
- 搜索缓存归档到_search_cache/

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
| 重新生成人格 | `skills/think-tank/scripts/regenerate_personality.py` | `--apply` |
| 入库 | `skills/think-tank/scripts/ingest_v3.py` | 严禁并发 |
| 报告生成 | `skills/think-tank/scripts/think_tank_pipeline_v3.py` | |

## 🔴 红线
- **用skill前先读SKILL.md**
- **chromadb 严禁并发**
- **蒸馏逐篇逐chunk，一人一个子session，>10万字符分批**
- **蒸馏实事求是，没观点输出空数组，禁止编造**
- **evidence.excerpt 必须原文逐字摘录，禁止转述/概括/改写，没evidence的claim=编造**
- **GLM调用走sessions_spawn**
- **SiliconFlow 禁止**
- **Sci-Hub 间隔15秒，外部API间隔5-10秒**
- **init_db不注册json，素材必须.md/.txt**

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
