#!/usr/bin/env python3
"""
事件研究法 + 衰减系数拟合模块 v0.1

流程：
  1. 从event_list读取事件，从industry_daily获取行业日收益率
  2. 对每个事件-行业组合计算异常收益(AR)和累积异常收益(CAR)
  3. 用事件信息势能（来自keyword_density_engine）拟合衰减系数λ
  4. 支持NLS和网格搜索两种拟合方法

用法：
  python3 event_study.py --compute-all         # 批量计算所有事件的CAR
  python3 event_study.py --fit-lambda           # 拟合衰减系数λ
  python3 event_study.py --fit-importance       # 拟合重要性权重
  python3 event_study.py --list-events          # 列出已有事件
  python3 event_study.py --test-event 1         # 测试单个事件

依赖：
  keyword_density_engine.py, scipy, numpy, pandas, statsmodels, asyncpg
"""

import asyncio, json, sys, os, argparse, logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('event_study')

# ─── PG连接信息 ───
PG_DSN = "postgresql://guludan:toto@192.168.3.14:5432/guludan"

# ─── 事件窗口参数 ───
ESTIMATION_WINDOW = (-120, -11)   # 估计窗 [-120, -11] 交易日
EVENT_WINDOW = (0, 60)            # 事件窗 [0, +60] 交易日（CAR超短期看+20）
CAR_WINDOWS = [5, 10, 20, 60]     # 要计算的CAR窗口


# ═══════════════════════════════════════
#   数据库操作
# ═══════════════════════════════════════

async def fetch_events(conn) -> list:
    """获取事件列表（含NLP分数）"""
    rows = await conn.fetch("""
        SELECT e.event_id, e.event_date, e.trade_date, e.source, e.title,
               e.direction, e.full_text, e.source_subtype,
               s.direction as nlp_dir, s.macro_score,
               s.industry_json, s.importance_total
        FROM event_list e
        LEFT JOIN nlp_event_scores s ON e.event_id = s.event_id
        ORDER BY e.event_date
    """)
    return rows


async def fetch_industry_daily(conn, start_date, end_date) -> pd.DataFrame:
    """获取行业日收益率"""
    rows = await conn.fetch("""
        SELECT trade_date, industry_code, industry_name, pct_chg, amount
        FROM industry_daily
        WHERE trade_date >= $1 AND trade_date <= $2
        ORDER BY industry_code, trade_date
    """, start_date, end_date)

    df = pd.DataFrame(rows, columns=['trade_date', 'industry_code', 'industry_name',
                                       'pct_chg', 'amount'])
    return df


async def fetch_market_daily(conn, start_date, end_date) -> pd.DataFrame:
    """获取市场（中证全指或沪深300）收益率"""
    rows = await conn.fetch("""
        SELECT trade_date, pct_chg, amount
        FROM daily_prices
        WHERE code = '000985.SH' AND trade_date >= $1 AND trade_date <= $2
        ORDER BY trade_date
    """, start_date, end_date)

    df = pd.DataFrame(rows, columns=['trade_date', 'pct_chg', 'amount'])
    return df


async def fetch_trade_calendar(conn, start_date, end_date) -> List[str]:
    """获取交易日历"""
    rows = await conn.fetch("""
        SELECT DISTINCT trade_date
        FROM daily_prices
        WHERE trade_date >= $1 AND trade_date <= $2
        ORDER BY trade_date
    """, start_date, end_date)
    return [str(r['trade_date']) for r in rows]


