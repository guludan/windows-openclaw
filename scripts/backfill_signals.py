#!/usr/bin/env python3
"""
backfill_signals.py — 回补近7天信号数据

用途：收盘后追补 fund_flow / csi_lhb_detail / northbound_daily 等滞后数据
用法：python3 scripts/backfill_signals.py [--days 7]
数据源：东财 push2his + datacenter + akshare（统一来源）
"""
import sys, os, json, time, logging, traceback
from datetime import date, timedelta
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backfill")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import data_finance as df

UA = "Mozilla/5.0"
DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"
PUSH2HIS = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"

def main():
    days = 7
    if '--days' in sys.argv:
        idx = sys.argv.index('--days')
        if idx + 1 < len(sys.argv):
            days = int(sys.argv[idx + 1])
    
    today = date.today()
    target_dates = [(today - timedelta(days=i)).isoformat() for i in range(days)]
    log.info(f"回补目标: {target_dates[-1]} ~ {target_dates[0]} ({days}天)")
    
    # === 1. fund_flow 回补（东财 push2his，500只TOP）===
    log.info("\n1️⃣  fund_flow 回补")
    codes, _ = df._pg_execute("SELECT DISTINCT SUBSTRING(symbol FROM 3) FROM daily_prices WHERE symbol LIKE 'A.%' AND trade_date='2026-05-26' AND (symbol ~ 'A\\.6' OR symbol ~ 'A\\.0' OR symbol ~ 'A\\.3')")
    stock_codes = [r[0] for r in codes if r[0]]
    log.info(f"  总 {len(stock_codes)} 只股票")
    
    total_fund = 0
    for code in stock_codes[:500]:  # TOP 500只
        market = 1 if code.startswith("6") else 0
        params = {"secid": f"{market}.{code}", "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56,f57", "lmt": "10"}
        try:
            r = requests.get(PUSH2HIS, params=params, headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}, timeout=15)
            d = r.json()
            for line in d.get("data", {}).get("klines", []):
                parts = line.split(",")
                if len(parts) < 7: continue
                d_ = parts[0]
                if d_ not in target_dates: continue
                main_net = float(parts[1]) if parts[1] != "-" else 0
                small_net = float(parts[2]) if parts[2] != "-" else 0
                mid_net = float(parts[3]) if parts[3] != "-" else 0
                large_net = float(parts[4]) if parts[4] != "-" else 0
                super_net = float(parts[5]) if parts[5] != "-" else 0
                df._pg_execute(
                    "INSERT INTO fund_flow (code, trade_date, main_net, small_net, mid_net, big_net, super_net) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (code, d_, main_net, small_net, mid_net, large_net, super_net), fetch=False
                )
                total_fund += 1
        except:
            pass
        time.sleep(0.05)
    log.info(f"  fund_flow 写入 {total_fund} 条")
    
    # === 2. csi_lhb_detail 回补（东财 datacenter）===
    log.info("\n2️⃣  龙虎榜回补")
    total_lhb = 0
    for td in target_dates:
        try:
            params = {
                "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
                "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,EXPLANATION,CLOSE_PRICE,CHANGE_RATE,BILLBOARD_NET_AMT,BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,TURNOVERRATE,TOTAL_NEGOTIABLEMV",
                "filter": f"(TRADE_DATE>='{td}')(TRADE_DATE<='{td}')",
                "pageNumber": "1", "pageSize": "500",
                "sortColumns": "BILLBOARD_NET_AMT", "sortTypes": "-1",
                "source": "WEB", "client": "WEB",
            }
            resp = requests.get(DATACENTER, params=params, headers={"User-Agent": UA}, timeout=15)
            resp.raise_for_status()
            r = resp.json()
            if r.get('result') is None:
                log.warning(f"  {td}: 龙虎榜result为空: {r.get('message','')}")
                continue
            items = r.json().get("result", {}).get("data", [])
            count = 0
            for row in items:
                code = row.get("SECURITY_CODE","")
                df._pg_execute(
                    "INSERT INTO csi_lhb_detail (trade_date, code, name, close, change_pct, "
                    "lhb_net_buy, lhb_buy, lhb_sell, turnover, reason) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (td, code, row.get("SECURITY_NAME_ABBR",""), row.get("CLOSE_PRICE",0),
                     row.get("CHANGE_RATE",0), row.get("BILLBOARD_NET_AMT",0)/10000,
                     row.get("BILLBOARD_BUY_AMT",0)/10000, row.get("BILLBOARD_SELL_AMT",0)/10000,
                     row.get("TURNOVERRATE",0), row.get("EXPLANATION","")), fetch=False
                )
                count += 1
            log.info(f"  {td}: {count} 条龙虎榜")
            total_lhb += count
        except Exception as e:
            log.warning(f"  {td}: 龙虎榜失败 {e}")
        time.sleep(1)
    log.info(f"  龙虎榜共计: {total_lhb} 条")
    
    # === 3. northbound_daily + market_signals 回补（akshare北向接口）===
    log.info("\n3️⃣  北向资金回补")
    try:
        import akshare as ak
        df_north = ak.stock_hsgt_fund_flow_summary_em()
        count = 0
        sig_map = {"沪股通": "hsgt_north_sh", "深股通": "hsgt_north_sz",
                    "港股通(沪)": "hsgt_south_sh", "港股通(深)": "hsgt_south_sz"}
        for _, r in df_north.iterrows():
            td = str(r["交易日"])[:10]
            if td not in target_dates: continue
            direction = str(r["板块"])
            sig_type = sig_map.get(direction, "")
            if not sig_type: continue
            try:
                net_buy = float(r["成交净买额"]) if r["成交净买额"] else 0.0
                inflow = float(r["资金净流入"]) if r["资金净流入"] else 0.0
            except:
                net_buy = inflow = 0.0
            df._pg_execute(
                "INSERT INTO market_signals (signal_type, code, trade_date, value, change_val, change_pct, extra) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT (signal_type, trade_date, code) DO UPDATE SET value=EXCLUDED.value",
                (sig_type, "TOTAL", td, net_buy, inflow, 0, json.dumps({"source": "backfill"})), fetch=False
            )
            if direction == "沪股通":
                df._pg_execute(
                    "INSERT INTO northbound_daily (trade_date, net_buy, flow_in, balance) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT (trade_date) DO UPDATE SET net_buy=EXCLUDED.net_buy",
                    (td, net_buy, inflow, 0), fetch=False
                )
            count += 1
        log.info(f"  北向: {count} 条")
    except Exception as e:
        log.warning(f"  北向失败: {e}")
    
    log.info(f"\n✅ 信号回补完成")

if __name__ == "__main__":
    main()
