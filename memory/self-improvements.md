# Self-Improvements - 教训记录

> 每次犯错、被纠正、发现更好方法时，必须记录。执行任务前必读。

---

## 2026-04-28: 数量级必须反复核对，不能凭感觉报数字
- "Chris Miller" → 搜出数论教授和动画导演，全名是 Christopher M. Miller
- "Nate Silver" → 搜出生物医学信号处理论文
- "Juan Zarate" → Treasury War下载到内战论文
- **规则**: 短名/常见名搜索时，加领域关键词（如 "semiconductor", "forecasting", "geopolitics"）
- **name_map应存全名**: en字段用全名（Christopher Miller而非Chris Miller）

## 2026-04-28: libgen著作下载误匹配严重
- 7本著作下载到错误内容（同名论文/其他作者的书）
- 根因: libgen搜索只按标题匹配，不带作者，同名书很多
- **验证**: 下载后必须检查内容是否为正确作者的书，不能只看标题匹配
- 已确认错误的: Flashpoints(Friedman), Next 100 Years(Friedman), J Curve(Bremmer), Crisis Economics(Roubini), Disunited Nations(Zeihan), Active Measures(Rid), Treasury War(Zarate)

---

## 2026-05-10: 先查备份再决定方案，不要上来就重建
- 电脑重启后chromadb空了，没查备份就想重新向量化147人
- 备份就在D盘237MB，cp回来就解决了
- 浪费半小时排查"segfault"，实际是DB_PATH指向旧空路径
- **规则**: 数据丢了/空了 → 先查备份 → 再决定方案
- **规则**: DB_PATH只改一次，改完立即验证
- **规则**: 有现成脚本就用，不要手写新脚本

## 2026-05-10: WSL跨盘大文件操作用robocopy
- rsync/du/cp在D→D2跨盘操作时WSL2 OOM被杀
- Windows robocopy稳定快速，34秒926MB
- **规则**: 跨盘大操作一律用 `/mnt/c/Windows/System32/cmd.exe /c "robocopy ..."`

## 2026-04-21: chromadb 绝对不能并发
- chromadb 不支持多进程并发写入，WSL 9p 文件系统下尤其严重
- 并发写 → segfault / 数据库损坏 / SQLite 锁死
- **必须串行：一次只跑一个 python 进程访问 chromadb**

## 2026-04-25: 蒸馏必须逐篇读素材
- 一人10-60篇素材，不能一次全读，会幻觉
- 每篇读完提取 insights + 标注 source_ref，再读下一篇
- 需要10-30分钟/人，不能设短时间cron批量跑
- 蒸馏输出文件名是 `personality_v3.json`，不是 `distillate.json`

## 2026-04-25: 中文冒号导致 JSON parse 失败
- 蒸馏输出 JSON 中混入中文冒号（：）会导致解析失败
- 所有 JSON 输出必须用英文冒号

## 2026-04-29: 质量评分prompt batch_size必须=5
- batch_size=20导致单个prompt 200KB（20×10000字符预览），子session读不动
- 改为batch_size=5后，最大prompt约70KB，子session可直接读
- **不需要拆分split文件**，之前搞的split方案完全多余
- **每子session最多3个prompt**：>3个会context overflow，必须分多个子session
- **10并发**：glm-5-turbo + glm-4.7混用
- gateway超时是常态（~50%），必须立即重试spawn，不要空转等待
- **统一流程**：_preprocess --quality → quality_check_llm.py生成prompt(bat=5) → spawn子session(≤3prompt/session)读prompt+评分+apply

## 2026-04-29: scores JSON中文件名扩展名必须同步
- .txt→.md批量重命名后，_quality_scores.json中的filename也必须同步
- 否则apply时全部file not found，评分白做
- **修复脚本**：遍历所有scores JSON，filename.endswith('.txt')→替换为.md

## 2026-04-25: 素材超长需截断
- 单篇素材 >60000 字符会超出 LLM 上下文
- 蒸馏前需截断或分段

## 2026-04-25: Sci-Hub 禁止批量下载
- 一篇一篇，间隔60s+
- 批量爬会被封IP，得不偿失
- 镜像优先级：.in > .st > .ru > .se

## 2026-04-25: 质量评分只用 LLM，禁止规则评分脚本
- 规则评分不够准确，必须用 LLM 判断素材质量
- quality_check_llm.py + sessions_spawn

## 2026-04-25: GLM API 调用必须走 sessions_spawn
- 直接调会被内容过滤 400 且花钱
- 子 session 走 coding plan 免费

## 2026-04-26: 执行任务前必须先读 SKILL.md
- 不读就动手 = 瞎搞
- 凭记忆用 skill 会脱离红线
- **每次动手前：读 SKILL.md → 读 self-improvements.md → 再执行**

## 2026-04-26: 禁止手写 bash 绕过现有模块
- 有 data_search.py 就用 data_search.py
- 手写 Exa 裸搜绕过了预处理、质量评分等环节
- 导致缺论文、缺预处理、缺质量评分，脏数据入库污染向量库
- **有现成模块就用模块，不要自己造轮子**

## 2026-04-26: 素材采集必须走完整流程
- 采集（data_search + academic-research-hub补论文，每人30-50篇）
- 预处理（_preprocess.py --quality）
- LLM质量评分（quality_check_llm.py）
- 蒸馏（sessions_spawn）
- 入库（ingest_v3.py）
- **任何环节跳过都会导致下游质量问题**