async def save_event_car(conn, event_id: int, industry_code: str, industry_name: str,
                         ar_data: List[Tuple[int, str, float, float]]):
    """保存单行业CAR数据

    ar_data: [(t_relative, trade_date, ar, car), ...]
    """
    # 先删后插
    await conn.execute("""
        DELETE FROM event_car
        WHERE event_id = $1 AND industry_code = $2
    """, event_id, industry_code)

    for t_rel, td, ar, car in ar_data:
        # 计算各窗口CAR
        car_5 = car if 0 <= t_rel <= 5 else None
        car_10 = car if 0 <= t_rel <= 10 else None
        car_20 = car if 0 <= t_rel <= 20 else None
        car_60 = car if 0 <= t_rel <= 60 else None

        await conn.execute("""
            INSERT INTO event_car
                (event_id, industry_code, industry_name, t_relative, trade_date, ar, car,
                 car_5, car_10, car_20, car_60)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (event_id, industry_code, t_relative) DO UPDATE SET
                ar=EXCLUDED.ar, car=EXCLUDED.car,
                car_5=EXCLUDED.car_5, car_10=EXCLUDED.car_10,
                car_20=EXCLUDED.car_20, car_60=EXCLUDED.car_60
        """, event_id, industry_code, industry_name,
            t_rel, td, ar, car, car_5, car_10, car_20, car_60)


# ═══════════════════════════════════════
#   事件研究核心
# ═══════════════════════════════════════

def compute_abnormal_return(
    ind_returns: pd.Series,
    mkt_returns: pd.Series,
    est_start: int,
    est_end: int,
    evt_start: int,
    evt_end: int
) -> Tuple[Dict[int, float], Dict[int, float], float, float, float]:
    """用市场模型计算异常收益

    Args:
        ind_returns: 行业收益率全序列
        mkt_returns: 市场收益率全序列
        est_start/end: 估计窗下标（相对于序列起点）
        evt_start/end: 事件窗下标

    Returns:
        (ar_dict, car_dict, alpha, beta, r_squared)
        ar/car_dict: {t_relative: value}
    """
    # 估计窗
    est_len = est_end - est_start
    if est_len < 50:
        log.warning(f"估计窗过短: {est_len}个交易日")
        return {}, {}, 0, 0, 0

    X = mkt_returns.iloc[est_start:est_end].values.astype(float)
    y = ind_returns.iloc[est_start:est_end].values.astype(float)

    # 去掉NaN
    valid = ~(np.isnan(X) | np.isnan(y))
    X = X[valid]
    y = y[valid]

    if len(X) < 40:
        return {}, {}, 0, 0, 0

    # OLS: y = α + βx
    X_design = np.column_stack([np.ones(len(X)), X])
    try:
        beta = np.linalg.lstsq(X_design, y, rcond=None)[0]
    except np.linalg.LinAlgError:
        return {}, {}, 0, 0, 0

    alpha, beta_mkt = beta[0], beta[1]

    # R²
    y_pred = X_design @ beta
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_sq = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0

    # 事件窗AR
    evt_len = evt_end - evt_start
    ar_dict = {}
    for i in range(evt_len):
        idx = evt_start + i
        if idx >= len(ind_returns) or idx >= len(mkt_returns):
            continue
        r_ind = ind_returns.iloc[idx]
        r_mkt = mkt_returns.iloc[idx]
        if np.isnan(r_ind) or np.isnan(r_mkt):
            continue
        ar = r_ind - (alpha + beta_mkt * r_mkt)
        ar_dict[i] = ar  # i=0对应事件日

    # 累积异常收益
    car_dict = {}
    cum = 0.0
    for i in range(evt_len):
        if i in ar_dict:
            cum += ar_dict[i]
        car_dict[i] = cum

    return ar_dict, car_dict, alpha, beta_mkt, r_sq


