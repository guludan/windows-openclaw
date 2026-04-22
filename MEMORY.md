# MEMORY.md - 小狗蛋的长期记忆

## 黄老板
- 称呼：黄老板
- 项目方向：
  1. **人格智库** (think-tank) — 学者多视角评论，独立观点，自然碰撞
  2. **人格还原** (person-replication) — 还原真实人物的决策逻辑，预测其在新事件中的判断和决策
- GitHub 账号：guludan
- 服务器：阿里云 Linux (Alinux 8), 2核 3.5G 内存

## 智库项目现状（2026-04-21）

### 工作目录
- **数据根**: `/mnt/d/data/think-tank-data/`（D盘）
- **personalities**: `/mnt/d/data/think-tank-data/personalities/`（workspace 下 symlink）
- **向量库**: `/mnt/d/data/think-tank-data/_chromadb/lacan_db/`
- **报告**: `/mnt/d/data/think-tank-data/reports/`

### 人员体系（三套，96人）

| 体系 | 子领域 | 人数 | 蒸馏 | 素材 |
|------|--------|------|------|------|
| 军事(military) | — | 25 | 25/25 ✅ | 680 |
| 学者(scholars) | game-theory(10), economics(8), ir-theory(8), middle-east(5), central-asia(4), ir-security(4), persian-islamic(4), religious-studies(4) | 47 | 47/47 ✅ | 1164 |
| 心理学家(psychologists) | behavioral(7), cognitive(6), psychoanalysis(6), social-engineering(5) | 24 | 23/24 | 445 |
| **合计** | | **96** | **95/96** | **2289** |

缺蒸馏：克里斯·哈德纳吉（social-engineering，素材仅1条meta，无实际内容，需补采集）

### 向量库（lacan_db, v4.2架构）

| 集合 | 说明 | 关联 |
|------|------|------|
| insights | 蒸馏核心逻辑（按dim分类，不截断） | source_ref→material_id |
| tension | 思想内在矛盾（v4.2独立集合） | source_ref→material_id |
| materials | 原始素材分块（800字/块） | material_id |
| core_profile | 人格摘要（96人） | — |

关联表：insight/tension.metadata.source_ref ↔ materials.metadata.material_id
Embedding: Ollama bge-m3 (1024维)

### 唯一工作流

```
素材采集 → 预处理(_preprocess.py) → 下载全文 → 预处理(_preprocess.py) → chunk入库(ingest_v3.py) → V3蒸馏(sessions_spawn) → 结构化入库(ingest_v3.py)
```

⚠️ 预处理必须做，不能跳过（去噪、删404、删极小文件）

### 核心脚本（全部在 skills/think-tank/scripts/ 下）

| 脚本 | 用途 |
|------|------|
| `skills/think-tank/scripts/ingest_v3.py` | ⭐ 通用入库（素材chunk+结构化，所有体系） |
| `skills/think-tank/scripts/ingest_batch.py` | 批量入库多人 |
| `skills/think-tank/scripts/_preprocess.py` | 素材预处理（去噪/删垃圾/删404） |
| `skills/think-tank/scripts/_batch_distill.py` | 蒸馏辅助（生成子session任务，大素材分段） |
| `skills/think-tank/scripts/think_tank_pipeline_v3.py` | 报告生成v3.2 |
| `skills/think-tank/scripts/collect_new_scholars.py` | Google Scholar论文元数据 |
| `skills/think-tank/scripts/batch_game_theory_collect.py` | 全文下载（OA+Sci-Hub+Playwright） |
| `skills/think-tank/scripts/historical_context.py` | 历史事件引擎(Redis) |
| `skills/think-tank/scripts/cache_news.py` | 财联社/A股/FRED缓存 |

### 已废弃
- ❌ `distill_v2.py`, `_extract_one.py`, `incremental_vecdb.py` — 不存在
- ❌ `batch_scholar_papers.py`, `scholar_download.py` — 不存在，已被 collect_new_scholars.py + batch_game_theory_collect.py 替代
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
采集技能：playwright-scraper, extract-pdf-text, academic-research-hub
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
- Sci-Hub镜像优先级：.st > .ru > .se（.se被GFW屏蔽）
- DNS改8.8.8.8后GitHub/SSH/pip全通
- Playwright需要atk, cups-libs, libXdamage等系统库
- ClawHub安装限流：每个请求间隔30s+
- 素材>60000字符需截断再蒸馏
- 蒸馏后必须检查domains是否被覆盖，手动恢复
- 中文冒号导致JSON parse失败（不是屏蔽）
