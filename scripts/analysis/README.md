# 📊 数据分析框架

## 架构

```
analysis/
├── README.md          ← 本文档
├── sectors.py         ← 板块/行业分析
├── signals.py         ← 信号交叉分析（两融+龙虎榜+ETF份额+外资）
├── cross_corr.py      ← 时间序列交叉相关（找时间差信号）
└── report.py          ← 简报生成（板块涨跌幅TOP + 异动信号）
```

## 数据源

| 表 | 内容 | 频率 |
|---|---|---|
| `market_snapshots` | 全市场行情快照（含PE/PB/市值） | 每日盘后 |
| `market_signals` | 信号数据（signal_type 区分类型） | 每日 |
| `daily_prices` | 日线（开高低收量） | 每日 |
| `stock_list` | 股票列表 | 静态 |

### market_signals 的 signal_type

- `etf_share` — ETF份额变化，code=基金代码，value=份额数(份)
- `foreign_etf` — 海外中国ETF，code=FXI/MCHI/KWEB等，value=美元价格
- `lhb` — 龙虎榜，extra->'net'=净买入额(元)，extra->'buy'/'sell'
- `margin_sh` — 上交所融资融券汇总，code=TOTAL
- `margin_sz` — 深交所融资融券汇总
- `margin_detail` — 融资余额变化明细(个券)

## 使用方式

```python
from scripts.analysis.sectors import SectorAnalyzer
sa = SectorAnalyzer()
df = sa.daily_summary()  # 今日板块涨跌幅TOP
```

```python
from scripts.analysis.signals import SignalAnalyzer
sa = SignalAnalyzer()
sa.margin_leaders(days=5)  # 近5日融资净买入/卖出TOP
sa.etf_flow()              # ETF份额变化趋势
sa.lhb_flow()              # 龙虎榜净流入/出TOP
sa.foreign_flow()          # 外资ETF折溢价
```

```python
from scripts.analysis.cross_corr import CrossCorrelation
cc = CrossCorrelation()
cc.find_leads('000300', window=20)  # 沪深300 vs 各信号的领先滞后关系
```
