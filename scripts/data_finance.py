#!/usr/bin/env python3
"""
data_finance.py — 结构化金融数据采集与缓存层

职责：所有结构化/时序金融数据的统一入口
- 股票/指数/宏观/加密/衍生品的日线拉取
- PG 缓存（工控机 192.168.3.14）
- @lru_cache 热缓存
- 所有数据返回 pd.DataFrame

不负责：网页搜索、论文下载、非结构化文本 — 那是 data_search.py 的事。

用法：
    python3 data_finance.py --update-all        # 全量更新一次所有数据源
    python3 data_finance.py --update-daily      # 增量更新（默认昨天到今天）
    python3 data_finance.py --scan-all          # 全量行情扫描（5257只活跃股，批量腾讯接口，~2分钟）
    python3 data_finance.py --init-stock-list   # 初始化股票列表（第一次扫盘前运行）
    python3 data_finance.py --backfill-hist     # 后台回填全量日线（首次，~30分钟）
"""
import sys, os, time, json, logging, random
from datetime import datetime, timedelta, date
from functools import lru_cache
from pathlib import Path
from typing import Optional, List, Tuple

import pandas as pd
import numpy as np
  # 全局单例, 规避局部 import 冲突

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("data_finance")

# ============================================================
# 配置
# ============================================================
PG_HOST = "192.168.3.14"
PG_PORT = 5432
PG_USER = "guludan"
PG_PASS = "toto"
PG_DB = "guludan"

FRED_API_KEY = os.environ.get("FRED_API_KEY", "14876f904987b05adca701fb6f6e026d")
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "d709oh1r01qtb4ra0b50d709oh1r01qtb4ra0b5g")

# 代理（yfinance 需要）
PROXY = "http://172.26.0.1:10808"

# 缓存目录（本地 fallback）
CACHE_DIR = Path("/mnt/d/data/finance_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# PG 连接
# ============================================================
_pg_conn = None

def _get_pg():
    global _pg_conn
    if _pg_conn is None or _pg_conn.closed:
        import psycopg2
        _pg_conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS, dbname=PG_DB, connect_timeout=5
        )
    return _pg_conn

def _pg_execute(sql, params=None, fetch=True):
    conn = _get_pg()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        if fetch:
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description] if cur.description else []
            cur.close()
            conn.commit()
            return rows, cols
        cur.close()
        conn.commit()
        return None, None
    except Exception as e:
        conn.rollback()
        raise e

def _pg_executemany(sql, params_list, template=None):
    """批量插入"""
    conn = _get_pg()
    cur = conn.cursor()
    try:
        from psycopg2.extras import execute_values
        execute_values(cur, sql, params_list, template=template, page_size=500)
        cur.close()
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e

