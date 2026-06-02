# HEARTBEAT.md

## 盘中监测（午休 11:30~13:00）
午休时扫北向+主力资金+盘中V8信号，输出盘中简报：
```bash
python3 scripts/lunch_monitor.py
```
输出到 `memory/reports/lunch-{today}.md` 和 `D:/data/reports/{today}/午间盘面_{today}.md`

## 收盘后日更（15:30~16:30）
交易日日更顺序（数据源已统一为同花顺L1+mootdx TCP）：

### 行情层
1. `python3 scripts/update_daily_mootdx.py` — 个股日线（mootdx TCP，5,129只）
2. `python3 scripts/data_finance.py --update-daily` — 宏观(FRED)+ETF行情+两融+龙虎榜+北向
3. `python3 scripts/backfill_limit_up.py` — 从 daily_prices 反算涨停写入 daily_limit_up

### V6 引擎
4. `python3 scripts/pool_engine_v1.py` — 大盘相空间[B]/[A]递推
5. `python3 scripts/pool_regime_integration.py` — 动态牛熊检测（surplus_z + 状态机 → regime）
6. `python3 scripts/stock_energy_incr.py` — 个股股票能量增量补全
7. `PYTHONUNBUFFERED=1 python3 scripts/fetch_exec_hold_daily.py` — 董监高持股变动增量（当日）

### 基本面
8. `python3 scripts/fetch_financial_data_v2.py` — 季度财报（mootdx F10，每季初跑）

### 简报+信号
9. 生成A股简报 → `memory/reports/a-share-{today}.md`
10. `python3 scripts/strategies/v8_dashboard.py` → `memory/reports/v8_dashboard_{today}.txt`
11. `python3 scripts/position_tracker.py` → `D:/data/reports/{today}/持仓资金行为监控_{today}.md`
12. `python3 scripts/zhuangjia_scanner_v3.py` → `D:/data/reports/{today}/庄家行为监控_v3_{today}.md`
13. `python3 scripts/neutral_monitor.py` → `memory/reports/neutral_monitor_{today}.md`

### 归档至D盘
14. `python3 scripts/daily_report_to_disk.py` → 复制到 `D:/data/reports/{today}/`

### 💡 已废弃（不再运行）
- `fetch_industry_daily.py` / `fetch_industry_daily_v2.py` — 行业指数数据不再采集
- `sector_daily` / `industry_daily` 表已删除
- 行业超额由 `v6_multi_factor.precalc_all_industry_excess()` 从 `daily_prices` + `industry_components_ths_l1` 实时算

## 待办跟踪
- Finnhub ETF指数数据（需VPN）
- UCDP/ACLED冲突数据（需VPN）
- 行业成分股映射（`build_industry_components_ths.py` — 按需运行）
- 新闻联播（`data_acquisition.py --cctv --latest --build-events` — 按需运行）

## 定期任务
- 检查磁盘空间（阈值85%）
- 检查企业微信机器人 wecom-aibot 连接状态
