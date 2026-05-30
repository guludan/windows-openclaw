#!/usr/bin/env python3
"""
拉取 A 股季度财务数据到 PG v2 — mootdx F10 版
替代 baostock（卡死不稳定），仅用于补最新季度（2026Q1）
mootdx TCP 协议，不封IP，速度快

核心推理: mootdx.F10(财务分析) 文本表格含:
- 净利润/净利润增长率(yoy)/营收/营收增长率(yoy)
- 加权净资产收益率(ROE) / 基本每股收益(EPS) / 每股净资产(bvps)
- 历史6个报告期（最新+前5年）

用法: python3 fetch_financial_data_v2.py [--full]
      --full: 全量覆盖（默认只补 latest_quarter）
"""
import sys, os, re, time, logging
import psycopg2.extras as pge

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import data_finance as df

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fetch_fin_v2")

# 在脚本内导入 mootdx（避免顶层导入污染）
from mootdx.quotes import Quotes
client = Quotes.factory(market='std')

def parse_financial_f10(text: str) -> dict:
    """
    解析 mootdx F10 财务分析文本表格。
    返回: { "2026-03-31": {净利润,净利润增长率,营业总收入,营收增长率,roe,eps,bvps,...}, ... }
    字段名带引用的会去标
    """
    if not text:
        return {}
    
    # 取所有表格行: 每行以｜开头｜结尾，中间含｜分隔
    lines = text.split('\n')
    
    # 找到表头行 — 日期列 如 2026-03-31
    header_date_cols = []
    header_idx = -1
    for i, line in enumerate(lines):
        if '｜' not in line:
            continue
        # 找日期列
        parts = line.split('｜')
        date_hits = [p.strip() for p in parts if re.match(r'\s*\d{4}-\d{2}-\d{2}\s*', p)]
        if len(date_hits) >= 3:
            header_date_cols = parts
            header_idx = i
            break
    
    if not header_date_cols:
        return {}
    
    # 提取日期列
    dates = [p.strip() for p in header_date_cols if re.match(r'\s*\d{4}-\d{2}-\d{2}\s*', p)]
    
    # 找指标行
    indicators = {
        '净利润(元)': 'net_profit',
        '净利润增长率': 'net_profit_yoy',
        '营业总收入(元)': 'revenue',
        '营业总收入增长率': 'revenue_yoy',
        '加权净资产收益率': 'roe',
        '基本每股收益': 'eps',
        '每股净资产(元)': 'bvps',
    }
    
    result = {}
    for d in dates:
        result[d] = {}
    
    for line in lines:
        if '｜' not in line:
            continue
        parts = [p.strip() for p in line.split('｜')]
        if len(parts) < len(header_date_cols):
            continue
        
        label = parts[1]  # 第一列是指标名
        # 找匹配
        matched_key = None
        for pat, key in indicators.items():
            if pat in label:
                matched_key = key
                break
        if not matched_key:
            continue
        
        # 各列的数字（跳过 label 列）
        # 从第2列开始匹配 dates
        val_parts = parts[2:2+len(dates)]
        for j, d in enumerate(dates):
            raw = val_parts[j] if j < len(val_parts) else ''
            # 清除百分比%符号、汉字单位
            clean = raw.replace('%', '').replace(',', '').strip()
            if not clean or clean == '－' or clean == '-' or clean == '—':
                continue
            
            # 处理单位 (亿/万)
            num = None
            if '万亿' in clean:
                num = float(clean.replace('万亿', '')) * 1e12
            elif '亿' in clean and '万亿' not in clean:
                num = float(clean.replace('亿', '')) * 1e8
            elif '万' in clean:
                num = float(clean.replace('万', '')) * 1e4
            else:
                try:
                    num = float(clean)
                except:
                    pass
            
            if num is not None:
                # 百分比指标不要乘单位
                if matched_key in ('net_profit_yoy', 'revenue_yoy', 'roe'):
                    pass  # 直接存百分比数值
                result[d][matched_key] = num
    
    return result


def get_quarter_info(end_date_str: str) -> (int, int):
    """2026-03-31 → (2026, 1)"""
    parts = end_date_str.split('-')
    year = int(parts[0])
    month = int(parts[1])
    quarter = (month - 1) // 3 + 1
    return year, quarter


