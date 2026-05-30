#!/usr/bin/env python3
"""
信息衰减系数与时滞实证校准 v0.1

直接从新闻联播缓存 + PG行业日线 跑通全流程：
  1. 事件检测：从新闻联播中提取政策相关头条
  2. 行业CAR计算：市场模型 → 异常收益 → 累积异常收益
  3. 时滞τ估计：首次显著异动窗口检测
  4. 衰减λ拟合：NLS + 网格搜索
  5. 输出参数配置JSON + CAR可视化数据

用法：
  python3 calibrate_decay.py --event-count 30          # 用最近30个事件跑通全流程
  python3 calibrate_decay.py --year 2023                # 用某年的事件
  python3 calibrate_decay.py --all                      # 全量跑（耗时较长）
  python3 calibrate_decay.py --plot-event 5             # 画单个事件CAR图

依赖：
  psycopg2, numpy, pandas, scipy, statsmodels, matplotlib
"""

import json, sys, os, argparse, logging, hashlib
from pathlib import Path
from datetime import datetime, timedelta, date
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('calibrate')

# ─── 路径 ───
BASE_DIR = Path(__file__).resolve().parent.parent
CCTV_DIR = BASE_DIR / 'cache' / 'news_cctv'
OUTPUT_DIR = BASE_DIR / 'cache' / 'calibration'

# ─── PG ───
PG_DSN = dict(host='192.168.3.14', port=5432, user='guludan', password='toto', dbname='guludan')

# ─── 参数（后续会被校准结果覆盖） ───
ESTIMATION_WINDOW = (-120, -11)
EVENT_WINDOW = (0, 30)
CAR_WINDOWS = [5, 10, 20, 30]


def load_cctv_events(max_count: int = 200, year: Optional[int] = None,
                     policy_only: bool = True,
                     from_db: bool = False) -> List[dict]:
    """加载新闻联播事件

    Args:
        from_db: True → 从PG event_list表读取, False → 从缓存文件读取
    """
    if from_db:
        return _load_events_from_pg(max_count, year, policy_only)
    else:
        return _load_events_from_cache(max_count, year, policy_only)


