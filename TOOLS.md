# TOOLS.md - 本地配置笔记

## API Keys

### 搜索
- **SerpAPI** (Google/Bing 搜索API): `848c4ec87cac7776b9404f0af1aeb36fce936d1aa3aebe899664eaa43e67ccc2` (jaccck1234567@gmail.com)
- **SerpAPI 备用**: `cbf5714a2a6bafe15343f793774329a8e62bbe3ed1c2ec6eaef5c0210ed92cb1` (tearsjaccck@gmail.com)
- **Exa Search 主**: `7be7a599-fa7b-4107-9ef1-ad96646156c2` (tearsjaccck@gmail.com, working)
- **Exa Search 备**: `68d64b2b-0c48-4fd4-a1fb-74fb71f3f4ba` (jaccck1234567@gmail.com)
- **Tavily**: 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 (primary), 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 (backup)
  > Tavily keys可能已过期，优先用SerpAPI/Exa

## 经济/金融数据
- **FRED API**: 14876f904987b05adca701fb6f6e026d
- **EIA API**: 5k9gGtqYJZbo6xmBRntk1DqoXGbyQrFxRqYeJVqf
- **Finnhub** (ETF): d709oh1r01qtb4ra0b50d709oh1r01qtb4ra0b5g (VPN恢复后推进)
- **AKShare**: conda tt 环境可用
- **Baostock**: Python3环境可用（日线/财务数据）

## 搜索
- **SerpAPI** (Google/Bing): `848c4ec87cac7776b9404f0af1aeb36fce936d1aa3aebe899664eaa43e67ccc2`
- **SerpAPI 备用**: `cbf5714a2a6bafe15343f793774329a8e62bbe3ed1c2ec6eaef5c0210ed92cb1`
- **Exa Search 主**: `7be7a599-fa7b-4107-9ef1-ad96646156c2`
- **Exa Search 备**: `68d64b2b-0c48-4fd4-a1fb-74fb71f3f4ba`
- **Tavily**: 可能已过期，优先用SerpAPI/Exa

## 冲突/政治数据
- **UCDP API**: 3704a6e6c39684d5 (VPN恢复后推进)
- **ACLED**: email=jaccck1234567@gmail.com (VPN恢复后推进)

## 知乎 Cookies
- 路径: .cookies/zhihu_cookies.txt
- 状态: ✅ 正常
- 注意: cookies含中文值，不能用MozillaCookieJar加载

## Ollama (本地 Embedding)
- **地址**: http://172.26.0.1:11434
- **Embedding 模型**: bge-m3:latest (1024维, 多语言)
- **备选**: qwen3-embedding:latest (4096维)
- **调用**: POST /api/embed, {"model":"bge-m3:latest","input":"text"}

## 数据存储
- **PG数据库**: 工控机 192.168.3.14（日线/macro/财务/板块数据）
- **脚本**: workspace/scripts/（已上GitHub）
- **智库数据**: /mnt/d/data/think-tank-data/（D盘，数据文件不提交Git）
- **Redis**: 127.0.0.1:6379, 二进制 ~/.local/redis/bin/

## 企业微信机器人
- **Bot ID**: `aib5RbAzY0N-Cg_-xBCBxCG-Sj_DY2axkYg`
- **Secret**: `EUuYVxOGVTGCOVddxjBtcNmHABBLf38FaicLITuRGhY`
- **插件**: `wecom-aibot@0.2.0`
- **allowFrom**: `*`

## Python环境
- **Python 3.8**: /usr/bin/python3（base环境，装不了新包）
- **conda tt**: ~/miniconda3/envs/tt/bin/python3（Python 3.13，有akshare）
- **子session分析任务**: sessions_spawn 走 coding plan 免费
- **GLM调用**: 必须走sessions_spawn，直接调API会被过滤400

## PG数据库表
- `daily_prices` — 日线（2002~2026, 890万条, 7,045只）
- `market_snapshots` — 行情快照（每日，4,947只）
- `financial_data` — 季度财务数据（ROE/净利/营收/EPS，补拉中）
- `macro_indicators` — 宏观指标（M2/M1/10Y国债/SHIBOR/LPR/信用利差）
- `sector_daily` — 板块日收益率
- `stock_list` — 5,257只活跃A股
- `etf_list / etf_snapshots` — ETF
- `industry_components` — 行业成分
- `market_signals` — 信号数据

## WSL代理
- **V2RayN**: http://172.26.0.1:10808（需开启局域网访问）
- libgen/archive.org 等走代理，sci-hub.ru直连
