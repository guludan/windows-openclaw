#!/usr/bin/env python3
"""
板块动量指数 S_mom — 分歧动力学模型(DDM)核心
S_mom = w1×vol_norm + w2×turn_norm + w3×diverge_norm

数据源：industry_daily（同花顺90个行业，2015年起）
输出：market_signals(sector_momentum / sector_mom_signal)

改动历史：
- 2026-05-21 v1: 从旧 sector_r.py 改造，放弃 R=1/(w·x) 改为正向 S_mom=w·x
"""
import os, sys, argparse, json
from datetime import datetime, timedelta, date
import numpy as np

sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/scripts'))
import data_finance as df
import psycopg2
import psycopg2.extras

LOOKBACK = 60  # 基础窗口（多尺度用 S_short/long 参数覆盖）

# ─── 旧板块R相关signal_type，即将迁移 ───
_DEPRECATED_SIGNALS = ['sector_r_index', 'sector_r_signal']


def _jsonsafe(obj):
    if isinstance(obj, dict): return {k: _jsonsafe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)): return [_jsonsafe(v) for v in obj]
    if isinstance(obj, np.floating): return float(obj)
    if isinstance(obj, np.integer): return int(obj)
    return obj


def _get_conn():
    return psycopg2.connect(
        host=df.PG_HOST, port=df.PG_PORT,
        dbname=df.PG_DB, user=df.PG_USER, password=df.PG_PASS
    )


def _pg_executemany(sql, rows):
    """批量写入（用原生psycopg2）"""
    conn = _get_conn()
    cur = conn.cursor()
    psycopg2.extras.execute_values(cur, sql, rows, template=None, page_size=500)
    conn.commit()
    cur.close()
    conn.close()


def compute_s_mom(full_backfill=False):
    """板块动量 S_mom
    S_mom = w1*vol_norm + w2*turn_norm + w3*diverge_norm
    先等权 (1/3,1/3,1/3)，后续换自适应岭回归
    """
    # 获取日期范围
    if full_backfill:
        r,_ = df._pg_execute("SELECT DISTINCT trade_date FROM industry_daily ORDER BY trade_date")
        all_dates = [x[0] for x in r]
    else:
        existing,_ = df._pg_execute(
            "SELECT DISTINCT trade_date FROM market_signals WHERE signal_type='sector_momentum' ORDER BY trade_date"
        )
        last_date = existing[-1][0] if existing else None
        if not last_date:
            r,_ = df._pg_execute("SELECT DISTINCT trade_date FROM industry_daily ORDER BY trade_date")
            all_dates = [x[0] for x in r]
        else:
            r,_ = df._pg_execute(
                "SELECT DISTINCT trade_date FROM industry_daily WHERE trade_date>%s ORDER BY trade_date",
                (last_date,)
            )
            all_dates = [x[0] for x in r]

    if not all_dates:
        print("  无新日期")
        return

    print(f"  日期: {all_dates[0]} ~ {all_dates[-1]} ({len(all_dates)}天)")

    industries,_ = df._pg_execute("SELECT DISTINCT industry FROM industry_daily ORDER BY industry")
    print(f"  行业: {len(industries)}个")

    # 等权权重
    W_VOL, W_TURN, W_DIVERGE = 1/3, 1/3, 1/3

    conn = _get_conn()
    cur = conn.cursor()
    written = 0
    td_set = set(all_dates)

    for idx, (ind,) in enumerate(industries):
        r,_ = df._pg_execute(
            "SELECT trade_date, close, amount FROM industry_daily WHERE industry=%s ORDER BY trade_date",
            (ind,)
        )
        if not r:
            continue

        dates = [x[0] for x in r]
        closes = np.array([float(x[1]) if x[1] else 0 for x in r])
        amounts = np.array([float(x[2]) if x[2] else 0 for x in r])

        pct = np.diff(closes) / np.maximum(closes[:-1], 0.01) * 100
        pct = np.insert(pct, 0, 0)

        n = len(dates)
        s_mom = np.full(n, np.nan)

        for i in range(n):
            w_start = max(0, i - LOOKBACK + 1)
            w_vol = pct[w_start:i+1]
            w_amt = amounts[w_start:i+1]

            if len(w_vol) >= 5:
                vol_pct = np.mean(w_vol < abs(pct[i]))
            else:
                vol_pct = 0.5

            if len(w_amt) >= 5:
                turn_pct = np.mean(w_amt < amounts[i])
            else:
                turn_pct = 0.5

            diverge = np.mean(abs(w_vol) > 5) if len(w_vol) >= 5 else 0
            diverge_pct = min(diverge * 2, 1.0)

            # S_mom = 正向加权得分（不再取倒数）
            s_mom[i] = W_VOL * vol_pct + W_TURN * turn_pct + W_DIVERGE * diverge_pct

        # 写入
        batch_rows = []
        for i, td in enumerate(dates):
            if td not in td_set or np.isnan(s_mom[i]):
                continue
            sm = float(s_mom[i])

            # 当日横截面百分位（对全行业算，此处只存原始值，后面统一算百分位）
            extra = json.dumps(_jsonsafe({
                'industry': ind,
                'vol_norm': round(float(np.mean(abs(pct[max(0,i-LOOKBACK+1):i+1]) if i>=LOOKBACK else pct[:i+1])), 4) if i>=1 else 0,
                'turn_norm': round(float(amounts[i]), 2) if amounts[i] else 0,
                'diverge_norm': round(diverge_pct, 4),
                's_mom_raw': round(sm, 4),
            }))
            batch_rows.append((ind, td, sm, extra))

        if batch_rows:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO market_signals(signal_type,code,trade_date,value,extra) VALUES %s "
                "ON CONFLICT (signal_type,code,trade_date) DO UPDATE SET value=EXCLUDED.value,extra=EXCLUDED.extra",
                [('sector_momentum', r[0], r[1], r[2], r[3]) for r in batch_rows],
                template="(%s, %s, %s::date, %s, %s::jsonb)",
                page_size=500
            )
            written += len(batch_rows)
            conn.commit()

        if (idx+1) % 10 == 0:
            print(f"  进度 {idx+1}/{len(industries)} 行业 ({written}条)")

    cur.close()
    conn.close()
    print(f"  ✅ 写入 {written} 条 S_mom")