async def compute_event_car(conn, event_id: int, event_date: str, trade_date: str,
                            industry_df: pd.DataFrame, market_df: pd.DataFrame,
                            trade_cal: List[str]) -> dict:
    """对单个事件计算所有行业的CAR"""
    # 确定事件日在交易日历中的位置
    td = trade_date or event_date
    if td not in trade_cal:
        log.warning(f"事件 #{event_id} 日期 {td} 不在交易日历中")
        return {"event_id": event_id, "status": "skip_no_trade_date"}

    event_idx = trade_cal.index(td)

    # 估计窗范围
    est_start_idx = max(0, event_idx + ESTIMATION_WINDOW[1] + 1)  # -11
    est_end_idx = max(0, event_idx + ESTIMATION_WINDOW[0])        # -120
    # 注意：est_start是较早日期（-120），est_end是较晚日期（-11）
    est_lo = min(est_start_idx, est_end_idx)
    est_hi = max(est_start_idx, est_end_idx)

    # 事件窗范围
    evt_start_idx = event_idx + EVENT_WINDOW[0]
    evt_end_idx = min(len(trade_cal), event_idx + EVENT_WINDOW[1] + 1)

    if est_hi <= est_lo or evt_start_idx >= len(trade_cal):
        return {"event_id": event_id, "status": "skip_window_too_small"}

    # 准备行业+市场收益率对齐
    ind_codes = industry_df['industry_code'].unique()
    results_count = 0

    for icode in ind_codes:
        ind_data = industry_df[industry_df['industry_code'] == icode].sort_values('trade_date')
        ind_name = ind_data['industry_name'].iloc[0] if not ind_data.empty else icode

        # 对齐交易日
        ind_returns = []
        mkt_returns = []
        valid_dates = []

        for cal_date in trade_cal[est_lo:evt_end_idx]:
            ind_row = ind_data[ind_data['trade_date'] == pd.Timestamp(cal_date)]
            mkt_row = market_df[market_df['trade_date'] == pd.Timestamp(cal_date)]

            if ind_row.empty or mkt_row.empty:
                ind_returns.append(np.nan)
                mkt_returns.append(np.nan)
            else:
                ind_returns.append(float(ind_row['pct_chg'].iloc[0]))
                mkt_returns.append(float(mkt_row['pct_chg'].iloc[0]))
                valid_dates.append(cal_date)

        if len(valid_dates) < 50:
            continue

        ind_series = pd.Series(ind_returns)
        mkt_series = pd.Series(mkt_returns)

        # 下标偏移
        est_len = est_hi - est_lo
        evt_len = evt_end_idx - evt_start_idx
        est_start_local = 0
        est_end_local = est_len
        evt_start_local = est_len
        evt_end_local = est_len + evt_len

        ar_dict, car_dict, alpha, beta, r_sq = compute_abnormal_return(
            ind_series, mkt_series,
            est_start_local, est_end_local,
            evt_start_local, evt_end_local
        )

        if not ar_dict:
            continue

        # 构建数据
        ar_data = []
        for i in range(evt_len):
            if i not in ar_dict:
                continue
            rel_idx = evt_start_local + i
            if rel_idx < len(valid_dates):
                td_str = valid_dates[rel_idx]
                ar_data.append((i, td_str, ar_dict[i], car_dict.get(i, 0.0)))

        if ar_data:
            await save_event_car(conn, event_id, icode, ind_name, ar_data)
            results_count += 1

    return {
        "event_id": event_id,
        "status": "ok",
        "industries_computed": results_count,
        "alpha": alpha if 'alpha' in dir() else 0,
        "beta": beta if 'beta' in dir() else 0,
        "r_sq": r_sq if 'r_sq' in dir() else 0,
    }


# ═══════════════════════════════════════
#   衰减系数拟合
# ═══════════════════════════════════════

def fit_lambda_nls(car_data: np.ndarray, energy_data: np.ndarray,
                   t_array: np.ndarray,
                   initial_lambda: float = 0.15) -> dict:
    """非线性最小二乘法拟合λ

    模型: CAR(t) = β₁ · E · (1 - e^{-λt})

    Args:
        car_data: 各时间点的CAR值 (n_samples,)
        energy_data: 各样本的信息势能值 (n_samples,)
        t_array: 各样本的时间点（距事件日的天数）(n_samples,)

    Returns:
        {lambda, beta1, r_squared, ...}
    """
    from scipy.optimize import curve_fit

    def model(t, beta1, lam):
        return beta1 * energy_data * (1.0 - np.exp(-lam * t))

    p0 = [0.5, initial_lambda]
    bounds = ([0.001, 0.001], [10.0, 2.0])

    try:
        popt, pcov = curve_fit(
            lambda t, b1, lam: model(t, b1, lam),
            t_array, car_data, p0=p0, bounds=bounds,
            maxfev=5000
        )
        beta1_fit, lam_fit = popt
        perr = np.sqrt(np.diag(pcov))

        # R²
        residuals = car_data - model(t_array, beta1_fit, lam_fit)
        ss_res = np.sum(residuals ** 2)
        ss_tot = np.sum((car_data - np.mean(car_data)) ** 2)
        r_sq = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0

        return {
            "lambda": lam_fit,
            "lambda_se": perr[1],
            "beta1": beta1_fit,
            "beta1_se": perr[0],
            "r_squared": r_sq,
            "n": len(car_data),
            "method": "NLS",
        }
    except Exception as e:
        log.warning(f"NLS拟合失败: {e}")
        return {"error": str(e), "method": "NLS"}