def _load_events_from_cache(max_count, year, policy_only):
    """从缓存文件读取事件"""
    import sys
    sys.path.insert(0, str(BASE_DIR / 'scripts'))
    from data_acquisition import is_policy_related

    files = sorted(CCTV_DIR.glob('*.json'))
    if year:
        files = [f for f in files if f.stem.startswith(str(year))]

    events = []
    for f in files:
        try:
            data = json.load(open(f))
        except: continue
        if not isinstance(data, list):
            data = [data]

        for item in data:
            is_policy = item.get('_is_policy', False)
            relevance = item.get('_relevance_score', 0)

            if policy_only and not is_policy:
                continue

            events.append({
                "date": f.stem[:4] + "-" + f.stem[4:6] + "-" + f.stem[6:8],
                "trade_date": None,
                "title": item.get('title', ''),
                "content": item.get('content', ''),
                "is_policy": is_policy,
                "relevance": relevance,
                "index": item.get('index', 1),
                "source": "news_cctv",
            })

    events.sort(key=lambda e: e['date'])
    log.info(f"从缓存加载 {len(events)} 个事件（{'仅政策' if policy_only else '全部'}）")

    if max_count and len(events) > max_count:
        step = max(1, len(events) // max_count)
        events = events[::step][:max_count]
        log.info(f"  采样为 {len(events)} 个")
    return events[-max_count:] if max_count < len(events) else events


def _load_events_from_pg(max_count, year, policy_only):
    """从PG event_list表读取事件"""
    import psycopg2
    conn = psycopg2.connect(**PG_DSN)
    cur = conn.cursor()

    where_clauses = ["source = 'news_cctv'"]
    if year:
        where_clauses.append(f"EXTRACT(YEAR FROM event_date) = {year}")
    if policy_only:
        where_clauses.append("category != 'general'")

    where = " AND ".join(where_clauses)
    limit = max_count if max_count else 50000

    cur.execute(f"""
        SELECT event_date, title, full_text, category, direction
        FROM event_list
        WHERE {where}
        ORDER BY event_date
        LIMIT {limit}
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    events = []
    for r in rows:
        d_str = str(r[0])
        title = r[1] or ''
        content = r[2] or ''
        cat = r[3] or ''

        events.append({
            "date": d_str,
            "trade_date": None,
            "title": title,
            "content": content,
            "is_policy": cat != 'general',
            "relevance": 1.5 if cat != 'general' else 0,
            "index": 1,
            "source": "news_cctv",
        })

    log.info(f"从PG加载 {len(events)} 个事件（{'仅政策' if policy_only else '全部'}）")

    if max_count and len(events) > max_count:
        step = max(1, len(events) // max_count)
        events = events[::step][:max_count]
        log.info(f"  采样为 {len(events)} 个")

    return events


def load_cls_events(max_count: int = 100) -> List[dict]:
    """从财联社快讯加载事件"""
    cls_file = BASE_DIR / 'cache' / 'cls_telegraph_archive.json'
    if not cls_file.exists():
        log.warning("财联社缓存不存在")
        return []

    data = json.load(open(cls_file))
    events = []
    for item in (data if isinstance(data, list) else list(data.values())):
        events.append({
            "date": item.get('date', ''),
            "trade_date": None,
            "title": item.get('title', ''),
            "content": item.get('content', ''),
            "source": "cls_telegraph",
        })

    if max_count and len(events) > max_count:
        events = events[-max_count:]
    return events


def map_trade_dates(events: List[dict], trade_cal: List[date]) -> List[dict]:
    """将自然日映射到交易日"""
    trade_set = set(trade_cal)
    for evt in events:
        try:
            d = datetime.strptime(evt['date'][:10], '%Y-%m-%d').date()
        except:
            continue

        # 当天如果是交易日则用当天，否则找下一个交易日
        if d in trade_set:
            evt['trade_date'] = d
        else:
            # 找下一个交易日（最多往后5天）
            for i in range(1, 6):
                nd = d + timedelta(days=i)
                if nd in trade_set:
                    evt['trade_date'] = nd
                    break
            else:
                evt['trade_date'] = None

    mapped = [e for e in events if e['trade_date']]
    log.info(f"交易日映射: {len(mapped)}/{len(events)} 事件有交易日")
    return mapped


def load_industry_daily(conn, start_date: str, end_date: str) -> pd.DataFrame:
    """从PG加载行业日收益率"""
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, industry, close
        FROM industry_daily
        WHERE trade_date >= %s AND trade_date <= %s
        ORDER BY trade_date
    """, (start_date, end_date))
    rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=['trade_date', 'industry', 'close'])
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    if df.empty:
        return df

    # 计算日收益率
    df = df.sort_values(['industry', 'trade_date'])
    df['return'] = df.groupby('industry')['close'].pct_change() * 100  # 百分比
    df = df.dropna(subset=['return'])
    df = df.drop(columns=['close'])

    log.info(f"行业数据: {len(df)} 行, {df['industry'].nunique()} 个行业, "
             f"{df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")
    return df


