#!/usr/bin/env python3
"""
腾讯API分时/分钟数据拉取工具
- 今日分时: 每分钟一笔（242个点），含价格/成交量/成交额
- 历史分钟K线: 5min/15min/30min/60min
- 腾讯实时行情（PE/PB/市值/换手率/量比等）

数据源: ifzq.gtimg.cn (腾讯财经)
不封IP, 零鉴权
"""

import sys
import json
import re
import urllib.request
import urllib.error
from datetime import datetime, date, timedelta
from typing import Optional

def get_prefix(code: str) -> str:
    """6位代码 → 市场前缀 (sh/sz/bj)"""
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith("8"):
        return "bj"
    else:
        return "sz"


def fetch_today_intraday(code: str) -> dict:
    """
    拉取今日分时数据（每分钟一笔）。
    返回: {date, points: [{time, price, vol, amount}, ...], status}
    """
    prefix = get_prefix(code)
    url = f"http://ifzq.gtimg.cn/appstock/app/minute/query?code={prefix}{code}"
    
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read().decode("utf-8")
        d = json.loads(raw)
    except Exception as e:
        return {"status": "error", "msg": str(e)}
    
    data = d.get("data", {}).get(f"{prefix}{code}", {})
    minute_data = data.get("data", {})
    date_str = minute_data.get("date", "")
    points_raw = minute_data.get("data", [])
    
    points = []
    for p in points_raw:
        parts = p.strip().split()
        if len(parts) >= 4:
            time_str = parts[0]  # e.g. "0930"
            price = float(parts[1])
            vol = int(parts[2])  # 手
            amount = float(parts[3])  # 元
            points.append({
                "time": time_str,
                "price": price,
                "vol": vol,
                "amount": amount,
            })
    
    # 含昨收/均价信息
    qt = data.get("qt", {})
    qt_info = qt.get(f"{prefix}{code}", [])
    
    result = {
        "status": "ok",
        "code": code,
        "date": date_str,
        "points": points,
        "count": len(points),
    }
    
    # 从qt中提取关键信息
    if len(qt_info) > 50:
        result["last_close"] = float(qt_info[4]) if qt_info[4] else 0
        result["open"] = float(qt_info[5]) if qt_info[5] else 0
        result["price"] = float(qt_info[3]) if qt_info[3] else 0
        result["high"] = float(qt_info[33]) if qt_info[33] else 0
        result["low"] = float(qt_info[34]) if qt_info[34] else 0
        result["change_pct"] = float(qt_info[32]) if qt_info[32] else 0
        result["amount_wan"] = float(qt_info[37]) if qt_info[37] else 0  # 成交额(万)
        result["turnover_pct"] = float(qt_info[38]) if qt_info[38] else 0
        result["vol_ratio"] = float(qt_info[49]) if qt_info[49] else 0
        result["pe_ttm"] = float(qt_info[39]) if qt_info[39] else 0
        result["pb"] = float(qt_info[46]) if qt_info[46] else 0
        result["mcap_yi"] = float(qt_info[44]) if qt_info[44] else 0
        result["amplitude_pct"] = float(qt_info[43]) if qt_info[43] else 0
    
    return result


def fetch_intraday_history(code: str, trade_date: str) -> dict:
    """
    拉取指定日期的历史分时数据。
    腾讯API有date参数，但实际只返回最新数据。
    这里作为包装接口，返回信息和fetch_today_intraday一致。
    
    说明：腾讯API分时数据无法获取历史日期，
    历史分钟K线请用 fetch_minute_kline()
    """
    return fetch_today_intraday(code)


