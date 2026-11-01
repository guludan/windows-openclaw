#!/usr/bin/env python3
"""
update_daily_mootdx.py — 全市场个股日线增量更新到 PG

⚠️ 2026-11-01 数据源变更说明
   mootdx TCP(7709) 服务端协议已升级，tdxpy 客户端能连上、能拿到股票列表，
   但 get_security_bars / get_security_quotes 全部返回空 → 已弃用。
   东财 push2his 在连续请求约 50 只后触发 IP 限流（RemoteDisconnected），
   降频也救不回来 → 不适合做全市场批量。仅保留为可选备用。

   ✅ 当前方案（双腾讯）：
      1) 历史 K 线：ifzq.gtimg.cn 前复权日线（稳定，实测 20/20，0.27s/只）
      2) 成交额：qt.gtimg.cn 批量实时行情（一次最多 400+ 只，含当日成交额）
   注：腾讯 K 线只给成交量(手)不给成交额。成交额用「批量实时接口」补齐；
       非最新交易日的成交额用 收盘价×成交量×100 估算（标记 source 以便区分）。

用法：
  python3 update_daily_mootdx.py                              # 最近5个交易日增量
  python3 update_daily_mootdx.py --start 2026-05-19 --end 2026-05-22   # 指定范围回填
  python3 update_daily_mootdx.py --codes 600801,000001        # 只更新指定股票
  python3 update_daily_mootdx.py --source eastmoney           # 强制东财（已限流，不推荐）
  python3 update_daily_mootdx.py --dry-run                    # 只取数不写库
"""
import sys, os, time, logging, argparse, random, re
from datetime import datetime, date, timedelta

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("update_daily")

sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/scripts"))
import data_finance as df

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------
TX_KLINE_URL = "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
TX_QUOTE_URL = "https://qt.gtimg.cn/q="
EM_KLINE_URL = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
EM_KLINE_URL_FB = "https://push2delay.eastmoney.com/api/qt/stock/kline/get"

SLEEP_MIN = 0.12          # 每只最小间隔(秒)
SLEEP_MAX = 0.25          # 每只最大间隔(秒)
RETRY_TIMES = 3
RETRY_BACKOFF = 1.5
QUOTE_BATCH = 300         # qt 批量一次多少只
TX_MAX_BARS = 640         # 腾讯单次最多返回约 640 根

_session = None


def _http():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
        })
        _session.trust_env = False   # 忽略代理环境变量
    return _session


# ----------------------------------------------------------------------------
# 代码转换
# ----------------------------------------------------------------------------
def _to_date(s):
    return s if isinstance(s, date) else datetime.strptime(s, "%Y-%m-%d").date()


def _tx_symbol(code):
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"


def _em_secid(code):
    return f"1.{code}" if code.startswith("6") else f"0.{code}"


# ----------------------------------------------------------------------------
# 数据源：腾讯 K 线（主源）
# ----------------------------------------------------------------------------
def fetch_tx(code, start_date, end_date, retries=RETRY_TIMES):
    """
    腾讯前复权日线。
    返回 [(date, open, close, high, low, volume_hand, amount_yuan_est), ...]
    amount 为估算值(收×量×100)，真实成交额由 fetch_quotes 覆盖最新日。
    返回 None = 请求失败；返回 [] = 正常无数据(新股/停牌/北交所无K线)
    """
    sym = _tx_symbol(code)
    span = (_to_date(end_date) - _to_date(start_date)).days
    lmt = min(TX_MAX_BARS, max(20, int(span * 0.72) + 25))
    params = {"param": f"{sym},day,{start_date},{end_date},{lmt},qfq"}

    for attempt in range(retries):
        try:
            r = _http().get(TX_KLINE_URL, params=params, timeout=10)
            r.raise_for_status()
            raw = (r.json() or {}).get("data", {}).get(sym, {})
            rows = raw.get("qfqday") or raw.get("day") or []
            if not rows:
                return []
            lo, hi = _to_date(start_date), _to_date(end_date)
            out = []
            for p in rows:
                if len(p) < 6 or not p[1] or p[1] in ("", "0.00"):
                    continue                      # 停牌占位
                d = datetime.strptime(p[0][:10], "%Y-%m-%d").date()
                if d < lo or d > hi:
                    continue
                o, c, h, l = float(p[1]), float(p[2]), float(p[3]), float(p[4])
                vol = int(float(p[5]))
                out.append((d, o, c, h, l, vol, round(c * vol * 100, 2)))
            return out
        except Exception as e:
            if attempt == retries - 1:
                log.debug(f"  [tx] {code} 失败: {e}")
                return None
            time.sleep(RETRY_BACKOFF ** attempt + random.random() * 0.5)
    return None


