---
name: think-tank
description: "多视角人格智库系统（统一pipeline）。四套体系（联参25人/设计师24人/学者6人/心理学家24人），v3逻辑溯源。采集→提取→蒸馏→入库→张力→RAG报告。触发词：智库讨论、人格蒸馏、人格还原、战略报告、pipeline、逻辑溯源。"
metadata: {"openclaw":{"emoji":"🏛️","os":["linux","darwin","win32"]}}
---

# 🏛️ 人格智库系统

核心理念：**独立思考，真实还原**。每位人物保持独立视角，忠实于其真实立场。矛盾是自然产生的，不是强求的。

## 统一 Pipeline

所有四套体系共用一个入口：`think-tank/pipeline.py`

```bash
cd ~/workspace/think-tank
python pipeline.py list                          # 查看全部79人状态
python pipeline.py list -c joint-staff            # 查看某类
python pipeline.py run -c <category> -p <person>  # 全流程（提取→蒸馏→入库）
python pipeline.py extract -c scholars -p 张维迎  # 只提取
python pipeline.py distill -c designers -p Goyard # 只蒸馏
python pipeline.py ingest -c joint-staff           # 全部入库
python pipeline.py tension                         # 张力分析（四套）
python pipeline.py report -c joint-staff -t "议题"  # 联参战略报告
```

### 配置文件
`think-tank/config.yaml` — 四套体系差异全部在此：
- 联参/学者/心理学家：Lacan标准四维
- 设计师：Lacan审美适配版（审美无意识/签名重复/风格转移/创作驱力）
- 采集源、目录结构、向量库路径

## 四套体系

| 体系 | 人数 | 目录 | Lacan维度 | 张力维度 | 报告引擎 |
|------|------|------|-----------|---------|---------|
| 联合参谋部 | 25 | `joint-staff/{group}/{person}/` | standard | intuition × logic | ✅ v5 RAG |
| 设计师 | 24 | `designers/{brand}/` | aesthetic | art × commerce | ❌ |
| 学者 | 6 | `scholars/{sub}/{person}/` | standard | intuition × logic | ❌ |
| 心理学家 | 24 | `psychologists/{sub}/{person}/` | standard | theory × application | ❌ |

## 流水线步骤

### Step 1: 素材采集（skill层）
各体系采集源不同，使用对应的skill：
- 联参：`joint_staff_research.py`（网页抓取）
- 设计师：`batch_fetch_materials.py`（网页）+ `analyze_images_v2.py`（图片）
- 学者：`bilibili_download.py`（B站）+ `collect_papers.py`（论文）
- 心理学家：`collect_papers.py`（论文）+ web采集

素材存放：`{person_dir}/materials/*.txt` 或 `{person_dir}/persona/materials/*.txt`

### Step 2: 结构化提取
```bash
python scripts/_extract_one.py <素材文件> <source> <is_military> <输出> <mid> <title>
```
输出：`{person_dir}/_extraction_cache/*.json`

### Step 3: 人格蒸馏（v3）

```bash
# v3蒸馏 = v2 + logical_tracing（逻辑溯源）
python scripts/distill_v2.py
```
输出：`{person_dir}/personality_v2.json`

**v3 新增 logical_tracing** — 回答"是什么让这个人这么想"（而非只回答"怎么想"）：

| 维度 | 说明 | 子字段 |
|------|------|--------|
| `lineage` | 思想谱系 | inherited(继承) / transformed(改造) / ruptured(断裂) / converged(融合) |
| `turning_points` | 思想转折 | period, title, narrative, catalyst, before/after_state, significance, source_ref |
| `grounding` | 扎根经验 | embodied(肉身亲历) / contextual(语境沉浸) |
| `collaborations` | 关键合作 | with, nature(互补/对抗/师徒), product, significance |
| `renunciations` | 拒绝与放弃 | what, reason, aftermath（一个人拒绝了什么，比接受了什么更定义他） |
| `adversaries` | 论敌 | who, axis, dynamic（交锋如何塑造思想） |
| `tension_explanation` | 张力溯源 | 用溯源故事解释张力分数（张力是思想史的化石） |

**提取三层法：**
1. **显性**（直接提取）：自述"我年轻时……"、"受到X的影响……"
2. **半隐性**（推理提取）：频繁引用的人物→inherited；"反对/拒绝/超越"→renunciations
3. **隐性**（深度推理）：抽象理论中嵌入的具体场景→embodied grounding；反复出现的焦虑点→contextual grounding

蒸馏模板：`think-tank/templates/distill_prompt_v3.md`

### Step 4: 向量入库
```bash
python scripts/incremental_vecdb.py <person_name> <person_dir>
```
入库到 `think-tank/_chromadb/lacan_db/`，7个集合：
- **核心四维**：unconscious / repetition / transference / drive
- **扩展**：core_profile / insights(2035条) / tension(31条)