def fetch_minute_kline(code: str, freq: str = "m5", count: int = 240) -> dict:
    """
    拉取历史分钟K线数据。
    
    参数:
        code: 6位股票代码
        freq: m5(5分钟) / m15(15分钟) / m30(30分钟) / m60(60分钟)
        count: 最多条数（默认240）
    
    返回: {status, code, freq, klines: [{time, open, close, high, low, vol, amount}, ...]}
    """
    prefix = get_prefix(code)
    url = f"http://ifzq.gtimg.cn/appstock/app/kline/mkline?param={prefix}{code},{freq},,{count}"
    
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read().decode("utf-8")
        d = json.loads(raw)
    except Exception as e:
        return {"status": "error", "msg": str(e)}
    
    data = d.get("data", {}).get(f"{prefix}{code}", {})
    klines_raw = data.get(freq, [])
    
    # 分钟K线格式: [time, open, close, high, low, vol(手), {}, amt_ratio(成交额/流通市值)]
    # amt_ratio: 假设先存原始值，后续有流通市值信息时计算真实成交额
    klines = []
    for k in klines_raw:
        if len(k) >= 8:
            raw_amt = float(k[7]) if k[7] and str(k[7]).replace('.','').replace('-','').isdigit() else 0
            # 原始k[7] = 成交额(万) / float_mcap_yi(流通市值亿)
            # 真实成交额(万) = raw_amt * float_mcap_yi
            klines.append({
                "time": str(k[0]),
                "open": float(k[1]),
                "close": float(k[2]),
                "high": float(k[3]),
                "low": float(k[4]),
                "vol_shou": float(k[5]) if k[5] else 0,  # 手
                "amt_ratio": raw_amt if raw_amt else 0,  # 成交额(万)/流通市值(亿)
            })
    
    return {
        "status": "ok",
        "code": code,
        "freq": freq,
        "klines": klines,
        "count": len(klines),
    }


def fetch_daily_kline(code: str, count: int = 240) -> dict:
    """
    拉取日K线数据（前复权）。
    
    返回: {status, code, klines: [{date, open, close, high, low, vol}, ...]}
    """
    prefix = get_prefix(code)
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{count},qfq"
    
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read().decode("utf-8")
        d = json.loads(raw)
    except Exception as e:
        return {"status": "error", "msg": str(e)}
    
    data = d.get("data", {}).get(f"{prefix}{code}", {})
    # qfqday = 前复权日线, day = 未复权
    klines_raw = data.get("qfqday", data.get("day", []))
    
    klines = []
    for k in klines_raw:
        if len(k) >= 6:
            klines.append({
                "date": str(k[0]),
                "open": float(k[1]),
                "close": float(k[2]),
                "high": float(k[3]),
                "low": float(k[4]),
                "vol": float(k[5]),  # 手
            })
    
    return {
        "status": "ok",
        "code": code,
        "klines": klines,
        "count": len(klines),
    }


def fetch_tencent_quote(codes: list) -> dict:
    """
    腾讯实时行情（PE/PB/市值/换手率/涨跌停/量比等）。
    
    codes: ["600273", "002073", "600635"]
    返回: {code: {name, price, pe_ttm, pb, mcap_yi, ...}}
    """
    prefixed = []
    for c in codes:
        prefixed.append(f"{get_prefix(c)}{c}")
    
    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = resp.read().decode("gbk")
    except Exception as e:
        return {"status": "error", "msg": str(e)}
    
    result = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key[2:] if len(key) > 2 else key
        
        try:
            result[code] = {
                "name": vals[1],
                "price": float(vals[3]) if vals[3] else 0,
                "last_close": float(vals[4]) if vals[4] else 0,
                "open": float(vals[5]) if vals[5] else 0,
                "change_amt": float(vals[31]) if vals[31] else 0,
                "change_pct": float(vals[32]) if vals[32] else 0,
                "high": float(vals[33]) if vals[33] else 0,
                "low": float(vals[34]) if vals[34] else 0,
                "amount_wan": float(vals[37]) if vals[37] else 0,
                "turnover_pct": float(vals[38]) if vals[38] else 0,
                "pe_ttm": float(vals[39]) if vals[39] else 0,
                "amplitude_pct": float(vals[43]) if vals[43] else 0,
                "mcap_yi": float(vals[44]) if vals[44] else 0,
                "float_mcap_yi": float(vals[45]) if vals[45] else 0,
                "pb": float(vals[46]) if vals[46] else 0,
                "limit_up": float(vals[47]) if vals[47] else 0,
                "limit_down": float(vals[48]) if vals[48] else 0,
                "vol_ratio": float(vals[49]) if vals[49] else 0,
                "pe_static": float(vals[52]) if vals[52] else 0,
            }
        except (ValueError, IndexError):
            continue
    
    return result


