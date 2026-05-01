---
name: data-search
description: "统一数据搜索与采集模块。按议题定向从多源拉取数据：通用搜索(SearXNG/DuckDuckGo/Exa/Tavily/SerpAPI)、中文深度(知乎/搜狗/头条/秘塔/财联社)、学术(arXiv/Semantic Scholar/PubMed)、宏观经济(World Bank/AKShare/FRED)。内置去重、质量评分、Playwright反爬降级。触发词：数据搜索、多源搜索、议题采集、搜索模块、data search、抓取时事、拉数据。"
---

# Data Search — 统一数据搜索与采集模块

按议题定向从多源拉取数据，自动去重、审计质量、降级处理。不是堆砌所有搜索源，而是根据议题智能匹配最相关的数据层。

## 架构

```
议题 + 关键词
  │
  ├─→ L1 通用搜索: SearXNG / DuckDuckGo / Exa / Tavily / SerpAPI（并行，全部跑）
  ├─→ L2 中文深度: 知乎 / 搜狗(Playwright) / 头条(Playwright) / 秘塔(Playwright) / 财联社
  ├─→ L3 学术支撑: arXiv / Semantic Scholar / PubMed（论文）
  ├─→ L4 宏观数据: World Bank / AKShare / FRED（定向指标）
  ├─→ L5 全文下载: Sci-Hub(论文) / libgen.com.im(著作) / archive.org(回退)
  │
  └─→ 去重 + 质量评分 + 格式化输出
```

## 依赖

- Python: `~/miniconda3/envs/tt/bin/python3`
- 模块: `requests`, `akshare`(可选), `redis`(可选)
- Playwright: `/snap/bin/chromium`（中文搜索必需，Playwright-core 反爬降级用）
- Node.js: 用于运行 Playwright 脚本
- 无额外 pip 安装，全部用标准库 + 已有依赖

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `SEARXNG_URL` | 否 | SearXNG实例URL（默认：http://localhost:8888） |
| `EXA_API_KEY` | 否 | Exa搜索(付费) |
| `TAVILY_API_KEY` | 否 | Tavily搜索(付费) |
| `SERPAPI_KEY` | 否 | SerpAPI(付费) |
| `FRED_API_KEY` | 否 | FRED宏观(免费注册) |
| `REDIS_PASSWORD` | 否 | Redis缓存 |
| `https_proxy` | 著作下载需要 | WSL代理，如 `http://172.26.0.1:10808` |

## 使用方式

### 1. 作为Python模块调用（推荐）

```python
from data_search import search

# 按议题搜索，返回统一格式
results = search(
    question="存款90万如何躺平",
    layers=["general", "chinese", "macro"],  # 按需选择层级
    dedup=True,                                # 自动去重
    max_per_source=5                            # 每源最多5条
)

# results 结构
{
    "general": [{"title": ..., "text": ..., "url": ..., "source": "Tavily", "score": 0.9}],
    "chinese": [{"title": ..., "text": ..., "url": ..., "source": "知乎", "score": 0.8}],
    "macro": [{"indicator": "中国CPI", "date": "2024", "value": "0.22", "source": "World Bank"}],
    "summary": {"total": 15, "sources": {"Tavily": 5, "Exa": 3, "知乎": 2, "World Bank": 5}}
}
```

### 2. 命令行快速测试

```bash
~/miniconda3/envs/tt/bin/python3 skills/data-search/scripts/data_search.py \
  search "中美贸易战对A股的影响" --layers general,chinese,macro --max 5
```

### 3. 人物搜索（自动中英文双语）

```bash
python3 data_search.py person "林毅夫" --output-dir /path/to/materials
```

自动读 `name_map.json` 解析中英文名，双语搜索合并去重。新人物不在映射中会返回提示，需先调 `resolve_new_person()` 推断别名。

### 4. 单层调用（灵活）

```python
from data_search import search_general, search_chinese, search_academic, search_macro

# 仅通用搜索
news = search_general("通胀风险", max_results=5)

# 仅知乎搜索
zhihu = search_chinese("躺平", max_results=5)

# 仅宏观数据
macro = search_macro("CPI 利率 汇率")

# 仅学术搜索
papers = search_academic("躺平 FIRE movement", max_results=3)
```

## 各层详细说明

### L1 通用搜索 (`search_general`)

**数据源**: SearXNG → DuckDuckGo → Exa → Tavily → SerpAPI（并行，全部跑，能通就用）

**适用**: 国际新闻、英文资讯、通用搜索

**返回格式**:
```python
[{"title": str, "text": str, "url": str, "pub": str, "source": str}]
```

**特点**:
- SearXNG: 自建元搜索引擎，聚合Google/Bing/DDG（优先，免费）
- DuckDuckGo: HTML版本直接抓取（次优先，免费）
- Exa: 长文优先，500+字结果（付费）
- Tavily: 深度搜索模式（付费）
- SerpAPI: Google搜索结果（付费）
- 全部失败返回空列表，不报错

