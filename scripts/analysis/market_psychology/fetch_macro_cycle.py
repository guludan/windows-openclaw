#!/usr/bin/env python3
"""
宏观周期数据采集 — 中美货币政策指标

采集指标：
  - 中国：LPR(1Y/5Y)、MLF(1Y)、7天逆回购利率、存款准备金率、10Y国债收益率
  - 美国：联邦基金利率、10Y/2Y国债收益率、美元指数
  - 衍生：中美利差

来源：akshare（中国）+ FRED API（美国）
入库：macro_indicators 表（indicator, date, value, frequency, source）
"""
import sys, os, logging, json, time
from datetime import date, timedelta
import requests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import data_finance as df

log = logging.getLogger('macro_cycle')

FRED_API_KEY = "14876f904987b05adca701fb6f6e026d"
FRED_SERIES = {
    "FEDFUNDS": ("美国联邦基金利率", "daily"),
    "DGS10": ("美国10Y国债收益率", "daily"),
    "DGS2": ("美国2Y国债收益率", "daily"),
    "T10Y2Y": ("美国10Y-2Y利差", "daily"),
    "DTWEXBGS": ("美元指数(贸易加权)", "daily"),
}


def _insert_macro(indicator, dt_val, value, frequency, source):
    """写入宏观指标"""
    if value is None:
        return
    try:
        df._pg_execute(
            "INSERT INTO macro_indicators (indicator, date, value, frequency, source) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (indicator, date) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
            (indicator, dt_val, float(value), frequency, source))
    except Exception as e:
        log.warning(f"  写入失败 {indicator} {dt_val}: {e}")


def fetch_fred():
    """通过 FRED API 拉取美国利率/国债/美元数据"""
    log.info("=== 美国宏观指标 (FRED API) ===")
    for series_id, (name_cn, freq) in FRED_SERIES.items():
        try:
            url = f"https://api.stlouisfed.org/fred/series/observations"
            params = {
                'series_id': series_id,
                'api_key': FRED_API_KEY,
                'file_type': 'json',
                'observation_start': '2020-01-01',
                'sort_order': 'desc',
                'limit': 2000,
            }
            resp = requests.get(url, params=params, timeout=30)
            data = resp.json()
            obs = data.get('observations', [])
            count = 0
            for o in obs:
                if o['value'] not in ('.', ''):
                    _insert_macro(
                        name_cn,
                        o['date'],
                        float(o['value']),
                        freq,
                        f'fred_{series_id}'
                    )
                    count += 1
            log.info(f"  {name_cn}: {count} 条")
            time.sleep(0.5)  # 限速
        except Exception as e:
            log.warning(f"  采集失败 {series_id}: {e}")
    
    # 计算中美利差
    log.info("=== 计算中美利差 (中国10Y - 美国10Y) ===")
    try:
        rows_cn, _ = df._pg_execute(
            "SELECT date, value FROM macro_indicators WHERE indicator='中国10Y国债收益率' "
            "AND date >= '2020-01-01' ORDER BY date")
        rows_us, _ = df._pg_execute(
            "SELECT date, value FROM macro_indicators WHERE indicator='美国10Y国债收益率' "
            "AND date >= '2020-01-01' ORDER BY date")
        
        us_map = {str(r[0]): float(r[1]) for r in rows_us}
        count = 0
        for r in rows_cn:
            dt_str = str(r[0])
            cn_val = float(r[1])
            if dt_str in us_map:
                spread = cn_val - us_map[dt_str]
                _insert_macro("中美10Y利差", dt_str, round(spread, 2), "daily", "calc")
                count += 1
        log.info(f"  中美10Y利差: {count} 条")
    except Exception as e:
        log.warning(f"  利差计算失败: {e}")


