#!/usr/bin/env python3
"""
股市日报 - 交易日收盘后生成
黄老板专属：市场温度计 + 智库视角解读
输出: /mnt/d/data/a-stock/reports/{date}/股市日报_{date}.md
"""
import json
import sys
import os
from datetime import datetime, date
from pathlib import Path

# ===== PG 查询辅助 =====
sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/scripts"))
try:
    from db_utils import get_connection
except:
    print("⚠️ db_utils 加载失败，日报将使用模拟数据")

def query(sql, params=None):
    """从PG查数据"""
    try:
        from db_utils import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(sql, params or ())
        cols = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        return []

def main():
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    today_compact = today.strftime("%Y%m%d")
    
    # 输出目录
    outdir = Path(f"/mnt/d/data/a-stock/reports/{today_str}")
    outdir.mkdir(parents=True, exist_ok=True)
    outpath = outdir / f"股市日报_{today_compact}.md"
    
    # ===== 1. 大盘水位 =====
    rows = query("""
        SELECT date, open, high, low, close, volume, amount
        FROM daily_prices 
        WHERE symbol = 'A.000001' AND date >= %s
        ORDER BY date DESC LIMIT 5
    """, (today,))
    
    if rows:
        latest = rows[0]
        prev = rows[1] if len(rows) > 1 else latest
        chg_pct = round((latest['close'] - prev['close']) / prev['close'] * 100, 2)
        hs300 = latest['close']
    else:
        # 模拟数据（数据库无今天数据时）
        hs300 = "---"
        chg_pct = "---"
    
    # ===== 2. 市场情绪 =====
    # 查 pool_energy 大盘 surplus 判断情绪
    sentiment_rows = query("""
        SELECT date, surplus, b_pool, a_pool, extra
        FROM pool_energy
        WHERE level = 'market'
        ORDER BY date DESC LIMIT 2
    """)
    
    if sentiment_rows:
        latest_se = sentiment_rows[0]
        surplus = round(latest_se['surplus'], 2) if latest_se['surplus'] else "---"
        # 从extra解析regime
        extra = latest_se.get('extra', {})
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except:
                extra = {}
        regime = extra.get('regime', 'N/A')
    else:
        surplus = "---"
        regime = "N/A"
    
    # ===== 3. 板块异动（涨幅前3/跌幅前3） =====
    sector_rows = query("""
        SELECT date, sector, pct_chg
        FROM sector_daily
        WHERE date = %s
        ORDER BY pct_chg DESC
    """, (today,)) if today else []
    
    top_sectors = sector_rows[:3] if sector_rows else []
    bottom_sectors = sector_rows[-3:] if sector_rows else []
    if bottom_sectors:
        bottom_sectors.reverse()
    
    # ===== 4. 智库视角解读 =====
    # 用surplus+regime判断市场阶段
    interpretation = ""
    if regime == "BEAR":
        if surplus is not None and surplus < -2:
            interpretation = "极空区(A_ACCUM)" if surplus < -3 else "深度空头区"
        elif surplus is not None and surplus > -1:
            interpretation = "空头衰竭中"
    elif regime == "BULL":
        interpretation = "多头市场"
    elif regime == "TOP_WARNING":
        interpretation = "顶部警示区"
    elif regime == "BOTTOM_WARNING":
        interpretation = "底部警示区"
    elif regime == "SIDEWAYS":
        interpretation = "震荡整理"
    
    # ===== 写入日报 =====
    content = f"""# 📊 股市日报 | {today_str}

---

## 一、大盘水位

| 指标 | 数值 |
|:---|:---:|
| 沪深300 | {hs300} |
| 日涨跌 | {chg_pct}% |
| 能量 surplus | {surplus} |
| 当前 regime | {regime} |

**智库解读：** {interpretation}

---

## 二、板块异动

"""

    if top_sectors:
        content += "**领涨：**\n"
        for s in top_sectors:
            content += f"- {s['sector']}: {s['pct_chg']}%\n"
    else:
        content += "**领涨：** 数据未入库\n"

    content += "\n"

    if bottom_sectors:
        content += "**领跌：**\n"
        for s in bottom_sectors:
            content += f"- {s['sector']}: {s['pct_chg']}%\n"
    else:
        content += "**领跌：** 数据未入库\n"

    # ===== 智库视角 =====
    content += f"""

---

## 三、智库视角

**{today_str} 市场速评**

"""

    if regime == "BEAR":
        content += """当前市场处于空头阶段。Minsky框架解读：当surplus持续为负且偏离加大，
说明机构资金在持续流出（B_pool > A_pool）。如果surplus_z < -2，按照前面回测数据，
这种深跌反而是极空区布局窗口——但不是给你推荐的，只是告诉你市场的真实温度。

Taleb视角：熊市是检验脆弱的时刻。扛不住的资产会爆，扛得住的资产会留下。

Zeihan视角：人口结构长期压制需求，熊市的深度和时间广度都可能超预期。
"""
    elif regime == "BULL":
        content += """当前市场处于多头阶段。但Dalio会提醒你：任何牛市都是债务周期的产物。
需要警惕的是这个牛市建立在什么基础上——是基本面改善还是流动性驱动。
"""
    else:
        content += f"""当前市场处于{regime}阶段(震荡整理/警示区)，无明确方向信号。

Dalio框架：震荡期是"大周期"中的过渡阶段，重点观察下一轮趋势的方向信号。
Schelling框架：临界点未到，保持流动性等待明确信号。
"""

    content += f"""

---

## 四、一句话判断

**{today_str}：** {"市场偏弱，观望为主" if regime in ("BEAR", "TOP_WARNING") else "无明确方向，持有现金等待"}

> *本报告由小狗蛋自动生成，仅供参考，不是投资建议。*
"""

    # 写入文件
    with open(outpath, "w") as f:
        f.write(content)
    
    print(f"✅ 股市日报已写入: {outpath}")
    print(content)

if __name__ == "__main__":
    main()
