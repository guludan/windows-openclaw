# 全周期回测框架架构说明

## 目录结构

```
scripts/
├── ARCHITECTURE.md              ← 本文档
├── cycle_backtest_framework.py  ← **新框架** 全周期回测引擎（核心）
│
├── backtest/                    ← 回测执行目录
│   ├── v8/                      ← V8策略回测脚本
│   ├── zhuangjia/               ← 庄家行为回测脚本
│   └── rolling/                 ← 滚动回测脚本
│
├── strategies/                  ← 策略定义目录
│   ├── v8/                      ← V8信号策略
│   └── zhuangjia/               ← 庄家阶段策略
│
├── data/                        ← 数据维护脚本
│
├── backtest_*.py                ← [旧版] 零散回测脚本
├── v6_*.py                      ← [旧版] V6回测
├── v7_*.py                      ← [旧版] V7回测
├── v8_*.py                      ← [旧版] V8回测
├── zhuangjia_*.py               ← [旧版] 庄家回测
├── stock_*.py                   ← [旧版] 股票能量/滚动回测
└── ... 其余138个.py              ← 历史积累的单次/实验性脚本
```

## 新框架 vs 旧脚本

### 新框架 (`cycle_backtest_framework.py`)
- **三层架构集成**：市场周期(CycleClassifier) + 庄家阶段(StageDetector) + V8信号(V8SignalDetector)
- **全组合交叉统计**：regime × phase × stage × tier → 收益/胜率/尾部风险/MaxDD
- **统一数据源**：所有数据通过同一DB连接获取，一致的日期过滤
- **参数化运行**：`--sample`(采样20只) / `--full`(全量) / `--codes`(指定)

### 旧版脚本 (标记为 `[旧版]`)
这些是历史积累的单次实验脚本，可根据需要迁移：
| 旧脚本 | 对应新框架模块 | 说明 |
|--------|---------------|------|
| `zhuangjia_backtest.py` / `zhuangjia_backtest_v2.py` | `StageDetector` | 庄家阶段逻辑已被提取并v2化 |
| `v8_meanrev.py` / `v8_resonance*.py` | `V8SignalDetector` | SMA偏离逻辑 |
| `stock_energy_backtest*.py` / `stock_rolling_backtest.py` | `CycleBacktestEngine` | 滚动回测引擎 |
| `v6_*` / `v7_*` | — | 旧版本号体系，未迁移 |
| `pool_engine_v2.py` | `CycleClassifier` | pool_energy数据源，框架直接查询DB |
| `macro_cycle_analysis*.py` | `CycleClassifier` | 宏观周期逻辑 |

## 使用流程

```bash
# 1. 采样快速验证
~/miniconda3/envs/tt/bin/python3 scripts/cycle_backtest_framework.py --sample

# 2. 全量回测
~/miniconda3/envs/tt/bin/python3 scripts/cycle_backtest_framework.py --full --save

# 3. 指定股票和日期范围
~/miniconda3/envs/tt/bin/python3 scripts/cycle_backtest_framework.py \
    --codes 000001 600519 300750 \
    --start 2020-01-01 --end 2026-06-02 --save

# 4. Python API 方式
from cycle_backtest_framework import CycleBacktestEngine, generate_report
engine = CycleBacktestEngine(codes=['000001'], start='2023-01-01')
df = engine.run()
stats = generate_report(df)
```
