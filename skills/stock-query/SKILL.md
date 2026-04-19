---
name: stock-query
description: "Query A-share (China mainland) stock market data via multi-source (Yahoo Finance + AKShare) with caching. Use when user asks about stock prices, quotes, K-line/candlestick charts, historical data, financial metrics, or any A-share market inquiry. Triggers on stock codes (600xxx/000xxx/002xxx/300xxx), stock names (茅台/比亚迪), or questions like '行情', 'K线', '财报', '市值', '涨跌', 'PE', 'ROE'. Supports real-time quotes, historical K-lines, and comprehensive financial data."
---

# A股多数据源查询

Python: `/home/admin/miniconda3/bin/python3`
Script: `scripts/stock_query.py`
数据源: Yahoo Finance (主) + AKShare (备), 含自动缓存

## 股票代码格式

支持多种输入: `600519` / `600519.SS` / `sh600519` 均可自动识别。
- 上海: 6xxxxx (主板/科创板) → `.SS`
- 深圳: 0xxxxx/3xxxxx → `.SZ`

## 子命令

### quote — 实时行情

```bash
/home/admin/miniconda3/bin/python3 scripts/stock_query.py quote <codes...>
```

返回: 价格、涨跌幅、成交量、市值、PE、PB、ROE、股息率、营收。
支持多股票批量查询。缓存60秒。

### kline — 历史K线

```bash
/home/admin/miniconda3/bin/python3 scripts/stock_query.py kline <code> --period <period>
```

- `--period`: `5d` `1mo` `3mo` `6mo` `1y` `5y`
- 返回: 日期、开高低收、成交量。优先用AKShare(复权), 备用Yahoo。缓存5分钟。

### financial — 财务数据

```bash
/home/admin/miniconda3/bin/python3 scripts/stock_query.py financial <code>
```

返回: 行业、市值、营收增长率、毛利率、净利率、ROE、PE、PB、负债率、现金流等20项。缓存1小时。
