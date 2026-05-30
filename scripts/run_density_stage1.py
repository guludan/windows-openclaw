#!/usr/bin/env python3
"""
run_density_stage1.py — 密度引擎阶段1：批量处理事件库 → nlp_event_scores

用法:
  python3 run_density_stage1.py           # 处理2019-2024所有未解析事件
  python3 run_density_stage1.py --limit 100  # 只处理前100条测试
  python3 run_density_stage1.py --dry-run    # 只统计不写入
"""
import sys, os, json, logging, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from keyword_density_engine import KeywordDensityEngine
import psycopg2
import psycopg2.extras as pge

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("stage1")

PG = "host=192.168.3.14 dbname=guludan user=guludan password=toto"
BATCH_SIZE = 500  # 每批commit

def get_pending_events(cur, limit=None):
    """获取待处理事件（有full_text且未在nlp_event_scores中）"""
    sql = """
        SELECT e.event_id, e.title, e.full_text, e.source
        FROM event_list e
        LEFT JOIN nlp_event_scores n ON e.event_id = n.event_id
        WHERE e.event_date >= '2019-01-01'
          AND e.event_date <= '2024-12-31'
          AND e.full_text IS NOT NULL
          AND n.event_id IS NULL
        ORDER BY e.event_id
    """
    if limit:
        sql += f" LIMIT {limit}"
    cur.execute(sql)
    return cur.fetchall()

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="限制处理条数")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = parser.parse_args()
    
    # 初始化引擎
    engine = KeywordDensityEngine()
    log.info(f"引擎就绪: {engine._keyword_count}关键词, {engine._industry_count}行业")
    
    conn = psycopg2.connect(PG)
    cur = conn.cursor()
    
    events = get_pending_events(cur, limit=args.limit)
    total = len(events)
    log.info(f"待处理事件: {total}个")
    
    if args.dry_run:
        conn.close()
        return
    
    if total == 0:
        log.info("无待处理事件")
        conn.close()
        return
    
    # 批量处理
    start_time = time.time()
    processed = 0
    skipped = 0
    
    # 准备插入语句（批量用execute_values）
    insert_sql = """
        INSERT INTO nlp_event_scores
            (event_id, direction, macro_score, industry_json,
             importance_source, importance_scope, importance_level,
             importance_total, keyword_weights_json, version)
        VALUES %s
        ON CONFLICT (event_id) DO UPDATE SET
            direction=EXCLUDED.direction,
            macro_score=EXCLUDED.macro_score,
            industry_json=EXCLUDED.industry_json,
            importance_source=EXCLUDED.importance_source,
            importance_scope=EXCLUDED.importance_scope,
            importance_level=EXCLUDED.importance_level,
            importance_total=EXCLUDED.importance_total,
            keyword_weights_json=EXCLUDED.keyword_weights_json,
            updated_at=NOW()
    """
    
    batch = []
    for r in events:
        eid, title, full_text, source = r
        
        result = engine.analyze_text(
            text=full_text,
            title=title or "",
            source=source or "",
            doc_id=eid
        )
        
        # 跳过无关键词匹配的事件
        if not result.keyword_hits:
            skipped += 1
            processed += 1
            continue
        
        batch.append((
            result.doc_id, result.direction, result.macro_score,
            json.dumps(result.industry_scores, ensure_ascii=False),
            result.importance_source, result.importance_scope,
            result.importance_level, result.importance_total,
            json.dumps(result.keyword_hits, ensure_ascii=False),
            result.version
        ))
        
        processed += 1
        
        if len(batch) >= BATCH_SIZE:
            pge.execute_values(cur, insert_sql, batch, page_size=200)
            conn.commit()
            batch = []
            elapsed = (time.time() - start_time) / 60
            log.info(f"进度: {processed}/{total} (+{len(batch)} 批) 耗时{elapsed:.1f}分 速率{processed/elapsed:.0f}/分")
    
    if batch:
        pge.execute_values(cur, insert_sql, batch, page_size=200)
        conn.commit()
    
    elapsed = (time.time() - start_time) / 60
    log.info(f"=== 完成 ===")
    log.info(f"总处理: {processed}, 写入: {processed - skipped}, 跳过(无关键词): {skipped}")
    log.info(f"耗时: {elapsed:.1f}分, 速率: {processed/elapsed:.0f}条/分")
    
    # 统计
    cur.execute("SELECT COUNT(*) FROM nlp_event_scores")
    r = cur.fetchone()
    log.info(f"nlp_event_scores 总计: {r[0]}条")
    
    conn.close()

if __name__ == "__main__":
    main()