## 2026-04-26: 大量论文素材只有元数据没有正文
- 979个paper文件中565个只有标题/DOI（58%），没有正文
- 之前报告"论文下载450篇成功"，但实际只抓了索引，没下载PDF全文
- SKILL.md写"全部108人已完成LLM质量评分"是假的，实际只有沈逸1人做了
- **没有验证就报告成功 = 欺骗用户**
- 必须验证下载结果是否真的有正文内容

## 2026-04-26: 学术论文补下载必须逐人逐篇
- academic-research-hub补下载论文全文，**禁止批量**
- 每人逐篇下载，间隔60s+，确认全文提取成功
- 下载完要验证：文件大小>1KB，非纯元数据

## 2026-04-26: 蒸馏必须逐篇处理，禁止一次性塞给LLM
- MEMORY.md红线、SKILL.md都写了"逐篇读素材"
- 还是3次犯同样的错：一次性把23篇素材塞给LLM，导致超时
- 正确做法：--per-file生成逐篇prompt，每个子session只处理1篇素材
- **全量蒸馏用 _batch_distill.py --per-file，增量用 incremental_distill.py --auto**
- 两者共用 _per_file_prompts/ 和 _per_file_results/ 目录

## 2026-04-29: spawn子session必须立即重试超时
- gateway超时率~50%，spawn失败后必须立刻重试，不要等
- 每个子session完成事件后立刻spawn下一批，保持流水线
- 5并发上限，glm-5-turbo模型
- 黄老板要求：超时自动重试，不要空转

## 2026-04-26: 删数据库必须经黄老板同意
- 删向量库、清空数据是破坏性操作，**绝对不能自己决定**
- 即使数据有问题，也要先报告情况，等黄老板决定怎么处理
- 这次一刀切清空了133人的正确数据，等于把劳动成果全毁了
- **任何破坏性操作：先报告，等指示**

## 2026-04-26: 犯错后必须记录，不能敷衍
- self-improvements 不是摆设
- 每次被纠正、发现错误、学到教训，必须立刻记录
- 不记录 = 下次还会犯同样的错
- "骗人"不是故意的，但不记录教训和不学习没有区别

## 2026-04-26: 代码任务交给 Claude Code
- 不手写复杂脚本，用 sessions_spawn 让 Claude Code 写
- 自己写的代码质量不如专业 coding agent

## 2026-04-25: 外部 API 禁止高频请求
- 间隔至少5-10s
- 不能 for 循环一秒一个请求
- 尊重对方服务器，保护自己的 IP

## 2026-04-25: 蒸馏后检查 domains 覆盖
- 防止某些领域知识在蒸馏过程中丢失
- 检查输出 JSON 的 domains 字段是否覆盖素材涉及的所有领域

## 2026-04-30: 现有模块有功能就必须用，不要绕路手写
- data_search.py 已有 Sci-Hub、download 等功能，还手写 curl + jina 脚本
- 正确做法：先查 MEMORY.md 模块索引 → 看有没有现成函数 → 没有再集成进模块
- 新增 fetch_webpage() + fetch_urls_to_materials() + CLI `fetch` 子命令到 data_search.py
- **SKILL.md已更新**，MEMORY.md模块索引已更新
- 黄老板要求：新功能必须同步更新文档，不能只改代码不改文档

## 2026-04-30: 蒸馏必须强调evidence（原文逐字摘录）
- 蒸馏最核心的输出是 insights_with_evidence，不是顶层人格字段
- evidence.excerpt 必须是**原文逐字摘录**，一字不改，禁止转述/概括/改写
- 每个 claim 至少1条 evidence，重要观点2-5条
- 没有 evidence 的 claim = 编造，比空数组更差
- 每人至少15-30个 insights
- evidence 结构：{"excerpt": "原文", "source_file": "文件名", "location": "位置描述"}
- **总结蒸馏红线时漏掉了evidence这个最关键要求**，这是蒸馏质量命脉

## 2026-05-11: 🔴 未经允许不允许在脚本中单独调用付费API
- **规则**: 未经黄老板明确允许，不允许在脚本中单独调用付费API（SiliconFlow、GLM等）
- sessions_spawn走coding plan，免费，可以正常使用
- 本地ollama调用免费，可以正常使用

## 2026-05-11: 向量入库批量写入导致电脑重启
- chromadb向量入库时批量写入太多数据，内存溢出导致电脑重启
- **规则**: 向量入库必须逐个入库，禁止大批量写入
- 蒸馏可以批量跑，只要不超出上下文限制即可
- **规则**: 超时就重试，不要自作主张换策略
- **规则**: 没有黄老板明确指令不要自作主张写新脚本，用现有脚本
- **规则**: embedding模型是bge-m3:latest（http://172.26.0.1:11434），不是qwen
- **规则**: qwen3:8b禁止调用，本项目不使用该模型
- **规则**: 蒸馏/骨架提取/量化生成等所有LLM任务必须走子session（sessions_spawn），不允许在脚本中直接调用本地ollama模型
- **规则**: 子session蒸馏合法模型只有glm-5-turbo和glm-5.1
- **规则**: pipeline报告生成也必须走子session，不允许脚本直接调模型

## 2026-04-30: regenerate_personality必须严格按模板字段名输出
- 子session在regenerate时自造字段名，没有按distill_prompt_v3.md模板
- turning_points用了event/description而不是period/title/narrative/catalyst/before_state/after_state/significance
- situational_triggers用了strength/response而不是condition/behavioral_shift/confidence/source_ref
- grounding.embodied用了experience/description而不是context/role/duration/impact
- lineage.transformed缺original/transformation，converged缺streams/product
- **解决方案**：regenerate prompt中必须附上完整的模板字段名要求，并明确说"字段名必须严格按以下结构，禁止自造字段名"
