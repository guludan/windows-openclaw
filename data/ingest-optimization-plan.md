# 入库优化方案 — 中英文双语入库

## 现状问题

| 内容 | 中文 | 英文 |
|------|------|------|
| Claims（观点） | 2336 (99.5%) | 12 (0.5%) |
| Evidence（证据） | 1170 (18.8%) | 5061 (81.2%) |

**核心矛盾**：Claims是中文（LLM生成），Evidence是英文（原始论文）。用户用中文检索时，需要同时命中中文观点和英文证据。

## 当前方案问题

1. **bge-m3直接embed中英文混合** — bge-m3本身支持多语言，但中英文向量空间不完全对齐，跨语言检索精度下降
2. **evidence全是原文excerpt** — 英文excerpt无法被中文query直接命中
3. **没有语言标记** — metadata里没有language字段，无法按语言过滤

## 优化方案：双轨入库

### 集合设计（6个集合）

| 集合 | 内容 | 语言 | 用途 |
|------|------|------|------|
| `core_profile` | brief+school+domains+evolution | 中文（原文） | 核心定位检索 |
| `insights` | claim+dim | 中文（原文） | 观点检索（主检索入口） |
| `insights_en` | claim翻译为英文 | 英文 | 英文query命中观点 |
| `evidence` | excerpt原文 | 混合（原始语言） | 证据溯源 |
| `evidence_cn` | excerpt翻译为中文（仅英文excerpt） | 中文 | 中文query命中英文证据 |
| `tension` | 冲突/张力点 | 中文 | 矛盾分析 |

### 入库流程

```
personality_v3.json
    ↓
┌─ core_profile: brief+school+domains → embed(bge-m3, 原文)
├─ insights: claim → embed(bge-m3, 中文原文)
├─ insights_en: translate(claim) → embed(bge-m3, 英文)  [新增]
├─ evidence: excerpt → embed(bge-m3, 原文)
├─ evidence_cn: translate(excerpt) → embed(bge-m3, 中文) [新增，仅英文excerpt]
└─ tension: 张力点 → embed(bge-m3, 中文)
```

### 翻译策略

- **Claims翻译**：已有中文claim，用LLM翻译为英文（批量，不逐条调API）
- **Evidence翻译**：仅翻译英文excerpt为中文（5061条），中文excerpt保留不翻
- **翻译质量控制**：保留原文ID映射，翻译只做附加，不替换原文

### 检索策略

```
用户Query（中文）
    ↓ embed(query)
    ├─ 检索 insights（中文claims）     → 高精度命中
    ├─ 检索 evidence_cn（中文证据）     → 跨语言命中英文证据
    └─ 检索 core_profile              → 人物定位

用户Query（英文）
    ↓ embed(query)  
    ├─ 检索 insights_en（英文claims）   → 高精度命中
    ├─ 检索 evidence（英文原文）         → 原文证据
    └─ 检索 core_profile
```

### Metadata增强

每条记录增加：
```json
{
  "language": "zh|en",
  "original_id": "xxx_ins_001",
  "person": "人物名",
  "category": "scholars/economics",
  "dim": "method",
  "source_file": "原始文件",
  "is_translated": false
}
```

### 实现步骤

1. **清空现有chromadb**（lacan_db）
2. **批量翻译**：5061条英文excerpt → 中文（LLM批量翻译，每批50条）
3. **批量翻译**：2336条中文claim → 英文
4. **双轨入库**：原文+翻译分别写入不同集合
5. **验证**：中英文query分别测试检索精度

### 成本估算

- 翻译：~7400条 × ~100字 = ~740K字
- bge-m3 embedding：原文+翻译 = ~15000条
- 预计时间：翻译1-2小时，embedding入库30分钟

### 备选方案（低成本）

如果不想做翻译，可以：
- 只在metadata加language字段
- 检索时同时搜insights和evidence
- 依赖bge-m3的跨语言能力（精度会下降）