def fit_lambda_grid(events: List[dict], t_max: int = 20,
                    lam_range: Tuple[float, float] = (0.01, 1.0),
                    lam_steps: int = 100) -> dict:
    """网格搜索法拟合λ（更稳健）

    对每个候选λ，计算 累积势能 = E × (1 - e^{-λ·t_max})，
    然后用这个值对 CAR[0, t_max] 做回归，取R²最大的λ。

    Args:
        events: [{"industry_scores": ..., "car_20": ..., "energy": ...}, ...]
        t_max: 事件窗终点（天）
        lam_range: λ搜索范围
        lam_steps: 搜索步数

    Returns:
        {best_lambda, r2_at_best, all_results: [...]}
    """
    from sklearn.linear_model import LinearRegression

    lambdas = np.linspace(lam_range[0], lam_range[1], lam_steps)
    results = []

    for lam in lambdas:
        cum_energies = []
        cars = []

        for evt in events:
            energy = evt.get('energy', 0)
            car = evt.get('car', 0)
            if energy is None or car is None:
                continue
            # 累积势能 = E × (1 - e^{-λ·t_max})
            cum_e = energy * (1.0 - np.exp(-lam * t_max))
            cum_energies.append(cum_e)
            cars.append(car)

        if len(cum_energies) < 10:
            continue

        X = np.array(cum_energies).reshape(-1, 1)
        y = np.array(cars)

        reg = LinearRegression().fit(X, y)
        r2 = reg.score(X, y)

        results.append({
            "lambda": float(lam),
            "r_squared": float(r2),
            "coef": float(reg.coef_[0]),
            "intercept": float(reg.intercept_),
            "n": len(cum_energies),
        })

    if not results:
        return {"error": "没有足够的数据点"}

    # 取最优点
    best = max(results, key=lambda r: r['r_squared'])
    return {
        "best_lambda": best['lambda'],
        "best_r_squared": best['r_squared'],
        "best_coef": best['coef'],
        "best_intercept": best['intercept'],
        "sample_size": best['n'],
        "all_results": results,
        "method": "grid_search",
    }


def fit_importance_weights(events: List[dict]) -> dict:
    """多元回归拟合重要性评分权重

    模型: CAR_abs = w1*authority + w2*scope + w3*level

    Args:
        events: [{"car_abs": ..., "auth": ..., "scope": ..., "level": ...}, ...]
    """
    from sklearn.linear_model import LinearRegression

    X = np.array([[e['auth'], e['scope'], e['level']] for e in events if
                   all(k in e for k in ['auth', 'scope', 'level'])])
    y = np.array([e['car_abs'] for e in events if
                  all(k in e for k in ['auth', 'scope', 'level'])])

    if len(X) < 10:
        return {"error": "样本不足"}

    reg = LinearRegression(fit_intercept=True).fit(X, y)
    return {
        "w_authority": float(reg.coef_[0]),
        "w_scope": float(reg.coef_[1]),
        "w_level": float(reg.coef_[2]),
        "intercept": float(reg.intercept_),
        "r_squared": float(reg.score(X, y)),
        "n": len(X),
    }


# ═══════════════════════════════════════
#   CLI 主流程
# ═══════════════════════════════════════

