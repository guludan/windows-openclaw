# 收盘后日更管线（15:30~）

## 行情层
1. `python3 scripts/update_daily_mootdx.py` — 个股日线（mootdx TCP）
2. `python3 scripts/data_finance.py --update-daily` — 宏观+ETF+两融+龙虎榜+北向
3. `python3 scripts/backfill_limit_up.py` — 涨停数据反算

## V6 引擎
4. `python3 scripts/pool_engine_v1.py` — 大盘[B]/[A]递推
5. `python3 scripts/pool_regime_integration.py` — 动态牛熊检测
6. `python3 scripts/stock_energy_incr.py` — 股票能量增量
7. `PYTHONUNBUFFERED=1 python3 scripts/fetch_exec_hold_daily.py` — 董监高持股变动

## 基本面
8. `python3 scripts/fetch_financial_data_v2.py` — 季度财报（每季初）

## V9 框架
9. `python3 scripts/v9_framework.py run` — V9全量

## 交易监控
10. `python3 scripts/trade_master.py status` — 状态总览
11. `python3 scripts/trade_master.py daily` — 全自动管线

## 🧠 心理圆桌
12. `python3 scripts/psych_roundtable_daily.py --date $(date +%Y-%m-%d)`

## 📊 股市日报（交易日收盘后）
13. `python3 scripts/daily_market_report.py`

---

# 📂 报告输出目录规范（新增）

所有分析报告写入 `/mnt/d/data/a-stock/reports/{YYYY-MM-DD}/`

## 股市日报（交易日每日一份）

文件名: `股市日报_{YYYYMMDD}.md`

## 智库圆桌报告

文件名: `圆桌_{议题缩写}_{YYYYMMDD}.md`

## 家族资产管理备忘录

文件名: `家族资产管理_{YYYYMMDD}.md`
