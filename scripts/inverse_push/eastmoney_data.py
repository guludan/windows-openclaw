#!/usr/bin/env python3
"""
东财数据层（替代akshare，直接调push2/datacenter API）
- 行业板块排名：push2.eastmoney.com/api/qt/clist/get
- 全市场龙虎榜：datacenter.eastmoney.com/api/data/v1/get
- 分钟级资金流：push2.eastmoney.com/api/qt/stock/fflow/kline/get
- 个股新闻：search-api-web.eastmoney.com/search/jsonp
- 财联社快讯：cls.cn/nodeapi/telegraphList
- 东财全球资讯：np-weblist.eastmoney.com/comm/web/getFastNewsList
- 北向资金：data.hexin.cn/market/hsgtApi/method/dayChart/
"""
import os, time, json, re
import requests
from datetime import datetime
from pathlib import Path
import uuid as _uuid
import pandas as pd

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_BASE_HEADERS = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}

# 100个东财行业→申万31行业的映射（人工标注+自动补全）
INDUSTRY_MAP_THS2SW = {
    "半导体": "电子", "电子": "电子", "元件": "电子", "光学光电子": "电子",
    "电子化学品": "电子", "消费电子": "电子", "其他电子": "电子",
    "计算机应用": "计算机", "计算机设备": "计算机", "软件开发": "计算机",
    "IT服务": "计算机", "通信服务": "通信", "通信设备": "通信", 
    "传媒": "传媒", "文化传媒": "传媒", "游戏": "传媒", "广告传媒": "传媒",
    "汽车零部件": "汽车", "汽车整车": "汽车", "汽车服务": "汽车",
    "电力设备": "电力设备", "电网设备": "电力设备", "风电设备": "电力设备",
    "光伏设备": "电力设备", "电池": "电力设备",
    "化学制品": "基础化工", "化学原料": "基础化工", "化学纤维": "基础化工",
    "塑料": "基础化工", "橡胶": "基础化工", "农化制品": "基础化工",
    "橡胶制品": "基础化工", "石油加工贸易": "石油石化", "油服工程": "石油石化",
    "煤炭开采加工": "煤炭", "钢铁": "钢铁", "工业金属": "有色金属",
    "贵金属": "有色金属", "小金属": "有色金属", "金属新材料": "有色金属",
    "建筑装饰": "建筑装饰", "建筑建材": "建筑装饰", "水泥": "建筑材料",
    "玻璃玻纤": "建筑材料", "装修装饰": "建筑装饰", "工程咨询服务": "建筑装饰",
    "通用设备": "机械设备", "专用设备": "机械设备", "自动化设备": "机械设备",
    "仪器仪表": "机械设备", "工程机械": "机械设备", "金属制品": "机械设备",
    "军工装备": "国防军工", "军工电子": "国防军工", "航空装备": "国防军工",
    "航海装备": "国防军工", "地面装备": "国防军工",
    "中药": "医药生物", "化学制药": "医药生物", "生物制品": "医药生物",
    "医疗器械": "医药生物", "医药商业": "医药生物", "医疗服务": "医药生物",
    "食品加工制造": "食品饮料", "饮料制造": "食品饮料", "食品饮料": "食品饮料",
    "养殖业": "农林牧渔", "种植业": "农林牧渔", "饲料": "农林牧渔",
    "农产品加工": "农林牧渔", "渔业": "农林牧渔", "农业服务": "农林牧渔",
    "银行": "银行", "证券": "非银金融", "保险": "非银金融", 
    "多元金融": "非银金融", "期货": "非银金融",
    "房地产": "房地产", "房地产开发": "房地产", "房地产服务": "房地产",
    "机场航运": "交通运输", "港口航运": "交通运输", "铁路公路": "交通运输",
    "物流": "交通运输", "公路铁路": "交通运输",
    "零售": "商贸零售", "贸易": "商贸零售", "互联网电商": "商贸零售",
    "饮料乳品": "食品饮料", "调味品": "食品饮料", "休闲食品": "食品饮料",
    "服装家纺": "纺织服饰", "纺织制造": "纺织服饰",
    "白色家电": "家用电器", "黑色家电": "家用电器", "厨卫电器": "家用电器",
    "小家电": "家用电器", "照明设备": "家用电器",
    "环保": "环保", "环保设备": "环保", "环境治理": "环保",
    "造纸": "轻工制造", "包装印刷": "轻工制造", "文娱用品": "轻工制造",
    "家用轻工": "轻工制造", "教育": "社会服务", "酒店餐饮": "社会服务",
    "旅游": "社会服务", "体育": "社会服务", "专业服务": "社会服务",
    "美容护理": "美容护理", "化妆品": "美容护理", "个护用品": "美容护理",
    "电力": "公用事业", "燃气": "公用事业", "水务": "公用事业",
    "公用事业": "公用事业",
    "航空机场": "交通运输", "航海运输": "交通运输",
    "房屋建设": "建筑装饰", "基础建设": "建筑装饰", "专业工程": "建筑装饰",
}

# 缺失映射的默认回退
_DEFAULT_SW = "综合"