def compute_intraday_stats(points: list, last_close: float = 0) -> dict:
    """
    从分时数据计算统计指标。
    
    返回:
    - avg_price: 均价（成交额/成交量）
    - vwap: VWAP均价
    - volume_total: 总成交量(手)
    - amount_total: 总成交额(元)
    - price_range: {high, low, range_pct}
    - volume_ratio_vs_avg: 当前量比（vs月均）
    """
    if not points:
        return {}
    
    total_vol = sum(p["vol"] for p in points)
    total_amt = sum(p["amount"] for p in points)
    
    prices = [p["price"] for p in points]
    
    stats = {
        "total_vol_shou": total_vol,
        "total_amt": total_amt,
        "avg_price": round(total_amt / total_vol / 100, 2) if total_vol else 0,
        "high": max(prices),
        "low": min(prices),
        "first_price": points[0]["price"],
        "last_price": points[-1]["price"],
        "range_pct": round((max(prices) - min(prices)) / points[0]["price"] * 100, 2),
    }
    
    if points[0]["time"] <= "0935":
        # 开盘密集成交区（前5分钟）的成交量占比
        open5min_vol = sum(p["vol"] for p in points if p["time"] <= "0935")
        stats["open5min_vol_pct"] = round(open5min_vol / total_vol * 100, 2) if total_vol else 0
    
    # 午休前后成交对比
    am_vol = sum(p["vol"] for p in points if p["time"] < "1130")
    pm_vol = sum(p["vol"] for p in points if p["time"] >= "1300")
    stats["am_vol"] = am_vol
    stats["pm_vol"] = pm_vol
    stats["am_pm_ratio"] = round(am_vol / pm_vol, 2) if pm_vol else 0
    
    return stats


def print_intraday(code: str):
    """CLI友好输出分时数据"""
    data = fetch_today_intraday(code)
    if data["status"] != "ok":
        print(f"❌ 拉取失败: {data.get('msg', 'unknown')}")
        return
    
    print(f"\n{'='*60}")
    print(f"📊 {code} 分时数据 — {data.get('date', '')}")
    print(f"  现价: {data.get('price', 'N/A')}  涨跌: {data.get('change_pct', 'N/A')}%")
    print(f"  量比: {data.get('vol_ratio', 'N/A')}  PE: {data.get('pe_ttm', 'N/A')}  PB: {data.get('pb', 'N/A')}")
    print(f"  总市值: {data.get('mcap_yi', 'N/A')}亿  振幅: {data.get('amplitude_pct', 'N/A')}%")
    print(f"{'='*60}")
    
    points = data.get("points", [])
    if not points:
        print("⚠️ 无分时数据（非交易时间/未开盘）")
        return
    
    stats = compute_intraday_stats(points, data.get("last_close", 0))
    
    print(f"\n📈 统计:")
    print(f"  成交量: {stats.get('total_vol_shou', 'N/A'):,} 手")
    print(f"  成交额: {stats.get('total_amt', 0)/1e4:,.0f} 万元")
    print(f"  均价: {stats.get('avg_price', 'N/A')}")
    print(f"  最高: {stats.get('high', 'N/A')}  最低: {stats.get('low', 'N/A')}")
    print(f"  日内振幅: {stats.get('range_pct', 'N/A')}%")
    print(f"  午前/午后量比: {stats.get('am_pm_ratio', 'N/A')}")
    
    # 分时段成交量分布
    print(f"\n📊 分时段成交:")
    segments = [("开盘", "0930", "1000"), ("上午", "1000", "1130"),
                ("午休成交", "1300", "1330"), ("下午", "1330", "1430"),
                ("尾盘", "1430", "1500")]
    for name, t_start, t_end in segments:
        vol = sum(p["vol"] for p in points if t_start <= p["time"] < t_end)
        pct = vol / stats["total_vol_shou"] * 100 if stats["total_vol_shou"] else 0
        print(f"  {name}({t_start}-{t_end}): {vol:,}手 ({pct:.1f}%)")
    
    # 大单成交量（单分钟>平均2倍）
    avg_min_vol = stats["total_vol_shou"] / len(points) if points else 0
    big_ticks = [p for p in points if p["vol"] > avg_min_vol * 2]
    if big_ticks:
        print(f"\n🔥 异常放量分钟 (>2x均值, {len(big_ticks)}笔):")
        for p in big_ticks[:10]:
            vs_avg = p["vol"] / avg_min_vol if avg_min_vol else 0
            print(f"  {p['time']}: {p['price']}元  {p['vol']:,}手 ({vs_avg:.1f}x均值)")
        if len(big_ticks) > 10:
            print(f"  ... 还有 {len(big_ticks) - 10} 笔")


