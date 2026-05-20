# MEMORY.md - 小狗蛋的长期记忆

## 黄老板
- **称呼**: 黄老板
- **项目方向**: ① **人格智库** (think-tank) ② **人格还原** (person-replication) ③ **A股金融数据**
- **GitHub**: guludan
- **数据源**: PG工控机 192.168.3.14, AKShare(conda tt), Baostock

## 智库项目状态（2026-05-15）

### 新V3管线全线打通 ✅
154/154 人完成 dedup+regen
- merge(_per_file_results) → dedup(语义去重) → regen(顶层字段)
- 总insights: 8743，无evidence: 11条

## A股金融数据（2026-05-20）

### 已就绪
| 数据 | 范围 | 条数 |
|------|------|------|
| 日线 (daily_prices) | 2002-01 ~ 2026-05, 7,045只 | 8,899,351 |
| 行情快照 (market_snapshots) | 每日4,947只 | 日更新 |
| 板块日收益率 (sector_daily) | 板块级 | 日更新 |
| M2/M1同比+绝对值 | 2008-01 ~ 2026-04 月 | 各220 |
| 中国10Y国债收益率 | 2015 ~ 2026-05 日 | 2,842 |
| AAA企业债10Y | 2015 ~ 2026-05 日 | 2,842 |
| 信用利差 (AAA-10Y) | 2015 ~ 2026-05 日 | 2,842 |
| LPR 1Y/5Y | 2015-02 ~ 2026-05 月 | 各1,572 |
| SHIBOR(ON/1W/1M/3M) | 2016-07 ~ 2026-05 日 | 各2,285 |

### 补拉中
- **季度财务数据** (financial_data): 2,124只 / 16,958条，还有~3,000只待补

### 缺失（暂不可达）
- 申万/中信一级行业指数日线
- 北向资金
- 两融/ETF份额（akshare环境问题）

## 核心脚本（workspace/scripts/，已上GitHub）

### 金融数据
| 脚本 | 用途 | 运行环境 |
|------|------|----------|
| `data_finance.py` | 主入口：日线更新/行情快照/FRED宏观/加密/信号 | conda tt |
| `backfill_history.py` | A股日线回填（分段跑） | base Python |
| `fetch_financial_data.py` | 季度财报补拉（baostock, 2024-2025） | conda tt |
| `fetch_macro_cn.py` | 中国宏观指标（国债/SHIBOR/LPR/信用利差） | conda tt (akshare) |

### 智库
| 脚本 | 用途 |
|------|------|
| `_batch_distill.py --merge-per-file` | 逐篇蒸馏合并 |
| `batch_dedup_regen.py` | 语义去重+顶层字段regen |
| `regenerate_personality.py` | 人格顶层字段重生成 |
| `ingest_v3.py` | LanceDB入库 |
| `think_tank_pipeline_v3.py` | 智库报告生成 |

## 🔴 红线
- **质量评分5分制(1-5)，用action=keep/discard判断**
- **用skill前先读SKILL.md**
- **LanceDB/财务数据补拉严禁并发写入**
- **蒸馏实事求是，evidence.excerpt必须原文逐字摘录**
- **GLM调用走sessions_spawn，禁止直接调API**
- **SiliconFlow 禁止使用**
- **Sci-Hub间隔15秒，外部API间隔5-10秒**
- **长耗时脚本(>5min)需要在screen或独立进程跑，否则被SIGKILL**
- **Python 3.8不能装新包（yfinance/akshare冲突）**
- **论文下载必须验证作者身份**

## 关键经验
- 蒸馏输出: `personality_v3.json`
- Python 3.13 `{{ }}` 陷阱: 普通代码中双大括号→set literal, f-string别用dict unpacking
- Codex/Claude Code跑编码任务
- `_pg_execute()` 返回 (rows_list, columns_list) 元组
- Baostock财务数据速度~20只/20秒，需开screen后台跑
- 已有PG unique constraint = (indicator, date)