def _clean_proxy():
    """清代理环境变量"""
    for k in list(os.environ.keys()):
        if 'proxy' in k.lower() or 'PROXY' in k:
            os.environ.pop(k, None)


def _req(url, params=None, timeout=10):
    """带重试的JSON GET请求"""
    _clean_proxy()
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=_BASE_HEADERS, timeout=timeout)
            if r.status_code != 200:
                time.sleep(1)
                continue
            return r.json()
        except Exception:
            time.sleep(2)
    return None


def _req_jsonp(url, params=None, timeout=10):
    """带重试的JSONP GET请求，自动剥离函数包裹"""
    _clean_proxy()
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=_BASE_HEADERS, timeout=timeout)
            if r.status_code != 200:
                time.sleep(1)
                continue
            text = r.text
            idx = text.find('(')
            if idx < 0:
                return None
            json_str = text[idx + 1 : text.rindex(')')]
            return json.loads(json_str)
        except Exception:
            time.sleep(2)
    return None


# ═══════════════════════════════════════════════
# 1. 行业板块排名（100个东财行业）
# ═══════════════════════════════════════════════

def industry_comparison(top_n: int = 100) -> dict:
    """
    全行业涨跌幅排名（东财行业板块，~100个行业）
    返回: {top: [...], bottom: [...], total: int}
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1",
        "fltt": "2", "invt": "2",
        "fs": "m:90+t:2",
        "fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207",
    }
    d = _req(url, params)
    if not d:
        return {"top": [], "bottom": [], "total": 0}
    
    items = d.get("data", {}).get("diff", [])
    if not items:
        return {"top": [], "bottom": [], "total": 0}
    
    rows = []
    for i, item in enumerate(items):
        rows.append({
            "rank": i + 1,
            "name": item.get("f14", ""),
            "change_pct": item.get("f3", 0),
            "code": item.get("f12", ""),
            "up_count": item.get("f104", 0),
            "down_count": item.get("f105", 0),
            "leader": item.get("f140", ""),
            "leader_change": item.get("f136", 0),
            # 基础的资金流字段
            "inflow": item.get("f62", 0),    # 主力流入
            "outflow": item.get("f72", 0),   # 主力流出
            "net_inflow": item.get("f66", 0),# 主力净流入
        })
    
    return {
        "top": rows[:top_n],
        "bottom": rows[-top_n:],
        "total": len(rows),
    }


# ═══════════════════════════════════════════════
# 2. 全市场龙虎榜
# ═══════════════════════════════════════════════

def daily_dragon_tiger(trade_date: str = None, min_net_buy: float = None) -> dict:
    """
    每日全市场龙虎榜汇总
    trade_date: YYYY-MM-DD（默认当日）
    min_net_buy: 净买入下限（万元），None 不过滤
    返回: {date, total_records, stocks: [{...}]}
    """
    from datetime import datetime
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    
    url = "https://datacenter.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
        "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,EXPLANATION,EXPLAIN,CLOSE_PRICE,CHANGE_RATE,BILLBOARD_NET_AMT,BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,TURNOVERRATE,DEAL_NET_RATIO,FREE_MARKET_CAP,DEAL_AMOUNT_RATIO,BUY_RATIO,SELL_RATIO,ACCUM_AMOUNT",
        "filter": f"(TRADE_DATE>='{trade_date}')(TRADE_DATE<='{trade_date}')",
        "pageNumber": 1, "pageSize": 500,
        "sortTypes": -1, "sortColumns": "BILLBOARD_NET_AMT",
    }
    d = _req(url, params)
    if not d or not isinstance(d, dict):
        return {"date": trade_date, "total_records": 0, "stocks": [],
                "note": "API请求失败"}
    
    result_data = d.get("result", {})
    if not isinstance(result_data, dict):
        return {"date": trade_date, "total_records": 0, "stocks": [],
                "note": "API返回格式异常"}
    
    items = result_data.get("data", [])
    if not items:
        return {"date": trade_date, "total_records": 0, "stocks": [],
                "note": "无数据（非交易日或盘后未更新）"}
    
    stocks = []
    for row in items:
        net_buy = (row.get("BILLBOARD_NET_AMT") or 0) / 10000
        if min_net_buy is not None and net_buy < min_net_buy:
            continue
        
        chg = round(float(row.get("CHANGE_RATE") or 0), 2)
        
        stocks.append({
            "code": row.get("SECURITY_CODE", ""),
            "name": row.get("SECURITY_NAME_ABBR", ""),
            "reason": row.get("EXPLANATION", ""),
            "explain": row.get("EXPLAIN", ""),
            "close": row.get("CLOSE_PRICE") or 0,
            "change_pct": chg,
            "is_limit_up": chg >= 9.5,
            "net_buy_wan": round(net_buy, 1),
            "buy_wan": round((row.get("BILLBOARD_BUY_AMT") or 0) / 10000, 1),
            "sell_wan": round((row.get("BILLBOARD_SELL_AMT") or 0) / 10000, 1),
            "net_ratio": row.get("DEAL_NET_RATIO") or 0,
            "turnover_pct": round(float(row.get("TURNOVERRATE") or 0), 2),
            "buy_ratio": row.get("BUY_RATIO") or 0,
            "sell_ratio": row.get("SELL_RATIO") or 0,
            "accum_amount": row.get("ACCUM_AMOUNT") or 0,
        })
    
    return {"date": trade_date, "total_records": len(stocks), "stocks": stocks}


# ═══════════════════════════════════════════════
# 3. 龙虎榜机构买卖明细（datacenter）
# ═══════════════════════════════════════════════

def lhb_inst_detail(trade_date: str = None) -> list:
    """
    龙虎榜机构买卖明细（回退到akshare，因datacenter无机构明细reportName）
    trade_date: YYYY-MM-DD
    返回: [{code, name, inst_buy, inst_sell, inst_net, inst_buyers, inst_sellers, inst_net_ratio}]
    """
    if trade_date is None:
        from datetime import datetime
        trade_date = datetime.now().strftime("%Y-%m-%d")
    
    # 从datacenter龙虎榜明细中提取机构特征（BUY/SELL RATIO可辅助判断）
    # 但精确机构信息仍需akshare的stock_lhb_jgmmtj_em
    return _akshare_inst_detail(trade_date)


def _akshare_inst_detail(trade_date_str):
    """akshare机构买卖明细（stock_lhb_jgmmtj_em）"""
    try:
        import akshare as ak
        ds = trade_date_str.replace("-", "")
        df = ak.stock_lhb_jgmmtj_em(start_date=ds, end_date=ds)
        if df is None or len(df) == 0:
            return []
        
        result = []
        for _, row in df.iterrows():
            result.append({
                "code": str(row.get("代码", "")).strip(),
                "name": str(row.get("名称", "")).strip(),
                "inst_buy": float(row.get("机构买入总额", 0) or 0),
                "inst_sell": float(row.get("机构卖出总额", 0) or 0),
                "inst_net": float(row.get("机构买入净额", 0) or 0),
                "inst_buyers": int(row.get("买方机构数", 0) or 0),
                "inst_sellers": int(row.get("卖方机构数", 0) or 0),
                "inst_net_ratio": float(row.get("机构净买额占总成交额比", 0) or 0),
            })
        return result
    except Exception as e:
        print(f"    [WARN] akshare机构明细失败: {str(e)[:50]}")
        return []


# ═══════════════════════════════════════════════
# 4. 分钟级个股资金流（盘中）
# ═══════════════════════════════════════════════

def fund_flow_minute(code: str) -> list[dict]:
    """
    个股资金流向（分钟级，当日盘中）
    code: 6位股票代码
    返回: [{time, main_net, small_net, mid_net, large_net, super_net}, ...]
    单位: 元
    """
    secid = f"1.{code}" if code.startswith("6") else f"0.{code}"
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
    params = {
        "secid": secid, "klt": 1,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }
    d = _req(url, params)
    if not d:
        return []
    
    klines = d.get("data", {}).get("klines", [])
    rows = []
    for line in klines:
        parts = line.split(",")
        if len(parts) >= 7:
            rows.append({
                "time": parts[0],
                "main_net": float(parts[1]),
                "small_net": float(parts[2]),
                "mid_net": float(parts[3]),
                "large_net": float(parts[4]),
                "super_net": float(parts[5]),
            })
    return rows


# ═══════════════════════════════════════════════
# 5. 东财行业→申万行业映射
# ═══════════════════════════════════════════════

def map_to_sw_industry(ths_name: str) -> str:
    """同花顺/东财行业名 → 申万一级行业"""
    return INDUSTRY_MAP_THS2SW.get(ths_name, _DEFAULT_SW)


def get_industry_by_code(code: str) -> str:
    """从industry_components_sw获取申万一级行业（PG）"""
    import sys
    sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
    import data_finance as df
    r,_ = df._pg_execute(
        "SELECT industry_name FROM industry_components_sw WHERE stock_code LIKE %s",
        (code[:6] + '%',)
    )
    return r[0][0] if r else None


# ═══════════════════════════════════════════════
# 6. 获取当日CSI行业基金流（快速）
# ═══════════════════════════════════════════════

def get_industry_fund_flow() -> list[dict]:
    """
    从东财行业板块排名获取当日行业资金流
    返回: [{industry_sw, name, change_pct, net_inflow, up_count, down_count}, ...]
    """
    comp = industry_comparison(100)
    results = []
    for r in comp["top"]:
        sw = map_to_sw_industry(r["name"])
        results.append({
            "industry_sw": sw,
            "name": r["name"],
            "change_pct": r["change_pct"],
            "net_inflow": r.get("net_inflow", 0),
            "up_count": r["up_count"],
            "down_count": r["down_count"],
            "leader": r["leader"],
            "leader_change": r["leader_change"],
        })
    return results


# ═══════════════════════════════════════════════
# 5. 东财个股新闻
# ═══════════════════════════════════════════════

def stock_news(code: str, page_size: int = 20) -> list[dict]:
    """
    东财个股新闻（JSONP 接口）
    code: 股票代码 或 关键词
    返回: [{title, content, time, source, url}]
    """
    cb = "jQuery_news"
    inner = json.dumps({
        "uid": "", "keyword": code,
        "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
            "pageIndex": 1, "pageSize": page_size, "preTag": "", "postTag": ""}},
    }, separators=(',', ':'))
    
    d = _req_jsonp("https://search-api-web.eastmoney.com/search/jsonp",
                    params={"cb": cb, "param": inner},
                    timeout=10)
    if not d or not isinstance(d, dict):
        return []
    
    articles = d.get("result", {}).get("cmsArticleWebOld", [])
    rows = []
    for a in articles:
        rows.append({
            "title": re.sub(r'<[^>]+>', '', a.get("title", "")),
            "content": re.sub(r'<[^>]+>', '', a.get("content", ""))[:300],
            "time": a.get("date", ""),
            "source": a.get("mediaName", ""),
            "url": a.get("url", ""),
        })
    return rows


# ═══════════════════════════════════════════════
# 6. 财联社快讯
# ═══════════════════════════════════════════════

def cls_telegraph(page_size: int = 50) -> list[dict]:
    """
    财联社电报（全市场实时快讯）
    返回: [{title, content, time}]
    """
    d = _req("https://www.cls.cn/nodeapi/telegraphList",
             params={"rn": str(page_size), "page": "1"},
             timeout=10)
    if not d:
        return []
    
    items = d.get("data", {}).get("roll_data", [])
    rows = []
    for item in items:
        ts = item.get("ctime", 0)
        if isinstance(ts, int):
            tstr = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        elif isinstance(ts, str):
            tstr = ts[:16]
        else:
            tstr = str(ts)[:16]
        
        title = item.get("title", "") or item.get("brief", "")
        content = item.get("content", "") or item.get("brief", "")
        
        rows.append({
            "title": title,
            "content": re.sub(r'<[^>]+>', '', content)[:500],
            "time": tstr,
        })
    return rows


# ═══════════════════════════════════════════════
# 7. 东财全球资讯（7x24）
# ═══════════════════════════════════════════════

def eastmoney_global_news(page_size: int = 50) -> list[dict]:
    """
    东方财富全球财经资讯（7x24 滚动）
    返回: [{title, summary, time}]
    """
    d = _req("https://np-weblist.eastmoney.com/comm/web/getFastNewsList",
             params={
                 "client": "web", "biz": "web_724",
                 "fastColumn": "102", "sortEnd": "",
                 "pageSize": str(page_size),
                 "req_trace": str(_uuid.uuid4()),
             },
             timeout=10)
    if not d:
        return []
    
    items = d.get("data", {}).get("fastNewsList", [])
    rows = []
    for item in items:
        rows.append({
            "title": item.get("title", ""),
            "summary": item.get("summary", "")[:200],
            "time": item.get("showTime", ""),
        })
    return rows


# ═══════════════════════════════════════════════
# 8. 北向资金（同花顺 hsgtApi）
# ═══════════════════════════════════════════════

def hsgt_realtime() -> list[dict]:
    """
    沪深股通当日实时分钟流向（含集合竞价 09:10–15:00）
    返回: [{time, hgt_yi, sgt_yi}, ...]
    单位: 亿元
    """
    r = requests.get("https://data.hexin.cn/market/hsgtApi/method/dayChart/",
                     headers={"User-Agent": UA}, timeout=10)
    try:
        d = r.json()
    except:
        return []
    
    times = d.get("time", [])
    hgt = d.get("hgt", [])
    sgt = d.get("sgt", [])
    n = min(len(times), len(hgt), len(sgt))
    
    rows = []
    for i in range(n):
        rows.append({
            "time": times[i],
            "hgt_yi": float(hgt[i]),
            "sgt_yi": float(sgt[i]),
        })
    return rows


def northbound_cache_path() -> Path:
    """北向资金本地 CSV 缓存路径"""
    p = Path.home() / ".tradingagents" / "cache" / "northbound_daily.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def save_northbound_snapshot(date: str, hgt: float, sgt: float):
    """写入/更新当天北向收盘数据到 CSV"""
    path = northbound_cache_path()
    rows = {}
    if path.exists():
        for line in path.read_text().strip().split("\n")[1:]:
            parts = line.split(",")
            if len(parts) == 3:
                rows[parts[0]] = line
    rows[date] = f"{date},{hgt},{sgt}"
    with open(path, "w") as f:
        f.write("date,hgt,sgt\n")
        for d in sorted(rows.keys()):
            f.write(rows[d] + "\n")


def load_northbound_history(n: int = 20) -> list[dict]:
    """读取最近 N 天北向历史（CSV缓存）"""
    path = northbound_cache_path()
    if not path.exists():
        return []
    lines = path.read_text().strip().split("\n")[1:]
    rows = []
    for line in lines[-n:]:
        parts = line.split(",")
        if len(parts) == 3:
            rows.append({"date": parts[0], "hgt": float(parts[1]), "sgt": float(parts[2])})
    return rows


# ═══════════════════════════════════════════════
# 9. 北向历史（akshare）
# ═══════════════════════════════════════════════

def fetch_northbound_daily():
    """
    从akshare获取北向资金全量历史
    返回 DataFrame: [trade_date, net_buy, buy_amt, sell_amt, ..., hs300_change]
    """
    import akshare as ak
    df = ak.stock_hsgt_hist_em(symbol="北向资金")
    df = df.rename(columns={
        "日期": "trade_date",
        "当日成交净买额": "net_buy",
        "买入成交额": "buy_amt",
        "卖出成交额": "sell_amt",
        "历史累计净买额": "cum_net_buy",
        "当日资金流入": "flow_in",
        "当日余额": "balance",
        "持股市值": "total_mv",
        "领涨股": "leader_name",
        "领涨股-涨跌幅": "leader_change",
        "沪深300": "hs300",
        "沪深300-涨跌幅": "hs300_change",
        "领涨股-代码": "leader_code",
    })
    return df


def backfill_northbound_to_db():
    """全量回填北向历史到PG"""
    import sys as _sys
    _sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
    import data_finance as df
    
    print("📡 北向资金: 从akshare获取全量历史...")
    raw = fetch_northbound_daily()
    print(f"  {len(raw)}行, 范围{raw.iloc[-1]['trade_date']}~{raw.iloc[0]['trade_date']}")
    
    existing,_ = df._pg_execute("SELECT trade_date FROM northbound_daily")
    existing_set = set(x[0] for x in existing)
    
    inserted = 0
    for _, row in raw.iterrows():
        td = row["trade_date"]
        if td in existing_set:
            continue
        df._pg_execute("""
            INSERT INTO northbound_daily 
            (trade_date, net_buy, buy_amt, sell_amt, cum_net_buy, flow_in, balance, 
             total_mv, leader_name, leader_code, leader_change, hs300, hs300_change)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (trade_date) DO NOTHING
        """, (
            row["trade_date"],
            None if pd.isna(row["net_buy"]) else float(row["net_buy"]),
            None if pd.isna(row["buy_amt"]) else float(row["buy_amt"]),
            None if pd.isna(row["sell_amt"]) else float(row["sell_amt"]),
            None if pd.isna(row["cum_net_buy"]) else float(row["cum_net_buy"]),
            None if pd.isna(row["flow_in"]) else float(row["flow_in"]),
            None if pd.isna(row["balance"]) else float(row["balance"]),
            None if pd.isna(row["total_mv"]) else float(row["total_mv"]),
            row.get("leader_name"),
            row.get("leader_code"),
            None if pd.isna(row["leader_change"]) else float(row["leader_change"]),
            None if pd.isna(row["hs300"]) else float(row["hs300"]),
            None if pd.isna(row["hs300_change"]) else float(row["hs300_change"]),
        ), fetch=False)
        inserted += 1
        if inserted % 200 == 0:
            print(f"    写入{inserted}/{len(raw)}...")
    
    print(f"  ✅ 新增{inserted}条, 跳过{len(raw)-inserted}条已有")
    return inserted


def update_northbound_daily():
    """增量更新北向资金（当日数据，akshare只有到前一天）"""
    import sys as _sys
    _sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
    import data_finance as df
    
    r,_ = df._pg_execute("SELECT MAX(trade_date) FROM northbound_daily")
    latest = r[0][0] if r[0][0] else None
    print(f"  PG最新: {latest}")
    
    raw = fetch_northbound_daily()
    newest_row = raw.iloc[0]  # akshare按时间倒序
    newest_date = newest_row["trade_date"]
    
    if latest and newest_date <= latest:
        print(f"  ✅ 已是最新 ({newest_date})")
        return 0
    
    # 从latest+1开始写入
    inserted = 0
    for _, row in raw.iterrows():
        td = row["trade_date"]
        if latest and td <= latest:
            continue
        df._pg_execute("""
            INSERT INTO northbound_daily
            (trade_date, net_buy, buy_amt, sell_amt, cum_net_buy, flow_in, balance,
             total_mv, leader_name, leader_code, leader_change, hs300, hs300_change)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (trade_date) DO NOTHING
        """, (
            row["trade_date"],
            None if pd.isna(row["net_buy"]) else float(row["net_buy"]),
            None if pd.isna(row["buy_amt"]) else float(row["buy_amt"]),
            None if pd.isna(row["sell_amt"]) else float(row["sell_amt"]),
            None if pd.isna(row["cum_net_buy"]) else float(row["cum_net_buy"]),
            None if pd.isna(row["flow_in"]) else float(row["flow_in"]),
            None if pd.isna(row["balance"]) else float(row["balance"]),
            None if pd.isna(row["total_mv"]) else float(row["total_mv"]),
            row.get("leader_name"),
            row.get("leader_code"),
            None if pd.isna(row["leader_change"]) else float(row["leader_change"]),
            None if pd.isna(row["hs300"]) else float(row["hs300"]),
            None if pd.isna(row["hs300_change"]) else float(row["hs300_change"]),
        ), fetch=False)
        inserted += 1
    
    print(f"  ✅ 新增{inserted}条（最新: {newest_date}）")
    return inserted


# ═══════════════════════════════════════════════
# 6. 东财行业→申万行业映射
# ═══════════════════════════════════════════════

def map_to_sw_industry(ths_name: str) -> str:
    """同花顺/东财行业名 → 申万一级行业"""
    return INDUSTRY_MAP_THS2SW.get(ths_name, _DEFAULT_SW)


def get_industry_by_code(code: str) -> str:
    """从industry_components_sw获取申万一级行业（PG）"""
    import sys
    sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
    import data_finance as df
    r,_ = df._pg_execute(
        "SELECT industry_name FROM industry_components_sw WHERE stock_code LIKE %s",
        (code[:6] + '%',)
    )
    return r[0][0] if r else None


# ═══════════════════════════════════════════════
# 7. 获取当日行业资金流（快速）
# ═══════════════════════════════════════════════

def get_industry_fund_flow() -> list[dict]:
    """
    从东财行业板块排名获取当日行业资金流
    返回: [{industry_sw, name, change_pct, net_inflow, up_count, down_count}, ...]
    """
    comp = industry_comparison(100)
    results = []
    for r in comp["top"]:
        sw = map_to_sw_industry(r["name"])
        results.append({
            "industry_sw": sw,
            "name": r["name"],
            "change_pct": r["change_pct"],
            "net_inflow": r.get("net_inflow", 0),
            "up_count": r["up_count"],
            "down_count": r["down_count"],
            "leader": r["leader"],
            "leader_change": r["leader_change"],
        })
    return results


# ═══════════════════════════════════════════════
# 9. 限售解禁（datacenter）
# ═══════════════════════════════════════════════

def lockup_expiry(code: str, trade_date: str = None, forward_days: int = 90) -> dict:
    """
    限售解禁日历
    code: 6位股票代码
    trade_date: 查询基准日（不含当天），默认今天
    forward_days: 未来多少天
    返回: {history: [...], upcoming: [...]}
    """
    from datetime import datetime, timedelta
    
    base = datetime.strptime(trade_date, "%Y-%m-%d") if trade_date else datetime.now()
    
    # 历史解禁
    r = _req("https://datacenter.eastmoney.com/api/data/v1/get", params={
        "reportName": "RPT_LIFT_STAGE",
        "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,FREE_DATE,FREE_SHARES_TYPE,FREE_SHARES,FREE_RATIO,ABLE_FREE_SHARES,LIFT_MARKET_CAP",
        "filter": f'(SECURITY_CODE="{code}")',
        "pageNumber": "1", "pageSize": "50",
        "sortTypes": "-1", "sortColumns": "FREE_DATE",
        "source": "WEB", "client": "WEB",
    })
    history = []
    if r and r.get("result") and r["result"].get("data"):
        for row in r["result"]["data"]:
            fd = str(row.get("FREE_DATE", ""))[:10]
            if not trade_date or fd < trade_date:
                history.append({
                    "date": fd,
                    "type": row.get("FREE_SHARES_TYPE", ""),
                    "shares": row.get("FREE_SHARES", 0),
                    "ratio": row.get("FREE_RATIO", 0),
                    "able_shares": row.get("ABLE_FREE_SHARES", 0),
                    "mcap": row.get("LIFT_MARKET_CAP", 0),
                    "name": row.get("SECURITY_NAME_ABBR", ""),
                })
    
    # 未来待解禁
    end_str = (base + timedelta(days=forward_days)).strftime("%Y-%m-%d")
    start_str = base.strftime("%Y-%m-%d")
    r = _req("https://datacenter.eastmoney.com/api/data/v1/get", params={
        "reportName": "RPT_LIFT_STAGE",
        "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,FREE_DATE,FREE_SHARES_TYPE,FREE_SHARES,FREE_RATIO,LIFT_MARKET_CAP",
        "filter": f'(SECURITY_CODE="{code}")(FREE_DATE>="{start_str}")(FREE_DATE<="{end_str}")',
        "pageNumber": "1", "pageSize": "50",
        "sortTypes": "1", "sortColumns": "FREE_DATE",
        "source": "WEB", "client": "WEB",
    })
    upcoming = []
    if r and r.get("result") and r["result"].get("data"):
        for row in r["result"]["data"]:
            upcoming.append({
                "date": str(row.get("FREE_DATE", ""))[:10],
                "type": row.get("FREE_SHARES_TYPE", ""),
                "shares": row.get("FREE_SHARES", 0),
                "ratio": row.get("FREE_RATIO", 0),
                "mcap": row.get("LIFT_MARKET_CAP", 0),
                "name": row.get("SECURITY_NAME_ABBR", ""),
            })
    
    return {"history": history, "upcoming": upcoming}


# ═══════════════════════════════════════════════
# 10. 融资融券明细（datacenter）
# ═══════════════════════════════════════════════

def margin_detail(code: str, page_size: int = 60) -> list[dict]:
    """
    个股融资融券明细（日级）
    code: 6位股票代码
    返回: [{date, rzye, rzmre, rzche, rqye, rqmcl, rqchl, rzrqye}, ...]
    """
    r = _req("https://datacenter.eastmoney.com/api/data/v1/get", params={
        "reportName": "RPTA_WEB_RZRQ_GGMX",
        "columns": "DATE,SCODE,SECNAME,RZYE,RZMRE,RZCHE,RQYE,RQMCL,RQCHL,RZRQYE,SPJ",
        "filter": f'(SCODE="{code}")',
        "pageNumber": "1", "pageSize": str(page_size),
        "sortTypes": "-1", "sortColumns": "DATE",
        "source": "WEB", "client": "WEB",
    })
    rows = []
    if not r or not r.get("result") or not r["result"].get("data"):
        return rows
    for row in r["result"]["data"]:
        rows.append({
            "date": str(row.get("DATE", ""))[:10],
            "rzye": float(row.get("RZYE", 0)),
            "rzmre": float(row.get("RZMRE", 0)),
            "rzche": float(row.get("RZCHE", 0)),
            "rqye": float(row.get("RQYE", 0)),
            "rqmcl": float(row.get("RQMCL", 0)),
            "rqchl": float(row.get("RQCHL", 0)),
            "rzrqye": float(row.get("RZRQYE", 0)),
            "close": float(row.get("SPJ", 0)),
            "name": row.get("SECNAME", ""),
        })
    return rows


# ═══════════════════════════════════════════════
# 11. 同花顺热点（题材归因）
# ═══════════════════════════════════════════════

def ths_hot_reason(date: str = None) -> list[dict]:
    """
    同花顺当日强势股归因
    date: YYYY-MM-DD 格式，None=今天
    返回: [{code, name, reason, zhangfu, huanshou, ...}, ...]
    """
    from datetime import date as _date
    dt = date or _date.today().strftime("%Y-%m-%d")
    
    url = f"http://zx.10jqka.com.cn/event/api/getharden/date/{dt}/orderby/date/orderway/desc/charset/GBK/"
    r = requests.get(url, headers={"User-Agent": UA}, timeout=10)
    data = r.json()
    
    items = data.get("data") or []
    rows = []
    for item in items:
        rows.append({
            "code": item.get("code", ""),
            "name": item.get("name", ""),
            "reason": item.get("reason", ""),
            "date": dt,
        })
    return rows


# ═══════════════════════════════════════════════
# 12. mootdx 通达信协议（五档/逐笔/K线）
# ═══════════════════════════════════════════════

_mootdx_client = None


def _get_mootdx():
    """获取mootdx客户端（单例）"""
    global _mootdx_client
    if _mootdx_client is None:
        from mootdx.quotes import Quotes
        _mootdx_client = Quotes.factory(market='std')
    return _mootdx_client


def mootdx_bars(symbol: str, category: int = 4, offset: int = 800) -> list:
    """
    通达信K线（不封IP）
    symbol: 6位代码
    category: 4=日线, 5=周线, 6=月线, 7=1分钟, 8=5分钟, 9=15分钟, 10=30分钟, 11=60分钟
    offset: 返回条数（最多800）
    返回: [{open,close,high,low,vol,amount,datetime}]
    """
    client = _get_mootdx()
    df = client.bars(symbol=symbol, category=category, offset=offset)
    if df is None or len(df) == 0:
        return []
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "open": float(r["open"]),
            "close": float(r["close"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "vol": int(r["vol"]),
            "amount": float(r["amount"]),
            "datetime": str(r["datetime"]),
        })
    return rows


def mootdx_quotes(symbols: list) -> list:
    """
    五档盘口实时行情
    symbols: ["600519", "000001", ...]
    返回: [{code, price, open, high, low, last_close, bid1~5, ask1~5, ...}]
    """
    client = _get_mootdx()
    df = client.quotes(symbol=symbols)
    if df is None or len(df) == 0:
        return []
    rows = []
    for _, r in df.iterrows():
        row = {"code": r["code"]}
        for col in df.columns:
            if col != "code":
                val = r[col]
                row[col] = float(val) if isinstance(val, (int, float)) else val
        rows.append(row)
    return rows


def mootdx_transactions(symbol: str, date: str) -> list:
    """
    逐笔成交（交易时段外返回空）
    symbol: 6位代码
    date: YYYYMMDD
    返回: [{time, price, vol, num, buyorsell}]
    """
    client = _get_mootdx()
    df = client.transaction(symbol=symbol, date=date)
    if df is None or len(df) == 0:
        return []
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "time": str(r["time"]),
            "price": float(r["price"]),
            "vol": int(r["vol"]),
            "num": int(r.get("num", 0)),
            "buyorsell": int(r.get("buyorsell", -1)),
        })
    return rows


# ═══════════════════════════════════════════════
# 测试入口
# ═══════════════════════════════════════════════

if __name__ == '__main__':
    import json
    from datetime import date as _dt
    
    print("=== 1. 行业板块排名 ===")
    comp = industry_comparison(5)
    print(f"共{comp['total']}个行业")
    for r in comp["top"][:5]:
        print(f"  {r['name']}: {r['change_pct']}% 涨{r['up_count']}跌{r['down_count']} 领涨{r['leader']}")
    
    print("\n=== 2. 龙虎榜今日数据 ===")
    dt = daily_dragon_tiger()
    print(f"共{dt['total_records']}条记录")
    for s in dt['stocks'][:5]:
        print(f"  {s['code']} {s['name']}: 净买{s['net_buy_wan']}万 涨跌{s['change_pct']}% 涨停={s['is_limit_up']}")
    
    print("\n=== 3. 机构买卖明细 ===")
    inst = lhb_inst_detail()
    if inst:
        for s in inst[:5]:
            print(f"  {s['code']} {s['name']}: 机构净买{s['inst_net']/10000:.0f}万 买{s['inst_buyers']}卖{s['inst_sellers']}")
    else:
        print("  无数据（接口可能不支持该日期）")
    
    print("\n=== 4. 分钟级资金流（000858） ===")
    ff = fund_flow_minute("000858")
    if ff:
        last = ff[-1]
        total_main = sum(r["main_net"] for r in ff)
        print(f"  {len(ff)}条分钟数据")
        print(f"  最后: 主力净{last['main_net']:.0f}元")
        print(f"  全天主力累计: {total_main/10000:.0f}万元")
    
    print("\n=== 5. 行业映射样例 ===")
    for name in ["半导体", "化学制品", "汽车零部件", "银行", "军工装备", "白色家电"]:
        sw = map_to_sw_industry(name)
        print(f"  {name} → {sw}")
    
    print("\n=== 6. 个股新闻(688017) ===")
    news = stock_news("688017", 3)
    for n in news:
        c = n['content'][:60] if n['content'] else ''
        print(f"  {n['time'][:10]} | {n['source']} | {n['title'][:50]}")
    
    print("\n=== 7. 财联社快讯 ===")
    teleg = cls_telegraph(3)
    for t in teleg:
        print(f"  {t['time'][:16]} | {t['title'][:60]}")
    
    print("\n=== 8. 东财全球资讯 ===")
    global_n = eastmoney_global_news(3)
    for n in global_n:
        print(f"  {n['time']} | {n['title'][:60]}")
    
    print("\n=== 9. 北向资金 ===")
    hsgt = hsgt_realtime()
    if len(hsgt) > 0:
        print(f"  {len(hsgt)}个时间点")
        print(f"  最新: {hsgt[-1]['time']} 沪{hsgt[-1]['hgt_yi']:.2f}亿 深{hsgt[-1]['sgt_yi']:.2f}亿")
    
    print("\n=== 10. 限售解禁（立讯 002475）===")
    lu = lockup_expiry("002475")
    print(f"  历史{len(lu['history'])}批, 未来{len(lu['upcoming'])}批")
    for item in lu['upcoming'][:3]:
        print(f"    {item['date']} | {item['type']} | 解禁{item['shares']}股 市值{item['mcap']}万")
    if not lu['upcoming']:
        # 看历史
        for item in lu['history'][:3]:
            print(f"    [历史] {item['date']} | {item['type']} | 解禁{item['shares']}股 占比{item['ratio']}%")
    
    print("\n=== 11. 融资融券（茅台 600519）===")
    mg = margin_detail("600519", 3)
    for item in mg[:3]:
        net = (item['rzmre'] - item['rzche']) / 1e8
        print(f"    {item['date']}: 融资余额{item['rzye']/1e8:.1f}亿 买入{item['rzmre']/1e8:.2f}亿 净{net:+.2f}亿")
    
    print("\n=== 12. 同花顺热点（今日）===")
    hot = ths_hot_reason()
    print(f"  {len(hot)}只强势股")
    for item in hot[:3]:
        print(f"    {item['code']} {item['name']}: {item['reason'][:80]}")
    # 词频统计
    from collections import Counter
    all_tags = []
    for h in hot:
        for t in str(h['reason']).split('+'):
            if t.strip():
                all_tags.append(t.strip())
    cnt = Counter(all_tags)
    print(f"  题材TOP5: {dict(cnt.most_common(5))}")
    
    print("\n=== 13. mootdx K线（688017 日线）===")
    klines = mootdx_bars("688017", 4, 5)
    if klines:
        print(f"  最新: {klines[-1]['datetime']} O{klines[-1]['open']} H{klines[-1]['high']} L{klines[-1]['low']} C{klines[-1]['close']} V{klines[-1]['vol']}")
    
    print("\n=== 14. mootdx 五档盘口（688017）===")
    q = mootdx_quotes(["688017", "600519"])
    for item in q:
        print(f"  {item['code']}: 现{item['price']} 买1={item['bid1']}×{item['bid_vol1']} 卖1={item['ask1']}×{item['ask_vol1']}")
    
    print("\n=== 15. mootdx 逐笔成交（非交易时段可能空）===")
    t = mootdx_transactions("688017", "20260521")
    print(f"  {len(t)}条")
    if t:
        print(f"    最新: {t[-1]['time']} 价{t[-1]['price']} 量{t[-1]['vol']} 方向{t[-1]['buyorsell']}")
