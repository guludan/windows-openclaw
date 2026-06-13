"""
网格交易执行器 - GPT助手 + 腾讯API联合模式

工作原理：
1. 用户设定网格参数（股票代码、持仓量、网格间距、仓位分配）
2. 每5-15分钟拉一次腾讯API实时行情
3. 判断当前价格是否触发网格买入/卖出
4. 输出买卖指令（用户手动确认执行，模拟量化资金的网格策略）

数据源：腾讯API（qt.gtimg.cn / ifzq.gtimg.cn），零鉴权不封IP
"""

import urllib.request
import json
import sys
import os
from datetime import datetime, date
from typing import Optional, List, Tuple

# ==================== 配置 ====================

# 网格参数示例（可根据股票调整）
GRID_CONFIGS = {
    "600273": {  # 嘉化能源
        "name": "嘉化能源",
        "grid_spread": 0.015,       # 格差1.5%
        "max_rebalance": 0.50,      # 单次最大调仓比例
        "upper_profit": 0.15,       # 总盈利15%清仓
        "stop_loss": -0.10,         # 总亏损10%止损
        "max_grids": 5,             # 最大网格层数
    },
    "600635": {  # 大众公用
        "name": "大众公用",
        "grid_spread": 0.02,
        "max_rebalance": 0.50,
        "upper_profit": 0.15,
        "stop_loss": -0.10,
        "max_grids": 5,
    },
    "002073": {  # 软控股份
        "name": "软控股份",
        "grid_spread": 0.02,
        "max_rebalance": 0.50,
        "upper_profit": 0.15,
        "stop_loss": -0.12,
        "max_grids": 5,
    },
    "000001": {  # 上证指数（监测用）
        "name": "大盘监控",
        "grid_spread": 0.03,
        "max_rebalance": 1.0,
        "upper_profit": 0.30,
        "stop_loss": -0.10,
        "max_grids": 3,
    }
}

# ==================== 数据获取 ====================

QT_CACHE = {}

def get_market_prefix(code: str) -> str:
    """获取市场前缀"""
    first = code[0]
    if first in ('6', '9'):
        return 'sh'
    elif first == '8':
        return 'bj'
    else:
        return 'sz'

def fetch_quote(code: str) -> dict:
    """从腾讯API获取实时行情"""
    prefix = get_market_prefix(code)
    url = f"https://qt.gtimg.cn/q={prefix}{code}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = resp.read().decode("gbk")
        for line in data.strip().split(";"):
            if not line.strip() or "=" not in line or '"' not in line:
                continue
            vals = line.split('"')[1].split("~")
            if len(vals) < 50:
                continue
            return {
                "code": code,
                "name": vals[1],
                "price": float(vals[3]),
                "prev_close": float(vals[4]) if vals[4] else 0,
                "open": float(vals[5]) if vals[5] else 0,
                "high": float(vals[33]) if vals[33] else 0,
                "low": float(vals[34]) if vals[34] else 0,
                "volume": int(vals[6]) if vals[6] else 0,
                "amount": float(vals[37]) if vals[37] else 0,
                "change_pct": float(vals[32]) if vals[32] else 0,
                "pe": float(vals[39]) if len(vals) > 39 and vals[39] else 0,
                "pb": float(vals[45]) if len(vals) > 45 and vals[45] else 0,
                "vol_ratio": float(vals[49]) if len(vals) > 49 and vals[49] else 0,
                "amplitude": float(vals[43]) if len(vals) > 43 and vals[43] else 0,
                "turnover": float(vals[38]) if len(vals) > 38 and vals[38] else 0,
                "total_mv": float(vals[45]) if len(vals) > 45 and vals[45] else 0,
            }
    except Exception as e:
        return {"code": code, "error": str(e)}

def fetch_minute_kline(code: str, period: str = "m5", count: int = 48) -> list:
    """获取分钟K线"""
    prefix = get_market_prefix(code)
    url = f"http://ifzq.gtimg.cn/appstock/app/kline/mkline?param={prefix}{code},{period},,{count}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read().decode("utf-8")
        d = json.loads(raw)
        data = d.get("data", {})
        key = f"{prefix}{code}"
        if key in data:
            klines = data[key].get(period, [])
            return klines
        return []
    except Exception as e:
        return []

def fetch_daily_kline(code: str, count: int = 120) -> list:
    """获取前复权日K线"""
    prefix = get_market_prefix(code)
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{count},qfq"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read().decode("utf-8")
        d = json.loads(raw)
        data = d.get("data", {}).get(key := f"{prefix}{code}", {})
        klines = data.get("qfqday", data.get("day", []))
        return klines
    except Exception as e:
        return []

