# _deprecated — 弃用脚本归档

> 2026-05-22: 行业分类统一为同花顺一级行业（90个），以下脚本因依赖申万一级或已删除的表而弃用。

| 脚本 | 弃用原因 |
|------|---------|
| `analysis/industry_components.py` | 写入`industry_components`表（已删），改用`build_industry_components_ths.py` |
| `analysis/sectors.py` | 申万一级板块分析，`industry_daily`已直接存行业指数日线 |
| `analysis/market_psychology/build_sector_cycle.py` | 依赖`industry_components`表，改用`industry_daily` |
| `analysis/market_psychology/sector_cycle_report.py` | 依赖`sector_daily`表，数据源断 |
| `analysis/run.py` | 调用已弃用的`sector_daily_chart()` |
| `inverse_push/csi_lhb_v4.py` | 旧版龙虎榜CSI，被`csi_lhb_v5.py`替代 |
