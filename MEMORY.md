# MEMORY.md - 小狗蛋的长期记忆

## 黄老板
- **称呼**: 黄老板
- **项目方向**: ① **人格智库** (think-tank) ② **人格还原** (person-replication) ③ **A股金融数据**
- **GitHub**: guludan
- **数据源**: PG工控机 192.168.3.14, AKShare(conda tt), Baostock

## v6 [B]/[A]递推引擎 + 精确信号体系（2026-05-23 ✅ 上线）

### ⛔ 事件驱动路线全面关闭（2026-05-25）
所有 CCTV 文本→信号增强/门控路径经回测证伪：
- **宏观政策底 → S3 门控**：L0 无底日反转最强(+22%)，政策底日弱(+3.69%)→ 方向反
- **行业共振 → S2 增强**：所有回看窗口(3~14d)，有共振比无共振差 → 方向反
- **文本层无区分度**：event_CAR(0.26%)、NLP 分数(0.615 vs 0.604)、surplus_z ρ 均失败
- **根因**：CCTV 是滞后确认信号（产业政策已消化），不适用于日频信号增强
- **归档**：analysis/archive/，industry_lexicon.py 保留（中线行业轮动可用）
- **S3 失效根因**：非事件层问题，转向 E/PB 因子通道（Q 问题）

### pool_energy 表
- level维度: 'market'(大盘) 和 'code:A.xxxxxx'(个股，sig信号)
- 字段: date, b_pool, a_pool, b_inject, a_inject, surplus, imbalance, phase, extra
- phase: B_ACCUM/B_DISTRIB/A_ACCUM/A_RECOVER/NEUTRAL/HI_EQ
- 大盘已写入2435天 (2015-01~2026-05-22)

### 精确信号体系 S1/S2/S3
| 信号 | 条件 | 解释 |
|------|------|------|
| S1 surge | vol_z>3.0, \|v_z\|<1.5 | 波动暴增但没放量→未定价事件 |
| S2 pulse | vol_z>2.5, v_z>2.0, ret>5% | 量价齐爆→Phase β公开脉冲 |
| S3 panic | vol_z>2.5, v_z>2.0, ret<-5% | 放量恐慌→恐慌出逃 |

### 全量回测确认（6,208只 × 73K信号）
| 条件 | T+20中位 | 胜率 | 判断 |
|------|----------|------|------|
| S2脉冲 × 高盈余(B冲顶) | **-4.09%** | 35.3% | 强力卖出 |
| S3恐慌 × 低盈余(B低位) | **+22.46%** | 97.8% | 极强买入 |
| S1波动 × 低盈余(B低位) | **+9.52%** | 78.5% | 买入 |

### symbol格式修复
- mootdx增量写纯6位 `600028`，历史在`A.600028` → 统一为A.前缀
- daily_prices: 8,352,770条, 5,469只 ✅
- pool_energy level也同步修复

### 脚本
- `scripts/pool_engine_v1.py` — 大盘[B]/[A]递推引擎
- `scripts/stock_energy_full.py` — 个股S1/S2/S3信号提取+写入
- `scripts/stock_energy_backtest_full.py` — 全量回测统计
- `scripts/fix_symbol_format.py` — symbol格式修复工具

## 智库项目状态（2026-05-15）

### 新V3管线全线打通 ✅
154/154 人完成 dedup+regen
- merge(_per_file_results) → dedup(语义去重) → regen(顶层字段)
- 总insights: 8743，无evidence: 11条

## A股金融数据（2026-05-23，symbol统一为A.前缀 ✅）

### 核心表
| 表 | 覆盖 | 行数 |
|------|------|------|
| daily_prices | 2002-01 ~ 2026-05-22 | 835万 (5,469只, A.前缀统一) |
| market_signals | 2005~2026 | 30万 |
| pool_energy | 2015~2026 (大盘+个股信号) | 7.3万 (新增v6)
| incr_decr | 2022~2026 (上交所董监高增减持) | 2万 (10800增/9153减)

### eastmoney_data.py 21个API
限售解禁/融资融券/同花顺热点/mootdx(TCP 7709)等✅
<!-- 精简掉了各表的详细行数，保持MEMORY.md精简 -->

## 2026-05-25 策略更新

### ❌ 事件驱动路线：全面关闭
CCTV文本在三方向全部证伪，归档至 analysis/archive/

