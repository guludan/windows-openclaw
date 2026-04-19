#!/usr/bin/env python3
"""A股多数据源查询工具 — Yahoo Finance(主) + AKShare(备), 含缓存"""
import argparse, json, os, time, hashlib
import pandas as pd
import yfinance as yf

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
CACHE_TTL = {"quote": 60, "kline": 300, "financial": 3600}

def _ck(cmd, *args):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, hashlib.md5(f"{cmd}:{':'.join(str(a) for a in args)}".encode()).hexdigest() + ".json")

def _cg(key, ttl):
    if not os.path.exists(key): return None
    if time.time() - os.path.getmtime(key) > ttl: return None
    try: return json.load(open(key))
    except: return None

def _cs(key, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    open(key,"w").write(json.dumps(data, ensure_ascii=False, default=str))

def _norm(code):
    """600519 / 600519.SS / sh600519 → 600519"""
    c = code.upper().strip().lstrip("SH").lstrip("SZ").lstrip("SS").lstrip("SZ")
    return c.split(".")[0]

def _to_yf(code):
    nc = _norm(code)
    return f"{nc}.SS" if nc[0] in "65" else f"{nc}.SZ"

def _s(df, row_idx, col, ticker):
    """从MultiIndex DataFrame取标量"""
    if len(df) == 0: return None
    try:
        if isinstance(df.columns, pd.MultiIndex):
            return float(df.iloc[row_idx][(col, ticker)])
        return float(df.iloc[row_idx][col])
    except (KeyError, IndexError, TypeError):
        return None

# ========== NAME MAP ==========
NAMES = {
    "600519":"贵州茅台","000858":"五粮液","002594":"比亚迪","000001":"平安银行",
    "601318":"中国平安","600036":"招商银行","000333":"美的集团","002415":"海康威视",
    "601012":"隆基绿能","300750":"宁德时代","002714":"牧原股份","600900":"长江电力",
    "601899":"紫金矿业","000651":"格力电器","002475":"立讯精密","601398":"工商银行",
    "600276":"恒瑞医药","000568":"泸州老窖","002304":"洋河股份","600809":"山西汾酒",
    "002352":"顺丰控股","600887":"伊利股份","002142":"宁波银行","601166":"兴业银行",
}

# ========== quote ==========

def quote(codes):
    k = _ck("quote", *codes)
    c = _cg(k, CACHE_TTL["quote"])
    if c: print(json.dumps(c, indent=2, ensure_ascii=False)); return

    yf_codes = [_to_yf(c) for c in codes]
    data = yf.download(yf_codes, period="1d", progress=False)
    result = {}
    for i, code in enumerate(codes):
        yc = yf_codes[i]
        nc = _norm(code)
        price = _s(data, -1, "Close", yc)
        prev = _s(data, -2, "Close", yc) if len(data) > 1 else None
        vol = _s(data, -1, "Volume", yc)
        info = yf.Ticker(yc).info
        chg = round((price - prev) / prev * 100, 2) if (price and prev) else None
        result[code] = {
            "name": NAMES.get(nc, info.get("shortName", "N/A")),
            "price": round(price, 2) if price else None,
            "change_pct": chg,
            "volume": int(vol) if vol else None,
            "market_cap": info.get("marketCap"),
            "pe": info.get("trailingPE"),
            "pb": info.get("priceToBook"),
            "roe": info.get("returnOnEquity"),
            "dividend_yield": info.get("dividendYield"),
            "total_revenue": info.get("totalRevenue"),
        }
    _cs(k, result)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))

# ========== kline ==========

def kline(code, period="1mo"):
    k = _ck("kline", code, period)
    c = _cg(k, CACHE_TTL["kline"])
    if c: print(json.dumps(c, indent=2, ensure_ascii=False)); return

    # AKShare first (Chinese data more accurate for A-shares)
    out = None
    try:
        import akshare as ak
        nc = _norm(code)
        days_map = {"5d":10,"1mo":35,"3mo":100,"6mo":200,"1y":400,"5y":2000}
        end = pd.Timestamp.now()
        start = end - pd.Timedelta(days=days_map.get(period, 35))
        df = ak.stock_zh_a_hist(symbol=nc, period='daily',
                                start_date=start.strftime('%Y%m%d'),
                                end_date=end.strftime('%Y%m%d'), adjust='qfq')
        if not df.empty:
            out = [{"date":str(r["日期"]),"open":round(float(r["开盘"]),2),
                     "high":round(float(r["最高"]),2),"low":round(float(r["最低"]),2),
                     "close":round(float(r["收盘"]),2),"volume":int(r["成交量"])}
                    for _,r in df.iterrows()]
    except: pass

    if not out:
        yc = _to_yf(code)
        df = yf.Ticker(yc).history(period=period)
        out = [{"date":str(dt.date()),"open":round(float(r["Open"]),2),
                 "high":round(float(r["High"]),2),"low":round(float(r["Low"]),2),
                 "close":round(float(r["Close"]),2),"volume":int(r["Volume"])}
                for dt,r in df.iterrows()]

    _cs(k, out)
    print(json.dumps(out, indent=2, ensure_ascii=False))

# ========== financial ==========

def financial(code):
    k = _ck("financial", code)
    c = _cg(k, CACHE_TTL["financial"])
    if c: print(json.dumps(c, indent=2, ensure_ascii=False, default=str)); return

    yc = _to_yf(code)
    info = yf.Ticker(yc).info
    keys = ["shortName","sector","industry","marketCap","totalRevenue","revenueGrowth",
            "grossMargins","operatingMargins","profitMargins","returnOnEquity",
            "trailingPE","priceToBook","totalDebt","debtToEquity","freeCashflow",
            "dividendYield","earningsGrowth","bookValue","operatingCashflow","totalCash"]
    result = {kk: info.get(kk) for kk in keys}
    _cs(k, result)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))

# ========== CLI ==========

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="A股多数据源查询 (Yahoo+AKShare)")
    sub = p.add_subparsers(dest="cmd")
    q = sub.add_parser("quote"); q.add_argument("tickers", nargs="+")
    k = sub.add_parser("kline"); k.add_argument("ticker")
    k.add_argument("--period", default="1mo", choices=["5d","1mo","3mo","6mo","1y","5y"])
    f = sub.add_parser("financial"); f.add_argument("ticker")
    a = p.parse_args()
    if a.cmd == "quote": quote(a.tickers)
    elif a.cmd == "kline": kline(a.ticker, a.period)
    elif a.cmd == "financial": financial(a.ticker)
    else: p.print_help()
