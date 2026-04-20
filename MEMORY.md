# MEMORY.md - 小狗蛋的长期记忆

## 黄老板
- 称呼：黄老板
- 项目方向：
  1. **人格智库** (think-tank) — 学者多视角评论，独立观点，自然碰撞
  2. **人格还原** (person-replication) — 还原真实人物的决策逻辑，预测其在新事件中的判断和决策
- GitHub 账号：guludan
- 服务器：阿里云 Linux (Alinux 8), 2核 3.5G 内存

## 技能体系（20+个）
核心技能：self-improving-agent, colleague-skill, agent-knowledge, knowledge-capture, agent-council, agent-autopilot, adaptive-reasoning
采集技能：playwright-mcp, playwright-scraper, whisper-transcribe, extract-pdf-text, academic-research-hub
工具技能：github-cli, api-gateway, summarize-pro, debug-pro, docker-essentials, pr-reviewer, task-status

## API Keys（已有）
- **智谱 (zai/GLM)**: auth-profiles.json 中 `19649c91787b...`，用于GLM-4V-Flash视觉模型等
- **SiliconFlow (Embedding)**: `sk-tnaasdpeozarapwsxndsewyobdlvmwauwoemzmwcvltbapmj`，Qwen3-Embedding-8B
- **Sci-Hub/论文下载**: 用 academic-research-hub 技能，可搜索下载全文PDF

## 本地服务
- **STT 语音识别**：http://127.0.0.1:9977 (jianchang512/stt, faster-whisper base模型, conda stt 环境)
- **B站视频提取**：B站API拉音频流 → ffmpeg转wav → STT转录（脚本 bilibili_download.py）
- **Embedding**：SiliconFlow Qwen3-Embedding-8B（`input` 不是 `inputs`，便宜首选）
- **Python 环境**：miniconda3 (base=3.13, stt=3.11)

## 智库Pipeline (政策/IR分析→金融市场)
- **Pipeline v2**: `scripts/think_tank_pipeline_v2.py`
  - 子议题拆分搜索（每个维度分学者/心理两路，每路6条）
  - 概率自动归一化（总和=100%）
  - 学术化prompt避免GLM 400过滤
  - 历史参照Redis长效化（10个事件）
  - 报告必须通过sessions_spawn生成（走coding plan，不花钱）
  - 保存路径: `/mnt/d/data/think-tank-data/reports/`
- **历史事件引擎**: `scripts/historical_context.py` (Redis DB0)
  - 预置: 海湾1991、伊拉克2003、两伊1980、赎罪日1973、苏伊士1956、油轮2019、俄乌2022、朝鲜1950、太平洋1941、中印1962
  - 可通过add_custom_event()添加
- **RAG**: 78人格（25军事+30学者+23心理），15,747条向量
  - Embedding: Ollama bge-m3 (1024维)
  - Collections: core_profile, insights, materials
- **已生成的报告**:
  - `亚洲热点研判v1_2026-04-20.md` (初版)
  - `亚洲热点研判v3_2026-04-20.md` (完整版，概率已归一化)

## 人格蒸馏v2→v3工作流
详细操作手册：**WORKFLOWS.md**（reset后必读）
- 四类数据源：联参25人(joint-staff)、设计师24人(designers)、学者(scholars)、心理学家24人(psychologists)
- 流程：素材→提取(_extract_one.py)→蒸馏(distill_v2.py)→入库(incremental_vecdb.py)
- **v3新增 logical_tracing（逻辑溯源）**：回答"是什么让这个人这么想"
  - 模板：`templates/distill_prompt_v3.md`
  - 6大维度：lineage(inherited/transformed/ruptured/converged)、turning_points、grounding(embodied/contextual)、collaborations、renunciations、adversaries
  - tension_explanation：用溯源故事解释张力分数
  - 所有字段带 source_ref 指向素材出处
  - 提取三层法：显性直接提取→半隐性信号推理→隐性行为痕迹反推
- 蒸馏后必须检查domains是否被覆盖，手动恢复
- 向量库：lacan_db 5集合(unconscious/repetition/transference/drive/core_profile)
- 报告用RAG驱动+领域边界+`[RAG: dim/source]`标记，不带版本号
- **已修复的坑**：中文冒号导致JSON parse失败（不是屏蔽）、Path未import
- **已废弃**：binary_sig维度、sessions_spawn、 Scholars B站管道(用新v2流程)

## 人格智库蒸馏流程 (标准化) ← 旧版，参考WORKFLOWS.md

## 工作流约定
- **黄老板说"分析"→必须调用智库**：用think-tank pipeline跑多视角专家分析，不要自己直接写
- **趋势查询自动报告
- **趋势查询自动报告**：黄老板输入类似"女士包包在德国"→自动搜索→抓取→入库→生成区域报告→push GitHub，不需要重复说明

## 重要经验
- **未经黄老板明确许可，不得使用 SiliconFlow（硅基流动）API**，即使作为保底也不行。SiliconFlow 要钱。
- **marxists.org 原始文献是核心素材**，不能当垃圾丢弃。毛泽东/林彪/周恩来的原文比二手评论更有价值。预处理时要注意区分原始文献和网页垃圾。
- B站反爬用 yt-dlp 会 412，必须用 Playwright
- Python 3.6 太旧，miniconda3 解决
- STT 安装需要 Python 3.11 (torch 2.1.2 兼容)
- Playwright 需要：atk, cups-libs, libXdamage, libXcomposite 等系统库
- ClawHub 安装限流：每个请求间隔 30s+
- **Sci-Hub镜像优先级：.st > .ru > .se**（.se被GFW屏蔽）
- **DNS改8.8.8.8后GitHub/SSH/pip全通**
- **read SKILL.md全文再判断功能**，不要只读前半就说没有
- **先读已有skill再写脚本**，避免重复造轮子
- **Claude Code用CLI --print模式**，ACP spawn不稳定
- **代码任务交给Claude Code**，我不手写
- **self-improving-agent skill用来记录经验教训**，犯错后主动更新
- **不要自作主张截断/跳过数据**，用户要全量就全量，大文件分段处理
- **大素材必须全文分块处理**（8000字一块带overlap），不能只取头尾8000字当全文处理——pipeline_v3_full.py的教训
- **先审查素材结构再跑任务**，乱码目录应该在Phase 1就发现并清理
- **不要假设乱码目录名是GBK编码问题就忽略**，应该识别为mojibake重复目录并删除