### L2 中文深度 (`search_chinese`)

**数据源**: 知乎 → 搜狗(Playwright) → 头条(Playwright) → 秘塔(Playwright) → 财联社

**适用**: 中文互联网真实声音、民间经验、政策快讯

**返回格式**: 同L1

**降级策略**:
1. 知乎API: 有cookie直接调，401则降级
2. 搜狗搜索: 通过Playwright渲染搜索结果（绕反爬）
3. 头条搜索: 通过Playwright渲染搜索结果（头条资讯）
4. 秘塔AI搜索: 通过Playwright抓取AI摘要
5. 财联社: AKShare `ak.stock_info_global_cls()` 免费接口

**Playwright降级**: 当API全挂时，启动无头浏览器抓取搜索结果页面，提取标题+摘要。搜狗/头条/秘塔共用同一个无头浏览器实例，减少资源开销。

### L3 学术支撑 (`search_academic`)

**数据源**: arXiv → Semantic Scholar → PubMed

**适用**: 学者论文、学术引用、理论支撑

**返回格式**:
```python
[{"title": str, "authors": [str], "abstract": str, "url": str, "year": str, "source": str}]
```

**特点**:
- arXiv: 免费，物理/数学/计算机/量化金融
- Semantic Scholar: 免费，跨学科，有引用关系，用于搜索DOI
- PubMed: 免费，生物医学
- 返回论文摘要，不含全文

### L5.5 网页内容抓取 (`fetch_webpage` / `fetch_urls_to_materials`)

**用途**: 批量抓取URL列表的内容，保存为素材`.md`文件。适用于搜索后拿到URL列表，需要逐篇提取正文内容的场景。

**原理**: 通过Jina Reader API（`r.jina.ai`）将网页转为Markdown，走WSL代理访问。

**Python调用**:
```python
from data_search import fetch_webpage, fetch_urls_to_materials

# 抓取单个URL
text = fetch_webpage('https://example.com/article')

# 批量抓取URL列表并保存为素材
urls = [
    {"title": "文章标题", "url": "https://example.com/article"},
    ...
]
stats = fetch_urls_to_materials(
    urls,
    output_dir='/path/to/Person_Dir/',  # 人物目录
    delay=6.0,                            # 间隔秒数（最小5秒）
    proxy='http://172.26.0.1:10808'       # Jina代理
)
# stats: {"saved": N, "skipped": N, "failed": N, "files": [...]}
```

**CLI调用**:
```bash
# 1. 先用SerpAPI等收集URL列表，保存为JSON
python3 data_search.py fetch urls.json --output-dir /path/to/Person_Dir/

# urls.json格式: [{"title": "...", "url": "https://..."}, ...]
```

**自动处理**:
- 跳过YouTube/Twitter/Reddit等非内容站
- 跳过已存在的文件（>500字节）
- 自动创建 `materials/raw/` 目录
- 文件名sanitize
- **间隔6秒**（红线：外部API 5-10秒间隔）

### L5 全文下载 (`download_scihub_pdf` / `download_book`)

**论文下载**:
```python
from data_search import download_scihub_pdf
# 用DOI下载论文PDF
pdf_path = download_scihub_pdf('10.1038/nature12373', output_dir)
```
- 镜像：sci-hub.ru → sci-hub.in（可直连）
- 间隔15秒，防封

**著作下载**:
```python
from data_search import download_book
# 用书名搜索下载
proxies = {'https': 'http://172.26.0.1:10808', 'http': 'http://172.26.0.1:10808'}
path = download_book('Thinking Fast and Slow', output_dir, proxies=proxies)
```
- 回退链：libgen.com.im → archive.org
- 需要代理（WSL: `http://172.26.0.1:10808`）
- 下载后用PyMuPDF(fitz)提取文本即可

### L4 宏观数据 (`search_macro`)

**数据源**: World Bank → AKShare → FRED → 本地JSON

**适用**: GDP、CPI、利率、汇率、股市指数等

**关键词→指标映射**:
```
"通胀"/"CPI"/"物价"  → 中国CPI(世界银行) + 美国CPI(FRED)
"利率"/"LPR"/"加息"  → 中国LPR(AKShare) + 美国国债收益率(FRED)
"美元"/"汇率"/"人民币" → 美元指数(FRED) + 人民币汇率(FRED)
"股市"/"A股"/"上证"  → 上证指数(AKShare)
"黄金"/"金价"        → VIX恐慌指数(FRED)
"石油"/"油价"/"原油"  → 布伦特原油(FRED)
"失业"              → 失业率(FRED)
"GDP"               → GDP(世界银行)
"港股"/"恒生"        → 恒生指数(AKShare)
```

**优先级**: API在线 → 本地JSON缓存 → 跳过

