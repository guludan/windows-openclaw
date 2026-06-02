#!/usr/bin/env python3
"""
R指数 v3 — 大盘+个股级别心理距离/信息密度计算

核心公式（大盘）：
  R = 1 / (0.35×vol_norm + 0.25×turn_norm + 0.40×diverge_norm)
  vol_norm = 截面波动率百分位（0~1）
  turn_norm = 截面平均成交量百分位（0~1）
  diverge_norm = |涨跌比 - 0.5| × 2

核心公式（个股，方案A）：
  raw_div = turnover / max(|change_pct|, 0.01)  # 当日用真实turnover
  div_norm = z-score(raw_div, 60日历史 via daily_prices volume×close)
  amp_norm = z-score(振幅, 60日历史)
  R = 1 / (0.5×div_norm_01 + 0.5×amp_norm_01 + 0.01)

输出到：market_signals
  - r_index: 大盘R指数
  - ba_surplus: [B]-[A]浓度盈余
  - r_warning: 大盘预警
  - r_stock_index: 个股R指数
  - r_stock_high_div: 高分歧个股
"""
import os, sys, json, argparse
from datetime import datetime, timedelta, date
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
import data_finance as df


def _jsonsafe(obj):
    """递归将numpy类型转为Python原生类型（JSON兼容）"""
    if isinstance(obj, dict):
        return {k: _jsonsafe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonsafe(v) for v in obj]
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