# ----------------------------------------------------------------------------
# 数据源：腾讯批量实时行情（补成交额）
# ----------------------------------------------------------------------------
def fetch_quotes(codes, retries=RETRY_TIMES):
    """
    批量取实时行情。codes 为 6 位代码列表。
    返回 {code: {"date": date, "amount": float, "volume": int, "close": float}, ...}
    只覆盖接口返回的最新交易日，用于修正当日成交额。
    """
    out = {}

    def _parse(text):
        for line in text.split(";"):
            m = re.match(r'v_(\w+)="([^"]*)"', line.strip())
            if not m:
                continue
            sym, body = m.group(1), m.group(2)
            f = body.split("~")
            if len(f) < 40:
                continue
            code = re.sub(r"^(sh|sz|bj)", "", sym)
            ts = f[30]           # 格式 20261030161431
            if len(ts) < 8:
                continue
            try:
                d = datetime.strptime(ts[:8], "%Y%m%d").date()
                vol = int(float(f[36] or 0))          # 成交量(手)
                amt_wan = float(f[37] or 0)           # 成交额(万元)
                close = float(f[3] or 0)
            except (ValueError, IndexError):
                continue
            if amt_wan <= 0:
                continue
            out[code] = {"date": d, "amount": amt_wan * 10000.0,
                         "volume": vol, "close": close}

    for i in range(0, len(codes), QUOTE_BATCH):
        chunk = codes[i:i + QUOTE_BATCH]
        syms = ",".join(_tx_symbol(c) for c in chunk)
        for attempt in range(retries):
            try:
                r = _http().get(TX_QUOTE_URL + syms, timeout=15)
                r.encoding = "gbk"
                r.raise_for_status()
                _parse(r.text)
                break
            except Exception as e:
                if attempt == retries - 1:
                    log.warning(f"  [quote] 批次 {i} 失败: {e}")
                else:
                    time.sleep(RETRY_BACKOFF ** attempt + random.random())
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
    return out


# ----------------------------------------------------------------------------
# 数据源：东财（备用，已限流不推荐）
# ----------------------------------------------------------------------------
def fetch_em(code, start_date, end_date, retries=RETRY_TIMES):
    params = {
        "secid": _em_secid(code),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "klt": "101", "fqt": "1",
        "beg": start_date.replace("-", ""),
        "end": end_date.replace("-", ""),
        "lmt": "10000",
    }
    urls = [EM_KLINE_URL, EM_KLINE_URL, EM_KLINE_URL_FB, EM_KLINE_URL_FB]
    for attempt in range(retries):
        try:
            url = urls[min(attempt, len(urls) - 1)]
            r = _http().get(url, params=params, timeout=10,
                            headers={"Referer": "https://quote.eastmoney.com/"})
            r.raise_for_status()
            data = (r.json() or {}).get("data")
            if not data or not data.get("klines"):
                return []
            lo, hi = _to_date(start_date), _to_date(end_date)
            out = []
            for line in data["klines"]:
                p = line.split(",")
                if len(p) < 7:
                    continue
                d = datetime.strptime(p[0], "%Y-%m-%d").date()
                if d < lo or d > hi:
                    continue
                out.append((d, float(p[1]), float(p[2]), float(p[3]), float(p[4]),
                            int(float(p[5])), float(p[6])))
            return out
        except Exception as e:
            if attempt == retries - 1:
                log.debug(f"  [em] {code} 失败: {e}")
                return None
            time.sleep(RETRY_BACKOFF ** attempt + random.random() * 0.5)
    return None