async def cmd_compute_all(conn, args):
    """批量计算所有事件的CAR"""
    events = await fetch_events(conn)
    log.info(f"共 {len(events)} 个事件")

    # 确定数据时间范围
    if not events:
        print("事件表为空")
        return

    min_date = min(str(e['event_date']) for e in events)
    max_date = max(str(e['event_date']) for e in events)
    # 扩展到事件窗范围
    data_start = (datetime.strptime(min_date, '%Y-%m-%d') - timedelta(days=200)).strftime('%Y-%m-%d')
    data_end = (datetime.strptime(max_date, '%Y-%m-%d') + timedelta(days=100)).strftime('%Y-%m-%d')

    log.info(f"数据范围: {data_start} ~ {data_end}")

    # 加载市场数据
    log.info("加载行业收益率...")
    ind_df = await fetch_industry_daily(conn, data_start, data_end)
    log.info(f"  行业数据: {len(ind_df)} 行, {ind_df['industry_code'].nunique()} 个行业")

    mkt_df = await fetch_market_daily(conn, data_start, data_end)
    log.info(f"  市场数据: {len(mkt_df)} 行")

    trade_cal = await fetch_trade_calendar(conn, data_start, data_end)
    log.info(f"  交易日: {len(trade_cal)} 天")

    if ind_df.empty or mkt_df.empty:
        log.error("市场数据为空")
        return

    # 逐个事件计算
    total = len(events)
    ok_count = 0
    for i, evt in enumerate(events):
        result = await compute_event_car(
            conn, evt['event_id'], str(evt['event_date']),
            str(evt['trade_date']) if evt['trade_date'] else str(evt['event_date']),
            ind_df, mkt_df, trade_cal
        )
        if result.get('status') == 'ok':
            ok_count += 1
        if (i + 1) % 20 == 0:
            log.info(f"进度: {i+1}/{total}, 已计算: {ok_count}")

    log.info(f"✅ 完成: {ok_count}/{total} 个事件成功计算CAR")


