---
name: hotspot-monitor
description: "全维度热点监控引擎。整合财联社快讯、Twitter/X趋势、Reddit讨论、冲突事件(UCDP/ACLED)、自定义关键词监控，AI分析热点与金融市场的关联性，推送智能摘要。Use when user asks about: 热点监控, 信息监控, 舆情监控, 金融新闻, 市场情绪, hotspot monitoring, news surveillance, or wants to set up automated news/sentiment alerts."
metadata:
  clawdbot:
    emoji: "📡"
    requires:
      skills: []
---

# Hotspot Monitor — 全维度热点监控引擎

**冲突×政策×市场×舆论，一网打尽。**

## 概述

多源信息聚合监控，自动识别热点事件，AI分析其与金融市场的关联性，推送智能摘要。

## 数据源

### 触发条件

- 用户提到 "热点"、"舆情"、"监控"、"新闻聚合"
- 需要冲突事件/经济数据/市场情绪的综合视图
- 用户想设置信息监控推送
- 智库分析前的热点事件收集

## 操作步骤

1. 单源采集（按需）：运行对应脚本
2. 全量采集：`python3 scripts/analyze.py --all`
3. AI 分析热点与金融关联
4. 如需深度分析 → 联动 think-tank pipeline

### 数据源

| 源 | 方式 | 内容 | 频率 |
|----|------|------|------|
| 财联社 | REST API | 电报快讯、市场实时消息 | 实时 |
| 央行 | API | 金融统计、货币政策 | 月度 |
| 证监会 | Playwright | 监管动态 | 不定期 |
| UCDP | API | 全球冲突事件 | 日更 |
| ACLED | API | 冲突事件（更全） | 日更 |
| FRED | API | 美国经济数据 | 日更 |
| Finnhub | API | 全球市场指数 | 实时 |
| 自定义关键词 | Exa/SerpAPI | 定向搜索 | 按需 |

## 脚本

```
skills/hotspot-monitor/scripts/
├── fetch_cls.py          # 财联社快讯
├── fetch_ucdp.py         # UCDP冲突数据
├── fetch_acled.py        # ACLED冲突数据
├── fetch_market.py       # FRED/Finnhub市场数据
└── analyze.py            # 热点-市场关联分析
```

## 用法

### 1. 单源采集

```bash
# 财联社快讯
python3 scripts/fetch_cls.py -o /tmp/cls_items.json

# UCDP冲突（需VPN）
python3 scripts/fetch_ucdp.py

# 市场数据
python3 scripts/fetch_market.py --indices SPY,FXI,GLD
```

### 2. 全量采集+分析

```bash
python3 scripts/analyze.py --all
```

### 3. 智库联动

热点事件自动触发智库分析pipeline：
```python
from scripts.think_tank_pipeline_v2 import run_pipeline
task, _ = run_pipeline("热点事件对XX市场的影响")
```

## 输出

- JSON: 结构化事件数据（时间、来源、摘要、标签）
- 摘要: AI生成的热点简报（与金融关联度、情绪方向、建议关注）
- 自动入库Redis用于去重和历史查询

## API Keys

参见 TOOLS.md:
- FRED API: 14876f904987b05adca701fb6f6e026d
- Finnhub: d709oh1r01qtb4ra0b50d709oh1r01qtb4ra0b5g
- UCDP API: 3704a6e6c39684d5
- ACLED: email=jaccck1234567@gmail.com