### Step 5: 张力分析
```bash
python think-tank/tension_framework.py
```
- 联参/学者：intuition_score × logic_score
- 设计师：art_score × commerce_score
- 心理学家：theory_score × application_score（百分位归一化）
- 输出：`think-tank/tension_analysis.json`

## 报告生成

### 联参战略报告 v5
```bash
python think-tank/joint-staff/joint_staff_report.py "议题" --research
python think-tank/joint-staff/joint_staff_report.py "议题" --deep
```

**v5 特性 + logical_tracing 融合：**
- **RAG锚点**：每人发言前检索10条最相关素材
- **张力排序**：冲突最大的人先发言
- **溯源深度**：RAG回答"他说过什么" + logical_tracing回答"他为什么会这么说" = 有因果链条的分析
- **碰撞发现**：两个人的 lineage.ruptured 指向同一对象时，自动识别为报告中的对立碰撞点
- **选人策略**：不只按向量相似度选人，还按 tension 维度选互补视角

### 多体系通用报告
所有四套体系均可使用类似RAG模式：
1. 给定议题，按向量相似度+tension互补选人
2. 每人基于 RAG素材 + logical_tracing 生成分析
3. 识别 lineage 碰撞点，组织多人对话
4. 报告末尾标注"AI模拟"

## 应用层：人格推理

### 两层架构

| 层 | 类型 | 内容 |
|----|------|------|
| Layer 1 | 静态知识库 | personality_v2.json + 向量库 → "这个人会怎么看这类问题" |
| Layer 2 | 动态推理引擎 | 当前事件 + 人格画像 → 模型扮演人物做判断 |

### logical_tracing 在推理中的价值
- **预测思维路径**：turning_points 和 grounding 帮助预测面对新问题时此人会激活哪些思维模式
- **发现碰撞**：两个人的 lineage.ruptured 指向同一对象 → 天然对立
- **理解偏差**：tension_explanation 帮助预判回答中的倾向性偏差

### 使用场景
- **多视角报告**：给定事件，多个人格各自分析 → 综合报告
- **人格还原**：给定决策场景，预测某人的判断 → 回测验证
- **张力碰撞**：对比两个人格在同一议题上的差异 → 对话式分析

## 人格还原（person-replication）

目标：不只模拟说话风格，而是还原**决策逻辑**。面对新事件时，AI判断应与本人高度一致。

### 与智库的区别
- 智库 = 多视角评论分析（输出观点）
- 人格还原 = 预测个人决策（输出判断）

### 回测验证
1. 选一个该人物过去做过的决策
2. 只给决策前信息（不含结果）
3. 让模型判断该人物会怎么做
4. 对比预测 vs 实际
5. 分析偏差，调整模型

## 向量库查询

```python
import sys; sys.path.insert(0, 'scripts')
from personality_vecdb import PersonalityVectorDB

db = PersonalityVectorDB()

# 获取人格画像（四维聚合+tension）
profile = db.get_profile("林彪")

# 语义相似度检索
results = db.find_similar("精细计算兵力", "unconscious", person="林彪", n=5)

# 人格对比
cmp = db.compare("毛泽东", "林彪")

# 查看状态
db.stats()
```

## 目录结构
```
think-tank/
├── config.yaml              # 四套体系统一配置
├── pipeline.py              # 统一流水线入口
├── tension_framework.py     # 张力分析框架
├── tension_analysis.json    # 张力结果
├── templates/
│   ├── distill_prompt_v3.md # v3蒸馏模板（含logical_tracing）
│   └── person_schema_v2.json
├── _chromadb/
│   ├── lacan_db/            # 7集合（四维+core_profile+insights+tension）
│   └── designers/
├── joint-staff/
│   ├── joint_staff_report.py    # v5 RAG报告引擎
│   ├── joint_staff_llm.py       # 25人角色定义
│   ├── reports/
│   └── {group}/{person}/
├── designers/
│   ├── build_rag.py
│   ├── analyze_images_v2.py
│   └── {brand}/
├── scholars/
│   ├── _distillation/
│   ├── _processed_bilibili.json
│   └── {sub}/{person}/
├── psychologists/
│   └── {sub}/{person}/        # 24人数据
scripts/
├── _extract_one.py
├── distill_v2.py
├── incremental_vecdb.py
├── personality_vecdb.py
├── tension_framework.py
├── joint_staff_research.py
└── auto_distill.py
```

## API Keys（已配置）
- 智谱GLM：见 TOOLS.md
- SiliconFlow Embedding：见 TOOLS.md
- Tavily搜索：见 TOOLS.md

## 红线
- API调用间隔 ≥ 6秒
- 人无完人：资料必须包含正反两面
- 分析必须基于RAG素材，禁止凭空编造
- 每份报告末尾标注"AI模拟"
- logical_tracing 的 source_ref 必须指向真实素材，禁止编造思想来源
- turning_points 必须是渐进叙事（不是瞬间切换）
- tension_explanation 必须基于素材证据，不是事后合理化
