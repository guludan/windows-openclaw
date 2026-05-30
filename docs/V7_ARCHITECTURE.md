# V7 引擎 — 最终架构文档

## 版本历史
| 版本 | 日期 | 变化 |
|:---|:----:|:----|
| v6 | 2026-05-23 | 大盘[B]/[A]递推 + S1/S2/S3 精确信号体系上线 |
| v7 | 2026-05-27 | 岭回归评分模块 + 行为信号筛选（S1 退回纯风险预算） |

## V7 三层架构

```
V7 引擎
│
├── 模块一：风险预算（三层全量）
│   ├── 大盘相空间 → 仓位乘数
│   ├── 行业超额 → 行业权重分配
│   └── 个股 Phase 状态 → 个股仓位约束
│
├── 模块二：岭回归评分
│   ├── v7_score_s3()                  ✅ 已入库
│   │   (全 phase, T+3+T+5, CV R²=0.089/0.144)
│   └── v7_score_s2_bdistrib()         ✅ 已入库
│       (S2+B_DISTRIB 阶段, T+5, CV R²=0.020)
│
└── 模块三：行为信号筛选（全量）
    ├── S3 + 7 日内增持 → +20% 加成
    ├── S3 + 龙虎榜机构净卖 → 一票否决
    └── S1/S2 预留扩展
```

### 模块一：风险预算

**仓位乘数表（大盘 Phase 驱动）：**
| Phase | 乘数 | 说明 |
|:-----|:---:|:----|
| B_ACCUM | 1.0x | 全场买入 |
| A_ACCUM | 0.7x | 深度买入 |
| NEUTRAL | 0.4x | 观望 |
| B_DISTRIB | 0.3x | 轻度持仓 |
| A_RECOVER | 0.6x | 试探性建仓 |
| HI_EQ | 0.2x | 回避 |

**行业权重：** 由 `precalc_all_industry_excess()` 从 `daily_prices` + `industry_components_ths_l1` 实时计算超额收益。历史数据不储存 `sector_daily` 表。

**个股仓位：** 个股自身的 Phase 状态约束仓位上限。

### 模块二：岭回归评分

#### v7_score_s3() — 恐慌信号评分

```sql
CREATE OR REPLACE FUNCTION v7_score_s3(
  p_lagged_5d_return DECIMAL,    -- 前5日收益
  p_turnover_z DECIMAL,          -- 换手率Z值
  p_total_energy_trend DECIMAL,  -- 总能量趋势
  p_insider_buy INTEGER,         -- 7日内高管增持(0/1)
  p_insider_sell INTEGER,        -- 7日内高管减持(0/1)
  p_lhb_net_sell INTEGER,        -- 龙虎榜机构净卖(0/1)
  p_phase TEXT                    -- 大盘Phase
)
```

**系数（RidgeCV, α=376.49, 训练期2020-2026, 6208只×73K信号）：**
| 特征 | 系数 | 说明 |
|:----|:---:|:----|
| lagged_5d_return | -0.027 | 前5日跌越多→反弹概率越大 |
| turnover_z | -0.005 | 换手率略降→恐慌收敛 |
| total_energy_trend | -0.012 | 能量向下→反转可能性增加 |
| insider_buy=1 | +0.001 | 增持微调加成 |
| lhb_net_sell=1 | -0.040 | 机构净卖→强力做空信号 |
| phase=B_DISTRIB | +0.002 | 弱势阶段微弱加成 |
| phase=A_RECOVER | -0.004 | 复苏阶段减分 |
| phase=A_ACCUM | -0.001 | 积累阶段减分 |

**回测结果：** T+3 CV R²=0.089, T+5 CV R²=0.144, 11/11 符号稳定

#### v7_score_s2_bdistrib() — 脉冲信号评分(B_DISTRIB阶段)

```sql
CREATE OR REPLACE FUNCTION v7_score_s2_bdistrib(
  p_lagged_5d_return DECIMAL,
  p_turnover_z DECIMAL,
  p_total_energy_trend DECIMAL,
  p_insider_buy INTEGER,
  p_insider_sell INTEGER
)
```