# ================================================================
# 大盘R指数 — PG窗口函数版
# ================================================================
def compute_market_r(target_dates=None, lookback=60):
    """
    大盘R指数 — 全量回填（增量模式）
    
    用PG窗口函数一次性算所有日的截面统计和滚动百分位：
    1. CTE1: 每日截面统计 (change_pct从close/open算)
    2. CTE2: 60日滚动z-score标准化
    3. CTE3: 百分位映射 + 合成R指数
    4. 仅回填missing的日期
    """
    if target_dates is None:
        existing, _ = df._pg_execute(
            "SELECT DISTINCT trade_date FROM market_signals WHERE signal_type='r_index' ORDER BY trade_date"
        )
        if existing:
            last_date = existing[-1][0]
            row, _ = df._pg_execute(
                "SELECT trade_date FROM daily_prices WHERE trade_date>%s AND close>0 AND volume>0 LIMIT 1", (last_date,)
            )
            if not row:
                print("  没有新日期需要计算")
                return []
            target_dates, _ = df._pg_execute(
                "SELECT DISTINCT trade_date FROM daily_prices WHERE trade_date>%s AND close>0 AND volume>0 ORDER BY trade_date",
                (last_date,)
            )
            target_dates = [r[0] for r in target_dates]
        else:
            target_dates, _ = df._pg_execute(
                "SELECT DISTINCT trade_date FROM daily_prices WHERE close>0 AND volume>0 ORDER BY trade_date"
            )
            target_dates = [r[0] for r in target_dates]
    
    if not target_dates:
        print("  无可用的交易日期")
        return []
    
    start_ds = (target_dates[0] - timedelta(days=lookback)).strftime('%Y-%m-%d')
    end_ds = target_dates[-1].strftime('%Y-%m-%d')
    print(f"  交易日期: {target_dates[0]} ~ {target_dates[-1]} ({len(target_dates)}天)")
    
    pg_sql = """
    WITH daily_stats AS (
        SELECT trade_date,
               COUNT(*) AS stock_count,
               STDDEV((close - open) / NULLIF(open, 0) * 100) AS cs_vol,
               AVG(volume) AS avg_volume,
               AVG(amount) AS avg_amount,
               SUM(CASE WHEN (close - open) / NULLIF(open, 0) > 0 THEN 1 ELSE 0 END)::float
                    / NULLIF(COUNT(*), 0) AS advance_ratio,
               SUM(CASE WHEN (close - open) / NULLIF(open, 0) * 100 > 2 THEN 1 ELSE 0 END)::float
                    / NULLIF(COUNT(*), 0) AS strong_bull,
               SUM(CASE WHEN (close - open) / NULLIF(open, 0) * 100 < -2 THEN 1 ELSE 0 END)::float
                    / NULLIF(COUNT(*), 0) AS strong_bear,
               AVG((close - open) / NULLIF(open, 0) * 100) AS avg_pct,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY (close - open) / NULLIF(open, 0) * 100) AS median_pct
        FROM daily_prices
        WHERE trade_date >= %(start_ds)s AND trade_date <= %(end_ds)s
          AND close > 0 AND volume > 0 AND open > 0
        GROUP BY trade_date
    ),
    normed AS (
        SELECT *,
               (cs_vol - AVG(cs_vol) OVER (ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW))
               / NULLIF(STDDEV(cs_vol) OVER (ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW), 0) AS vol_z,
               (avg_volume - AVG(avg_volume) OVER (ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW))
               / NULLIF(STDDEV(avg_volume) OVER (ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW), 0) AS turn_z,
               advance_ratio - 0.5 AS diverge_raw,
               ROW_NUMBER() OVER (ORDER BY cs_vol)::float / NULLIF(COUNT(*) OVER (), 0) AS vol_pct,
               ROW_NUMBER() OVER (ORDER BY avg_volume)::float / NULLIF(COUNT(*) OVER (), 0) AS turn_pct
        FROM daily_stats
    )
    SELECT trade_date, stock_count, cs_vol, avg_volume, avg_amount,
           advance_ratio, strong_bull, strong_bear, avg_pct, median_pct,
           vol_z, turn_z,
           ABS(diverge_raw) * 2 AS diverge_norm,
           vol_pct, turn_pct,
           1.0 / NULLIF(vol_pct * 0.35 + turn_pct * 0.25 + ABS(diverge_raw) * 2 * 0.40, 0) AS r_index,
           advance_ratio * (1 + GREATEST(avg_pct, 0) * 2) AS b_raw,
           (1 - advance_ratio) * (1 + GREATEST(-avg_pct, 0) * 2) AS a_raw
    FROM normed
    WHERE trade_date >= %(start_target)s
    ORDER BY trade_date
    """
    
    rows, _ = df._pg_execute(pg_sql, {
        'start_ds': start_ds,
        'end_ds': end_ds,
        'start_target': target_dates[0].strftime('%Y-%m-%d'),
    })
    if not rows:
        print("  PG查询结果为空")
        return []
    
    print(f"  PG计算完成: {len(rows)} 天")
    
    results = []
    INSERT_SQL = """INSERT INTO market_signals (signal_type, code, trade_date, value, extra, updated_at)
       VALUES (%s,%s,%s,%s,%s::jsonb,NOW())
       ON CONFLICT (signal_type,code,trade_date)
       DO UPDATE SET value=EXCLUDED.value,extra=EXCLUDED.extra,updated_at=NOW()"""
    
    r_hist = []
    for r in rows:
        td = r[0]; stock_count = int(r[1]); cs_vol = float(r[2] or 0)
        avg_vol = float(r[3] or 0); avg_amt = float(r[4] or 0)
        adv_ratio = float(r[5] or 0.5); s_bull = float(r[6] or 0); s_bear = float(r[7] or 0)
        avg_pct = float(r[8] or 0); med_pct = float(r[9] or 0)
        vol_z = float(r[10] or 0); turn_z = float(r[11] or 0)
        diverge_norm = float(r[12] or 0.5); vol_pct = float(r[13] or 0.5); turn_pct = float(r[14] or 0.5)
        r_index = float(r[15] or 1.0); b_raw = float(r[16] or 0); a_raw = float(r[17] or 0)
        
        total = b_raw + a_raw
        b_norm = round(b_raw / total, 4) if total > 0 else 0.5
        a_norm = round(1.0 - b_norm, 4)
        surplus = round(b_norm - a_norm, 4)
        
        # 滚动百分位（Python侧）
        r_hist.append(r_index)
        recent = r_hist[-lookback:]
        pct_rank = sum(1 for x in recent if x <= r_index) / len(recent) if recent else 0.5
        
        warning = None
        if len(recent) >= 10:
            if pct_rank < 0.20 and np.mean(recent[-3:]) < np.mean(r_hist[-min(10, len(r_hist)):]):
                warning = 'low_r_consensus'
            if pct_rank > 0.80 and r_index > np.mean(recent[-5:]):
                warning = 'high_r_divergence'
        
        extra = _jsonsafe({
            'vol_z': round(vol_z, 4), 'turn_z': round(turn_z, 4),
            'diverge_norm': round(diverge_norm, 4), 'cs_vol': round(cs_vol, 4),
            'advance_ratio': round(adv_ratio, 4), 'avg_pct': round(avg_pct, 4),
            'strong_bull': round(s_bull, 4), 'strong_bear': round(s_bear, 4),
            'median_pct': round(med_pct, 4), 'stock_count': stock_count,
            'avg_volume': round(avg_vol, 0), 'avg_amount': round(avg_amt, 0),
            'r_pct': round(pct_rank, 4), 'b_conc': b_norm, 'a_conc': a_norm,
            'surplus': surplus, 'vol_pct': round(vol_pct, 4), 'turn_pct': round(turn_pct, 4),
            'type': 'r_index'
        })
        
        try:
            df._pg_execute(INSERT_SQL,
                ('r_index', 'market', td, r_index, json.dumps(extra, ensure_ascii=False)), fetch=False
            )
            df._pg_execute(INSERT_SQL,
                ('ba_surplus', 'market', td, surplus,
                 json.dumps({**extra, 'type': 'ba_surplus', 'b_conc': b_norm, 'a_conc': a_norm}, ensure_ascii=False)),
                fetch=False
            )
            if warning:
                df._pg_execute(INSERT_SQL,
                    ('r_warning', 'market', td, r_index,
                     json.dumps({'type': 'r_warning', 'warning': warning, 'r_value': r_index,
                                'r_pct': round(pct_rank, 4), **extra}, ensure_ascii=False)),
                    fetch=False
                )
        except Exception as e:
            print(f"  写入失败 {td}: {e}")
        
        results.append(dict(date=td, r_index=r_index, r_pct=round(pct_rank, 4),
                           b_conc=b_norm, a_conc=a_norm, surplus=surplus,
                           advance_ratio=adv_ratio, warning=warning))
    
    return results


