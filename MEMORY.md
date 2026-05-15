# MEMORY.md - 小狗蛋的长期记忆

## 黄老板
- 称呼：黄老板
- 项目方向：① **人格智库** (think-tank) ② **人格还原** (person-replication)
- GitHub：guludan

## 智库项目状态（2026-05-15）

### 新V3管线全线打通 ✅
154/154 人完成 dedup+regen，统一步道
- 新管线：merge(_per_file_results) → dedup(语义去重) → regen(顶层字段)
- 废除了旧v3合并逻辑，全员走新流程
- batch_dedup_regen.py 核心脚本
- 总insights: 8743，无evidence: 11条

### 数据目录
- 数据根：`/mnt/d/data/think-tank-data/`
- 人物映射：`personalities/name_map.json`（154人）
- 最新备份：`/mnt/d/data2/think-tank-backup-20260515/`（15M）

### 管线脚本
| 阶段 | 脚本 |
|------|------|
| Merge | `_batch_distill.py --merge-per-file` |
| 去重+regen | `batch_dedup_regen.py` |
| 批量管线 | `batch_pipeline.py` |

### 已完成流程
1. ✅ `--merge-per-file` → 154人
2. ✅ `batch_dedup_regen.py --dedup-only` → 154人
3. ✅ `batch_dedup_regen.py --regen-only` → 154人

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
- Python环境：`~/miniconda3/envs/tt/bin/python3` (Python 3.13)
- **Python 3.13 `{{ }}` 陷阱**: 双大括号在普通代码中被解析为 `{ { set_literal } }` → `unhashable type: dict`。f-string 中 `env={{**os.environ, ...}}` 先构建 dict 再 str() 化，传给 `subprocess.run(env=str)` → 报错。修复：不要在 f-string 中用 dict unpacking，构建 dict 变量再传
- API Keys：见 TOOLS.md, 默认 key 写在 `_DEFAULT_KEYS` dict 中(data_search.py 顶部)
- 代码任务交给Claude Code
- data_search.py是统一入口，不要重写
- SerpAPI ✅ Google搜索正常（`serpapi.com`，参数 `api_key`）
- Exa Search: `7be7a599-fa7b-4107-9ef1-ad96646156c2` (可用)
- Tavily API key 可能已过期，优先用 SerpAPI/Exa

## 本地服务
- STT: http://127.0.0.1:9977
- Embedding: Ollama bge-m3 (http://172.26.0.1:11434)
- Redis: 127.0.0.1:6379

## 已废弃（不要再提）
旧脚本（distill_v2, scholar_download, collect_v2, batch_game_theory_collect等）统一到data_search.py