def main():
    """命令行入口"""
    if len(sys.argv) < 2:
        print("用法: python3 tencent_intraday.py <股票代码> [--stats | --points | --quote | --m5 | --m15 | --daily]")
        print("示例:")
        print("  python3 tencent_intraday.py 600273          # 今日分时统计")
        print("  python3 tencent_intraday.py 600273 --points # 输出所有分时数据点(JSON)")
        print("  python3 tencent_intraday.py 600273 --quote  # 实时行情(PE/PB/量比)")
        print("  python3 tencent_intraday.py 600273 --m5     # 5分钟K线")
        print("  python3 tencent_intraday.py 600273 --m15    # 15分钟K线")
        print("  python3 tencent_intraday.py 600273 --daily  # 日K线")
        sys.exit(1)
    
    code = sys.argv[1].strip()
    if code.startswith(("sh", "sz", "bj", "SH", "SZ", "BJ")):
        code = code[2:]
    code = code.split(".")[0]  # remove .SH/.SZ suffix
    
    mode = "--stats"
    if len(sys.argv) >= 3:
        mode = sys.argv[2]
    
    if mode in ("--quote", "-q"):
        quotes = fetch_tencent_quote([code])
        if code in quotes:
            q = quotes[code]
            print(f"\n📊 {q['name']}({code}) 实时行情:")
            print(f"  现价: {q['price']}  涨跌: {q['change_pct']:+.2f}%")
            print(f"  昨收: {q['last_close']}  今开: {q['open']}")
            print(f"  最高: {q['high']}  最低: {q['low']}")
            print(f"  成交量额: {q['amount_wan']:.0f}万  换手: {q['turnover_pct']}%")
            print(f"  PE(TTM): {q['pe_ttm']}  PB: {q['pb']}")
            print(f"  市值: {q['mcap_yi']}亿  流通: {q['float_mcap_yi']}亿")
            print(f"  量比: {q['vol_ratio']}  振幅: {q['amplitude_pct']}%")
            print(f"  涨停: {q['limit_up']}  跌停: {q['limit_down']}")
        else:
            print(f"❌ 未找到 {code} 的行情数据")
    
    elif mode == "--points":
        data = fetch_today_intraday(code)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    
    elif mode in ("--m5", "--m15", "--m30", "--m60"):
        freq = mode[2:]  # m5, m15, m30, m60
        data = fetch_minute_kline(code, freq=freq, count=240)
        if data["status"] == "ok":
            # 顺便拉实时行情获取流通市值（用于amt_ratio→真实成交额换算）
            quotes = fetch_tencent_quote([code]) if mode != "--points" else {}
            float_mcap = quotes.get(code, {}).get("float_mcap_yi", 0)
            
            print(f"\n📊 {code} {freq}分钟K线 (最近{data['count']}条):")
            if float_mcap:
                print(f"   流通市值: {float_mcap:.1f}亿 | amt_ratio×流通市值=成交额(万)")
            for k in data["klines"][-30:]:
                pct = (k["close"] / k["open"] - 1) * 100 if k["open"] else 0
                amt_display = f"{k['amt_ratio']*float_mcap:.0f}万" if float_mcap else f"{k['amt_ratio']:.2f}(ratio)"
                print(f"  {k['time']}: O={k['open']} H={k['high']} L={k['low']} "
                      f"C={k['close']} {pct:+.2f}% 量={k['vol_shou']:.0f}手 额={amt_display}")
        else:
            print(f"❌ 失败: {data.get('msg')}")
    
    elif mode in ("--daily", "-d"):
        data = fetch_daily_kline(code, count=120)
        if data["status"] == "ok":
            print(f"\n📊 {code} 日K线 (最近{data['count']}条):")
            for k in data["klines"][-30:]:
                pct = (k["close"] / k["open"] - 1) * 100 if k["open"] else 0
                print(f"  {k['date']}: O={k['open']} H={k['high']} L={k['low']} "
                      f"C={k['close']} {pct:+.2f}% 量={k['vol']:.0f}")
        else:
            print(f"❌ 失败: {data.get('msg')}")
    
    else:
        print_intraday(code)


if __name__ == "__main__":
    main()