# ================================================================
# 大盘R指数 — 全量回填入口
# ================================================================
def backfill_market_r(start_year=2005, lookback=60):
    """全量回填 old_v1 → PG窗口函数新版"""
    existing, _ = df._pg_execute(
        "SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM market_signals WHERE signal_type='r_index'"
    )
    if existing[2] and existing[2] > 0:
        print(f"  market_r 已有 {existing[2]} 条 ({existing[0]} ~ {existing[1]})")
        print("  增量模式: 只算新日期")
    else:
        print("  从零开始全量计算...")
    
    return compute_market_r()


# ================================================================
# 个股R指数（方案A）
# ================================================================
def compute_stock_r(trade_date, top_n=50):
    """
    个股R指数（方案A）
    
    当日数据: market_snapshots 的真实 turnover / |change_pct|
    历史数据: daily_prices 的 volume × close 做成交额代理
    通过60日滚动z-score标准化 + 振幅z-score 合成R
    """
    lookback = 60
    ds = trade_date.strftime('%Y-%m-%d')
    start_ds = (trade_date - timedelta(days=lookback)).strftime('%Y-%m-%d')
    
    # 当日数据：market_snapshots
    today_rows, _ = df._pg_execute("""
        SELECT s.code, s.change_pct, s.turnover, s.amount, s.amplitude, s.price,
               COALESCE(l.name, '') AS name
        FROM market_snapshots s
        LEFT JOIN stock_list l ON l.code = s.code
        WHERE s.trade_date = %s
          AND s.change_pct IS NOT NULL AND s.turnover IS NOT NULL AND s.turnover > 0
    """, (ds,))
    
    if not today_rows:
        # fallback: daily_prices
        today_rows, _ = df._pg_execute("""
            SELECT p.symbol, (p.close-p.open)/NULLIF(p.open,0)*100, 0::real, p.amount,
                   (p.high-p.low)/NULLIF(p.open,0)*100, p.close, COALESCE(l.name,'')
            FROM daily_prices p
            LEFT JOIN stock_list l ON l.code = p.symbol
            WHERE p.trade_date = %s AND p.close>0 AND p.volume>0
        """, (ds,))
    
    if not today_rows:
        print(f"  {ds}: 无当日行情数据")
        return []
    
    today_info = {}
    for r in today_rows:
        today_info[r[0]] = {
            'chg': r[1] or 0, 'turn': r[2] or 0, 'amt': r[3] or 0,
            'amp': r[4] or 0, 'close': r[5] or 0, 'name': r[6] or ''
        }
    
    codes = list(today_info.keys())
    
    # 从daily_prices拉历史数据（带LAG算出change_pct）
    hist_raw, _ = df._pg_execute("""
        WITH ranked AS (
            SELECT symbol, trade_date, close, volume, amount, open, high, low,
                   LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date) AS prev_close
            FROM daily_prices
            WHERE trade_date >= %s AND trade_date < %s AND close>0 AND volume>0
        )
        SELECT symbol, trade_date, close, volume,
               CASE WHEN prev_close>0 THEN (close-prev_close)/prev_close*100 ELSE 0 END AS change_pct,
               (high-low)/NULLIF(open,0)*100 AS amplitude
        FROM ranked
        ORDER BY symbol, trade_date
    """, (start_ds, ds))
    
    hist = defaultdict(list)
    for r in hist_raw:
        c = r[0]
        if c in today_info:
            chg = r[4] or 0; vol = r[3] or 0; close = r[2] or 0; amp = r[5] or 0
            if chg != 0 and vol > 0 and close > 0:
                hist[c].append({'chg': chg, 'vol': vol, 'close': close, 'amp': amp})
    
    stock_results = []
    min_std = 1e-6
    INSERT_SQL = """INSERT INTO market_signals (signal_type, code, trade_date, value, extra, updated_at)
       VALUES (%s,%s,%s,%s,%s::jsonb,NOW())
       ON CONFLICT (signal_type,code,trade_date)
       DO UPDATE SET value=EXCLUDED.value,extra=EXCLUDED.extra,updated_at=NOW()"""
    
    for code in codes:
        i = today_info[code]
        if i['chg'] == 0:
            continue
        
        # 当日raw_div: 有真实turnover就用，否则volume*close代理
        if i['turn'] and i['turn'] > 0:
            raw_div = i['turn'] / max(abs(i['chg']), 0.01)
        else:
            raw_div = 0.5
        raw_amp = i['amp'] or 0
        
        code_hist = hist.get(code, [])
        if code_hist:
            hist_divs = [h['vol'] * h['close'] / max(abs(h['chg']), 0.01) for h in code_hist]
            hist_amps = [h['amp'] for h in code_hist]
        else:
            hist_divs, hist_amps = [], []
        
        if len(hist_divs) >= 10:
            mu_div = float(np.mean(hist_divs))
            sigma_div = max(float(np.std(hist_divs)), min_std)
            div_norm = max(-3.0, min(3.0, (raw_div - mu_div) / sigma_div))
            div_norm_01 = (div_norm + 3.0) / 6.0
            
            mu_amp = float(np.mean(hist_amps))
            sigma_amp = max(float(np.std(hist_amps)), min_std)
            amp_norm = max(-3.0, min(3.0, (raw_amp - mu_amp) / sigma_amp))
            amp_norm_01 = (amp_norm + 3.0) / 6.0
        else:
            div_norm_01 = 0.5
            amp_norm_01 = 0.5
        
        r_inv = 0.5 * div_norm_01 + 0.5 * amp_norm_01 + 0.01
        r_stock = round(1.0 / r_inv, 4)
        
        stock_results.append(dict(
            code=code, name=i['name'], r_stock=r_stock,
            div_norm=round(div_norm_01, 4), amp_norm=round(amp_norm_01, 4),
            raw_div=raw_div, chg=round(i['chg'], 2), amp=round(raw_amp, 2),
            hist_len=len(hist_divs)
        ))
    
    if not stock_results:
        return []
    
    stock_results.sort(key=lambda x: x['r_stock'], reverse=True)
    
    for item in stock_results[:top_n]:
        extra = _jsonsafe({
            'name': item['name'], 'r_stock': item['r_stock'],
            'div_norm': item['div_norm'], 'amp_norm': item['amp_norm'],
            'raw_div': item['raw_div'], 'change_pct': item['chg'],
            'amp': item['amp'], 'hist_len': item['hist_len'],
            'type': 'r_stock'
        })
        try:
            df._pg_execute(INSERT_SQL,
                ('r_stock_index', item['code'], ds, item['r_stock'],
                 json.dumps(extra, ensure_ascii=False)), fetch=False
            )
        except:
            pass
    
    # 高分歧个股
    high_div = [s for s in stock_results if s['div_norm'] > 0.70 or s['amp_norm'] > 0.70][:top_n]
    for item in high_div:
        extra = _jsonsafe({
            'name': item['name'], 'r_stock': item['r_stock'],
            'div_norm': item['div_norm'], 'amp_norm': item['amp_norm'],
            'type': 'r_stock_high_div'
        })
        try:
            df._pg_execute(INSERT_SQL,
                ('r_stock_high_div', item['code'], ds, item['r_stock'],
                 json.dumps(extra, ensure_ascii=False)), fetch=False
            )
        except:
            pass
    
    return stock_results