def generate_mom_signal(target_date=None):
    """基于 S_mom 横截面百分位生成买卖信号（DDM版）

    信号映射（与旧方案相反，S_mom高=动量强=趋势持续）：
      P80-100: buy_momentum（趋势追入）
      P20-40:  watch
      P0-20:   weak_avoid（弱势回避）
    """
    if target_date is None:
        r,_ = df._pg_execute(
            "SELECT trade_date FROM market_signals WHERE signal_type='sector_momentum' ORDER BY trade_date DESC LIMIT 1"
        )
        if not r: return
        target_date = r[0][0]

    ds = target_date.strftime('%Y-%m-%d') if hasattr(target_date, 'strftime') else str(target_date)

    r,_ = df._pg_execute(
        "SELECT code, value FROM market_signals WHERE signal_type='sector_momentum' AND trade_date=%s",
        (ds,)
    )
    if not r:
        print(f"  {ds} 无板块动量数据")
        return

    values = sorted([float(x[1]) for x in r])
    n = len(values)
    p20 = np.percentile(values, 20)
    p40 = np.percentile(values, 40)
    p60 = np.percentile(values, 60)
    p80 = np.percentile(values, 80)

    batch = []
    for code, val in r:
        val = float(val)
        pct = sum(1 for v in values if v <= val) / n

        if val >= p80:
            signal = 'buy_momentum'
        elif val >= p60:
            signal = 'watch'
        elif val >= p40:
            signal = 'neutral'
        else:
            signal = 'weak_avoid'

        extra = json.dumps({
            'industry': code, 's_mom': round(val, 4),
            'percentile': round(pct, 4), 'signal': signal
        })
        batch.append((code, ds, round(pct, 4), extra))

    if batch:
        conn = _get_conn()
        cur = conn.cursor()
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO market_signals(signal_type,code,trade_date,value,extra) VALUES %s "
            "ON CONFLICT (signal_type,code,trade_date) DO UPDATE SET value=EXCLUDED.value,extra=EXCLUDED.extra",
            [('sector_mom_signal', r[0], r[1], r[2], r[3]) for r in batch],
            template="(%s, %s, %s::date, %s, %s::jsonb)",
            page_size=500
        )
        conn.commit()
        cur.close()
        conn.close()
    print(f"  ✅ 板块动量信号: {len(batch)}条 ({ds})")


def cleanup_deprecated():
    """清理旧版板块R信号"""
    for sig in _DEPRECATED_SIGNALS:
        conn = _get_conn()
        cur = conn.cursor()
        r,_ = df._pg_execute("SELECT COUNT(*) FROM market_signals WHERE signal_type=%s", (sig,))
        cnt = r[0][0]
        if cnt > 0:
            cur.execute("DELETE FROM market_signals WHERE signal_type=%s", (sig,))
            conn.commit()
            print(f"  🗑️ 已删除 {sig}: {cnt}条")
        cur.close()
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['backfill', 'incremental', 'signal', 'cleanup'], default='incremental')
    args = parser.parse_args()

    if args.mode == 'backfill':
        compute_s_mom(full_backfill=True)
    elif args.mode == 'incremental':
        compute_s_mom(full_backfill=False)
    elif args.mode == 'signal':
        generate_mom_signal()
    elif args.mode == 'cleanup':
        cleanup_deprecated()
