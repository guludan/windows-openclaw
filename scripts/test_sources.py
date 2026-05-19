#!/usr/bin/env python3
"""数据源可靠性测试 v2 — 区分代理/直连路径"""
import sys, json, time, os
from datetime import datetime
from pathlib import Path

results = {"timestamp": datetime.now().isoformat(), "sources": {}}

def log(name, status, detail="", elapsed=0):
    results["sources"][name] = {"status": status, "detail": detail[:200], "elapsed_s": round(elapsed, 2)}
    icon = {"ok": "✅", "partial": "⚠️", "fail": "❌"}.get(status, "?")
    print(f"  {icon} {name}: {status} ({elapsed:.1f}s) {detail[:100]}")

PROXY = "http://172.26.0.1:10808"
PROXY_DICT = {"http": PROXY, "https": PROXY}

def req_proxy(url, **kw):
    """走代理请求"""
    import requests
    kw.setdefault("timeout", 20)
    return requests.get(url, proxies=PROXY_DICT, **kw)

def req_direct(url, **kw):
    """直连请求"""
    import requests
    kw.setdefault("timeout", 20)
    return requests.get(url, **kw)

# ================================================================
# 1. yfinance — 代理路径
# ================================================================
def test_yfinance():
    t0 = time.time()
    try:
        import yfinance as yf
        import curl_cffi
        sess = curl_cffi.requests.Session()
        sess.proxies.update(PROXY_DICT)
        sp = yf.download("SPY", period="5d", progress=False, session=sess)
        if sp is not None and len(sp) >= 2:
            log("yfinance (SPY 美股)", "ok", f"close={sp['Close'].iloc[-1]:.2f}", time.time()-t0)
        else:
            log("yfinance (SPY 美股)", "fail", "not enough data", time.time()-t0)
    except Exception as e:
        log("yfinance (SPY 美股)", "fail", str(e)[:80], time.time()-t0)

def test_yfinance_extra():
    t0 = time.time()
    try:
        import yfinance as yf
        import curl_cffi
        sess = curl_cffi.requests.Session()
        sess.proxies.update(PROXY_DICT)
        ok = []
        for sym in ["^N225", "CL=F", "BTC-USD"]:
            df = yf.download(sym, period="5d", progress=False, session=sess)
            if df is not None and len(df) >= 2:
                ok.append(f"{sym}={df['Close'].iloc[-1]:.2f}")
            time.sleep(1)
        log("yfinance (日经/原油/BTC)", "ok" if ok else "fail", "; ".join(ok[:3]), time.time()-t0)
    except Exception as e:
        log("yfinance (日经/原油/BTC)", "fail", str(e)[:80], time.time()-t0)

def test_ovx():
    t0 = time.time()
    try:
        import yfinance as yf
        import curl_cffi
        sess = curl_cffi.requests.Session()
        sess.proxies.update(PROXY_DICT)
        ovx = yf.download("^OVX", period="5d", progress=False, session=sess)
        log("CBOE OVX (原油波动率)", "ok" if len(ovx)>=2 else "fail", f"close={ovx['Close'].iloc[-1]:.1f}" if len(ovx)>=1 else "nodata", time.time()-t0)
    except Exception as e:
        log("CBOE OVX (原油波动率)", "fail", str(e)[:80], time.time()-t0)

# ================================================================
# 2. AKShare — 直连
# ================================================================
def test_akshare_all():
    import akshare as ak

    t0 = time.time()
    try:
        df = ak.stock_zh_a_hist(symbol="000001", period="daily", start_date="20260501", end_date="20260518", adjust="")
        log("AKShare (A股日线)", "ok" if len(df)>0 else "fail", f"{len(df)} rows close={df['收盘'].iloc[-1]}", time.time()-t0)
    except Exception as e:
        log("AKShare (A股日线)", "fail", str(e)[:80], time.time()-t0)

    t0 = time.time()
    try:
        df = ak.macro_china_cpi_yearly()
        log("AKShare (中国CPI)", "ok" if len(df)>0 else "fail", f"{len(df)} rows", time.time()-t0)
    except Exception as e:
        log("AKShare (中国CPI)", "fail", str(e)[:80], time.time()-t0)

    t0 = time.time()
    try:
        import akshare as ak
        df = ak.futures_warehouse_receipt(symbol="sc")
        log("AKShare (原油仓单)", "ok" if len(df)>0 else "partial", f"{len(df)} rows", time.time()-t0)
    except Exception as e:
        log("AKShare (原油仓单)", "partial", f"API changed: {str(e)[:60]}", time.time()-t0)

    t0 = time.time()
    try:
        df = ak.stock_info_global_cls()
        log("AKShare (财联社快讯)", "ok" if len(df)>0 else "partial", f"{len(df)} items", time.time()-t0)
    except Exception as e:
        log("AKShare (财联社快讯)", "fail", str(e)[:80], time.time()-t0)