def main():
    full_mode = '--full' in sys.argv
    
    if full_mode:
        log.info("全量模式：覆盖所有股票最新季报")
    else:
        log.info("补录模式：仅补缺失最新季度")
    
    all_codes = df.get_active_codes()
    log.info(f"总活跃股票: {len(all_codes)} 只")
    
    # 已有数据
    rows, _ = df._pg_execute("SELECT symbol, MAX(end_date) FROM financial_data GROUP BY symbol")
    existing = {r[0]: r[1] for r in rows}
    log.info(f"已有季报数据的股票: {len(existing)} 只")
    
    # 获取最新季度: 看今天是几月
    from datetime import date
    today = date.today()
    latest_quarter_month = ((today.month - 1) // 3) * 3 + 3  # 5月→3, 8月→6, ...
    latest_quarter = f"{today.year}-{latest_quarter_month:02d}-{31 if latest_quarter_month in [1,3,5,7,8,10,12] else 30}"
    # 简化：Q1=03-31, Q2=06-30, Q3=09-30, Q4=12-31
    q_suffix = {3: '03-31', 6: '06-30', 9: '09-30', 12: '12-31'}
    latest_q_str = f"{today.year}-{q_suffix[latest_quarter_month]}"
    
    # 如果是2026上半年，最新完整季是2026-03-31
    # 更安全：直接从已有数据看最新end_date
    cur, _ = df._pg_execute("SELECT MAX(end_date) FROM financial_data")
    max_end_date = cur[0][0] if cur and cur[0] else None
    if max_end_date:
        log.info(f"当前数据库最新季度: {max_end_date}")
        # 如果已有2026Q1, 看是否需要补2026Q2
        # 先只补2026Q1（当前任务）
    
    target_q = "2026-03-31"
    log.info(f"目标季度: {target_q}")
    
    # 过滤：需要补 target_q 的股票
    need = []
    for code in all_codes:
        if code in existing and existing[code] and str(existing[code]) >= target_q:
            continue
        need.append(code)
    
    log.info(f"需补 {target_q} 财报的股票: {len(need)} 只")
    
    total_written = 0
    failed = []
    
    for idx, code in enumerate(need):
        if idx > 0 and idx % 50 == 0:
            log.info(f"进度 {idx}/{len(need)}, 已写入 {total_written} 行")
        
        try:
            text = client.F10(symbol=code, name='财务分析')
        except Exception as e:
            failed.append((code, str(e)))
            time.sleep(1)
            continue
        
        parsed = parse_financial_f10(text)
        if not parsed or target_q not in parsed:
            failed.append((code, 'F10解析失败或无目标季度'))
            time.sleep(0.3)
            continue
        
        qdata = parsed[target_q]
        year, q_type = get_quarter_info(target_q)
        
        net_profit = qdata.get('net_profit')
        revenue = qdata.get('revenue')
        bvps = qdata.get('bvps')
        eps = qdata.get('eps')
        roe = qdata.get('roe')
        np_yoy = qdata.get('net_profit_yoy')
        rev_yoy = qdata.get('revenue_yoy')
        
        # SQL写入
        try:
            conn = df._get_pg()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO financial_data 
                (symbol, end_date, publish_date, roe, net_profit, net_profit_yoy,
                 revenue, revenue_yoy, eps, bvps, quarter_type, year)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, end_date) DO UPDATE SET
                roe=EXCLUDED.roe, net_profit=EXCLUDED.net_profit,
                net_profit_yoy=EXCLUDED.net_profit_yoy,
                revenue=EXCLUDED.revenue, revenue_yoy=EXCLUDED.revenue_yoy,
                eps=EXCLUDED.eps, bvps=EXCLUDED.bvps,
                publish_date=EXCLUDED.publish_date
            """, (
                'A.' + code, target_q, f"{today.year}-{today.month:02d}-{today.day:02d}",
                roe, net_profit, np_yoy,
                revenue, rev_yoy, eps, bvps,
                q_type, year
            ))
            conn.commit()
            cur.close()
            total_written += 1
        except Exception as e:
            failed.append((code, f"DB错误: {e}"))
        
        time.sleep(0.2)  # 适度限速，不封IP
    
    log.info(f"\n✅ 完成！写入 {total_written}/{len(need)} 只，失败 {len(failed)} 只")
    if failed:
        log.info(f"失败示例(前10): {failed[:10]}")


if __name__ == "__main__":
    main()