### ✅ 一手信源路线：龙虎榜+增减持

**龙虎榜（负向筛选器）:**
- S3 + 龙虎榜 → T+20 -24.66%, 6.2%胜率
- 原因：龙虎榜收录涨跌幅异常股，能上龙虎榜的S3是流动性危机而非普通恐慌

**高管增持（强正向信号）:**
- S3 + 7d内增持 → T+20 **+26.04%**, 92.9%胜率（vs基线 +9.16%）
- S3 + 7d内减持 → T+20 +3.54%, 38.1%胜率
- 19966条上交所数据已入库 incr_decr 表
- policy_injector_v2.py 规则：Tier1(+20%) / Tier2(+10%) / Tier3(-8%) / 回避(-100%)

**待办：**深交所增减持数据、V6完整接入

## 董监高持股变动（2026-05-30 ✅ 全市场）

### exec_hold_changes 表
- 数据源：东财datacenter RPT_EXECUTIVE_HOLD_DETAILS
- **166,284条**（2004-11-29 ~ 2026-05-29）
- 覆盖：深主板(69K) + 创业板(50K) + 上交所(44K) + 北交所(1.8K) + 其他(0.3K)
- 字段含：人员/职务/变动数量/均价/金额/原因/变动前后持股/变动人关系
- 对比旧表 incr_decr（SSE源，仅上交所60K条），exec_hold_changes是全市场版
- 脚本：`fetch_exec_hold_all.py`（全量拉取） + `fetch_exec_hold_daily.py`（增量更新）

## 动态牛熊检测（2026-05-30 ✅ V8集成）

### pool_regime_integration.py
- 从 pool_energy.surplus 计算 60日滚动z-score(surplus_z) + sz_5/sz_20/加速度
- 运行 V8 状态机 → 每日 regime (BULL/TOP_WARNING/SIDEWAYS/BOTTOM_WARNING/BEAR)
- 写入 pool_energy.extra: surplus_z/sz_5/sz_20/accel/regime/advance_ratio
- 写入 market_signals(regime_signal): 数值编码
- V8Strategy.load_regime_series() 改为从 pool_energy.extra 直接读取，不再自算
- 状态分布：SIDEWAYS 40%, BEAR 29%, BULL 24%, BOTTOM_WARNING 4%, TOP_WARNING 3%
- 近期(2026-05-29): surplus_z=-2.405, BEAR, A_ACCUM

## 核心脚本（workspace/scripts/，已上GitHub）

### 金融数据
| 脚本 | 用途 | 运行环境 |
|------|------|----------|
| `data_finance.py` | 主入口：日线更新/行情快照/ETF/沪深港通/信号 | conda tt |
| `update_daily_mootdx.py` | **个股日线（mootdx TCP，不封IP）** | conda tt |
| `update_daily_mootdx.py --start YYYY-MM-DD --end YYYY-MM-DD` | 指定范围回填 | conda tt |
| `scripts/analysis/signals.py` | 两融/龙虎榜/ETF份额/外资ETF信号分析 | conda tt |
| `scripts/analysis/sectors.py` | 板块成分股+收益分析（⚠️东财反爬） | conda tt |
| `scripts/analysis/cross_corr.py` | 时滞交叉相关系数 | conda tt |
| `scripts/analysis/run.py` | 统一入口（briefing生成） | conda tt |
| `scripts/analysis/market_psychology/` | 板块周期/宏观周期报告脚本集 | conda tt |
| `backfill_history.py` | A股日线回填（分段跑） | base Python |
| `fetch_financial_data.py` | 季度财报补拉（baostock, 2024-2025） | conda tt |
| `fetch_macro_cn.py` | 中国宏观指标（国债/SHIBOR/LPR/信用利差） | conda tt (akshare) |

### 智库
| 脚本 | 用途 |
|------|------|
| `_batch_distill.py --merge-per-file` | 逐篇蒸馏合并 |
| `batch_dedup_regen.py` | 语义去重+顶层字段regen |
| `regenerate_personality.py` | 人格顶层字段重生成 |
| `ingest_v3.py` | LanceDB入库 |
| `think_tank_pipeline_v3.py` | 智库报告生成 |