def load_trade_calendar(conn, start_date: str, end_date: str) -> list:
    """加载交易日历"""
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT trade_date
        FROM daily_prices
        WHERE trade_date >= %s AND trade_date <= %s
        ORDER BY trade_date
    """, (start_date, end_date))
    return [r[0] for r in cur.fetchall()]  # datetime.date 列表


def compute_car_for_event(
    event_date, trade_cal: list,
    ind_returns: pd.DataFrame
) -> Dict[str, Dict[int, float]]:
    """计算单事件各行业的CAR

    使用市场模型: AR = r_ind - (α + β * r_market)
    市场收益 = 行业等权平均

    Returns: {industry: {t: car_value}}
    """
    if event_date not in trade_cal:
        return {}

    event_idx = trade_cal.index(event_date)

    # 确定窗口索引
    est_lo = max(0, event_idx + ESTIMATION_WINDOW[0])      # -120
    est_hi = max(0, event_idx + ESTIMATION_WINDOW[1] + 1)  # -10
    evt_lo = event_idx + EVENT_WINDOW[0]                    # 0
    evt_hi = min(len(trade_cal), event_idx + EVENT_WINDOW[1] + 1)  # +31

    window_dates = trade_cal[est_lo:evt_hi]  # list of datetime.date
    if len(window_dates) < 60:
        return {}

    # 统一转换为pd.Timestamp
    wd_ts = pd.to_datetime(window_dates)

    ind_df = ind_returns[ind_returns['trade_date'].isin(wd_ts)]
    if ind_df.empty:
        return {}

    # 市场收益 = 当日所有行业等权均值
    mkt = ind_df.groupby('trade_date')['return'].mean()

    est_len = len(wd_ts[:est_hi - est_lo])
    evt_len = len(wd_ts[est_len:])

    results = {}
    for industry in ind_df['industry'].unique():
        ind_data = ind_df[ind_df['industry'] == industry].set_index('trade_date')

        # 估计窗: OLS
        est_returns = ind_data.reindex(wd_ts[:est_len]).dropna()
        mkt_est = mkt.reindex(wd_ts[:est_len]).dropna()

        # 相交
        common_idx = est_returns.index.intersection(mkt_est.index)
        if len(common_idx) < 40:
            continue

        y_est = est_returns.loc[common_idx, 'return'].values
        x_est = mkt_est.loc[common_idx].values

        X_arr = np.column_stack([np.ones(len(x_est)), x_est])
        beta = np.linalg.lstsq(X_arr, y_est, rcond=None)[0]
        alpha, beta_mkt_val = beta[0], beta[1]

        # 事件窗: AR + CAR
        evt_wd = wd_ts[est_len:]
        ar_dict = {}
        for i, ts in enumerate(evt_wd):
            if ts in ind_data.index and ts in mkt.index:
                r_ind = ind_data.loc[ts, 'return']
                r_mkt = mkt.loc[ts]
                if not np.isnan(r_ind) and not np.isnan(r_mkt):
                    ar = r_ind - (alpha + beta_mkt_val * r_mkt)
                    ar_dict[i] = ar

        car_dict = {}
        cum = 0.0
        for i in range(evt_len):
            if i in ar_dict:
                cum += ar_dict[i]
            car_dict[i] = cum

        results[industry] = car_dict

    return results


def detect_tau(ars: Dict[int, float]) -> int:
    """检测信息生效时滞τ

    定义首次显著日：
    - 连续2个交易日的AR |t| > 1.96（无条件判断），
      或单日AR超过3倍估计窗标准差
    - 若t=0即显著，τ=0

    简化版：只用绝对值阈值（因为事件窗短，不拟合t检验）
    """
    if not ars:
        return 0

    ar_values = np.array(list(ars.values()))
    std_est = np.std(ar_values[:min(10, len(ar_values))])  # 前10天估计波动

    if std_est < 0.001:
        std_est = np.std(ar_values)

    threshold = 2.0 * std_est

    # 找最早连续2天超threshold
    for t in sorted(ars.keys()):
        if t > 10:  # 超过10天后不再视为时滞
            break
        v = abs(ars[t])
        next_t = t + 1
        if next_t in ars and abs(ars[next_t]) > threshold * 0.8:
            if v > threshold or abs(ars[next_t]) > threshold:
                return max(0, t)

    # 单日超3倍
    for t in sorted(ars.keys())[:5]:
        if abs(ars[t]) > 3 * std_est:
            return max(0, t)

    return 0


def fit_lambda_grid(car_data: List[float], energy_data: List[float],
                    t_max: int = 20,
                    lam_range=(0.01, 1.0), steps=200) -> dict:
    """网格搜索λ"""
    from sklearn.linear_model import LinearRegression

    lambdas = np.linspace(lam_range[0], lam_range[1], steps)
    best = {"lambda": 0.15, "r_squared": 0, "coef": 0, "intercept": 0}

    for lam in lambdas:
        cum_e = [e * (1 - np.exp(-lam * t_max)) for e in energy_data]
        X = np.array(cum_e).reshape(-1, 1)
        y = np.array(car_data)

        if len(X) < 10:
            continue

        reg = LinearRegression().fit(X, y)
        r2 = reg.score(X, y)

        if r2 > best['r_squared']:
            best = {
                "lambda": float(lam),
                "r_squared": float(r2),
                "coef": float(reg.coef_[0]),
                "intercept": float(reg.intercept_),
                "n": len(X),
            }

    return best


def fit_lambda_nls(t_array: np.ndarray, car_array: np.ndarray,
                   energy_array: np.ndarray,
                   tau: float = 0, initial_lam: float = 0.15) -> dict:
    """NLS拟合衰减λ（含时滞τ）

    模型: CAR(t) = β₁ · E · (1 - e^{-λ·max(0, t-τ)}) + β₀
    """
    from scipy.optimize import curve_fit

    def model(t, beta1, lam, beta0):
        effective_t = np.maximum(0, t - tau)
        return beta0 + beta1 * energy_array * (1 - np.exp(-lam * effective_t))

    p0 = [0.5, initial_lam, 0.0]
    bounds = ([0.001, 0.001, -5.0], [10.0, 2.0, 5.0])

    try:
        popt, pcov = curve_fit(
            lambda t, b1, l, b0: model(t, b1, l, b0),
            t_array, car_array, p0=p0, bounds=bounds, maxfev=10000
        )
        beta1, lam, beta0 = popt
        perr = np.sqrt(np.diag(pcov))

        residuals = car_array - model(t_array, beta1, lam, beta0)
        ss_res = np.sum(residuals ** 2)
        ss_tot = np.sum((car_array - np.mean(car_array)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        return {
            "lambda": float(lam),
            "lambda_se": float(perr[1]),
            "beta1": float(beta1),
            "beta1_se": float(perr[0]),
            "beta0": float(beta0),
            "tau": tau,
            "r_squared": float(r2),
            "n": len(t_array),
            "method": "NLS",
        }
    except Exception as e:
        return {"error": str(e), "method": "NLS"}


# ═══════════════════════════════════════
#   CLI
# ═══════════════════════════════════════

def cmd_calibrate(args):
    """全流程校准"""
    import psycopg2

    log.info("=" * 60)
    log.info("信息衰减系数校准管线 v0.1")
    log.info("=" * 60)

    # 1. 加载事件
    events_cctv = load_cctv_events(max_count=args.event_count, year=args.year, from_db=args.from_db)
    log.info(f"新闻联播事件: {len(events_cctv)}")

    events_cls = [] if not args.include_cls else load_cls_events(max_count=50)
    if events_cls:
        log.info(f"财联社事件: {len(events_cls)}")

    all_events = events_cctv + events_cls
    if not all_events:
        log.error("无事件可处理")
        return

    # 2. PG 连接
    conn = psycopg2.connect(**PG_DSN)

    try:
        # 3. 确定时间范围并加载数据
        min_date = min(e['date'][:10] for e in all_events)
        max_date = max(e['date'][:10] for e in all_events)
        data_start = (datetime.strptime(min_date, '%Y-%m-%d') - timedelta(days=180)).strftime('%Y-%m-%d')
        data_end = (datetime.strptime(max_date, '%Y-%m-%d') + timedelta(days=60)).strftime('%Y-%m-%d')

        log.info(f"数据范围: {data_start} ~ {data_end}")

        trade_cal = load_trade_calendar(conn, data_start, data_end)
        log.info(f"交易日: {len(trade_cal)}")

        # 4. 映射交易日
        all_events = map_trade_dates(all_events, trade_cal)
        if not all_events:
            log.error("无事件有交易日")
            return

        # 5. 加载行业收益率
        ind_df = load_industry_daily(conn, data_start, data_end)
        if ind_df.empty:
            log.error("行业数据为空")
            return

        industries = sorted(ind_df['industry'].unique())
        log.info(f"行业数: {len(industries)}")

        # 6. 对每个事件计算CAR
        results = []
        tau_values = defaultdict(list)  # 按信息类型分组的τ
        all_car_paths = []

        log.info(f"计算 {len(all_events)} 个事件的CAR...")
        for i, evt in enumerate(all_events):
            ed = evt['trade_date']
            if not ed:
                continue

            car_dicts = compute_car_for_event(ed, trade_cal, ind_df)
            if not car_dicts:
                continue

            # 计算行业平均CAR路径
            avg_car = defaultdict(float)
            count = defaultdict(int)
            for ind, cdict in car_dicts.items():
                for t, car in cdict.items():
                    avg_car[t] += car
                    count[t] += 1

            avg_path = {t: avg_car[t] / count[t] for t in sorted(avg_car.keys())}

            # τ检测（用第一个行业的AR路径近似）
            first_ind = list(car_dicts.keys())[0]
            first_ar = {}
            if first_ind in car_dicts:
                first_car = car_dicts[first_ind]
                # 反向算AR：AR(t) = CAR(t) - CAR(t-1)
                prev = 0.0
                for t in sorted(first_car.keys()):
                    first_ar[t] = first_car[t] - prev
                    prev = first_car[t]

            tau = detect_tau(first_ar)
            tau_values[evt.get('source', 'unknown')].append(tau)

            results.append({
                "event_id": i + 1,
                "date": str(evt['date']),
                "trade_date": str(ed),
                "title": evt['title'][:80],
                "source": evt.get('source', 'news_cctv'),
                "num_industries": len(car_dicts),
                "tau_detected": tau,
                "avg_car_path": {str(k): round(v, 4) for k, v in avg_path.items()},
            })

            # 保存全路径用于λ拟合
            for t, car in avg_path.items():
                if t <= 20:
                    all_car_paths.append({
                        "t": t,
                        "car": car,
                        "energy": 1.0,  # 暂时用1.0，后续替换为真实NLP密度
                    })

            if (i + 1) % 20 == 0:
                log.info(f"  进度: {i+1}/{len(all_events)}")

        # 7. 时滞τ估计结果
        tau_report = {}
        for src, vals in tau_values.items():
            if vals:
                tau_report[src] = {
                    "mean_tau": float(np.mean(vals)),
                    "median_tau": float(np.median(vals)),
                    "mode_tau": float(max(set(vals), key=vals.count)) if vals else 0,
                    "min_tau": int(min(vals)),
                    "max_tau": int(max(vals)),
                    "pct_zero": sum(1 for v in vals if v == 0) / len(vals) * 100,
                    "n": len(vals),
                }

        # 8. λ拟合（用网格搜索）
        car_20_list = []
        for r in results:
            path = r.get('avg_car_path', {})
            key = '20'
            if key in path:
                car_20_list.append({
                    "car": abs(path[key]),
                    "energy": 1.0,  # 占位
                })

        lam_result = {}
        if len(car_20_list) >= 10:
            cars = [x['car'] for x in car_20_list]
            energies = [x['energy'] for x in car_20_list]
            lam_result = fit_lambda_grid(cars, energies, t_max=20)

        # 9. 输出
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        output = {
            "timestamp": datetime.now().isoformat(),
            "config": {
                "event_count": len(all_events),
                "years": str(args.year) if args.year else "mixed",
                "policy_only": True,
                "data_range": f"{min_date} ~ {max_date}",
            },
            "tau_estimation": tau_report,
            "lambda_estimation": lam_result,
            "sample_note": "energy=1.0占位, 需接入NLP引擎后更新",
            "events": results,
        }

        out_file = OUTPUT_DIR / f"calibration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(out_file, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        log.info(f"✅ 结果保存到 {out_file}")

        # 10. 打印摘要
        print()
        print("=" * 60)
        print("📊 校准摘要")
        print("=" * 60)
        print(f"事件样本: {len(results)}")
        if tau_report:
            print(f"\n⏱ 时滞τ估计:")
            for src, tr in tau_report.items():
                print(f"  {src}: 均值={tr['mean_tau']:.1f}天, "
                      f"中位数={tr['median_tau']:.0f}天, "
                      f"众数={tr['mode_tau']:.0f}天, "
                      f"τ=0占比={tr['pct_zero']:.0f}%")
        if lam_result:
            print(f"\n📉 衰减λ拟合（网格搜索）:")
            print(f"  λ = {lam_result.get('lambda', 'N/A'):.4f}")
            hl = np.log(2) / lam_result.get('lambda', 0.15)
            print(f"  半衰期 = {hl:.1f} 天")
            print(f"  R² = {lam_result.get('r_squared', 'N/A'):.4f}")
            print(f"  样本量 = {lam_result.get('n', 0)}")

        print(f"\n💡 提示: 如需接入NLP关键词密度，运行 keyword_density_engine.py 后重跑")

    finally:
        conn.close()


def cmd_plot_event(args):
    """画单个事件的CAR图"""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        log.error("需要 matplotlib")
        return

    import psycopg2
    conn = psycopg2.connect(**PG_DSN)
    trade_cal = load_trade_calendar(conn, '2019-01-01', '2026-12-31')
    ind_df = load_industry_daily(conn, '2019-01-01', '2026-12-31')

    # 从缓存找到对应事件
    events = load_cctv_events(max_count=args.plot_event)
    if args.plot_event <= len(events):
        evt = events[args.plot_event - 1]
        print(f"绘制事件 #{args.plot_event}: {evt['date']} {evt['title'][:60]}")
        car_dicts = compute_car_for_event(evt['trade_date'] or evt['date'], trade_cal, ind_df)

        if not car_dicts:
            print("无CAR数据")
            return

        plt.figure(figsize=(12, 8))

        # 选每个行业的CAR路径画出来
        for ind, cdict in list(car_dicts.items())[:10]:
            ts = sorted(cdict.keys())
            cars = [cdict[t] for t in ts]
            plt.plot(ts, cars, label=ind, alpha=0.6)

        # 平均路径
        avg_car = defaultdict(float)
        count = defaultdict(int)
        for cdict in car_dicts.values():
            for t, car in cdict.items():
                avg_car[t] += car
                count[t] += 1
        ts = sorted(avg_car.keys())
        avg = [avg_car[t] / count[t] for t in ts]
        plt.plot(ts, avg, 'k-', linewidth=3, label='平均CAR')
        plt.axvline(x=0, color='red', linestyle='--', alpha=0.5, label='事件日')
        plt.axhline(y=0, color='gray', linestyle='-', alpha=0.3)
        plt.xlabel('事件日相对天数')
        plt.ylabel('累积异常收益 CAR (%)')
        plt.title(f'事件 #{args.plot_event}: {evt["date"]} {evt["title"][:50]}')
        plt.legend(loc='best', fontsize=8)
        plt.grid(alpha=0.3)

        out_file = OUTPUT_DIR / f'event_{args.plot_event}_car.png'
        plt.savefig(out_file, dpi=150, bbox_inches='tight')
        print(f"✅ 图片保存: {out_file}")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="信息衰减系数与市场时滞实证校准")
    parser.add_argument('--event-count', type=int, default=50, help='事件采样数')
    parser.add_argument('--year', type=int, help='指定年份')
    parser.add_argument('--include-cls', action='store_true', help='包含财联社事件')
    parser.add_argument('--plot-event', type=int, help='画单个事件的CAR图')
    parser.add_argument('--all', action='store_true', help='全量运行')
    parser.add_argument('--from-db', action='store_true', help='从PG event_list表读取事件(默认从缓存)')
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.plot_event:
        cmd_plot_event(args)
    else:
        if args.all:
            args.event_count = 5000
        cmd_calibrate(args)


if __name__ == '__main__':
    main()