**返回格式**:
```python
[{"indicator": str, "date": str, "value": float/str, "source": str}]
```

### 去重层 (`dedup_results`)

- 基于标题前40字符去重
- 跨源去重：不同搜索源返回同一篇文章只保留一条
- 保留信息量最大的版本（text最长的）

### 质量评分 (`score_result`)

每条结果评分 0-1：
- 标题与query关键词匹配度 (0-0.3)
- 正文长度（越长越可能有用）(0-0.3)
- 来源可信度（学术>官方新闻>论坛）(0-0.2)
- 时效性（越新越好）(0-0.2)

## 与其他Skill的关系

| 调用方 | 调用方式 | 说明 |
|--------|---------|------|
| **think-tank pipeline** | `import data_search` | pipeline的`search_current_affairs`替换为`data_search.search` |
| **policy-monitor** | 调用L2财联社层 | 复用财联社采集逻辑 |
| **独立使用** | CLI或import | 任何需要多源搜索的场景 |
| **独立使用** | CLI或import | 任何需要多源搜索的场景 |

## 文件结构

```
skills/data-search/
├── SKILL.md                    # 本文件
└── scripts/
    ├── data_search.py          # 主模块（所有搜索逻辑）
    └── test_data_search.py     # 测试套件（TDD）
```

## 素材保存工具函数

### `sanitize_filename(title, max_len=200)`
将标题转为安全文件名：去除HTML标签、智能引号(`''``""`)、特殊字符(`<>"|\`)。

### `result_to_md(result)`
将搜索结果dict转为markdown内容（含frontmatter: title/url/source/pub）。

### `save_result(result, output_dir, prefix="")`
单条搜索结果保存为`.md`到`output_dir/materials/raw/`。自动sanitize文件名、去重（文件存在时追加编号）。

```python
from data_search import save_result
save_result({"title":"标题","text":"正文","url":"URL","source":"Exa"}, "/path/to/person_dir")
```

### `save_results_batch(results, output_dir, prefix="")`
批量保存搜索结果。返回保存路径列表。

### `search_person()` 自动保存
`search_person(output_dir=...)` 调用时，自动将搜索缓存保存到`_search_cache/`，同时把有正文的结果调用`save_result()`存为md。

## 文件结构

```
skills/data-search/
├── SKILL.md                    # 本文件
├── templates/
│   └── collection_task_prompt.md  # 子session采集task模板
└── scripts/
    ├── data_search.py          # 主模块（所有搜索+保存逻辑）
    ├── bilibili_transcribe.py  # B站视频转录
    └── test_data_search.py     # 测试套件
```

## 约束

- 每个搜索源独立try/except，失败静默跳过
- 不硬编码API key，全部用 `os.environ.get()`
- 并行搜索但同一源不并发（防rate limit）
- **搜索结果必须保存为.md，禁止保存.json素材文件**
- **文件名必须经过sanitize_filename()处理**
- 搜索缓存（`_search_results_*.json`）保存到`_search_cache/`目录，init_db不注册
- 中文cookie路径: `~/.openclaw/workspace/.cookies/zhihu_cookies.txt`
- Playwright使用 `/snap/bin/chromium` 作为无头浏览器
- SearXNG默认实例: `http://localhost:8888`

## B站视频采集+转录

**脚本**: `scripts/bilibili_transcribe.py`

### 功能
搜索B站视频 → you-get下载 → ffmpeg提取音频 → faster-whisper转录 → 保存为`.md`格式

### 依赖
- `you-get`（tt conda env）
- `ffmpeg`（tt conda env）
- `faster-whisper`（tt conda env）
- Playwright（cookie获取+搜索）

### 用法
```bash
# 搜索并转录（关键词搜索，下载top N个视频）
~/miniconda3/envs/tt/bin/python3 scripts/bilibili_transcribe.py search \
  --keyword "沈逸 中美博弈" --max 5 \
  --output-dir /path/to/materials

# 直接下载指定BV号
~/miniconda3/envs/tt/bin/python3 scripts/bilibili_transcribe.py download \
  --bv "BV1Ur4y1D71T" \
  --output-dir /path/to/materials
```

### 输出格式
```
title: "BV号 转录"
source: bilibili
bvid: BV号
video_title: 标题
language: zh
date_added: "YYYY-MM-DD"
---

(转录文字内容)
```

输出文件名: `bilibili_{BV号}.md`

### Whisper模型选择
| 模型 | VRAM/速度 | 中文质量 | 推荐 |
|------|-----------|----------|------|
| tiny | 最快 | 差 | 快速预览 |
| base | 快 | 还行 | **默认，够用** |
| small | 中等 | 好 | 高质量需求 |
| medium | 慢 | 很好 | GPU可用时 |
| large-v3 | 最慢 | 最好 | GPU可用+高精度 |