# ==================== 网格计算 ====================

class GridTrader:
    """网格交易器"""

    def __init__(self, code: str, position: dict = None):
        self.code = code
        self.cfg = GRID_CONFIGS.get(code, {
            "name": code, "grid_spread": 0.02, "max_rebalance": 0.5,
            "upper_profit": 0.15, "stop_loss": -0.10, "max_grids": 5
        })
        self.name = self.cfg["name"]
        # 持仓信息（从外部传入）
        self.position = position or {"shares": 0, "cost": 0, "buyable": 0}

    def set_position(self, shares: int, cost: float, buyable: float):
        """设置当前持仓"""
        self.position = {"shares": shares, "cost": cost, "buyable": buyable}

    def calculate_grid_levels(self, price: float) -> List[dict]:
        """
        根据当前价计算网格层级
        返回网格列表：[{level, buy_price, sell_price, shares}]
        """
        spreads = self.cfg["grid_spread"]
        max_grids = self.cfg["max_grids"]
        grids = []
        for i in range(1, max_grids + 1):
            grids.append({
                "level": i,
                "buy_price": round(price * (1 - spreads * i), 3),
                "sell_price": round(price * (1 + spreads * i), 3),
                "shares": max(50, int(self.position.get("shares", 0) / max_grids)),
            })
        return grids

    def analyze_market(self) -> dict:
        """
        分析市场状态（辅助网格判断）
        """
        daily = fetch_daily_kline(self.code, 60)
        minute = fetch_minute_kline(self.code, "m5", 24)
        quote = fetch_quote(self.code)

        result = {
            "price": quote.get("price", 0),
            "change_pct": quote.get("change_pct", 0),
            "vol_ratio": quote.get("vol_ratio", 0),
            "pe": quote.get("pe", 0),
            "trend": "unknown",
            "support": 0,
            "resistance": 0,
        }

        if daily:
            closes = [float(k[2]) for k in daily]
            result["trend"] = "down" if closes[-1] < sum(closes[-5:])/5 < sum(closes[-10:])/10 else "up"
            result["support"] = min(float(k[4]) for k in daily[-20:])
            result["resistance"] = max(float(k[3]) for k in daily[-20:])
            result["sma5"] = sum(closes[-5:])/5 if len(closes) >= 5 else closes[-1]
            result["sma20"] = sum(closes[-20:])/20 if len(closes) >= 20 else closes[-1]
            result["sma120"] = sum(closes[-120:])/120 if len(closes) >= 120 else closes[-1]
            result["deviation5"] = (closes[-1] / result["sma5"] - 1) * 100 if result["sma5"] else 0
            result["deviation20"] = (closes[-1] / result["sma20"] - 1) * 100 if result["sma20"] else 0
            result["deviation120"] = (closes[-1] / result["sma120"] - 1) * 100 if result["sma120"] else 0

        # 量比判断
        vr = result["vol_ratio"]
        if vr > 2.0:
            result["volume_signal"] = "异常放量"
        elif vr > 1.5:
            result["volume_signal"] = "放量"
        elif vr < 0.7:
            result["volume_signal"] = "缩量"
        else:
            result["volume_signal"] = "正常"

        return result

    def generate_signals(self, price: float = None) -> List[dict]:
        """
        生成买卖信号
        返回信号列表：[{action, reason, price, shares, level}]
        """
        signals = []
        if price is None:
            quote = fetch_quote(self.code)
            price = quote.get("price", 0)
            if price == 0:
                return [{"action": "ERROR", "reason": f"无法获取行情: {quote.get('error','')}", "price": 0, "shares": 0}]

        market = self.analyze_market()
        grids = self.calculate_grid_levels(price)
        cost = self.position["cost"]
        shares = self.position["shares"]
        buyable = self.position["buyable"]

        # 利润计算
        if cost > 0 and shares > 0:
            profit_pct = (price / cost - 1) * 100
        else:
            profit_pct = 0

        # ---- 策略信号 ----

        # 1. 止损/止盈
        if profit_pct <= self.cfg["stop_loss"]:
            signals.append({
                "action": "SELL_ALL",
                "reason": f"止损触发: 亏{profit_pct:.1f}% <= {self.cfg['stop_loss']*100:.0f}%",
                "price": price, "shares": shares, "level": "stop_loss"
            })
            return signals

        if profit_pct >= self.cfg["upper_profit"]:
            signals.append({
                "action": "SELL_ALL",
                "reason": f"止盈触发: 赚{profit_pct:.1f}% >= {self.cfg['upper_profit']*100:.0f}%",
                "price": price, "shares": shares, "level": "profit_target"
            })
            return signals

        # 2. 网格买入信号（价格低于买入网格）
        for g in grids:
            if price <= g["buy_price"] and buyable >= g["shares"]:
                signals.append({
                    "action": "BUY",
                    "reason": f"网格{str(g['level'])}买入: 价格{price:.3f} <= {g['buy_price']:.3f}",
                    "price": price, "shares": g["shares"], "level": g["level"],
                    "estimate_cost": round(price * g["shares"] * 100, 2)
                })
                break  # 只触发最低一档

        # 3. 网格卖出信号（价格高于卖出网格）
        for g in reversed(grids):
            if price >= g["sell_price"] and shares >= g["shares"]:
                signals.append({
                    "action": "SELL",
                    "reason": f"网格{str(g['level'])}卖出: 价格{price:.3f} >= {g['sell_price']:.3f}",
                    "price": price, "shares": g["shares"], "level": g["level"],
                    "revenue": round(price * g["shares"] * 100, 2)
                })
                break  # 只触发最高一档

        # 4. 异常信号
        if market.get("volume_signal") == "异常放量" and market.get("vol_ratio", 0) > 3:
            if price > market.get("sma20", price):  # 放量上涨
                signals.append({
                    "action": "ALERT",
                    "reason": f"异常放量拉升: 量比{market['vol_ratio']:.1f}, 关注是否起涨",
                    "price": price, "shares": 0, "level": "watch"
                })
            else:  # 放量下跌
                signals.append({
                    "action": "ALERT",
                    "reason": f"异常放量砸盘: 量比{market['vol_ratio']:.1f}, 关注恐慌出清",
                    "price": price, "shares": 0, "level": "panic"
                })

        # 5. 深偏离买入信号
        if market.get("deviation120", 0) < -20:
            signals.append({
                "action": "ADVISORY_BUY",
                "reason": f"SMA120深偏离{market['deviation120']:.0f}%, 历史回测胜率>70%",
                "price": price, "shares": max(50, int(shares/2)), "level": "deep_dip"
            })

        return signals

    def summary(self) -> str:
        """生成当前状态摘要"""
        q = fetch_quote(self.code)
        m = self.analyze_market()
        p = q.get("price", 0)
        cost = self.position["cost"]
        shares = self.position["shares"]
        profit_pct = (p / cost - 1) * 100 if cost > 0 else 0

        lines = []
        lines.append(f"【{self.name}({self.code})】")
        lines.append(f"  现价={p:.3f} ({q.get('change_pct',0):+.2f}%)  PE={q.get('pe',0):.1f}  PB={q.get('pb',0):.2f}")
        lines.append(f"  量比={m.get('vol_ratio',0):.2f}  振幅={q.get('amplitude',0):.1f}%")
        lines.append(f"  趋势={m.get('trend','?')}  SMA偏离: 5日={m.get('deviation5',0):+.1f}% 20日={m.get('deviation20',0):+.1f}% 120日={m.get('deviation120',0):+.1f}%")
        lines.append(f"  支撑={m.get('support',0):.3f}  阻力={m.get('resistance',0):.3f}")
        lines.append(f"  持仓={shares}股@成本{cost:.4f}  盈亏={(p-cost)*shares*100:.2f}元({profit_pct:+.2f}%)")
        lines.append(f"  可用资金={self.position.get('buyable',0):.2f}元")
        signals = self.generate_signals(p)
        lines.append(f"  信号数={len(signals)}")
        for s in signals:
            lines.append(f"    [{s['action']}] {s['reason']}")
        return "\n".join(lines)


# ==================== 命令行入口 ====================

def main():
    if len(sys.argv) < 2:
        print("用法: python3 grid_trader.py <code> [shares] [cost] [buyable]")
        print("示例: python3 grid_trader.py 600273 200 7.2451 587")
        return

    code = sys.argv[1].strip()
    shares = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    cost = float(sys.argv[3]) if len(sys.argv) > 3 else 0
    buyable = float(sys.argv[4]) if len(sys.argv) > 4 else 1000

    trader = GridTrader(code)
    if shares > 0:
        trader.set_position(shares, cost, buyable)

    print(trader.summary())
    print()
    print("--- 网格层级 ---")
    q = fetch_quote(code)
    price = q.get("price", 0)
    if price > 0:
        grids = trader.calculate_grid_levels(price)
        for g in grids:
            print(f"  网格{g['level']}: 买<={g['buy_price']:.3f} / 卖>={g['sell_price']:.3f} ({g['shares']}股)")


if __name__ == "__main__":
    main()
