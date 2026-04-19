# WORKFLOWS.md - 定型工作流

> 最后更新：2026-04-19

## 一、人格蒸馏 V3 工作流（通用）

```
素材采集 → V3蒸馏(子session+GLM-5.1) → personality_v3.json → 向量入库(chromadb)
```

### 1. 素材采集

**论文素材（学者/心理学家 — 主粮）：**
1. **论文元数据**：`scripts/batch_scholar_papers.py`（SerpAPI Google Scholar，每人20篇）
2. **全文下载**：`scripts/scholar_download.py`（OpenAlex→Unpaywall(OA)→Sci-Hub+Playwright(非OA)）
   - 环境：`~/miniconda3/envs/tt/bin/python`（Python 3.13 + chromadb 1.5.8 + PyMuPDF + playwright）
   - Chromium：`/snap/bin/chromium`
3. **素材保存**：`{person}/materials/paper_{title}.txt`

**网页素材（联参/设计师 — 辅助）：**
- `web_fetch` 获取长文，`web_search` 获取摘要
- `materials/exa_{title}.txt` 或 `materials/serp_{title}.txt`
- 跳过 <500B 文件

### 2. V3 蒸馏（子session）

**不使用本地脚本调API**，用 `sessions_spawn` 启动子session：
- 子session自动使用OpenClaw配置好的模型（GLM-5.1）
- 任务prompt：读取模板 + 素材 → 生成 `personality_v3.json`
- 素材 >60000 字符需截断

**模板**：`think-tank/templates/distill_prompt_v3.md`

**V3 核心字段 logical_tracing：**
| 维度 | 说明 |
|------|------|
| lineage | 思想谱系：inherited/transformed/ruptured/converged |
| turning_points | 思想转折（渐进叙事，非瞬间切换） |
| grounding | 扎根经验：embodied(肉身亲历)/contextual(语境沉浸) |
| collaborations | 关键合作（互补/对抗/师徒） |
| renunciations | 拒绝与放弃（比接受更定义人） |
| adversaries | 论敌（交锋如何塑造思想） |
| tension_explanation | 张力分数的溯源解释 |

**红线：**
- 所有 source_ref 必须指向真实素材文件名
- 信息不足用空数组 `[]`，不编造
- 输出文件：`{person}/personality_v3.json`

### 3. 向量入库

- **脚本**：待重建（需要 `scripts/incremental_vecdb.py`）
- **向量库**：`think-tank/_chromadb/lacan_db/`（chromadb 1.5.8）
- **集合**：unconscious, repetition, transference, drive, core_profile, insights, tension
- **Embedding**：本地 Ollama bge-m3（`http://172.26.0.1:11434/api/embed`）
- **环境**：`~/miniconda3/envs/tt/bin/python`

## 二、论文下载工作流

### 已验证路径

| 步骤 | 工具 | 状态 |
|------|------|------|
| 搜索论文 | SerpAPI Google Scholar | ✅ |
| 查DOI+OA链接 | OpenAlex API（免费不限流） | ✅ |
| 查免费PDF | Unpaywall API（免费） | ✅ |
| 下载OA全文 | requests 直接下载 | ✅ |
| 下载非OA全文 | Sci-Hub + Playwright | ✅ |
| PDF文本提取 | PyMuPDF (fitz) | ✅ |

### 脚本
- `scripts/batch_scholar_papers.py` — 论文元数据拉取
- `scripts/scholar_download.py` — 全文下载（OA+Sci-Hub）

### 废弃方案
- ❌ Semantic Scholar：429限流严重
- ❌ SerpAPI搜DOI：返回数据无DOI字段
- ❌ 本地脚本调智谱API蒸馏：key过期，改用子session

## 三、四套体系

| 体系 | 人数 | 目录结构 | 状态 |
|------|------|----------|------|
| 联参(military) | 32 | `military/{person}/` | 素材✅ 蒸馏❌(V2已清) |
| 设计师(designers) | 21 | `designers/{brand}/` | 素材✅ 蒸馏❌(V2已清) |
| 学者(scholars) | 30 | `scholars/{sub}/{person}/` | 元数据✅ 下载🔄 蒸馏❌ |
| 心理学家(psychologists) | 24 | `psychologists/{sub}/{person}/` | 元数据✅ 下载🔄 蒸馏❌ |

### 人物名映射
`scripts/scholar_download.py` 中 `NAME_MAP` 字典：目录名 → OpenAlex搜索名

## 四、报告生成工作流（待实现）

### 核心思路
```
议题拆分(LLM) → 维度搜索(向量库) → RAG人格分析(子session) → 汇总报告
```

### 设计要点
- 用子session + GLM-5.1 分析，不回退
- 议题自动拆分3-6个分析维度
- 每个维度独立搜索+选人+分析
- 基于RAG素材+logical_tracing生成分析
- 报告标注"AI模拟"

## 五、环境配置

### Python
- **系统**：Python 3.8（太旧，chromadb不可用）
- **think-tank环境**：`~/miniconda3/envs/tt/bin/python`（Python 3.13）
  - 包：chromadb 1.5.8, requests, playwright, PyMuPDF (fitz)
- **激活**：`source ~/miniconda3/bin/activate tt`

### Chromium
- 路径：`/snap/bin/chromium`（Playwright用）

### Embedding
- 本地 Ollama bge-m3：`http://172.26.0.1:11434/api/embed`（1024维，中英文最优）

### DNS
- WSL默认DNS 10.255.255.254 间歇性挂
- 修复：resolv.conf加 `nameserver 8.8.8.8`

### API Keys
- **SerpAPI**：见 TOOLS.md（Google Scholar搜索）
- **Tavily**：见 TOOLS.md（备用搜索）
- **智谱GLM**：见 TOOLS.md（蒸馏用子session，不直接调API）
- **OpenAlex/Unpaywall**：免费，只需User-Agent含邮箱

## 六、脚本清单

| 脚本 | 用途 | 状态 |
|------|------|------|
| `scripts/batch_scholar_papers.py` | Google Scholar论文元数据 | ✅ |
| `scripts/scholar_download.py` | OpenAlex+Unpaywall+Sci-Hub全文下载 | ✅ |
| `scripts/_preprocess.py` | 素材预处理（去噪） | ✅ |
| `pipeline.py` | 统一流水线入口 | ⚠️ 需适配V3 |
| `tension_framework.py` | 张力分析 | ⚠️ 待验证 |

### 废弃脚本（已移到 _trash/）
- `distill_v3.py`、`enrich_dois.py`、`download_papers.py`、`batch_papers.py`
- 旧版采集脚本（_collect*.py, _collect*.sh）
