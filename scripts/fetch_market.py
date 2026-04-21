#!/usr/bin/env python3
"""拉取市场数据（FRED + Finnhub）→ D盘"""
import json, time, csv, os
from pathlib import Path
import requests

OUT_DIR = Path("/mnt/d/data/conflict-data/market")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ===== FRED =====
FRED_API = "14876f904987b05adca701fb6f6e026d"
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# 关键指数series ID
FRED_SERIES = {
    "SP500": "SP500",           # S&P 500
    "DGS10": "DGS10",           # 10年期国债收益率
    "DEXUSEU": "DEXUSEU",       # 美元/欧元
    "DTWEXBGS": "DTWEXBGS",    # 美元指数（贸易加权）
    "VIXCLS": "VIXCLS",         # VIX恐慌指数
    "GOLDAMGBD228NLBM": "GOLDAMGBD228NLBM",  # 金价（伦敦）
    "DCOILBRENTEU": "DCOILBRENTEU",  # 布伦特原油
    "DEXCHUS": "DEXCHUS",       # 美元/人民币
    "NIKKEI225": "NIKKEI225",   # 日经225
    "IRLTLT01JPM156N": "IRLTLT01JPM156N",  # 日本10年国债
    "GFDEBTN": "GFDEBTN",       # 美国国债总额
    "CPIAUCSL": "CPIAUCSL",     # CPI
    "UNRATE": "UNRATE",         # 失业率
    "GDP": "GDP",               # GDP
}

def fetch_fred_series(series_id, start="2010-01-01"):
    params = {
        "series_id": series_id,
        "api_key": FRED_API,
        "file_type": "json",
        "observation_start": start,
        "observation_end": "2026-04-20",
        "frequency": "d",  # daily
    }
    r = requests.get(FRED_BASE, params=params, timeout=30)
    if r.status_code == 200:
        data = r.json()
        obs = data.get("observations", [])
        return obs
    print(f"  FRED ERROR {series_id}: {r.status_code}")
    return []

# ===== Finnhub =====
FINNHUB_API = "d709oh1r01qtb4ra0b50d709oh1r01qtb4ra0b5g"
FINNHUB_BASE = "https://finnhub.io/api/v1"

FINNHUB_INDICES = {
    "SPY": "^GSPC",
    "FXI": "FXI",       # 中国大盘ETF
    "EEM": "EEM",       # 新兴市场ETF
    "GLD": "GLD",       # 黄金ETF
    "TLT": "TLT",       # 20年国债ETF
    "USO": "USO",       # 原油ETF
    "VIX": "^VIX",
    "EWJ": "EWJ",       # 日本ETF
    "EWY": "EWY",       # 韩国ETF
    "EWA": "EWA",       # 澳大利亚ETF
}

def fetch_finnhub_candles(symbol, start="1577836800", end="1745068800"):
    """获取日K线（2020-01至今）"""
    params = {
        "symbol": symbol,
        "resolution": "D",
        "from": start,
        "to": end,
        "token": FINNHUB_API,
    }
    r = requests.get(f"{FINNHUB_BASE}/stock/candle", params=params, timeout=30)
    if r.status_code == 200:
        data = r.json()
        if data.get("s") == "ok":
            return data
    print(f"  Finnhub ERROR {symbol}: {r.status_code}")
    return None


if __name__ == "__main__":
    print("=" * 50)
    print("📊 市场数据拉取")
    print("=" * 50)

    # FRED
    print("\n【FRED 经济数据】")
    for name, sid in FRED_SERIES.items():
        fname = OUT_DIR / f"fred_{sid}.json"
        if fname.exists() and fname.stat().st_size > 500:
            print(f"  {name}({sid}): exists")
            continue
        print(f"  {name}({sid})...", end=" ", flush=True)
        obs = fetch_fred_series(sid)
        if obs:
            with open(fname, 'w') as f:
                json.dump(obs, f, ensure_ascii=False)
            print(f"✅ {len(obs)} obs")
        time.sleep(0.5)

    # Finnhub
    print("\n【Finnhub 市场指数】")
    for symbol, name in FINNHUB_INDICES.items():
        fname = OUT_DIR / f"finnhub_{symbol}.json"
        if fname.exists() and fname.stat().st_size > 500:
            print(f"  {symbol}: exists")
            continue
        print(f"  {symbol}...", end=" ", flush=True)
        data = fetch_finnhub_candles(symbol)
        if data:
            with open(fname, 'w') as f:
                json.dump(data, f, ensure_ascii=False)
            print(f"✅ {len(data.get('t',[]))} days")
        time.sleep(1)

    print(f"\n✅ 完成! 数据保存在 {OUT_DIR}")
