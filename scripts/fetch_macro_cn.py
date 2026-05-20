#!/usr/bin/env python3
"""
拉取中国宏观核心指标到 PG
优先级最高：利率 & 流动性

数据源：AKShare
指标：
  1. CN_10Y_YIELD  — 中国10年期国债收益率（日度）
  2. CN_DR007      — DR007 加权平均利率（日度）
  3. CN_LPR_1Y     — LPR 1年期（月度）
  4. CN_LPR_5Y     — LPR 5年期以上（月度）
  5. CN_AAA_CREDIT_SPREAD — AAA企业债收益率（日度）
  6. CN_SHIBOR_1W  — SHIBOR 1周（日度）
"""
import sys, os, logging, pandas as pd
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fetch_macro_cn")

sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/scripts"))
import data_finance as df

def save_indicator(indicator, rows_data, freq, source):
    """rows_data: list of (date_str, value)"""
    inserted = 0
    for date_str, value in rows_data:
        try:
            d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        except ValueError:
            try:
                d = datetime.strptime(str(date_str)[:7], "%Y-%m").date()
            except:
                continue
        try:
            val = float(value)
        except (TypeError, ValueError):
            continue
        try:
            df._pg_execute(
                "DELETE FROM macro_indicators WHERE indicator=%s AND date=%s",
                (indicator, d.isoformat()), fetch=False
            )
            df._pg_execute(
                "INSERT INTO macro_indicators (indicator, date, value, frequency, source, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,now())",
                (indicator, d.isoformat(), val, freq, source), fetch=False
            )
            inserted += 1
        except Exception as e:
            log.warning(f"  {indicator} {date_str} 写入失败: {e}")
    return inserted

def fetch_10y_yield():
    """中国10年期国债收益率（日度），按年分段拉取"""
    import akshare as ak
    total = 0
    for year in range(2015, 2027):
        s = f"{year}0101"
        e = f"{year}1231" if year < 2026 else "20260531"
        try:
            df_ak = ak.bond_china_yield(start_date=s, end_date=e)
            df_gov = df_ak[df_ak["曲线名称"].str.contains("中债国债收益率曲线", na=False)]
            rows = []
            for _, r in df_gov.iterrows():
                if pd.notna(r["10年"]):
                    rows.append((str(r["日期"])[:10], float(r["10年"])))
            c = save_indicator("CN_10Y_YIELD", rows, "D", "akshare")
            total += c
            log.info(f"    {year}年: {len(rows)} 条")
        except Exception as e:
            log.warning(f"    {year}年失败: {e}")
    log.info(f"  中国10Y国债: 总计 {total} 条")
    return total

def fetch_credit_spread():
    """AAA企业债收益率（按年分段）"""
    import akshare as ak
    total = 0
    for year in range(2015, 2027):
        s = f"{year}0101"
        e = f"{year}1231" if year < 2026 else "20260531"
        try:
            df_ak = ak.bond_china_yield(start_date=s, end_date=e)
            df_aaa = df_ak[df_ak["曲线名称"].str.contains("中债中短期票据收益率曲线", na=False)]
            rows = []
            for _, r in df_aaa.iterrows():
                if pd.notna(r["10年"]):
                    rows.append((str(r["日期"])[:10], float(r["10年"])))
            c = save_indicator("CN_AAA_10Y", rows, "D", "akshare")
            total += c
            log.info(f"    {year}年: {len(rows)} 条")
        except Exception as e:
            log.warning(f"    {year}年失败: {e}")
    log.info(f"  AAA企业债10Y: 总计 {total} 条")
    return total

def fetch_lpr():
    """LPR 利率（按月发布）"""
    import akshare as ak
    try:
        df_ak = ak.macro_china_lpr()
        rows_1y, rows_5y = [], []
        for _, r in df_ak.iterrows():
            yr = str(r["年份"]).strip()
            mo = str(r["月份"]).strip()
            rows_1y.append((f"{yr}-{int(mo):02d}-01", float(r["1年期LPR"])))
            rows_5y.append((f"{yr}-{int(mo):02d}-01", float(r["5年期以上LPR"])))
        c1 = save_indicator("CN_LPR_1Y", rows_1y, "M", "akshare")
        c5 = save_indicator("CN_LPR_5Y", rows_5y, "M", "akshare")
        log.info(f"  LPR 1Y: {c1}条, LPR 5Y: {c5}条")
        return c1 + c5
    except Exception as e:
        log.warning(f"  LPR失败: {e}")
        return 0

def fetch_dr007():
    """DR007（存款类机构7天质押式回购加权平均利率，日度）
    接口有限制，用东方财富的 repo 数据代替"""
    import akshare as ak
    total = 0
    for year in range(2015, 2027):
        try:
            df_ak = ak.rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="7天")
            rows = []
            for _, r in df_ak.iterrows():
                date_str = str(r.iloc[0])[:10]
                if date_str.startswith(str(year)):
                    # 取第二列通常是利率值
                    val = float(r.iloc[1])
                    rows.append((date_str, val))
            # 只取当年的
            rows_yr = [r for r in rows if r[0].startswith(str(year))]
            c = save_indicator("CN_SHIBOR_1W", rows_yr, "D", "akshare")
            total += c
            log.info(f"    SHIBOR {year}年: {len(rows_yr)} 条")
        except Exception as e:
            log.warning(f"    {year} SHIBOR失败: {e}")
    log.info(f"  SHIBOR 1W: 总计 {total} 条")
    return total

def fetch_shibor():
    """SHIBOR 1周（日度）— 已合并在fetch_dr007中"""
    return 0

def fetch_bbb_spread():
    """BBB级信用债收益率 — 暂不支持"""
    return 0

def fetch_m1():
    """M1同比（月度，已在CN_M2里含M1但补充到独立indicator）"""
    import akshare as ak
    try:
        df_ak = ak.macro_china_money_supply()
        rows = []
        for _, r in df_ak.iterrows():
            date_str = str(r["月份"]).strip()
            try:
                d = datetime.strptime(date_str, "%Y年%m月份").date()
            except:
                continue
            try:
                m1_yoy = float(r["货币(M1)-同比增长"])
                rows.append((d.isoformat(), m1_yoy))
            except:
                continue
        c = save_indicator("CN_M1_YOY", rows, "M", "akshare")
        log.info(f"  M1同比: {c} 条 (从原M2数据分离)")
        return c
    except Exception as e:
        log.warning(f"  M1提取失败: {e}")
        return 0

def main():
    log.info("📡 开始拉取中国宏观指标...")
    total = 0
    total += fetch_10y_yield()
    total += fetch_dr007()
    total += fetch_shibor()
    total += fetch_lpr()
    total += fetch_credit_spread()
    total += fetch_bbb_spread()
    total += fetch_m1()
    log.info(f"\n✅ 中国宏观指标拉取完成，共写入 {total} 条记录")

if __name__ == "__main__":
    main()
