# HEARTBEAT.md

## 盘中监测（午休 11:30~13:00）
午休时扫北向+主力资金+盘中V8信号，输出盘中简报：
```bash
python3 scripts/lunch_monitor.py
```
输出到 `memory/reports/lunch-{today}.md` 和 `D:/data/a-stock/reports/{today}/午间盘面_{today}.md`

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

### 收盘报告（9~14统一为1步）
9. `python3 scripts/closing_report.py` — 统一收盘报告
   - 整合：大盘状态/持仓监控/市场宽度/V8扫描/资金行为/Phase回溯/策略
   - 输出：`D:/data/a-stock/reports/{today}/{收盘报告,收盘复盘}_{today}.md`
   - 替换了独立的：简报/V8仪表盘/持仓监控/庄家监控/护盘扫描/NEUTRAL监视/牛熊识别

### 💡 已废弃（不再运行）
- `fetch_industry_daily.py` / `fetch_industry_daily_v2.py` — 行业指数数据不再采集
- `sector_daily` / `industry_daily` 表已删除
- 行业超额由 `v6_multi_factor.precalc_all_industry_excess()` 从 `daily_prices` + `industry_components_ths_l1` 实时算

## 智库快速作战室（按需）
```bash
# 恐慌/策略审议场景：自动匹配学者并行讨论
cd skills/think-tank && python3 scripts/fast_tank.py "议题" --data /tmp/topic_data.json
# 输出到 sessions_spawn 调度指令，手动执行并行讨论
```
详见 `skills/think-tank/SKILL.md` C章

## 特征框架v2修正（待部署）
- 14维→12维（去掉开收盘趋同/多空单比例/价格中枢，加入因子残差换手率/条件日内偏度/VaR偏离率）
- 做因子暴露分解（对FF3+流动性回归取残差再算特征）
- 做跨regime聚类稳定性验证
- 行为突变监测：PCA投影→指数衰减baseline→CUSUM/HMM
详见 `skills/think-tank/scripts/fast_tank.py` 中的Simons讨论结论

## 待办跟踪
- Asness因子暴露分解验证 → 集成到特征框架v2
- Simons PCA/ICA模式发现 + 行为突变检测 → 集成到监控管线
- 快速作战室：支持psychologist和military体系 + 语义匹配
- 恐慌期C1超短线策略重新激活条件：surplus_z回到-1.0以上 + 日内振幅稳定2周以上
- Finnhub ETF指数数据（需VPN）
- UCDP/ACLED冲突数据（需VPN）
- 行业成分股映射（`build_industry_components_ths.py` — 按需运行）
- 新闻联播（`data_acquisition.py --cctv --latest --build-events` — 按需运行）

## 定期任务
- 检查磁盘空间（阈值85%）
- 检查企业微信机器人 wecom-aibot 连接状态