# ================================================================
# 3. FRED — 直连
# ================================================================
def test_fred():
    FRED_KEY = os.environ.get("FRED_API_KEY", "14876f904987b05adca701fb6f6e026d")

    t0 = time.time()
    try:
        r = req_direct("https://api.stlouisfed.org/fred/series/observations", params={
            "series_id": "DCOILBRENTEU", "api_key": FRED_KEY, "file_type": "json",
            "observation_start": "2026-04-01", "observation_end": "2026-05-18"
        })
        if r.status_code == 200:
            obs = r.json().get("observations", [])
            log("FRED (布伦特原油)", "ok" if obs else "partial", f"{len(obs)} obs", time.time()-t0)
        else:
            log("FRED (布伦特原油)", "fail", f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        log("FRED (布伦特原油)", "fail", str(e)[:80], time.time()-t0)

    t0 = time.time()
    ok_s = []
    for sid, name in {"SP500":"S&P","VIXCLS":"VIX","GDP":"GDP","CPIAUCSL":"CPI","UNRATE":"失业率"}.items():
        try:
            r = req_direct("https://api.stlouisfed.org/fred/series/observations", params={
                "series_id": sid, "api_key": FRED_KEY, "file_type": "json",
                "observation_start": "2026-01-01", "observation_end": "2026-05-18"
            })
            if r.status_code == 200 and len(r.json().get("observations",[])) > 0:
                ok_s.append(name)
            time.sleep(0.3)
        except: pass
    log("FRED (5指标)", "ok" if len(ok_s)==5 else "partial", f"{len(ok_s)}/5 ok: {','.join(ok_s)}", time.time()-t0)

# ================================================================
# 4. Finnhub — 直连
# ================================================================
def test_finnhub():
    t0 = time.time()
    KEY = os.environ.get("FINNHUB_API_KEY", "d709oh1r01qtb4ra0b50d709oh1r01qtb4ra0b5g")
    try:
        r = req_direct("https://finnhub.io/api/v1/stock/candle", params={
            "symbol":"SPY","resolution":"D","from":"20260501","to":"20260518","token":KEY
        })
        data = r.json()
        if r.status_code == 200 and data.get("s")=="ok":
            log("Finnhub (SPY日线)", "ok", f"{len(data['t'])}d close={data['c'][-1]:.2f}", time.time()-t0)
        else:
            log("Finnhub (SPY日线)", "fail", f"resp={data.get('s','?')}", time.time()-t0)
    except Exception as e:
        log("Finnhub (SPY日线)", "fail", str(e)[:80], time.time()-t0)

# ================================================================
# 5. CoinGecko — 直连（Yahoo blocked国内但 CoinGecko 可能也需要代理）
# ================================================================
def test_coingecko_proxy():
    t0 = time.time()
    try:
        r = req_proxy("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart", params={"vs_currency":"usd","days":"7"})
        if r.status_code == 200:
            prices = r.json().get("prices", [])
            log("CoinGecko (BTC 7d 代理)", "ok" if prices else "fail", f"{len(prices)}pts ${prices[-1][1]:.0f}", time.time()-t0)
        else:
            log("CoinGecko (BTC 7d 代理)", "fail", f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        log("CoinGecko (BTC 7d 代理)", "fail", str(e)[:80], time.time()-t0)

def test_coingecko_direct():
    t0 = time.time()
    try:
        r = req_direct("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart", params={"vs_currency":"usd","days":"7"})
        if r.status_code == 200:
            prices = r.json().get("prices", [])
            log("CoinGecko (BTC 7d 直连)", "ok" if prices else "fail", f"{len(prices)}pts ${prices[-1][1]:.0f}", time.time()-t0)
        else:
            log("CoinGecko (BTC 7d 直连)", "fail", f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        log("CoinGecko (BTC 7d 直连)", "fail", str(e)[:80], time.time()-t0)

# ================================================================
# 6. Coinglass — 需要 key/api 更新
# ================================================================
def test_coinglass():
    t0 = time.time()
    try:
        # Coinglass v3 API
        r = req_direct("https://open-api.coinglass.com/public/v2/liquidations", params={
            "symbol":"BTC","interval":"1d","limit":3
        }, headers={"accept":"application/json"})
        if r.status_code == 200:
            log("Coinglass (BTC清算)", "ok", f"HTTP200", time.time()-t0)
        else:
            log("Coinglass (BTC清算)", "partial", f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        log("Coinglass (BTC清算)", "fail", str(e)[:80], time.time()-t0)

# ================================================================
# 7. pytrends — 走代理
# ================================================================
def test_pytrends():
    t0 = time.time()
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl='en-US', tz=360, timeout=(10,15))
        # pytrends uses requests.Session internally, override via env
        os.environ["HTTP_PROXY"] = PROXY
        os.environ["HTTPS_PROXY"] = PROXY
        pt.build_payload(kw_list=["oil price"], timeframe='today 1-m')
        df = pt.interest_over_time()
        if len(df) > 0:
            log("pytrends (oil price)", "ok", f"latest={df['oil price'].iloc[-1]} {len(df)}d", time.time()-t0)
        else:
            log("pytrends (oil price)", "fail", "no data", time.time()-t0)
    except Exception as e:
        log("pytrends (oil price)", "fail", str(e)[:80], time.time()-t0)

# ================================================================
# 8. 冲突数据
# ================================================================
def test_ucdp():
    t0 = time.time()
    try:
        r = req_direct("https://ucdp.uu.se/geopackage/ucdp-ged", headers={"x-ucdp-access-token":"3704a6e6c39684d5"})
        log("UCDP (冲突事件)", "ok" if r.status_code==200 else "fail", f"HTTP {r.status_code} {len(r.content)//1024}KB", time.time()-t0)
    except Exception as e:
        log("UCDP (冲突事件)", "fail", str(e)[:80], time.time()-t0)

def test_acled():
    t0 = time.time()
    try:
        r = req_direct("https://api.acleddata.com/acled/read", params={
            "email":"jaccck1234567@gmail.com","password":"Thugstools123@","limit":3,"country":"Iran","year":"2026"
        })
        if r.status_code == 200:
            log("ACLED (伊朗冲突)", "ok", f"{r.json().get('count',0)} events", time.time()-t0)
        else:
            log("ACLED (伊朗冲突)", "fail", f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        log("ACLED (伊朗冲突)", "fail", str(e)[:80], time.time()-t0)

# ================================================================
# 9. ISDA SwapsInfo
# ================================================================
def test_swapsinfo():
    t0 = time.time()
    try:
        r = req_direct("https://www.swapsinfo.org/", timeout=15)
        log("ISDA SwapsInfo", "partial" if r.status_code<500 else "fail", f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        log("ISDA SwapsInfo", "fail", str(e)[:80], time.time()-t0)

# ================================================================
# 10. 搜索引擎 (data_search.py)
# ================================================================
def test_search():
    t0 = time.time()
    try:
        sys.path.insert(0, str(Path.home() / ".openclaw/workspace/skills/data-search/scripts"))
        os.environ["https_proxy"] = PROXY
        os.environ["http_proxy"] = PROXY
        from data_search import search_general
        r = search_general('"war risk premium" "Strait of Hormuz"', max_results=5)
        log("搜索引擎 (劳合社AWRP)", "ok" if r and len(r)>0 else "fail", f"{len(r) if r else 0} results", time.time()-t0)
    except Exception as e:
        log("搜索引擎 (劳合社AWRP)", "fail", str(e)[:80], time.time()-t0)

    t0 = time.time()
    try:
        from data_search import search_chinese
        r = search_chinese("美伊战争 油价 预测", max_results=5)
        log("搜索引擎 (中文 美伊油价)", "ok" if r and len(r)>0 else "fail", f"{len(r) if r else 0} results", time.time()-t0)
    except Exception as e:
        log("搜索引擎 (中文 美伊油价)", "fail", str(e)[:80], time.time()-t0)

# ================================================================
# 11. PostgreSQL
# ================================================================
def test_postgres():
    t0 = time.time()
    try:
        import psycopg2
        conn = psycopg2.connect(host='192.168.3.14', port=5432, user='guludan', password='toto', dbname='guludan', connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close(); conn.close()
        log("PostgreSQL (工控机)", "ok", "192.168.3.14:5432/guludan", time.time()-t0)
    except Exception as e:
        log("PostgreSQL (工控机)", "fail", str(e)[:80], time.time()-t0)

# ================================================================
if __name__ == "__main__":
    os.environ["HTTP_PROXY"] = PROXY
    os.environ["HTTPS_PROXY"] = PROXY

    print("=" * 60)
    print("📡 数据源可靠性测试 v2")
    print("=" * 60)
    start_all = time.time()

    print("\n--- 股票/指数/商品 ---")
    test_yfinance()
    test_yfinance_extra()

    print("\n--- 中国/全球宏观 ---")
    test_akshare_all()
    test_fred()

    print("\n--- 加密货币 ---")
    test_coingecko_proxy()
    test_coingecko_direct()
    test_coinglass()

    print("\n--- 情绪/另类 ---")
    test_pytrends()

    print("\n--- 地缘冲突 ---")
    test_ucdp()
    test_acled()

    print("\n--- 市场定价 ---")
    test_finnhub()
    test_ovx()
    test_swapsinfo()

    print("\n--- 搜索引擎 ---")
    test_search()

    print("\n--- 基础设施 ---")
    test_postgres()

    elapsed = time.time() - start_all
    ok = [k for k,v in results["sources"].items() if v["status"]=="ok"]
    partial = [k for k,v in results["sources"].items() if v["status"]=="partial"]
    fail = [k for k,v in results["sources"].items() if v["status"]=="fail"]

    print(f"\n{'='*60}")
    print(f"📊 汇总 ({elapsed:.0f}s)")
    print(f"✅ OK: {len(ok)}  ⚠️ Partial: {len(partial)}  ❌ Fail: {len(fail)}")
    print(f"\n✅ OK 列表:")
    for k in ok: print(f"   ✅ {k}")
    print(f"\n⚠️ Partial 列表:")
    for k in partial: print(f"   ⚠️ {k}")
    print(f"\n❌ Fail 列表:")
    for k in fail: print(f"   ❌ {k}")

    out = Path("/mnt/d/data/test_sources_result.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n📁 结果保存到 {out}")
