---
name: policy-monitor
description: "中国宏观经济金融政策监控管道。自动采集央行、财联社、证监会、国家统计局的政策和数据，AI分析后推送飞书。Use when user asks about: 政策监控, 经济数据跟踪, 政策速报, macro policy monitoring, or wants to set up automated policy/financial news alerts."
---

# 中国政策金融监控管道

Python: `/home/admin/miniconda3/bin/python3`
Script: `scripts/pipeline.py`

## 触发条件

- 用户提到 "政策监控"、"央行"、"财联社"、"证监会"、"统计局"
- 需要 GDP/CPI/PMI 等经济数据
- 用户想设置政策速报推送

## 操作步骤

1. 单源采集：`python3 scripts/pipeline.py --sources cls -o /tmp/output.json`
2. 全量采集：`python3 scripts/pipeline.py --all -o /tmp/output.json`
3. AI 分析结果后推送

## 数据源

| 源 | 方式 | 内容 | 更新频率 |
|----|------|------|---------|
| 央行 | requests API | 金融统计、货币政策、发布会 | 月度 |
| 财联社 | REST API | 电报快讯、市场实时消息 | 实时 |
| 证监会 | Playwright | 监管动态、政策法规 | 不定期 |
| 统计局 | Playwright | GDP、CPI、PMI等 | 月度 |

## 用法

```bash
# Phase 1: 央行 + 财联社
/home/admin/miniconda3/bin/python3 scripts/pipeline.py --sources pbc cls -o /tmp/policy_items.json

# 全部数据源
/home/admin/miniconda3/bin/python3 scripts/pipeline.py --all -o /tmp/policy_items.json

# 仅财联社快讯
/home/admin/miniconda3/bin/python3 scripts/pipeline.py --sources cls
```

## 输出格式

JSON: `{"time": "...", "count": N, "items": [{"source","category","title","summary","url","id","time"}]}`

## 去重机制

基于 `.state/seen_<source>.json` 存储已见 item ID，首次运行会获取全量历史，后续只返回新增。

## Cron 集成

采集由 cron 定时触发，新内容经 AI 分析后推送飞书。
