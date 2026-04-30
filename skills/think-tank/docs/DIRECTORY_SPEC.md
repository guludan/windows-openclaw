# DIRECTORY_SPEC.md — 人物目录结构规范

每个人物目录（如 `/mnt/d/data/think-tank-data/personalities/{group}/{person}/`）的标准结构：

```
person/
├── _db.sqlite                 # 素材索引数据库（init_db.py 生成）
├── personality_v3.json        # 蒸馏后的人格模型（最终产出）
├── profile.json               # 人物基本信息（部分人物有）
├── meta.json                  # 人物元数据（部分人物有）
│
├── materials/
│   ├── raw/                   # ✅ 质量通过的素材（.md/.txt）
│   ├── _discarded/            # ❌ 质量评分未通过的素材（.md/.json）
│   ├── _duplicates/           # 重复素材
│   ├── _search_cache/         # 搜索结果缓存（.json，init_db不注册）
│   ├── _per_file_prompts/     # 逐篇蒸馏生成的prompts
│   ├── _per_file_results/     # 逐篇蒸馏结果JSON
│   ├── _quality_prompt_*.md   # LLM质量评分prompt（batch_size=8）
│   ├── _quality_report.json   # 质检报告（800字符预览）
│   └── _quality_scores.json   # LLM质量评分结果
│
└── papers/
    ├── raw/                   # 论文提取文本（.md）
    ├── pdfs/                  # 论文PDF原件
    ├── distillates/           # [旧版] 蒸馏后的insights JSON
    └── _meta.json             # 论文元数据（doi, year, title）
```

## ⚠️ 关键规则

1. **materials/根目录不放任何素材文件** — 所有素材在 `raw/` 或 `_discarded/` 里
2. **只保存 .md 和 .txt** — init_db只注册这两种格式，不注册 .json
3. **搜索缓存放 `_search_cache/`** — 带 `_` 前缀的目录 init_db 不扫描
4. **PDF原件放 `papers/pdfs/`** — `papers/raw/` 只放提取后的文本
5. **文件名必须 sanitize** — 去除 HTML 标签、智能引号、特殊字符

## 数据库表结构（_db.sqlite）

### items 表
素材文件索引，每个文件一行。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| person | TEXT | 人物名 |
| category | TEXT | 'materials' 或 'papers' |
| filename | TEXT | 文件名（纯文件名，不含路径前缀） |
| source_type | TEXT | 来源（paper/exa/tavily/zhihu等） |
| status | TEXT | 状态：`raw` / `quality_checked` / `discarded` |
| file_path | TEXT | 相对路径 |
| file_size | INTEGER | 文件大小 |
| char_count | INTEGER | 字符数 |
| quality_score | INTEGER | 质量评分（1-5） |
| quality_checked | INTEGER | 是否已质量检查 |
| distilled | INTEGER | 是否已蒸馏（0/1） |
| distillate_path | TEXT | 蒸馏产物路径 |
| distill_time | TEXT | 蒸馏时间 |
| insight_count | INTEGER | 蒸馏出的insight数量 |

### distillate_insights 表
每条insight的摘要，用于快速检索。

### processing_log 表
操作日志，记录每次处理动作。
