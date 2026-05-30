#!/usr/bin/env python3
"""
同花顺研报爬虫 — 0鉴权，纯HTTP

从 10jqka.com.cn 获取券商研报列表（标题、摘要、机构、评级），
写入PG broker_report 表。

用法:
  python3 broker_research.py --latest        # 获取最新研报
  python3 broker_research.py --page 1-5      # 多页
  python3 broker_research.py --stock 600519  # 指定个股研报
  python3 broker_research.py --init-db       # 建表
  python3 broker_research.py --stats         # 统计
"""

import json, sys, os, re, time, argparse, logging
from pathlib import Path
from datetime import datetime, date
from typing import List, Dict, Optional

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('broker_research')

# ─── 配置 ───
BASE_DIR = Path(__file__).resolve().parent.parent
PG_DSN = dict(host='192.168.3.14', port=5432, user='guludan', password='toto', dbname='guludan')

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def get_connection():
    import psycopg2
    return psycopg2.connect(**PG_DSN)


def init_db():
    """创建broker_report表"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS broker_report (
            id SERIAL PRIMARY KEY,
            stock_code VARCHAR(10),
            stock_name VARCHAR(20),
            title TEXT NOT NULL,
            broker VARCHAR(50),
            analyst VARCHAR(100),
            rating VARCHAR(20),
            target_price NUMERIC(10,2),
            report_date DATE NOT NULL,
            summary TEXT,
            industry VARCHAR(50),
            url TEXT,
            source VARCHAR(20) DEFAULT '10jqka',
            fetched_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(report_date, source, title)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_broker_report_date ON broker_report(report_date)");
    cur.execute("CREATE INDEX IF NOT EXISTS idx_broker_report_stock ON broker_report(stock_code)");
    conn.commit()
    cur.close()
    conn.close()
    log.info("✅ broker_report 表已就绪")


def fetch_research_list(category: str = "company", page: int = 1,
                        stock_code: str = "", days: int = 30) -> List[Dict]:
    """
    从东方财富获取研报列表

    返回: [{
        "stock_code", "stock_name", "title", "broker", "analyst",
        "rating", "target_price", "report_date", "summary", "industry"
    }, ...]
    """
    import requests, re
    from datetime import datetime, timedelta

    reports = []
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    headers = {
        "User-Agent": UA,
        "Referer": "https://data.eastmoney.com/report/",
    }

    url = "https://reportapi.eastmoney.com/report/list"
    params = {
        "cb": "jQuery",
        "industryCode": "*",
        "pageSize": "50",
        "industry": "*",
        "rating": "*",
        "ratingChange": "*",
        "beginTime": start_date,
        "endTime": end_date,
        "pageNo": str(page),
        "fields": "",
        "qType": "0",
        "orgCode": "",
        "rcode": "",
        "pCode": "",
    }

    if stock_code:
        params['stockCode'] = stock_code
        params['qType'] = '1'

    if category != 'all' and not stock_code:
        cat_map = {
            'company': '1',
            'industry': '2',
            'macro': '3',
            'strategy': '4',
        }
        col_map = {
            '1': '002004001002',   # 公司研报
            '2': '002004001003',   # 行业研报
            '3': '002004001004',   # 宏观研报
            '4': '002004001005',   # 策略研报
        }
        # 设置column参数
        params['columns'] = col_map.get(cat_map.get(category, '1'), '002004001002')

    try:
        r = requests.get(url, params=params, headers=headers, timeout=30)
        if r.status_code != 200:
            log.warning(f"  HTTP {r.status_code}")
            return reports

        text = r.text
        # 去掉JSONP包装
        m = re.search(r'jQuery\((.*)\)', text)
        if not m:
            log.warning("  非JSONP返回")
            return reports

        data = json.loads(m.group(1))
        items = data.get('data', [])
        log.info(f"  HTTP 200, {len(items)}条")

        for item in items:
            report = _parse_eastmoney_item(item)
            if report and report.get('title'):
                reports.append(report)

    except Exception as e:
        log.error(f"  请求失败: {e}")

    return reports


def _parse_eastmoney_item(item: Dict) -> Dict:
    """解析东方财富研报条目"""
    title = item.get('title', '').strip()
    if not title:
        return {}

    # 提取评级
    rating = item.get('emRatingName', item.get('rating', '')) or ''
    if not rating:
        # 从标题提取
        for kw in ['买入', '增持', '推荐', '持有', '中性', '减持', '卖出',
                   '强烈推荐', '优于大市', '同步大市', '落后大市']:
            if kw in title:
                rating = kw
                break

    # 目标价
    target_price = item.get('emTargetPrice', '')
    if not target_price:
        tp_match = re.search(r'目标价[：:]*\s*(\d+\.?\d*)', title)
        if tp_match:
            target_price = tp_match.group(1)

    try:
        target_price = float(target_price) if target_price else None
    except:
        target_price = None

    # 全文摘要
    summary = item.get('emSummary', item.get('abstract', '')) or ''

    # 盈利预测
    eps_info = {}
    for year_key in ['predictThisYearEps', 'predictNextYearEps', 'predictNextTwoYearEps']:
        val = item.get(year_key, '')
        if val:
            eps_info[year_key] = val

    if eps_info:
        eps_str = ', '.join(f"{k}={v}" for k, v in eps_info.items())
        if summary:
            summary += f" | EPS:{eps_str}"
        else:
            summary = f"EPS:{eps_str}"

    # 日期
    pub_date = item.get('publishDate', '')
    if pub_date and len(pub_date) >= 10:
        pub_date = pub_date[:10]

    return {
        'stock_code': item.get('stockCode', ''),
        'stock_name': item.get('stockName', ''),
        'title': title[:200],
        'broker': item.get('orgSName', item.get('orgName', ''))[:50],
        'analyst': item.get('analyst', item.get('emAnalyst', ''))[:100],
        'rating': rating[:30],
        'target_price': target_price,
        'report_date': pub_date,
        'summary': summary[:500],
    }


def _parse_html_tds(tds: List[str]) -> Dict:
    """从HTML <td>列表解析一条研报"""
    try:
        title = ''
        broker = ''
        analyst = ''
        rating = ''
        stock_code = ''
        stock_name = ''
        report_date = ''
        summary = ''

        # 根据列数解析
        # 个股研报: 代码/名称 | 标题 | 机构 | 分析师 | 评级 | 日期
        for i, td in enumerate(tds):
            text = re.sub(r'<[^>]+>', '', td).strip()

            if i == 0:
                # 股票代码和名称: <a href="...">600519</a> 贵州茅台
                codes = re.findall(r'>(\d{6})<', td)
                if codes:
                    stock_code = codes[0]
                names = re.findall(r'<a[^>]*>([^<]+)</a>', td)
                if names:
                    stock_name = names[-1].strip()
                elif codes:
                    stock_name = text.replace(codes[0], '').strip()

            elif i == 1:
                # 标题
                title = text

            elif i == 2:
                # 机构
                broker = text

            elif i == 3:
                # 分析师
                analyst = text

            elif i == 4:
                # 评级
                rating = text

            elif i == 5:
                # 日期
                report_date = text

            elif i == 6:
                # 摘要（如果有）
                summary = text

        if not title:
            # 另一种表格格式：标题 | 机构 | 分析师 | 评级 | 日期
            title = re.sub(r'<[^>]+>', '', tds[0]).strip()
            broker = re.sub(r'<[^>]+>', '', tds[1]).strip() if len(tds) > 1 else ''
            analyst = re.sub(r'<[^>]+>', '', tds[2]).strip() if len(tds) > 2 else ''
            rating = re.sub(r'<[^>]+>', '', tds[3]).strip() if len(tds) > 3 else ''
            report_date = re.sub(r'<[^>]+>', '', tds[4]).strip() if len(tds) > 4 else ''

        # 标准化日期
        if report_date:
            report_date = report_date.replace('/', '-')[:10]

        # 清理标题
        title = title.replace('\n', '').replace('\r', '').strip()
        # 去掉"研报"二字尾缀
        title = re.sub(r'研报$', '', title).strip()

        # 提取评级中的目标价（如果有）
        target_price = None
        tp_match = re.search(r'目标价[：:]\s*(\d+\.?\d*)', title)
        if not tp_match:
            tp_match = re.search(r'目标价(\d+\.?\d*)', rating)
        if tp_match:
            target_price = float(tp_match.group(1))

        # 标准评级
        if rating:
            rating_map = {
                '买入': 'buy',
                '增持': 'overweight',
                '推荐': 'recommend',
                '强烈推荐': 'strong_buy',
                '持有': 'hold',
                '中性': 'neutral',
                '减持': 'underweight',
                '卖出': 'sell',
                '优于大市': 'outperform',
                '同步大市': 'market_perform',
                '落后大市': 'underperform',
            }
            # 保留中文评级，也加英文映射
            for cn, en in rating_map.items():
                if cn in rating:
                    rating = f"{cn}({en})"
                    break

        return {
            'stock_code': stock_code,
            'stock_name': stock_name,
            'title': title[:200],
            'broker': broker[:50],
            'analyst': analyst[:100],
            'rating': rating[:30],
            'target_price': target_price,
            'report_date': report_date[:10],
            'summary': summary[:500],
        }

    except Exception as e:
        return {}


def _parse_research_row(row: Dict) -> Dict:
    """从JSON行解析"""
    return {
        'stock_code': row.get('stock_code', row.get('code', '')),
        'stock_name': row.get('stock_name', row.get('name', '')),
        'title': row.get('title', '')[:200],
        'broker': row.get('org_name', row.get('broker', ''))[:50],
        'analyst': row.get('analyst', '')[:100],
        'rating': row.get('rating', '')[:30],
        'target_price': row.get('target_price'),
        'report_date': str(row.get('report_date', ''))[:10],
        'summary': row.get('summary', '')[:500],
    }


def save_reports(reports: List[Dict], batch_size: int = 50) -> int:
    """批量写入PG"""
    if not reports:
        return 0

    psycopg2 = __import__('psycopg2')
    conn = get_connection()
    cur = conn.cursor()

    inserted = 0
    batch = []

    for r in reports:
        if not r.get('title'):
            continue
        # 只存有日期的
        if not r.get('report_date'):
            continue

        # 从标题提取股票信息（如果没从表格获得）
        if not r.get('stock_code'):
            title = r['title']
            code_match = re.search(r'(\d{6})[\.]', title)
            if not code_match:
                code_match = re.search(r'[（(](\d{6})[)）]', title)
            if code_match:
                r['stock_code'] = code_match.group(1)

        batch.append((
            r.get('stock_code', ''),
            r.get('stock_name', ''),
            r['title'],
            r.get('broker', ''),
            r.get('analyst', ''),
            r.get('rating', ''),
            r.get('target_price'),
            r['report_date'],
            r.get('summary', ''),
        ))

        if len(batch) >= batch_size:
            inserted += _execute_batch(cur, batch)
            conn.commit()
            batch = []

    if batch:
        inserted += _execute_batch(cur, batch)
        conn.commit()

    cur.close()
    conn.close()
    return inserted


def _execute_batch(cur, batch):
    try:
        from psycopg2.extras import execute_values
        execute_values(
            cur,
            """
            INSERT INTO broker_report
                (stock_code, stock_name, title, broker, analyst,
                 rating, target_price, report_date, summary)
            VALUES %s
            ON CONFLICT (report_date, source, title) DO NOTHING
            """,
            batch,
            template="(%s, %s, %s, %s, %s, %s, %s::numeric, %s::date, %s)"
        )
        return len(batch)
    except Exception as e:
        log.warning(f"  批量插入失败: {e}")
        # 逐条插入
        n = 0
        for b in batch:
            try:
                cur.execute("""
                    INSERT INTO broker_report
                        (stock_code, stock_name, title, broker, analyst,
                         rating, target_price, report_date, summary)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (report_date, source, title) DO NOTHING
                """, b + ('10jqka',))
                n += 1
            except:
                pass
        return n


# ═══════════════════════════════════════
#  CLI
# ═══════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="同花顺研报爬虫")
    parser.add_argument('--latest', action='store_true', help='获取最新几页研报')
    parser.add_argument('--page', type=int, default=1, help='起始页码')
    parser.add_argument('--pages', type=int, default=3, help='总页数')
    parser.add_argument('--stock', type=str, help='指定个股代码')
    parser.add_argument('--category', choices=['all', 'company', 'industry', 'strategy', 'macro'],
                        default='company', help='研报类别')
    parser.add_argument('--init-db', action='store_true', help='建表')
    parser.add_argument('--stats', action='store_true', help='统计')
    parser.add_argument('--dry-run', action='store_true', help='只预览不写入')
    args = parser.parse_args()

    if args.init_db:
        init_db()
        return

    if args.stats:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM broker_report")
        total = cur.fetchone()[0]
        cur.execute("SELECT report_date, count(*) FROM broker_report GROUP BY report_date ORDER BY report_date DESC LIMIT 10")
        print(f"总计: {total}条\n")
        print("最近10期:")
        for r in cur.fetchall():
            print(f"  {r[0]}: {r[1]}条")
        cur.execute("SELECT broker, count(*) as cnt FROM broker_report GROUP BY broker ORDER BY cnt DESC LIMIT 10")
        print("\n最活跃券商:")
        for r in cur.fetchall():
            print(f"  {r[0]}: {r[1]}份")
        cur.close()
        conn.close()
        return

    # 抓取
    all_reports = []

    if args.stock:
        log.info(f"获取 {args.stock} 个股研报...")
        reports = fetch_research_list(stock_code=args.stock)
        all_reports.extend(reports)
    else:
        log.info(f"获取 {args.category}研报 第{args.page}-{args.page+args.pages-1}页...")
        for p in range(args.page, args.page + args.pages):
            reports = fetch_research_list(category=args.category, page=p)
            if reports:
                all_reports.extend(reports)
                log.info(f"  第{p}页: {len(reports)}条")
            time.sleep(0.5)

    log.info(f"总计获取: {len(all_reports)}条")

    if not all_reports:
        log.warning("无数据")
        return

    if args.dry_run:
        print("\n预览前10条:")
        for r in all_reports[:10]:
            print(f"  [{r.get('report_date','')}] [{r.get('broker','')}] {r.get('title','')[:50]}")
        return

    inserted = save_reports(all_reports)
    log.info(f"写入PG: {inserted}条")

    # 统计
    brokers = set(r.get('broker', '') for r in all_reports if r.get('broker'))
    stocks = set(r.get('stock_code', '') for r in all_reports if r.get('stock_code'))
    print(f"  涉及券商: {len(brokers)}, 涉及个股: {len(stocks)}")


if __name__ == '__main__':
    main()