# ============================================================
# 建表（幂等）
# ============================================================
DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS daily_prices (
        symbol TEXT NOT NULL,
        trade_date DATE NOT NULL,
        open DOUBLE PRECISION,
        high DOUBLE PRECISION,
        low DOUBLE PRECISION,
        close DOUBLE PRECISION,
        volume BIGINT,
        source TEXT DEFAULT 'unknown',
        updated_at TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (symbol, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS macro_indicators (
        indicator TEXT NOT NULL,
        date DATE NOT NULL,
        value DOUBLE PRECISION,
        frequency TEXT DEFAULT 'd',
        source TEXT DEFAULT 'fred',
        updated_at TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (indicator, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS crypto_metrics (
        symbol TEXT NOT NULL,
        date DATE NOT NULL,
        price DOUBLE PRECISION,
        mcap BIGINT,
        volume_24h BIGINT,
        source TEXT DEFAULT 'coingecko',
        updated_at TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (symbol, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pipeline_log (
        run_id SERIAL PRIMARY KEY,
        task_name TEXT NOT NULL,
        status TEXT DEFAULT 'running',
        rows_fetched INT DEFAULT 0,
        error_msg TEXT,
        started_at TIMESTAMPTZ DEFAULT now(),
        finished_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS etf_list (
        code TEXT PRIMARY KEY,
        name TEXT,
        category TEXT,
        active BOOLEAN DEFAULT TRUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS etf_snapshots (
        code TEXT NOT NULL,
        trade_date DATE NOT NULL,
        time TEXT,
        name TEXT,
        price REAL,
        change_pct REAL,
        high REAL,
        low REAL,
        volume REAL,
        amount REAL,
        PRIMARY KEY (code, trade_date, time)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_signals (
        signal_type TEXT NOT NULL,
        code TEXT,
        trade_date DATE NOT NULL,
        value DOUBLE PRECISION,
        change_val DOUBLE PRECISION,
        change_pct DOUBLE PRECISION,
        extra JSONB DEFAULT '{}',
        updated_at TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (signal_type, trade_date, code)
    )
    """,
]

def init_schema():
    """建所有表（幂等）"""
    for ddl in DDL_STATEMENTS:
        try:
            _pg_execute(ddl, fetch=False)
        except Exception as e:
            log.warning(f"DDL error (may be harmless): {e}")
    log.info("✅ PG schema initialized")

# ============================================================
# 1. FRED → macro_indicators
# ============================================================
FRED_SERIES = {
    # 美股指数
    "SP500": "S&P 500",
    "DJIA": "道琼斯",
    "NASDAQCOM": "纳斯达克综合指数",
    # 波动率
    "VIXCLS": "VIX恐慌指数",
    # 利率
    "DGS10": "10年国债收益率",
    "DGS2": "2年国债收益率",
    "FEDFUNDS": "联邦基金利率",
    # 商品
    "DCOILBRENTEU": "布伦特原油(美元/桶)",
    "GOLDAMGBD228NLBM": "金价(美元/盎司)",
    # 经济
    "CPIAUCSL": "CPI(消费者价格指数)",
    "UNRATE": "失业率(%)",
    "GDP": "GDP(十亿美元)",
    "ICSA": "首次申请失业救济人数",
    # 美元
    "DTWEXBGS": "美元指数(贸易加权)",
}

@lru_cache(maxsize=1)
def fetch_fred(series_id: str, start: str = "2020-01-01") -> Optional[pd.DataFrame]:
    """拉取单个 FRED 系列 → DataFrame(index=date, value=float)"""
    import requests
    r = requests.get("https://api.stlouisfed.org/fred/series/observations", params={
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": start,
    }, timeout=20)
    if r.status_code != 200:
        log.warning(f"FRED {series_id}: HTTP {r.status_code}")
        return None
    obs = r.json().get("observations", [])
    if not obs:
        log.warning(f"FRED {series_id}: no observations")
        return None
    df = pd.DataFrame(obs)
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"])
    df = df.set_index("date")
    return df[["value"]]

def update_fred(since: str = "2020-01-01"):
    """全量更新所有 FRED 指标到 PG"""
    rows_total = 0
    for sid, name in FRED_SERIES.items():
        try:
            df = fetch_fred(sid, since)
            if df is None or df.empty:
                continue
            rows = []
            for dt, row in df.iterrows():
                rows.append((sid, str(dt.date()), float(row["value"]), "d"))
            if rows:
                _pg_executemany(
                    "INSERT INTO macro_indicators (indicator, date, value, frequency) VALUES %s "
                    "ON CONFLICT (indicator, date) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                    rows, template="(%s, %s::date, %s, %s)"
                )
            rows_total += len(rows)
            log.info(f"  FRED {sid} ({name}): {len(rows)} rows")
        except Exception as e:
            log.error(f"  FRED {sid} failed: {e}")
        time.sleep(0.3)  # rate limit
    log.info(f"✅ FRED total: {rows_total} rows")

# ============================================================
# 2. AKShare → daily_prices (A股)
# ============================================================
# 主要指数 + 代表性标的
A_SHARE_LIST = [
    # 指数
    ("000001", "index", "上证指数"),
    ("399001", "index", "深证成指"),
    ("399006", "index", "创业板指"),
    ("000688", "index", "科创50"),
    # 核心股票
    ("600519", "stock", "贵州茅台"),
    ("000300", "index", "沪深300"),
]

_TENCENT_SYMBOL_MAP = {
    "000001": "sh000001",
    "399001": "sz399001",
    "399006": "sz399006",
    "000688": "sh000688",
    "000300": "sh000300",
    "600519": "sh600519",
    "00700": "hk00700",
}

def _fmt_date(d: str) -> str:
    """把 YYYYMMDD 转 YYYY-MM-DD，各种格式容错"""
    d = d.replace("-", "").replace("/", "").strip()
    if len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d

def fetch_tencent_daily(symbol: str, start: str = "20250101", end: str = None) -> Optional[pd.DataFrame]:
    """从腾讯接口获取A股/港股日线（免费，无需代理，阿里云/腾讯云都通）
    返回 DataFrame[open, high, low, close, volume, trade_date]
    """
    import requests
    if end is None:
        end = datetime.now().strftime("%Y%m%d")
    
    # 统一成 YYYY-MM-DD 格式
    start_date = _fmt_date(start)
    end_date = _fmt_date(end)
    
    tencent_sym = _TENCENT_SYMBOL_MAP.get(symbol)
    if not tencent_sym:
        if symbol.startswith("6"):
            tencent_sym = f"sh{symbol}"
        elif symbol.startswith(("0", "3")):
            tencent_sym = f"sz{symbol}"
        else:
            tencent_sym = f"sh{symbol}"
    
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tencent_sym},day,{start_date},{end_date},640,qfq"
    
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            log.warning(f"Tencent {symbol}: HTTP {r.status_code}")
            return None
        
        data = r.json()
        # 容错：data 可能是 list 或 dict
        if isinstance(data, dict):
            if data.get("code") != 0:
                log.warning(f"Tencent {symbol}: API code={data.get('code')}, msg={data.get('msg','')}")
                return None
            stock_data = data.get("data", {})
        elif isinstance(data, list):
            log.warning(f"Tencent {symbol}: got list response, length={len(data)}")
            return None
        else:
            log.warning(f"Tencent {symbol}: unexpected response type {type(data)}")
            return None
        
        # 从 stock_data 获取对应 symbol 的数据
        if isinstance(stock_data, dict):
            inner = stock_data.get(tencent_sym)
        elif isinstance(stock_data, list):
            inner = None
        else:
            inner = stock_data
        
        raw = None
        if isinstance(inner, dict):
            raw = inner.get("day") or inner.get("qfqday")
        elif isinstance(inner, list):
            raw = inner
        
        if not raw:
            log.warning(f"Tencent {symbol}: no day data (inner={type(inner).__name__})")
            return None
        
        records = []
        for row in raw:
            try:
                records.append({
                    "trade_date": pd.to_datetime(row[0]),
                    "open": float(row[1]),
                    "close": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "volume": float(row[5]),
                })
            except (IndexError, ValueError):
                continue
        
        if not records:
            return None
        
        df = pd.DataFrame(records)
        df = df[df["trade_date"] >= pd.to_datetime(start_date)]
        return df[["trade_date", "open", "high", "low", "close", "volume"]]
    except Exception as e:
        log.warning(f"Tencent {symbol}: {e}")
        return None

def fetchakshare_daily(symbol: str, start: str = "20250101", end: str = None) -> Optional[pd.DataFrame]:
    """A股日线（腾讯主力 → AKShare备用）"""
    # 先试腾讯（最稳）
    df = fetch_tencent_daily(symbol, start, end)
    if df is not None and not df.empty:
        return df
    
    # fallback: AKShare 带重试
    
    import requests
    if end is None:
        end = datetime.now().strftime("%Y%m%d")
    
    for attempt in range(2):
        try:
            time.sleep(1)
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start, end_date=end, adjust="")
            if df is not None and not df.empty:
                df.columns = [c.strip() for c in df.columns]
                df = df.rename(columns={
                    "日期": "trade_date", "开盘": "open", "最高": "high", "最低": "low", "收盘": "close", "成交量": "volume",
                })
                df["trade_date"] = pd.to_datetime(df["trade_date"])
                df["open"] = pd.to_numeric(df["open"], errors="coerce")
                df["high"] = pd.to_numeric(df["high"], errors="coerce")
                df["low"] = pd.to_numeric(df["low"], errors="coerce")
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
                return df[["trade_date", "open", "high", "low", "close", "volume"]]
        except Exception as e:
            log.warning(f"  AKShare {symbol} attempt {attempt+1}: {e}")
            time.sleep(3)
    return None

def updateakshare(since: str = "20250101"):
    """更新 A 股数据到 PG（每只间隔3秒防封）"""
    for sym, typ, name in A_SHARE_LIST:
        try:
            sym_pg = f"A.{sym}" if typ == "stock" else f"I.{sym}"
            df = fetchakshare_daily(sym, since)
            if df is None or df.empty:
                log.warning(f"  A股 {name}({sym}): no data")
                continue
            rows = []
            for _, r in df.iterrows():
                rows.append((sym_pg, str(r["trade_date"].date()), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), int(r["volume"]), "akshare"))
            if rows:
                _pg_executemany(
                    "INSERT INTO daily_prices (symbol, trade_date, open, high, low, close, volume, source) VALUES %s "
                    "ON CONFLICT (symbol, trade_date) DO UPDATE SET "
                    "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
                    "close=EXCLUDED.close, volume=EXCLUDED.volume, updated_at=now()",
                    rows, template="(%s, %s::date, %s, %s, %s, %s, %s, %s)"
                )
            log.info(f"  A股 {name}({sym_pg}): {len(rows)} rows")
        except Exception as e:
            log.error(f"  A股 {name} failed: {e}")
        time.sleep(3)

# ============================================================
# 3. CoinGecko → crypto_metrics
# ============================================================
CRYPTO_LIST = ["bitcoin", "ethereum"]

def fetch_coingecko(coin: str = "bitcoin", days: int = 90) -> Optional[pd.DataFrame]:
    import requests
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart",
            proxies={"http": PROXY, "https": PROXY},

            params={"vs_currency": "usd", "days": days},
            timeout=20
        )
        if r.status_code != 200:
            return None
        data = r.json()
        prices = data.get("prices", [])
        if not prices:
            return None
        df = pd.DataFrame(prices, columns=["timestamp_ms", "price"])
        df["date"] = pd.to_datetime(df["timestamp_ms"], unit="ms").dt.date
        # market_caps
        mcap = data.get("market_caps", [])
        if mcap:
            df_mcap = pd.DataFrame(mcap, columns=["timestamp_ms", "mcap"])
            df_mcap["date"] = pd.to_datetime(df_mcap["timestamp_ms"], unit="ms").dt.date
            df = df.merge(df_mcap[["date", "mcap"]], on="date", how="left")
        else:
            df["mcap"] = None
        return df.groupby("date").agg({"price": "last", "mcap": "last"}).reset_index()
    except Exception as e:
        log.warning(f"CoinGecko {coin}: {e}")
        return None

def update_coingecko(days: int = 90):
    for coin in CRYPTO_LIST:
        sym = "BTC-USD" if coin == "bitcoin" else "ETH-USD"
        df = fetch_coingecko(coin, days)
        if df is None or df.empty:
            log.warning(f"  Crypto {coin}: no data")
            continue
        rows = [(sym, str(row["date"]), float(row["price"]), int(row["mcap"]) if row["mcap"] else None, None, "coingecko")
                for _, row in df.iterrows()]
        if rows:
            _pg_executemany(
                "INSERT INTO crypto_metrics (symbol, date, price, mcap, volume_24h, source) VALUES %s "
                "ON CONFLICT (symbol, date) DO UPDATE SET "
                "price=EXCLUDED.price, mcap=EXCLUDED.mcap, updated_at=now()",
                rows, template="(%s, %s::date, %s, %s, %s, %s)"
            )
        log.info(f"  Crypto {sym}: {len(rows)} rows")

# ============================================================
# 4. UCDP → 冲突数据缓存到 PG（可选）
# ============================================================
# 8. ETF 数据（同花顺源）
# ============================================================

def init_etf_list():
    import akshare as ak
    """从同花顺拉取 ETF 列表，存入 etf_list 表"""
    try:
        df = ak.fund_etf_spot_ths()
    except Exception as e:
        log.error(f"ETF 列表获取失败: {e}")
        return 0
    
    log.info(f"ETF 列表获取: {len(df)} 只")
    
    # 纯 ETF（代码 51/58/159/15 开头）
    import re
    df["code_num"] = df["基金代码"].astype(str).str.replace(r"[a-z]", "", regex=True)
    pure = df[df["code_num"].str.match(r"^(51|58|159|15)\d+")].copy()
    pure = pure.drop_duplicates(subset="基金代码")
    
    rows_to_upsert = []
    for _, r in pure.iterrows():
        code = str(r["基金代码"]).strip()
        name = str(r.get("基金名称", "")).strip()
        rows_to_upsert.append((code, name, "ETF"))
    
    if rows_to_upsert:
        _pg_executemany(
            "INSERT INTO etf_list (code, name, category, active) VALUES %s "
            "ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, active=TRUE",
            rows_to_upsert,
            template="(%s,%s,%s,TRUE)"
        )
    
    log.info(f"✅ ETF 列表入库: {len(rows_to_upsert)} 只")
    return len(rows_to_upsert)


def scan_etf_market():
    """扫描所有 ETF 实时行情（腾讯批量接口）"""
    rows, _ = _pg_execute("SELECT code, name FROM etf_list WHERE active ORDER BY code")
    if not rows:
        log.warning("⚠️ etf_list 为空，先运行 --init-etf")
        return
    
    codes = [r[0] for r in rows]
    names_map = {r[0]: r[1] for r in rows}
    log.info(f"📡 ETF 行情扫描: {len(codes)} 只")
    
    etf_snaps = []
    batch_size = 50
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        df = fetch_batch_quotes(batch)
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                etf_snaps.append((
                    r["code"], date.today().isoformat(), datetime.now().strftime("%H%M%S"),
                    names_map.get(r["code"], r["name"]),
                    r["price"], r["change_pct"],
                    r["high"], r["low"],
                    r["volume"], r["amount"]
                ))
    
    if etf_snaps:
        _pg_executemany(
            "INSERT INTO etf_snapshots (code, trade_date, time, name, price, change_pct, high, low, volume, amount) "
            "VALUES %s ON CONFLICT (code, trade_date, time) DO UPDATE "
            "SET price=EXCLUDED.price, change_pct=EXCLUDED.change_pct",
            etf_snaps,
            template="(%s,%s::date,%s,%s,%s,%s,%s,%s,%s,%s)"
        )
    
    log.info(f"  ✅ ETF 行情: {len(etf_snaps)} 条")
    
    # 简报
    winners = sorted(etf_snaps, key=lambda x: x[5] or 0, reverse=True)[:5]
    losers = sorted(etf_snaps, key=lambda x: x[5] or 0)[:3]
    log.info(f"📊 ETF 涨幅TOP5: {[(w[3], f'{w[5]:.2f}%') for w in winners]}")
    log.info(f"📊 ETF 跌幅TOP3: {[(l[3], f'{l[5]:.2f}%') for l in losers]}")


def backfill_etf_kline(start: str = "20250101", end: str = None):
    """回填 ETF 日线（异步并发）"""
    try:
        import asyncio, aiohttp, json as _json
    except ImportError:
        log.error("需要 aiohttp: pip install aiohttp")
        return 0, 0
    
    rows, _ = _pg_execute("SELECT code, name FROM etf_list WHERE active ORDER BY code")
    if not rows:
        log.warning("⚠️ etf_list 为空，先运行 --init-etf")
        return 0, 0
    
    codes = [(r[0], r[1]) for r in rows]
    log.info(f"📡 ETF 日线回填: {len(codes)} 只")
    
    if end is None:
        end = datetime.now().strftime("%Y%m%d")
    
    # 跳过已有数据的
    existing = set()
    ex_rows, _ = _pg_execute("SELECT DISTINCT substr(symbol, 5) FROM daily_prices WHERE symbol LIKE 'ETF.%'")
    if ex_rows:
        existing = set(r[0] for r in ex_rows)
    
    to_fetch = [c for c in codes if c[0] not in existing]
    log.info(f"  需补: {len(to_fetch)} 只 (已有 {len(existing)} 只跳过)")
    
    if not to_fetch:
        log.info("  ✅ 全部 ETF 已有日线")
        return 0, 0
    
    success = 0
    failed = 0
    buffer = []
    
    for idx, (code, name) in enumerate(to_fetch):
        if idx > 0 and idx % 50 == 0:
            log.info(f"  📊 进度: {idx}/{len(to_fetch)} = {idx/len(to_fetch)*100:.0f}%, 成功={success}, 失败={failed}")
        try:
            tx = f"sh{code}" if code.startswith("5") else f"sz{code}"
            start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
            end_fmt = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
            url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tx},day,{start_fmt},{end_fmt},640,qfq"
            import requests
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            j = r.json()
            if isinstance(j, dict) and j.get("code") == 0:
                inner = j.get("data", {}).get(tx, {})
                raw = inner.get("day") or inner.get("qfqday") if isinstance(inner, dict) else None
                if raw and len(raw) > 0:
                    for row in raw:
                        try:
                            d = row[0]
                            o, c, h, l, v = float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
                            buffer.append((f"ETF.{code}", d, o, h, l, c, v, "tencent"))
                        except (IndexError, ValueError):
                            continue
                    success += 1
                else:
                    failed += 1
            else:
                failed += 1
        except Exception:
            failed += 1
        time.sleep(0.08)
        
        if len(buffer) >= 200:
            _pg_executemany(
                "INSERT INTO daily_prices (symbol, trade_date, open, high, low, close, volume, source) VALUES %s "
                "ON CONFLICT (symbol, trade_date) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, "
                "low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume, updated_at=now()",
                buffer, template="(%s, %s::date, %s, %s, %s, %s, %s, %s)"
            )
            buffer = []
    
    if buffer:
        _pg_executemany(
            "INSERT INTO daily_prices (symbol, trade_date, open, high, low, close, volume, source) VALUES %s "
            "ON CONFLICT (symbol, trade_date) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, "
            "low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume, updated_at=now()",
            buffer, template="(%s, %s::date, %s, %s, %s, %s, %s, %s)"
        )
    
    log.info(f"  ✅ ETF 日线回填: {success} 成功, {failed} 失败")
    return success, failed

# ============================================================
# 当前 UCDP 直接走 fetch_ucdp.py，暂不入 PG
# 这里留个接口，后续需要再实现

# ============================================================
# 5. 查询接口（智库 Pipeline 调用）
# ============================================================
@lru_cache(maxsize=256)
def get_stock_hist(symbol: str, start: str = "2020-01-01", end: str = None) -> pd.DataFrame:
    """从 PG 读取日线数据，返回 DataFrame(index=date, columns=open/high/low/close/volume)"""
    if end is None:
        end = date.today().isoformat()
    rows, cols = _pg_execute(
        """SELECT trade_date, open, high, low, close, volume, source
           FROM daily_prices
           WHERE symbol = %s AND trade_date BETWEEN %s AND %s
           ORDER BY trade_date""",
        (symbol, start, end)
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume", "source"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("trade_date")
    return df

# ============================================================
# 5. 全量 A 股扫描（腾讯批量行情接口）
# ============================================================

def init_stock_list():
    """初始化股票列表到 PG（去除 ST/退市）"""
    
    df = ak.stock_info_a_code_name()
    active = df[~df['name'].str.contains('ST|退', na=False)]
    codes = active['code'].tolist()
    _pg_execute("DROP TABLE IF EXISTS stock_list", fetch=False)
    _pg_execute(
        "CREATE TABLE stock_list (code TEXT PRIMARY KEY, name TEXT, active BOOLEAN DEFAULT TRUE)",
        fetch=False
    )
    rows = [(c, active[active['code']==c]['name'].iloc[0], True) for c in codes]
    _pg_executemany("INSERT INTO stock_list (code, name, active) VALUES %s ON CONFLICT DO NOTHING",
                     rows, template="(%s, %s, %s)")
    log.info(f"📋 股票列表初始化完成: {len(codes)} 只活跃股")
    return codes

def get_active_codes() -> list:
    """获取活跃股列表"""
    rows, _ = _pg_execute("SELECT code FROM stock_list WHERE active=TRUE ORDER BY code")
    return [r[0] for r in rows] if rows else init_stock_list()

def _parse_tx_quote(code: str, raw: str) -> Optional[dict]:
    """解析腾讯批量返回的单条行情"""
    parts = raw.split("~")
    if len(parts) < 80:
        return None
    
    def _f(i: int, default=0.0) -> float:
        """安全的 float 转换"""
        v = parts[i] if i < len(parts) else ""
        if v and v.strip():
            try:
                return float(v.strip())
            except ValueError:
                return default
        return default
    
    def _iv(i: int, default=0) -> int:
        v = parts[i] if i < len(parts) else ""
        if v and v.strip():
            try:
                return int(float(v.strip()))
            except ValueError:
                return default
        return default
    
    try:
        name = parts[2] if len(parts) > 2 and parts[2].strip() else code
        return {
            "code": code, "name": name,
            "price": _f(3), "pre_close": _f(4),
            "change": _f(31), "change_pct": _f(32),
            "high": _f(33), "low": _f(34),
            "volume": _iv(36),
            "amount": _f(37) / 10000,  # 万元→亿
            "turnover": _f(38), "pe": _f(39),
            "amplitude": _f(43),
            "total_mv": _f(44), "float_mv": _f(45),
            "pb": _f(46),
        }
    except Exception:
        return None

def fetch_batch_quotes(codes: list, batch_size: int = 50) -> pd.DataFrame:
    """用腾讯批量行情接口获取全市场快照
    返回 DataFrame: code, name, price, pre_close, change, change_pct, high, low, volume, amount, turnover, pe, pb, total_mv, float_mv, amplitude
    """
    import requests
    all_records = []
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        # 转腾讯 code 格式：sh600000, sz000001
        tx_codes = []
        for c in batch:
            if c.startswith("6"):
                tx_codes.append(f"sh{c}")
            elif c.startswith(("0", "3", "1")):
                tx_codes.append(f"sz{c}")
            else:
                tx_codes.append(f"sh{c}")
        q = ",".join(tx_codes)
        url = f"https://web.sqt.gtimg.cn/q={q}"
        try:
            r = requests.get(url, timeout=10,
                             headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0"})
            if r.status_code == 200:
                for line in r.text.strip().split(";"):
                    if not line or "=" not in line:
                        continue
                    # 提取 v_sh600519=... 中的值
                    try:
                        val = line.split("=", 1)[1].strip('"')
                        rec_code = line.split("_")[1].split("=")[0]  # sh600519
                        actual_code = rec_code[2:]  # 去掉 sh/sz
                        rec = _parse_tx_quote(actual_code, val)
                        if rec:
                            all_records.append(rec)
                    except Exception:
                        continue
        except Exception as e:
            log.warning(f"Batch quotes at {i}: {e}")
    
    if not all_records:
        return pd.DataFrame()
    df = pd.DataFrame(all_records)
    return df

def scan_all_stocks() -> pd.DataFrame:
    """扫盘：获取所有活跃股最新行情并存入库"""
    codes = get_active_codes()
    log.info(f"📡 全市场行情扫描: {len(codes)} 只股票")
    t0 = time.time()
    df = fetch_batch_quotes(codes)
    elapsed = time.time() - t0
    
    if df.empty:
        log.warning("  行情扫描无数据")
        return df
    
    log.info(f"  ✅ {len(df)} 只股票行情已完成 ({elapsed:.0f}s)")
    
    # 存入 PG 快照表
    _pg_execute(
        "CREATE TABLE IF NOT EXISTS market_snapshots ("
        "code TEXT, trade_date DATE, time TIMESTAMP,"
        "price REAL, change_pct REAL, amount REAL, turnover REAL,"
        "pe REAL, pb REAL, total_mv REAL, float_mv REAL,"
        "volume REAL, high REAL, low REAL, amplitude REAL,"
        "PRIMARY KEY (code, trade_date)"
        ")", fetch=False
    )
    today = date.today()
    now = datetime.now()
    rows = []
    for _, r in df.iterrows():
        rows.append((
            r["code"], today, now,
            r["price"], r["change_pct"], r["amount"], r["turnover"],
            r["pe"], r["pb"], r["total_mv"], r["float_mv"],
            r["volume"], r["high"], r["low"], r["amplitude"],
        ))
    _pg_executemany(
        "INSERT INTO market_snapshots (code, trade_date, time, price, change_pct, amount, turnover, pe, pb, total_mv, float_mv, volume, high, low, amplitude) "
        "VALUES %s ON CONFLICT (code, trade_date) DO UPDATE SET "
        "time=EXCLUDED.time, price=EXCLUDED.price, change_pct=EXCLUDED.change_pct, "
        "amount=EXCLUDED.amount, turnover=EXCLUDED.turnover, pe=EXCLUDED.pe, "
        "pb=EXCLUDED.pb, total_mv=EXCLUDED.total_mv, float_mv=EXCLUDED.float_mv, "
        "volume=EXCLUDED.volume, high=EXCLUDED.high, low=EXCLUDED.low, amplitude=EXCLUDED.amplitude",
        rows,
        template="(%s, %s::date, %s::timestamp, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    log.info(f"  💾 已存入 PG market_snapshots: {len(rows)} 条")
    
    log.info(f"\n📊 行情速览:")
    log.info(f"  上涨: {(df['change_pct']>0).sum()} | 下跌: {(df['change_pct']<0).sum()} | 平盘: {(df['change_pct']==0).sum()}")
    if len(df) > 0:
        top5 = df.nlargest(5, 'change_pct')
        log.info(f"  涨幅TOP5:")
        for _, r in top5.iterrows():
            log.info(f"    {r['code']} {r['name']}: {r['change_pct']:+.2f}% (PE={r['pe']:.1f}, 市值={r['total_mv']:.0f}亿)")
    return df

# ============================================================
# 6. 异步日线增量拉取（并发拉取最新的几条日线）
# ============================================================

def backfill_hist_kline(codes: list = None, max_concurrent: int = 30, start: str = "20230101", end: str = None):
    """回填/增量全量日线，异步并发
    - 首次：拉2023年至今的全量
    - 增量：自动跳过已有数据，只拉最新
    返回 (成功数, 失败数)
    """
    import asyncio
    import aiohttp
    try:
        import aiohttp
    except ImportError:
        log.error("需要安装 aiohttp: pip install aiohttp")
        return 0, 0
    
    if codes is None:
        codes = get_active_codes()
    if end is None:
        end = datetime.now().strftime("%Y%m%d")
    
    # 检查已有的最新 trade_date，跳过已有数据的股票
    already_done = set()
    rows, _ = _pg_execute("SELECT symbol FROM daily_prices WHERE trade_date >= %s::date - 5 GROUP BY symbol", (end[:10],))
    if rows:
        for r in rows:
            already_done.add(r[0].replace("A.", "").replace("I.", ""))
    
    to_fetch = [c for c in codes if c not in already_done]
    log.info(f"📡 日线回填: {len(codes)} 只中 {len(to_fetch)} 只需要补 (已有数据跳过)")
    _t_start = time.time()
    
    if not to_fetch:
        log.info("  ✅ 所有股票已有最新日线")
        return 0, 0
    
    # 改用同步批量模式：requests + PG batch insert，每批分批入库
    success = 0
    failed = 0
    total = len(to_fetch)
    batch_buffer = []  # 累积一批写入 PG
    
    # 分批客户端请求（requests 同步，但每批批量入库优化 PG）
    for idx, code in enumerate(to_fetch):
        if idx > 0 and idx % 100 == 0:
            # 每100个汇报进度
            elapsed = time.time() - _t_start
            rate = idx / elapsed if elapsed > 0 else 0
            log.info(f"  📊 进度: {idx}/{total} ({idx/total*100:.0f}%), {rate:.0f} 只/秒, 成功={success}, 失败={failed}")
        
        try:
            # 转腾讯 code
            if code.startswith("6"):
                tx = f"sh{code}"
            elif code.startswith(("0", "3")):
                tx = f"sz{code}"
            else:
                tx = f"sh{code}"
            start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
            end_fmt = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
            url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tx},day,{start_fmt},{end_fmt},640,qfq"
            
            import requests as _req
            r = _req.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            j = r.json()
            
            if isinstance(j, dict) and j.get("code") == 0:
                inner = j.get("data", {}).get(tx, {})
                raw = inner.get("day") or inner.get("qfqday") if isinstance(inner, dict) else None
                if raw and len(raw) > 0:
                    for row in raw:
                        try:
                            d = row[0]
                            o, c, h, l, v = float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
                            batch_buffer.append((f"A.{code}", d, o, h, l, c, v, "tencent"))
                        except (IndexError, ValueError):
                            continue
                    success += 1
                    
                    # 每 200 条记录批量入库一次
                    if len(batch_buffer) >= 200:
                        _pg_executemany(
                            "INSERT INTO daily_prices (symbol, trade_date, open, high, low, close, volume, source) VALUES %s "
                            "ON CONFLICT (symbol, trade_date) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, "
                            "low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume, updated_at=now()",
                            batch_buffer,
                            template="(%s, %s::date, %s, %s, %s, %s, %s, %s)"
                        )
                        batch_buffer = []
                else:
                    failed += 1
            else:
                failed += 1
        except Exception:
            failed += 1
        time.sleep(0.1)  # 每只100ms，5000只≈8分钟
    
    # 最后一批入库
    if batch_buffer:
        _pg_executemany(
            "INSERT INTO daily_prices (symbol, trade_date, open, high, low, close, volume, source) VALUES %s "
            "ON CONFLICT (symbol, trade_date) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, "
            "low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume, updated_at=now()",
            batch_buffer,
            template="(%s, %s::date, %s, %s, %s, %s, %s, %s)"
        )
    
    _elapsed = time.time() - _t_start
    log.info(f"  ✅ 日线回填: {success} 成功, {failed} 失败 ({_elapsed:.0f}s, {success/_elapsed:.1f} 只/秒)")
    return success, failed

@lru_cache(maxsize=128)
def get_macro(indicator: str, start: str = "2020-01-01", end: str = None) -> pd.DataFrame:
    """从 PG 读取宏观指标"""
    if end is None:
        end = date.today().isoformat()
    rows, cols = _pg_execute(
        """SELECT date, value FROM macro_indicators
           WHERE indicator = %s AND date BETWEEN %s AND %s
           ORDER BY date""",
        (indicator, start, end)
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "value"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    return df

@lru_cache(maxsize=64)
def get_crypto(symbol: str = "BTC-USD", start: str = "2025-01-01", end: str = None) -> pd.DataFrame:
    """从 PG 读取加密货币"""
    if end is None:
        end = date.today().isoformat()
    rows, cols = _pg_execute(
        """SELECT date, price, mcap FROM crypto_metrics
           WHERE symbol = %s AND date BETWEEN %s AND %s
           ORDER BY date""",
        (symbol, start, end)
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "price", "mcap"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    return df

# ============================================================
# 6. 快速查询（单值，不走缓存）
# ============================================================
def latest_price(symbol: str) -> Optional[float]:
    """最新收盘价"""
    rows, _ = _pg_execute(
        "SELECT close FROM daily_prices WHERE symbol = %s ORDER BY trade_date DESC LIMIT 1",
        (symbol,)
    )
    return float(rows[0][0]) if rows else None

def latest_macro(indicator: str) -> Optional[float]:
    """最新宏观指标值"""
    rows, _ = _pg_execute(
        "SELECT value FROM macro_indicators WHERE indicator = %s ORDER BY date DESC LIMIT 1",
        (indicator,)
    )
    return float(rows[0][0]) if rows else None

def df_to_cache_csv(df: pd.DataFrame, name: str):
    """DataFrame → 本地 CSV 缓存（备用）"""
    path = CACHE_DIR / f"{name}.csv"
    df.to_csv(path)
    return path

# ============================================================
# 7. 全量更新
# ============================================================

def update_all():
    """全量更新所有数据源"""
    log.info("=" * 50)
    log.info("📡 全量数据更新开始")
    log.info("=" * 50)
    t_start = time.time()

    log.info("\n1️⃣  FRED 宏观经济指标")
    update_fred("2020-01-01")

    log.info("\n2️⃣  加密货币 (CoinGecko)")
    update_coingecko(days=90)

    log.info("\n3️⃣  全市场行情快照")
    scan_all_stocks()
    scan_etf_market()

    log.info("\n4️⃣  市场信号")
    _update_margin()
    _update_etf_scale()
    _update_dragon_tiger()
    _update_foreign_proxy()

    elapsed = time.time() - t_start
    log.info(f"\n✅ 全量更新完成 ({elapsed:.0f}s)")


def update_daily():
    """每日增量更新（约 3 分钟）"""
    log.info("📡 每日增量更新")
    t_start = time.time()
    
    log.info("\n1️⃣  FRED 宏观")
    update_fred((date.today() - timedelta(days=7)).isoformat())
    
    log.info("\n2️⃣  加密货币")
    update_coingecko(days=2)
    
    log.info("\n3️⃣  全市场行情快照")
    scan_all_stocks()
    scan_etf_market()
    
    log.info("\n4️⃣  市场信号")
    _update_margin()
    _update_etf_scale()
    _update_dragon_tiger()
    _update_foreign_proxy()
    
    elapsed = time.time() - t_start
    log.info(f"\n✅ 日更完成 ({elapsed:.0f}s)")

# ============================================================
# 9. 信号数据采集（两融、ETF份额、海外proxy、龙虎榜等）
# ============================================================

def _update_margin():
    import akshare as ak
    """采集融资融券（沪深大盘 + 个券明细）
    
    大盘历史用 macro_china_market_margin_sh/sz（2010~今），
    每日增量覆盖最新交易日。
    """
    
    today = date.today().isoformat()
    
    # 查当前最大日期，只更新后续数据
    rows, _ = _pg_execute("SELECT max(trade_date) FROM market_signals WHERE signal_type='margin_sh'")
    last_date_sh = str(rows[0][0]) if rows and rows[0][0] else '2010-01-01'
    
    # 沪深大盘融资融券（akshare macro 接口，全历史）
    try:
        df_sh = ak.macro_china_market_margin_sh()
        count = 0
        for _, r in df_sh.iterrows():
            d = str(r["日期"])[:10]
            if d <= last_date_sh:
                continue
            _pg_execute(
                "INSERT INTO market_signals (signal_type, code, trade_date, value, change_val, change_pct, extra) "
                "VALUES (%s,%s,%s::date,%s,%s,%s,%s::jsonb) "
                "ON CONFLICT (signal_type, trade_date, code) DO UPDATE SET value=EXCLUDED.value",
                ("margin_sh", "TOTAL", d, float(r["融资融券余额"]), float(r["融资余额"]), float(r["融资买入额"]),
                 json.dumps({"rzye": float(r["融资余额"]), "rqye": float(r["融券余额"]), "rzyre": float(r["融资买入额"])})),
                fetch=False
            )
            count += 1
        log.info(f"  ✅ 上交所两融余额: {count} 天新数据")
    except Exception as e:
        log.warning(f"  ⚠️ 上交所两融失败: {e}")
    
    try:
        df_sz = ak.macro_china_market_margin_sz()
        rows, _ = _pg_execute("SELECT max(trade_date) FROM market_signals WHERE signal_type='margin_sz'")
        last_date_sz = str(rows[0][0]) if rows and rows[0][0] else '2010-01-01'
        count = 0
        for _, r in df_sz.iterrows():
            d = str(r["日期"])[:10]
            if d <= last_date_sz:
                continue
            _pg_execute(
                "INSERT INTO market_signals (signal_type, code, trade_date, value, change_val, change_pct, extra) "
                "VALUES (%s,%s,%s::date,%s,%s,%s,%s::jsonb) "
                "ON CONFLICT (signal_type, trade_date, code) DO UPDATE SET value=EXCLUDED.value",
                ("margin_sz", "TOTAL", d, float(r["融资融券余额"]), float(r["融资余额"]), float(r["融资买入额"]),
                 json.dumps({"rzye": float(r["融资余额"]), "rqye": float(r["融券余额"]), "rzyre": float(r["融资买入额"])})),
                fetch=False
            )
            count += 1
        log.info(f"  ✅ 深交所两融余额: {count} 天新数据")
    except Exception as e:
        log.warning(f"  ⚠️ 深交所两融失败: {e}")
    
    # 上交所标的证券明细
    try:
        detail = ak.stock_margin_detail_sse()
        for _, r in detail.iterrows():
            _pg_execute(
                "INSERT INTO market_signals (signal_type, code, trade_date, value, change_val, change_pct, extra) "
                "VALUES (%s,%s,%s::date,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING",
                ("margin_detail", r["标的证券代码"], today,
                 float(r["融资余额"]), float(r["融资买入额"]), float(r.get("融券余量", 0)),
                 json.dumps({"name": r["标的证券简称"]})), fetch=False
            )
        log.info(f"  ✅ 上交所两融明细: {len(detail)} 标的")
    except Exception as e:
        log.warning(f"  ⚠️ 上交所两融明细失败: {e}")


def _update_etf_scale():
    """采集 ETF 份额日度变化（上交所+深交所）"""
    import akshare
    import tempfile, os, warnings, traceback
    import requests
    
    today_str = date.today().strftime("%Y%m%d")
    today_iso = date.today().isoformat()
    
    # 上交所ETF份额（akshare 直接调，数据未就绪时自动回退最近交易日）
    try:
        dates_to_try = [today_str]
        if today_str == date.today().strftime("%Y%m%d"):
            # 今天的数据可能还没出，补充最近几天
            for d in range(1, 5):
                dt = date.today() - timedelta(days=d)
                dates_to_try.append(dt.strftime("%Y%m%d"))
        
        df = None
        used_date = None
        for d in dates_to_try:
            try:
                df = akshare.fund_etf_scale_sse(date=d)
                if len(df) > 0:
                    used_date = d
                    log.info(f"  📅 上交所ETF份额使用 {d}")
                    break
            except Exception:
                continue
        
        if df is None or len(df) == 0:
            log.warning("  ⚠️ 上交所ETF份额: 无可用的数据")
        else:
            count = 0
            for _, r in df.iterrows():
                code = r["基金代码"]
                name = r["基金简称"]
                share = float(r["基金份额"])
                _pg_execute(
                    "INSERT INTO market_signals (signal_type, code, trade_date, value, extra) "
                    "VALUES (%s,%s,%s::date,%s,%s::jsonb) ON CONFLICT (signal_type, trade_date, code) DO UPDATE SET value=EXCLUDED.value",
                    ("etf_share", code, used_date[:4] + "-" + used_date[4:6] + "-" + used_date[6:], share,
                     json.dumps({"name": name, "type": r.get("ETF类型", "")})), fetch=False
                )
                count += 1
            log.info(f"  ✅ ETF份额(上交所): {count} 只")
    except Exception as e:
        log.warning(f"  ⚠️ 上交所ETF份额失败: {traceback.format_exc()}")
    
    # 深交所ETF份额（直接 requests 绕过 akshare bytes bug）
    try:
        url = "https://fund.szse.cn/api/report/ShowReport"
        params = {"SHOWTYPE": "xlsx", "CATALOGID": "1000_lf", "TABKEY": "tab1", "random": "0.076"}
        headers = {"Referer": "https://fund.szse.cn/marketdata/fundslist/index.html", "User-Agent": "Mozilla/5.0"}
        r = requests.get(url, params=params, headers=headers, timeout=15)
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as f:
            f.write(r.content)
            tmp_path = f.name
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            df_sz = pd.read_excel(tmp_path, engine="openpyxl", dtype={"基金代码": str})
        os.unlink(tmp_path)
        count = 0
        for _, r in df_sz.iterrows():
            share_str = str(r.get("当前规模(份)", "0")).replace(",", "")
            share = float(share_str) if share_str else 0.0
            _pg_execute(
                "INSERT INTO market_signals (signal_type, code, trade_date, value, extra) "
                "VALUES (%s,%s,%s::date,%s,%s::jsonb) ON CONFLICT (signal_type, trade_date, code) DO UPDATE SET value=EXCLUDED.value",
                ("etf_share", r["基金代码"], today_iso, share,
                 json.dumps({"name": r["基金简称"], "type": r.get("基金类别", "")})), fetch=False
            )
            count += 1
        log.info(f"  ✅ ETF份额(深交所): {count} 只")
    except Exception as e:
        log.warning(f"  ⚠️ 深交所ETF份额失败: {e}")


def _update_foreign_proxy():
    """通过 yfinance 采集海外中国ETF行情（外资流向proxy）"""
    import yfinance as yf
    import os
    
    # 设置代理（yfinance 新版用 curl_cffi，自动读环境变量）
    os.environ.setdefault("https_proxy", "http://172.26.0.1:10808")
    os.environ.setdefault("http_proxy", "http://172.26.0.1:10808")
    
    foreign_etfs = {
        "FXI": "iShares中国大盘",  # 美国上市，跟踪富时中国50指数
        "MCHI": "MSCI中国ETF",
        "KWEB": "中概互联网ETF",
        "ASHR": "沪深300中证500ETF",
        "CHAU": "做多A股2x",
    }
    
    for ticker, name_cn in foreign_etfs.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="10d")
            if hist.empty:
                log.warning(f"  ⚠️ {ticker}: 无数据")
                continue
            
            latest = hist.iloc[-1]
            trade_date = latest.name.strftime("%Y-%m-%d") if hasattr(latest.name, 'strftime') else str(latest.name)[:10]
            
            _pg_execute(
                "INSERT INTO market_signals (signal_type, code, trade_date, value, change_val, change_pct, extra) "
                "VALUES (%s,%s,%s::date,%s,%s,%s,%s::jsonb) "
                "ON CONFLICT (signal_type, trade_date, code) DO UPDATE SET value=EXCLUDED.value, change_pct=EXCLUDED.change_pct",
                ("foreign_etf", ticker, trade_date,
                 float(latest["Close"]), float(hist["Close"].iloc[-2] - hist["Close"].iloc[-1]) if len(hist) > 1 else 0,
                 float(((latest["Close"] / hist["Close"].iloc[-2]) - 1) * 100) if len(hist) > 1 else 0,
                 json.dumps({"name": name_cn, "volume": int(latest["Volume"]), "high": float(latest["High"]), "low": float(latest["Low"])})),
                fetch=False
            )
            log.info(f"  ✅ {ticker}({name_cn}): {latest['Close']:.2f}")
        except Exception as e:
            log.warning(f"  ⚠️ {ticker}失败: {e}")


def _update_dragon_tiger():
    import akshare as ak
    """采集龙虎榜数据"""
    
    try:
        df = ak.stock_lhb_stock_statistic_em()
        count = 0
        for _, r in df.iterrows():
            code = r["代码"]
            date_val = r["最近上榜日"]
            trade_val = float(r.get("龙虎榜总成交额", r.get("龙虎榜买入额", 0)))
            _pg_execute(
                "INSERT INTO market_signals (signal_type, code, trade_date, value, extra) "
                "VALUES (%s,%s,%s::date,%s,%s::jsonb) ON CONFLICT (signal_type, trade_date, code) DO UPDATE SET value=EXCLUDED.value",
                ("lhb", code, date_val, trade_val,
                 json.dumps({"name": r["名称"], "net": float(r.get("龙虎榜净买额",0)),
                            "buy": float(r.get("龙虎榜买入额",0)),
                            "sell": float(r.get("龙虎榜卖出额",0))})),
                fetch=False
            )
            count += 1
        log.info(f"  ✅ 龙虎榜: {count} 条")
    except Exception as e:
        log.warning(f"  ⚠️ 龙虎榜失败: {e}")


def _update_hsgt():
    """采集沪深港通资金流向
    
    北向（沪股通/深股通）：2023年后东方财富不提供逐日净买卖，
    改用 stock_hsgt_fund_flow_summary_em 每日追加一笔。
    南向（港股通沪/深）：数据完整，每日增量追回。
    非交易日跳过（summary 返回空/0）。
    """
    import akshare as ak
    
    today = date.today()
    today_str = today.isoformat()
    
    # ==========================================
    # 南向资金（港股通）：全量逐日，可用
    # ==========================================
    for direction, symbol in [('south_sh', '港股通沪'), ('south_sz', '港股通深'), ('south_all', '南向资金')]:
        try:
            df = ak.stock_hsgt_hist_em(symbol=symbol)
            count = 0
            for _, r in df.iterrows():
                d = r["日期"]
                net_val = float(r["当日成交净买额"]) if pd.notna(r["当日成交净买额"]) else 0.0
                buy_amt = float(r["买入成交额"]) if pd.notna(r["买入成交额"]) else 0.0
                sell_amt = float(r["卖出成交额"]) if pd.notna(r["卖出成交额"]) else 0.0
                accum = float(r["历史累计净买额"]) if pd.notna(r["历史累计净买额"]) else 0.0
                inflow = float(r["当日资金流入"]) if pd.notna(r["当日资金流入"]) else 0.0
                _pg_execute(
                    "INSERT INTO market_signals (signal_type, code, trade_date, value, change_val, change_pct, extra) "
                    "VALUES (%s,%s,%s::date,%s,%s,%s,%s::jsonb) "
                    "ON CONFLICT (signal_type, trade_date, code) DO UPDATE SET value=EXCLUDED.value, change_val=EXCLUDED.change_val",
                    (f"hsgt_{direction}", "TOTAL", d, net_val, buy_amt, sell_amt,
                     json.dumps({"accum": accum, "inflow": inflow})),
                    fetch=False
                )
                count += 1
            log.info(f"  ✅ 南向{symbol}: {count} 天")
        except Exception as e:
            log.warning(f"  ⚠️ 南向{symbol}失败: {e}")
    
    # ==========================================
    # 北向资金（沪股通/深股通）：每日追加
    # 2023年后逐日净买卖停更，只取最新交易日
    # ==========================================
    try:
        df_summary = ak.stock_hsgt_fund_flow_summary_em()
        for _, r in df_summary.iterrows():
            direction_str = str(r["板块"])
            trade_date = str(r["交易日"])[:10]
            net_buy = float(r["成交净买额"]) if pd.notna(r["成交净买额"]) else 0.0
            inflow = float(r["资金净流入"]) if pd.notna(r["资金净流入"]) else 0.0
            balance = float(r["当日资金余额"]) if pd.notna(r["当日资金余额"]) else 0.0
            up = int(r["上涨数"]) if pd.notna(r["上涨数"]) else 0
            flat = int(r["持平数"]) if pd.notna(r["持平数"]) else 0
            down = int(r["下跌数"]) if pd.notna(r["下跌数"]) else 0
            
            # 映射板块名到 signal_type
            if direction_str == "沪股通":
                sig_type = "hsgt_north_sh"
            elif direction_str == "深股通":
                sig_type = "hsgt_north_sz"
            elif direction_str == "港股通(沪)":
                sig_type = "hsgt_south_sh"
            elif direction_str == "港股通(深)":
                sig_type = "hsgt_south_sz"
            else:
                continue
            
            _pg_execute(
                "INSERT INTO market_signals (signal_type, code, trade_date, value, change_val, change_pct, extra) "
                "VALUES (%s,%s,%s::date,%s,%s,%s,%s::jsonb) "
                "ON CONFLICT (signal_type, trade_date, code) DO UPDATE SET value=EXCLUDED.value, extra=EXCLUDED.extra",
                (sig_type, "TOTAL", trade_date, net_buy, inflow, balance,
                 json.dumps({"up": up, "flat": flat, "down": down, "source": "summary"})),
                fetch=False
            )
        log.info(f"  ✅ 北向当日追加: {len(df_summary)} 条 ({today_str})")
    except Exception as e:
        log.warning(f"  ⚠️ 北向当日追加失败: {e}")


# ============================================================
# 10. 智库 Pipeline 助手
# ============================================================
def get_pipeline_context(
    macro_indicators: List[str] = None,
    stocks: List[str] = None,
    crypto: List[str] = None,
    days: int = 365
) -> dict:
    """为智库 Pipeline 准备一次性数据快照"""
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=days)).isoformat()
    ctx = {}

    if macro_indicators:
        ctx["macro"] = {}
        for ind in macro_indicators:
            df = get_macro(ind, start, end)
            if not df.empty:
                ctx["macro"][ind] = {
                    "latest": float(df["value"].iloc[-1]),
                    "mean": float(df["value"].mean()),
                    "min": float(df["value"].min()),
                    "max": float(df["value"].max()),
                    "trend": "up" if df["value"].iloc[-1] > df["value"].iloc[0] else "down",
                }

    if stocks:
        ctx["stocks"] = {}
        for sym in stocks:
            df = get_stock_hist(sym, start, end)
            if not df.empty:
                ctx["stocks"][sym] = {
                    "latest_close": float(df["close"].iloc[-1]),
                    "change_pct": float((df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100),
                    "volatility": float(df["close"].pct_change().std() * 100),
                }

    if crypto:
        ctx["crypto"] = {}
        for sym in crypto:
            df = get_crypto(sym, start, end)
            if not df.empty:
                ctx["crypto"][sym] = {
                    "latest_price": float(df["price"].iloc[-1]),
                    "change_pct": float((df["price"].iloc[-1] / df["price"].iloc[0] - 1) * 100),
                }

    return ctx


# ============================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="金融数据采集与缓存层")
    parser.add_argument("--init-schema", action="store_true", help="初始化 PG 表结构")
    parser.add_argument("--update-all", action="store_true", help="全量更新所有数据源")
    parser.add_argument("--update-daily", action="store_true", help="增量更新（最近7天）")
    parser.add_argument("--scan-all", action="store_true", help="全市场行情扫描（~2分钟）")
    parser.add_argument("--init-stock-list", action="store_true", help="初始化股票列表")
    parser.add_argument("--backfill-hist", action="store_true", help="回填全量日线（首次，~30分钟，异步并发）")
    parser.add_argument("--init-etf", action="store_true", help="初始化 ETF 列表（同花顺源）")
    parser.add_argument("--scan-etf", action="store_true", help="ETF 全市场行情扫描")
    parser.add_argument("--backfill-etf", action="store_true", help="ETF 日线回填")
    parser.add_argument("--info", action="store_true", help="显示 PG 中已有数据统计")
    args = parser.parse_args()

    if args.init_schema:
        init_schema()
    elif args.update_all:
        init_schema()
        update_all()
    elif args.update_daily:
        init_schema()
        update_daily()
    elif args.init_stock_list:
        init_stock_list()
    elif args.scan_all:
        init_schema()
        init_stock_list()
        scan_all_stocks()
    elif args.backfill_hist:
        init_schema()
        backfill_hist_kline(start="20250101")
    elif args.init_etf:
        init_schema()
        init_etf_list()
    elif args.scan_etf:
        init_schema()
        scan_etf_market()
    elif args.backfill_etf:
        init_schema()
        backfill_etf_kline(start="20250101")
    elif args.info:
        for table in ["stock_list", "market_snapshots", "daily_prices", "macro_indicators", "crypto_metrics"]:
            rows, _ = _pg_execute(f"SELECT count(*) FROM {table}")
            log.info(f"  {table}: {rows[0][0]} rows")
    else:
        parser.print_help()