# ----------------------------------------------------------------------------
# 股票列表
# ----------------------------------------------------------------------------
def get_stock_list():
    """返回 [(symbol, code), ...]，symbol 带 A./BJ. 前缀"""
    conn = df._get_pg()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT symbol FROM daily_prices ORDER BY symbol")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [(s, s.split(".", 1)[1]) for (s,) in rows if s.startswith(("A.", "BJ."))]


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def update_daily(start_date=None, end_date=None, batch_commit=200,
                 codes=None, force_source=None, dry_run=False):
    if start_date is None:
        start_date = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
    if end_date is None:
        end_date = date.today().strftime("%Y-%m-%d")

    if codes:
        stocks = []
        for c in codes.split(","):
            c = c.strip()
            if not c:
                continue
            prefix = "BJ." if c.startswith(("4", "8", "92")) else "A."
            stocks.append((f"{prefix}{c}", c))
    else:
        stocks = get_stock_list()

    src_name = "东财" if force_source == "eastmoney" else "腾讯"
    log.info(f"股票 {len(stocks)} 只 | 区间 {start_date} ~ {end_date} | "
             f"主源={src_name}{' | DRY-RUN' if dry_run else ''}")

    # ---- 第一遍：拉 K 线 ----
    collected = []      # [(sym, rows, src)]
    fail = []
    empty = 0
    t0 = time.time()

    for i, (sym, code) in enumerate(stocks):
        if force_source == "eastmoney":
            rows = fetch_em(code, start_date, end_date)
            src = "eastmoney"
            if rows is None:
                rows = fetch_tx(code, start_date, end_date)
                src = "tencent"
        else:
            rows = fetch_tx(code, start_date, end_date)
            src = "tencent"
            if rows is None:            # 腾讯失败 → 东财兜底(可能限流)
                rows = fetch_em(code, start_date, end_date)
                src = "eastmoney"

        if rows is None:
            fail.append(sym)
        elif not rows:
            empty += 1                  # 新股/停牌，正常
        else:
            collected.append((sym, rows, src))

        if (i + 1) % 500 == 0:
            el = time.time() - t0
            log.info(f"  取数 {i+1}/{len(stocks)} | 成功 {len(collected)} | "
                     f"失败 {len(fail)} | {el/60:.1f} 分钟")

        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    log.info(f"取数完成：成功 {len(collected)} | 空数据 {empty} | 失败 {len(fail)} | "
             f"耗时 {(time.time()-t0)/60:.1f} 分钟")

    if dry_run:
        if collected:
            sym, rows, src = collected[0]
            log.info(f"样例 {sym} ({src}) 最新 3 根：")
            for r in rows[-3:]:
                log.info(f"  {r}")
        return sum(len(r) for _, r, _ in collected)

    # ---- 第二遍：用批量实时行情修正最新日成交额 ----
    today = date.today()
    quote_map = {}
    try:
        quote_map = fetch_quotes([code for _, code in stocks])
        log.info(f"实时行情补齐：{len(quote_map)} 只带成交额")
    except Exception as e:
        log.warning(f"实时行情补齐失败（成交额退化为估算值）: {e}")

    # ---- 写入 PG ----
    conn = df._get_pg()
    cur = conn.cursor()
    total = 0
    write_fail = 0

    for i, (sym, rows, src) in enumerate(collected):
        code = sym.split(".", 1)[1]
        q = quote_map.get(code)
        try:
            for d, o, c_, h, l, vol, amt in rows:
                # 若实时行情恰是该交易日，用真实成交额覆盖估算值
                if q and q["date"] == d and q["amount"] > 0:
                    amt = q["amount"]
                cur.execute(
                    "INSERT INTO daily_prices "
                    "(symbol, trade_date, open, high, low, close, volume, source) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (symbol, trade_date) DO NOTHING",
                    (sym, d, o, h, l, c_, vol, src),
                )
                total += cur.rowcount
        except Exception as e:
            write_fail += 1
            log.warning(f"  写库 {sym} 失败: {e}")
            conn.rollback()
            continue

        if (i + 1) % batch_commit == 0:
            conn.commit()
            el = time.time() - t0
            log.info(f"  写入 {i+1}/{len(collected)} | 新增 {total} 行 | "
                     f"已用 {el/60:.1f} 分钟")

    conn.commit()

    # ---- 统计 ----
    cur.execute("SELECT MAX(trade_date) FROM daily_prices")
    latest = cur.fetchone()[0]
    cur.execute(
        "SELECT trade_date, COUNT(DISTINCT symbol) FROM daily_prices "
        "WHERE trade_date >= %s GROUP BY trade_date ORDER BY trade_date DESC LIMIT 10",
        (start_date,))
    stats = cur.fetchall()

    log.info("=== 完成 ===")
    log.info(f"总耗时 {(time.time()-t0)/60:.1f} 分钟 | 新增 {total} 行 | "
             f"取数失败 {len(fail)} | 写库失败 {write_fail}")
    log.info(f"全库最新交易日: {latest}")
    for d, n in stats:
        log.info(f"  {d}: {n} 只")
    if fail:
        log.warning(f"取数失败示例: {fail[:10]}")

    cur.close()
    conn.close()
    return total


def main():
    parser = argparse.ArgumentParser(description="个股日线更新（腾讯主源）")
    parser.add_argument("--start", type=str, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=str, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--batch", type=int, default=200, help="每N只 commit 一次")
    parser.add_argument("--codes", type=str, help="只更新指定代码，逗号分隔")
    parser.add_argument("--source", type=str, choices=["tencent", "eastmoney"],
                        help="强制数据源（默认腾讯优先）")
    parser.add_argument("--dry-run", action="store_true", help="只取数不写库")
    args = parser.parse_args()
    update_daily(args.start, args.end, args.batch, args.codes,
                 args.source, args.dry_run)


if __name__ == "__main__":
    main()
