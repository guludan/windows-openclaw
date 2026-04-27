# DIRECTORY_SPEC.md — 人物目录结构规范

每个人物目录（如 `/mnt/d/data/think-tank-data/personalities/{group}/{person}/`）的标准结构：

```
person/
├── _db.sqlite                 # 素材索引数据库（init_db.py 生成）
├── _distilled_files.json      # [旧版] 已蒸馏文件记录（逐步弃用，改用DB）
├── personality_v3.json        # 蒸馏后的人格模型
├── _per_file_prompts/         # 逐篇蒸馏生成的prompts（临时，蒸馏完可删）
├── _per_file_results/         # 逐篇蒸馏结果（临时，合并完可删）
│
├── materials/
│   ├── raw/                   # 原始素材文本文件
│   ├── distillates/           # 蒸馏后的insights JSON
│   ├── _low_quality/          # 低质量文件（被preprocess移出）
│   ├── _duplicates/           # 重复文件（被dedup移出）
│   ├── _quality_prompt_*.md   # LLM质量评估prompt
│   ├── _quality_report.json   # 质量评估报告
│   └── _quality_scores.json   # LLM质量评分
│
└── papers/
    ├── raw/                   # 论文素材文本文件
    ├── distillates/           # 蒸馏后的insights JSON
    ├── pdfs/                  # 原始PDF文件
    ├── _discarded/            # 质量评分废弃的论文素材
    └── _meta.json             # 论文元数据（doi, year, title）
```

## 数据库表结构（_db.sqlite）

### items 表
素材文件索引，每个文件一行。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| person | TEXT | 人物名 |
| category | TEXT | 'materials' 或 'papers' |
| filename | TEXT | 文件名 |
| source_type | TEXT | 来源（paper/exa/tavily/zhihu等） |
| status | TEXT | 状态：raw / distilled / discarded |
| file_path | TEXT | 相对路径（如 materials/raw/xxx.txt） |
| file_size | INTEGER | 文件大小 |
| char_count | INTEGER | 字符数 |
| quality_score | INTEGER | 质量评分 |
| quality_checked | INTEGER | 是否已质量检查 |
| distilled | INTEGER | 是否已蒸馏（0/1） |
| distillate_path | TEXT | 蒸馏产物路径 |
| distill_time | TEXT | 蒸馏时间 |
| insight_count | INTEGER | 蒸馏出的insight数量 |

### distillate_insights 表
每条insight的摘要，用于快速检索。

### processing_log 表
操作日志，记录每次处理动作。