## 🔴 红线
- **质量评分5分制(1-5)，用action=keep/discard判断**
- **用skill前先读SKILL.md**
- **LanceDB/财务数据补拉严禁并发写入**
- **蒸馏实事求是，evidence.excerpt必须原文逐字摘录**
- **GLM调用走sessions_spawn，禁止直接调API**
- **SiliconFlow 禁止使用**
- **Sci-Hub间隔15秒，外部API间隔5-10秒**
- **长耗时脚本(>5min)需要在screen或独立进程跑，否则被SIGKILL**
- **Python 3.8不能装新包（yfinance/akshare冲突）**
- **论文下载必须验证作者身份**

## 关键经验
- 蒸馏输出: `personality_v3.json`
- Python 3.13 `{{ }}` 陷阱: 普通代码中双大括号→set literal, f-string别用dict unpacking
- **f-string中env={{**os.environ,...}}在3.13会被解析为dict表达式，提前构建变量再传**
- Codex/Claude Code跑编码任务
- `_pg_execute()` 返回 (rows_list, columns_list) 元组
- Baostock财务数据速度~20只/20秒，需开screen后台跑
- 已有PG unique constraint = (indicator, date)
- CHANGENUM 在上交所JSON中是字符串（str），入库需 int()
- SSE query 默认只返回第一页（100条），设置 pageSize=5000 + 翻页可拿全量
- `level`格式 `code:A.000158`，股票代码提取 = SUBSTRING(level FROM 8)

## 2026-05-30 待办4/5/6完成

### 经营现金流（Item 6 ✅ 后台运行中）
- `scripts/fetch_cashflow.py` — 从东财 `RPT_F10_FINANCE_MAINFINADATA` 拉取
- 建表 `financial_cashflow`，字段: symbol, end_date, netcash_operate, netcash_invest, netcash_finance
- 当前入库8,500+条（覆盖2022~2026），后台拉取1350页继续中
- UNIQUE(symbol, end_date)，ON CONFLICT覆盖

### NEUTRAL异常监视（Item 4 ✅）
- `scripts/neutral_monitor.py` — 4规则监视器
  1. NEUTRAL+BEAR → 弱势震荡警告
  2. NEUTRAL+surplus_z<-1.5 → 空头导向
  3. NEUTRAL连续>10天 → 方向不明
  4. Bear+accel转正 → 底背离
- 首次报告: `memory/reports/neutral_monitor_2026-05-29.md`
- 纳入HEARTBEAT收盘管线（第10步）

### V8仪表盘（Item 5 ✅）
- `scripts/strategies/v8_dashboard.py` — 输出到 `memory/reports/v8_dashboard_YYYY-MM-DD.txt`
- 纳入HEARTBEAT收盘管线（第9步）

### 离场策略回测（Item 3 ⚠️ 部分完成）
- `scripts/exit_strategy_backtest.py` — 对比压力位止盈 vs 固定周期 vs 混合策略
- 4,125次V8入场信号的回测结果:
  | 策略 | 收益 | 胜率 | 持有 |
  |------|------|------|------|
  | T+5 | +2.65% | 60.7% | 5d |
  | T+10 | +5.31% | 67.7% | 10d |
  | T+20 | +9.24% | 72.2% | 20d |
  | 混合40d | ~12% | ~81% | 14d |
- 混合策略（压力位+超时强制）收益和胜率都最优
- 但所有策略最大回撤~100%异常，需检查时序排序
- 注：`backtest_v8_final.py`因bvps_map key不匹配导致0条信号（已定位：financial_data.symbol是'A.前缀'但density_map用纯6位）

### 深度管线已更新（HEARTBEAT.md）
```
4. pool_engine_v1.py — [B]/[A]递推
5. pool_regime_integration.py — 动态牛熊检测 ← 新增
6. stock_energy_incr.py — 股票能量补全
7. fetch_exec_hold_daily.py — 董监高增量 ← 新增
8. fetch_financial_data_v2.py — 季度财报(按需)
9. v8_dashboard.py — V8仪表盘 ← 新增
10. neutral_monitor.py — NEUTRAL监视 ← 新增
11. 生成简报
```

### 待修漏洞
- backtest_v8_final.py bvps_map key格式问题（`A.`前缀vs纯6位）
- 最大回撤计算需按exit日期排序（当前同一天过多信号扭曲结果）
- regime_signal表未写入（但pool_energy.extra已有regime，V8直接读extra）