# ================================================================
# 打印摘要
# ================================================================
def print_market_summary(results):
    if not results:
        return
    latest = results[-1]
    print(f"\n📊 大盘R指数 — 最新: {latest['date']}")
    print(f"  R指数={latest['r_index']:.2f} (百分位={latest['r_pct']:.0%})")
    print(f"  [B]={latest['b_conc']:.1%} [A]={latest['a_conc']:.1%} 盈余={latest['surplus']:+.1%}")
    print(f"  涨跌比={latest['advance_ratio']:.1%}")
    if latest['warning']:
        print(f"  ⚠️ 预警: {latest['warning']}")
    print(f"\n📈 最近5天:")
    h = f"{'日期':<12} {'R指数':<8} {'R%分位':<8} {'B-A盈余':<10} {'涨跌比':<8} {'预警':<10}"
    print(h)
    for r in results[-5:]:
        w = r['warning'] or '-'
        print(f"{str(r['date']):<12} {r['r_index']:<8.2f} {r['r_pct']:<8.0%} {r['surplus']:<+10.1%} {r['advance_ratio']:<8.1%} {w:<10}")


# ================================================================
# 入口
# ================================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['market', 'stock', 'stock-backfill', 'all'], default='all')
    parser.add_argument('--days', type=int, default=120)
    parser.add_argument('--stock-days', type=int, default=5)
    parser.add_argument('--end-date', type=str, default=None)
    args = parser.parse_args()
    
    end_date = datetime.strptime(args.end_date, '%Y%m%d').date() if args.end_date else date.today()
    start_date = end_date - timedelta(days=args.days)
    
    print(f"📡 R指数 v3 (PG窗口函数)")
    print(f"   大盘R = 1/(vol_pct×0.35 + turn_pct×0.25 + diverge_norm×0.40)")
    print(f"   个股R = turnover/|chg| 60dz-score 合成")
    
    if args.mode in ('market', 'all'):
        print("\n📥 大盘R指数 (增量)...")
        results = compute_market_r()
        print_market_summary(results)
    
    if args.mode in ('stock', 'stock-backfill', 'all'):
        print("\n📥 个股R指数...")
        snap_dates, _ = df._pg_execute(
            "SELECT DISTINCT trade_date FROM market_snapshots WHERE trade_date>=%s AND trade_date<=%s ORDER BY trade_date",
            (max(start_date, end_date - timedelta(days=args.stock_days)), end_date)
        )
        ds_list = [r[0] for r in snap_dates]
        
        if args.mode == 'stock-backfill':
            # 从daily_prices回填
            ds_list, _ = df._pg_execute(
                "SELECT DISTINCT trade_date FROM daily_prices WHERE trade_date>=%s AND trade_date<=%s AND close>0 AND volume>0 ORDER BY trade_date",
                (start_date, end_date)
            )
            ds_list = [r[0] for r in ds_list]
            print(f"  回填模式: {len(ds_list)} 天")
        
        for td in ds_list:
            top = compute_stock_r(td, top_n=50)
            if top:
                high = sum(1 for s in top if s['div_norm'] > 0.7 or s['amp_norm'] > 0.7)
                print(f"  {td}: {len(top)}只, 高分歧{high}只")
                for s in top[:5]:
                    print(f"    {s['code']}({s['name']}): R={s['r_stock']:.1f} "
                          f"div={s['div_norm']:.2f} amp={s['amp_norm']:.2f} chg={s['chg']:+.1f}%")
    
    if args.mode == 'all':
        print(f"\n{'='*60}")
        print("生成综合简报...")
        os.system(f'{sys.executable} {os.path.join(os.path.dirname(__file__), "daily_signals.py")}')
    
    print("\n✅ 完成")