def fetch_china_rates():
    """通过 akshare 拉中国利率数据"""
    import akshare as ak
    log.info("=== 中国宏观指标 (akshare) ===")
    
    # 1. 中国10Y国债收益率
    try:
        # 从akshare取债券收益率
        df_bond = ak.bond_china_yield(start_date="20200101")
        if 'date' in df_bond.columns or '日期' in df_bond.columns:
            date_col = 'date' if 'date' in df_bond.columns else '日期'
            # 找10年国债收益率列
            yield_cols = [c for c in df_bond.columns if '10' in str(c) or '十年' in str(c)]
            if yield_cols:
                col = yield_cols[0]
                count = 0
                for _, r in df_bond.iterrows():
                    try:
                        val = float(r[col])
                        _insert_macro("中国10Y国债收益率", str(r[date_col])[:10], val, "daily", "akshare")
                        count += 1
                    except:
                        pass
                log.info(f"  中国10Y国债收益率: {count} 条")
    except Exception as e:
        log.warning(f"  中国国债收益率失败: {e}")
    
    # 2. LPR
    try:
        df_lpr = ak.interest_rate_history()
        log.info(f"  利率行数: {len(df_lpr)}, 列: {df_lpr.columns.tolist()}")
        # 试试不同版本
        try:
            df_lpr = ak.rate_interbank()
            log.info(f"  rate_interbank: {len(df_lpr)} 行")
        except:
            pass
    except Exception as e:
        log.warning(f"  LPR采集失败: {e}")
    
    # 用 requests 绕开 akshare 的 LPR 接口（东方财富）
    try:
        import requests as req
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            'reportName': 'RPT_LRP_INTEREST_RATE',
            'columns': 'TRADE_DATE,ONE_YEAR_LPR,FIVE_YEAR_LPR',
            'pageNumber': 1,
            'pageSize': 100,
            'sortTypes': -1,
            'sortColumns': 'TRADE_DATE',
            'source': 'WEB',
            'client': 'WEB',
        }
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = req.get(url, params=params, headers=headers, timeout=15)
        data = r.json()
        items = data.get('result', {}).get('data', [])
        count = 0
        for item in items:
            dt = str(item.get('TRADE_DATE', ''))[:10]
            yr1 = item.get('ONE_YEAR_LPR')
            yr5 = item.get('FIVE_YEAR_LPR')
            if dt:
                if yr1 is not None:
                    _insert_macro("LPR_1Y", dt, float(yr1), "monthly", "eastmoney")
                if yr5 is not None:
                    _insert_macro("LPR_5Y", dt, float(yr5), "monthly", "eastmoney")
                count += 1
        log.info(f"  LPR: {count} 条")
    except Exception as e:
        log.warning(f"  LPR东方财富失败: {e}")
    
    # 3. 存款准备金率
    try:
        # 用akshare宏经
        df_rrr = ak.macro_china_required_reserve_ratio()
        date_col = [c for c in df_rrr.columns if '日' in str(c) or 'date' in str(c).lower()][0]
        big_cols = [c for c in df_rrr.columns if '中型' in c or '大型' in c]
        for c in big_cols[:1]:  # 取第一个
            count = 0
            for _, r in df_rrr.iterrows():
                try:
                    val = float(r[c])
                    _insert_macro("存款准备金率", str(r[date_col])[:10], val, "periodic", "akshare")
                    count += 1
                except:
                    pass
            log.info(f"  存款准备金率 ({c}): {count} 条")
    except Exception as e:
        log.warning(f"  存款准备金率失败: {e}")
    
    # 4. 中国 SHIBOR 利率曲线（替代中国短期利率）
    try:
        df_shibor = ak.rate_interbank(market="上海银行间同业拆放利率", symbol="Shibor人民币", indicator="3月", start_date="20100101")
        date_col = [c for c in df_shibor.columns if '日期' in str(c) or 'date' in str(c).lower()][0]
        rate_cols = [c for c in df_shibor.columns if '利率' in str(c) or '收盘' in str(c) or 'close' in str(c).lower()]
        if rate_cols:
            col = rate_cols[0]
            count = 0
            for _, r in df_shibor.iterrows():
                try:
                    val = float(r[col])
                    _insert_macro("SHIBOR_3M", str(r[date_col])[:10], val, "daily", "akshare")
                    count += 1
                except:
                    pass
            log.info(f"  SHIBOR_3M: {count} 条")
    except Exception as e:
        log.warning(f"  SHIBOR失败: {e}")


def update_all():
    """更新全部宏观指标"""
    fetch_fred()
    fetch_china_rates()
    
    # 统计
    rows, _ = df._pg_execute(
        "SELECT indicator, count(*), min(date), max(date) "
        "FROM macro_indicators GROUP BY indicator ORDER BY indicator")
    log.info("\n=== 宏观指标统计 ===")
    for r in rows:
        log.info(f"  {r[0]}: {r[1]}行, {r[2]}~{r[3]}")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    update_all()
