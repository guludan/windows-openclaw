# TOOLS.md - 本地配置笔记

## API Keys

### 搜索
- **SerpAPI** (Google/Bing 搜索API): `848c4ec87cac7776b9404f0af1aeb36fce936d1aa3aebe899664eaa43e67ccc2` (jaccck1234567@gmail.com)
- **SerpAPI 备用**: `cbf5714a2a6bafe15343f793774329a8e62bbe3ed1c2ec6eaef5c0210ed92cb1` (tearsjaccck@gmail.com)
- **Exa Search 主**: `7be7a599-fa7b-4107-9ef1-ad96646156c2` (tearsjaccck@gmail.com, working)
- **Exa Search 备**: `68d64b2b-0c48-4fd4-a1fb-74fb71f3f4ba` (jaccck1234567@gmail.com)
- **Tavily**: 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 (primary), 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 (backup)
  > Tavily keys可能已过期，优先用SerpAPI/Exa

### 航空
- **ICAO API**: 8d3f6e2a-9de9-4894-9bf4-8f9e1413c029
- **OPENSKY**: clientId=jaccck1234567@gmail.com-api-client, clientSecret=UOv4T51xiUEB1c4VEx7FAefNWynaTqAk

### 经济/金融数据
- **MAP KEY**: 758b37447501167e207b94e50bf793ef
- **EIA API**: 5k9gGtqYJZbo6xmBRntk1DqoXGbyQrFxRqYeJVqf
- **FRED API**: 14876f904987b05adca701fb6f6e026d
- **AIS API**: edf9248fb33dedf8e9c72f23b9d1e5bb6a31afff
- **Finnhub**: d709oh1r01qtb4ra0b50d709oh1r01qtb4ra0b5g
- **WTO API**: (pending)

### 冲突/政治数据
- **UCDP API**: 3704a6e6c39684d5 (需用header: `x-ucdp-access-token: <key>`，不再支持query参数)
- **ACLED**: email=jaccck1234567@gmail.com, password=Thugstools123@

### 航空/交通
- **ICAO API**: 8d3f6e2a-9de9-4894-9bf4-8f9e1413c029
- **OPENSKY**: clientId=jaccck1234567@gmail.com-api-client, clientSecret=UOv4T51xiUEB1c4VEx7FAefNWynaTqAk

### LLM
- **SiliconFlow** (兜底): sk-tnaasdpeozarapwsxndsewyobdlvmwauwoemzmwcvltbapmj
- **本地 Ollama Embedding**: http://localhost:11434/api/embed

## 财联社
- **AKShare**: `ak.stock_info_global_cls()` → DataFrame(标题/内容/时间)，最简单
- **Playwright备用**: `scripts/fetch_cls.py` (绕WAF)
- Cookies: `.cookies/cls_cookies.txt`

## 知乎 Cookies
- 路径: .cookies/zhihu_cookies.txt
- 状态: ✅ 正常（2026-04-24更新）
- 注意: cookies里有中文值，不能用MozillaCookieJar加载，需手动tab解析

## Ollama (本地 LLM + Embedding)
- **地址**: http://172.26.0.1:11434
- **Embedding 模型**: bge-m3:latest (1024维, 多语言100+语言, 中英文最优) — 推荐
- **Embedding 备用**: qwen3-embedding:latest (4096维)
- **LLM**: qwen3-embedding 仅 embed, 需 pull 大模型做蒸馏
- **调用**: POST /api/embed, {"model":"qwen3-embedding:latest","input":"text"}
- 优先用本地 Ollama embedding, SiliconFlow 仅兜底

## 数据存储
- **统一路径**: /mnt/d/data (Windows D:\data)
- 所有数据库、向量库、缓存数据放这里
- chromadb、postgres、redis 等数据目录都在此

## 注意
- SiliconFlow 要钱，仅作兜底
- 优先用本地 Ollama embedding
- 子 session 跑分析任务（coding plan 免费）
- **GLM API调用必须走子session（sessions_spawn）**，直接调API会被内容过滤400且花钱
- **所有智库报告保存到D盘**: `/mnt/d/data/think-tank-data/reports/`
- **Redis**: 127.0.0.1:6379, 数据在 `/mnt/d/data/redis/data/`, 二进制在 `~/.local/redis/bin/`
- **Pipeline v2**: `scripts/think_tank_pipeline_v2.py` — 子议题拆分搜索+归一化概率+学术化prompt

## WSL代理
- **V2RayN代理**: `http://172.26.0.1:10808`（需开启局域网访问）
- libgen.li、archive.org 等需走代理
- sci-hub.ru/.in 可直连
- 脚本里需 `export https_proxy=http://172.26.0.1:10808 http_proxy=http://172.26.0.1:10808`