async def cmd_compute_single(conn, args):
    """测试单个事件"""
    event_id = args.test_event
    row = await conn.fetchrow("""
        SELECT event_id, event_date, trade_date, source, title
        FROM event_list WHERE event_id = $1
    """, event_id)

    if not row:
        print(f"事件 #{event_id} 不存在")
        return

    print(f"计算事件 #{row['event_id']}: [{row['event_date']}] {row['title'][:60]}")

    data_start = (row['event_date'] - timedelta(days=200)).strftime('%Y-%m-%d')
    data_end = (row['event_date'] + timedelta(days=100)).strftime('%Y-%m-%d')

    ind_df = await fetch_industry_daily(conn, data_start, data_end)
    mkt_df = await fetch_market_daily(conn, data_start, data_end)
    trade_cal = await fetch_trade_calendar(conn, data_start, data_end)

    result = await compute_event_car(
        conn, event_id,
        str(row['event_date']),
        str(row['trade_date']) if row['trade_date'] else str(row['event_date']),
        ind_df, mkt_df, trade_cal
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


async def cmd_fit_lambda(conn, args):
    """拟合衰减系数λ"""
    from sklearn.linear_model import LinearRegression

    # 加载事件+NLP+CAR数据
    rows = await conn.fetch("""
        SELECT e.event_id, e.event_date, e.title,
               s.industry_json, s.macro_score, s.importance_total,
               c.industry_code, c.industry_name, c.car_20,
               c.car_10, c.car_5, c.car_60
        FROM event_list e
        JOIN nlp_event_scores s ON e.event_id = s.event_id
        JOIN event_car c ON e.event_id = c.event_id
        WHERE c.car_20 IS NOT NULL
    """)
    log.info(f"加载 {len(rows)} 条事件-行业-CAR记录")

    if not rows:
        print("没有可用的数据（需先跑 --compute-all）")
        return

    # 方法1: 网格搜索（全样本）
    events_for_grid = []
    for r in rows:
        ind_scores = json.loads(r['industry_json']) if r['industry_json'] else {}
        energy = ind_scores.get(r['industry_code'], 0)
        if energy > 0 and r['car_20'] is not None:
            events_for_grid.append({
                "energy": energy,
                "car": abs(float(r['car_20'])),  # 用绝对值（方向由direction决定）
                "direction": 0,
            })

    if len(events_for_grid) < 20:
        print(f"样本不足: {len(events_for_grid)}")
        return

    print(f"\n{'='*60}")
    print(f"衰减系数 λ 拟合")
    print(f"{'='*60}")
    print(f"样本: {len(events_for_grid)} 个事件-行业组合")
    print(f"使用: CAR[0, +20] 作为响应变量")

    grid_result = fit_lambda_grid(events_for_grid, t_max=20,
                                   lam_range=(0.01, 1.0), lam_steps=100)

    print(f"\n网格搜索结果:")
    print(f"  λ = {grid_result['best_lambda']:.4f}")
    print(f"  R² = {grid_result['best_r_squared']:.4f}")
    print(f"  系数 = {grid_result['best_coef']:.4f}")
    print(f"  截距 = {grid_result['best_intercept']:.4f}")
    print(f"  样本量 = {grid_result['sample_size']}")

    # 半衰期
    hl = np.log(2) / grid_result['best_lambda']
    print(f"  半衰期 = {hl:.1f} 天")

    # 方法2: NLS
    # 用CAR[0] ~ CAR[+20]的路径来拟合
    car_path_rows = await conn.fetch("""
        SELECT c.event_id, c.industry_code, c.t_relative, c.car,
               c.industry_json
        FROM event_car c
        WHERE EXISTS (
            SELECT 1 FROM nlp_event_scores s WHERE s.event_id = c.event_id
        )
        AND c.t_relative BETWEEN 0 AND 20
        AND c.car IS NOT NULL
        ORDER BY c.event_id, c.industry_code, c.t_relative
    """)

    # 提取NLP势能（每个事件-行业组合的初始势能）
    nlp_cache = {}
    nlp_rows = await conn.fetch("""
        SELECT event_id, industry_json FROM nlp_event_scores
    """)
    for nr in nlp_rows:
        nlp_cache[nr['event_id']] = json.loads(nr['industry_json']) if nr['industry_json'] else {}

    # 构建NLS样本
    t_list = []
    car_list = []
    energy_list = []

    for r in car_path_rows:
        ind_scores = nlp_cache.get(r['event_id'], {})
        energy = ind_scores.get(r['industry_code'], 0)
        if energy > 0 and r['car'] is not None:
            t_list.append(r['t_relative'])
            car_list.append(float(r['car']))
            energy_list.append(energy)

    if len(t_list) > 50:
        t_arr = np.array(t_list)
        car_arr = np.array(car_list)
        en_arr = np.array(energy_list)

        nls_result = fit_lambda_nls(car_arr, en_arr, t_arr)
        print(f"\nNLS拟合结果:")
        if 'error' in nls_result:
            print(f"  ❌ 错误: {nls_result['error']}")
        else:
            print(f"  λ = {nls_result['lambda']:.4f} (SE = {nls_result['lambda_se']:.4f})")
            print(f"  β₁ = {nls_result['beta1']:.4f} (SE = {nls_result['beta1_se']:.4f})")
            print(f"  R² = {nls_result['r_squared']:.4f}")
            print(f"  样本量 = {nls_result['n']}")
            hl_nls = np.log(2) / nls_result['lambda']
            print(f"  半衰期 = {hl_nls:.1f} 天")
    else:
        print(f"\nNLS跳过: 有效样本 {len(t_list)} < 50")

    # 保存结果
    await conn.execute("""
        INSERT INTO calibrated_params
            (param_name, param_group, value, r_squared, sample_size, method)
        VALUES ('lambda', 'global', $1, $2, $3, 'grid_search')
        ON CONFLICT DO NOTHING
    """, grid_result['best_lambda'], grid_result['best_r_squared'],
        len(events_for_grid))

    print(f"\n✅ 结果已保存到 calibrated_params")


async def cmd_fit_importance(conn, args):
    """拟合重要性评分权重"""
    rows = await conn.fetch("""
        SELECT e.event_id,
               s.importance_source, s.importance_scope, s.importance_level,
               ABS(AVG(c.car_5)) as car_abs
        FROM event_list e
        JOIN nlp_event_scores s ON e.event_id = s.event_id
        JOIN event_car c ON e.event_id = c.event_id
        WHERE c.car_5 IS NOT NULL
          AND s.importance_source > 0
        GROUP BY e.event_id, s.importance_source, s.importance_scope, s.importance_level
    """)

    if len(rows) < 20:
        print(f"样本不足: {len(rows)}")
        return

    events = [{
        "car_abs": float(r['car_abs']),
        "auth": float(r['importance_source']),
        "scope": float(r['importance_scope']),
        "level": float(r['importance_level']),
    } for r in rows]

    result = fit_importance_weights(events)

    print(f"\n{'='*60}")
    print(f"重要性评分权重拟合")
    print(f"{'='*60}")
    if 'error' in result:
        print(f"❌ {result['error']}")
    else:
        print(f"  w_authority(来源权威) = {result['w_authority']:.4f}")
        print(f"  w_scope(影响范围)    = {result['w_scope']:.4f}")
        print(f"  w_level(政策层级)    = {result['w_level']:.4f}")
        print(f"  截距                = {result['intercept']:.4f}")
        print(f"  R²                  = {result['r_squared']:.4f}")
        print(f"  样本量               = {result['n']}")

        await conn.execute("""
            INSERT INTO calibrated_params
                (param_name, param_group, value, r_squared, sample_size, method)
            VALUES ('importance_weights', 'global', $1, $2, $3, 'regression')
        """, json.dumps({
            'w_authority': result['w_authority'],
            'w_scope': result['w_scope'],
            'w_level': result['w_level'],
            'intercept': result['intercept'],
        }, ensure_ascii=False), result['r_squared'], result['n'])

        print(f"\n✅ 结果已保存到 calibrated_params")


async def cmd_list_events(conn, args):
    """列出事件"""
    rows = await fetch_events(conn)
    print(f"\n事件列表 ({len(rows)}条):")
    print(f"{'ID':<6} {'日期':<12} {'来源':<15} {'方向':<6} {'NLP':<6} {'标题'}")
    print("-" * 100)
    for r in rows:
        dir_s = "利好" if r['nlp_dir'] and float(r['nlp_dir']) > 0 else (
                "利空" if r['nlp_dir'] and float(r['nlp_dir']) < 0 else "中性")
        print(f"#{r['event_id']:<4} {str(r['event_date']):<12} "
              f"{r['source']:<15} {r['direction']:<6} "
              f"{dir_s:<6} {r['title'][:60] if r['title'] else '(无标题)'}")


async def main():
    parser = argparse.ArgumentParser(description="事件研究+衰减拟合")
    parser.add_argument('--compute-all', action='store_true', help='批量计算所有事件CAR')
    parser.add_argument('--test-event', type=int, help='测试单个事件ID')
    parser.add_argument('--fit-lambda', action='store_true', help='拟合衰减系数λ')
    parser.add_argument('--fit-importance', action='store_true', help='拟合重要性权重')
    parser.add_argument('--list-events', action='store_true', help='列出事件')
    args = parser.parse_args()

    import asyncpg
    conn = await asyncpg.connect(PG_DSN)

    try:
        if args.compute_all:
            await cmd_compute_all(conn, args)
        elif args.test_event:
            await cmd_compute_single(conn, args)
        elif args.fit_lambda:
            await cmd_fit_lambda(conn, args)
        elif args.fit_importance:
            await cmd_fit_importance(conn, args)
        elif args.list_events:
            await cmd_list_events(conn, args)
        else:
            parser.print_help()
    finally:
        await conn.close()


if __name__ == '__main__':
    asyncio.run(main())