**系数（RidgeCV, B_DISTRIB阶段限定, T+5窗口）：**
| 特征 | 系数 | 说明 |
|:----|:---:|:----|
| lagged_5d_return | -0.034 | S2脉冲+B_DISTRIB→反转为跌 |
| turnover_z | -0.009 | 放量见顶→减持 |
| total_energy_trend | +0.008 | 能量向上→但方向与S3相反 |
| insider_buy=1 | -0.001 | 买入做空(微调) |
| insider_sell=1 | +0.002 | 卖出做空(微调) |

**回测结果：** T+5 CV R²=0.020, 5/5 符号稳定

#### S1 岭回归路线：完全关闭

全量、分段（B_ACCUM/B_DISTRIB/NEUTRAL）、阈值扫描（vol_z 3.0~6.0）、方向约束（cum_return>3%）、波动率预测（CV R²=0.004）、S3 调制验证（s1_nearby 系数=0.004 但 R² 变化为 0）——全部失败。

**S1 退化为纯描述指标**，保留其 vol_z>3.0 检测但仅用于风险监控，不参与评分/决策。

### 模块三：行为信号筛选

| 条件 | 效果 | 来源 |
|:----|:---:|:----|
| S3 + 7日内高管增持 | 评分+20%加成 | 回测(S3+增持→T+20 +26%) |
| S3 + 龙虎榜机构净卖 | 一票否决(-0.040) | 回测(S3+龙虎榜→T+20 -24.7%) |
| S3 + 7日内减持 | 评分-8%扣减 | 回测(S3+减持→T+20 +3.5%) |

## 日更链路

```
15:30 收盘后
  1. update_daily_mootdx.py      — 个股日线更新 (~60s)
  2. data_finance --scan-all     — 全量行情快照拉取(~30s，补turnover)
  3. backfill_limit_up.py        — 涨停回填 (~5s)
  4. pool_engine_v1.py           — 大盘相空间递推 (~30s)
  5. stock_energy_incr.py        — 个股pool_energy占位 (~5s)
  6. pool_signal_today.py        — 信号检测 + V7评分写入 (~10s)
  7. → a-share简报               — 选股/风控输出
```

### SQL 函数引用

- `v7_score_s3()` — 全 phase 恐慌评分
- `v7_score_s2_bdistrib()` — S2 + B_DISTRIB 脉冲评分
- `v7_score_risk_budget(market_phase)` — 风险预算仓位乘数

## 数据表

| 表 | 用途 | 行数 |
|:--|:----|:----:|
| pool_energy | 大盘+个股相空间 | 市场2435天+个股信号 |
| daily_prices | A股日线(含turnover) | ~1000万行(5469只) |
| market_snapshots | 全量行情快照 | 每日~5000条 |
| industry_components_ths_l1 | 同花顺行业成分 | ~5000只 |
| incr_decr | 上交所高管增减持 | ~20000条 |
| daily_limit_up | 涨停记录 | 日更新 |

## 回测结果汇总

| 信号 | 窗口 | CV R² | 符号稳定 | 结论 |
|:---|:---:|:-----:|:-------:|:----|
| S3 全量 | T+3 | +0.089 | 11/11 | ✅ |
| S3 全量 | T+5 | +0.144 | 11/11 | ✅ |
| S2 B_DISTRIB | T+5 | +0.020 | 5/5 | ✅（弱通过） |
| S2 B_ACCUM | T+3/T+5 | ≤0 | - | ❌ |
| S2 NEUTRAL | T+3/T+5 | ≤0 | - | ❌ |
| S1 各phase | T+3/T+5 | ≤0 | - | ❌ 全部关闭 |
| S1 波动率预测 | T+5 | +0.004 | - | ❌ |
| S1 调制S3 | T+5 | 0.000 | ✅(系数-0.004) | ❌ 太弱 |
