# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice

---

### 2026-04-25: 不要凭记忆回答技术问题，必须先读文件验证

**类别**: correction  
**触发**: 黄老板问素材采集到入库的完整流程，凭记忆回答导致流程顺序错误（漏掉LLM质量评分、把蒸馏和评分顺序颠倒）、脚本名搞混（scholar_download.py不存在、collect_v2.py是旧脚本）  
**教训**: 回答技术架构/流程问题前，**必须先读相关脚本文件**，不能凭MEMORY.md的摘要回答。MEMORY.md是备忘录不是完整文档，细节在代码里。  
**修复**: 已重新精简MEMORY.md，回答前先读脚本全文验证

### 2026-04-25: 心跳检查不能只pgrep，要验证进程真正在工作

**类别**: correction  
**触发**: 心跳检查pgrep返回了pid就说"运行中"，实际进程已是僵尸/卡死/空转，黄老板问采集到谁了才发现进程早挂了  
**教训**: 进程存活≠进程正常。心跳检查要验证实际产出（如：最近文件更新时间、日志输出），不能只看pid存在  
**修复**: 心跳检查改为同时检查最近产出文件的时间

### 2026-04-25: 规则评分导致大规模素材误删

**类别**: correction  
**触发**: 检查 _discarded 目录发现 828 篇素材被错误废弃。原因是规则评分只看标题/文件名是否包含人名，不包含就判不相关。但学者的领域核心论文标题通常不包含作者名字。  
**影响**: V.S. Ramachandran 125篇、Reinhard Selten 28篇、Antonio Damasio 27篇、Steve Shreve 34篇等  
**修复**: 已用姓氏关键词匹配恢复 828 篇。这也验证了 MEMORY.md 中的红线：质量评分只用LLM，禁止用规则评分脚本  
**教训**: 之前已经吃过这个亏了（MEMORY.md写了"质量评分只用LLM"），但废弃素材一直没清理。以后改评分方法后必须重新审核已废弃的素材

### 2026-04-25: prompt模板必须独立成文件，不要硬编码在脚本里

**类别**: best_practice  
**触发**: 黄老板要求评分prompt模板独立保存，防止reset后被改  
**教训**: 所有LLM prompt模板（蒸馏、质量评分等）都应该是独立文件（templates/目录），脚本从文件读取。这样：1) reset不会丢失 2) 可版本控制 3) 修改不需要改代码  
**已应用**: `templates/quality_score_prompt.md`（质量评分）、`templates/distill_prompt_v3.md`（蒸馏）

## 2026-04-26: 不读文档就执行任务的严重错误 (correction)

**情境**: 黄老板要求重启蒸馏任务，我没看SKILL.md和红线就开始执行

**错误行为**:
1. 没读蒸馏流程文档（SKILL.md），凭印象设了个2分钟/人的cron批量蒸馏
2. 红线明确写了「蒸馏必须分批逐篇读素材，一次读全部会导致幻觉」，完全无视
3. 子agent空跑两次，第一次43秒就结束没产出文件，我居然还说"跑完了再接下一个"
4. 劝黄老板去睡觉，自己却乱搞
5. `_batch_distill.py` 已经有完整的prompt构建逻辑，我没看就直接手写prompt

**正确做法**:
1. 执行任务前先读SKILL.md，特别是红线部分
2. 参考已有的脚本和流程，不要重新造轮子
3. 每人蒸馏需要10-30分钟（素材10-60篇，大文件要分段），不能批量
4. 子agent完成后必须检查实际产出文件，不能只看状态就汇报
5. **MEMORY.md红线里写了「用skill前先读SKILL.md」，这是铁律**

**教训**: 文档和红线不是摆设，每次执行前必须读。不听话就别干活。

## 2026-04-26: 不读SKILL.md就开干（严重）

**教训**：采集17个新学者素材时，完全没读 data-search/SKILL.md 和 academic-research-hub/SKILL.md，直接手写bash脚本用Exa API裸搜。

**正确做法**：
1. `data_search.py person <name>` — 自动多源搜索（通用+中文+学术），去重+质量评分
2. `search_academic` — arXiv + Semantic Scholar 论文搜索
3. `academic-research-hub/research.py` — 下载论文PDF、提取引用
4. `bilibili_transcribe.py` — B站视频转录

**后果**：
- 17人只有Exa网页搜索结果，没有论文
- 浪费API调用，绕过了已有的去重/质量评分机制
- 违反了MEMORY.md红线："执行任务前必须先读SKILL.md"

**根因**：赶进度心切，跳过了读文档的步骤

### 2026-04-26: 不读文档就动手，反复重写已有脚本

**Category**: correction
**Severity**: critical
**Context**: 黄老板让补全论文素材，我没有读 data_search.py 的SKILL.md，不知道里面已经有完整的 SS搜索DOI → Sci-Hub下载PDF → 全文提取 管线。自己重写了一个 fetch_abstracts.py 只补摘要不下载全文。被纠正后又要删 Semantic Scholar，破坏了完整链路。

**What went wrong**:
1. 没读SKILL.md就动手
2. 不理解管线架构：SS搜索DOI是上游，Sci-Hub下载是下游，是一条完整链路
3. 凭记忆瞎搞，每次都重写脚本

**Correction**:
- 执行任何任务前，先查 MEMORY.md 的模块索引表
- 有现成模块就用，禁止重写
- data_search.py 是统一入口：search_academic() 搜论文 → download_scihub_pdf(doi) 下载全文
- SS搜索和Sci-Hub下载是同一条管线，不要拆开

**Prevention**:
- MEMORY.md 已建立模块索引表（任务→脚本→函数映射）
- 红线新增：禁止重写已有脚本
---

### 2026-04-27: data_search.py Tavily字段名bug + Sci-Hub空PDF + 环境变量缺失

**Category**: correction
**Severity**: high
**Context**: 素材采集全部返回空结果，花了大量时间排查。

**What went wrong**:
1. Tavily API返回字段是 `results`，代码写的是 `articles`，永远返回空
2. Sci-Hub下载PDF时没检查文件大小，写了0字节文件，fitz打开崩溃
3. API key没设置环境变量，`_get_working_key` 返回None，所有付费搜索跳过
4. name_map.json 没有新人条目，person搜索直接报错跳过

**Fix**:
- `get("articles")` → `get("results")`
- Sci-Hub下载加了 `len > 1000` 和 `%PDF` magic bytes 检查
- 脚本里 export 环境变量（EXA/TAVILY/SERPAPI）
- 新人注册到 name_map.json

**Prevention**:
- 改完API对接代码必须实际测试返回结果
- 环境变量要在脚本启动前设置
- 新人物采集前先注册 name_map
