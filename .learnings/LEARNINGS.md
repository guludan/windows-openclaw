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
