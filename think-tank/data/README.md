# 冲突事件数据库 — 使用指南

## 概览

本数据库服务于「智能算法驱动的民粹主义话语传播机制与国际关系危机预警」研究，整合多个冲突事件数据源到统一的 SQLite 数据库。

**数据库文件**：`think-tank/data/populist_crisis.db`

## 数据源说明

### 1. ACLED（Armed Conflict Location & Event Data）

| 项目 | 说明 |
|------|------|
| 官网 | <https://acleddata.com/> |
| 覆盖范围 | 全球，每周更新 |
| 认证 | 免费注册 myACLED 账号 |
| 下载方式 | **网页手动导出**（Cloudflare 防护，无法 API 自动下载） |

**手动下载步骤**：
1. 登录 <https://acleddata.com/user/login>
2. 进入 [Data Export Tool](https://acleddata.com/conflict-data/data-export-tool)
3. 筛选条件建议：
   - **国家**：Ukraine, Russia, Poland, Hungary, Romania, Moldova, Belarus, Czech Republic, Slovakia, Lithuania, Latvia, Estonia
   - **时间**：2023-01-01 至今
   - **格式**：CSV
4. 导出后上传到服务器 `think-tank/data/acled/`
5. 也可以在 [Download Data Files](https://acleddata.com/conflict-data/download-data-files) 下载预打包文件

**导入命令**：
```bash
~/miniconda3/bin/python3 think-tank/data/data_import.py --source acled --file think-tank/data/acled/你的文件.csv
```

### 2. GDELT（Global Database of Events, Language, and Tone）

| 项目 | 说明 |
|------|------|
| 官网 | <https://www.gdeltproject.org/> |
| 覆盖范围 | 全球新闻，每 15 分钟更新 |
| 认证 | **无需认证**，直接 HTTP 下载 |

**下载页面**：

- **Events Database**（事件编码，CAMEO + Goldstein + Tone）
  <http://data.gdeltproject.org/events/index.html>
  - 文件：`YYYYMMDD.export.CSV.zip`（每天 5-8MB）
  - 从 2013 年至今

- **GKG 2.1**（主题/实体/语调/组织）
  <http://data.gdeltproject.org/gkg/index.html>
  - 文件：`YYYYMMDD.gkg.csv.zip`（每天 ~34MB）
  - 含 65 种语言实时翻译

- **在线查询导出**（GDELT Analysis Service）
  <https://www.gdeltproject.org/data.html>
  - 可视化筛选 + 导出
  - [Doc API](https://api.gdeltproject.org/api/v2/doc/doc?query=xxx&maxrecords=75&format=json)

**批量下载命令**（示例：最近30天）：
```bash
mkdir -p think-tank/data/gdelt
cd think-tank/data/gdelt
for i in $(seq 0 29); do
  d=$(date -d "$i days ago" +%Y%m%d)
  wget -q "https://data.gdeltproject.org/events/${d}.export.CSV.zip" -O "events_${d}.zip" &
done
wait
```

**导入命令**：
```bash
~/miniconda3/bin/python3 think-tank/data/data_import.py --source gdelt-events --dir think-tank/data/gdelt/ --pattern "events_*.zip"
~/miniconda3/bin/python3 think-tank/data/data_import.py --source gdelt-gkg --dir think-tank/data/gdelt/ --pattern "gkg_*.zip"
```

### 3. UCDP（Uppsala Conflict Data Program）

| 项目 | 说明 |
|------|------|
| 官网 | <https://ucdp.uu.se/> |
| API 文档 | <https://ucdp.uu.se/apidocs/> |
| 覆盖范围 | 全球，版本 25.1 含 385,918 条事件 |
| 认证 | 免费 Token，2026年2月起需 HTTP Header 认证 |

**API 格式**：
```bash
# 基础查询（需版本号）
curl -H "x-ucdp-access-token: TOKEN" \
  "https://ucdpapi.pcr.uu.se/api/gedevents/25.1?page=1&pagesize=100"

# 参数化查询（推荐，避免全量遍历）
# 支持: year, country_id, type_of_violence, region 等
```

**自动下载**（使用内置脚本）：
```bash
cd think-tank/data
~/miniconda3/bin/python3 download_ucdp.py
# 输出: ucdp/eastern_europe_events.csv
# 支持断点续传（记录进度到 _progress.json）
```

> ⚠️ **注意**：服务器内存有限（3.5G），全量遍历 38 万页会被 OOM Kill。建议用 API 参数化查询按国家/年份分批下载。

**导入命令**：
```bash
~/miniconda3/bin/python3 think-tank/data/data_import.py --source ucdp --file think-tank/data/ucdp/eastern_europe_events.csv
```

### 4. 社媒数据（Reddit / X / TikTok）

待实现。计划通过各平台 API 或 Playwright 采集，存入 `social_posts` 表。

### 5. 官方源（联合国/欧盟/北约/欧安组织）

待实现。计划通过 Playwright 采集决议、声明、制裁等官方文件，存入 `official_events` 表。

---

## 数据库结构

```
populist_crisis.db
│
├── 数据源层（原始数据）
│   ├── conflict_events    ← ACLED + UCDP 合并
│   ├── gdelt_events       ← GDELT Events（CAMEO/Goldstein/Tone）
│   ├── gdelt_gkg          ← GDELT GKG（主题/实体/语调）
│   ├── social_posts       ← 社媒帖子（待实现）
│   └── official_events    ← 官方事件源（待实现）
│
├── 分析层（派生指标）
│   ├── text_indicators    ← 每帖 P/M/N/H 四维 + 话语温度
│   ├── social_edges       ← 社交网络边 → 极化分析
│   ├── time_series        ← 日/周聚合序列 → 预警基线
│   └── simulations        ← 反事实仿真结果
│
├── 决策层
│   ├── alerts             ← 分级预警记录
│   └── alert_rules        ← 预警规则配置
│
└── 元数据
    ├── data_versions      ← 数据血缘
    ├── locations          ← 统一地理编码
    ├── actors             ← 政治行为体编码
    └── populism_lexicon   ← 多语民粹词表
```

完整 schema 见 `schema.sql`。

### 视图

| 视图 | 用途 |
|------|------|
| `unified_timeline` | 四层数据统一时间轴查询 |
| `daily_temperature` | 每日话语温度汇总（按平台/语种） |

---

## 话语温度指标体系

每条社媒帖子的分析指标存入 `text_indicators` 表：

```
Temp_i = 100 × (α·P_i + β·M_i + γ·N_i + δ·H_i)
```

| 指标 | 含义 | 数据来源 |
|------|------|---------|
| **P** 民粹强度 | 人民/精英/外群词密度 + 二元对立加分 | `populism_lexicon` 词表匹配 |
| **M** 道德化 | 道德化语言强度 | 词表 + NLP 分类器 |
| **N** 负性情绪 | 负性情绪唤起 | 情感分析模型 |
| **H** 威胁对抗 | 威胁化/对抗语言 | 词表 + 句法分析 |

默认等权：α=β=γ=δ=0.25，可通过 `alert_rules` 调整。

---

## 快速查询示例

```sql
-- 查看已导入的数据统计
SELECT source, COUNT(*) FROM conflict_events GROUP BY source;
SELECT COUNT(*) FROM gdelt_events;

-- 乌克兰冲突事件时间线
SELECT event_date, event_type, fatalities, actor_a, actor_b
FROM conflict_events
WHERE country LIKE '%Ukraine%'
ORDER BY event_date DESC LIMIT 20;

-- GDELT 俄乌新闻（按语调排序）
SELECT event_date, actor1_name, event_code, avg_tone, num_articles
FROM gdelt_events
WHERE action_geo_country_code IN ('RS','UP')
ORDER BY avg_tone ASC LIMIT 20;

-- 统一时间轴（最近30天，所有数据源）
SELECT * FROM unified_timeline
WHERE date >= date('now','-30 days')
ORDER BY date DESC LIMIT 50;
```

---

## 文件清单

| 文件 | 用途 |
|------|------|
| `schema.sql` | 数据库建表 DDL（15表+2视图） |
| `populist_crisis.db` | SQLite 数据库文件 |
| `data_import.py` | 数据导入脚本（ACLED/UCDP/GDELT） |
| `download_ucdp.py` | UCDP 自动下载脚本（断点续传） |
| `README.md` | 本文档 |
| `acled/` | ACLED 数据存放目录（手动下载上传） |
| `gdelt/` | GDELT 数据存放目录 |
| `ucdp/` | UCDP 数据存放目录 |
